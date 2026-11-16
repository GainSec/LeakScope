"""Durable, idempotent lookup and capture jobs shared by buttons and watches."""
import hashlib
import ipaddress
import json
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from uuid import uuid4

from celery import shared_task
from celery.exceptions import Ignore
from celery.signals import worker_ready
from croniter import croniter
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from leakscope.services.config import get_config, redact
from leakscope.services.runs import enqueue, write_state
from leakscope.shared import is_blacklisted
from leakscope_app.models import SavedLookup, ServiceEndpoint, EvidenceArtifact, DispatchSlot, ExecutionLease, CustomQuery
from .providers import search_provider, effective_page_size
from .enrichment import enrich_host
from .usage_policy import validate_demo, demo_snapshot, demo_metadata
from .state import ingest_observation, tag_names, set_tags, acquire_lease

ACTIONS = {'http', 'httpx', 'screenshot', 'identity', 'tcp', 'udp', 'check'}


def validate_actions(actions):
    if not isinstance(actions, list) or len(actions) > len(ACTIONS) or any(not isinstance(a, str) or a not in ACTIONS for a in actions):
        raise ValueError('Select supported fetch actions')
    return sorted(set(actions))


def validate_capture_options(actions, options=None):
    from .wire import validate_tcp_options, validate_udp_options
    options = {} if options is None else options
    if not isinstance(options, dict) or set(options) - {'tcp', 'udp', 'screenshot'} or set(options) - set(actions):
        raise ValueError('Capture options must belong to a selected action')
    normalized = {}
    for action, validate in [('tcp', validate_tcp_options), ('udp', validate_udp_options)]:
        if action in actions:
            value, _ = validate(options.get(action))
            if action in options: normalized[action] = value
    if 'screenshot' in options:
        from .browser_client import validate_options
        normalized['screenshot'] = validate_options(options['screenshot'])
    return normalized


def next_occurrence(*, interval_seconds=None, cron='', timezone_name='America/New_York', now=None):
    now = now or timezone.now()
    try: zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError, TypeError): raise ValueError('Unknown schedule timezone') from None
    if cron:
        if not isinstance(cron, str) or len(cron.split()) != 5 or len(cron) > 100 or not croniter.is_valid(cron):
            raise ValueError('Cron must have five valid fields')
        if interval_seconds: raise ValueError('Choose an interval or cron, not both')
        return croniter(cron, now.astimezone(zone)).get_next(datetime).astimezone(dt_timezone.utc)
    if interval_seconds is None: return None
    if isinstance(interval_seconds, bool): raise ValueError('Invalid interval')
    try: interval_seconds = int(interval_seconds)
    except (ValueError, TypeError): raise ValueError('Invalid interval') from None
    if not 60 <= interval_seconds <= 366 * 86400: raise ValueError('Schedule interval must be 60 seconds to 366 days')
    return now + timedelta(seconds=interval_seconds)


def scoped_query(provider, query, scope):
    from leakscope.intelligence.queries import _scope, _quote
    scope = _scope(scope or {})
    if not isinstance(query, str) or not query.strip() or len(query) > 8000: raise ValueError('Invalid provider query')
    if provider not in {'shodan', 'zoomeye', 'netlas'}: raise ValueError('Unknown search provider')
    clauses = []
    if scope.get('network'):
        network = scope['network']
        field = ('cidr' if '/' in network else 'ip') if provider == 'zoomeye' else ('net' if provider == 'shodan' and '/' in network else 'ip')
        clauses.append(field + ('=' if provider == 'zoomeye' else ':') + _quote(network, provider))
    if scope.get('country'):
        field = 'geo.country' if provider == 'netlas' else 'country'
        clauses.append(field + ('=' if provider == 'zoomeye' else ':') + _quote(scope['country'], provider))
    for term in filter(None, [x.strip() for x in scope.get('exclude', '').split(',')]):
        value = _quote(term, provider)
        clauses.append('-' + value if provider == 'shodan' else ('http.body!=' + value if provider == 'zoomeye' else 'NOT http.body:' + value))
    if not clauses: return query
    join = {'shodan': ' ', 'zoomeye': ' && ', 'netlas': ' AND '}[provider]
    return (query if provider == 'shodan' else '(' + query + ')') + join + join.join(clauses)


def lookup_snapshot(lookup):
    snapshot={name: getattr(lookup, name) for name in ('provider', 'query', 'service_type', 'scope', 'actions', 'auto_fetch', 'collection_mode',
        'page_count', 'start_page', 'page_size', 'max_results', 'cooldown_seconds', 'max_daily_runs')} | {'tags': tag_names('lookup', lookup.pk)}
    return demo_snapshot(snapshot)


