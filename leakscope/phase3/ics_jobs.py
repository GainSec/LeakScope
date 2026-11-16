"""Explicit indexed expansion, read-only point inspection and TCP inventory."""
import hashlib
import ipaddress
import json
from pathlib import Path
from uuid import UUID
from datetime import timedelta

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from leakscope.services.config import get_config,redact
from leakscope.services.runs import enqueue
from leakscope.shared import is_blacklisted
from leakscope_app.models import ServiceObservation,ServiceEndpoint,EvidenceArtifact,Run,ExecutionLease,AssetEvidence
from .state import visible_endpoints,ingest_observation,set_tags,tag_names,acquire_lease,_json_safe
from .asset_names import normalize_ip
from .jobs import _claim_execution,queue_capture,capture_root,endpoint_snapshot
from .providers import _request,_normalize,search_provider,_provider
from .collection_policy import validate_collection_mode,plan_collection
from .hmi_profiles import match_hmi
from .usage_policy import validate_demo,demo_metadata


def host_ip(endpoint):
    try:return normalize_ip(endpoint.address)
    except ValueError:pass
    for observation in endpoint.observations.filter(hidden=False).only('data')[:30]:
        metadata=observation.data.get('metadata') or {}
        for key in ('resolved_ip','ip','ip_str'):
            try:return normalize_ip(metadata.get(key))
            except ValueError:pass
    proofs=AssetEvidence.objects.filter(subject__kind='domain',subject__value=endpoint.address,source='dns',expires_at__gt=timezone.now()).order_by('-observed_at')[:20]
    for proof in proofs:
        if proof.data.get('type') not in {'A','AAAA'}:continue
        try:return normalize_ip(proof.data.get('value'))
        except ValueError:pass
    cached=AssetEvidence.objects.filter(subject__kind='domain',subject__value=endpoint.address,source='enrichment:dns',expires_at__gt=timezone.now()).order_by('-last_collected_at').first()
    if cached:
        for record in (cached.data.get('result',{}).get('records') or [])[:64]:
            if record.get('type') not in {'A','AAAA'} or record.get('query_name',record.get('name'))!=endpoint.address:continue
            try:return normalize_ip(record.get('value'))
            except ValueError:pass
    raise ValueError('No observed IP is saved for this result; resolve names explicitly first')


def indexed_host(provider,address,config,max_results=100,*,demo=False):
    demo=validate_demo(demo)
    if demo:max_results=1
    address=normalize_ip(address)
    if provider=='shodan':
        payload=_request('shodan','/shodan/host/'+address,config)
        rows=payload.get('data')
        if not isinstance(rows,list):raise ValueError('Shodan returned no indexed host service list')
        observations=[]
        for raw in rows[:max_results]:
            if not isinstance(raw,dict):continue
            item=_normalize('shodan',dict(raw,ip_str=address),config)
            if item:observations.append(item)
        return {'observations':observations,'partial':len(rows)>max_results,'errors':[],'requests':1}
    query='ip="'+address+'"' if provider=='zoomeye' else 'ip:"'+address+'"'
    return search_provider(provider,query,config,page_count=1,page_size=min(100,max_results),**({'demo':True} if demo else {}))


def _cancelled(task):
    return Run.objects.filter(task_id=getattr(task.request,'id',None),state='cancelled').exists()


def _eligible(task,endpoint):
    return not _cancelled(task) and visible_endpoints().filter(pk=endpoint.pk).exists()


def _discard(result):
    root=capture_root().resolve()
    for item in (result.get('artifacts') or [])[:16]:
        try:
            path=Path(item['path']).resolve()
            if path.is_relative_to(root) and path.is_file() and not EvidenceArtifact.objects.filter(relative_path=str(path.relative_to(root))).exists():path.unlink(missing_ok=True)
        except (KeyError,TypeError,OSError):pass


