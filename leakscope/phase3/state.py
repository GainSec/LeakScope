"""Stable endpoint identity, evidence history, tags and bounded work leases."""
import hashlib
import ipaddress
import json
import math
import re
from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from leakscope.services.config import get_config
from leakscope_app.models import (ServiceEndpoint, ServiceObservation, Tag, TagAssignment,
    ExecutionLease, ProviderEndpointState, ObservationChange)
from .analysis import stable_evidence, summarize_changes, index_observation


def normalize_address(value):
    if not isinstance(value, str) or not value or len(value) > 253:
        raise ValueError('Invalid endpoint address')
    value = value.strip().rstrip('.')
    if value.startswith('[') and value.endswith(']'):value=value[1:-1]
    elif '[' in value or ']' in value:raise ValueError('Invalid endpoint address brackets')
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        if re.fullmatch(r'(?:\d+\.){3}\d+',value):raise ValueError('Invalid IPv4 address') from None
    try:
        value = value.encode('idna').decode().lower()
    except UnicodeError:
        raise ValueError('Invalid endpoint address') from None
    if not re.fullmatch(r'(?=.{1,253}$)[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', value) or '..' in value:
        raise ValueError('Endpoint address must be an IP or hostname, without a URL/path')
    if any(len(label) > 63 or label.startswith('-') or label.endswith('-') for label in value.split('.')):
        raise ValueError('Invalid hostname label')
    return value


def visible_endpoints():
    visible=ServiceObservation.objects.filter(endpoint_id=OuterRef('pk'),hidden=False).exclude(provider='certstream')
    return ServiceEndpoint.objects.annotate(has_visible_source=Exists(visible)).filter(has_visible_source=True)


def refresh_endpoint_projection(endpoint_id):
    """Write-side cache refresh; never allow hidden source titles to leak."""
    visible=ServiceObservation.objects.filter(endpoint_id=endpoint_id,hidden=False).order_by('-last_collected_at','-pk')
    values={}
    for field in ('title','service_type'):
        value=visible.exclude(**{'data__'+field:''}).values_list('data__'+field,flat=True).first()
        values[field]=value if isinstance(value,str) else ''
    ServiceEndpoint.objects.filter(pk=endpoint_id).update(**values)


def _secret_values(config):
    values=[]
    def visit(value):
        if not isinstance(value,dict):return
        for key,item in value.items():
            if isinstance(item,dict):visit(item)
            elif isinstance(item,str) and item and re.search(r'(?:^|_)(?:key|password|token|secret)$',str(key),re.I):values.append(item)
    visit(config)
    return values


def _clean_text(value,secrets):
    text=str(value).encode('utf-8',errors='replace').decode('utf-8')
    text=''.join(char if ord(char)>=32 or char in '\n\r\t' else '\ufffd' for char in text)
    for secret in secrets:text=text.replace(secret,'[redacted]')
    return text


def _json_safe(value, maximum=1024 * 1024, *, config=None):
    secrets=_secret_values(config if config is not None else get_config()['config'])
    visited=0
    def clean(item,depth=0):
        nonlocal visited
        visited+=1
        if depth>12 or visited>20000:raise ValueError('Observation metadata is too deeply nested or complex')
        if isinstance(item,dict):
            result={}
            for key,value in item.items():
                normalized=_clean_text(key,secrets)
                if normalized in result:raise ValueError('Observation metadata keys collide after normalization')
                result[normalized]=clean(value,depth+1)
            return result
        if isinstance(item,(list,tuple)):return [clean(value,depth+1) for value in item]
        if isinstance(item,float) and not math.isfinite(item):return None
        if item is None or isinstance(item,(bool,int,float)):return item
        return _clean_text(item,secrets)
    cleaned=clean(value)
    if len(json.dumps(cleaned,ensure_ascii=False,allow_nan=False).encode())>maximum:
        raise ValueError('Observation metadata exceeds 1 MiB')
    return cleaned