def search_progress_key(provider, query, page_size):
    """Identity of one paged result sequence: provider, exact scoped query and page size."""
    return hashlib.sha256(json.dumps([provider, query.strip(), effective_page_size(provider, page_size)]).encode()).hexdigest()


def search_progress(provider, query, page_size):
    from leakscope_app.models import SearchProgress
    return SearchProgress.objects.filter(pk=search_progress_key(provider, query, page_size)).first()


def _remember_progress(provider, query, result):
    """Keep the furthest page reached; a later run from an earlier page never moves it back."""
    from leakscope_app.models import SearchProgress
    reached, size = result.get('next_page'), result.get('page_size')
    if not isinstance(reached, int) or not isinstance(size, int) or not result.get('pages'): return
    with transaction.atomic():
        row, created = SearchProgress.objects.select_for_update().get_or_create(pk=search_progress_key(provider, query, size),
            defaults={'provider': provider, 'page_size': size, 'next_page': reached})
        if not created and reached < row.next_page: return
        row.next_page = reached
        row.exhausted = not result.get('more_available') and not result.get('errors')
        total = result.get('total')
        row.total = total if result.get('total_is_exact', True) and isinstance(total, int) and 0 <= total < 2**31 else None
        row.save()


def queue_lookup(lookup, *, slot_id=None, demo=False):
    demo=validate_demo(demo)
    snapshot=demo_snapshot(lookup_snapshot(lookup),demo)
    if not _claim_daily_budget('lookup:' + str(lookup.pk), lookup.max_daily_runs):
        from leakscope_app.models import Run
        reason = 'Daily lookup job budget reached (UTC day)'
        SavedLookup.objects.filter(pk=lookup.pk).update(last_state='skipped', last_error=reason)
        return Run.objects.create(kind='lookup', provider=lookup.provider, type=lookup.service_type,
            state='skipped', finished_at=timezone.now(), error=reason,
            parameters=json.dumps({'lookup_id': str(lookup.pk), 'collection_mode': snapshot['collection_mode'] or 'custom'}), result=json.dumps({'skipped': True, 'reason': reason}))
    run = enqueue(run_lookup, kind='lookup', type=lookup.service_type, provider=lookup.provider,
        parameters={'lookup_id': str(lookup.pk), 'slot_id': str(slot_id) if slot_id else None,
                    'collection_mode': snapshot['collection_mode'] or 'custom',**({'demo':True,'effective_limits':{'pages':1,'saved_results':1}} if snapshot['collection_mode']=='demo' else {})},
        kwargs={'lookup_id': str(lookup.pk), 'snapshot': snapshot, 'slot_id': str(slot_id) if slot_id else None})
    SavedLookup.objects.filter(pk=lookup.pk).update(last_state=run.state, last_error=run.error)
    if lookup.custom_query_id: CustomQuery.objects.filter(pk=lookup.custom_query_id).update(last_status=run.state)
    return run


def _claim_execution(task, label, slot_id=None):
    from leakscope_app.models import Run
    from leakscope.services.runs import TERMINAL
    task_id = getattr(task.request, 'id', None)
    if task_id and Run.objects.filter(task_id=task_id, state__in=TERMINAL).exists(): raise Ignore()
    if slot_id and not acquire_lease('lookup-slot:' + str(slot_id), 7 * 86400): raise Ignore()
    if task_id and not acquire_lease(label + '-task:' + task_id, 7 * 86400): raise Ignore()


def _collection_note(collection, endpoint, reason):
    if len(collection['notes']) < 20:
        collection['notes'].append({'endpoint_id': str(endpoint.pk), 'reason': redact(reason)[:500]})


