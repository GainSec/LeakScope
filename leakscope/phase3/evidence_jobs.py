"""Draw evidence images from records LeakScope already holds, once each.

Three kinds of saved record can be drawn: a provider's banner text, a wire
transcript from a native capture, and a live observation that kept its request
and response as hex. Drawing reads the database and the capture store only; it
never contacts an endpoint or a provider. The source record is left untouched
and the image is saved beside it with the source's own timestamps.
"""
import hashlib
import json
import logging
import time
from pathlib import Path

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from leakscope.services.runs import enqueue, write_state, _parse
from leakscope_app.models import EvidenceArtifact, Run, ServiceObservation
from .attribution import CONFIRMATION_KEYS, LIVE_PREFIXES, is_true
from .evidence_render import EVIDENCE_KIND, RENDERER, STATES, describe, render_document, stamp
from .protocol_hints import banner_text, decode_banner, is_placeholder, service_label

log = logging.getLogger(__name__)
PROVIDER_LABELS = {'shodan': 'Shodan', 'zoomeye': 'ZoomEye', 'netlas': 'Netlas'}
NO_CONTACT = 'LeakScope did not contact this endpoint to draw this image. No login, key exchange or authentication is shown or implied.'
NO_CREDENTIALS = 'These are the application bytes LeakScope recorded when it ran the operation above. No credentials were sent and nothing is replayed here.'
# LeakScope did not run this exchange, so it states only what was saved.
SAVED_EXCHANGE = 'These are the request and response bytes the collector saved with this observation. LeakScope did not run this exchange and nothing is replayed here.'
# Bookkeeping that is already stated elsewhere on the image.
_SKIP = frozenset({'operation', 'method', 'identity_verified', 'wire_summary', 'artifacts', 'content_hashes', 'collection_mode', 'partial',
                   'error', 'error_code', 'service_type', 'request_hex', 'response_hex', 'at', 'campaign', 'source', 'host', 'peer', 'port',
                   'address', 'transport', 'service_type_basis', 'protocol_hint', 'title', 'limitations', 'adapter', 'read_only'})


def _target(endpoint):
    host = '[' + endpoint.address + ']' if ':' in endpoint.address else endpoint.address
    return host + ':' + str(endpoint.port)


def _is_text(data):
    data = bytes(data[:2048])
    return bool(data) and sum(32 <= byte < 127 or byte in (9, 10, 13) for byte in data) >= len(data) * 0.85


def _hex(value):
    try:
        return bytes.fromhex(value) if isinstance(value, str) else b''
    except ValueError:
        return b''


def _byte_sections(sent, received, received_title='Received from the endpoint', received_caption='', sender='LeakScope'):
    sections = [{'title': 'Sent by ' + sender, 'kind': 'hex' if sent else 'text', 'body': sent if sent else 'Nothing was sent.' if sender == 'LeakScope' else 'No request bytes were saved.',
                 'caption': '%d bytes' % len(sent)}]
    if received and _is_text(received):
        sections.append({'title': received_title + ' (text)', 'kind': 'text', 'body': received, 'caption': '%d bytes' % len(received)})
    sections.append({'title': received_title + ' (hex)', 'kind': 'hex' if received else 'text', 'body': received if received else 'No bytes were received.',
                     'caption': received_caption or '%d bytes' % len(received)})
    return sections


def _footer(digest, now):
    return 'SHA-256 of the drawn bytes ' + digest + ' · drawn ' + stamp(now) + ' by ' + RENDERER