@transaction.atomic
def ingest_observation(provider, row):
    if not isinstance(row, dict) or not isinstance(provider, str) or not provider or len(provider) > 32:
        raise ValueError('Invalid observation')
    address = normalize_address(row.get('address'))
    port = row.get('port')
    if isinstance(port, bool) or not isinstance(port,(str,int)): raise ValueError('Invalid port')
    try: port = int(port)
    except (ValueError, TypeError): raise ValueError('Invalid port') from None
    if not 1 <= port <= 65535: raise ValueError('Invalid port')
    transport = str(row.get('transport') or 'tcp').lower()
    scheme = str(row.get('scheme') or '').lower()
    if transport not in {'tcp', 'udp'} or scheme not in {'', 'http', 'https', 'ftp', 'ftps', 'ssh', 'mqtt', 'mqtts', 'opc.tcp'}:
        raise ValueError('Unsupported transport or scheme')
    def identity(value): return hashlib.sha256(json.dumps([address, port, transport, value]).encode()).hexdigest()
    key = identity(scheme)
    # One address and port is one saved result unless two different schemes were really seen there.
    # A port scan reports no scheme, a provider usually does: without this the same service is saved twice.
    named = list(ServiceEndpoint.objects.filter(address=address, port=port, transport=transport).exclude(scheme='').values_list('key', 'scheme')[:2])
    if not scheme and len(named) == 1:
        key, scheme = named[0]
    elif scheme and not named:
        # The service was first saved without a scheme: that row takes the scheme now known instead of gaining a twin.
        ServiceEndpoint.objects.filter(key=identity('')).update(key=key, scheme=scheme)
    now = timezone.now()
    cfg=get_config()['config']
    secrets=_secret_values(cfg)
    provider=_clean_text(provider,secrets)
    if len(provider)>32:raise ValueError('Invalid observation provider')
    raw_metadata=row.get('metadata') or {}
    if not isinstance(raw_metadata,dict):raise ValueError('Observation metadata must be an object')
    metadata = _json_safe(raw_metadata,config=cfg)
    from .technology_signatures import annotate
    metadata = annotate(metadata)
    data = {'title': _clean_text(row.get('title') or '',secrets)[:500],
            'service_type': _clean_text(row.get('service_type') or '',secrets)[:100], 'metadata': metadata}
    if metadata.get('implementation') and data['service_type'] in ('cardano','amaru','cardanonode','cardano-node','ouroboros'):
        data['service_type']=metadata['implementation']
        protocol_confirmed=metadata.get('identity_verified') is True
        metadata.update(service_type_basis='Experimental native response signature; Cardano protocol confirmed',
                        protocol_identity_verified=protocol_confirmed,cardano_protocol_confirmed=protocol_confirmed,identity_verified=False)
    stable=stable_evidence(data)
    hidden=row.get('hidden',False)
    if not isinstance(hidden,bool):raise ValueError('Invalid observation visibility')
    fingerprint = hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    endpoint, created = ServiceEndpoint.objects.get_or_create(key=key, defaults={
        'address': address, 'port': port, 'transport': transport, 'scheme': scheme,
        'service_type': '' if hidden else data['service_type'], 'title': '' if hidden else data['title'], 'first_seen': now, 'last_seen': now})
    # Serialize all per-endpoint state transitions, including first concurrent
    # provider observations. The payload itself is fetched only for a change.
    endpoint=ServiceEndpoint.objects.select_for_update().get(pk=endpoint.pk)
    # A legacy row projection is not directly comparable to a full provider
    # response, even when both carry the same proven provider attribution.
    # Retain a separate version/source link for each typed row, without treating
    # two duplicate legacy rows or a raw provider response as sequential states.
    channel='legacy:'+hashlib.sha256((provider+':'+str(metadata.get('legacy_reference'))).encode()).hexdigest()[:25] if str(row.get('source_id') or '').startswith('legacy:') else provider
    if provider.startswith('cloud:') and isinstance(metadata.get('cloud_resource'), dict):
        resource_url = metadata['cloud_resource'].get('url')
        if isinstance(resource_url, str) and resource_url == row.get('source_id'):
            channel = 'cloud:' + hashlib.sha256((provider + ':' + resource_url).encode()).hexdigest()[:26]
    current=ProviderEndpointState.objects.select_for_update().select_related('observation').defer('observation__data').filter(endpoint=endpoint,provider=channel).first()
    stamp = row.get('observed_at')
    if isinstance(stamp, str):
        try: stamp = parse_datetime(stamp)
        except ValueError: stamp = None
    if not isinstance(stamp,datetime):stamp=None
    if stamp and timezone.is_naive(stamp): stamp = timezone.make_aware(stamp, timezone.get_default_timezone())
    observation, first_capture = ServiceObservation.objects.defer('data').get_or_create(endpoint=endpoint, provider=provider, fingerprint=fingerprint,
        defaults={'source_id': _clean_text(row.get('source_id') or '',secrets)[:256], 'hidden':hidden, 'observed_at': stamp, 'last_observed_at':stamp, 'data': data,
                  'first_collected_at': now, 'last_collected_at': now})
    updates={'last_collected_at':now,'hidden':hidden}
    profile = metadata.get('matched_profile')
    if isinstance(profile, str) and profile:
        # The endpoint lock serializes collection lineage updates. Matching a
        # second search profile is provenance, not a new response version.
        retained = observation.data
        previous = retained.get('metadata') or {}
        matches = previous.get('profile_matches') or {}
        profiles = {p for p in matches if isinstance(p, str)} if isinstance(matches, dict) else set()
        if isinstance(previous.get('matched_profile'), str): profiles.add(previous['matched_profile'])
        profiles.add(profile)
        retained['metadata'] = dict(previous, profile_matches={p: True for p in sorted(profiles)[:128]})
        updates['data'] = retained
    if stamp and (observation.last_observed_at is None or stamp>observation.last_observed_at):updates['last_observed_at']=stamp
    # The endpoint lock above serializes this counter with every other capture.
    if not first_capture:updates['collection_count']=observation.collection_count+1
    ServiceObservation.objects.filter(pk=observation.pk).update(**updates)
    for field,value in updates.items():setattr(observation,field,value)
    # A historical/equal-time provider snapshot is not evidence of a newer state.
    stale=bool(current and stamp and current.provider_observed_at and stamp<=current.provider_observed_at and current.observation_id!=observation.pk)
    if current is None:
        ProviderEndpointState.objects.create(endpoint=endpoint,provider=channel,observation=observation,provider_observed_at=stamp,updated_at=now)
    elif not stale:
        version_changed=current.observation_id!=observation.pk
        if version_changed:
            ObservationChange.objects.create(endpoint=endpoint,provider=provider,previous=current.observation,
                observation=observation,collected_at=now,summary=summarize_changes(stable_evidence(current.observation.data),stable))
        current.observation=observation
        current.updated_at=now
        if stamp is not None or version_changed:current.provider_observed_at=stamp
        current.save(update_fields=['observation','provider_observed_at','updated_at'])
    changes={'last_seen':now}
    if not stale and not hidden:
        if data['title']:changes['title']=data['title']
        if data['service_type']:changes['service_type']=data['service_type']
    ServiceEndpoint.objects.filter(pk=endpoint.pk).update(**changes)
    for name,value in changes.items():setattr(endpoint,name,value)
    index_observation(observation,now,metadata=metadata)
    if provider != 'certstream':
        from .relationships import ingest_observation_graph, using_config
        try:
            with using_config(cfg):
                ingest_observation_graph(observation)
        except ValueError:
            # Some retained legacy addresses cannot be represented as DNS/IP
            # identities. Preserve their original observations for review.
            pass
    # True when this source had already saved a result for this endpoint.
    endpoint.repeat_capture=current is not None
    return endpoint


