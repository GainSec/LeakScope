"""Login-free private workbench: validated HTTP boundaries over existing handlers."""

import csv
from datetime import datetime, timezone as datetime_timezone
from functools import wraps
import io
import json
import logging
import math
import platform
import re
from urllib.parse import urlsplit

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import connection, InterfaceError, OperationalError
from django.http import JsonResponse, HttpResponse, QueryDict
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie

from leakscope import tasks
from leakscope.phase3.state import tag_names
from leakscope.phase3.tags import tag_details
from leakscope_app.models import Search, CustomQuery, BlacklistEntry

log = logging.getLogger(__name__)
PROVIDERS = {"shodan", "zoomeye", "netlas"}
ALIASES = {"consulkv": "consul", "harbor": "registry", "dockerapi": "registry"}
DISCOVERY_ALIASES = {"keys": "api_key", "amazonbe": "asia"}
LABELS = {
    "gitlab": "GitLab",
    "elastic": "Elasticsearch",
    "opensearch": "OpenSearch",
    "dirs": "Directory listings",
    "amazonbe": "S3 references",
    "amazons3be": "S3 listings",
    "amazonbuckets": "S3 buckets",
    "mongo": "MongoDB",
    "mongoexpress": "Mongo Express",
    "couchdb": "CouchDB",
    "consul": "Consul KV",
    "gcsbucket": "Google Cloud Storage",
    "azureblob": "Azure Blob",
    "keys": "Key indicators",
    "javascript": "JavaScript",
    "github": "Git repositories",
    "nexus": "Nexus",
    "registry": "Container registry",
    "rsync": "Rsync",
    "ftp": "FTP",
    "gitea": "Gitea",
    "gogs": "Gogs",
    "swagger": "OpenAPI / Swagger",
}
SENSITIVE = {"secret", "secrets", "context", "indicator"}
BALANCE_CACHE = {}


def _config():
    return tasks.get_config().get("config", {})


def _redact(message):
    text = str(message)
    try:
        for key, value in _config().items():
            if "KEY" in key and isinstance(value, str) and value:
                text = text.replace(value, "[redacted]")
    except Exception:
        pass
    return text[:1500]


def endpoint(methods=("GET",)):
    def decorate(fn):
        @wraps(fn)
        def wrapped(request, *args, **kwargs):
            if request.method not in methods:
                response = JsonResponse({"error": "Method not allowed"}, status=405)
                response["Allow"] = ", ".join(methods)
                return response
            try:
                return fn(request, *args, **kwargs)
            except ObjectDoesNotExist:
                return JsonResponse({"error": "Record not found"}, status=404)
            except (OperationalError, InterfaceError) as exc:
                from leakscope.runtime_diagnostics import database_unavailable
                return database_unavailable(request, exc)
            except (
                ValueError,
                TypeError,
                KeyError,
                ValidationError,
                json.JSONDecodeError,
            ) as exc:
                return JsonResponse({"error": _redact(exc)}, status=400)
            except Exception as exc:
                log.error("Workbench %s failed: %s", fn.__name__, _redact(exc))
                return JsonResponse({"error": _redact(exc)}, status=500)

        return wrapped

    return decorate


def _body(request):
    if len(request.body) > 1024 * 1024:
        raise ValueError("Request exceeds 1 MiB")
    if request.content_type == "application/json":
        data = json.loads(request.body or "{}")
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object")
        return data
    return request.POST.dict()


def _flag(value):
    return value is True or str(value).lower() in {"1", "true", "on", "yes"}


def _number(value, default, low, high):
    result = int(value if value not in (None, "") else default)
    if not low <= result <= high:
        raise ValueError("Value must be between %s and %s" % (low, high))
    return result


def _type(name):
    from leakscope.services.registry import canonical_type

    return canonical_type(name)


def _models():
    from leakscope_app.services.findings import selected_models

    return selected_models()


def _model(name):
    model = _models().get(_type(name))
    if model is None:
        raise ValueError("Unsupported service type")
    return model


