"""Private workbench collection, inventory, scheduling and analysis endpoints."""
import json
import math
import re
from datetime import timedelta
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from django.db.models import Count, Q, Value
from django.db import transaction
from django.db.models.functions import TruncDate
from django.http import JsonResponse, FileResponse
from django.utils import timezone

from .discovery_catalog import catalog as protocol_catalog, catalog_entry
from leakscope.phase3.providers import available_providers, account_info
from leakscope.phase3.state import normalize_address, tag_names, set_tags, visible_endpoints
from leakscope.phase3.tags import tag_details
from leakscope.phase3.jobs import validate_actions, validate_capture_options, next_occurrence, queue_lookup, queue_capture, capture_root
from leakscope.services.config import get_config
from leakscope.services.runs import serialize_run
from .models import (SavedLookup, WatchRule, StreamSettings, StreamHealth, ServiceEndpoint,
                     ServiceObservation, EvidenceArtifact, TagAssignment, ExecutionLease, CustomQuery,
                     CertificateRecord, CertificateWatchMatch, AssetNode)
from leakscope.phase3.usage_policy import validate_demo
from .workbench import endpoint, _body, _number, _flag, _model


def _cfg(): return get_config()['config']


def _stamp(value): return value.isoformat() if value else None


def _integer(data, name, default, low, high):
    value = data.get(name, default)
    if isinstance(value, bool): raise ValueError('Invalid ' + name)
    try: value = int(value)
    except (TypeError, ValueError): raise ValueError('Invalid ' + name) from None
    if not low <= value <= high: raise ValueError(name + ' is outside its supported range')
    return value


def _text(data, name, default='', maximum=1000, required=False):
    value = data.get(name, default)
    if not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 and c not in '\n\t' for c in value):
        raise ValueError('Invalid ' + name)
    value = value.strip()
    if required and not value: raise ValueError('Enter ' + name)
    return value


def _fetch_actions(endpoint):
    from leakscope.phase3.capture import supported_actions
    from leakscope.phase3.jobs import endpoint_snapshot
    return supported_actions(endpoint_snapshot(endpoint))


def _capture_counts(keys):
    """Times each endpoint's visible results were saved, summed over sources and versions."""
    from django.db.models import Sum
    from .models import ServiceObservation
    return {row['endpoint_id']: row['n'] for row in ServiceObservation.objects.filter(endpoint_id__in=keys, hidden=False)
            .values('endpoint_id').annotate(n=Sum('collection_count'))}


def _endpoint(row, count=None, snapshot=None, captures=None):
    from leakscope.phase3.jobs import endpoint_snapshot
    from leakscope.phase3.native import identity_capability
    from leakscope.phase3.capture import _endpoint as parse_endpoint, supported_actions
    snapshot = snapshot or endpoint_snapshot(row)
    identity=identity_capability(parse_endpoint(snapshot)[3])
    identity['available']=row.transport in identity['transports']
    resource = snapshot['path']
    actions = supported_actions(snapshot)
    # The stored type stays as saved. When it is only a placeholder, a provider's
    # response text may name the protocol; that is reported, never verified.
    hint = snapshot.get('protocol_hint')
    return {'id': str(row.pk), 'address': row.address, 'port': row.port, 'transport': row.transport,
            'scheme': row.scheme, 'service_type': row.service_type, 'title': row.title,
            'effective_service_type': snapshot['service_type'], 'protocol_hint': hint,
            'service_type_basis': 'provider_hint' if hint else 'stored',
            'first_seen': _stamp(row.first_seen), 'last_seen': _stamp(row.last_seen),
            **tag_details('endpoint', row.pk), 'observation_count': count if count is not None else row.observations.filter(hidden=False).count(),
            'collection_count': captures if captures is not None else _capture_counts([row.pk]).get(row.pk, 0),
            'resource_path': resource,'native_identity':identity,
            'fetch_actions': actions, 'default_actions': ['screenshot'] if row.service_type=='vnc' and 'screenshot' in actions else ['identity'] if 'identity' in actions else (['http'] if 'http' in actions else (['tcp'] if 'tcp' in actions else []))}


