"""Technology dashboard derived from visible retained observations only."""
from collections import defaultdict
from functools import lru_cache
import json
from django.db.models import Subquery,Q
from django.http import JsonResponse
from leakscope.phase3.catalog import catalog,CATEGORY_LABELS
from leakscope.phase3.local_correlations import visible_artifacts
from leakscope.services.run_events import sanitize
from .models import ServiceObservation
from .workbench import endpoint
from .atlas_shared import filtered_endpoints,time_filter

MAX_OBSERVATIONS=10000


@lru_cache(maxsize=1)
def identity_catalog():
    lookup={}
    for item in catalog():
        lookup[item['key'].casefold()]=item
        lookup[item['label'].casefold()]=item
    return lookup


def classify_levels(data,provider):
    """Technology signals in one observation, each with its attribution level.

    A placeholder such as a provider's "auto" module is never a technology, and
    a label a collection step merely carried is a candidate, not its finding.
    """
    from leakscope.phase3.attribution import observation_level,QUALITY,LIVE_PREFIXES,is_true
    from leakscope.phase3.protocol_hints import is_placeholder,protocol_hint
    data=data if isinstance(data,dict) else {}
    metadata=data.get('metadata')
    metadata=metadata if isinstance(metadata,dict) else {}
    verdict=observation_level(provider,data)
    requested_only=verdict['level']=='candidate' and not verdict['carried']
    lookup=identity_catalog()
    aliases={'elasticsearch':'elastic','mongodb':'mongo','postgres':'postgresql','siemens s7':'s7','modbus-tcp':'modbus'}
    matches=metadata.get('signature_matches',[])
    matches=matches if isinstance(matches,list) else []
    signature_keys={m.get('technology') for m in matches if isinstance(m,dict) and isinstance(m.get('technology'),str)}
    if requested_only:
        unverified={'key':'unverified','label':'Unverified requested identity','category':'unknown','quality':'unverified','level':'candidate','basis':verdict['basis']}
        if len(signature_keys)!=1:return [unverified]
        key=next(iter(signature_keys));entry=lookup.get(key,{})
        return [unverified,{'key':key,'label':entry.get('label',key),'category':entry.get('category','other'),'quality':'signature','level':'signature_matched','basis':'Observed evidence independently matched a saved signature; the requested search label stays unverified.'}]
    candidates=[]
    service=data.get('service_type','')
    if isinstance(service,str) and not is_placeholder(service):candidates.append((service,verdict))
    elif not str(provider).startswith(LIVE_PREFIXES+('scan:',)):
        hint=protocol_hint(data,provider)
        if hint and hint['strength']=='strong':
            candidates.append((hint['service'],{'level':'provider_reported','basis':hint['basis_label']+'; not confirmed live.'}))
    if len(signature_keys)==1:
        candidates.append((next(iter(signature_keys)),{'level':'signature_matched','basis':'Saved fingerprint rule matched; experimental and can be imitated.'}))
    if is_true(metadata.get('cardano_protocol_confirmed')):
        candidates.append(('cardano',{'level':'protocol_confirmed','basis':'Live Ouroboros version query confirmed the protocol; implementation is separate.'}))
    for field in ['product','service','app']:
        value=metadata.get(field)
        if isinstance(value,str) and not is_placeholder(value):candidates.append((value,verdict))
    http=metadata.get('http')
    if isinstance(http,dict) and isinstance(http.get('components'),dict):candidates.extend((name,verdict) for name in list(http['components'])[:30])
    values=[];seen=set()
    for candidate,basis in candidates:
        if not isinstance(candidate,str) or not candidate.strip() or len(candidate)>100 or not candidate.isprintable() or '<' in candidate or '>' in candidate:continue
        candidate=candidate.strip()
        key=aliases.get(candidate.casefold(),candidate.casefold());entry=lookup.get(key)
        if entry:
            key,label,category=entry['key'],entry['label'],entry['category']
        else:
            label=candidate.upper() if candidate.casefold() in {'http','https','ssh','grpc','ftp','vnc','rtsp','smtp','imap'} else candidate
            category='other'
        if key in seen:continue
        seen.add(key);values.append({'key':key,'label':label,'category':category,'quality':QUALITY[basis['level']],'level':basis['level'],'basis':basis['basis']})
    if values:return values
    return [{'key':'unknown','label':'Unknown identity','category':'unknown','quality':'unknown','level':None,'basis':'No saved record names the service.'}]


