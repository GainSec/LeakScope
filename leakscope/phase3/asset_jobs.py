"""Explicit bounded enrichment and evidence-first certificate follow-up jobs."""
import hashlib
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

from celery import shared_task
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from leakscope.services.config import get_config
from leakscope.services.runs import enqueue, serialize_run
from leakscope.shared import is_blacklisted
from leakscope_app.models import (AssetNode, AssetEvidence, Run, ExecutionLease,
    WatchRule, CertificateRecord, ServiceObservation, EvidenceArtifact)
from . import relationships as store
from .asset_names import normalize_domain, normalize_ip
from .dns_enrichment import collect_dns, resolve_names
from .registry import netblock_info, discover_organization
from . import free_registry
from .web_services import research_services
from .usage_policy import validate_demo, demo_metadata
from .jobs import (_claim_execution, _claim_daily_budget, validate_actions,
    capture_root, capture_endpoint)
from .state import _json_safe, acquire_lease, ingest_observation, tag_names, set_tags

KINDS = {'resolve', 'dns', 'netblock', 'organization', 'services', 'registration', 'identity_metadata'}
HTTP_ACTIONS = {'http', 'httpx', 'screenshot'}
MAX_CAPTURE_ATTEMPTS = 20
CAPTURE_DEADLINE = 300


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _text(value,maximum=200):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError('Enter an organization name or alias of at most '+str(maximum)+' characters')
    return ' '.join(value.split())


def _spec(kind, target, organization_id=None, max_requests=2, aliases=None, include_netlas=False, demo=False):
    demo=validate_demo(demo)
    if demo and kind not in {'organization','services','identity_metadata'}: raise ValueError('Demo applies to organization discovery and web service research')
    if not isinstance(include_netlas,bool) or (include_netlas and kind!='organization'): raise ValueError('Netlas opt-in belongs to organization discovery')
    if kind not in KINDS:
        raise ValueError('Unknown asset enrichment kind')
    if kind == 'identity_metadata':
        if organization_id or aliases:
            raise ValueError('Public identity metadata uses a selected domain')
        target = normalize_domain(target)
        aliases = []
        if isinstance(max_requests, bool) or not isinstance(max_requests, int) or not 1 <= max_requests <= 6:
            raise ValueError('Public identity metadata permits one to six requests')
        max_requests = 1
    elif kind == 'services':
        if aliases: raise ValueError('Service research uses the selected target')
        if organization_id:
            try: organization=AssetNode.objects.get(pk=organization_id,kind='organization')
            except (AssetNode.DoesNotExist,ValidationError,ValueError,TypeError): raise ValueError('Unknown organization asset') from None
            if organization.organization_status!='active': raise ValueError('Organization must be active for research')
            target=_text(target)
            if organization.value!=target: raise ValueError('Organization target does not match the saved asset')
        else:
            try: target=normalize_ip(target)
            except ValueError: target=normalize_domain(target)
        aliases=[]
        if isinstance(max_requests,bool) or max_requests not in (1,2): raise ValueError('Service research permits at most two Firecrawl requests')
    elif kind == 'organization':
        target = _text(target)
        if isinstance(max_requests, bool) or not isinstance(max_requests, int) or not 1 <= max_requests <= 2:
            raise ValueError('Organization discovery supports one or two bounded provider requests')
        if organization_id:
            try:
                organization = AssetNode.objects.get(pk=organization_id, kind='organization')
            except (AssetNode.DoesNotExist, ValidationError, ValueError, TypeError):
                raise ValueError('Unknown organization asset') from None
            if organization.organization_status!='active': raise ValueError('Organization must be active for discovery')
            if organization.value.casefold() != target.casefold():
                raise ValueError('Organization target does not match the saved asset')
            if aliases is None:
                aliases = organization.attributes.get('aliases', [])
        if aliases is None:
            aliases = []
        maximum_aliases=2048 if organization_id else 8
        if not isinstance(aliases, list) or len(aliases) > maximum_aliases:
            raise ValueError('Too many organization aliases')
        aliases = sorted(set(_text(value,500 if organization_id else 200) for value in aliases))
    else:
        if organization_id or aliases:
            raise ValueError('Organization options belong to organization discovery')
        target = normalize_domain(target) if kind in {'dns','registration','identity_metadata'} else normalize_ip(target)
        aliases = []
        max_requests = 2
    if demo: max_requests=1
    spec = {'kind': kind, 'target': target, 'organization_id': str(organization_id) if organization_id else None,
            'max_requests': max_requests, 'aliases': aliases}
    identity = {'kind': kind, 'target': target.casefold(), 'max_requests': max_requests,
                'aliases': sorted({alias.casefold() for alias in aliases})}
    if kind=='services': identity['target_kind']='organization' if organization_id else _target_kind(target)
    if kind=='identity_metadata':
        from .public_metadata import IDENTITY_POLICY_VERSION
        identity['identity_policy_version']=IDENTITY_POLICY_VERSION
    if kind=='organization':
        spec['include_netlas']=include_netlas
        identity.update(registry_policy='free-first-v1',include_netlas=include_netlas)
    if demo: spec['demo']=True;identity['demo']=True
    spec['cache_key'] = _digest(identity)
    return spec