def _lookup(row):
    names = ('name', 'provider', 'query', 'service_type', 'scope', 'actions', 'auto_fetch', 'collection_mode', 'enabled',
             'interval_seconds', 'cron', 'timezone', 'page_count', 'start_page', 'page_size', 'max_results', 'max_daily_runs', 'cooldown_seconds', 'last_state', 'last_error')
    return {'id': str(row.pk), **{key: getattr(row, key) for key in names}, **tag_details('lookup', row.pk),
            'next_run_at': _stamp(row.next_run_at), 'last_run_at': _stamp(row.last_run_at)}


def _watch(row):
    names = ('name', 'domains', 'keywords', 'ports', 'actions', 'auto_fetch', 'enabled', 'cooldown_seconds', 'max_daily_fetches')
    return {'id': str(row.pk), **{key: getattr(row, key) for key in names}, **tag_details('watch', row.pk)}


def _tools():
    row = ExecutionLease.objects.filter(pk='capture-tools', expires_at__gt=timezone.now()).first()
    return row.data if row else {'curl': False, 'httpx': False, 'gowitness': False}


@endpoint()
def catalog(request):
    items = protocol_catalog()
    from leakscope.phase3.capture import _HTTP_PORTS, supported_actions
    for item in items:
        item['http_ports'] = sorted(_HTTP_PORTS.get(item['key'], set()))
        item['fetch_actions'] = supported_actions(dict(address='192.0.2.1', port=next(iter(item['ports']), 1), transport=item['transport'], service_type=item.get('base_type',item['key']), scheme=''))
        item['http'] = 'http' in item['fetch_actions']
        item['default_actions'] = ['screenshot'] if item['key']=='vnc' else ['identity'] if item.get('identity') else (['http'] if item['http'] else (['tcp'] if item['transport'] == 'tcp' else []))
    return JsonResponse({'results': items, 'providers': {p['key']: {'configured': p['configured']} for p in available_providers(_cfg())}, 'tools': _tools()})


@endpoint(('POST',))
def provider_account(request):
    provider = _text(_body(request), 'provider', required=True, maximum=20)
    return JsonResponse({'provider': provider, 'account': account_info(provider, _cfg())})


@transaction.atomic
def _save_lookup(data, row=None):
    from leakscope.phase3.collection_policy import validate_collection_mode
    default = lambda key, fallback=None: getattr(row, key, fallback) if row else fallback
    provider = _text(data, 'provider', default('provider', 'netlas'), maximum=20)
    if provider not in {'shodan', 'zoomeye', 'netlas', 'research'}: raise ValueError('Unknown provider')
    query = _text(data, 'query', default('query', ''), maximum=8000, required=True)
    service_type = _text(data, 'service_type', default('service_type', ''), maximum=100)
    entry = catalog_entry(service_type) if service_type and service_type not in {'http','https'} else {}
    service_type = entry.get('base_type',service_type)
    scope = data.get('scope', default('scope', {}))
    from leakscope.intelligence.queries import _scope
    from .discovery_catalog import narrow_scope
    scope = narrow_scope(entry.get('preset_scope',{}),_scope(scope))
    actions = validate_actions(data.get('actions', default('actions', [])))
    collection_mode = validate_collection_mode(data.get('collection_mode', default('collection_mode', '')), allow_custom=True)
    auto_fetch = _flag(data.get('auto_fetch', default('auto_fetch', False)))
    if collection_mode:
        actions = []
        auto_fetch = collection_mode not in {'passive','demo'}
    if 'udp' in actions: raise ValueError('Generic UDP capture requires a manual payload; use native identity for supported automatic UDP probes')
    if provider == 'research' and (len(query) > 300 or actions or auto_fetch):
        raise ValueError('Web research uses a topic of at most 300 characters and produces suggestions, not automatic target fetches')
    interval = data.get('interval_seconds', default('interval_seconds'))
    if interval in ('', None): interval = None
    cron = _text(data, 'cron', default('cron', ''), maximum=100)
    zone = _text(data, 'timezone', default('timezone', 'America/New_York'), maximum=64)
    due = next_occurrence(interval_seconds=interval, cron=cron, timezone_name=zone)
    values = {'name': _text(data, 'name', default('name', ''), maximum=200, required=True),
        'provider': provider, 'query': query, 'service_type': service_type, 'scope': scope, 'actions': actions,
        'auto_fetch': auto_fetch, 'collection_mode': collection_mode,
        'enabled': _flag(data.get('enabled', default('enabled', False))),
        'interval_seconds': int(interval) if interval is not None else None, 'cron': cron, 'timezone': zone,
        'page_count': _integer(data, 'page_count', default('page_count', 1), 1, 20),
        'page_size': _integer(data, 'page_size', default('page_size', 100), 1, 100),
        'max_results': _integer(data, 'max_results', default('max_results', 100), 1, 10000),
        'max_daily_runs': _integer(data, 'max_daily_runs', default('max_daily_runs', 24), 1, 1440),
        'cooldown_seconds': _integer(data, 'cooldown_seconds', default('cooldown_seconds', 3600), 1, 7 * 86400)}
    values['next_run_at'] = due if values['enabled'] else None
    if row is None: row = SavedLookup.objects.create(**values)
    else:
        for key, value in values.items(): setattr(row, key, value)
        row.save()
    if 'tags' in data: set_tags('lookup', row.pk, data['tags'])
    return row