def banner_document(endpoint, observation, now=None):
    """Document for a provider's saved response text, or None if there is none.

    A saved HTTP response is left to the web captures; this covers services
    that have no page to screenshot.
    """
    data = observation.data if isinstance(observation.data, dict) else {}
    metadata = data.get('metadata') if isinstance(data.get('metadata'), dict) else {}
    text, field = banner_text(metadata)
    if not text or observation.provider.startswith(LIVE_PREFIXES + ('analysis:', 'scan:', 'certstream')) or text.lstrip()[:5].upper() == 'HTTP/':
        return None
    decoded = decode_banner(text)
    source = PROVIDER_LABELS.get(observation.provider, observation.provider)
    encoded = text.encode('utf-8', errors='replace')
    seen = observation.last_observed_at or observation.observed_at
    seen_text = stamp(seen) if seen else 'Not recorded by the source'
    if seen and observation.last_collected_at and seen > observation.last_collected_at:
        # Some providers report local times without a zone; do not present them as exact.
        seen_text += ' as recorded; later than the save time, so the source\'s time zone may not be UTC'
    saved = stamp(observation.first_collected_at)
    if observation.last_collected_at and observation.last_collected_at != observation.first_collected_at:
        saved += ' · same record last returned ' + stamp(observation.last_collected_at)
    sections = [{'title': 'Response text', 'kind': 'text', 'body': text, 'caption': '%d characters as saved' % len(text)},
                {'title': 'Bytes of the saved text', 'kind': 'hex', 'body': encoded, 'caption': 'UTF-8 encoding; the provider holds the original bytes'}]
    if decoded:
        sections.append({'title': 'Decoded from the response', 'kind': 'fields', 'body': decoded[1]})
    parsed = {key: metadata[key] for key in ('product', 'version', 'info', 'os', 'cpe') if metadata.get(key)}
    if parsed:
        sections.append({'title': 'Fields parsed by ' + source, 'kind': 'fields', 'body': parsed})
    notes = [NO_CONTACT]
    if '�' in text:
        notes.append('Some control bytes were replaced when this record was saved; they are drawn as a dot.')
    digest = hashlib.sha256(encoded).hexdigest()
    return {'target': _target(endpoint), 'transport': endpoint.transport, 'state': 'reported',
            'headline': (service_label(decoded[0]) + ' identification' if decoded else 'Service response') + ' saved by ' + source,
            'facts': [('Source', source + ' index record'), ('Seen by source', seen_text),
                      ('Saved by LeakScope', saved),
                      ('Operation', 'Response text recorded by ' + source + ' during its own scan. LeakScope sent nothing to this endpoint.'),
                      ('Outcome', 'Provider reported. Not checked live by LeakScope.'),
                      ('Provenance', 'Observation ' + str(observation.pk) + ' · field "' + field + '"')],
            'sections': sections, 'notes': notes, 'footer': _footer(digest, now or timezone.now()),
            'source': {'kind': 'provider_banner', 'id': observation.pk.hex, 'sha256': digest, 'provider': observation.provider,
                       'observed_at': seen.isoformat() if seen else None, 'collected_at': observation.first_collected_at.isoformat(),
                       'service': decoded[0] if decoded else '', 'decoded': decoded[1] if decoded else {}, 'bytes': len(encoded),
                       'operation': 'Provider-saved response text; nothing sent by LeakScope', 'observation_pk': observation.pk.hex}}


def _outcome(metadata, received, confirmed_label):
    error = str(metadata.get('error') or '')[:300]
    if any(is_true(metadata.get(key)) for key in CONFIRMATION_KEYS):
        return 'confirmed', 'Live protocol confirmed' + (': ' + confirmed_label if confirmed_label else '') + '.'
    if error:
        got = ' %d bytes were received before it ended.' % received if received else ' No bytes were received.'
        return ('partial' if received else 'failed'), 'Did not complete: ' + error.rstrip('.') + '.' + got
    return 'unidentified', 'A response was saved. This operation does not identify the protocol.'


def _decoded(metadata):
    return {key: value for key, value in metadata.items() if key not in _SKIP and not key.endswith('_hex') and value not in (None, '', [], {})}


