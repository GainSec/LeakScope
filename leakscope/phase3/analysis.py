"""Deterministic change history and indexed, evidence-backed correlations."""
import hashlib
import json
import re
from datetime import timedelta

from django.db.models import Count, Q, F, Window, Exists, OuterRef, Subquery
from django.db.models.functions import TruncDate, RowNumber
from django.utils import timezone

from leakscope_app.models import (ServiceEndpoint, ServiceObservation, EvidenceArtifact, SavedLookup,
    WatchRule, ObservationChange, EvidenceCorrelation, TagAssignment)
from .catalog import catalog

_ROOT_VOLATILE = {'@timestamp','timestamp','update_time','last_updated','last_updated_at','last_seen',
    'scan_date','observed_at','collected_at','collection_scope','lookup_id','matched_profile','profile_matches',
    'legacy_review','query_created_at','_provider','reported_log','cert_index','update_type',
    'duration_ms','collection_mode','timings','timing','response_time','elapsed','time_total','time_connect','time_starttransfer','artifacts'}
_SECRET = re.compile(r'password|passwd|secret|token|api.?key|authorization|cookie|credential|context|indicator',re.I)
_MISSING=object()


def stable_evidence(data):
    """Ignore collector provenance/timing, never arbitrary nested business timestamps."""
    if isinstance(data, dict) and (data.get('metadata') or {}).get('service_type_basis') == 'Requested search profile; identity not verified':
        # A requested label is useful for choosing a probe, but it is not a
        # response fact. Retain it in the observation while comparing bytes.
        data = dict(data, service_type='')
    def clean(value,path=()):
        if isinstance(value,dict):
            result={}
            for key,item in value.items():
                lowered=key.casefold()
                if path==('metadata','exchanges') and key=='response_hex' and isinstance(item,str) and len(item)>=16 and (data.get('metadata') or {}).get('protocol')=='cardano':
                    # First four bytes are the peer's monotonic mux timestamp.
                    # Original bytes stay in the observation/artifacts; compare only stable framing and payload.
                    item='00000000'+item[8:]
                if path==('metadata',) and lowered in _ROOT_VOLATILE:continue
                if path==('metadata',) and lowered=='_geo' and (any(isinstance(value.get(name),dict) for name in ('location','geo','geoinfo')) or any(name in value for name in ('country.name','country.code','lat','lon'))):continue
                if path==('metadata','_geo') and lowered in {'source_date','collected_at','observed_at','last_observed_at'}:continue
                if path==('metadata','wire_summary') and lowered in {'events_sent','events_received'}:continue
                if path==('metadata','_shodan') and lowered!='module':continue
                if len(path)>=2 and path[-1].casefold() in {'headers','unknown_headers'} and lowered in {'date','set-cookie','set_cookie'}:continue
                normalized=clean(item,path+(key,))
                if path==('metadata',) and key=='_shodan' and not normalized:continue
                result[key]=normalized
            return result
        if isinstance(value,list):
            result=[clean(item,path) for item in value]
            if path and path[-1].casefold() in {'technologies','tech','domains','hostnames','all_domains'} and all(isinstance(x,str) for x in result):
                return sorted(set(result))
            return result
        if path and path[-1].casefold() in {'sha256','sha1','fingerprint','fingerprint_sha256','fingerprint_sha1','hash_sha256'} and isinstance(value,str):
            normalized=value.lower().replace(':','').strip()
            if re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}',normalized):return normalized
        if path and (path[-1]=='iconhash' or path[-2:]==('favicon','hash')):
            try:
                number=int(value)
                if not isinstance(value,bool) and str(number)==str(value) and -(2**31)<=number<2**31:return number
            except (TypeError,ValueError):pass
        if isinstance(value,str) and path==('metadata','data') and value.startswith('HTTP/'):
            header,separator,body=value.partition('\r\n\r\n')
            header='\r\n'.join(line for line in header.split('\r\n') if not re.match(r'(?i)^(date|set-cookie):',line))
            return header+separator+body
        return value
    return clean(data)


def _display(value,path):
    if value is _MISSING:return '[not reported]'
    if _SECRET.search(path):return '[masked]'
    if isinstance(value,(dict,list)):
        digest=hashlib.sha256(json.dumps(value,sort_keys=True,default=str).encode()).hexdigest()[:12]
        return f'[{type(value).__name__}: {len(value)} items; sha256 {digest}]'
    if isinstance(value,str):
        if len(value)>160 or path.rsplit('.',1)[-1].casefold() in {'body','data','html','content','banner'}:
            return f'[text: {len(value)} chars; sha256 {hashlib.sha256(value.encode()).hexdigest()[:12]}]'
        return re.sub(r'(?i)(?:bearer\s+|(?:password|secret|token|api[_-]?key)\s*[:=]\s*)[^\s,;]+','[masked]',value)
    return value


