"""Bounded local indexing of retained files; never contact a target/provider."""
from io import BytesIO
from html import unescape
import hashlib
import json
from itertools import islice
import os
from pathlib import Path
import re
import stat
import time
from uuid import UUID
from urllib.parse import parse_qs, urlsplit
import warnings

from celery import shared_task
from django.db import transaction
from django.db.models import CharField, Exists, OuterRef, Q, Subquery, Value
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast, Lower, Replace

from leakscope.services.runs import enqueue, write_state, _parse
from leakscope_app.models import EvidenceArtifact, EvidenceCorrelation, Run, ServiceObservation
from .state import visible_endpoints

FILE_LIMIT = 16 * 1024 * 1024
PIXEL_LIMIT = 16_000_000
RESPONSE_LIMIT = 1024 * 1024
FILE_KINDS = {'screenshot', 'http', 'httpx', 'response', 'http_response'}
RESPONSE_KINDS = FILE_KINDS - {'screenshot'}
HASH = re.compile(r'^[0-9a-f]{16}$')
ANALYTICS = re.compile(r'(?<![A-Z0-9_-])(?:G-[A-Z0-9]{6,20}|UA-[0-9]{4,12}-[0-9]{1,4}|GTM-[A-Z0-9]{4,12})(?![A-Z0-9_-])')
PUBLIC_TRACKING_LITERALS = (
    (
        'tracking.meta_pixel',
        r'''(?<![\w$])fbq\s*\(\s*(?P<command_quote>['"])init(?P=command_quote)\s*,\s*'''
        r'''(?P<id_quote>['"])(?P<identifier>[1-9][0-9]{4,19})(?P=id_quote)''',
    ),
    (
        'tracking.linkedin_partner',
        r'''(?<![\w$])_linkedin_partner_id\s*=\s*'''
        r'''(?P<id_quote>['"])(?P<identifier>[1-9][0-9]{3,19})(?P=id_quote)''',
    ),
    (
        'tracking.microsoft_uet',
        r'''\bnew\s+UET\s*\(\s*\{[^{}]{0,512}?'''
        r'''(?:(?P<field_quote>['"])ti(?P=field_quote)|\bti)\s*:\s*'''
        r'''(?P<id_quote>['"])(?P<identifier>[1-9][0-9]{4,19})(?P=id_quote)''',
    ),
    (
        'tracking.microsoft_uet',
        r'''\b(?:var|let|const)\s+(?P<configuration>[A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*\{[^{}]{0,512}?'''
        r'''(?:(?P<field_quote>['"])ti(?P=field_quote)|\bti)\s*:\s*'''
        r'''(?P<id_quote>['"])(?P<identifier>[1-9][0-9]{4,19})(?P=id_quote)'''
        r'''[^{}]{0,512}\}[^{}]{0,256}\bnew\s+UET\s*\(\s*(?P=configuration)\s*\)''',
    ),
    (
        'tracking.tiktok_pixel',
        r'''(?<![\w$])ttq\.load\s*\(\s*'''
        r'''(?P<id_quote>['"])(?P<identifier>[A-Za-z0-9]{20,32})(?P=id_quote)''',
    ),
    (
        'tracking.hotjar_site',
        r'''(?<![\w$])_hjSettings\s*=\s*\{[^{}]{0,256}?\bhjid\s*:\s*'''
        r'''(?P<identifier>[1-9][0-9]{2,19})(?=\s*[,}])''',
    ),
    (
        'tracking.google_ads',
        r'''(?<![\w$])gtag\s*\(\s*(?P<command_quote>['"])config(?P=command_quote)\s*,\s*'''
        r'''(?P<id_quote>['"])(?P<identifier>AW-[1-9][0-9]{4,19})(?P=id_quote)''',
    ),
    (
        'tracking.google_adsense',
        r'''(?<![\w:-])data-ad-client\s*=\s*'''
        r'''(?P<id_quote>['"])(?P<identifier>ca-pub-[0-9]{16})(?P=id_quote)''',
    ),
)
PUBLIC_TRACKING_LITERALS = tuple((kind, re.compile(pattern)) for kind, pattern in PUBLIC_TRACKING_LITERALS)
QUOTED_TRACKING_URL = re.compile(r'''(?P<quote>['"])(?P<url>(?:https?:)?//[^'"<>\\\s]{1,2048})(?P=quote)''', re.I)
PUBLIC_TRACKING_URLS = (
    ('tracking.meta_pixel', r'(?:www\.)?facebook\.com', r'/tr/?', 'id', r'[1-9][0-9]{4,19}'),
    ('tracking.linkedin_partner', r'px4?\.ads\.linkedin\.com', r'/collect/?', 'pid', r'[1-9][0-9]{3,19}'),
    ('tracking.microsoft_uet', r'bat\.bing\.com', r'/(?:p/)?action/[0-9]+/?', 'ti', r'[1-9][0-9]{4,19}'),
    ('tracking.microsoft_clarity', r'(?:www\.)?clarity\.ms', r'/tag/(?P<identifier>[A-Za-z0-9]{6,32})', '', ''),
    ('tracking.hubspot_portal', r'js(?:-(?:na|eu|ap)[0-9])?\.hs-scripts\.com', r'/(?P<identifier>[1-9][0-9]{0,19})\.js', '', ''),
    ('tracking.hotjar_site', r'static\.hotjar\.com', r'/c/hotjar-(?P<identifier>[1-9][0-9]{2,19})\.js', '', ''),
    ('tracking.google_adsense', r'pagead2\.googlesyndication\.com', r'/pagead/js/adsbygoogle\.js', 'client', r'ca-pub-[0-9]{16}'),
)
PUBLIC_TRACKING_URLS = tuple(
    (kind, re.compile(host, re.I), re.compile(path), parameter, re.compile(identifier))
    for kind, host, path, parameter, identifier in PUBLIC_TRACKING_URLS
)