def transcript_document(endpoint, artifact, transcript, observation=None, now=None):
    """Document for a saved native wire transcript, or None if it holds no bytes."""
    if not isinstance(transcript, dict):
        return None
    metadata = (observation.data.get('metadata') if observation and isinstance(observation.data, dict) else None) or {}
    provider = observation.provider if observation else 'fetch'
    rfb = transcript.get('format') == 'leakscope-rfb-wire/1'
    if rfb:
        if metadata.get('screenshot_available'):
            return None
        sent = _hex((transcript.get('sent') or {}).get('control_hex'))
        received = _hex((transcript.get('received') or {}).get('control_hex'))
        summary = {'bytes_sent': (transcript.get('sent') or {}).get('bytes'), 'bytes_received': (transcript.get('received') or {}).get('bytes'), 'transport': 'tcp'}
        caption = 'Control bytes only; pixel data is never kept in the transcript'
    elif transcript.get('format') == 'leakscope-wire/1':
        sent = _hex((transcript.get('sent') or {}).get('hex'))
        received = _hex((transcript.get('received') or {}).get('hex'))
        summary = transcript.get('summary') if isinstance(transcript.get('summary'), dict) else {}
        caption = ''
    else:
        return None
    if not sent and not received:
        return None
    service = str((observation.data.get('service_type') if observation and isinstance(observation.data, dict) else '') or '')
    hint = metadata.get('protocol_hint') if isinstance(metadata.get('protocol_hint'), dict) else None
    if is_placeholder(service):
        # An unverified attempt keeps the endpoint's stored type; name the reader that ran.
        service = str((hint or {}).get('service') or '')
    label = 'RFB (VNC)' if rfb else service_label(service) if service else ''
    state, outcome = _outcome(metadata, len(received), label)
    operation = str(metadata.get('operation') or ('Remote framebuffer negotiation' if rfb else 'Bounded protocol exchange'))[:300]
    if state == 'confirmed':
        headline = label + ' exchange completed by LeakScope'
    elif provider.endswith((':tcp', ':udp')):
        headline = 'Bounded ' + provider.rsplit(':', 1)[1].upper() + ' read by LeakScope'
    else:
        headline = (label + ' ' if label else '') + 'exchange attempted by LeakScope'
    connection = '%s%s · %s bytes sent · %s bytes received%s' % (
        summary.get('transport') or endpoint.transport, ' over TLS' if summary.get('tls') else '',
        summary.get('bytes_sent', len(sent)), summary.get('bytes_received', len(received)),
        ' · ended: ' + str(summary['termination']).replace('_', ' ') if summary.get('termination') else '')
    facts = [('Source', 'LeakScope native collector (' + provider + ')'), ('Collected', stamp(artifact.created_at)),
             ('Operation', operation), ('Outcome', outcome), ('Connection', connection),
             ('Provenance', 'Wire transcript ' + str(artifact.pk) + (' · job ' + str(artifact.run_id) if artifact.run_id else ''))]
    if hint:
        facts.insert(3, ('Reader chosen by', 'Provider-reported ' + service_label(hint.get('service')) + ' response text. That choice is not a verification.'))
    sections = _byte_sections(sent, received, received_caption=caption)
    decoded = _decoded(metadata)
    if decoded:
        sections.append({'title': 'Decoded fields', 'kind': 'fields', 'body': decoded})
    digest = hashlib.sha256(bytes(sent) + b'\x00' + bytes(received)).hexdigest()
    return {'target': _target(endpoint), 'transport': endpoint.transport, 'state': state, 'headline': headline, 'facts': facts,
            'sections': sections, 'notes': [NO_CREDENTIALS], 'footer': _footer(digest, now or timezone.now()),
            'source': {'kind': 'wire_transcript', 'id': artifact.pk.hex, 'sha256': artifact.sha256, 'provider': provider,
                       'observed_at': None, 'collected_at': artifact.created_at.isoformat(), 'service': service, 'decoded': decoded,
                       'bytes': len(sent) + len(received), 'operation': operation,
                       'observation_pk': observation.pk.hex if observation else None, 'run_id': str(artifact.run_id) if artifact.run_id else None}}