def _collect_endpoint(endpoint, spec, collection, tools=None):
    mode = collection['mode']
    if mode in {'passive','demo'}:
        return
    try:
        if mode == 'custom':
            if not spec.get('auto_fetch') or not spec.get('actions'):
                return
            actions, skipped = spec['actions'], []
        else:
            from .collection_policy import plan_collection
            plan = plan_collection(endpoint_snapshot(endpoint), mode, tools=tools)
            actions, skipped = plan['actions'], plan['skipped']
        if skipped:
            _collection_note(collection, endpoint, '; '.join(skipped))
        if not actions:
            collection['skipped'] += 1
            return
        policy = {'collection_mode': mode, 'skipped_actions': skipped} if mode != 'custom' else {}
        child = queue_capture(endpoint.pk, actions, origin='lookup:' + str(spec['_lookup_id']),
            cooldown_seconds=spec.get('cooldown_seconds', 3600), max_daily=100, **policy)
        if child is None:
            collection['limited'] += 1
            _collection_note(collection, endpoint, 'Capture was not queued because of cooldown or the capture budget.')
        elif child.state in ('failed', 'lost', 'cancelled'):
            collection['failed'] += 1
            _collection_note(collection, endpoint, 'Capture job could not start: ' + str(child.error or child.state))
        else:
            collection['queued'] += 1
            if len(collection['run_ids']) < 100:
                collection['run_ids'].append(str(child.pk))
    except Exception as exc:
        collection['failed'] += 1
        _collection_note(collection, endpoint, 'Capture could not be queued: ' + redact(exc))


def _draw_banner(endpoint, row, images, maximum=100):
    """Draw a saved non-web provider banner as an evidence image; local work only."""
    from .protocol_hints import banner_text
    text, _ = banner_text(row.get('metadata') if isinstance(row.get('metadata'), dict) else {})
    if not text or text.lstrip()[:5].upper() == 'HTTP/': return
    if images['drawn'] >= maximum:
        images['deferred'] += 1
        return
    from .evidence_jobs import render_quietly
    outcome = render_quietly(endpoint, budget=2)
    images['drawn'] += outcome['drawn']
    images['existing'] += outcome['existing']


@shared_task(bind=True, name='leakscope.phase3.run_lookup')
def run_lookup(self, lookup_id, snapshot=None, slot_id=None):
    lookup = SavedLookup.objects.get(pk=lookup_id)
    spec = snapshot or lookup_snapshot(lookup)
    spec=demo_snapshot(spec,validate_demo(spec.get('demo',False)))
    demo=spec.get('collection_mode')=='demo'
    _claim_execution(self, 'lookup', slot_id)
    if slot_id and not lookup.enabled: return {'skipped': True, 'reason': 'Scheduled lookup was paused'}
    SavedLookup.objects.filter(pk=lookup.pk).update(last_run_at=timezone.now(), last_state='running', last_error='')
    cfg = get_config()['config']
    try:
        from .collection_policy import validate_collection_mode
        mode=validate_collection_mode(spec.get('collection_mode',''),allow_custom=True)
        if spec['provider'] == 'research':
            from leakscope.intelligence.clients import research_queries
            result = research_queries(spec['query'], cfg,**({'demo':True} if demo else {}))
            # Store sourced suggestions through the same normalizer as manual research.
            from leakscope_app.intelligence import save_research_suggestions
            candidates = save_research_suggestions(result, cfg)
            result = {'total': len(candidates), 'sources': result['sources'], 'suggestions': candidates, 'warnings': result['warnings'],**({k:result[k] for k in ('demo','partial','usage_policy') if k in result} if demo else {})}
        else:
            query = scoped_query(spec['provider'], spec['query'], spec.get('scope'))
            result = search_provider(spec['provider'], query, cfg, page_count=spec.get('page_count', 1), start_page=spec.get('start_page', 1),
                page_size=min(100, spec.get('page_size', spec.get('max_results', 100)), spec.get('max_results', 100)),**({'demo':True} if demo else {}))
            if not demo: _remember_progress(spec['provider'], query, result)
            collection = {'mode': mode or 'custom', 'queued': 0, 'skipped': 0,
                          'limited': 0, 'failed': 0, 'run_ids': [], 'notes': []}
            spec = dict(spec, _lookup_id=str(lookup.pk))
            tools_row = ExecutionLease.objects.filter(pk='capture-tools', expires_at__gt=timezone.now()).first() if mode and mode not in {'passive','demo'} else None
            tools = tools_row.data if tools_row and isinstance(tools_row.data, dict) else None
            endpoints, filtered, invalid_rows, repeats = [], 0, 0, 0
            images = {'drawn': 0, 'existing': 0, 'deferred': 0, 'network_contact': False}
            maximum = min(10000, int(spec.get('max_results', 100)))
            rows = result.get('observations', [])
            for row in rows[:maximum]:
                if not isinstance(row, dict) or not isinstance(row.get('metadata') or {}, dict):
                    invalid_rows += 1; continue
                if is_blacklisted(row.get('address', ''), {'config': cfg}) or is_blacklisted((row.get('metadata') or {}).get('resolved_ip', ''), {'config': cfg}):
                    filtered += 1; continue
                if not row.get('service_type'):
                    row['service_type'] = spec.get('service_type') or ''
                    row['metadata'] = dict(row.get('metadata') or {}, service_type_basis='Requested search profile; identity not verified')
                row['metadata'] = dict(row.get('metadata') or {}, collection_scope=spec.get('scope') or {},
                                       lookup_id=str(lookup.pk), matched_profile=spec.get('service_type') or '')
                try: endpoint = ingest_observation(spec['provider'], row)
                except (ValueError, TypeError):
                    invalid_rows += 1; continue
                endpoints.append(str(endpoint.pk)); repeats += bool(getattr(endpoint, 'repeat_capture', False))
                set_tags('endpoint', endpoint.pk, spec.get('tags', []), origin='lookup:' + str(lookup.pk))
                _collect_endpoint(endpoint, spec, collection, tools)
                _draw_banner(endpoint, row, images)
            result = {key: value for key, value in result.items() if key != 'observations'} | {
                'endpoint_ids': endpoints, 'stored': len(endpoints), 'new_results': len(endpoints) - repeats, 'repeat_results': repeats, 'filtered': filtered,
                'invalid_rows': invalid_rows, 'partial': bool(result.get('partial') or invalid_rows or collection['failed']),
                'collection': collection, 'evidence_images': images,
                'limited': len(rows) > maximum, 'query': query}
            if invalid_rows: result['errors'] = list(result.get('errors') or []) + [str(invalid_rows) + ' invalid provider observations omitted']
        if demo:
            result['demo']=True
            result['partial']=True
            result.setdefault('usage_policy',demo_metadata(max_pages=1,max_results=1,target_requests=0)['usage_policy'])
        SavedLookup.objects.filter(pk=lookup.pk).update(last_state='succeeded', last_error='')
        if lookup.custom_query_id: CustomQuery.objects.filter(pk=lookup.custom_query_id).update(last_status='succeeded', last_run_at=timezone.now())
        return result
    except Exception as exc:
        SavedLookup.objects.filter(pk=lookup.pk).update(last_state='failed', last_error=redact(exc))
        if lookup.custom_query_id: CustomQuery.objects.filter(pk=lookup.custom_query_id).update(last_status='failed', last_run_at=timezone.now())
        raise ValueError(redact(exc)) from None