def _integer(value, low, high, name):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f'{name} must be an integer from {low} to {high}')
    return value


def _ids(values):
    if not isinstance(values, list) or len(values) > 1000:
        raise ValueError('Choose at most 1000 saved IDs')
    try:
        return list(dict.fromkeys(str(UUID(str(value))) for value in values))
    except (ValueError, TypeError, AttributeError):
        raise ValueError('Invalid saved record ID') from None


def visible_artifacts(endpoints=None):
    """Saved artifact proof, including legacy screenshot hidden-source handling."""
    endpoints = visible_endpoints() if endpoints is None else endpoints.filter(pk__in=Subquery(visible_endpoints().values('pk')))
    observations = ServiceObservation.objects.filter(endpoint_id=OuterRef('endpoint_id'), hidden=False).annotate(
        source_key=Lower(Replace(Cast('pk', CharField()), Value('-'), Value('')))).filter(source_key=OuterRef('source_key'))
    legacy_hidden = ServiceObservation.objects.filter(endpoint_id=OuterRef('endpoint_id'), provider='fetch:screenshot', hidden=True)
    rows = EvidenceArtifact.objects.filter(endpoint_id__in=Subquery(endpoints.values('pk'))).annotate(
        source_key=Lower(Replace(KeyTextTransform('observation_pk', 'metadata'), Value('-'), Value(''), output_field=CharField())),
        bound_visible=Exists(observations), legacy_hidden=Exists(legacy_hidden))
    unbound = Q(metadata__observation_pk__isnull=True) | Q(metadata__observation_pk=None) | Q(metadata__observation_pk='')
    return rows.filter(Q(bound_visible=True) | (unbound & ~Q(kind='screenshot', legacy_hidden=True)))


def _read_artifact(row, *, limit=None):
    from .jobs import capture_root
    root = capture_root()
    limit = FILE_LIMIT if limit is None else min(limit, FILE_LIMIT)
    relative = Path(row.relative_path)
    if relative.is_absolute() or '..' in relative.parts or not relative.parts:
        raise ValueError('unsafe_path')
    # Follow no symlink components, including a final file changed during open.
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in relative.parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor); descriptor = child
        file_descriptor = os.open(relative.parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        with os.fdopen(file_descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
                raise ValueError('file_limit')
            data = stream.read(limit + 1)
    finally:
        os.close(descriptor)
    if len(data) > limit:
        raise ValueError('file_limit')
    if hashlib.sha256(data).hexdigest() != str(row.sha256).lower():
        raise ValueError('saved_hash_mismatch')
    return data


def screenshot_dhash(data):
    """64 horizontal grayscale comparisons; blank/uniform gradients are omitted."""
    from PIL import Image
    if len(data) > FILE_LIMIT:
        raise ValueError('file_limit')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(data), formats=('PNG', 'JPEG', 'WEBP')) as image:
                if image.width < 1 or image.height < 1 or image.width * image.height > PIXEL_LIMIT:
                    raise ValueError('pixel_limit')
                image.load()
                small = image.convert('L').resize((9, 8), Image.Resampling.LANCZOS)
                pixels = list(small.getdata())
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValueError('pixel_limit') from None
    number = 0
    for y in range(8):
        for x in range(8):
            number = (number << 1) | (pixels[y*9+x] > pixels[y*9+x+1])
    return None if number in {0, (1 << 64)-1} else f'{number:016x}'


def _tracking_url_ids(text):
    for match in islice(QUOTED_TRACKING_URL.finditer(text), 256):
        try:
            parsed = urlsplit('https:' + match['url'] if match['url'].startswith('//') else match['url'])
            if parsed.username is not None or parsed.password is not None:
                continue
            query = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=32)
            hostname = parsed.hostname or ''
        except ValueError:
            continue
        for kind, host, path, parameter, identifier in PUBLIC_TRACKING_URLS:
            path_match = path.fullmatch(parsed.path)
            if not host.fullmatch(hostname) or not path_match:
                continue
            if parameter:
                values = query.get(parameter, [])
                if len(values) == 1 and identifier.fullmatch(values[0]):
                    yield kind, values[0]
            else:
                yield kind, path_match['identifier']