def exchange_document(endpoint, observation, now=None):
    """Document for a live observation that kept its exchange as hex fields."""
    data = observation.data if isinstance(observation.data, dict) else {}
    metadata = data.get('metadata') if isinstance(data.get('metadata'), dict) else {}
    received = _hex(metadata.get('response_hex'))
    sent = _hex(metadata.get('request_hex'))
    if not observation.provider.startswith(LIVE_PREFIXES) or not (received or sent):
        return None
    service = str(data.get('service_type') or '')
    state, outcome = _outcome(metadata, len(received), service_label(service) if service else '')
    if isinstance(metadata.get('handshake_outcome'), str):
        outcome += ' Recorded result: ' + metadata['handshake_outcome'][:200] + '.'
    operation = str(metadata.get('method') or metadata.get('operation') or 'Bounded protocol exchange')[:300]
    collected = metadata.get('at') if isinstance(metadata.get('at'), str) else observation.first_collected_at
    facts = [('Source', 'Live collector (' + observation.provider + ')'), ('Collected', stamp(collected)),
             ('Saved by LeakScope', stamp(observation.first_collected_at)), ('Operation', operation), ('Outcome', outcome)]
    if isinstance(metadata.get('implementation'), str):
        facts.append(('Implementation', metadata['implementation'][:300]))
    facts.append(('Provenance', 'Observation ' + str(observation.pk) + ' · fields "request_hex" and "response_hex"'))
    sections = _byte_sections(sent, received, sender='the collector')
    decoded = _decoded(metadata)
    if decoded:
        sections.append({'title': 'Decoded fields', 'kind': 'fields', 'body': decoded})
    notes = [SAVED_EXCHANGE]
    if state == 'confirmed':
        notes.append('A completed exchange names the protocol. It does not identify the software that implements it or who operates it.')
    digest = hashlib.sha256(sent + b'\x00' + received).hexdigest()
    return {'target': _target(endpoint), 'transport': endpoint.transport, 'state': state,
            'headline': (service_label(service) + ' ' if service else '') + 'exchange saved by ' + observation.provider,
            'facts': facts, 'sections': sections, 'notes': notes, 'footer': _footer(digest, now or timezone.now()),
            'source': {'kind': 'observation_exchange', 'id': observation.pk.hex, 'sha256': digest, 'provider': observation.provider,
                       'observed_at': collected if isinstance(collected, str) else None, 'collected_at': observation.first_collected_at.isoformat(),
                       'service': service, 'decoded': decoded, 'bytes': len(sent) + len(received), 'operation': operation,
                       'observation_pk': observation.pk.hex}}


def render_key(source):
    return hashlib.sha256(json.dumps([RENDERER, source['kind'], source['id'], source['sha256']]).encode()).hexdigest()


def evidence_time(collected_at, drawn):
    """Listing time of a drawn image: when its evidence was saved.

    The draw time is always kept in the image's own record. Evidence older
    than the capture retention period keeps the draw time instead, so
    housekeeping does not remove an image the moment it is drawn.
    """
    from datetime import timedelta
    from django.conf import settings
    from django.utils.dateparse import parse_datetime
    try:
        saved = parse_datetime(collected_at) if isinstance(collected_at, str) else None
    except ValueError:
        saved = None
    if saved is None or timezone.is_naive(saved) or saved > drawn:
        return drawn
    days = max(1, int(getattr(settings, 'LEAKSCOPE_CAPTURE_RETENTION_DAYS', 30)))
    return saved if saved > drawn - timedelta(days=days) + timedelta(hours=24) else drawn


def existing_keys(endpoint_ids):
    rows = EvidenceArtifact.objects.filter(endpoint_id__in=endpoint_ids, kind=EVIDENCE_KIND).values_list('metadata__render__fingerprint', flat=True)
    return {key for key in rows if isinstance(key, str)}