def capture_endpoint(endpoint, action, config):
    from .capture import capture_endpoint as capture
    return capture(endpoint, action, config)


def capture_root():
    return Path(getattr(settings, 'LEAKSCOPE_CAPTURE_DIR', settings.EXPORT_DIR.parent / 'captures')).resolve()


@shared_task(name='leakscope.phase3.cleanup_captures')
def cleanup_captures():
    days = max(1, int(getattr(settings, 'LEAKSCOPE_CAPTURE_RETENTION_DAYS', 30)))
    cutoff = timezone.now() - timedelta(days=days)
    removed = 0
    from .gallery import protected_artifacts,delete_derivative
    candidates=EvidenceArtifact.objects.filter(created_at__lt=cutoff).exclude(pk__in=protected_artifacts().values('pk'))
    for identifier in candidates.values_list('pk',flat=True).iterator(chunk_size=200):
        with transaction.atomic():
            artifact=EvidenceArtifact.objects.select_for_update().filter(pk=identifier,created_at__lt=cutoff).first()
            if artifact is None or protected_artifacts().filter(pk=identifier).exists():continue
            path=(capture_root()/artifact.relative_path).resolve()
            if path.is_relative_to(capture_root()):
                try:path.unlink(missing_ok=True)
                except OSError:continue
            try:delete_derivative(artifact)
            except OSError:
                import logging
                logging.getLogger(__name__).warning('Could not remove expired capture derivative for %s',artifact.pk)
            artifact.delete();removed+=1
    from .browser_client import cleanup_snapshots
    cleanup_snapshots(capture_root())
    ExecutionLease.objects.filter(expires_at__lt=timezone.now()).exclude(key__startswith='browser-snapshot:').delete()
    return {'artifacts_removed': removed, 'retention_days': days}


@shared_task(name='leakscope.phase3.capture_health')
def capture_health():
    import shutil
    from .browser_client import cleanup_snapshots
    cleanup_snapshots(capture_root())
    tools = {name: bool(shutil.which(name)) for name in ('curl', 'httpx', 'gowitness')}
    from .browser_client import request
    try: tools['playwright']=request('GET','/health',timeout=3).get('available') is True
    except ValueError: tools['playwright']=False
    ExecutionLease.objects.update_or_create(key='capture-tools', defaults={
        'expires_at': timezone.now() + timedelta(minutes=5), 'data': tools})
    return tools