def analytics_ids(data):
    """Return bounded public marketing-project references from retained response text."""
    text = data[:RESPONSE_LIMIT].decode('utf-8', errors='replace')
    values = {
        ('tracking.google_tag_manager' if value.startswith('GTM-') else 'tracking.google_analytics', value)
        for value in ANALYTICS.findall(text)
    }
    text = unescape(text)
    for kind, pattern in PUBLIC_TRACKING_LITERALS:
        values.update((kind, match['identifier']) for match in islice(pattern.finditer(text), 64))
    values.update(_tracking_url_ids(text))
    return sorted(values)[:64]


def response_signals(data):
    from .declaration_signals import declaration_signals
    return sorted(set(analytics_ids(data) + declaration_signals(data)))


def artifact_signals(row):
    data = _read_artifact(row)
    if row.kind == 'screenshot':
        value = screenshot_dhash(data)
        return [('screenshot.dhash64', value)] if value else []
    if row.kind in FILE_KINDS:
        return response_signals(data)
    return []


def _persist_artifact_signals(row, signals):
    """Caller owns the transaction and has rechecked visible artifact proof."""
    for kind, value in signals:
        signal, created = EvidenceCorrelation.objects.select_for_update().get_or_create(
            endpoint_id=row.endpoint_id, kind=kind, value=value,
            defaults={'artifact': row, 'first_seen': row.created_at, 'last_seen': row.created_at})
        if not created:
            # An older backfill must not replace newer retained visible proof.
            if (row.created_at >= signal.last_seen or not signal.artifact_id
                    or not visible_artifacts().filter(pk=signal.artifact_id).exists()):
                signal.artifact = row
            signal.first_seen = min(signal.first_seen, row.created_at)
            signal.last_seen = max(signal.last_seen, row.created_at)
            signal.save(update_fields=['artifact', 'first_seen', 'last_seen'])


def index_captured_response(artifact_id):
    """Index one committed small HTTP-like artifact using its final saved proof."""
    row = visible_artifacts().filter(pk=artifact_id, kind__in=RESPONSE_KINDS,
        size__gt=0, size__lte=RESPONSE_LIMIT).first()
    if row is None:
        return 0
    signals = response_signals(_read_artifact(row, limit=RESPONSE_LIMIT))
    with transaction.atomic():
        if not visible_artifacts().filter(pk=row.pk, endpoint_id=row.endpoint_id).exists():
            return 0
        _persist_artifact_signals(row, signals)
    return len(signals)


def queue_reindex(spec, *, endpoints=None):
    if not isinstance(spec, dict) or spec.keys()-{'limit', 'artifact_ids', 'endpoint_ids'}:
        raise ValueError('Indexing supports limit and saved artifact/endpoint IDs only')
    limit = _integer(spec.get('limit', 50), 1, 1000, 'Limit')
    rows = visible_artifacts(endpoints).filter(kind__in=FILE_KINDS, size__gt=0, size__lte=FILE_LIMIT)
    if 'artifact_ids' in spec:
        rows = rows.filter(pk__in=_ids(spec['artifact_ids']))
    if 'endpoint_ids' in spec:
        rows = rows.filter(endpoint_id__in=_ids(spec['endpoint_ids']))
    ids = list(rows.order_by('-created_at', '-pk').values_list('pk', flat=True)[:limit+1])
    selected = [str(value) for value in ids[:limit]]
    if not selected:
        raise ValueError('No visible eligible saved files matched this selection')
    return enqueue(run_local_correlation_index, kind='correlation_index', provider='local', type='saved_artifacts',
        parameters={'limit': limit, 'planned': len(selected), 'selection_truncated': len(ids)>limit, 'network_contact': False},
        kwargs={'artifact_ids': selected})


def _current(run_id, task_id):
    return Run.objects.filter(pk=run_id, task_id=task_id, state__in=['queued', 'running']).exists()