def tag_names(kind, key):
    return sorted(set(TagAssignment.objects.filter(target_kind=kind, target_key=str(key)).values_list('tag__name', flat=True)))


@transaction.atomic
def set_tags(kind, key, tags, origin='manual'):
    if kind not in {'endpoint', 'lookup', 'finding', 'watch', 'query', 'certificate', 'asset'}: raise ValueError('Unknown tag target')
    if not isinstance(tags, list) or len(tags) > 30 or len(str(key)) > 120 or not str(key): raise ValueError('Invalid tags')
    names = set()
    for name in tags:
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 64 or any(ord(c) < 32 for c in name): raise ValueError('Tags must be 1–64 printable characters')
        names.add(name.strip().casefold())
    assignments = TagAssignment.objects.filter(target_kind=kind, target_key=str(key), origin=origin)
    assignments.exclude(tag__name__in=names).delete()
    for name in names:
        tag, _ = Tag.objects.get_or_create(name=name)
        TagAssignment.objects.get_or_create(tag=tag, target_kind=kind, target_key=str(key), origin=origin)
    if kind=='finding':
        # Explicit tag writes project through the legacy link; GET reads never
        # normalize or mutate data. Preserve manual endpoint assignments.
        linked=ServiceObservation.objects.filter(source_id='legacy:'+str(key))
        visible_ids=set(linked.filter(hidden=False).values_list('endpoint_id',flat=True))
        projected=tag_names(kind,key)
        for endpoint_id in linked.values_list('endpoint_id',flat=True).distinct():
            set_tags('endpoint',endpoint_id,projected if endpoint_id in visible_ids else [],origin='finding:'+str(key))
    return tag_names(kind, key)


@transaction.atomic
def acquire_lease(key, seconds, data=None):
    now = timezone.now()
    existing = ExecutionLease.objects.select_for_update().filter(pk=key).first()
    if existing and existing.expires_at > now: return False
    # get_or_create's unique key also arbitrates simultaneous first claims.
    if existing is None:
        _, created = ExecutionLease.objects.get_or_create(key=key, defaults={'expires_at': now + timedelta(seconds=seconds), 'data': data or {}})
        return created
    existing.expires_at = now + timedelta(seconds=seconds)
    existing.data = data or {}
    existing.save(update_fields=['expires_at', 'data'])
    return True