@endpoint(('GET', 'POST'))
def lookups(request, lookup_id=None, action=None):
    if request.method == 'GET':
        if action or lookup_id: raise ValueError('Use POST for lookup actions')
        return JsonResponse({'results': [_lookup(x) for x in SavedLookup.objects.all()[:500]]})
    data = _body(request)
    row = SavedLookup.objects.get(pk=lookup_id) if lookup_id else None
    if action == 'run':
        demo=validate_demo(data.get('demo',False))
        return JsonResponse({'run': serialize_run(queue_lookup(row,**({'demo':True} if demo else {})))})
    if action == 'toggle':
        row.enabled = _flag(data.get('enabled'))
        row.next_run_at = next_occurrence(interval_seconds=row.interval_seconds, cron=row.cron, timezone_name=row.timezone) if row.enabled else None
        row.save(update_fields=['enabled', 'next_run_at', 'updated_at'])
    elif action == 'delete':
        row.delete(); return JsonResponse({'ok': True})
    elif action in {None, 'save'}: row = _save_lookup(data, row)
    else: raise ValueError('Unknown lookup action')
    return JsonResponse({'lookup': _lookup(row)})


@transaction.atomic
def _save_watch(data, row=None):
    default = lambda key, fallback=None: getattr(row, key, fallback) if row else fallback
    domains, keywords = data.get('domains', default('domains', [])), data.get('keywords', default('keywords', []))
    if not isinstance(domains, list) or not isinstance(keywords, list) or len(domains) + len(keywords) > 100:
        raise ValueError('Use at most 100 watch domains/keywords')
    domains = sorted(set(('*.' + normalize_address(x[2:])) if isinstance(x, str) and x.startswith('*.') else normalize_address(x) for x in domains))
    if not domains and not keywords: raise ValueError('Enter at least one domain or keyword')
    if any(not isinstance(k, str) or not 2 <= len(k.strip()) <= 100 for k in keywords): raise ValueError('Watch keywords must contain 2–100 characters')
    auto_fetch = _flag(data.get('auto_fetch', default('auto_fetch', False)))
    ports = data.get('ports', default('ports', [443])) or [443] if auto_fetch else []
    if not isinstance(ports, list) or len(ports) > 10: raise ValueError('Use at most ten ports')
    ports = [_integer({'port': p}, 'port', 443, 1, 65535) for p in ports]
    values = {'name': _text(data, 'name', default('name', ''), maximum=200, required=True), 'domains': domains,
        'keywords': sorted(set(k.strip().casefold() for k in keywords)), 'ports': sorted(set(ports)),
        'actions': validate_actions(data.get('actions', default('actions', ['http']))) if auto_fetch else [],
        'enabled': _flag(data.get('enabled', default('enabled', True))),
        'auto_fetch': auto_fetch,
        'cooldown_seconds': _integer(data, 'cooldown_seconds', default('cooldown_seconds', 3600), 1, 7 * 86400),
        'max_daily_fetches': _integer(data, 'max_daily_fetches', default('max_daily_fetches', 100), 1, 10000)}
    if 'udp' in values['actions']: raise ValueError('Generic UDP capture requires a manual payload')
    if row is None: row = WatchRule.objects.create(**values)
    else:
        for key, value in values.items(): setattr(row, key, value)
        row.save()
    if 'tags' in data: set_tags('watch', row.pk, data['tags'])
    return row