def _target_kind(target):
    try: normalize_ip(target);return 'ip'
    except ValueError: return 'domain'


def _root(spec):
    if spec['organization_id']:
        return AssetNode.objects.get(pk=spec['organization_id'], kind='organization')
    root=store.node(_target_kind(spec['target']) if spec['kind']=='services' else 'organization' if spec['kind'] == 'organization' else 'domain' if spec['kind'] in {'dns','registration','identity_metadata'} else 'ip', spec['target'])
    if root.kind=='organization' and root.organization_status!='active': raise ValueError('Organization must be active for enrichment')
    return root


def _cached(root, spec):
    return AssetEvidence.objects.filter(subject=root, source='enrichment:' + spec['kind'],
        data__cache_key=spec['cache_key'], expires_at__gt=timezone.now()).order_by('-last_collected_at').first()


def demo_sample(result, *, cached=False):
    sample=dict(result)
    for field in ('domains','networks','ips','asns','entities','services','sources','identities'):
        if isinstance(sample.get(field),list): sample[field]=sample[field][:1]
    if isinstance(result.get('sources'),list) and isinstance(result.get('services'),list):
        sources={item.get('url'):item for item in result['sources'] if isinstance(item,dict) and item.get('url')}
        sample['services']=[]
        for candidate in result['services']:
            if not isinstance(candidate,dict):continue
            citation=next((proof for proof in candidate.get('evidence',[]) if isinstance(proof,dict) and proof.get('source_url') in sources),None)
            if citation:
                sample['services']=[dict(candidate,evidence=[citation])]
                sample['sources']=[sources[citation['source_url']]]
                break
    metadata=demo_metadata(max_requests_per_paid_provider=1,max_results_per_collection=1,cached=cached)
    policy=dict(result.get('usage_policy') or {},**metadata['usage_policy'])
    if cached:
        for key in ('search_requests','llm_requests','requests_made'):
            if key in policy:policy[key]=0
    sample.update(demo=True,usage_policy=policy)
    sample['partial']=True
    if cached:
        sample['cached_original_requests']=sample.get('requests',0)
        sample.update(requests=0,free_requests=0,netlas_requests=0)
    return sample


def _mode_cached(root,spec):
    cached=_cached(root,spec)
    if cached or not spec.get('demo'):return cached
    for budget in ((1,) if spec['kind'] == 'identity_metadata' else (2, 1)):
        normal=_spec(spec['kind'],spec['target'],spec['organization_id'],budget,spec['aliases'],spec.get('include_netlas',False))
        cached=_cached(root,normal)
        if cached:return cached
    return None


def queue_enrichment(kind, target, *, organization_id=None, max_requests=2, aliases=None, include_netlas=False, demo=False):
    """Return a serialized durable run, or saved evidence while its cache is valid.

    Query inputs are frozen in the task payload; integration credentials stay in
    worker configuration and are never included in broker parameters.
    """
    spec = _spec(kind, target, organization_id, max_requests, aliases, include_netlas, demo)
    root = _root(spec)
    cached = _mode_cached(root, spec)
    if cached:
        return {'cached': True, 'result': demo_sample(cached.data['result'],cached=True) if demo else cached.data['result'], 'evidence_id': str(cached.pk),
                'expires_at': cached.expires_at.isoformat()}
    provider = 'public_metadata' if kind == 'identity_metadata' else 'firecrawl' if kind=='services' else 'rdap+netlas' if kind == 'organization' and include_netlas else 'rdap' if kind in {'organization','netblock','registration'} else 'dns'
    run = enqueue(run_asset_enrichment, kind='asset_enrichment', type=kind, provider=provider,
        parameters=spec, kwargs=spec)
    return {'cached': False, 'run': serialize_run(run)}