def _types(include_saved=False):
    from leakscope.services.registry import service_types
    from .discovery_catalog import catalog
    from leakscope.phase3.capture import supported_actions
    entries = {item['key']: item for item in catalog(include_saved=include_saved)}
    for entry in entries.values():
        entry['fetch_actions'] = supported_actions(dict(address='192.0.2.1', port=next(iter(entry['ports']), 1),
            transport=entry['transport'], service_type=entry.get('base_type',entry['key']), scheme=''))
    values = []
    for service in sorted(service_types(), key=lambda s: s["key"]):
        key = service["key"]
        query_type = service["query_type"]
        entry = entries.pop(key, {})
        modes = {mode: [provider for provider, query in variants.items() if query['supported']]
                 for mode, variants in entry.get('query_variants', {}).items()}
        modes['legacy'] = ['shodan', 'zoomeye'] + (['netlas'] if entry else [])
        values.append(
            {
                "key": key,
                "value": key,
                "label": LABELS.get(key, service["label"]),
                "preview": service["preview"],
                "export": service["export"],
                "explore": service["explore"],
                "capabilities": [
                    op for op in ["preview", "export", "explore"] if service[op]
                ],
                "discovery": service["discovery"],
                "legacy": True,
                "category": entry.get('category', 'other'),
                "category_label": entry.get('category_label', 'Other services'),
                "categories": entry.get('categories', ['other']),
                "discovery_modes": modes,
                "query_variants": entry.get('query_variants', {}),
                "discovery_providers": sorted({p for providers in modes.values() for p in providers}),
                "discovery_type": query_type,
                "fingerprint_summary": entry.get('fingerprint_summary', ''),
                "fetch_actions": entry.get('fetch_actions', []),
                "transport": entry.get('transport', 'tcp'),
            }
        )
    for entry in entries.values():
        modes = {mode: [provider for provider, query in variants.items() if query['supported']]
                 for mode, variants in entry.get('query_variants', {}).items()}
        values.append(dict(entry, value=entry['key'], legacy=False, preview=False, export=False,
                           explore=False, capabilities=[], discovery=True, discovery_type=entry['key'],
                           discovery_modes=modes,
                           discovery_providers=sorted({p for providers in modes.values() for p in providers})))
    return values


def _review_model():
    try:
        return apps.get_model("leakscope_app", "FindingReview")
    except LookupError:
        return None


def _hidden_ids(type_name):
    model = _review_model()
    if model is None:
        return set()
    return set(
        model.objects.filter(type=_type(type_name), hidden=True).values_list(
            "object_id", flat=True
        )
    )


def _hidden(obj, type_name):
    from leakscope.services.identity import is_hidden

    return is_hidden(type_name, obj)


def _status(obj, type_name, hidden=None):
    if hidden if hidden is not None else _hidden(obj, type_name):
        return "hidden"
    if obj.confirmed:
        return "confirmed"
    if obj.for_later:
        return "for_later"
    return "new"


def _safe_url(value):
    value = str(value or "")
    parsed = urlsplit(value)
    return value if parsed.scheme in {"http", "https"} and parsed.netloc else ""


def _finding(obj, type_name, hidden=None):
    search = getattr(obj, "search", None)
    ip = str(getattr(obj, "ip", "") or "")
    port = str(getattr(obj, "port", "") or "")
    label = ip + ((":" + port) if port else "")
    if not label:
        label = str(
            getattr(obj, "bucket", "")
            or getattr(obj, "path", "")
            or getattr(obj, "keyword", "")
            or ("Finding " + str(obj.pk))
        )[:300]
    stamp = getattr(search, "created_on", None)
    summary = str(
        getattr(obj, "title", "")
        or getattr(obj, "name", "")
        or getattr(obj, "version", "")
        or ""
    )[:300]
    return {
        "id": obj.pk,
        "type": type_name,
        "label": label,
        "ip": ip,
        "port": port,
        "url": _safe_url(getattr(obj, "url", "")),
        "provider": getattr(obj, "provider", "unknown") or "unknown",
        "status": _status(obj, type_name, hidden),
        "search_id": getattr(search, "pk", None),
        "summary": summary,
        **tag_details('finding', f'{type_name}:{obj.pk}'),
        "created_at": stamp.isoformat() if stamp else None,
        "created_on": stamp.isoformat() if stamp else None,
    }


def _run_data(run):
    from leakscope.services.runs import serialize_run

    data = serialize_run(run)
    data.setdefault(
        "progress",
        {
            "current": data.get("progress_current", 0),
            "total": data.get("progress_total", 0),
        },
    )
    return data


def _enqueue(task, kind, type_name, provider="shodan", kwargs=None):
    from leakscope.services.runs import enqueue

    return _run_data(
        enqueue(
            task,
            kind=kind,
            type=type_name,
            provider=provider,
            parameters=kwargs or {},
            kwargs=kwargs or {},
        )
    )


@ensure_csrf_cookie
def shell(request, page="overview", type_name=None, object_id=None):
    return render(
        request,
        "workbench/app.html",
        {
            "page": page,
            "type": type_name or request.GET.get("type", ""),
            "target_id": object_id or request.GET.get("target_id", ""),
        },
    )