@worker_ready.connect(weak=False)
def _capture_worker_ready(**kwargs):
    import os
    if os.environ.get('LEAKSCOPE_WORKER_KIND') == 'fetch': capture_health.delay()


@transaction.atomic
def _claim_daily_budget(origin, maximum):
    key = 'daily-budget:' + hashlib.sha256(str(origin).encode()).hexdigest() + ':' + timezone.now().date().isoformat()
    row, _ = ExecutionLease.objects.get_or_create(key=key, defaults={'expires_at': timezone.now() + timedelta(days=2), 'data': {'count': 0}})
    row = ExecutionLease.objects.select_for_update().get(pk=key)
    count = row.data.get('count', 0)
    if count >= maximum: return False
    row.data = {'count': count + 1}
    row.save(update_fields=['data'])
    return True


def queue_capture(endpoint_id, actions, *, origin='', cooldown_seconds=3600, max_daily=100, options=None, collection_mode='', skipped_actions=None, resource_url=None):
    actions = validate_actions(actions)
    if not actions: return None
    options = validate_capture_options(actions, options)
    endpoint = ServiceEndpoint.objects.get(pk=endpoint_id)
    snapshot = endpoint_snapshot(endpoint)
    if resource_url is not None:
        from .capture import _resource_path
        from .state import normalize_address
        if not isinstance(resource_url, str): raise ValueError('Invalid capture resource URL')
        parsed = urlsplit(resource_url)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Invalid capture resource URL')
        if normalize_address(parsed.hostname) != endpoint.address or (parsed.port or (443 if parsed.scheme == 'https' else 80)) != endpoint.port:
            raise ValueError('Capture resource must belong to the observed endpoint')
        snapshot['path'] = _resource_path(parsed.path or '/')
        snapshot['scheme'] = parsed.scheme
    if collection_mode:
        from .collection_policy import validate_collection_mode
        snapshot['collection_mode'] = validate_collection_mode(collection_mode)
        snapshot['collection_skips'] = list(skipped_actions or [])[:10]
    from .capture import supported_actions
    unavailable = sorted(set(actions) - set(supported_actions(snapshot)))
    if unavailable and origin:
        # Watch/lookup policies can encounter mixed transports. Keep collection
        # running and apply only actions the actual endpoint can support.
        actions = [action for action in actions if action not in unavailable]
        if not actions: return None
    elif unavailable:
        raise ValueError('A selected capture action is unavailable for this endpoint transport or service')
    options = {action: value for action, value in options.items() if action in actions}
    if 'screenshot' in options:
        if endpoint.service_type=='vnc': raise ValueError('Native VNC screenshots do not use browser options')
        session_id=options['screenshot'].get('session_id')
        if session_id:
            from .browser_client import assert_session
            assert_session(session_id,endpoint.pk)
        snapshot_id=options['screenshot'].get('snapshot_id')
        if snapshot_id:
            from .browser_client import assert_snapshot
            assert_snapshot(snapshot_id,endpoint.pk)
    for action, value in options.items(): snapshot[action + '_options'] = value
    profile = hashlib.sha256(json.dumps([actions, snapshot['path'], options], sort_keys=True).encode()).hexdigest()[:16]
    class Skipped(Exception): pass
    try:
        with transaction.atomic():
            if cooldown_seconds and not acquire_lease('capture:' + str(endpoint.pk) + ':' + profile, max(1, int(cooldown_seconds))): raise Skipped
            if origin and not _claim_daily_budget(origin, min(10000, max(1, int(max_daily)))): raise Skipped
    except Skipped:
        return None
    return enqueue(fetch_endpoint, kind='capture', type=endpoint.service_type,
        parameters={'endpoint_id': str(endpoint.pk), 'actions': actions, 'origin': origin, 'target': snapshot,
                    'options': options, 'unavailable_actions': unavailable,
                    'collection_mode': collection_mode or 'custom', 'skipped_actions': list(skipped_actions or [])[:10]},
        kwargs={'endpoint_id': str(endpoint.pk), 'actions': actions, 'snapshot': snapshot})