def _cancelled(task):
    task_id = getattr(task.request, 'id', None)
    return bool(task_id and Run.objects.filter(task_id=task_id, state='cancelled').exists())


def _date(value):
    if isinstance(value, str):
        try: value = parse_datetime(value)
        except ValueError: value = None
    if not isinstance(value, datetime): return None
    return timezone.make_aware(value, timezone.get_default_timezone()) if timezone.is_naive(value) else value


def _cache_expiry(kind, result):
    now = timezone.now()
    partial = bool(result.get('partial') or result.get('errors'))
    if kind in {'organization', 'netblock', 'services', 'registration', 'identity_metadata'}:
        return now + timedelta(seconds=60) if partial else now + timedelta(days=1)
    expiries = []
    for item in list(result.get('records') or []) + list(result.get('names') or []):
        if not isinstance(item, dict): continue
        expiry = _date(item.get('expires_at'))
        ttl = item.get('ttl')
        if expiry is None and isinstance(ttl, (float, int)) and not isinstance(ttl, bool):
            expiry = now + timedelta(seconds=max(0, min(ttl, 3600)))
        if expiry is not None: expiries.append(expiry)
    ceiling = now + timedelta(seconds=60 if partial else 3600)
    return min([ceiling] + expiries) if expiries else now + timedelta(seconds=60)


def _bounded(result, config):
    result = dict(result)
    for key, maximum in [('records', 512), ('names', 512), ('services', 128), ('domains', 512),
                         ('identities', 600), ('attempts', 6), ('networks', 128), ('entities',128), ('ips', 128), ('asns', 128), ('errors', 100)]:
        value = result.get(key)
        if isinstance(value, list) and len(value) > maximum:
            result[key] = value[:maximum]
            result['partial'] = result['limited'] = True
    return _json_safe(result, config=config)


