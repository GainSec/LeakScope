"""Read-only saved ICS evidence and explicit capture/discovery actions."""
import math
from uuid import UUID
from django.db.models import Q,Subquery,CharField,Value,Exists,OuterRef
from django.db.models.functions import Cast,Replace
from django.db.models.fields.json import KeyTextTransform
from django.http import JsonResponse

from leakscope_app.models import EvidenceArtifact,ServiceObservation,TagAssignment
from leakscope.phase3.state import visible_endpoints,tag_names
from leakscope.phase3.catalog import catalog
from leakscope.phase3.asset_names import normalize_ip
from leakscope.phase3 import ics_jobs as jobs
from leakscope.services.runs import serialize_run
from .workbench import endpoint,_body,_number
from .operations import _endpoint,_integer,_mask


def _row(value):
    return visible_endpoints().get(pk=UUID(str(value)))


def _gallery_rows():
    hidden=ServiceObservation.objects.filter(hidden=True).annotate(key=Replace(Cast('pk',CharField()),Value('-'),Value(''))).values('key')
    legacy_hidden=ServiceObservation.objects.filter(endpoint_id=OuterRef('endpoint_id'),hidden=True,provider='fetch:screenshot')
    return EvidenceArtifact.objects.filter(endpoint_id__in=Subquery(visible_endpoints().values('pk')),kind='screenshot',mime__in=['image/png','image/jpeg']).annotate(
        source_observation=KeyTextTransform('observation_pk','metadata'),hidden_legacy=Exists(legacy_hidden)).filter(
            Q(source_observation__isnull=True)|~Q(source_observation__in=Subquery(hidden))).exclude(
            source_observation__isnull=True,hidden_legacy=True).select_related('endpoint')


@endpoint()
def gallery(request):
    rows=_gallery_rows()
    query=request.GET.get('q','')[:300]
    service=request.GET.get('service_type','')[:100]
    provider=request.GET.get('provider','')[:40]
    if query:rows=rows.filter(Q(endpoint__address__icontains=query)|Q(endpoint__title__icontains=query)|Q(endpoint__service_type__icontains=query))
    if service:
        rows=rows.filter(Q(endpoint__service_type=service)|Q(endpoint__observations__data__metadata__matched_profile=service,endpoint__observations__hidden=False)|
            Q(endpoint__observations__data__metadata__hmi_profiles__has_key=service,endpoint__observations__hidden=False)).distinct()
    if provider:rows=rows.filter(endpoint__observations__provider=provider,endpoint__observations__hidden=False).distinct()
    page=_number(request.GET.get('page'),1,1,100000);size=_number(request.GET.get('page_size'),24,1,100)
    count=rows.count();results=[]
    for item in rows[(page-1)*size:page*size]:
        results.append({'id':str(item.pk),'endpoint':_endpoint(item.endpoint),'image_url':'/api/operations/artifacts/'+str(item.pk),
            'created_at':item.created_at.isoformat(),'kind':item.kind,'tool':item.tool,'sha256':item.sha256,'metadata':_mask(item.metadata)})
    return JsonResponse({'results':results,'count':count,'page':page,'pages':max(1,math.ceil(count/size)),
        'filters':{'service_types':[{'key':item['key'],'label':item['label']} for item in catalog() if item['category']=='ot'],
                   'providers':list(ServiceObservation.objects.filter(hidden=False).values_list('provider',flat=True).distinct().order_by('provider')[:50])}})


@endpoint()
def context(request):
    root=_row(request.GET.get('endpoint'))
    try:ip=jobs.host_ip(root)
    except ValueError:ip=None
    related=[]
    if ip:
        rows=visible_endpoints().filter(Q(address=ip)|Q(observations__data__metadata__resolved_ip=ip,observations__hidden=False)|
            Q(observations__data__metadata__related_host=ip,observations__hidden=False)).exclude(pk=root.pk).distinct()[:100]
        related=[_endpoint(row) for row in rows]
    latest=root.observations.filter(provider='fetch:ot',hidden=False).first()
    inspection=None
    if latest:
        artifact=root.artifacts.filter(kind='ot-inspection',metadata__observation_pk=latest.pk.hex).first()
        inspection={'metadata':_mask(latest.data.get('metadata') or {}),'created_at':latest.last_collected_at.isoformat(),
                    'artifact_id':str(artifact.pk) if artifact else None}
    return JsonResponse({'endpoint':_endpoint(root),'host_ip':ip,'related':related,'supported_inspection':root.service_type in {'modbus','bacnet'},
        'latest_inspection':inspection,'inspection_schema':{'modbus':{'unit_id':[1,255],'functions':[1,2,3,4],'max_blocks':8,'max_values':256},
            'bacnet':{'max_objects':8,'max_requests':32}},'collection_modes':['passive','demo','semi_passive','screenshot','full']})


@endpoint(('POST',))
def related(request):
    data=_body(request);root=_row(data.get('endpoint_id'))
    provider=data.get('provider','shodan')
    maximum=_integer(data,'max_results',100,1,100)
    from leakscope.phase3.usage_policy import validate_demo
    mode='demo' if validate_demo(data.get('demo',False)) else data.get('collection_mode','passive')
    run=jobs.queue_related(root,provider,mode,1 if mode=='demo' else maximum)
    return JsonResponse({'run':serialize_run(run)})


@endpoint(('POST',))
def inspect(request):
    from leakscope.phase3.ot_inspection import validate_inspection
    data=_body(request);root=_row(data.get('endpoint_id'))
    options=validate_inspection(root.service_type,data.get('options'))
    if data.get('preview') is True:return JsonResponse({'endpoint':_endpoint(root),'options':options,'read_only':True})
    run=jobs.queue_inspection(root,options)
    return JsonResponse({'run':serialize_run(run)})


@endpoint(('POST',))
def scan(request):
    from leakscope.phase3.tcp_inventory import validate_scan_options
    data=_body(request);root=_row(data.get('endpoint_id'))
    if data.get('allow_active_scan') is not True:raise ValueError('Enable the explicit active TCP inventory checkbox')
    options=validate_scan_options({key:data[key] for key in ('ports','rate_per_second','max_seconds','timeout_seconds') if key in data})
    run=jobs.queue_scan(root,options)
    return JsonResponse({'run':serialize_run(run)})


@endpoint(('POST',))
def web_probe(request):
    data=_body(request);root=_row(data.get('endpoint_id'))
    if not isinstance(data.get('tls'),bool):raise ValueError('Select HTTP or HTTPS')
    return JsonResponse({'run':serialize_run(jobs.queue_web_probe(root,data['tls']))})