def endpoint_snapshot(endpoint, observation=None):
    row = {key: getattr(endpoint, key) for key in ('address', 'port', 'transport', 'scheme', 'service_type')}
    row.update(id=str(endpoint.pk), path='/')
    observations = [observation] if observation else list(endpoint.observations.filter(hidden=False).only('data', 'provider', 'hidden', 'endpoint').all()[:12])
    for item in observations:
        if item.endpoint_id != endpoint.pk or item.hidden: raise ValueError('Observation is not available for this endpoint')
    # A provider's saved response text can name the protocol of a service on an
    # unusual port. That selects a native reader; it is not a verification.
    from .protocol_hints import routing_service, HINT_BASIS
    service, hint = routing_service(endpoint.service_type, [(item.provider, item.data) for item in observations])
    if service != endpoint.service_type:
        row['service_type'] = service
        if hint and hint['strength'] == 'strong':
            row['service_type_basis'] = HINT_BASIS
            row['protocol_hint'] = {key: hint[key] for key in ('service', 'strength', 'basis', 'evidence', 'field', 'provider')}
    for item in observations:
        metadata = item.data.get('metadata') or {}
        urls = [metadata.get(key) for key in ('uri', 'url', 'source_url')]
        for value in urls:
            if not isinstance(value, str): continue
            try:
                parsed = urlsplit(value)
                from .state import normalize_address
                if parsed.scheme not in {'http', 'https'} or not parsed.hostname or normalize_address(parsed.hostname) != endpoint.address:
                    continue
                if parsed.username or parsed.password or (parsed.port or (443 if parsed.scheme == 'https' else 80)) != endpoint.port: continue
                from .capture import _resource_path
                row['path'] = _resource_path(parsed.path or '/')
                row['scheme'] = parsed.scheme
                return row
            except ValueError:
                continue
    return row


