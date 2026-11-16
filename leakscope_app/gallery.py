"""Bounded saved-screenshot gallery and explicit shared curation mutations."""
from collections import defaultdict
from io import BytesIO
import re
from uuid import UUID
from django.core import signing
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.db.models import Count,Exists,OuterRef,Q,Subquery,Value,CharField,Window,F
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Lower,Replace,RowNumber
from django.http import FileResponse,JsonResponse
from django.utils import timezone
from leakscope.phase3.local_correlations import visible_artifacts,_ids
from leakscope.phase3.gallery import thumbnail_metadata,read_thumbnail,capture_metadata
from leakscope.phase3.capture_status import IMAGE_KINDS,IMAGE_CLASSES,IMAGE_NOTES,capture_class
from leakscope.phase3.state import visible_endpoints
from leakscope.services.run_events import sanitize
from leakscope.services.runs import serialize_run
from .models import (EvidenceArtifact,ServiceEndpoint,ServiceObservation,Tag,TagAssignment,ScreenshotCollection,
    ScreenshotMembership,ScreenshotCuration,AssetNode,AssetRelationship)
from .workbench import endpoint,_body
from .workspace import _time

SALT='leakscope.gallery.v1'
FILTERS={'q','technology','provider','tag','tag_scope','organization_id','country','continent','collection','since','until','latest','duplicates','group','sort','direction','attribution','capture_class'}
RAW_KINDS=('wire-transcript','wire-request','wire-response','identity','protocol-query')
SORTS={'created_at':'created_at','endpoint':'endpoint__address','technology':'endpoint__service_type','size':'size'}


def _number(value,default,maximum,minimum=1):
    if value is None:return default
    if isinstance(value,bool):raise ValueError('Invalid limit')
    try:result=int(value)
    except (TypeError,ValueError):raise ValueError('Invalid limit') from None
    if not minimum<=result<=maximum:raise ValueError('Limit outside supported range')
    return result


def _text(value,maximum=120):
    if not isinstance(value,str) or not value.strip() or len(value)>maximum or not value.isprintable() or '<' in value or '>' in value:raise ValueError('Use bounded printable text')
    return value.strip()


def _filters(request):
    if set(request.GET)-FILTERS-{'cursor','limit'}:raise ValueError('Unsupported gallery filter')
    values={key:request.GET.get(key,'') for key in FILTERS}
    if any(len(value)>512 for value in values.values()):raise ValueError('Gallery filter too long')
    values['sort']=values['sort'] or 'created_at';values['direction']=values['direction'] or 'desc'
    values['duplicates']=values['duplicates'] or 'all';values['group']=values['group'] or 'none';values['latest']=values['latest'] or '0'
    values['tag_scope']=values['tag_scope'] or 'any'
    if values['capture_class'] and values['capture_class'] not in IMAGE_CLASSES and values['capture_class']!='captured':raise ValueError('Unknown image class')
    if values['sort'] not in SORTS or values['direction'] not in {'asc','desc'} or values['duplicates'] not in {'all','collapse'} or values['group'] not in {'none','technology','organization','country'} or values['latest'] not in {'0','1'} or values['tag_scope'] not in {'any','endpoint'}:raise ValueError('Invalid gallery mode')
    cursor=request.GET.get('cursor');state=None
    if cursor:
        if len(cursor)>8192:raise ValueError('Invalid gallery cursor')
        try:state=signing.loads(cursor,salt=SALT,max_age=86400)
        except signing.BadSignature:raise ValueError('Invalid or expired gallery cursor') from None
        if not isinstance(state,dict) or not isinstance(state.get('filters'),dict):raise ValueError('Invalid gallery cursor')
        for key in FILTERS:
            if key in request.GET and values[key]!=state['filters'].get(key):raise ValueError('Cursor filters cannot change')
        values=state['filters']
    return values,state


def _endpoints(values):
    from .atlas_shared import filtered_endpoints
    return filtered_endpoints({key:value for key,value in values.items() if key not in {'tag','tag_scope','since','until','collection','latest','duplicates','group','sort','direction','capture_class'}})