def save_document(endpoint, document, run=None):
    """Draw and store one document; returns the new artifact."""
    from .capture import _persist
    from .gallery import render_thumbnail, store_thumbnail
    from .jobs import capture_root
    source = document['source']
    png = render_document(document)
    drawn = timezone.now()
    root = capture_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Named "fingerprint" because output filters mask any field called "key".
    render = {'fingerprint': render_key(source), 'renderer': RENDERER, 'rendered_at': drawn.isoformat(), 'network_contact': False, 'state': document['state'],
              'state_label': STATES[document['state']][0], 'headline': document['headline'], 'description': describe(document),
              'source_kind': source['kind'], 'source_id': source['id'], 'source_sha256': source['sha256'], 'source_provider': source['provider'],
              'source_observed_at': source['observed_at'], 'source_collected_at': source['collected_at'], 'service': source['service'],
              'operation': source['operation'], 'decoded': source['decoded'], 'source_bytes': source['bytes'], 'source_run_id': source.get('run_id')}
    item = _persist(root, png, 'image/png', EVIDENCE_KIND, 'leakscope-render', {'render': render, 'capture_class': 'rendered'}, '.png')
    item['metadata']['tool_version'] = RENDERER
    if source.get('observation_pk'):
        item['metadata']['observation_pk'] = source['observation_pk']
    path = Path(item['path'])
    try:
        with transaction.atomic():
            row = EvidenceArtifact.objects.create(endpoint=endpoint, run=run, kind=EVIDENCE_KIND, relative_path=str(path.relative_to(root)),
                mime='image/png', size=len(png), sha256=item['metadata']['sha256'], tool='leakscope-render', metadata=item['metadata'])
    except Exception:
        path.unlink(missing_ok=True)
        raise
    saved = evidence_time(source['collected_at'], drawn)
    if saved != drawn:
        EvidenceArtifact.objects.filter(pk=row.pk).update(created_at=saved)
        row.created_at = saved
    try:
        row.metadata = dict(row.metadata, gallery=store_thumbnail(row, render_thumbnail(row)))
        row.save(update_fields=['metadata'])
    except (OSError, ValueError) as exc:
        log.warning('Evidence image preview was not stored (%s); the image is retained', type(exc).__name__)
    return row


def endpoint_documents(endpoint, now=None, observation_limit=40, artifact_limit=40):
    """Every drawable saved record of one endpoint, newest first."""
    from .local_correlations import _read_artifact
    observations = list(endpoint.observations.filter(hidden=False).exclude(provider='certstream').order_by('-last_collected_at', '-pk')[:observation_limit])
    by_key = {row.pk.hex: row for row in observations}
    for row in observations:
        document = exchange_document(endpoint, row, now) if row.provider.startswith(LIVE_PREFIXES) else banner_document(endpoint, row, now)
        if document:
            yield document
    transcripts = EvidenceArtifact.objects.filter(endpoint=endpoint, kind='wire-transcript', size__lte=1024 * 1024).order_by('-created_at')[:artifact_limit]
    for artifact in transcripts:
        link = str((artifact.metadata or {}).get('observation_pk') or '').replace('-', '').lower() if isinstance(artifact.metadata, dict) else ''
        observation = by_key.get(link) or (ServiceObservation.objects.filter(pk=link, endpoint=endpoint, hidden=False).first() if len(link) == 32 else None)
        if link and observation is None:
            continue  # the source observation is hidden; its transcript stays undrawn
        try:
            transcript = json.loads(_read_artifact(artifact, limit=1024 * 1024))
        except (OSError, ValueError):
            continue
        document = transcript_document(endpoint, artifact, transcript, observation, now)
        if document:
            yield document


def render_endpoint(endpoint, run=None, known=None, budget=None):
    """Draw whatever this endpoint's saved records support and do not have yet."""
    known = existing_keys([endpoint.pk]) if known is None else known
    result = {'drawn': 0, 'existing': 0, 'failed': 0, 'artifact_ids': []}
    for document in endpoint_documents(endpoint):
        key = render_key(document['source'])
        if key in known:
            result['existing'] += 1
            continue
        if budget is not None and budget <= result['drawn']:
            break
        try:
            row = save_document(endpoint, document, run)
        except (OSError, ValueError) as exc:
            log.warning('Evidence image could not be drawn (%s)', type(exc).__name__)
            result['failed'] += 1
            continue
        known.add(key)
        result['drawn'] += 1
        result['artifact_ids'].append(str(row.pk))
    return result