@shared_task(bind=True, name='leakscope.phase3.run_asset_enrichment', soft_time_limit=90, time_limit=100)
def run_asset_enrichment(self, kind, target, organization_id=None, max_requests=2, aliases=None, cache_key=None, include_netlas=False, demo=False):
    demo=validate_demo(demo)
    _claim_execution(self, 'asset-enrichment')
    spec = _spec(kind, target, organization_id, max_requests, aliases, include_netlas, demo)
    target = spec['target']
    root = _root(spec)
    cached = _mode_cached(root, spec)
    if cached:
        return dict(demo_sample(cached.data['result'],cached=True) if demo else cached.data['result'], cached=True)
    lease = 'asset-enrichment-flight:' + spec['cache_key']
    token = str(getattr(self.request, 'id', '') or '')
    if not acquire_lease(lease, 120, {'task_id': token}):
        return {'skipped': True, 'reason': 'Equivalent enrichment is already running', 'root_id': str(root.pk)}
    config = get_config()['config']
    try:
        if _cancelled(self): return {'skipped': True, 'reason': 'Enrichment cancelled'}
        try:
            if kind == 'dns': result = collect_dns(target, config, max_queries=32, timeout=12)
            elif kind == 'resolve': result = resolve_names(target, config, max_queries=12, timeout=10)
            elif kind == 'netblock': result = netblock_info(target, config)
            elif kind == 'services': result = research_services(target,config,target_kind='organization' if organization_id else _target_kind(target),max_requests=spec['max_requests'],timeout=45,**({'demo':True} if demo else {}))
            elif kind == 'identity_metadata':
                from .public_metadata import collect_public_metadata
                from leakscope.services.run_events import emit
                active_run = Run.objects.filter(task_id=getattr(self.request, 'id', None)).first()
                result = collect_public_metadata(target, config, max_requests=spec['max_requests'],
                    on_progress=lambda attempt: emit(active_run, 'Checked ' + attempt['label'] + ' · ' + attempt['status'],
                                                     event='public_metadata', data=attempt) if active_run else None)
            elif kind=='registration': result=free_registry.registration(target,config)
            else:
                collector_aliases=[value for value in spec['aliases'] if len(value)<=255][:20]
                free_spec=_spec('organization',target,organization_id,max_requests,aliases,False,demo)
                previous=_mode_cached(root,free_spec) if include_netlas else None
                result=dict(previous.data['result']) if previous else free_registry.discover_organization(target,config,aliases=collector_aliases,max_requests=1 if demo else 8)
                result['include_netlas']=include_netlas
                if include_netlas:
                    try: paid=discover_organization(target,config,aliases=collector_aliases,max_requests=spec['max_requests'],**({'demo':True} if demo else {}))
                    except Exception as exc: paid={'partial':True,'errors':[{'source':'netlas','error':_json_safe(str(exc),config=config)}]}
                    for collection in ('domains','networks','ips','asns','entities','errors'):
                        result[collection]=list(result.get(collection) or [])+list(paid.get(collection) or [])
                    result['free_cache_reused']=bool(previous)
                    result['free_cached_requests']=result.get('requests',0) if previous else 0
                    result['free_requests']=0 if previous else result.get('requests',0)
                    result['netlas_requests']=paid.get('requests',0)
                    result['requests']=result['free_requests']+result['netlas_requests']
                    result['keyless']=False
                    result['partial']=bool(result.get('partial') or paid.get('partial') or result.get('errors'))
                    if demo:
                        result['paid_usage_policy']=paid.get('usage_policy',{})
                        result['omitted_paid_collections']=paid.get('omitted_collections',[])
                    result['paid_coverage']='Explicit Netlas opt-in; bounded requests may consume provider credits'

            if not isinstance(result, dict): raise ValueError('Enrichment returned a malformed result')
        except Exception as exc:
            result = {'organization' if kind == 'organization' else 'target': target,
                      'partial': True, 'errors': [{'source': kind, 'error': _json_safe(str(exc), config=config)}]}
        if _cancelled(self): return {'skipped': True, 'reason': 'Enrichment cancelled'}
        if kind=='organization' and (len(spec['aliases'])>20 or any(len(value)>255 for value in spec['aliases'])):
            result['alias_coverage']={'stored':len(spec['aliases']),'considered_at_most':len([value for value in spec['aliases'] if len(value)<=255][:20]),'unsupported_length':sum(len(value)>255 for value in spec['aliases']),'note':'All aliases are retained; at most20 aliases of at most255 characters are passed to the bounded collectors without truncating identities'}
            result['partial']=True
            result['coverage']=(result.get('coverage') or '')+' Alias selection limited to20 names of at most255 characters; all saved aliases remain intact.'
        if demo: result=demo_sample(result)
        result = _bounded(result, config)
        # Preserve the requested identity even if a partial adapter omits it.
        result['organization' if kind == 'organization' else 'target'] = target
        if kind=='services': result['target_kind']='organization' if organization_id else _target_kind(target)
        run = Run.objects.filter(task_id=getattr(self.request, 'id', None)).first()
        from .public_metadata import ingest_public_metadata
        ingest = {'identity_metadata': ingest_public_metadata, 'dns': store.ingest_dns, 'resolve': store.ingest_names,
                  'netblock': store.ingest_netblock, 'organization': store.ingest_organization,'registration':store.ingest_registration,'services':store.ingest_services}[kind]
        with transaction.atomic():
            if root.kind=='organization':
                current=AssetNode.objects.select_for_update().get(pk=root.pk)
                if current.organization_status!='active': return {'skipped':True,'reason':'Organization lifecycle changed while enrichment was running','root_id':str(root.pk)}
            root = ingest(result, run=run)
            result['root_id'] = str(root.pk)
            store.evidence(root, 'enrichment:' + kind, {'cache_key': spec['cache_key'], 'kind': kind,
                'target': target, 'result': result}, expires_at=_cache_expiry(kind, result), run=run)
        return result
    finally:
        ExecutionLease.objects.filter(pk=lease, data__task_id=token).delete()


def _candidate(domain, port, action):
    scheme = ('https' if port in (443, 8443) else 'http') if action in HTTP_ACTIONS else ''
    return {'address': domain, 'port': port, 'transport': 'tcp', 'scheme': scheme, 'service_type': '', 'path': '/'}