def classify(data,provider):
    """(key,label,category,quality) tuples; see classify_levels for the level."""
    return [(item['key'],item['label'],item['category'],item['quality']) for item in classify_levels(data,provider)]


def identity_rows(queryset,limit=10000,per_endpoint=None):
    from django.db.models.fields.json import KT
    from django.db.models.functions import Substr,RowNumber
    from django.db.models import Window,F
    fields={'atlas_type':('data__service_type',100),'atlas_basis':('data__metadata__service_type_basis',180),
        'atlas_product':('data__metadata__product',256),'atlas_service':('data__metadata__service',256),'atlas_app':('data__metadata__app',256),
        'atlas_components':('data__metadata__http__components',4096),'atlas_signature_matches':('data__metadata__signature_matches',16000),'atlas_signature_status':('data__metadata__signature_status',20),
        # What separates a reported label from a live confirmation, and the
        # opening of a provider's saved response text.
        'atlas_identity_verified':('data__metadata__identity_verified',8),'atlas_protocol_verified':('data__metadata__protocol_verified',8),
        'atlas_protocol_confirmed':('data__metadata__protocol_confirmed',8),'atlas_cardano_protocol_confirmed':('data__metadata__cardano_protocol_confirmed',8),
        'atlas_implementation_verified':('data__metadata__implementation_verified',8),'atlas_status':('data__metadata__status',8),
        'atlas_data':('data__metadata__data',300),'atlas_banner':('data__metadata__banner',300)}
    rows=queryset.annotate(**{name:Substr(KT(path),1,length) for name,(path,length) in fields.items()})
    if per_endpoint:rows=rows.annotate(atlas_row=Window(expression=RowNumber(),partition_by=[F('endpoint_id')],order_by=[F('last_collected_at').desc(),F('pk').desc()])).filter(atlas_row__lte=per_endpoint)
    rows=rows.order_by('-last_collected_at','-pk').values('id','endpoint_id','provider',*fields)[:limit]
    for row in rows.iterator(chunk_size=256):
        metadata={name:row['atlas_'+name] for name in ['product','service','app','identity_verified','protocol_verified','protocol_confirmed',
            'cardano_protocol_confirmed','implementation_verified','status','data','banner'] if row['atlas_'+name]}
        metadata['service_type_basis']=row['atlas_basis'] or ''
        metadata['signature_status']=row['atlas_signature_status'] or ''
        try:metadata['signature_matches']=json.loads(row['atlas_signature_matches'] or '[]')
        except (ValueError,TypeError):metadata['signature_matches']=[]
        try:components=json.loads(row['atlas_components'] or '{}')
        except (ValueError,TypeError):components={}
        if isinstance(components,dict):metadata['http']={'components':{key:True for key in list(components)[:30]}}
        yield {'id':row['id'],'endpoint_id':row['endpoint_id'],'provider':row['provider'],'data':{'service_type':row['atlas_type'] or '', 'metadata':metadata}}


def merge_signal(values,item):
    """Keep one signal per technology, at the highest level any record supports."""
    from leakscope.phase3.attribution import RANK
    current=values.get(item['key'])
    if current is None:
        if len(values)<20:values[item['key']]=dict(item)
    elif RANK.get(item['level'],-1)>RANK.get(current['level'],-1):values[item['key']]=dict(item)


def endpoint_technologies(endpoints,limit=10000):
    rows=identity_rows(ServiceObservation.objects.filter(endpoint_id__in=Subquery(endpoints.values('pk')),hidden=False).exclude(provider='certstream'),limit=limit,per_endpoint=20)
    result={}
    for row in rows:
        values=result.setdefault(str(row['endpoint_id']),{})
        for item in classify_levels(row['data'],row['provider']):merge_signal(values,item)
    return {key:list(values.values()) for key,values in result.items()}