def summarize_changes(before,after):
    result=[]
    def compare(old,new,path='',depth=0):
        if old==new or len(result)>=20:return
        if isinstance(old,dict) and isinstance(new,dict) and depth<6:
            for key in sorted(old.keys()|new.keys()):
                compare(old.get(key,_MISSING),new.get(key,_MISSING),(path+'.'+key).lstrip('.'),depth+1)
                if len(result)>=20:break
        else:
            result.append({'field':path[:300],'old':_display(old,path),'new':_display(new,path)})
    compare(before,after)
    return result


def _path(data,parts):
    for part in parts:
        if not isinstance(data,dict):return None
        data=data.get(part)
    return data


def _public_response_signals(metadata,provider):
    """Only retained, known provider HTTP fields; never scan arbitrary metadata."""
    if provider not in {'shodan','zoomeye','netlas'} or not isinstance(metadata,dict):return []
    from .local_correlations import RESPONSE_LIMIT, response_signals
    paths=[('http','html'),('http','body'),('http','response','body')]
    envelopes={'shodan':('data',),'zoomeye':('banner','header','data')}.get(provider,())
    remaining=RESPONSE_LIMIT
    result=set();seen=set()
    for path in paths+[(field,) for field in envelopes]:
        value=_path(metadata,path)
        if not isinstance(value,str) or not value or len(value)>remaining:continue
        if len(path)==1 and not re.match(r'^HTTP/(?:1\.[01]|2(?:\.0)?|3(?:\.0)?) [1-5][0-9]{2}(?:[ \r\n]|$)',value[:100]):continue
        try:data=value.encode('utf-8')
        except UnicodeError:continue
        if len(data)>remaining or data in seen:continue
        remaining-=len(data);seen.add(data)
        result.update(response_signals(data))
        if len(result)>=64 or not remaining:break
    return sorted(result)[:64]


def correlation_signals(metadata,provider):
    result=set()
    def add(kind,value,length):
        if not isinstance(value,str):return
        value=value.lower().replace(':','').strip()
        if re.fullmatch('[0-9a-f]{'+str(length)+'}',value):result.add((kind,value))
    for path in (('certificate','sha256'),('certificate','fingerprint_sha256'),('certificate','fingerprint','sha256'),
                 ('ssl','cert','sha256'),('ssl','cert','fingerprint','sha256'),('leaf_cert','sha256')):
        add('certificate.sha256',_path(metadata,path),64)
    for path in (('certificate','sha1'),('certificate','fingerprint_sha1'),('certificate','fingerprint','sha1'),
                 ('ssl','cert','fingerprint','sha1')):
        add('certificate.sha1',_path(metadata,path),40)
    if provider in {'shodan','zoomeye','certstream'}:
        for path in (('certificate','fingerprint'),('ssl','cert','fingerprint'),('leaf_cert','fingerprint')):
            add('certificate.sha1',_path(metadata,path),40)
    for path in (('http','favicon','hash_sha256'),('http','favicon','sha256'),('favicon','sha256')):
        add('favicon.sha256',_path(metadata,path),64)
    if provider=='netlas':add('favicon.ahash64',_path(metadata,('http','favicon','perceptual_hash')),16)
    for path in (('http','favicon','hash'),('favicon','mmh3'),('iconhash',)):
        value=_path(metadata,path)
        if isinstance(value,bool):continue
        try:
            number=int(value)
            if str(number)==str(value) and -(2**31)<=number<2**31:result.add(('favicon.mmh3',str(number)))
        except (TypeError,ValueError):pass
    for path in (('body_sha256',),('http','body_sha256')):
        add('http.body.sha256',_path(metadata,path),64)
    add('tls.jarm',metadata.get('jarm') if isinstance(metadata,dict) else None,62)
    return sorted(result)[:16]+_public_response_signals(metadata,provider)


def index_observation(observation,now=None,metadata=None):
    now=now or timezone.now()
    if metadata is None:metadata=observation.data.get('metadata',{})
    for kind,value in correlation_signals(metadata,observation.provider):
        correlation,_=EvidenceCorrelation.objects.update_or_create(endpoint_id=observation.endpoint_id,kind=kind,value=value,
            defaults={'observation':observation,'last_seen':now})
        correlation.observations.add(observation)


def index_artifact(artifact):
    digest=str(artifact.sha256 or '').lower()
    if artifact.size<1 or not re.fullmatch('[0-9a-f]{64}',digest):return
    kind=re.sub('[^a-z0-9_-]','',artifact.kind.lower())[:20]
    if not kind:return
    EvidenceCorrelation.objects.update_or_create(endpoint_id=artifact.endpoint_id,kind=f'artifact.{kind}.sha256',value=digest,
        defaults={'artifact':artifact,'last_seen':timezone.now()})