def _images(rows,group=''):
    """Saved images of the three classes; the class is never inferred from looks."""
    framebuffer=Q(kind='screenshot',metadata__protocol='rfb')
    if group=='rendered':return rows.filter(kind='evidence-render')
    if group=='captured':return rows.filter(kind='screenshot')  # pictures a service displayed: browser and framebuffer
    if group=='framebuffer':return rows.filter(framebuffer)
    # Exclude by saved ID: a missing JSON key must not drop an ordinary screenshot.
    if group=='browser':return rows.filter(kind='screenshot').exclude(pk__in=EvidenceArtifact.objects.filter(framebuffer).values('pk'))
    return rows.filter(kind__in=IMAGE_KINDS)


def _raw_files(endpoint_ids):
    """Raw responses and wire files saved beside a drawn image, by source observation."""
    result=defaultdict(list)
    for row in EvidenceArtifact.objects.filter(endpoint_id__in=endpoint_ids,kind__in=RAW_KINDS).order_by('kind').values('pk','endpoint_id','kind','mime','size','metadata__observation_pk')[:2000]:
        link=str(row['metadata__observation_pk'] or '').replace('-','').lower()
        if link:result[(str(row['endpoint_id']),link)].append({'id':str(row['pk']),'kind':row['kind'],'mime':row['mime'],'size':row['size'],'url':'/api/operations/artifacts/'+str(row['pk'])})
    return result


def _locations(endpoints):
    from leakscope.phase3.geography import endpoint_locations
    return endpoint_locations(endpoints)


def _collection(row,count=None):
    return {'id':str(row.pk),'name':row.name,'color':row.color,'position':row.position,
        'count':row.visible_count if count is None else count,'created_at':row.created_at.isoformat(),'updated_at':row.updated_at.isoformat()}


def _organizations(ids):
    if not ids:return {}
    from leakscope.phase3.relationships import visible_evidence
    keys={UUID(str(value)).hex:str(value) for value in ids}
    services=AssetNode.objects.filter(kind='service').annotate(endpoint_key=Lower(Replace(KeyTextTransform('endpoint_id','attributes'),Value('-'),Value(''),output_field=CharField()))).filter(endpoint_key__in=list(keys))
    service_keys={row.pk:keys[row.endpoint_key] for row in services}
    links=AssetRelationship.objects.filter(target_id__in=service_keys,relation='user_scope',source__kind='organization',evidence_id__in=Subquery(visible_evidence().values('pk'))).exclude(source__organization_status__in=['merged','deleted']).select_related('source')
    result=defaultdict(dict)
    for link in links:result[service_keys[link.target_id]][str(link.source_id)]={'id':str(link.source_id),'name':link.source.label or link.source.value,'status':link.source.organization_status}
    return {key:sorted(value.values(),key=lambda row:(row['name'],row['id'])) for key,value in result.items()}


