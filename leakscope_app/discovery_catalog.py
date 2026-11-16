"""Database-backed discovery choices layered over the static protocol catalog."""
from copy import deepcopy
from uuid import UUID
import ipaddress

from leakscope.phase3.catalog import catalog as builtins, catalog_entry as builtin_entry
from leakscope.intelligence.queries import compile_queries, _scope


def _saved(key):
    from .models import DiscoveryPreset
    try:pk=UUID(str(key).removeprefix('preset:'))
    except (ValueError,TypeError):raise ValueError('Invalid saved discovery preset') from None
    try:return DiscoveryPreset.objects.get(pk=pk)
    except DiscoveryPreset.DoesNotExist:raise ValueError('Saved discovery preset no longer exists') from None


def _record(preset,base):
    protocol=base.get(preset.service_type)
    if protocol is None:
        from leakscope.phase3.native import identity_capability
        identity=identity_capability(preset.service_type)
        protocol={'key':preset.service_type,'category':'other','category_label':'Other services','categories':['other'],
            'ports':[],'transport':'tcp','http_capture':preset.service_type in {'http','https'},'identity':identity['implemented'],
            'identity_operation':identity['operation'],'identity_protocol':identity['protocol'],'identity_transports':identity['transports'],
            'identity_limitation':identity['limitation'],'identity_sources':identity['sources'],
            'inspection':False,'export':False,'detection_confidence':'candidate','detection_notes':'User-saved indexed evidence query'}
    record=deepcopy(protocol)
    queries=compile_queries(preset.signals,mode=preset.mode,scope={})
    fingerprints={p:dict(dto,reason='Reviewed user preset; provider index evidence still requires confirmation.',basis=[]) for p,dto in queries.items()}
    unavailable={p:{'query':'','supported':False,'reason':'Saved presets use their reviewed response fingerprint queries.','basis':[]} for p in queries}
    record.update(key='preset:'+str(preset.pk),label='Saved · '+preset.name,base_type=preset.service_type,
        queries={},preset_kind='saved',preset_scope=preset.scope,
        fingerprint_summary=preset.description or 'Saved from selected result evidence; no query runs on save.',
        query_variants={'fingerprint':fingerprints,'nonstandard':deepcopy(unavailable),'ports':unavailable})
    return record


def catalog(include_saved=True):
    from .models import DiscoveryPreset
    records=builtins()
    if not include_saved:return records
    base={item['key']:item for item in records}
    return records+[_record(row,base) for row in DiscoveryPreset.objects.order_by('name','pk')]


def catalog_entry(key):
    if str(key).startswith('preset:'):
        return _record(_saved(key),{item['key']:item for item in builtins()})
    return builtin_entry(key)


def profile_query(key,provider,mode='fingerprint'):
    if not str(key).startswith('preset:'):
        from leakscope.phase3.catalog import profile_query as builtin_query
        return builtin_query(key,provider,mode)
    if provider not in {'shodan','zoomeye','netlas'} or mode not in {'fingerprint','nonstandard','ports'}:raise ValueError('Unknown preset strategy or provider')
    entry=catalog_entry(key)
    return dict(deepcopy(entry['query_variants'][mode][provider]),preset_scope=entry['preset_scope'])


def narrow_scope(saved,requested):
    """Retain saved boundaries and intersect additional Discover constraints."""
    saved=_scope(saved);requested=_scope(requested);result=dict(saved)
    for key,value in requested.items():
        previous=result.get(key)
        if not previous:result[key]=value;continue
        if key=='network':
            left=ipaddress.ip_network(previous,strict=False);right=ipaddress.ip_network(value,strict=False)
            if left.version!=right.version or not left.overlaps(right):raise ValueError('Discover network does not overlap the saved preset scope')
            result[key]=previous if left.prefixlen>=right.prefixlen else value
        elif key=='country':
            if previous!=value:raise ValueError('Discover country conflicts with the saved preset scope')
        elif key=='exclude':result[key]=','.join(dict.fromkeys(term.strip() for term in (previous+','+value).split(',') if term.strip()))
    return result