def evidence_legend():
    """The words every surface uses for levels, capture states and image classes."""
    from leakscope.phase3.attribution import LEVELS,LABELS,MEANINGS
    from leakscope.phase3.capture_status import STATES,STATE_LABELS,IMAGE_CLASSES,IMAGE_NOTES
    return {'levels':[{'key':key,'label':LABELS[key],'meaning':MEANINGS[key]} for key in LEVELS],
        'captures':[{'key':key,'label':STATE_LABELS[key]} for key in STATES],
        'images':[{'key':key,'label':IMAGE_CLASSES[key],'note':IMAGE_NOTES[key]} for key in IMAGE_CLASSES]}


def attribution_filter(value):
    from leakscope.phase3.attribution import LEVELS
    value=str(value or '').strip()
    if value and value not in LEVELS:raise ValueError('Unknown evidence level')
    return value


@endpoint()
def technologies(request):
    from collections import Counter
    from leakscope.phase3.attribution import LEVELS,RANK,QUALITY,LIVE_PREFIXES,best
    from leakscope.phase3.capture_status import batch_status,capture_class,STATES,IMAGE_CLASSES,IMAGE_KINDS
    from leakscope.phase3.protocol_hints import is_placeholder,protocol_hint
    endpoints,coverage=filtered_endpoints(request.GET)
    observations=ServiceObservation.objects.filter(endpoint_id__in=Subquery(endpoints.values('pk')),hidden=False).exclude(provider='certstream')
    provider=request.GET.get('provider')
    if provider:observations=observations.filter(Q(provider__startswith='fetch:')|Q(provider__startswith='native:')) if provider=='direct' else observations.filter(provider=provider)
    since=time_filter(request.GET.get('since'),'since');until=time_filter(request.GET.get('until'),'until')
    if since:observations=observations.filter(last_collected_at__gte=since)
    if until:observations=observations.filter(first_collected_at__lte=until)
    wanted=attribution_filter(request.GET.get('attribution'))
    total=observations.count();rows=list(identity_rows(observations,limit=MAX_OBSERVATIONS))
    groups={};hinted={};headline=defaultdict(list);confirmed={}
    for row in rows:
        data=row['data']
        if is_placeholder(data['service_type']) and not row['provider'].startswith(LIVE_PREFIXES+('scan:','analysis:')):
            hint=protocol_hint(data,row['provider'])
            if hint and hint['strength']=='strong':hinted.setdefault(str(row['endpoint_id']),hint['service'])
        for item in classify_levels(data,row['provider']):
            if item['key']!='unknown':headline[row['endpoint_id']].append(item['level'])
            # A saved live exchange is a protocol capture even when its collector kept no separate file.
            if RANK.get(item['level'],-1)>=RANK['protocol_confirmed'] and row['provider'].startswith(LIVE_PREFIXES):confirmed.setdefault(str(row['endpoint_id']),True)
            if request.GET.get('category') and item['category']!=request.GET['category']:continue
            group=groups.setdefault(item['key'],{'key':item['key'],'label':item['label'],'category':item['category'],'members':{}})
            current=group['members'].get(row['endpoint_id'],False)
            if current is False or RANK.get(item['level'],-1)>RANK.get(current,-1):group['members'][row['endpoint_id']]=item['level']
    status,runs_truncated=batch_status(endpoints,hinted,confirmed)
    images=visible_artifacts(endpoints=endpoints).filter(kind__in=IMAGE_KINDS)
    if since:images=images.filter(created_at__gte=since)
    if until:images=images.filter(created_at__lte=until)
    # Newest image per endpoint; a screenshot or framebuffer is preferred to a drawn document.
    previews={}
    for artifact in images.values('pk','endpoint_id','kind','tool','metadata__protocol','metadata__adapter').order_by('-created_at')[:4000]:
        group=capture_class(artifact['kind'],artifact['tool'],{'protocol':artifact['metadata__protocol'],'adapter':artifact['metadata__adapter']})
        current=previews.get(artifact['endpoint_id'])
        if current is None or (current[1]=='rendered' and group!='rendered'):previews[artifact['endpoint_id']]=(str(artifact['pk']),group)
    category_members=defaultdict(set);category_tech=defaultdict(set);result=[];unknown=set();unverified=set()
    for key,group in groups.items():
        members=group.pop('members')
        if wanted:members={identifier:level for identifier,level in members.items() if level==wanted}
        if not members:continue
        if key=='unknown':unknown.update(members)
        if key=='unverified':unverified.update(members)
        # "none" counts endpoints no saved record names; they are shown, not dropped.
        levels={level:0 for level in LEVELS};levels['none']=0;captures={state:0 for state in STATES};classes={name:0 for name in IMAGE_CLASSES};reasons=Counter()
        for identifier,level in members.items():
            levels[level or 'none']+=1
            info=status.get(str(identifier))
            if not info:continue
            captures[info['state']]+=1
            for name in classes:classes[name]+=info['images'][name]
            if info['state'] not in ('succeeded','partial'):reasons[(info['state'],info['reason'])]+=1
        qualities={QUALITY.get(level,'unknown') for level in members.values()}
        group.update(endpoints=len(members),screenshots=classes['browser']+classes['framebuffer'],levels=levels,level=best(members.values()),
            quality=next(iter(qualities)) if len(qualities)==1 else 'mixed',captures=captures,images=classes,filters={'technology':key})
        if reasons:
            (state,reason),count=reasons.most_common(1)[0]
            group['capture_note']={'state':state,'reason':reason,'endpoints':count}
        identifier=next((previews[member] for member in members if member in previews and previews[member][1]!='rendered'),None) or next((previews[member] for member in members if member in previews),None)
        if identifier:group['preview']={'id':identifier[0],'capture_class':identifier[1],'thumbnail_url':'/api/atlas/screenshots/'+identifier[0]+'/thumbnail',
            'original_url':'/api/operations/artifacts/'+identifier[0],'title':group['label']+' saved capture'}
        category_members[group['category']].update(members);category_tech[group['category']].add(key);result.append(group)
    result.sort(key=lambda row:(-row['endpoints'],row['label'].casefold()))
    scoped={str(row['endpoint_id']) for row in rows}
    level_totals={level:0 for level in LEVELS};level_totals['none']=0
    for identifier in {row['endpoint_id'] for row in rows}:level_totals[best(headline.get(identifier,[])) or 'none']+=1
    capture_totals={state:0 for state in STATES};image_totals={name:0 for name in IMAGE_CLASSES}
    for key,info in status.items():
        if key in scoped:
            capture_totals[info['state']]+=1
            for name in image_totals:image_totals[name]+=info['images'][name]
    output={'totals':{'endpoints':endpoints.count(),'observations':total,'screenshots':image_totals['browser']+image_totals['framebuffer'],'technologies':len(result),
            'unknown':len(unknown),'unverified':len(unverified),'levels':level_totals,'captures':capture_totals,'images':image_totals},'groups':result[:200],
        'categories':[{'key':key,'label':CATEGORY_LABELS.get(key,key.title()),'endpoints':len(members),'technologies':len(category_tech[key])} for key,members in sorted(category_members.items())],
        'truncated':total>MAX_OBSERVATIONS or len(result)>200 or coverage.get('scope_truncated',False) or runs_truncated,'coverage':coverage,'sampled_observations':len(rows),
        'legend':evidence_legend(),
        'basis':'Visible retained identity evidence only. Each endpoint is counted at the highest level its saved records support for that technology: discovery candidate, provider reported, live protocol confirmed or implementation verified. A port, keyword, tag or organization match is never an identity or ownership claim. Capture states come from saved capture jobs and files. An endpoint can have several technologies, so group totals overlap. No provider searches or target requests.'}
    return JsonResponse(sanitize(output,preserve_metrics=True))