def queue_watch_capture(record, watch, domain):
    """Queue candidates without creating a ServiceEndpoint from a certificate.

    The existing watch budget is claimed once per candidate port, with the same
    cooldown shared across repeated matching certificates. Ports/actions/tags
    are frozen; disabling a watch still stops queued follow-up.
    """
    if isinstance(domain, str) and domain.startswith('*.'): return None
    domain = normalize_domain(domain)
    if not watch.enabled or not watch.auto_fetch or not watch.actions or not watch.ports:
        return None
    if domain not in record.domains: return None
    from .certstream import domain_matches
    unicode_domain = domain.encode('ascii').decode('idna')
    if not (any(domain_matches(domain, pattern) for pattern in watch.domains) or
            any(keyword.casefold() in domain.casefold() or keyword.casefold() in unicode_domain.casefold()
                for keyword in watch.keywords if isinstance(keyword, str) and keyword)):
        return None
    actions = validate_actions(watch.actions)
    if 'udp' in actions: raise ValueError('Certificate follow-up requires explicit TCP capture actions')
    ports = watch.ports
    if not isinstance(ports, list) or len(ports) > 10 or any(isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535 for port in ports):
        raise ValueError('Watch capture supports at most ten valid ports')
    from .capture import supported_actions
    selected, skipped = [], []
    class Limited(Exception): pass
    for port in sorted(set(ports)):
        supported = set(supported_actions(_candidate(domain, port, 'http' if set(actions) & HTTP_ACTIONS else 'check')))
        if not set(actions) & supported:
            skipped.append(str(port) + ': selected actions require an observed protocol')
            continue
        try:
            with transaction.atomic():
                cooldown_key = 'watch-capture:' + _digest([domain, port, actions])
                if watch.cooldown_seconds and not acquire_lease(cooldown_key, max(1, min(int(watch.cooldown_seconds), 7 * 86400))):
                    raise Limited
                if not _claim_daily_budget('watch:' + str(watch.pk), max(1, min(int(watch.max_daily_fetches), 10000))):
                    raise Limited
        except Limited:
            skipped.append(str(port) + ': cooldown or watch daily budget')
            continue
        selected.append(port)
    if not selected: return None
    snapshot = {'certificate_id': str(record.pk), 'watch_id': str(watch.pk), 'domain': domain,
                'ports': selected, 'actions': actions, 'tags': tag_names('watch', watch.pk), 'skipped': skipped}
    return enqueue(capture_watch_certificate, kind='certificate_capture', type='certificate_followup',
        provider='certstream', parameters=snapshot, kwargs={'snapshot': snapshot})


def _has_response(result, action):
    metadata = result.get('metadata') or {}
    try: status = int(metadata.get('status') or 0)
    except (TypeError, ValueError): status = 0
    wire = metadata.get('wire_summary') or {}
    return bool(100 <= status <= 599 or metadata.get('reachable') is True or
        metadata.get('identity_verified') is True or wire.get('connection_established') is True or
        result.get('verified_type') or (action == 'screenshot' and any(
            item.get('kind') == 'screenshot' for item in result.get('artifacts', []) if isinstance(item, dict))))


def _http_response(result, action):
    try: status = int((result.get('metadata') or {}).get('status') or 0)
    except (TypeError, ValueError): status = 0
    return 100 <= status <= 599 or (action == 'screenshot' and any(
        item.get('kind') == 'screenshot' for item in result.get('artifacts', []) if isinstance(item, dict)))


def _prepare_artifacts(result):
    prepared, total = [], 0
    root = capture_root().resolve()
    artifacts = result.get('artifacts', [])
    if not isinstance(artifacts, list) or len(artifacts) > 16:
        raise ValueError('Capture returned too many artifacts')
    for item in artifacts:
        path = Path(item['path']).resolve()
        if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError('Capture returned an invalid artifact path/size')
        total += path.stat().st_size
        if total > 16 * 1024 * 1024: raise ValueError('Capture artifacts exceed the combined 16 MiB limit')
        prepared.append((item, path, hashlib.sha256(path.read_bytes()).hexdigest()))
    return prepared


def _discard(result):
    root = capture_root().resolve()
    for item in (result.get('artifacts') or [])[:16]:
        try:
            path = Path(item['path']).resolve()
            if path.is_relative_to(root) and path.is_file() and not EvidenceArtifact.objects.filter(relative_path=str(path.relative_to(root))).exists():
                path.unlink(missing_ok=True)
        except (KeyError, TypeError, OSError): pass


