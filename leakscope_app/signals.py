"""Compatibility indexing for existing typed findings; no read-side writes."""
import ipaddress
import logging
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_save, post_delete, pre_save
from django.dispatch import receiver

from leakscope.services.registry import service_types, canonical_type, model_for_type
from leakscope.services.identity import normalize_host, normalize_port
from leakscope.phase3.catalog import catalog_entry
from leakscope.phase3.state import ingest_observation, normalize_address, set_tags, tag_names, refresh_endpoint_projection
from leakscope.phase3.analysis import index_artifact
from .models import (EvidenceArtifact, ServiceObservation, FindingReview, FingerprintLog,
                     CustomQuery, SavedLookup, TagAssignment)

log=logging.getLogger(__name__)
LEGACY_MODELS={entry['model']:entry['key'] for entry in service_types()}
_DEFAULT_PORTS={'http':80,'https':443,'ftp':21,'ftps':990,'ssh':22,'mqtt':1883,'mqtts':8883,'opc.tcp':4840}
_SAFE_FIELDS=('name','title','version','files','indices','databases','projects','repositories','bucket','buckets')


@receiver(post_delete,sender=CustomQuery,dispatch_uid='leakscope_deleted_query_tags')
@receiver(post_delete,sender=SavedLookup,dispatch_uid='leakscope_deleted_lookup_tags')
def remove_deleted_query_tags(sender,instance,using,**kwargs):
    kind='query' if sender is CustomQuery else 'lookup'
    TagAssignment.objects.using(using).filter(target_kind=kind,target_key=str(instance.pk)).delete()


def legacy_row(instance):
    name=LEGACY_MODELS.get(type(instance))
    if name is None:return None
    address=getattr(instance,'ip','') or ''
    port=getattr(instance,'port','')
    scheme=str(getattr(instance,'scheme','') or '').lower()
    if scheme not in _DEFAULT_PORTS:scheme=''
    url=getattr(instance,'url','') or (getattr(instance,'path','') if name=='javascript' else '')
    origin=''
    try:
        parts=urlsplit(str(url))
        if parts.scheme in _DEFAULT_PORTS and parts.hostname:
            address=parts.hostname
            scheme=parts.scheme
            port=parts.port or port or _DEFAULT_PORTS[scheme]
            host='['+address+']' if ':' in address else address
            origin=urlunsplit((scheme,host+(':'+str(port) if port else ''),parts.path[:1000],'',''))
    except ValueError:return None
    try:
        address=normalize_address(address)
        if isinstance(port,bool):return None
        port=int(port)
        if not 1<=port<=65535:return None
    except (TypeError,ValueError):return None
    try:entry=catalog_entry(name)
    except ValueError:entry={}
    search=getattr(instance,'search',None)
    provider=str(getattr(instance,'provider','') or getattr(search,'provider','') or 'unknown')[:20]
    reference=f'{name}:{instance.pk}'
    metadata={'legacy_reference':reference}
    if origin:metadata['source_url']=origin
    try:
        ip=str(ipaddress.ip_address(str(getattr(instance,'ip',''))))
        if ip!=address:metadata['resolved_ip']=ip
    except ValueError:pass
    if search is not None:
        metadata['collection_scope']={key:str(getattr(search,key,'') or '') for key in ('network','country','keyword')}
        if search.created_on:metadata['query_created_at']=search.created_on.isoformat()
    for field in _SAFE_FIELDS:
        value=getattr(instance,field,None)
        if value not in (None,''):metadata[field]=value
    return {'address':address,'port':port,'scheme':scheme,'transport':entry.get('transport','tcp'),
            'service_type':name,'title':str(getattr(instance,'title','') or getattr(instance,'name','') or entry.get('label') or name)[:500],
            'source_id':'legacy:'+reference,'metadata':metadata,'provider':provider}


@transaction.atomic
def bridge_legacy(instance):
    row=legacy_row(instance)
    if row is None:return None
    provider=row.pop('provider')
    from .workbench import _hidden
    hidden=_hidden(instance,LEGACY_MODELS[type(instance)])
    row['hidden']=hidden
    endpoint=ingest_observation(provider,row)
    reference=row['metadata']['legacy_reference']
    set_tags('endpoint',endpoint.pk,tag_names('finding',reference),origin='finding:'+reference)
    _set_visibility(reference,hidden)
    return endpoint


def _set_visibility(reference,hidden):
    rows=ServiceObservation.objects.filter(source_id='legacy:'+reference)
    endpoints=list(rows.values_list('endpoint_id',flat=True).distinct())
    rows.update(hidden=hidden)
    for endpoint_id in endpoints:
        refresh_endpoint_projection(endpoint_id)
        set_tags('endpoint',endpoint_id,[] if hidden else tag_names('finding',reference),origin='finding:'+reference)


@transaction.atomic
def sync_legacy_visibility(instance):
    reference=LEGACY_MODELS.get(type(instance))
    if reference is None:return
    reference+=f':{instance.pk}'
    if not ServiceObservation.objects.filter(source_id='legacy:'+reference).exists():
        bridge_legacy(instance)
        return
    from .workbench import _hidden
    _set_visibility(reference,_hidden(instance,LEGACY_MODELS[type(instance)]))