@endpoint()
def screenshots(request):
    filters,cursor=_filters(request);limit=_number(request.GET.get('limit'),48,100)
    endpoints,coverage=_endpoints(filters)
    rows=_images(visible_artifacts(endpoints))
    until=_time(cursor['window_until']) if cursor else (_time(filters['until']) if filters['until'] else timezone.now())
    rows=rows.filter(created_at__lte=until)
    if filters['since']:
        since=_time(filters['since'])
        if since>until:raise ValueError('Since must be before until')
        rows=rows.filter(created_at__gte=since)
    if filters['collection']:rows=rows.filter(collection_memberships__collection_id=UUID(filters['collection']))
    if filters['tag']:
        assignments=TagAssignment.objects.filter(tag__name=filters['tag'].casefold())
        # UUID comparisons use text so both PostgreSQL and SQLite identities match.
        artifact_tags=assignments.filter(target_kind='artifact').annotate(tag_key=Lower(Replace('target_key',Value('-'),Value('')))).values('tag_key')
        endpoint_tags=assignments.filter(target_kind='endpoint').annotate(tag_key=Lower(Replace('target_key',Value('-'),Value('')))).values('tag_key')
        from django.db.models.functions import Cast
        rows=rows.annotate(artifact_tag_key=Lower(Replace(Cast('pk',CharField()),Value('-'),Value(''))),endpoint_tag_key=Lower(Replace(Cast('endpoint_id',CharField()),Value('-'),Value(''))))
        tag_match=Q(endpoint_tag_key__in=Subquery(endpoint_tags))
        if filters.get('tag_scope','any')=='any':tag_match|=Q(artifact_tag_key__in=Subquery(artifact_tags))
        rows=rows.filter(tag_match)
    # How many images of each class match the other filters, for one-click switching.
    class_counts=None
    if not cursor:
        tally=rows.aggregate(rendered=Count('pk',filter=Q(kind='evidence-render'),distinct=True),screens=Count('pk',filter=Q(kind='screenshot'),distinct=True),
            framebuffer=Count('pk',filter=Q(kind='screenshot',metadata__protocol='rfb'),distinct=True))
        class_counts={'browser':tally['screens']-tally['framebuffer'],'framebuffer':tally['framebuffer'],'rendered':tally['rendered']}
    rows=_images(rows,filters.get('capture_class',''))
    rows=rows.distinct();total=rows.count()
    if filters['latest']=='1':
        newer=rows.filter(endpoint_id=OuterRef('endpoint_id')).filter(Q(created_at__gt=OuterRef('created_at'))|Q(created_at=OuterRef('created_at'),pk__gt=OuterRef('pk')))
        rows=rows.annotate(newer_exists=Exists(newer)).filter(newer_exists=False)
    if filters['duplicates']=='collapse':
        newer=rows.filter(sha256=OuterRef('sha256')).filter(Q(created_at__gt=OuterRef('created_at'))|Q(created_at=OuterRef('created_at'),pk__gt=OuterRef('pk')))
        rows=rows.annotate(duplicate_newer=Exists(newer)).filter(duplicate_newer=False)
    count=rows.count();field=SORTS[filters['sort']];descending=filters['direction']=='desc'
    if cursor:
        last=cursor['last_value'];last=_time(last) if field=='created_at' else int(last) if field=='size' else last
        comparison='lt' if descending else 'gt'
        rows=rows.filter(Q(**{field+'__'+comparison:last})|Q(**{field:last,'pk__'+comparison:UUID(cursor['last_id'])}))
    order='-' if descending else ''
    selected=list(rows.select_related('endpoint','curation').order_by(order+field,order+'pk')[:limit+1]);more=len(selected)>limit;selected=selected[:limit]
    endpoint_map={str(row.endpoint_id):row.endpoint for row in selected}
    fields=('id','endpoint_id','provider','data__service_type','data__metadata__service_type_basis')
    proof_rows=ServiceObservation.objects.filter(endpoint_id__in=endpoint_map,hidden=False)
    observations=list(proof_rows.annotate(preview_row=Window(expression=RowNumber(),partition_by=[F('endpoint_id')],order_by=[F('last_collected_at').desc(),F('pk').desc()])).filter(preview_row__lte=20).values(*fields))
    source_ids=[]
    for row in selected:
        try:
            value=capture_metadata(row).get('observation_pk')
            if value:source_ids.append(UUID(str(value)))
        except ValueError:pass
    if source_ids:observations+=list(proof_rows.filter(pk__in=source_ids).values(*fields))
    by_id={str(row['id']):row for row in observations};by_endpoint=defaultdict(list)
    for row in by_id.values():by_endpoint[str(row['endpoint_id'])].append(row)
    tag_map=defaultdict(list)
    for row in TagAssignment.objects.filter(Q(target_kind='artifact',target_key__in=[str(item.pk) for item in selected])|Q(target_kind='endpoint',target_key__in=endpoint_map)).select_related('tag'):
        kind='artifact_inherited' if row.target_kind=='artifact' and row.origin!='manual' else row.target_kind
        tag_map[(kind,row.target_key)].append(row.tag.name)
    collection_map=defaultdict(list)
    for membership in ScreenshotMembership.objects.filter(artifact_id__in=[row.pk for row in selected]).select_related('collection').order_by('collection__position','collection__name'):
        collection_map[membership.artifact_id].append({'id':str(membership.collection_id),'name':membership.collection.name,'color':membership.collection.color})
    organizations=_organizations(list(endpoint_map));locations=_locations(list(endpoint_map.values())) if selected else {}
    from .atlas import endpoint_technologies
    technologies=endpoint_technologies(ServiceEndpoint.objects.filter(pk__in=endpoint_map),limit=2000) if selected else {}
    from leakscope.phase3.capture_status import batch_status
    confirmed={key:True for key,signals in technologies.items() if any(item.get('level') in ('protocol_confirmed','implementation_verified') for item in signals)}
    captures,_=batch_status(ServiceEndpoint.objects.filter(pk__in=endpoint_map),confirmed=confirmed) if selected else ({},False)
    raw_files=_raw_files(list(endpoint_map)) if any(row.kind=='evidence-render' for row in selected) else {}
    output=[]
    for row in selected:
        key=str(row.endpoint_id);proof=None
        source=capture_metadata(row).get('observation_pk')
        try:proof=by_id.get(str(UUID(str(source)))) if source else None
        except ValueError:pass
        if proof is None:proof=next(iter(by_endpoint[key]),None)
        technology=(proof or {}).get('data__service_type') or row.endpoint.service_type or 'unknown'
        if not isinstance(technology,str):technology='unknown'
        signals=technologies.get(key,[])
        ordered=sorted(signals,key=lambda item:(item['key'] in {'unknown','unverified','http','https'},item['category'] in {'other','unknown'}))
        primary=next(iter(ordered),None)
        if primary:technology=primary['key']
        technology_basis=primary['quality'] if primary else 'unknown'
        thumb=thumbnail_metadata(row);manual=sorted(set(tag_map[('artifact',str(row.pk))]));inherited=sorted(set(tag_map[('endpoint',key)]+tag_map[('artifact_inherited',str(row.pk))]))
        geo=locations.get(key,{}) or {};location=geo.get('location') or {};country=location.get('country_code') or location.get('country') or 'unknown'
        groups={'none':('all','All screenshots'),'technology':(technology,technology),'country':(country,location.get('country_name') or ('Unknown location' if country=='unknown' else country)),'organization':('unassigned','Unassigned')}
        orgs=organizations.get(key,[])
        if orgs:groups['organization']=(orgs[0]['id'],orgs[0]['name'])
        group_key,group_label=groups[filters['group']]
        details=capture_metadata(row);image_class=capture_class(row.kind,row.tool,details) or 'browser'
        render=details.get('render') if isinstance(details.get('render'),dict) else {}
        evidence=None
        if image_class=='rendered':
            link=str(details.get('observation_pk') or '').replace('-','').lower()
            evidence={name:render.get(name) for name in ('state','state_label','headline','description','operation','service','source_kind','source_provider','source_observed_at','source_collected_at','source_id','rendered_at')}
            evidence.update(raw_files=raw_files.get((key,link),[]),source_observation_id=link if render.get('source_kind')!='wire_transcript' else None)
        output.append({'capture_class':image_class,'capture_label':IMAGE_CLASSES[image_class],'capture_note':IMAGE_NOTES[image_class],'evidence':evidence,
            'capture':captures.get(key),'partial':bool(details.get('partial')),
            'id':str(row.pk),'endpoint_id':key,'reference':{'type':'endpoint','id':key},'address':row.endpoint.address,'port':row.endpoint.port,
            'title':row.endpoint.title,'technology':technology,'technology_basis':technology_basis,'technologies':signals,'provider':proof['provider'] if proof else 'unknown','providers':sorted({item['provider'] for item in by_endpoint[key]}),
            'country':country,'location_ip':geo.get('ip'),'organizations':orgs,'created_at':row.created_at.isoformat(),'mime':row.mime,'size':row.size,'sha256':row.sha256,
            'original_url':'/api/operations/artifacts/'+str(row.pk),'thumbnail_url':'/api/atlas/screenshots/'+str(row.pk)+'/thumbnail' if thumb else None,
            'thumbnail_status':'ready' if thumb else 'pending','file_status':'not_checked','width':thumb.get('width') if thumb else None,'height':thumb.get('height') if thumb else None,
            'tags':sorted(set(manual+inherited)),'manual_tags':manual,'inherited_tags':inherited,'pinned':getattr(getattr(row,'curation',None),'pinned',False),
            'collections':collection_map[row.pk],'group':{'key':group_key,'label':group_label}})
    next_cursor=None
    if more and selected:
        last=selected[-1];value=last.created_at.isoformat() if field=='created_at' else last.size if field=='size' else last.endpoint.address if field=='endpoint__address' else last.endpoint.service_type
        next_cursor=signing.dumps({'filters':filters,'window_until':until.isoformat(),'last_value':value,'last_id':str(last.pk)},salt=SALT,compress=True)
    return JsonResponse(sanitize({'results':output,'count':count,'total_visible_count':total,'next_cursor':next_cursor,'has_more':more,'limit':limit,
        'coverage':coverage,'location_basis':'Country grouping uses current local GeoIP for the latest retained known IP of each endpoint; it is not a historical capture-location snapshot.','sort_basis':('Stored endpoint service type; canonical product labels are derived separately.' if filters['sort']=='technology' else 'Stored artifact creation time.' if filters['sort']=='created_at' else 'Stored endpoint address.' if filters['sort']=='endpoint' else 'Stored artifact byte size.'),'class_counts':class_counts,'legend':{'images':[{'key':name,'label':IMAGE_CLASSES[name],'note':IMAGE_NOTES[name]} for name in IMAGE_CLASSES]},'basis':'Retained visible images only: browser screenshots, native framebuffer captures and evidence images drawn from saved response bytes, each labelled as what it is. Originals and derivative availability are checked when opened. Manual capture tags and inherited endpoint tags remain separate. Country uses local GeoIP only. Counts reflect the selected filtered scope and history/duplicate modes; endpoint metadata can change while browsing.'},preserve_metrics=True))