@shared_task(bind=True, name='leakscope.phase3.fetch_endpoint')
def fetch_endpoint(self, endpoint_id, actions, snapshot=None):
    actions = validate_actions(actions)
    if not actions: raise ValueError('Select a fetch action')
    _claim_execution(self, 'capture')
    task_id = getattr(self.request, 'id', None)
    endpoint = ServiceEndpoint.objects.get(pk=endpoint_id)
    config = dict(get_config()['config'], CAPTURE_DIR=str(capture_root()))
    row = snapshot or endpoint_snapshot(endpoint)
    options = validate_capture_options(actions, {action: row[action + '_options'] for action in ('tcp', 'udp', 'screenshot') if action + '_options' in row})
    for action, value in options.items(): config[action.upper() + '_OPTIONS'] = value
    results, errors, artifacts, partial = {}, [], [], False
    error_details=[]
    from .capture_diagnostics import error_detail, CaptureBatchError
    from leakscope_app.models import Run
    run = Run.objects.filter(task_id=task_id).first() if task_id else None
    def eligible():
        from .state import visible_endpoints
        return visible_endpoints().filter(pk=endpoint.pk).exists() and not (run and Run.objects.filter(pk=run.pk,state='cancelled').exists())
    def discard(result):
        root=capture_root().resolve()
        for item in (result.get('artifacts') or [])[:16]:
            try:
                path=Path(item['path']).resolve()
                if path.is_relative_to(root) and path.is_file() and not EvidenceArtifact.objects.filter(relative_path=str(path.relative_to(root))).exists():path.unlink(missing_ok=True)
            except (KeyError,TypeError,OSError):pass
    for action in actions:
        if not eligible():return {'cancelled':True,'results':results,'artifact_ids':artifacts,'reason':'Source hidden or run cancelled'}
        generated = []
        try:
            from .capture import CaptureEvidenceError
            try:
                result = capture_endpoint(row, action, config)
            except CaptureEvidenceError as exc:
                result = exc.result
                errors.append(action + ': ' + redact(exc))
                error_details.append(error_detail(exc,action))
            if not eligible():
                discard(result)
                return {'cancelled':True,'results':results,'artifact_ids':artifacts,'reason':'Source hidden or run cancelled during capture'}
            partial = partial or bool(result.get('partial') or (result.get('metadata') or {}).get('partial'))
            prepared = []
            total_size = 0
            for item in result.get('artifacts', []):
                path = Path(item['path']).resolve()
                if not path.is_relative_to(capture_root()) or not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
                    raise ValueError('Capture returned an invalid artifact path/size')
                total_size += path.stat().st_size
                if total_size > 16 * 1024 * 1024: raise ValueError('Capture artifacts exceed the combined 16 MiB limit')
                generated.append(path)
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                prepared.append((item, path, digest))
            action_artifacts = []
            with transaction.atomic():
                ServiceEndpoint.objects.select_for_update().get(pk=endpoint.pk)
                if not eligible():
                    discard(result)
                    return {'cancelled':True,'results':results,'artifact_ids':artifacts,'reason':'Source hidden or run cancelled before persistence'}
                for item, path, digest in prepared:
                    artifact = EvidenceArtifact.objects.create(endpoint=endpoint, run=run, kind=str(item.get('kind', action))[:32],
                        relative_path=str(path.relative_to(capture_root())), mime=str(item.get('mime', 'application/octet-stream'))[:100],
                        size=path.stat().st_size, sha256=digest, tool=str(item.get('tool', action))[:100], metadata=item.get('metadata', {}))
                    action_artifacts.append(str(artifact.pk))
                # A reader chosen from a provider hint that did not verify the protocol
                # must not change what the endpoint is recorded as.
                hinted = isinstance(row.get('protocol_hint'), dict)
                service_type = str(result.get('verified_type') or (endpoint.service_type if hinted else row['service_type']))[:100]
                metadata = dict(result.get('metadata') or {})
                if row.get('collection_mode'): metadata['collection_mode'] = row['collection_mode']
                # Keep how the reader was chosen on what it saved.
                if hinted: metadata.setdefault('protocol_hint', row['protocol_hint'])
                metadata['artifacts'] = [{'kind': item.get('kind', action), 'sha256': digest, 'bytes': path.stat().st_size} for item, path, digest in prepared]
                metadata['content_hashes'] = [{'kind': item.get('kind', action), 'sha256': digest} for item, path, digest in prepared
                    if item.get('kind') in {'response', 'screenshot'} or (action in {'tcp', 'udp'} and item.get('kind') == 'wire-response')]
                if result.get('verified_type'): metadata['identity_verified'] = True
                updated = ingest_observation('fetch:' + action, dict(row, title=metadata.get('title') or endpoint.title,
                    service_type=service_type, metadata=metadata))
                source=updated.observations.filter(provider='fetch:'+action).order_by('-last_collected_at').first()
                for artifact_id in action_artifacts:
                    artifact=EvidenceArtifact.objects.get(pk=artifact_id)
                    artifact.metadata=dict(artifact.metadata,observation_pk=source.pk.hex)
                    artifact.save(update_fields=['metadata'])
            results[action] = metadata
            artifacts.extend(action_artifacts)
            endpoint = updated
            if result.get('verified_type') or not hinted: row['service_type'] = updated.service_type
            if action in ('identity', 'tcp', 'udp') or (action == 'screenshot' and row.get('service_type') == 'vnc'):
                # Draw the saved exchange as a labelled image; local work only.
                from .evidence_jobs import render_quietly
                artifacts.extend(render_quietly(updated, run)['artifact_ids'])
        except Exception as exc:
            for path in generated:
                relative = str(path.relative_to(capture_root()))
                if not EvidenceArtifact.objects.filter(relative_path=relative).exists(): path.unlink(missing_ok=True)
            errors.append(action + ': ' + redact(exc))
            error_details.append(error_detail(exc,action))
    if not results:raise CaptureBatchError({'results':{},'artifact_ids':artifacts,'partial':True,'errors':errors,'error_details':error_details})
    return {'results': results, 'artifact_ids': artifacts, 'partial': bool(errors) or partial, 'errors': errors,
            'error_details':error_details,
            'collection_mode': row.get('collection_mode') or 'custom', 'skipped_actions': row.get('collection_skips', [])}


@shared_task(bind=True, name='leakscope.phase3.enrich_endpoint')
def enrich_endpoint(self, endpoint_id, collections=None, demo=False):
    demo=validate_demo(demo)
    _claim_execution(self, 'enrich')
    from .state import visible_endpoints
    endpoint = visible_endpoints().get(pk=endpoint_id)
    try:
        result = enrich_host(endpoint.address, get_config()['config'], collections,**({'demo':True} if demo else {}))
        ingest_observation('netlas:enrich', {'address': endpoint.address, 'port': endpoint.port,
            'transport': endpoint.transport, 'scheme': endpoint.scheme, 'service_type': endpoint.service_type,
            'title': endpoint.title, 'metadata': {'enrichment': result['results'], 'errors': result.get('errors', []),
                                                'source_basis': 'Netlas indexed host/DNS/certificate records; not a live service check'}})
        return result
    except Exception as exc: raise ValueError(redact(exc)) from None