@receiver(post_save,dispatch_uid='leakscope_legacy_observation_bridge')
def normalize_legacy(sender,instance,raw=False,update_fields=None,**kwargs):
    # Exact runtime classes exclude Run/Search and historical migration models.
    if raw or sender not in LEGACY_MODELS or not getattr(settings,'LEAKSCOPE_NORMALIZE_LEGACY',True):return
    try:
        # A savepoint prevents an indexing DB error from poisoning an enclosing
        # transaction that owns the original finding write.
        with transaction.atomic():
            if update_fields and set(update_fields)<={'confirmed','for_later'}:sync_legacy_visibility(instance)
            else:bridge_legacy(instance)
    except Exception as exc:
        log.warning('Legacy observation indexing failed (%s); original finding retained',type(exc).__name__)


@receiver(post_save,sender=EvidenceArtifact,dispatch_uid='leakscope_artifact_correlation')
def correlate_artifact(sender,instance,created=False,raw=False,update_fields=None,**kwargs):
    if raw or sender is not EvidenceArtifact:return
    if update_fields is not None and set(update_fields)<={'metadata'}:return
    try:
        with transaction.atomic():index_artifact(instance)
    except Exception as exc:
        log.warning('Artifact correlation indexing failed (%s); original artifact retained',type(exc).__name__)
    if created and instance.kind in {'http','httpx','response','http_response'}:
        # Capture jobs may bind observation_pk after creating the file record.
        # Reload only after their transaction commits so visibility uses that
        # final proof. Extraction is synchronous, bounded, and entirely local.
        identifier=instance.pk
        def index_response():
            try:
                from leakscope.phase3.local_correlations import index_captured_response
                index_captured_response(identifier)
            except Exception as exc:
                log.warning('Local response signal indexing failed (%s); original artifact retained',type(exc).__name__)
        transaction.on_commit(index_response)


def _sync_review(instance):
    model=model_for_type(instance.type)
    if model is None:return
    row=model.objects.filter(pk=instance.object_id).first()
    if row is not None:sync_legacy_visibility(row)


@receiver(post_save,sender=FindingReview,dispatch_uid='leakscope_review_projection_save')
@receiver(post_delete,sender=FindingReview,dispatch_uid='leakscope_review_projection_delete')
def project_review(sender,instance,raw=False,**kwargs):
    if raw or sender is not FindingReview or not getattr(settings,'LEAKSCOPE_NORMALIZE_LEGACY',True):return
    try:
        with transaction.atomic():_sync_review(instance)
    except Exception as exc:log.warning('Legacy review projection failed (%s)',type(exc).__name__)


@receiver(pre_save,sender=FingerprintLog,dispatch_uid='leakscope_fingerprint_previous_scope')
def previous_fingerprint_scope(sender,instance,raw=False,**kwargs):
    if raw or sender is not FingerprintLog or not getattr(settings,'LEAKSCOPE_NORMALIZE_LEGACY',True):return
    instance._leakscope_previous_scope=FingerprintLog.objects.filter(pk=instance.pk).values('type','ip','port','status').first() if instance.pk else None


def _sync_fingerprint(scope):
    if not scope:return
    model=model_for_type(scope['type'])
    if model is None or not any(field.name=='ip' for field in model._meta.fields):return
    host=normalize_host(scope['ip']);port=normalize_port(scope['port'])
    for row in model.objects.filter(ip=host).iterator(chunk_size=100):
        if normalize_port(getattr(row,'port',''))==port:sync_legacy_visibility(row)


@receiver(post_save,sender=FingerprintLog,dispatch_uid='leakscope_fingerprint_projection_save')
@receiver(post_delete,sender=FingerprintLog,dispatch_uid='leakscope_fingerprint_projection_delete')
def project_fingerprint(sender,instance,raw=False,**kwargs):
    if raw or sender is not FingerprintLog or not getattr(settings,'LEAKSCOPE_NORMALIZE_LEGACY',True):return
    previous=getattr(instance,'_leakscope_previous_scope',None) if kwargs.get('signal') is not post_delete else None
    current={'type':instance.type,'ip':instance.ip,'port':instance.port,'status':instance.status}
    if previous==current or (current['status']!='hidden' and (not previous or previous['status']!='hidden')):return
    try:
        with transaction.atomic():
            _sync_fingerprint(previous)
            _sync_fingerprint(current)
    except Exception as exc:log.warning('Legacy fingerprint projection failed (%s)',type(exc).__name__)


@receiver(post_delete,dispatch_uid='leakscope_deleted_legacy_projection')
def hide_deleted_legacy(sender,instance,**kwargs):
    if sender not in LEGACY_MODELS or not getattr(settings,'LEAKSCOPE_NORMALIZE_LEGACY',True):return
    try:
        with transaction.atomic():_set_visibility(f'{LEGACY_MODELS[sender]}:{instance.pk}',True)
    except Exception as exc:log.warning('Deleted legacy source projection failed (%s)',type(exc).__name__)


@receiver(post_save,sender=EvidenceArtifact,dispatch_uid='leakscope_gallery_thumbnail_created')
def queue_capture_thumbnail(sender,instance,created=False,raw=False,**kwargs):
    if raw or not created or instance.kind!='screenshot' or not getattr(settings,'LEAKSCOPE_GALLERY_AUTO_THUMBNAILS',False):return
    identifier=str(instance.pk)
    def dispatch():
        try:
            from leakscope.phase3.gallery_jobs import queue_index
            queue_index({'limit':1,'artifact_ids':[identifier]})
        except Exception as exc:
            log.warning('Local screenshot thumbnail dispatch failed (%s); original retained',type(exc).__name__)
    transaction.on_commit(dispatch)