def execute_index(run_id, task_id, artifact_ids):
    artifact_ids = _ids(artifact_ids)
    run = Run.objects.get(pk=run_id)
    if not _current(run_id, task_id):
        return _parse(run.result) or {'cancelled': True, 'indexed': 0}
    write_state(task_id, 'STARTED', {})
    start = time.monotonic()
    result = {'planned': len(artifact_ids), 'processed': 0, 'indexed': 0, 'signals': 0, 'skipped': 0,
              'errors': 0, 'cancelled': False, 'partial': False, 'network_contact': False}
    def progress():
        write_state(task_id, 'PROGRESS', {'current': result['processed'], 'total': len(artifact_ids), **result})
    progress()
    for identifier in artifact_ids:
        if not _current(run_id, task_id):
            result['cancelled'] = True; break
        if time.monotonic()-start > 120:
            result['partial'] = True; break
        row = visible_artifacts().filter(pk=identifier, kind__in=FILE_KINDS).first()
        if row is None:
            result['skipped'] += 1; result['processed'] += 1; progress(); continue
        try:
            signals = artifact_signals(row)
        except (OSError, ValueError, Warning):
            result['errors'] += 1; result['processed'] += 1; progress(); continue
        with transaction.atomic():
            locked = Run.objects.select_for_update().get(pk=run_id)
            if locked.task_id != task_id or locked.state not in {'queued', 'running'}:
                result['cancelled'] = True; break
            if not visible_artifacts().filter(pk=row.pk).exists():
                result['skipped'] += 1; result['processed'] += 1; progress(); continue
            _persist_artifact_signals(row, signals)
        result['processed'] += 1
        result['indexed'] += bool(signals)
        result['signals'] += len(signals)
        progress()
    if not _current(run_id, task_id):
        result['cancelled'] = True
    result['partial'] = result['partial'] or result['cancelled'] or bool(result['errors'] or result['skipped'])
    if result['cancelled']:
        Run.objects.filter(pk=run_id, task_id=task_id, state='cancelled').update(result=json.dumps(result))
    else:
        write_state(task_id, 'SUCCESS', result)
    return result


@shared_task(bind=True, name='leakscope.phase3.local_correlation_index', soft_time_limit=150, time_limit=180)
def run_local_correlation_index(self, artifact_ids):
    from .jobs import _claim_execution
    _claim_execution(self, 'correlation-index')
    run = Run.objects.get(task_id=self.request.id, kind='correlation_index')
    return execute_index(run.pk, self.request.id, artifact_ids)


def hamming_groups(signals, *, threshold=8, limit=500):
    threshold = _integer(threshold, 0, 8, 'Hamming threshold')
    limit = _integer(limit, 1, 500, 'Signal limit')
    sampled = list(islice(signals, limit+1))
    rows = [dict(row) for row in sampled[:limit] if HASH.fullmatch(str(row.get('value', '')))
            and row['value'] not in {'0'*16, 'f'*16}]
    used = set(); groups = []
    for index, anchor in enumerate(rows):
        if index in used:
            continue
        value = int(anchor['value'], 16); members = {}; matched = []
        for other, row in enumerate(rows):
            if other in used:
                continue
            distance = (value ^ int(row['value'], 16)).bit_count()
            if distance <= threshold:
                key = str(row['id']); matched.append(other)
                if key not in members or distance < members[key]['distance']:
                    members[key] = dict(row, distance=distance)
        used.update(matched)
        if len(members) < 2:
            continue
        meaning = f'Heuristic dHash64 image distance ≤{threshold} from the representative, not a transitive cluster or proof of ownership.'
        groups.append({'key': 'screenshot.dhash64:'+anchor['value'], 'kind': 'screenshot.dhash64', 'value': anchor['value'],
                       'algorithm': 'dHash64', 'threshold': threshold, 'endpoint_count': len(members),
                       'members': sorted(members.values(), key=lambda x: (x['distance'], str(x['id'])))[:20],
                       'members_truncated': len(members)>20, 'meaning': meaning})
    return {'results': groups, 'has_more': False, 'truncated': len(sampled)>limit,
            'sampled_signals': min(len(sampled), limit), 'signal_limit': limit, 'threshold': threshold, 'algorithm': 'dHash64',
            'basis': 'Heuristic distance over a bounded sample of locally indexed visible screenshots; blank hashes excluded. '
                     'Representative groups are not exhaustive or transitive; visual similarity does not establish ownership. No images are read or indexed on browse.'}


def screenshot_similarity(candidates, *, threshold=8, limit=500):
    limit = _integer(limit, 1, 500, 'Signal limit')
    rows = candidates.filter(kind='screenshot.dhash64', artifact_id__in=Subquery(visible_artifacts().values('pk'))).select_related('endpoint').order_by('-last_seen', '-pk')[:limit+1]
    values = ({'id': str(row.endpoint_id), 'value': row.value, 'artifact_id': str(row.artifact_id),
               'address': row.endpoint.address, 'port': row.endpoint.port, 'transport': row.endpoint.transport,
               'reference': {'type': 'endpoint', 'id': str(row.endpoint_id)}} for row in rows)
    return hamming_groups(values, threshold=threshold, limit=limit)