def _submit_slot(slot_id):
    from leakscope_app.models import Run
    now = timezone.now()
    with transaction.atomic():
        slot = DispatchSlot.objects.select_for_update(of=('self',)).select_related('lookup', 'run').get(pk=slot_id)
        if slot.state != 'pending' or not slot.lookup.enabled or (slot.next_attempt_at and slot.next_attempt_at > now): return
        if slot.run_id and slot.run.state in {'running', 'succeeded', 'failed', 'cancelled', 'skipped', 'lost'}:
            slot.state = 'submitted'; slot.save(update_fields=['state']); return
        if not slot.run_id:
            if not _claim_daily_budget('lookup:' + str(slot.lookup_id), slot.lookup.max_daily_runs):
                slot.state = 'skipped'
                slot.last_error = 'Daily lookup job budget reached (UTC day)'
                slot.save(update_fields=['state', 'last_error'])
                SavedLookup.objects.filter(pk=slot.lookup_id).update(last_state='skipped', last_error=slot.last_error)
                return
            slot.snapshot = slot.snapshot or lookup_snapshot(slot.lookup)
            slot.run = Run.objects.create(task_id=str(uuid4()), kind='lookup', type=slot.lookup.service_type, provider=slot.lookup.provider,
                parameters=json.dumps({'lookup_id': str(slot.lookup_id), 'slot_id': str(slot.pk),
                                       'collection_mode': slot.snapshot.get('collection_mode') or 'custom'}))
        slot.attempts += 1
        # Reserve the next attempt before publishing; another dispatcher cannot
        # race a slow/ambiguous broker operation. Retries reuse this run/task ID.
        slot.next_attempt_at = now + timedelta(seconds=min(300, 30 * 2 ** slot.attempts))
        slot.save(update_fields=['run', 'snapshot', 'attempts', 'next_attempt_at'])
        run_id, task_id, snapshot = slot.run_id, slot.run.task_id, slot.snapshot
    try:
        run_lookup.apply_async(kwargs={'lookup_id': str(slot.lookup_id), 'snapshot': snapshot, 'slot_id': str(slot.pk)}, task_id=task_id, retry=False)
    except Exception as exc:
        message = redact(exc)
        final = slot.attempts >= 3
        DispatchSlot.objects.filter(pk=slot.pk).update(state='failed' if final else 'pending', last_error=message)
        Run.objects.filter(pk=run_id, state='queued').update(error=message)
        if final: write_state(task_id, 'FAILURE', {'error': message, 'category': 'dispatch', 'attempts': slot.attempts})
    else:
        DispatchSlot.objects.filter(pk=slot.pk).update(state='submitted', last_error='')
        Run.objects.filter(pk=run_id, state__in=['queued', 'running']).update(error='')


@shared_task(name='leakscope.phase3.dispatch_due')
def dispatch_due():
    now = timezone.now()
    claimed = 0
    for lookup_id in SavedLookup.objects.filter(enabled=True, next_run_at__lte=now).values_list('pk', flat=True)[:100]:
        with transaction.atomic():
            lookup = SavedLookup.objects.select_for_update().get(pk=lookup_id)
            if not lookup.enabled or not lookup.next_run_at or lookup.next_run_at > now: continue
            day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            if DispatchSlot.objects.filter(lookup=lookup, scheduled_for__gte=day_start).count() >= lookup.max_daily_runs:
                lookup.next_run_at = day_start + timedelta(days=1)
                lookup.save(update_fields=['next_run_at'])
                continue
            slot, created = DispatchSlot.objects.get_or_create(lookup=lookup, scheduled_for=lookup.next_run_at, defaults={'snapshot': lookup_snapshot(lookup)})
            # Compute from NOW to coalesce outages instead of replaying all missed slots.
            lookup.next_run_at = next_occurrence(interval_seconds=lookup.interval_seconds, cron=lookup.cron, timezone_name=lookup.timezone, now=now)
            lookup.save(update_fields=['next_run_at'])
            if created:
                claimed += 1
                transaction.on_commit(lambda pk=slot.pk: _submit_slot(pk))
    # Pending committed slots are an outbox; publication failures are visible and
    # recover on the next tick. The logical slot lease prevents repeated work.
    from django.db.models import Q
    for pk in DispatchSlot.objects.filter(state='pending', lookup__enabled=True).filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now)).values_list('pk', flat=True)[:100]:
        transaction.on_commit(lambda pk=pk: _submit_slot(pk))
    return {'claimed': claimed}
