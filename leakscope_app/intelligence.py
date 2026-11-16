"""Validated result intelligence; context always comes from server-side evidence."""
import ipaddress
import hashlib
import json
import re
from urllib.parse import urlsplit, urlunsplit

from django.http import JsonResponse
from django.core.exceptions import ObjectDoesNotExist

from leakscope.intelligence.clients import ask_endpoint, research_queries, firecrawl_configured
from leakscope.intelligence.queries import compile_queries, _url
from leakscope.services.config import get_config
from leakscope.phase3.usage_policy import validate_demo
from leakscope.services.registry import canonical_type, service_types
from .models import QuerySuggestion, ServiceEndpoint
from .workbench import endpoint, _body, _model, _finding, _hidden

PROVIDERS = ['shodan', 'zoomeye', 'netlas']

_EVIDENCE_PATHS = {
    'device': [('device',), ('devicetype',), ('portinfo', 'device')],
    'server': [('http', 'headers', 'server'), ('http', 'server'), ('server',), ('webserver',), ('httpx', 'webserver')],
    'product': [('product',), ('portinfo', 'product')],
    'favicon.sha256': [('http', 'favicon', 'hash_sha256'), ('favicon_sha256',)],
    'favicon.mmh3': [('http', 'favicon', 'hash'), ('favicon_mmh3',), ('httpx', 'favicon')],
    'certificate.sha256': [('certificate', 'fingerprint_sha256'), ('certificate', 'sha256'), ('ssl', 'cert', 'fingerprint', 'sha256')],
    'certificate.sha1': [('certificate', 'fingerprint_sha1'), ('ssl', 'cert', 'fingerprint', 'sha1'), ('ssl', 'cert', 'fingerprint'), ('certificate', 'fingerprint')],
}


def _observed_fields(obj, source, cfg):
    """Only algorithm-specific, observed values; never manufacture fingerprints."""
    from leakscope.intelligence.queries import _value
    fields = []
    seen = set()
    for observation in obj.observations.filter(hidden=False).only('data', 'provider', 'observed_at')[:12]:
        metadata = observation.data.get('metadata') or {}
        from leakscope.intelligence.device_queries import observed_device_values
        observed=observed_device_values(metadata,getattr(obj,'service_type',''))
        paths_by_field={key:list(paths) for key,paths in _EVIDENCE_PATHS.items()}
        for canonical,value in observed:
            marker='_native_'+canonical+'_'+hashlib.sha256(str(value).encode()).hexdigest()[:12]
            metadata=dict(metadata,**{marker:value})
            paths_by_field.setdefault(canonical,[]).append((marker,))
        for canonical, paths in paths_by_field.items():
            for path in paths:
                value = metadata
                for part in path:
                    value = value.get(part) if isinstance(value, dict) else None
                if not isinstance(value, (str, int)) or isinstance(value, bool): continue
                value = _clean(value, cfg, 1000)
                if canonical.endswith(('.sha1', '.sha256')): value = value.replace(':', '').strip().lower()
                if '[redacted]' in value: continue
                try: value = _value(canonical, value)
                except ValueError: continue
                marker = (canonical, value)
                if marker in seen: continue
                seen.add(marker)
                suffix = hashlib.sha256((canonical + '\0' + value).encode()).hexdigest()[:12]
                fields.append({'id': source + ':' + canonical + ':' + suffix, 'field': canonical,
                    'label': canonical.replace('.', ' ').title(), 'value': value, 'source': source,
                    'provenance': {'provider': observation.provider, 'observation_id': str(observation.pk),
                                   'observed_at': observation.observed_at.isoformat() if observation.observed_at else None}})
                break
    return fields[:32]


def _config():
    return get_config()['config']


def _clean(value, config, limit=1000):
    text = str(value or '')
    for key, secret in config.items():
        if any(suffix in key for suffix in ('KEY', 'PASSWORD', 'TOKEN')) and isinstance(secret, str) and secret:
            text = text.replace(secret, '[redacted]')
    text = re.sub(r'(?i)\b(?:bearer\s+|(?:api[_-]?key|password|secret|token)\s*[:=]\s*)[^\s,;]+', '[redacted]', text)
    return text[:limit]


def _clean_data(value, config):
    if isinstance(value, dict):
        return {str(k): _clean_data(v, config) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean_data(v, config) for v in value]
    return _clean(value, config, 10000) if isinstance(value, str) else value