@endpoint()
def health(request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()
    return JsonResponse({"status": "ok", "service": "leakscope"})


@endpoint()
def types_api(request):
    return JsonResponse({"results": _types(include_saved=True)})


@endpoint()
def summary(request):
    from leakscope_app.models import Run

    from django.db.models import Count
    from leakscope_app.services.findings import summary_counts

    models = _models()
    counts, type_counts, by_provider = summary_counts(models)
    counts.update(findings=counts['total'], runs=Run.objects.count())
    by_type = [
        {'type': key, 'key': key, 'value': key,
         'label': LABELS.get(key, model.__name__), 'count': type_counts[key]}
        for key, model in models.items() if type_counts.get(key)
    ]
    cfg = _config()
    providers = {
        p: dict(BALANCE_CACHE.get(p, {}), configured=bool(cfg.get(p.upper() + "_API_KEY")))
        for p in PROVIDERS
    }
    from .models import ServiceEndpoint, ServiceObservation, EvidenceArtifact, SavedLookup, WatchRule
    from leakscope.phase3.state import visible_endpoints
    visible = visible_endpoints()
    observations = {'endpoints': visible.count(), 'observations': ServiceObservation.objects.filter(hidden=False).count(),
        'artifacts': EvidenceArtifact.objects.filter(endpoint__in=visible).count(), 'lookups': SavedLookup.objects.count(), 'watches': WatchRule.objects.count(),
        'by_provider': list(ServiceObservation.objects.filter(hidden=False).values('provider').annotate(count=Count('endpoint', distinct=True)).order_by('provider')),
        'by_type': list(visible.values('service_type').annotate(count=Count('pk')).order_by('-count', 'service_type'))}
    return JsonResponse(
        {
            "counts": counts,
            "by_type": by_type,
            "types": _types(),
            "by_provider": [
                {"provider": p, "count": v} for p, v in sorted(by_provider.items())
            ],
            "providers": providers,
            "observations": observations,
            "recent_runs": [
                _run_data(r) for r in Run.objects.order_by("-created_at")[:8]
            ],
        }
    )


@endpoint()
def findings(request):
    page = _number(request.GET.get("page"), 1, 1, 10000)
    size = _number(request.GET.get("page_size"), 30, 1, 200)
    name = _type(request.GET.get("type"))
    provider = request.GET.get("provider", "")
    status = request.GET.get("status", "")
    q = request.GET.get("q", "").strip()[:300]
    from leakscope_app.services.findings import page as read_page

    from .services.table_sorting import parameters
    sort, direction = parameters(request, {'target','type','provider','status','created_at'}, 'created_at')
    rows, count = read_page(name or None, provider, status, q, page, size, _finding, sort, direction)
    return JsonResponse(
        {
            "results": rows,
            "count": count,
            "total": count,
            "page": page,
            "pages": max(1, math.ceil(count / size)),
            "types": _types(),
        }
    )


@endpoint()
def detail(request, type_name, object_id):
    name = _type(type_name)
    obj = _model(name).objects.get(pk=object_id)
    row = _finding(obj, name)
    reveal = _flag(request.GET.get("reveal"))
    fields = {}
    for field in obj._meta.fields:
        if field.name in {"search", "id"}:
            continue
        value = getattr(obj, field.name)
        fields[field.name] = (
            "[masked — reveal to inspect]"
            if field.name in SENSITIVE and value and not reveal
            else value
        )
    search = getattr(obj, "search", None)
    source = (
        None
        if not search
        else {
            "id": search.pk,
            "type": search.type,
            "provider": search.provider,
            "keyword": search.keyword,
            "country": search.country,
            "network": search.network,
            "created_at": search.created_on.isoformat(),
        }
    )
    result = dict(
        row,
        finding=row,
        fields=fields,
        search=source,
        capabilities={"preview": True, "export": True},
        preview_supported=True,
        export_supported=True,
    )
    if not reveal:
        from leakscope.services.run_events import sanitize
        result = sanitize(result, preserve_metrics=True)
    return JsonResponse(result)


@endpoint(("POST",))
def review(request, type_name, object_id):
    name = _type(type_name)
    obj = _model(name).objects.get(pk=object_id)
    state = _body(request).get("status")
    if state not in {"new", "confirmed", "for_later", "hidden"}:
        raise ValueError("Invalid review status")
    model = _review_model()
    if model is not None:
        model.objects.update_or_create(
            type=name, object_id=obj.pk, defaults={"hidden": state == "hidden"}
        )
    obj.confirmed = state == "confirmed"
    obj.for_later = state == "for_later"
    obj.save(update_fields=["confirmed", "for_later"])
    return JsonResponse({"finding": _finding(obj, name), "ok": True})


def _discovery(data):
    from leakscope.phase3.usage_policy import validate_demo
    demo=validate_demo(data.get('demo',False)) or data.get('collection_mode')=='demo'
    from leakscope.phase3.collection_policy import validate_collection_mode
    collection_mode = validate_collection_mode('demo' if demo else data.get('collection_mode', 'passive'))
    provider = str(data.get("provider", "shodan")).lower()
    if provider not in PROVIDERS:
        raise ValueError("Select Shodan, ZoomEye or Netlas")
    selected = data.get("types") or ([data["type"]] if data.get("type") else [])
    if isinstance(selected, str):
        selected = [selected]
    if not isinstance(selected, list) or len(selected) > 36:
        raise ValueError("Select up to 36 service types")
    allowed = {t["key"]: t for t in _types(include_saved=True) if t["discovery"]}
    if any(_type(t) not in allowed for t in selected):
        raise ValueError("Unsupported discovery service")
    mode = data.get('query_mode', 'legacy')
    if mode not in {'legacy', 'fingerprint', 'nonstandard', 'ports'}:
        raise ValueError('Unknown discovery query mode')
    page = _number(data.get("page"), 1, 1, 10000)
    pages = _number(data.get("page_count"), 1, 1, 20)
    if page != 1:
        raise ValueError('Discovery searches start at page one')
    if _flag(data.get('active_probe')):
        raise ValueError('Select a collection mode instead of the retired active inspection option')
    start = data.get('start') or 'beginning'
    if start not in {'beginning', 'continue', 'page', 'after'}:
        raise ValueError('Unknown start position')
    start_value = None
    if start in {'page', 'after'}:
        raw = data.get('start_value')
        if isinstance(raw, bool) or raw in (None, ''):
            raise ValueError('Enter a page number' if start == 'page' else 'Enter how many results to skip')
        start_value = _number(raw, 0, 1 if start == 'page' else 0, 10000 if start == 'page' else 1000000)
    if demo:
        start, start_value = 'beginning', None
    if mode == 'legacy' and any(not allowed[_type(t)]['legacy'] for t in selected):
        raise ValueError('Select a response fingerprint or port candidate strategy for this service')
    parameters = {
        k: str(data.get(k, "") or "").strip()
        for k in ["keyword", "country", "network", "exclude"]
    }
    if any(len(v) > 1000 for v in parameters.values()):
        raise ValueError("Query field exceeds 1000 characters")
    if not selected and not parameters['keyword']:
        raise ValueError("Enter a keyword or select at least one service type")
    return (
        provider,
        list(dict.fromkeys(_type(t) for t in selected)) if selected else ['generic'],
        dict(
            parameters,
            query_mode=mode,
            collection_mode=collection_mode,
            page=page,
            start=start,
            start_value=start_value,
            page_count=1 if demo else pages,
            zoomeye_pagesize=_number(data.get("zoomeye_pagesize"), 20, 1, 100),
        ),
    )


def _discovery_query(name, provider, params):
    if name == 'generic':
        from leakscope.intelligence.queries import _scope, _quote
        from leakscope.phase3.jobs import scoped_query
        scope = _scope({key: params.get(key, '') for key in ('network', 'country', 'exclude')})
        base = _quote(params['keyword'], provider)
        return {'type': 'generic', 'base_type': '', 'label': 'Keyword search',
                'base_query': base, 'scope': scope, 'query': scoped_query(provider, base, scope),
                'supported': True, 'basis': [],
                'reason': 'Keyword search over provider default indexed fields; no service, product or port filter is added.'}
    if params['query_mode'] != 'legacy':
        return _profile_discovery_query(name, provider, params)
    if provider == 'netlas':
        return _profile_discovery_query(name, provider, dict(params, query_mode='fingerprint'))
    from leakscope.intelligence.queries import _scope
    from leakscope.phase3.jobs import scoped_query
    key = DISCOVERY_ALIASES.get(name, name)
    base = tasks.provider_queries(provider).get(key, '')
    scope = _scope({key: params.get(key, '') for key in ('network', 'country', 'exclude')})
    query = tasks.build_provider_query(provider, base, keyword=params.get('keyword')) if base else ''
    return {'type': name, 'base_query': query, 'scope': scope, 'supported': bool(query),
            'query': scoped_query(provider, query, scope) if query else '',
            'reason': 'Legacy provider query syntax; collection mode independently controls direct target requests.' if query else 'No legacy provider query is defined for this profile.',
            'basis': []}


def _page_size(provider, params):
    from leakscope.phase3.providers import effective_page_size
    return 1 if params['collection_mode'] == 'demo' else effective_page_size(provider, params['zoomeye_pagesize'])


def _discovery_start(name, provider, query, params):
    """Resolve the requested start position to a provider page. Reads saved progress only."""
    size, mode, value = _page_size(provider, params), params['start'], params['start_value']
    page, known, total, exhausted = 1, False, None, False
    if mode == 'page':
        page = value
    elif mode == 'after':
        # Providers page in fixed steps, so a start inside a page repeats that page's earlier results.
        page = value // size + 1
    elif mode == 'continue' and query.get('query'):
        from leakscope.phase3.jobs import search_progress
        progress = search_progress(provider, query['query'], size)
        if progress:
            page, known, total, exhausted = progress.next_page, True, progress.total, progress.exhausted
    if page > 10000:
        raise ValueError('Start position is beyond page 10000')
    first = (page - 1) * size + 1
    result = {'type': name, 'mode': mode, 'page': page, 'page_size': size, 'first_result': first,
              'repeated': value - (first - 1) if mode == 'after' else 0}
    if mode == 'continue':
        result.update(known=known, total=total)
        if exhausted: result['exhausted'] = True
    return result


@endpoint(("POST",))
def query_preview(request):
    provider, types, params = _discovery(_body(request))
    from leakscope.phase3.providers import demo_provider_policy
    policy={'demo':True,'usage_policy':demo_provider_policy(provider)} if params['collection_mode']=='demo' else {}
    return JsonResponse({**policy,
        'queries': (queries := [_discovery_query(name, provider, params) for name in types]),
        'start': [_discovery_start(name, provider, query, params) for name, query in zip(types, queries)],
        'collection': {'mode': params['collection_mode'],
                       'target_requests': params['collection_mode'] not in {'passive','demo'},
                       'note': 'Collection actions are selected from actual returned endpoints; this preview makes no target requests.'},
        'estimate': {'pages': params['page_count'] * len(types),
                     'note': 'Page count estimate; actual provider credit pricing may vary.'}})


@endpoint(("POST",))
def discover(request):
    provider, types, params = _discovery(_body(request))
    from .models import SavedLookup
    from leakscope.phase3.jobs import queue_lookup
    from leakscope.services.runs import serialize_run
    # Legacy changes query construction only. All Discover modes use the same
    # provider-only collector; direct actions are explicitly planned afterwards.
    queries = [(name, _discovery_query(name, provider, params)) for name in types]
    for name, query in queries:
        if not query['supported']:
            raise ValueError(name + ': ' + query['reason'])
    runs = []
    page_size = params['zoomeye_pagesize'] if provider == 'zoomeye' else (20 if provider == 'netlas' else 100)
    starts = [_discovery_start(name, provider, query, params) for name, query in queries]
    for start in starts:
        if start.get('exhausted'):
            raise ValueError((start['type'] + ': ' if len(starts) > 1 else '') + 'Earlier runs already fetched every result for this search'
                + (' (' + str(start['total']) + ')' if start.get('total') is not None else '') + '. Start from the beginning to fetch them again.')
    for (name, query), start in zip(queries, starts):
        lookup = SavedLookup.objects.create(name=provider.title() + ' · ' + query.get('label', name),
            provider=provider, query=query['base_query'], scope=query['scope'], service_type=query.get('base_type',name),
            collection_mode=params['collection_mode'], auto_fetch=params['collection_mode'] not in {'passive','demo'},
            actions=[], page_count=params['page_count'], start_page=start['page'], page_size=1 if params['collection_mode']=='demo' else page_size,
            max_results=1 if params['collection_mode']=='demo' else params['page_count'] * page_size)
        runs.append(serialize_run(queue_lookup(lookup)))
    return JsonResponse({'runs': runs, 'results_url': '/assets/', 'start': starts,
                         'collection_mode': params['collection_mode']}, status=202)


def _profile_discovery_query(name, provider, params):
    from .discovery_catalog import profile_query
    from leakscope.phase3.jobs import scoped_query
    from leakscope.intelligence.queries import _scope, _quote
    profile = profile_query(name, provider, params['query_mode'])
    scope = _scope({key: params.get(key, '') for key in ('network', 'country', 'exclude')})
    from .discovery_catalog import narrow_scope, catalog_entry
    scope = narrow_scope(profile.get('preset_scope',{}),scope)
    base_type = catalog_entry(name).get('base_type',name)
    base = profile['query']
    if profile['supported'] and params.get('keyword'):
        join = {'shodan': ' ', 'zoomeye': ' && ', 'netlas': ' AND '}[provider]
        base = (base if provider == 'shodan' else '(' + base + ')') + join + _quote(params['keyword'], provider)
    query = scoped_query(provider,base,scope) if profile['supported'] else ''
    if len(query)>8000:
        profile=dict(profile,supported=False,reason='Scoped provider query exceeds8000 characters; narrow the selected evidence or exclusions.')
        query=''
    return dict(profile, type=name, base_type=base_type, base_query=base, scope=scope,query=query)


def _netlas_discovery_query(name, params):
    from .discovery_catalog import profile_query
    from leakscope.phase3.jobs import scoped_query
    from leakscope.intelligence.queries import _quote
    try: profile = profile_query(name, 'netlas')
    except ValueError: raise ValueError('Netlas preset unavailable for ' + name + '; use Technologies or a custom query') from None
    if not profile['supported']: raise ValueError(profile['reason'])
    base = profile['query']
    if params.get('keyword'): base = '(' + base + ') AND ' + _quote(params['keyword'], 'netlas')
    return scoped_query('netlas', base, {key: params.get(key, '') for key in ('network', 'country', 'exclude')})


@endpoint()
def runs(request, run_id=None):
    from leakscope_app.models import Run
    from leakscope.services.runs import get_run

    if run_id:
        return JsonResponse(_run_data(get_run(run_id, refresh=True)))
    return JsonResponse(
        {
            "results": [
                _run_data(get_run(r.pk, refresh=r.state in {"queued", "running"}))
                for r in Run.objects.order_by("-created_at")[:100]
            ]
        }
    )


def _query(q):
    data = {
        f.name: getattr(q, f.name).isoformat()
        if isinstance(getattr(q, f.name), datetime)
        else getattr(q, f.name)
        for f in q._meta.fields
    }
    from leakscope.phase3.state import tag_names
    data.update(tag_details('query', q.pk))
    return data


@endpoint(("GET", "POST"))
def queries(request, query_id=None, action=None):
    if request.method == "GET":
        if action:
            return JsonResponse({"error": "Method not allowed"}, status=405)
        return JsonResponse(
            {
                "results": [
                    _query(q) for q in CustomQuery.objects.order_by("-updated_on")
                ]
            }
        )
    data = _body(request)
    if query_id:
        q = CustomQuery.objects.get(pk=query_id)
        if action == "delete":
            q.delete()
            return JsonResponse({"ok": True})
        if action == "run":
            from leakscope.phase3.usage_policy import validate_demo
            demo=validate_demo(data.get('demo',False))
            provider = str(data.get("provider") or q.provider).lower()
            if provider not in PROVIDERS:
                raise ValueError("Invalid provider")
            kwargs = {
                k: str(data.get(k, "") or "")
                for k in ["keyword", "country", "network", "exclude"]
            }
            kwargs.update(
                custom_query_id=q.pk,
                provider=provider,
                page=_number(data.get("page"), 1, 1, 10000),
                page_count=_number(data.get("page_count"), 1, 1, 20),
                active_probe=_flag(data.get("active_probe", q.active_probe_default)),
            )
            if demo: kwargs.update(page=1,page_count=1,active_probe=False,demo=True)
            if provider == 'netlas' or demo:
                from .models import SavedLookup
                from leakscope.phase3.jobs import queue_lookup
                from leakscope.services.runs import serialize_run
                from leakscope.services.query import render_query_template
                from leakscope.phase3.state import set_tags
                query = render_query_template(q.query_template, kwargs)
                lookup = SavedLookup.objects.create(name=q.name[:200], provider=provider, query=query,
                    service_type=q.default_type, custom_query=q, page_count=kwargs['page_count'],
                    max_results=1 if demo else min(10000, kwargs['page_count'] * 20),
                    scope={k: kwargs.get(k, '') for k in ('network', 'country', 'exclude')},**({'collection_mode':'demo','page_size':1} if demo else {}))
                set_tags('lookup', lookup.pk, tag_names('query', q.pk), origin='query:' + str(q.pk))
                q.last_run_at = datetime.now(datetime_timezone.utc); q.last_status = 'queued'
                q.save(update_fields=['last_run_at', 'last_status'])
                return JsonResponse({'run': serialize_run(queue_lookup(lookup)), 'results_url': '/assets/'}, status=202)
            return JsonResponse(
                {
                    "run": _enqueue(
                        tasks.check_custom_query,
                        "custom",
                        q.default_type,
                        provider,
                        kwargs,
                    )
                },
                status=202,
            )
    else:
        q = (
            CustomQuery.objects.filter(pk=data.get("id")).first()
            if data.get("id")
            else CustomQuery()
        )
        if q is None:
            raise ValueError("Saved query not found")
    template = str(data.get("query_template", "")).strip()
    if not template or len(template) > 20000:
        raise ValueError("A query template is required (maximum 20000 characters)")
    provider = data.get("provider", "shodan")
    if provider not in PROVIDERS:
        raise ValueError("Invalid provider")
    q.name = str(data.get("name", "Untitled")).strip()[:255] or "Untitled"
    q.description = str(data.get("description", ""))[:10000]
    q.query_template = template
    q.default_type = _type(data.get("default_type", ""))
    q.provider = provider
    q.active_probe_default = _flag(data.get("active_probe_default"))
    q.save()
    return JsonResponse({"query": _query(q)})


def _target(data):
    name = _type(data.get("type"))
    obj = _model(name).objects.get(pk=int(data.get("target_id")))
    if not _flag(data.get("active_probe")):
        raise ValueError("Enable active inspection to contact this target")
    return name, obj


@endpoint(("POST",))
def preview(request):
    data = _body(request)
    name, obj = _target(data)
    return JsonResponse(
        {
            "run": _enqueue(
                tasks.run_preview,
                "preview",
                name,
                getattr(obj, "provider", "unknown"),
                {"type": name, "target_id": obj.pk, "active_probe": True},
            )
        },
        status=202,
    )


@endpoint(("POST",))
def export(request):
    data = _body(request)
    name, obj = _target(data)
    fmt = data.get("format", "json")
    if fmt not in {"csv", "json"}:
        raise ValueError("Select JSON or CSV")
    selection = data.get("selection") or {}
    if not isinstance(selection, dict):
        raise ValueError("Selection must be an object")
    kwargs = {
        "type": name,
        "target_id": obj.pk,
        "selection": selection,
        "max_items": _number(data.get("max_items"), 1000, 1, 5000),
        "fmt": fmt,
        "active_probe": True,
    }
    return JsonResponse(
        {
            "run": _enqueue(
                tasks.export_preview_data,
                "export",
                name,
                getattr(obj, "provider", "unknown"),
                kwargs,
            )
        },
        status=202,
    )


@endpoint(("POST",))
def explore(request):
    from leakscope_app import views

    data = _body(request)
    name, obj = _target(data)
    selection = data.get("selection") or {}
    if not isinstance(selection, dict):
        raise ValueError("Selection must be an object")
    params = dict(selection, type=name, target_id=str(obj.pk), active_probe="true")
    request.GET = QueryDict("", mutable=True)
    request.GET.update({k: str(v) for k, v in params.items() if v is not None})
    if not selection:
        response = views.explore_start(request)
        payload = json.loads(response.content)
        payload.setdefault("items", [])
        return JsonResponse(payload, status=response.status_code)
    return views.explore_next(request)


@endpoint(("POST",))
def tools(request, tool):
    data = _body(request)
    if not _flag(data.get("active_probe")):
        raise ValueError("Enable active inspection for this tool")
    keyword = str(data.get("keyword", "")).strip()
    if not keyword or len(keyword) > 2000:
        raise ValueError("Provide a target or keyword")
    choices = {
        "javascript": tasks.javascript_search,
        "github": tasks.github_repo_search,
        "buckets": tasks.brute_buckets,
    }
    if tool not in choices:
        raise ValueError("Unsupported tool")
    if tool in {"javascript", "github"} and not _safe_url(keyword):
        raise ValueError("Provide an HTTP or HTTPS URL")
    return JsonResponse(
        {
            "run": _enqueue(
                choices[tool],
                tool,
                tool,
                "direct",
                {"keyword": keyword, "active_probe": True},
            )
        },
        status=202,
    )


def _runtime_health():
    database = "ok"
    try:
        with connection.cursor() as c:
            c.execute("SELECT 1")
    except Exception:
        database = "unavailable"
    broker = "unavailable"
    try:
        import redis

        client = redis.Redis.from_url(
            settings.CELERY_BROKER_URL, socket_connect_timeout=0.4, socket_timeout=0.4
        )
        if client.ping():
            broker = "ok"
    except Exception:
        pass
    worker = "unavailable"
    if broker == "ok":
        try:
            from leakscope.celery import app

            worker = (
                "responding"
                if app.control.inspect(timeout=0.5).ping()
                else "busy or unavailable"
            )
        except Exception:
            worker = "busy or unavailable"
    return {"database": database, "redis": broker, "worker": worker}


@endpoint()
def settings_api(request):
    import django, celery

    cfg = _config()
    version = {
        "python": platform.python_version(),
        "django": django.get_version(),
        "celery": celery.__version__,
    }
    providers = {
        p: dict(BALANCE_CACHE.get(p, {}), configured=bool(cfg.get(p.upper() + "_API_KEY")))
        for p in PROVIDERS
    }
    return JsonResponse(
        {
            "providers": providers,
            "blacklist": sorted(
                set(cfg.get("blacklist", []))
                | set(BlacklistEntry.objects.values_list("value", flat=True))
            ),
            "health": _runtime_health(),
            "version": version,
            "runtime": version,
            "storage": {"database": "Persistent PostgreSQL" if settings.DATABASES['default']['ENGINE'].endswith('postgresql') else "Persistent SQLite", "exports_ttl_hours": 24},
        }
    )


@endpoint(("POST",))
def provider_check(request):
    from concurrent.futures import ThreadPoolExecutor

    cfg = _config()

    def check(provider):
        if not cfg.get(provider.upper() + "_API_KEY"):
            return provider, {
                "configured": False,
                "ok": False,
                "error": "Key not configured",
            }
        try:
            if provider == 'netlas':
                from leakscope.phase3.providers import account_info
                data = account_info('netlas', cfg)
                return provider, {'configured': True, 'ok': True, 'account': data,
                    'credits': data.get('requests_left', data.get('coins')),
                    'last_checked': datetime.now(datetime_timezone.utc).isoformat()}
            client = (
                tasks.get_shodan_client()
                if provider == "shodan"
                else tasks.get_zoomeye_client()
            )
            client.timeout = 10
            data = client.info() if provider == "shodan" else client.userinfo()
            if provider == "zoomeye" and data.get("code") not in (None, 60000, 200, 0):
                raise ValueError("Provider did not accept the account check")
            body = data.get("data") or {}
            subscription = body.get("subscription") or data.get("subscription") or {}
            credits = (
                data.get("query_credits")
                if provider == "shodan"
                else subscription.get(
                    "zoomeye_points", subscription.get("points", body.get("points", 0))
                )
            )
            state = {
                "configured": True,
                "ok": True,
                "credits": credits,
                "last_checked": datetime.now(datetime_timezone.utc).isoformat(),
            }
        except Exception as exc:
            state = {
                "configured": True,
                "ok": False,
                "error": _redact(exc),
                "last_checked": datetime.now(datetime_timezone.utc).isoformat(),
            }
        return provider, state

    with ThreadPoolExecutor(max_workers=3) as pool:
        out = dict(pool.map(check, sorted(PROVIDERS)))
    BALANCE_CACHE.update(out)
    return JsonResponse({"providers": out})


@endpoint(("POST",))
def blacklist(request):
    data = _body(request)
    entries = data.get("entries", [])
    if isinstance(entries, str):
        entries = entries.splitlines()
    if not isinstance(entries, list) or len(entries) > 10000:
        raise ValueError("Provide up to 10000 blacklist entries")
    created = 0
    for value in entries:
        value = str(value).strip()
        if not value:
            continue
        if len(value) > 1000:
            raise ValueError("Entry exceeds 1000 characters")
        _, new = BlacklistEntry.objects.get_or_create(value=value)
        created += int(new)
    return JsonResponse(
        {"ok": True, "created": created, "count": BlacklistEntry.objects.count()}
    )


@endpoint()
def results_export(request):
    """Download the saved results current lookups, captures and workflows produce, newest first. Reads the database only."""
    from leakscope.build.kinds import ENDPOINT_FIELDS, endpoint_items
    from leakscope.phase3.state import visible_endpoints

    fmt = request.GET.get("format", "csv").lower()
    if fmt not in {"csv", "json"}:
        raise ValueError("Select JSON or CSV")
    limit = _number(request.GET.get("limit"), 5000, 1, 20000)
    rows = visible_endpoints()
    technology = str(request.GET.get("technology") or "").strip()[:100]
    if technology:
        rows = rows.filter(service_type=technology)
    total = rows.count()
    items, _ = endpoint_items(list(rows.order_by("-last_seen", "pk").values_list("pk", flat=True)[:limit]))
    records = [{"id": item["id"], **item["fields"]} for item in items]
    if fmt == "json":
        response = JsonResponse(records, safe=False)
    else:
        columns = ["id", *ENDPOINT_FIELDS]
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            line = {}
            for key in columns:
                value = record.get(key)
                if isinstance(value, (list, tuple)):
                    value = "; ".join(str(part) for part in value)
                # CSV readers may evaluate formula-leading strings. JSON retains original data.
                line[key] = "'" + value if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")) else value
            writer.writerow(line)
        response = HttpResponse(buf.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="saved-results%s.%s"' % ("-" + re.sub(r"[^A-Za-z0-9_.-]", "_", technology) if technology else "", fmt)
    response["X-Content-Type-Options"] = "nosniff"
    response["X-Total-Count"] = str(total)
    response["X-Exported-Count"] = str(len(records))
    return response


@endpoint()
def stored_export(request):
    from leakscope_app.services.findings import (
        selected_models,
        review_context,
        hidden_condition,
    )

    name = _type(request.GET.get("type"))
    if not name:
        raise ValueError("Select a service type")
    model = selected_models(name)[name]
    fmt = request.GET.get("format", "csv").lower()
    if fmt not in {"csv", "json"}:
        raise ValueError("Select JSON or CSV")
    reveal = _flag(request.GET.get("include_sensitive"))
    columns = [f.name for f in model._meta.fields if f.name != "id"]
    query = model.objects.exclude(
        hidden_condition(name, model, review_context())
    ).order_by("pk")
    rows = []
    for obj in query.iterator(chunk_size=500):
        item = {}
        for field in model._meta.fields:
            if field.name == "id":
                continue
            value = getattr(obj, field.attname)
            item[field.name] = (
                "[masked]"
                if field.name in SENSITIVE and value and not reveal
                else value
            )
        rows.append(item)
        if len(rows) > 50000:
            raise ValueError(
                "Export is limited to 50000 stored rows; filter the collection first"
            )
    if fmt == "json":
        response = JsonResponse(rows, safe=False)
    else:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            # CSV readers may evaluate formula-leading strings. JSON retains original data.
            writer.writerow(
                {
                    k: (
                        "'" + str(v)
                        if isinstance(v, str)
                        and v.startswith(("=", "+", "-", "@", "\t", "\r"))
                        else v
                    )
                    for k, v in row.items()
                }
            )
        response = HttpResponse(buf.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="%s.%s"' % (name, fmt)
    response["X-Content-Type-Options"] = "nosniff"
    return response