def _queue(task,kind,endpoint,parameters):
    marker=hashlib.sha256(json.dumps([kind,str(endpoint.pk),parameters],sort_keys=True).encode()).hexdigest()
    if not acquire_lease('ics-queue:'+marker,60):raise ValueError('Equivalent action was queued recently; check Runs')
    return enqueue(task,kind=kind,type=endpoint.service_type,provider=parameters.get('provider','native'),parameters=parameters,kwargs=parameters)


def queue_related(endpoint,provider,collection_mode='passive',max_results=100):
    _provider(provider,get_config()['config'])
    if collection_mode=='demo':max_results=1
    return _queue(related_services,'related_services',endpoint,{'endpoint_id':str(endpoint.pk),'provider':provider,
        'collection_mode':validate_collection_mode(collection_mode),'max_results':max_results,'target_ip':host_ip(endpoint)})


def queue_inspection(endpoint,options):
    from .ot_inspection import validate_inspection
    spec=validate_inspection(endpoint.service_type,options)
    return _queue(inspect_points,'ot_inspection',endpoint,{'endpoint_id':str(endpoint.pk),'options':spec,'snapshot':endpoint_snapshot(endpoint)})


def queue_scan(endpoint,options):
    from .tcp_inventory import validate_scan_options
    return _queue(inventory_ports,'port_inventory',endpoint,{'endpoint_id':str(endpoint.pk),'options':validate_scan_options(options),'target_ip':host_ip(endpoint)})


def _row_host(row):
    metadata=row.get('metadata') or {}
    for value in [row.get('address'),metadata.get('resolved_ip'),metadata.get('ip'),metadata.get('ip_str')]:
        try:return normalize_ip(value)
        except ValueError:pass
    return None


@shared_task(bind=True,name='leakscope.ics.related',soft_time_limit=180,time_limit=200)
def related_services(self,endpoint_id,provider,collection_mode='passive',max_results=100,target_ip=None):
    _claim_execution(self,'ics-related')
    parent=visible_endpoints().get(pk=endpoint_id)
    address=normalize_ip(target_ip) if target_ip else host_ip(parent)
    mode=validate_collection_mode(collection_mode)
    if isinstance(max_results,bool) or not isinstance(max_results,int) or not 1<=max_results<=100:raise ValueError('Related service limit must be1–100')
    cfg=get_config()['config']
    if is_blacklisted(address,{'config':cfg}):raise ValueError('Related target is blacklisted')
    if _cancelled(self):return {'cancelled':True}
    demo=mode=='demo'
    if demo:max_results=1
    result=indexed_host(provider,address,cfg,max_results,**({'demo':True} if demo else {}))
    stored=[];capture_runs=[];outside=0;errors=list(result.get('errors') or [])[:20]
    rows=result.get('observations') or []
    for row in rows[:max_results]:
        if not _eligible(self,parent):return {'cancelled':True,'endpoint_ids':stored,'stored':len(stored),'reason':'Parent result hidden or run cancelled'}
        if _row_host(row)!=address:outside+=1;continue
        if is_blacklisted(row.get('address',''),{'config':cfg}):continue
        metadata=dict(row.get('metadata') or {},related_host=address,related_to_endpoint=str(parent.pk),
            source_basis='Indexed same-IP service; current reachability and common ownership are unverified')
        matches=match_hmi(row.get('title',''),json.dumps(metadata,ensure_ascii=False))
        if matches:metadata.update(hmi_matches=matches,hmi_profiles={m['key']:True for m in matches})
        try:
            endpoint=ingest_observation(provider,dict(row,metadata=metadata))
            set_tags('endpoint',endpoint.pk,tag_names('endpoint',parent.pk),origin='related:'+str(parent.pk))
            stored.append(str(endpoint.pk))
            from .relationships import _service,evidence,relation
            source=endpoint.observations.filter(provider=provider,hidden=False).order_by('-last_collected_at').first()
            proof=evidence(_service(parent),'indexed_host',{'provider':provider,'ip':address,'parent_endpoint':str(parent.pk),
                'endpoint_id':str(endpoint.pk),'endpoint_pk':parent.pk.hex,'observation_pk':source.pk.hex,
                'assertion':'Shared indexed IP; no ownership assertion'})
            relation(_service(parent),_service(endpoint),'same_ip_service',proof,70)
            plan=plan_collection(endpoint_snapshot(endpoint),mode)
            if plan['actions']:
                run=queue_capture(endpoint.pk,plan['actions'],origin='related:'+str(parent.pk),cooldown_seconds=3600,
                    max_daily=100,collection_mode=mode,skipped_actions=plan['skipped'])
                if run:capture_runs.append(str(run.pk))
        except (ValueError,TypeError) as exc:errors.append(redact(exc))
    return {'target_ip':address,'provider':provider,'collection_mode':mode,'endpoint_ids':stored,'stored':len(stored),
        'capture_run_ids':capture_runs,'outside_host':outside,'partial':bool(result.get('partial') or errors or len(rows)>max_results),
        'errors':errors[:30],'requests':result.get('requests',result.get('pages',1)),**(demo_metadata(max_requests=1,max_results=1,target_requests=0) if demo else {})}