def render_quietly(endpoint, run=None, budget=4):
    """Used inside collection jobs: an image is a convenience and must never fail the job."""
    try:
        return render_endpoint(endpoint, run, budget=budget)
    except Exception as exc:
        log.warning('Evidence image step skipped (%s)', type(exc).__name__)
        return {'drawn': 0, 'existing': 0, 'failed': 1, 'artifact_ids': []}


def queue_render(spec):
    """Queue a local drawing job over saved records. No network contact."""
    from .local_correlations import _ids, _integer
    from .state import visible_endpoints
    if not isinstance(spec, dict) or spec.keys() - {'limit', 'endpoint_ids'}:
        raise ValueError('Evidence drawing supports limit and saved endpoint IDs only')
    limit = _integer(spec.get('limit', 200), 1, 5000, 'Limit')
    rows = visible_endpoints()
    if 'endpoint_ids' in spec:
        rows = rows.filter(pk__in=_ids(spec['endpoint_ids']))
    ids = [str(value) for value in rows.order_by('-last_seen', '-pk').values_list('pk', flat=True)[:limit + 1]]
    if not ids:
        raise ValueError('No visible saved endpoints matched this selection')
    return enqueue(run_evidence_render, kind='evidence_render', provider='local', type='rendered_evidence',
        parameters={'limit': limit, 'planned': len(ids[:limit]), 'selection_truncated': len(ids) > limit, 'network_contact': False},
        kwargs={'endpoint_ids': ids[:limit]})


def execute_render(run_id, task_id, endpoint_ids, seconds=900):
    from .state import visible_endpoints
    run = Run.objects.get(pk=run_id)
    current = lambda: Run.objects.filter(pk=run_id, task_id=task_id, state__in=['queued', 'running']).exists()
    if not current():
        return _parse(run.result) or {'cancelled': True, 'processed': 0, 'drawn': 0, 'network_contact': False}
    write_state(task_id, 'STARTED', {})
    started = time.monotonic()
    result = {'planned': len(endpoint_ids), 'processed': 0, 'drawn': 0, 'existing': 0, 'failed': 0, 'endpoints_with_images': 0,
              'cancelled': False, 'partial': False, 'network_contact': False}
    known = existing_keys(endpoint_ids)
    for index, identifier in enumerate(endpoint_ids):
        if index % 10 == 0:
            write_state(task_id, 'PROGRESS', {'current': result['processed'], 'total': result['planned'], **result})
            if not current():
                result['cancelled'] = True
                break
        if time.monotonic() - started > seconds:
            result['partial'] = True
            break
        endpoint = visible_endpoints().filter(pk=identifier).first()
        if endpoint is not None:
            outcome = render_endpoint(endpoint, run, known)
            for key in ('drawn', 'existing', 'failed'):
                result[key] += outcome[key]
            result['endpoints_with_images'] += bool(outcome['drawn'] or outcome['existing'])
        result['processed'] += 1
    result['partial'] = result['partial'] or result['cancelled'] or bool(result['failed'])
    if result['cancelled']:
        Run.objects.filter(pk=run_id, task_id=task_id, state='cancelled').update(result=json.dumps(result))
    else:
        write_state(task_id, 'SUCCESS', result)
    return result


@shared_task(bind=True, name='leakscope.phase3.evidence_render', soft_time_limit=1000, time_limit=1100)
def run_evidence_render(self, endpoint_ids):
    from .jobs import _claim_execution
    _claim_execution(self, 'evidence-render')
    run = Run.objects.get(task_id=self.request.id, kind='evidence_render')
    return execute_render(run.pk, self.request.id, endpoint_ids)
