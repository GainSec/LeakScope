"""Reviewed, durable discovery rules compiled from server-side result evidence.

These endpoints never execute searches or target requests. Saved rules preserve
an explicit user snapshot even if the originating result is hidden later.
"""
import hashlib
import json
from uuid import UUID

from django.db import transaction
from django.http import JsonResponse

from leakscope.intelligence.queries import compile_queries, _scope
from leakscope.services.registry import canonical_type
from leakscope.phase3.state import visible_endpoints
from leakscope_app.models import DiscoveryPreset
from .intelligence import _source_context, _config, _clean
from .workbench import endpoint, _body

PROVIDERS = ('shodan', 'zoomeye', 'netlas')
_PREVIEW_KEYS = {'references', 'selected_fields', 'mode', 'scope', 'scope_override'}
_IDENTITY_FIELDS = {'modbus.vendor', 'modbus.product', 'bacnet.vendor', 'bacnet.model'}
_DEFAULT_FIELDS = {'title', 'product', 'body', 'server'}


def serialize(row):
    return {'id': str(row.pk), 'key': 'preset:' + str(row.pk), 'name': row.name, 'label': row.name,
            'description': row.description, 'service_type': row.service_type, 'mode': row.mode,
            'signals': row.signals, 'queries': row.queries, 'scope': row.scope,
            'references': row.references, 'created_at': row.created_at.isoformat(),
            'updated_at': row.updated_at.isoformat(), 'collection_mode': 'passive'}


def _text(data, name, config, maximum, required=False):
    value = data.get(name, '')
    if not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 and (name != 'description' or c not in '\n\t') for c in value) or '\x7f' in value:
        raise ValueError('Invalid ' + name)
    value = _clean(value.strip(), config, maximum)
    if required and not value:
        raise ValueError('Enter ' + name)
    return value


def _service_type(references):
    types = set()
    for reference in references:
        if reference['type'] == 'endpoint':
            types.add(visible_endpoints().get(pk=reference['id']).service_type)
        else:
            types.add(canonical_type(reference['type']))
    if len(types) != 1:
        raise ValueError('Choose results with one underlying service type for a preset')
    return types.pop()


def _selection(data, source):
    fields = source['fields']
    if 'selected_fields' in data:
        selected = data['selected_fields']
        if not isinstance(selected, list) or not 1 <= len(selected) <= 64 or any(not isinstance(item, str) for item in selected):
            raise ValueError('Select between one and64 observed fields')
        if not set(selected) <= {field['id'] for field in fields}:
            raise ValueError('Selected evidence is not available for these findings')
        return list(dict.fromkeys(selected))
    identity = [field['id'] for field in fields if field['field'] in _IDENTITY_FIELDS]
    if identity:
        return identity
    return [field['id'] for field in fields if field['field'] in _DEFAULT_FIELDS and field.get('label') != 'Service classification']


def _prepare(data, *, saving=False):
    allowed = _PREVIEW_KEYS | ({'name', 'description'} if saving else set())
    if set(data) - allowed:
        raise ValueError('Unsupported preset fields; queries and signal values must come from observed evidence')
    if saving and 'selected_fields' not in data:
        raise ValueError('Preview and select observed fields before saving the preset')
    cfg = _config()
    source = _source_context({'references': data.get('references', [])}, cfg)
    selected = _selection(data, source)
    if not selected and saving:
        raise ValueError('No useful identity signals were selected; choose observed fields explicitly')
    selected_set = set(selected)
    signals = [dict(field) for field in source['fields'] if field['id'] in selected_set]
    mode = data.get('mode', 'all')
    if mode not in {'all', 'any'}:
        raise ValueError('Combination mode must be all or any')
    origin = _scope(source['scope'])
    raw_scope = data.get('scope', source['scope'])
    scope = _scope(raw_scope)
    changed = any(origin.get(key) and scope.get(key, '') != origin[key] for key in origin)
    conflicts = source['scope_conflicts']
    unresolved = bool(conflicts and (data.get('scope_override') is not True or not all(key in raw_scope for key in conflicts)))
    if changed and data.get('scope_override') is not True:
        raise ValueError('Changing the originating search scope requires an explicit scope edit')
    if unresolved:
        if saving:
            raise ValueError('Resolve differing source scopes before saving the preset')
        queries = {provider: {'query': '', 'url': '', 'supported': False,
                   'warnings': ['Resolve differing source scopes before generating queries']} for provider in PROVIDERS}
    elif not selected:
        queries = {provider: {'query': '', 'url': '', 'supported': False,
                   'warnings': ['No identity fields were selected automatically; choose observed fields explicitly.']} for provider in PROVIDERS}
    else:
        queries = compile_queries(signals, mode=mode, scope=scope)
    for query in queries.values():
        if query.get('supported') and len(query.get('query', '')) > 8000:
            query.update(query='', url='', supported=False)
            query['warnings'] = list(query.get('warnings') or []) + ['The complete query exceeds8000 characters; reduce the selected evidence. No predicates were dropped.']
    base = _service_type(source['references'])
    identity_values = list(dict.fromkeys(str(field['value']) for field in signals if field['field'] in _IDENTITY_FIELDS))
    display_values = identity_values or list(dict.fromkeys(str(field['value']) for field in signals if field['field'] in _DEFAULT_FIELDS))
    suggested_name = ' · '.join(display_values)[:255] or (base.title() + ' discovery preset')
    warnings = [warning for warning in source['warnings'] if unresolved or not warning.startswith('Selected findings have differing scopes;')]
    if not selected:
        warnings.append('No useful identity fields were selected automatically; choose observed fields explicitly.')
    return {'fields': source['fields'], 'selected_fields': selected, 'queries': queries,
            'service_type': base, 'suggested_name': suggested_name, 'scope': scope, 'references': source['references'],
            'warnings': warnings, 'scope_conflicts': conflicts if unresolved else [], 'signals': signals, 'mode': mode}, cfg


@endpoint(('POST',))
def preview(request):
    prepared, _ = _prepare(_body(request))
    return JsonResponse(prepared)


@endpoint(('GET', 'POST'))
def presets(request):
    if request.method == 'GET':
        rows = DiscoveryPreset.objects.all()
        return JsonResponse({'results': [serialize(row) for row in rows], 'count': rows.count()})
    data = _body(request)
    with transaction.atomic():
        prepared, cfg = _prepare(data, saving=True)
        if not any(query.get('supported') and query.get('query') for query in prepared['queries'].values()):
            raise ValueError('No provider can represent every selected predicate; change the selected evidence')
        name = _text(data, 'name', cfg, 255, required=True)
        description = _text(data, 'description', cfg, 4000)
        signature = {'service_type': prepared['service_type'], 'mode': prepared['mode'], 'scope': prepared['scope'],
                     'signals': sorted({(item['field'], str(item['value'])) for item in prepared['signals']})}
        fingerprint = hashlib.sha256(json.dumps(signature, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        row, created = DiscoveryPreset.objects.get_or_create(fingerprint=fingerprint, defaults={
            'name': name, 'description': description, 'service_type': prepared['service_type'], 'mode': prepared['mode'],
            'signals': prepared['signals'], 'queries': prepared['queries'], 'scope': prepared['scope'],
            'references': prepared['references']})
    return JsonResponse({'preset': serialize(row), 'created': created}, status=201 if created else 200)


@endpoint(('POST',))
def delete(request, preset_id):
    row = DiscoveryPreset.objects.get(pk=UUID(str(preset_id)))
    identifier = str(row.pk)
    row.delete()
    return JsonResponse({'deleted': True, 'id': identifier})