@endpoint(('GET', 'POST'))
def watches(request, watch_id=None, action=None):
    if request.method == 'GET':
        if action or watch_id: raise ValueError('Use POST for watch actions')
        return JsonResponse({'results': [_watch(row) for row in WatchRule.objects.all()[:500]]})
    data = _body(request)
    row = WatchRule.objects.get(pk=watch_id) if watch_id else None
    if action == 'toggle':
        row.enabled = _flag(data.get('enabled')); row.save(update_fields=['enabled'])
    elif action == 'delete': row.delete(); return JsonResponse({'ok': True})
    elif action in {None, 'save'}: row = _save_watch(data, row)
    else: raise ValueError('Unknown watch action')
    return JsonResponse({'watch': _watch(row)})


def _stream_data():
    config = StreamSettings.objects.filter(pk=1).first()
    health = StreamHealth.objects.filter(pk=1).first()
    external = config.external_url if config else ''
    try:
        parts = urlsplit(external)
        netloc = parts.hostname or ''
        if ':' in netloc: netloc = '[' + netloc + ']'
        if parts.port: netloc += ':' + str(parts.port)
        public_url = urlunsplit((parts.scheme, netloc, parts.path, '', ''))
    except ValueError: public_url = ''
    heartbeat = health.heartbeat_at if health else None
    stale = not heartbeat or (timezone.now() - heartbeat).total_seconds() > 90
    matches = CertificateWatchMatch.objects.select_related('certificate').order_by('-last_seen', '-pk')[:20]
    recent = [{'id': str(row.pk), 'certificate_id': str(row.certificate_id),
               'names': row.matched_names, 'watch_id': row.watch_key, 'watch_name': row.watch_name,
               'fingerprint': row.certificate.fingerprint, 'collected_at': _stamp(row.last_seen)} for row in matches]
    switching = config and health and heartbeat and (config.mode != health.mode or config.updated_at > heartbeat)
    return {'mode': config.mode if config else 'builtin', 'external_url': public_url,
        'external_url_has_credentials': public_url != external, 'enabled': config.enabled if config else True,
        'state': 'disabled' if config and not config.enabled else ('switching' if switching else ('unavailable' if stale else health.state)), 'last_event_at': _stamp(health.last_event_at) if health else None,
        'heartbeat_at': _stamp(heartbeat), 'last_error': health.last_error if health else '',
        'received': health.received if health else 0, 'matched': health.matched if health else 0, 'dropped': health.dropped if health else 0,
        'lag_seconds': round((timezone.now() - health.last_event_at).total_seconds()) if health and health.last_event_at else None,
        'builtin_url': 'ws://certstream:8080/', 'recent_matches': recent}


@endpoint(('POST',))
def enrich(request, endpoint_id):
    data = _body(request)
    demo=validate_demo(data.get('demo',False))
    collections = data.get('collections', ['host', 'dns', 'certificates'])
    if not isinstance(collections, list) or not 1 <= len(collections) <= 3 or any(x not in {'host', 'dns', 'certificates'} for x in collections) or len(set(collections)) != len(collections):
        raise ValueError('Select distinct host, dns or certificates collections')
    row = visible_endpoints().get(pk=endpoint_id)
    from leakscope.phase3.jobs import enrich_endpoint
    from leakscope.services.runs import enqueue
    run = enqueue(enrich_endpoint, kind='enrich', type=row.service_type, provider='netlas',
        parameters={'endpoint_id': str(row.pk), 'collections': collections,**({'demo':True} if demo else {})}, kwargs={'endpoint_id': str(row.pk), 'collections': collections,**({'demo':True} if demo else {})})
    return JsonResponse({'run': serialize_run(run)}, status=202)