def build_analysis():
    from .state import visible_endpoints
    endpoints_visible=visible_endpoints()
    observations=ServiceObservation.objects.filter(hidden=False)
    by_provider=[{'label':row['provider'],'count':row['count']} for row in observations.values('provider').annotate(count=Count('pk')).order_by('-count','provider')]
    type_counts=list(endpoints_visible.values('service_type').annotate(count=Count('pk')).order_by('-count','service_type'))
    entries={entry['key']:entry for entry in catalog()}
    categories={}
    by_service=[]
    for row in type_counts:
        entry=entries.get(row['service_type'],{})
        by_service.append({'label':entry.get('label') or row['service_type'] or 'Unclassified','count':row['count']})
        category=entry.get('category','other');categories[category]=categories.get(category,0)+row['count']
    from django.db.models.functions import Cast, Replace, Lower
    from django.db.models import CharField, Value
    visible_keys=endpoints_visible.annotate(string_id=Lower(Replace(Cast('pk',output_field=CharField()),Value('-'),Value('')))).values('string_id')
    tags=TagAssignment.objects.filter(target_kind='endpoint').annotate(normalized_key=Lower(Replace('target_key',Value('-'),Value('')))).filter(normalized_key__in=visible_keys)
    by_tag=[{'label':row['tag__name'],'count':row['count']} for row in tags.values('tag__name').annotate(count=Count('target_key',distinct=True)).order_by('-count','tag__name')[:50]]
    timeline=observations.filter(first_collected_at__gte=timezone.now()-timedelta(days=30)).annotate(day=TruncDate('first_collected_at')).values('day').annotate(count=Count('pk')).order_by('day')
    recent=[]
    changes=ObservationChange.objects.filter(observation__hidden=False).filter(Q(previous__isnull=True)|Q(previous__hidden=False))
    for change in changes.select_related('endpoint').order_by('-collected_at','-pk')[:30]:
        # Re-apply display masking for older persisted summaries as well.
        summary=[{'field':str(item.get('field',''))[:300],
                  'old':_display(item.get('old'),str(item.get('field',''))),
                  'new':_display(item.get('new'),str(item.get('field','')))} for item in change.summary[:20] if isinstance(item,dict)]
        recent.append({'endpoint_id':str(change.endpoint_id),'address':change.endpoint.address,
            'service_type':change.endpoint.service_type,'collected_at':change.collected_at.isoformat(),
            'provider':change.provider,'summary':summary,'observation_id':str(change.observation_id),
            'previous_observation_id':str(change.previous_id) if change.previous_id else None})
    proof=ServiceObservation.objects.filter(correlation_signals=OuterRef('pk'),hidden=False).order_by('-last_collected_at','-pk')
    candidates=EvidenceCorrelation.objects.filter(endpoint__in=endpoints_visible).annotate(
        has_visible_proof=Exists(proof),visible_observation_id=Subquery(proof.values('pk')[:1])).filter(Q(has_visible_proof=True)|Q(artifact__isnull=False))
    groups=list(candidates.values('kind','value').annotate(count=Count('endpoint_id',distinct=True)).filter(count__gte=2).order_by('-count','kind','value')[:50])
    pairs=Q(pk__in=[])
    for group in groups:pairs|=Q(kind=group['kind'],value=group['value'])
    endpoints={}
    if groups:
        bounded=candidates.filter(pairs).annotate(group_row=Window(expression=RowNumber(),
            partition_by=[F('kind'),F('value')],order_by=F('endpoint_id').asc())).filter(group_row__lte=50)
        for row in bounded.values('kind','value','endpoint_id','endpoint__address','endpoint__port','visible_observation_id','artifact_id'):
            key=(row['kind'],row['value'])
            endpoints.setdefault(key,[]).append({'id':str(row['endpoint_id']),'address':row['endpoint__address'],'port':row['endpoint__port'],
                'observation_id':str(row['visible_observation_id']) if row['visible_observation_id'] else None,
                'artifact_id':str(row['artifact_id']) if row['artifact_id'] else None})
    relations=[dict(group,endpoints=endpoints.get((group['kind'],group['value']),[])[:50],
                    meaning='Shared observed evidence; this does not establish common ownership') for group in groups]
    return {'counts':{'endpoints':endpoints_visible.count(),'observations':observations.count(),
                     'artifacts':EvidenceArtifact.objects.filter(endpoint__in=endpoints_visible).count(),'lookups':SavedLookup.objects.count(),
                     'watches':WatchRule.objects.count(),'changes':changes.count()},
        'by_provider':by_provider,'by_service':by_service,
        'by_category':[{'label':key,'count':value} for key,value in sorted(categories.items())],
        'by_tag':by_tag,'timeline':[{'date':row['day'].isoformat(),'count':row['count']} for row in timeline],
        'recent_changes':recent,'relations':relations}