@endpoint(('GET','POST'))
def collections(request):
    if request.method=='GET':
        visible=visible_artifacts().filter(kind__in=IMAGE_KINDS).values('pk')
        rows=ScreenshotCollection.objects.annotate(visible_count=Count('memberships',filter=Q(memberships__artifact_id__in=Subquery(visible))))
        total=rows.count()
        return JsonResponse(sanitize({'results':[_collection(row) for row in rows[:300]],'count':total,'truncated':total>300},preserve_metrics=True))
    data=_body(request)
    if data.keys()-{'id','name','color','position'}:raise ValueError('Unsupported collection field')
    created='id' not in data;row=ScreenshotCollection() if created else ScreenshotCollection.objects.get(pk=UUID(str(data['id'])))
    row.name=_text(data.get('name',row.name));color=data.get('color',row.color)
    if not isinstance(color,str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',color):raise ValueError('Use a six-digit hex color')
    row.color=color.lower();row.position=_number(data.get('position'),row.position,1000000,minimum=0)
    safe=sanitize({'name':row.name})
    if safe['name']!=row.name:raise ValueError('Remove credentials from collection name')
    if created and ScreenshotCollection.objects.count()>=300:raise ValueError('At most300 collections are supported')
    row.save()
    return JsonResponse({'collection':_collection(row,count=0 if created else row.memberships.filter(artifact_id__in=Subquery(visible_artifacts().filter(kind__in=IMAGE_KINDS).values('pk'))).count()),'created':created},status=201 if created else 200)


@endpoint(('POST',))
def delete_collection(request,collection_id):
    if _body(request):raise ValueError('Collection deletion takes no options')
    row=ScreenshotCollection.objects.get(pk=collection_id);key=str(row.pk);row.delete()
    return JsonResponse({'deleted':key,'originals_deleted':0})


def _tag_names(values):
    if not isinstance(values,list) or len(values)>30:raise ValueError('Choose at most30 tags')
    if any(not isinstance(value,str) or not 1<=len(value.strip())<=64 or not value.isprintable() for value in values):raise ValueError('Tags must be1–64 printable characters')
    return sorted(set(value.strip().casefold() for value in values))


@endpoint(('POST',))
@transaction.atomic
def curate(request):
    data=_body(request)
    if data.keys()-{'artifact_ids','tags','pinned','add_collections','remove_collections'} or not data.keys()-{'artifact_ids'}:raise ValueError('Supply supported curation changes')
    ids=_ids(data.get('artifact_ids'))
    if not 1<=len(ids)<=500:raise ValueError('Choose1–500 saved screenshots')
    rows=list(EvidenceArtifact.objects.select_for_update().filter(pk__in=ids,kind__in=IMAGE_KINDS))
    visible=set(str(value) for value in visible_artifacts().filter(pk__in=ids,kind__in=IMAGE_KINDS).values_list('pk',flat=True))
    if visible!=set(ids):raise ValueError('One or more screenshots is unavailable or hidden; nothing changed')
    if 'pinned' in data and not isinstance(data['pinned'],bool):raise ValueError('Keep must be a boolean')
    tags=_tag_names(data['tags']) if 'tags' in data else None
    add=_ids(data.get('add_collections',[]));remove=_ids(data.get('remove_collections',[]))
    if len(add)+len(remove)>60 or set(add)&set(remove):raise ValueError('Invalid collection changes')
    collection_ids=set(add+remove)
    if set(str(value) for value in ScreenshotCollection.objects.select_for_update().filter(pk__in=collection_ids).values_list('pk',flat=True))!=collection_ids:raise ValueError('Collection unavailable')
    for row in rows:
        if 'pinned' in data:ScreenshotCuration.objects.update_or_create(artifact=row,defaults={'pinned':data['pinned']})
        if tags is not None:
            assigned=TagAssignment.objects.filter(target_kind='artifact',target_key=str(row.pk),origin='manual');assigned.exclude(tag__name__in=tags).delete()
            for name in tags:
                tag,_=Tag.objects.get_or_create(name=name);TagAssignment.objects.get_or_create(tag=tag,target_kind='artifact',target_key=str(row.pk),origin='manual')
        if remove:ScreenshotMembership.objects.filter(artifact=row,collection_id__in=remove).delete()
        for key in add:ScreenshotMembership.objects.get_or_create(artifact=row,collection_id=key)
    return JsonResponse({'updated':len(rows),'artifact_ids':ids,'originals_modified':0})


@endpoint()
def thumbnail(request,artifact_id):
    row=visible_artifacts().get(pk=artifact_id,kind__in=IMAGE_KINDS)
    try:data=read_thumbnail(row)
    except (OSError,ValueError):raise ObjectDoesNotExist('Thumbnail unavailable; index retained captures first') from None
    response=FileResponse(BytesIO(data),content_type='image/png');response['Cache-Control']='private, no-store';return response


@endpoint(('POST',))
def render_evidence(request):
    """Queue local drawing of evidence images from saved records; no network contact."""
    from leakscope.phase3.evidence_jobs import queue_render
    row=queue_render(_body(request))
    return JsonResponse(sanitize({'run':serialize_run(row,include_activity=False)},preserve_metrics=True),status=202)


@endpoint(('POST',))
def index(request):
    from leakscope.phase3.gallery_jobs import queue_index
    row=queue_index(_body(request))
    return JsonResponse(sanitize({'run':serialize_run(row,include_activity=False)},preserve_metrics=True),status=202)