@endpoint(('GET', 'POST'))
def stream(request):
    if request.method == 'POST':
        data = _body(request)
        row, _ = StreamSettings.objects.get_or_create(pk=1)
        mode = data.get('mode', row.mode)
        if mode not in {'builtin', 'external'}: raise ValueError('Choose Built-in or External mode')
        url = data.get('external_url', row.external_url)
        if not isinstance(url, str) or len(url) > 2000: raise ValueError('Invalid external WebSocket URL')
        if mode == 'external':
            try:
                parts = urlsplit(url)
                if parts.scheme not in {'ws', 'wss'} or not parts.hostname or parts.fragment or any(ord(c) < 33 for c in url): raise ValueError
                parts.port
            except ValueError: raise ValueError('External Certstream requires a ws:// or wss:// URL') from None
        row.mode, row.external_url = mode, url
        row.enabled = _flag(data.get('enabled', row.enabled))
        row.save()
    return JsonResponse(_stream_data())


def _mask(data, reveal=False):
    if reveal: return data
    if isinstance(data, dict):
        def sensitive(key):
            key = re.sub('[^a-z0-9]', '', str(key).casefold())
            return (any(part in key for part in ('password', 'passwd', 'secret', 'apikey', 'authorization', 'cookie', 'privatekey'))
                    or key in {'token', 'tokens', 'tokenlist', 'refreshtoken', 'accesstoken', 'authtoken', 'sessiontoken', 'accesskey', 'credential', 'credentials', 'jwt', 'dsn', 'databaseurl', 'connectionstring', 'context', 'indicator'})
        return {key: ('[masked — reveal to inspect]' if sensitive(key) and value else _mask(value)) for key, value in data.items()}
    if isinstance(data, list): return [_mask(value) for value in data]
    return data


def _lookup_run_results(run_id):
    from .models import Run
    run = Run.objects.get(pk=UUID(run_id), kind__in=['lookup', 'workflow'])
    result = serialize_run(run)['result']
    result = result if isinstance(result, dict) else {}
    references = result.get('endpoint_ids', [])
    if not isinstance(references, list) or len(references) > 10000:
        raise ValueError('Invalid saved run result references')
    try:
        references = [UUID(value) for value in references]
    except (ValueError, TypeError, AttributeError):
        raise ValueError('Invalid saved run result references') from None
    return references, {'id': str(run.pk), 'provider': run.provider, 'state': run.state,
                        'query': result.get('query', ''), 'stored': result.get('stored'),
                        'total': result.get('total'), 'partial': bool(result.get('partial')),
                        'error': run.error or result.get('error', '')}