def _persist_result(endpoint,result,provider,run,row_override=None):
    root=capture_root().resolve();prepared=[]
    try:
        for item in result.get('artifacts',[]):
            path=Path(item['path']).resolve()
            if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size>16*1024*1024:raise ValueError('Invalid inspection artifact')
            prepared.append((item,path,hashlib.sha256(path.read_bytes()).hexdigest()))
        if len(prepared)>16 or sum(p.stat().st_size for _,p,_ in prepared)>16*1024*1024:raise ValueError('Inspection artifacts exceed limit')
        metadata=_json_safe(result.get('metadata') or {})
        metadata['artifacts']=[{'kind':i['kind'],'sha256':digest} for i,p,digest in prepared]
        with transaction.atomic():
            ServiceEndpoint.objects.select_for_update().get(pk=endpoint.pk)
            if not visible_endpoints().filter(pk=endpoint.pk).exists() or (run and Run.objects.filter(pk=run.pk,state='cancelled').exists()):
                raise ValueError('Inspection source hidden or run cancelled before persistence')
            endpoint=ingest_observation(provider,dict(row_override or endpoint_snapshot(endpoint),metadata=metadata,observed_at=timezone.now()))
            observation=endpoint.observations.filter(provider=provider).order_by('-last_collected_at').first()
            ids=[]
            for item,path,digest in prepared:
                artifact=EvidenceArtifact.objects.create(endpoint=endpoint,run=run,kind=item['kind'],relative_path=str(path.relative_to(root)),
                    mime=item['mime'],size=path.stat().st_size,sha256=digest,tool=item.get('tool','native'),
                    metadata=dict(_json_safe(item.get('metadata') or {}),observation_pk=observation.pk.hex))
                ids.append(str(artifact.pk))
        return {'metadata':metadata,'artifact_ids':ids,'endpoint_id':str(endpoint.pk),'partial':bool(result.get('partial') or metadata.get('partial'))}
    except Exception:
        for _,path,_ in prepared:
            if not EvidenceArtifact.objects.filter(relative_path=str(path.relative_to(root))).exists():path.unlink(missing_ok=True)
        raise


@shared_task(bind=True,name='leakscope.ics.inspect',soft_time_limit=40,time_limit=50)
def inspect_points(self,endpoint_id,options,snapshot=None):
    _claim_execution(self,'ot-inspection')
    endpoint=visible_endpoints().get(pk=endpoint_id)
    if _cancelled(self):return {'cancelled':True}
    from .ot_inspection import inspect_endpoint
    result=inspect_endpoint(snapshot or endpoint_snapshot(endpoint),options,dict(get_config()['config'],CAPTURE_DIR=str(capture_root())))
    if not _eligible(self,endpoint):
        _discard(result)
        return {'cancelled':True,'reason':'Source hidden or run cancelled during inspection'}
    return _persist_result(endpoint,result,'fetch:ot',Run.objects.filter(task_id=getattr(self.request,'id',None)).first())