def _public_base(value):
    try:
        parts = urlsplit(str(value or ''))
        if parts.scheme not in {'http', 'https'} or not parts.hostname:
            return ''
        host = parts.hostname
        if ':' in host:
            host = '[' + host + ']'
        if parts.port:
            host += ':' + str(parts.port)
        return urlunsplit((parts.scheme, host, parts.path, '', ''))
    except ValueError:
        return ''


@endpoint(('GET', 'POST'))
def settings(request):
    from leakscope.intelligence.clients import _thinking_mode
    cfg = _config()
    if request.method == 'POST':
        from leakscope.services.integrations import save_profiles
        save_profiles(_body(request), cfg)
        from .workbench import BALANCE_CACHE
        BALANCE_CACHE.clear()
        cfg = _config()
    return JsonResponse({
        'llm': {'configured': bool(cfg.get('LLM_API_BASE') and cfg.get('LLM_MODEL')),
                'key_configured': bool(cfg.get('LLM_API_KEY')),
                'base_url': _public_base(cfg.get('LLM_API_BASE')), 'model': _clean(cfg.get('LLM_MODEL'), cfg, 200),
                'thinking_mode':_thinking_mode(cfg.get('LLM_THINKING_MODE','default'))},
        'firecrawl': {'configured': firecrawl_configured(cfg), 'key_configured': bool(cfg.get('FIRECRAWL_API_KEY')),
                      'base_url': _public_base(cfg.get('FIRECRAWL_API_URL') or 'https://api.firecrawl.dev/v2')},
        'netlas': {'configured': bool(cfg.get('NETLAS_API_KEY'))},
        'domain_sources': {name:{'key_configured':bool(cfg.get(key))} for name,key in [('certspotter','CERTSPOTTER_API_KEY'),('urlscan','URLSCAN_API_KEY')]},
        'providers': PROVIDERS, 'errors': cfg.get('INTEGRATION_ERRORS', []),
    })