@endpoint()
def endpoints(request, endpoint_id=None):
    if endpoint_id:
        row = visible_endpoints().get(pk=endpoint_id)
        reveal = _flag(request.GET.get('reveal'))
        from .investigation import observation_summary
        from .evidence import endpoint_attribution, artifact_labels
        from leakscope.phase3.capture_status import endpoint_status
        from leakscope.phase3.jobs import endpoint_snapshot
        saved = list(row.observations.filter(hidden=False).order_by('-last_collected_at', '-pk')[:100])
        observations = [dict(observation_summary(x), data=_mask(x.data, reveal)) for x in saved]
        artifacts = [{'id': str(x.pk), 'kind': x.kind, 'mime': x.mime, 'size': x.size, 'tool': x.tool,
            'created_at': _stamp(x.created_at), 'sha256': x.sha256, 'metadata': _mask(x.metadata, reveal),
            'run_id': str(x.run_id) if x.run_id else None, **artifact_labels(x.kind, x.tool, x.metadata),
            'url': '/api/operations/artifacts/' + str(x.pk)} for x in row.artifacts.all()[:100]]
        snapshot = endpoint_snapshot(row)
        result = {'endpoint': _endpoint(row, snapshot=snapshot), 'observations': observations, 'artifacts': artifacts,
            'attribution': endpoint_attribution(row, saved), 'capture_status': endpoint_status(row, snapshot, saved),
            'investigation': {'basis': 'Saved records only; identical payloads are deduplicated, not a continuous observation interval. Collection does not revalidate reachability.',
                              'observation_limit': 100, 'observations_truncated': row.observations.filter(hidden=False).count() > 100}}
        if not reveal:
            from leakscope.services.run_events import sanitize
            result = sanitize(result, preserve_metrics=True)
        return JsonResponse(result)
    rows = visible_endpoints()
    scope = None
    context_keys = ('technology', 'category', 'attribution', 'organization_id', 'country', 'continent', 'since', 'until')
    if any(request.GET.get(key) for key in context_keys):
        from .atlas_shared import filtered_endpoints
        scoped, coverage = filtered_endpoints(request.GET)
        rows = rows.filter(pk__in=scoped.values('pk'))
        scope = dict(coverage, filters={key: request.GET[key] for key in context_keys if request.GET.get(key)})
    run_context = None
    if request.GET.get('run'):
        references, run_context = _lookup_run_results(request.GET['run'])
        # A lookup's results are that provider's observations; a workflow's are whatever it ended with.
        source = {'observations__provider': run_context['provider']} if run_context['provider'] else {}
        rows = rows.filter(pk__in=references, observations__hidden=False, **source).distinct()
    q, kind, tag, provider = [request.GET.get(key, '')[:300] for key in ('q', 'type', 'tag', 'provider')]
    if q and scope is None: rows = rows.filter(Q(address__icontains=q) | Q(title__icontains=q) | Q(service_type__icontains=q))
    if kind:
        # JSON array membership uses PostgreSQL containment; SQLite's JSON
        # extension lacks that lookup, so use bounded profile object keys.
        rows = rows.filter(Q(service_type=kind) | Q(observations__data__metadata__matched_profile=kind, observations__hidden=False)
                           | Q(observations__data__metadata__profile_matches__has_key=kind, observations__hidden=False)).distinct()
    if provider and scope is None:
        source = (Q(observations__provider__startswith='fetch:') | Q(observations__provider__startswith='native:')) if provider == 'direct' else Q(observations__provider=provider)
        rows = rows.filter(source, observations__hidden=False).distinct()
    if tag:
        # Materialize validated IDs so the ORM applies UUID conversion on both
        # SQLite and PostgreSQL instead of comparing UUID to a varchar column.
        keys = list(TagAssignment.objects.filter(target_kind='endpoint', tag__name=tag.casefold()).values_list('target_key', flat=True))
        rows = rows.filter(pk__in=keys)
    count = rows.count()
    page = _number(request.GET.get('page'), 1, 1, 100000)
    size = _number(request.GET.get('page_size'), 30, 1, 100)
    from .services.table_sorting import parameters, ordered, text_value
    from django.db import connection
    from django.db.models import F, OuterRef, Subquery, CharField
    from django.db.models.functions import Cast, Concat, Substr
    sort, direction = parameters(request, {'endpoint', 'technology', 'tags', 'observations', 'last_seen'}, 'last_seen')
    rows = rows.annotate(observation_count=Count('observations', filter=Q(observations__hidden=False), distinct=True))
    if sort == 'tags':
        endpoint_key = Cast('pk', CharField())
        if connection.vendor == 'sqlite':
            endpoint_key = Concat(Substr(endpoint_key,1,8),Value('-'),Substr(endpoint_key,9,4),Value('-'),
                Substr(endpoint_key,13,4),Value('-'),Substr(endpoint_key,17,4),Value('-'),Substr(endpoint_key,21,12))
        rows = rows.annotate(_sort_id=endpoint_key)
        tags = TagAssignment.objects.filter(target_kind='endpoint', target_key=OuterRef('_sort_id')).annotate(
            _sort_tag=text_value(F('tag__name'))).order_by('_sort_tag','tag__name')
        expression = Subquery(tags.values('_sort_tag')[:1])
    else:
        expression = F({'endpoint':'address', 'technology':'service_type', 'observations':'observation_count', 'last_seen':'last_seen'}[sort])
    rows = ordered(rows, expression, direction, textual=sort in {'endpoint','technology','tags'},
                   ties=('port','transport','pk') if sort == 'endpoint' else ('pk',))[(page - 1) * size:page * size]
    rows = list(rows)
    captures = _capture_counts([row.pk for row in rows])
    return JsonResponse({'results': [_endpoint(row, row.observation_count, captures=captures.get(row.pk, 0)) for row in rows], 'count': count, 'page': page, 'pages': max(1, math.ceil(count / size)),
                         **({'run': run_context} if run_context is not None else {}),
                         **({'scope': scope} if scope is not None else {})})