@shared_task(bind=True,name='leakscope.ics.scan',soft_time_limit=930,time_limit=950)
def inventory_ports(self,endpoint_id,options,target_ip=None):
    _claim_execution(self,'tcp-inventory')
    parent=visible_endpoints().get(pk=endpoint_id)
    address=normalize_ip(target_ip) if target_ip else host_ip(parent)
    cfg=get_config()['config']
    if is_blacklisted(address,{'config':cfg}):raise ValueError('Inventory target is blacklisted')
    from .tcp_inventory import scan_tcp
    lease='ics-scan-flight:'+address
    if not acquire_lease(lease,1000):raise ValueError('A TCP inventory of this IP is already running')
    try:result=scan_tcp(address,options,should_stop=lambda:not _eligible(self,parent))
    finally:ExecutionLease.objects.filter(pk=lease).delete()
    ids=[]
    if not _eligible(self,parent):return dict(result,cancelled=True,endpoint_ids=[],stored=0)
    for port in result.get('open_ports',[])[:256]:
        if is_blacklisted(address,{'config':cfg}):break
        endpoint=ingest_observation('scan:tcp',{'address':address,'port':port,'transport':'tcp','scheme':'','service_type':'',
            'observed_at':timezone.now(),'metadata':{'related_host':address,'related_to_endpoint':str(parent.pk),
                'connection_established':True,'operation':'TCP connect only; application protocol unverified'}})
        ids.append(str(endpoint.pk));set_tags('endpoint',endpoint.pk,tag_names('endpoint',parent.pk),origin='related:'+str(parent.pk))
    if len(result.get('open_ports',[]))>256:result.update(partial=True,storage_limited=True)
    return dict(result,endpoint_ids=ids,stored=len(ids))


def queue_web_probe(endpoint,tls):
    if not isinstance(tls,bool):raise ValueError('Select HTTP or HTTPS')
    if endpoint.transport!='tcp' or endpoint.service_type not in {'','unknown','unclassified','http','https'}:raise ValueError('HTTP probing requires an unclassified or HTTP TCP endpoint')
    return _queue(probe_web,'web_probe',endpoint,{'endpoint_id':str(endpoint.pk),'tls':tls,'snapshot':endpoint_snapshot(endpoint)})


@shared_task(bind=True,name='leakscope.ics.web_probe',soft_time_limit=45,time_limit=55)
def probe_web(self,endpoint_id,tls=False,snapshot=None):
    _claim_execution(self,'ics-web-probe')
    endpoint=visible_endpoints().get(pk=endpoint_id)
    row=dict(snapshot or endpoint_snapshot(endpoint),scheme='https' if tls else 'http',service_type='https' if tls else 'http')
    from .capture import capture_endpoint,CaptureEvidenceError
    try:result=capture_endpoint(row,'http',dict(get_config()['config'],CAPTURE_DIR=str(capture_root())))
    except CaptureEvidenceError as exc:result=exc.result
    relation_metadata={'related_to_endpoint':str(endpoint.pk)}
    try:relation_metadata['related_host']=host_ip(endpoint)
    except ValueError:pass
    result['metadata']=dict(result.get('metadata') or {},**relation_metadata)
    if not _eligible(self,endpoint):_discard(result);return {'cancelled':True}
    try:status=int((result.get('metadata') or {}).get('status') or 0)
    except (TypeError,ValueError):status=0
    if not 100<=status<=599:
        result['metadata']=dict(result.get('metadata') or {},http_verified=False,attempted_scheme=row['scheme'])
        result['partial']=True
        return _persist_result(endpoint,result,'fetch:web-probe',Run.objects.filter(task_id=getattr(self.request,'id',None)).first())
    result['metadata']=dict(result.get('metadata') or {},http_verified=True)
    return _persist_result(endpoint,result,'fetch:web-probe',Run.objects.filter(task_id=getattr(self.request,'id',None)).first(),row)