def _source_context(data, cfg, allow_empty=False):
    refs = data.get('references', [])
    if not isinstance(refs, list) or len(refs) > 10 or (not refs and not allow_empty and not data.get('suggestion_id')):
        raise ValueError('Select between one and ten findings')
    fields, references, records, origin_scopes = [], [], [], []
    seen = set()
    labels = {t['key']: t['label'] for t in service_types()}
    for ref in refs:
        if not isinstance(ref, dict) or isinstance(ref.get('id'), bool):
            raise ValueError('Invalid finding reference')
        name = canonical_type(ref.get('type'))
        if name == 'endpoint':
            from uuid import UUID
            try: object_id = str(UUID(str(ref.get('id'))))
            except ValueError: raise ValueError('Invalid endpoint ID') from None
        else:
            try: object_id = int(ref.get('id'))
            except (TypeError, ValueError): raise ValueError('Invalid finding ID') from None
            if object_id < 1: raise ValueError('Invalid finding ID')
        marker = (name, object_id)
        if marker in seen:
            continue
        seen.add(marker)
        if name == 'endpoint':
            from leakscope.phase3.state import visible_endpoints
            obj = visible_endpoints().get(pk=object_id)
        else: obj = _model(name).objects.get(pk=object_id)
        if name != 'endpoint' and _hidden(obj, name):
            raise ObjectDoesNotExist('Finding is hidden')
        row = ({'ip': obj.address, 'port': str(obj.port), 'label': f'{obj.address}:{obj.port}', 'url': ''}
               if name == 'endpoint' else _finding(obj, name))
        search = getattr(obj, 'search', None)
        collection_scope = {}
        if name == 'endpoint':
            source_observation = obj.observations.filter(hidden=False, data__metadata__collection_scope__isnull=False).first()
            if source_observation: collection_scope = source_observation.data['metadata'].get('collection_scope') or {}
        origin_scopes.append({key: str(collection_scope.get(key) or getattr(search, key, '') or '') for key in ('network', 'country', 'exclude')})
        source = f'{name}:{object_id}'
        references.append({'type': name, 'id': object_id, 'label': _clean(row['label'], cfg, 300)})
        facts = {}
        raw_title = getattr(obj, 'title', None) or getattr(obj, 'name', None)
        if raw_title:
            facts['title'] = _clean(raw_title, cfg, 500)
        if name not in {'keys', 'javascript', 'github', 'dirs', 'amazonbe', 'amazons3be', 'amazonbuckets'}:
            facts['product'] = _clean(getattr(obj, 'service_type', None) or labels.get(name, name), cfg, 200)
        if row.get('ip'):
            try:
                facts['ip'] = str(ipaddress.ip_address(row['ip']))
            except ValueError:
                # Do not present arbitrary stored text as a valid IP signal.
                if name == 'endpoint': facts['host'] = _clean(obj.address, cfg, 253)
        if str(row.get('port', '')).isdigit() and 0 < int(row['port']) < 65536:
            facts['port'] = int(row['port'])
        try:
            host = urlsplit(row.get('url', '')).hostname
            if host and host != facts.get('ip'):
                facts['host'] = _clean(host, cfg, 253)
        except ValueError:
            pass
        for key in ('server', 'asn', 'country'):
            value = getattr(obj, key, None)
            if value:
                facts[key] = _clean(value, cfg, 200)
        for key, value in facts.items():
            if value and '[redacted]' not in str(value):
                fields.append({'id': source + ':' + key, 'field': key,
                               'label': 'Service classification' if key == 'product' else key.replace('.', ' ').title(),
                               'value': value, 'source': source})
        if name == 'endpoint':
            observed = _observed_fields(obj, source, cfg)
            # Recorded products supersede a derived service classification.
            if any(f['field'] == 'product' for f in observed):
                fields = [f for f in fields if not (f['source'] == source and f['field'] == 'product')]
                facts.pop('product', None)
            fields.extend(observed)
        records.append({'reference': source, 'label': f'{name} finding #{object_id}', 'facts': facts})
    truncated = len(fields) > 64
    fields = fields[:64]
    selected = data.get('selected_fields')
    if selected is not None:
        if not isinstance(selected, list) or len(selected) > 64 or any(not isinstance(x, str) for x in selected):
            raise ValueError('Invalid evidence field selection')
        available = {field['id'] for field in fields}
        if not set(selected) <= available:
            raise ValueError('Selected evidence is not available for these findings')
        chosen = set(selected)
        fields = [field for field in fields if field['id'] in chosen]
    for record in records:
        record['facts'] = {}
        evidence = []
        for field in fields:
            if field['source'] != record['reference']: continue
            key, value = field['field'], field['value']
            if key in record['facts']:
                prior = record['facts'][key]
                record['facts'][key] = prior + [value] if isinstance(prior, list) else [prior, value]
            else: record['facts'][key] = value
            if 'provenance' in field: evidence.append({'field': key, 'value': value, **field['provenance']})
        if evidence: record['evidence_sources'] = evidence
    context = {'findings': records}
    if data.get('suggestion_id'):
        suggestion = QuerySuggestion.objects.get(pk=data['suggestion_id'])
        context['suggestion'] = {'title': suggestion.title, 'rationale': suggestion.rationale,
                                 'queries': suggestion.queries, 'sources': suggestion.sources[:5]}
    scope, conflicts = {}, []
    for key in ('network', 'country', 'exclude'):
        values = {item[key] for item in origin_scopes}
        scope[key] = next(iter(values)) if len(values) == 1 else ''
        if len(values) > 1: conflicts.append(key)
    warnings = ['Selected findings have differing scopes; choose an explicit ' + ', '.join(conflicts) + ' scope.'] if conflicts else []
    if truncated: warnings.append('Context is limited to 64 evidence fields; narrow the finding selection for more detail.')
    return {'references': references, 'fields': fields, 'context': _clean_data(context, cfg),
            'scope': scope, 'scope_conflicts': conflicts, 'warnings': warnings}


@endpoint(('POST',))
def context(request):
    return JsonResponse(_source_context(_body(request), _config(), allow_empty=True))


def _serialize(item):
    return {'id': str(item.pk), 'kind': item.kind, 'title': item.title, 'status': item.status,
            'queries': item.queries, 'sources': item.sources, 'rationale': item.rationale,
            'references': item.references, 'scope': item.scope, 'created_at': item.created_at.isoformat()}


@endpoint(('POST',))
def similar(request):
    data = _body(request)
    cfg = _config()
    source = _source_context(data, cfg)
    if not source['fields']:
        raise ValueError('Select at least one available evidence field')
    signals = [{k: field[k] for k in ('field', 'value', 'source')} for field in source['fields']]
    scope = data.get('scope', source['scope'])
    if not isinstance(scope, dict): raise ValueError('Invalid scope')
    changed = any(source['scope'].get(k) and scope.get(k, '') != source['scope'][k] for k in source['scope'])
    if source['scope_conflicts'] and not all(k in scope for k in source['scope_conflicts']):
        raise ValueError('Resolve differing source scopes before generating queries')
    if (changed or source['scope_conflicts']) and data.get('scope_override') is not True:
        raise ValueError('Changing the originating search scope requires an explicit scope edit')
    queries = compile_queries(signals, mode=data.get('mode', 'all'), scope=scope)
    item = QuerySuggestion.objects.create(kind='similar', title='Similar to ' + ', '.join(r['label'] for r in source['references'])[:220],
        queries=_clean_data(queries, cfg), signals=signals, references=source['references'], scope=scope,
        rationale='Generated deterministically from selected finding evidence; not executed.')
    return JsonResponse({'suggestion': _serialize(item), 'warnings': source['warnings']})