@shared_task(bind=True, name='leakscope.phase3.capture_watch_certificate', soft_time_limit=360, time_limit=370)
def capture_watch_certificate(self, snapshot):
    _claim_execution(self, 'certificate-capture')
    watch = WatchRule.objects.filter(pk=snapshot['watch_id'], enabled=True, auto_fetch=True).first()
    if watch is None or _cancelled(self): return {'skipped': True, 'reason': 'Watch follow-up is disabled or cancelled'}
    record = CertificateRecord.objects.get(pk=snapshot['certificate_id'])
    domain = normalize_domain(snapshot['domain'])
    if domain not in record.domains: raise ValueError('Capture domain is absent from the saved certificate')
    config = dict(get_config()['config'], CAPTURE_DIR=str(capture_root()))
    run = Run.objects.filter(task_id=getattr(self.request, 'id', None)).first()
    endpoints, artifacts, errors = [], [], list(snapshot.get('skipped') or [])[:20]
    attempts, deadline = 0, time.monotonic() + CAPTURE_DEADLINE
    from .capture import CaptureEvidenceError, supported_actions
    planned = []
    for port in snapshot['ports'][:10]:
        for action in validate_actions(snapshot['actions']):
            row = _candidate(domain, port, action)
            if action in supported_actions(row): planned.append((row, action))
            else: errors.append(str(port) + '/' + action + ': action requires an observed protocol')
    limited = False
    for row, action in planned:
        port = row['port']
        if _cancelled(self): return {'skipped': True, 'reason': 'Capture cancelled', 'endpoint_ids': endpoints}
        if attempts >= MAX_CAPTURE_ATTEMPTS or time.monotonic() >= deadline:
            errors.append('Certificate follow-up reached its bounded attempt/time budget')
            limited = True
            break
        result = {}
        attempts += 1
        try:
            if is_blacklisted(domain, {'config': config}): raise ValueError('Capture target is blacklisted')
            try:
                result = capture_endpoint(row, action, config)
            except CaptureEvidenceError as exc:
                result = exc.result
                errors.append(str(port) + '/' + action + ': ' + _json_safe(str(exc), config=config))
            if _cancelled(self):
                _discard(result)
                return {'skipped': True, 'reason': 'Capture cancelled', 'endpoint_ids': endpoints}
            if not isinstance(result, dict) or not _has_response(result, action):
                raise ValueError('Capture returned no response or connection evidence')
            prepared = _prepare_artifacts(result)
            metadata = _json_safe(dict(result.get('metadata') or {}, certificate_id=str(record.pk),
                watch_id=str(watch.pk), source_basis='Actual follow-up capture; certificate triggered the attempt',
                artifacts=[{'kind': item.get('kind', action), 'sha256': digest, 'bytes': path.stat().st_size}
                    for item, path, digest in prepared]), config=config)
            with transaction.atomic():
                observed = dict(row)
                http_observed = action in HTTP_ACTIONS and _http_response(result, action)
                if not http_observed: observed['scheme'] = ''
                verified_type = result.get('verified_type') or (row['scheme'] if http_observed else '')
                endpoint = ingest_observation('fetch:' + action, dict(observed,
                    service_type=str(verified_type), title=metadata.get('title') or '', metadata=metadata))
                action_artifacts = []
                for item, path, digest in prepared:
                    artifact = EvidenceArtifact.objects.create(endpoint=endpoint, run=run,
                        kind=str(item.get('kind', action))[:32], relative_path=str(path.relative_to(capture_root().resolve())),
                        mime=str(item.get('mime') or 'application/octet-stream')[:100], size=path.stat().st_size,
                        sha256=digest, tool=str(item.get('tool') or action)[:100], metadata=_json_safe(item.get('metadata') or {}, config=config))
                    action_artifacts.append(str(artifact.pk))
                set_tags('endpoint', endpoint.pk, snapshot.get('tags') or [], origin='watch:' + str(watch.pk))
                observation = ServiceObservation.objects.filter(endpoint=endpoint, provider='fetch:' + action).order_by('-last_collected_at').first()
                store.ingest_observation_graph(observation)
            if str(endpoint.pk) not in endpoints: endpoints.append(str(endpoint.pk))
            artifacts.extend(action_artifacts)
            if result.get('partial') or metadata.get('partial'):
                errors.append(str(port) + '/' + action + ': partial capture evidence')
        except Exception as exc:
            if isinstance(result, dict): _discard(result)
            errors.append(str(port) + '/' + action + ': ' + _json_safe(str(exc), config=config))
    if not endpoints:
        raise ValueError('No service response was captured: ' + '; '.join(errors)[:1500])
    return {'endpoint_ids': endpoints, 'artifact_ids': artifacts, 'attempts': attempts,
            'partial': bool(errors), 'limited': limited, 'errors': errors[:100], 'certificate_id': str(record.pk)}