@endpoint(('POST',))
def fetch(request, endpoint_id=None):
    data = _body(request)
    ids = [str(endpoint_id)] if endpoint_id else data.get('ids')
    if not isinstance(ids, list) or not 1 <= len(ids) <= 10: raise ValueError('Select one to ten endpoints')
    actions = validate_actions(data.get('actions', []))
    if not actions: raise ValueError('Select at least one fetch action')
    options = validate_capture_options(actions, data.get('options'))
    rows = list(visible_endpoints().filter(pk__in=ids))
    if len(rows) != len(set(ids)): raise ValueError('An endpoint was not found')
    if any(set(actions) - set(_fetch_actions(row)) for row in rows):
        raise ValueError('A selected fetch action is unavailable for an endpoint transport or service')
    runs = [serialize_run(queue_capture(row.pk, actions, cooldown_seconds=0, options=options)) for row in rows]
    return JsonResponse({'runs': runs, 'run': runs[0] if len(runs) == 1 else None})


def _tag_target(data):
    kind, key = data.get('target_kind'), str(data.get('target_key', ''))
    if kind in {'endpoint', 'lookup', 'watch', 'certificate', 'asset'}:
        try: UUID(key)
        except ValueError: raise ValueError('Invalid target ID') from None
        row = {'endpoint': ServiceEndpoint, 'lookup': SavedLookup, 'watch': WatchRule,
               'certificate': CertificateRecord, 'asset': AssetNode}[kind].objects.get(pk=key)
        key = str(row.pk)
    elif kind == 'query': key = str(CustomQuery.objects.get(pk=int(key)).pk)
    elif kind == 'finding':
        from leakscope.services.registry import canonical_type
        name, pk = key.rsplit(':', 1)
        name = canonical_type(name)
        row = _model(name).objects.get(pk=int(pk))
        key = name + ':' + str(row.pk)
    else: raise ValueError('Unknown tag target')
    return kind, key


@endpoint(('GET', 'POST'))
def tags(request):
    data = _body(request) if request.method == 'POST' else request.GET
    kind, key = _tag_target(data)
    if request.method == 'POST': set_tags(kind, key, data.get('tags', []))
    return JsonResponse(tag_details(kind, key))


@endpoint()
def artifact(request, artifact_id):
    from leakscope.phase3.local_correlations import visible_artifacts
    row = visible_artifacts().get(pk=artifact_id)
    path = (capture_root() / row.relative_path).resolve()
    if not path.is_relative_to(capture_root()) or not path.is_file():
        from django.core.exceptions import ObjectDoesNotExist
        raise ObjectDoesNotExist('Artifact not available')
    # Captured HTML/JS is an attachment, never same-origin executable content.
    return FileResponse(path.open('rb'), as_attachment=row.mime not in {'image/png', 'image/jpeg'}, filename=path.name,
                        content_type=row.mime if row.mime in {'image/png', 'image/jpeg', 'application/json', 'text/plain'} else 'application/octet-stream')


@endpoint()
def analysis(request):
    from leakscope.phase3.analysis import build_analysis
    return JsonResponse(dict(build_analysis(), tool_health=_tools()))