@endpoint(('POST',))
def ask(request):
    data = _body(request)
    demo=validate_demo(data.get('demo',False))
    question = data.get('question')
    if not isinstance(question, str) or not question.strip() or len(question) > 4000:
        raise ValueError('Enter a question of at most 4000 characters')
    cfg = _config()
    source = _source_context(data, cfg, allow_empty=True)
    result = ask_endpoint(_clean(question, cfg, 4000), source['context'], cfg,**({'demo':True} if demo else {}))
    sent_context=result.pop('context_sent',source['context']) if demo else source['context']
    return JsonResponse(dict(_clean_data(result, cfg), context=_clean_data(sent_context,cfg)))


@endpoint(('POST',))
def research(request):
    data = _body(request)
    demo=validate_demo(data.get('demo',False))
    topic = data.get('topic')
    if not isinstance(topic, str) or not topic.strip() or len(topic) > 300:
        raise ValueError('Enter a technology/topic of at most 300 characters')
    cfg = _config()
    result = _clean_data(research_queries(_clean(topic, cfg, 300), cfg,**({'demo':True} if demo else {})), cfg)
    items = save_research_suggestions(result, cfg)
    response={'sources': result.get('sources', [])[:5], 'suggestions': items, 'warnings': result.get('warnings', [])}
    if demo:response.update(demo=True,partial=True,usage_policy=result.get('usage_policy',{}))
    return JsonResponse(response)


def save_research_suggestions(result, cfg):
    """Shared persistence for manual and scheduled, source-validated research."""
    sources = result.get('sources', [])[:5]
    items = []
    for candidate in result.get('suggestions', [])[:10]:
        signals = candidate['signals']
        queries = compile_queries(signals, mode='all')
        signature = {'technology': candidate.get('technology', '').casefold(),
                     'signals': sorted([(s['field'], str(s['value'])) for s in signals]),
                     'sources': sorted(s['url'] for s in sources)}
        fingerprint = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()
        item, created = QuerySuggestion.objects.get_or_create(fingerprint=fingerprint, defaults={
            'kind': 'research', 'title': candidate['title'][:255], 'queries': queries,
            'sources': sources, 'signals': signals, 'rationale': candidate.get('rationale', '')[:4000]})
        # Preserve the source snapshot, signal IDs and rationale together when
        # reusing a candidate. Ranked refreshes may assign S1 to another URL.
        if not any(old['id'] == str(item.pk) for old in items): items.append(_serialize(item))
    return items


@endpoint()
def suggestions(request):
    rows = QuerySuggestion.objects.all()
    status = request.GET.get('status')
    if status:
        if status not in {'draft', 'adopted', 'rejected'}:
            raise ValueError('Invalid suggestion status')
        rows = rows.filter(status=status)
    return JsonResponse({'results': [_serialize(row) for row in rows[:100]], 'count': rows.count()})


@endpoint(('POST',))
def review(request, suggestion_id):
    status = _body(request).get('status')
    if status not in {'draft', 'adopted', 'rejected'}:
        raise ValueError('Invalid suggestion status')
    item = QuerySuggestion.objects.get(pk=suggestion_id)
    item.status = status
    item.save(update_fields=['status', 'updated_at'])
    return JsonResponse({'suggestion': _serialize(item)})


@endpoint(('POST',))
def edit_queries(request, suggestion_id):
    data = _body(request)
    provider, query = data.get('provider'), data.get('query')
    if provider not in PROVIDERS or not isinstance(query, str) or not query.strip() or len(query) > 8000 or any(ord(c) < 32 for c in query):
        raise ValueError('Select a provider and a query of at most 8000 printable characters')
    item = QuerySuggestion.objects.get(pk=suggestion_id)
    query = _clean(query.strip(), _config(), 8000)
    item.queries[provider] = {'query': query, 'url': _url(provider, query), 'supported': True,
                              'warnings': ['Manually edited query; unvalidated provider syntax.']}
    item.status = 'draft'
    item.save(update_fields=['queries', 'status', 'updated_at'])
    return JsonResponse({'suggestion': _serialize(item)})
