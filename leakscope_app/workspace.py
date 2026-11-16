"""Bounded, read-only saved-evidence workflows and explicit shared view CRUD."""
import json
from datetime import timedelta
from urllib.parse import parse_qsl, urlencode
from uuid import UUID
from django.core import signing
from django.db.models import (BooleanField, Case, CharField, Count, Exists, F, IntegerField,
                              OuterRef, Q, Subquery, Value, When, Window)
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast, Coalesce, Replace, RowNumber
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from leakscope.phase3 import relationships as store
from leakscope.phase3 import organization_lifecycle as lifecycle
from leakscope.phase3.state import visible_endpoints
from leakscope.services.run_events import sanitize
from leakscope.services.runs import serialize_run
from .models import (WorkspaceView, ServiceObservation, ObservationChange, CertificateRecord,
                     AssetNode, EvidenceCorrelation, Run, DomainExpansion, DomainCandidate)
from .workbench import endpoint, _body

PAGES = {'assets','findings','organizations','cloud','certificates','relationships','domain-expansion','changes','correlations','technologies','screenshots','geography'}
QUERY_KEYS = {'q','search','provider','tag','tag_scope','type','service_type','technology','sort','direction','page','page_size','public',
              'size','limit','status','state','root','organization','organization_id','kind','since','until','view','run',
              'endpoint','id','transport','scheme','country','network','keyword','source','mode','watch','domain',
              'reference_type','reference_id','max_depth','max_nodes','max_edges','target','query_mode','similar','threshold','signal_limit','company','focused','category','collection','latest','duplicates','group','source','count_mode','map_mode','projection'}
PROTECTED_COLUMNS = {'actions','action','select','selection','checkbox','inspect','tools'}
CHANGE_KINDS = {'change','endpoint','domain','certificate'}
ACTIVE = {'queued','running','paused'}
CURSOR_SALT = 'leakscope.workspace.v1'


def _integer(value, default, maximum, *, minimum=1):
    if value is None: return default
    if isinstance(value, bool): raise ValueError('Invalid limit')
    try: result = int(value)
    except (ValueError, TypeError): raise ValueError('Invalid limit') from None
    if not minimum <= result <= maximum: raise ValueError(f'Value must be {minimum}–{maximum}')
    return result


def _text(value, maximum=120):
    if not isinstance(value,str) or not value.strip() or len(value)>maximum or not value.isprintable() or '<' in value or '>' in value:
        raise ValueError('Enter bounded printable text')
    return value.strip()


def _state(value):
    if not isinstance(value,dict) or value.keys()-{'query','tables','disclosures'}:
        raise ValueError('View state supports query, tables and disclosures only')
    try: encoded=json.dumps(value,ensure_ascii=False).encode()
    except (ValueError,TypeError,RecursionError): raise ValueError('Invalid view state') from None
    if len(encoded)>16384: raise ValueError('View state exceeds16KiB')
    query=value.get('query','')
    if not isinstance(query,str) or len(query)>4096 or '#' in query or any(ord(c)<32 for c in query): raise ValueError('Invalid query filters')
    query=query.removeprefix('?')
    pairs=parse_qsl(query,keep_blank_values=True,max_num_fields=60)
    if any(key not in QUERY_KEYS for key,_ in pairs): raise ValueError('Unsupported query filter; URLs and credentials cannot be saved')
    if any(key == 'public' and value not in {'0', '1'} for key, value in pairs):
        raise ValueError('Public identifier view must be 0 or 1')
    tables=value.get('tables',{});disclosures=value.get('disclosures',{})
    if not isinstance(tables,dict) or len(tables)>30 or not isinstance(disclosures,dict) or len(disclosures)>100:
        raise ValueError('Too many tables or disclosures')
    normalized={}
    for key,prefs in tables.items():
        _text(key,200)
        if not isinstance(prefs,dict) or prefs.keys()-{'density','hidden_columns','sort'}: raise ValueError('Invalid table preferences')
        density=prefs.get('density','comfortable');columns=prefs.get('hidden_columns',[])
        if density not in {'compact','comfortable'} or not isinstance(columns,list) or len(columns)>40: raise ValueError('Invalid density or column preferences')
        columns=[_text(column) for column in columns]
        if any(column.casefold() in PROTECTED_COLUMNS for column in columns): raise ValueError('Selection and action columns must remain visible')
        normalized[key]={'density':density,'hidden_columns':list(dict.fromkeys(columns))}
        if 'sort' in prefs:
            order=prefs['sort']
            if not isinstance(order,dict) or set(order)!={'key','direction'} or order['direction'] not in {'asc','desc'}: raise ValueError('Invalid table sort')
            normalized[key]['sort']={'key':_text(order['key']),'direction':order['direction']}
    for key,opened in disclosures.items():
        _text(key,200)
        if not isinstance(opened,bool): raise ValueError('Disclosure states must be booleans')
    query=urlencode(pairs)
    if len(query)>4096: raise ValueError('Encoded query exceeds4096characters')
    result={'query':query,'tables':normalized,'disclosures':disclosures}
    if len(json.dumps(result,ensure_ascii=False).encode())>16384: raise ValueError('Encoded view state exceeds16KiB')
    return result


def _view(row):
    return {'id':str(row.pk),'name':row.name,'page':row.page,'state':row.state,
            'created_at':row.created_at.isoformat(),'updated_at':row.updated_at.isoformat()}


@endpoint(('GET','POST'))
def views(request):
    if request.method=='GET':
        rows=WorkspaceView.objects.all()
        page=request.GET.get('page')
        if page:
            if page not in PAGES: raise ValueError('Unsupported saved-view page')
            rows=rows.filter(page=page)
        total=rows.count()
        return JsonResponse({'results':[_view(row) for row in rows[:300]],'count':total,'truncated':total>300})
    data=_body(request)
    if data.keys()-{'id','name','page','state'}: raise ValueError('Unsupported saved-view field')
    row=WorkspaceView.objects.get(pk=UUID(str(data['id']))) if 'id' in data else WorkspaceView()
    created=row._state.adding
    if not created and not data.keys()-{'id'}: raise ValueError('No changes supplied')
    name=data.get('name',row.name);page=data.get('page',row.page)
    if page not in PAGES: raise ValueError('Unsupported saved-view page')
    state=_state(data.get('state',row.state or {}))
    # These shared investigation preferences never store configured credentials.
    safe=sanitize({'name':_text(name),'state':state},preserve_metrics=True)
    if safe!={'name':name.strip(),'state':state}: raise ValueError('Remove credentials from saved view preferences')
    row.name=safe['name'];row.page=page;row.state=state
    if created:row.save()
    else:row.save(update_fields=[key for key in ['name','page','state'] if key in data]+['updated_at'])
    return JsonResponse({'view':_view(row),'created':created},status=201 if created else 200)


@endpoint(('POST',))
def delete_view(request,view_id):
    row=WorkspaceView.objects.get(pk=UUID(str(view_id)))
    key=str(row.pk);row.delete()
    return JsonResponse({'deleted':key})


def _time(value):
    if not isinstance(value,str) or len(value)>80: raise ValueError('Use an ISO8601 timestamp with timezone')
    try: parsed=parse_datetime(value)
    except ValueError: parsed=None
    if parsed is None or timezone.is_naive(parsed): raise ValueError('Use an ISO8601 timestamp with timezone')
    return parsed


def _cursor(value, family):
    if not value: return None
    if not isinstance(value,str) or len(value)>4096: raise ValueError('Invalid cursor')
    try: result=signing.loads(value,salt=CURSOR_SALT+family,max_age=86400)
    except signing.BadSignature: raise ValueError('Invalid or expired cursor') from None
    if not isinstance(result,dict): raise ValueError('Invalid cursor')
    return result


def _scope(organization_id):
    if not organization_id: return None
    root=lifecycle.canonical(lifecycle.organization(UUID(str(organization_id))))
    if root.organization_status=='deleted': raise ValueError('Organization was deleted')
    links=root.outgoing.filter(evidence_id__in=Subquery(store.visible_evidence().values('pk'))).filter(
        Q(relation='user_scope')|Q(relation__in=['uses_service','references_domain','tracks_lookalike'],target__kind='domain'))
    return AssetNode.objects.filter(pk__in=Subquery(links.values('target_id')))


def _member_records(rows,members,kind,attribute):
    if members is None: return rows
    linked=members.filter(kind=kind).annotate(record_key=Replace(KeyTextTransform(attribute,'attributes'),Value('-'),Value(''),output_field=CharField())).filter(
        record_key=Replace(Cast(OuterRef('pk'),CharField()),Value('-'),Value('')))
    return rows.annotate(explicit_member=Exists(linked)).filter(explicit_member=True)


def _change_key(kind,pk):
    return kind+':'+(f'{pk:020d}' if kind=='change' else str(pk))


def _source_page(rows,field,kind,window,cursor,limit):
    rows=rows.filter(**{field+'__gte':window['since'],field+'__lte':window['until']})
    if cursor:
        stamp=_time(cursor['last_time']);last_kind,last_id=cursor['last_key'].split(':',1)
        condition=Q(**{field+'__lt':stamp})
        if kind<last_kind:condition|=Q(**{field:stamp})
        elif kind==last_kind:
            key=int(last_id) if kind=='change' else UUID(last_id)
            condition|=Q(**{field:stamp,'pk__lt':key})
        rows=rows.filter(condition)
    return rows.order_by('-'+field,'-pk')[:limit+1]


def _bounded_summary(value):
    visited=0
    def compact(item,depth=0):
        nonlocal visited
        visited+=1
        if visited>100 or depth>4:return '[nested summary omitted]'
        if isinstance(item,str):return item[:600]+'… [truncated]' if len(item)>600 else item
        if isinstance(item,list):return [compact(child,depth+1) for child in item[:20]]
        if isinstance(item,dict):return {str(key)[:120]:compact(child,depth+1) for key,child in list(item.items())[:12]}
        return item
    return compact(value)


@endpoint()
def changes(request):
    limit=_integer(request.GET.get('limit'),30,100)
    cursor=_cursor(request.GET.get('cursor'),'changes')
    filters={'organization_id':request.GET.get('organization_id',''),'kind':request.GET.get('kind','')}
    if cursor:
        for key,value in filters.items():
            if value and value!=cursor.get(key):raise ValueError('Cursor filters cannot change')
        filters={key:cursor[key] for key in filters}
        since=_time(cursor['since']);until=_time(cursor['until'])
        if request.GET.get('since') and _time(request.GET['since'])!=since:raise ValueError('Cursor window cannot change')
    else:
        until=timezone.now();since=_time(request.GET['since']) if request.GET.get('since') else until-timedelta(days=1)
    if since>until:raise ValueError('Since must not be in the future')
    if filters['kind'] and filters['kind'] not in CHANGE_KINDS:raise ValueError('Unsupported change kind')
    members=_scope(filters['organization_id'])
    endpoints=_member_records(visible_endpoints(),members,'service','endpoint_id')
    change_rows=ObservationChange.objects.filter(endpoint_id__in=Subquery(endpoints.values('pk')),observation__hidden=False).filter(Q(previous__isnull=True)|Q(previous__hidden=False))
    domains=store._visible_nodes().filter(kind='domain')
    if members is not None:domains=domains.filter(pk__in=Subquery(members.values('pk')))
    certificates=_member_records(CertificateRecord.objects.all(),members,'certificate','certificate_id')
    window={'since':since,'until':until};events=[]
    def add(kind,row,collected,observed,label,source,reference,summary):
        events.append({'kind':kind,'id':str(row.pk),'key':_change_key(kind,row.pk),'label':label[:500],
            'source':source,'collected_at':collected.isoformat(),'observed_at':observed.isoformat() if observed else None,
            'reference':reference,'summary':summary})
    if not filters['kind'] or filters['kind']=='change':
        for row in _source_page(change_rows.select_related('endpoint','observation'), 'collected_at','change',window,cursor,limit):
            summary=row.summary[:20] if isinstance(row.summary,list) else []
            add('change',row,row.collected_at,row.observation.observed_at,f'{row.endpoint.address}:{row.endpoint.port}',row.provider,{'type':'endpoint','id':str(row.endpoint_id)},summary)
    if not filters['kind'] or filters['kind']=='endpoint':
        proof=ServiceObservation.objects.filter(endpoint_id=OuterRef('pk'),hidden=False).exclude(provider='certstream').order_by('first_collected_at','pk')
        rows=endpoints.annotate(saved_source=Subquery(proof.values('provider')[:1]),saved_observed_at=Subquery(proof.values('observed_at')[:1]))
        for row in _source_page(rows,'first_seen','endpoint',window,cursor,limit):
            add('endpoint',row,row.first_seen,row.saved_observed_at,f'{row.address}:{row.port}',row.saved_source or 'saved',{'type':'endpoint','id':str(row.pk)},'New retained endpoint record; current reachability is unverified.')
    if not filters['kind'] or filters['kind']=='domain':
        for row in _source_page(domains,'first_seen','domain',window,cursor,limit):
            add('domain',row,row.first_seen,None,row.value,'saved',{'type':'asset','id':str(row.pk)},'New retained domain node; this does not establish ownership.')
    if not filters['kind'] or filters['kind']=='certificate':
        for row in _source_page(certificates,'first_seen','certificate',window,cursor,limit):
            add('certificate',row,row.first_seen,row.observed_at,row.fingerprint or 'Saved certificate','certstream',{'type':'certificate','id':str(row.pk)},'New retained certificate record; not a current service check.')
    events.sort(key=lambda row:(row['collected_at'],row['key']),reverse=True)
    more=len(events)>limit;events=events[:limit]
    output_window={key:value.isoformat() for key,value in window.items()}
    next_cursor=None
    if more and events:
        last=events[-1]
        next_cursor=signing.dumps(dict(output_window,**filters,last_time=last['collected_at'],last_key=last['key']),salt=CURSOR_SALT+'changes',compress=True)
    output=sanitize({'results':events,'next_cursor':next_cursor,'has_more':more,'window':output_window,
        'basis':'Retained collection-time events only, with a fixed browsing window. Current visibility is enforced on every page; hidden evidence can disappear. Endpoint/domain/certificate entries describe saved record creation, not live discovery. Duplicate payloads and overwritten DNS snapshots do not provide a complete history. Organization filtering uses explicit saved membership, not shared-infrastructure ownership. Change summaries are bounded previews.'},preserve_metrics=True)
    for row in output['results']:row['summary']=_bounded_summary(row['summary'])
    return JsonResponse(output)


@endpoint()
def activity(request):
    limit=_integer(request.GET.get('limit'),20,30);recent=timezone.now()-timedelta(days=1)
    rows=Run.objects.filter(Q(state__in=ACTIVE)|Q(state='failed',updated_at__gte=recent))
    counts={state:0 for state in [*sorted(ACTIVE),'failed']}
    counts.update({item['state']:item['count'] for item in rows.values('state').annotate(count=Count('pk')).order_by()})
    runs=list(rows.annotate(priority=Case(When(state__in=['queued','running'],then=Value(0)),When(state='paused',then=Value(1)),default=Value(2),output_field=IntegerField())).order_by('priority','-updated_at','-pk')[:limit+1])
    runs=runs[:limit]
    candidate_count=DomainCandidate.objects.filter(expansion_id=OuterRef('pk')).order_by().values('expansion_id').annotate(c=Count('pk')).values('c')
    keys=['id','run_id','state','usage','updated_at',*[f'checkpoint__{name}' for name in ['current_source','current_seed','current_action','source_queries_completed','pending_queries','processed','elapsed']]]
    expansions=DomainExpansion.objects.filter(run_id__in=[row.pk for row in runs if row.kind=='domain_expansion']).annotate(saved_candidates=Coalesce(Subquery(candidate_count),Value(0))).values(*keys,'saved_candidates')
    briefs={}
    for row in expansions:
        brief={'id':str(row['id']),'state':row['state'],'saved_candidates':row['saved_candidates'],'usage':row['usage'],'last_activity_at':row['updated_at'].isoformat()}
        for name in ['current_source','current_seed','current_action','source_queries_completed','pending_queries','processed']:brief[name]=row.get('checkpoint__'+name)
        brief['elapsed_seconds']=row.get('checkpoint__elapsed') or 0
        briefs[row['run_id']]=brief
    result=[]
    for row in runs:
        data=serialize_run(row,include_activity=False);data.pop('parameters',None)
        saved=data.get('result') if isinstance(data.get('result'),dict) else {}
        data['result']={key:saved[key] for key in ['stored','new_results','repeat_results','start_page','next_page','total','partial','processed','saved_candidates','pending_queries','planned','indexed','signals','skipped','errors','network_contact'] if key in saved and isinstance(saved[key],(int,float,bool))}
        data['error']=data['error'][:1000]
        if row.pk in briefs:data['activity']=briefs[row.pk]
        result.append(data)
    total=sum(counts.values())
    return JsonResponse(sanitize({'results':result,'counts':counts,'total_active':sum(counts[state] for state in ACTIVE),
        'limit':limit,'truncated':total>limit,'basis':'Saved job state only; no broker refresh or collection. Failed count covers the past24hours. Active jobs are prioritized; opening this tray does not start, resume or cancel work.'},preserve_metrics=True))


def _correlation_candidates(members):
    endpoints=_member_records(visible_endpoints(),members,'service','endpoint_id')
    proof=ServiceObservation.objects.filter(correlation_signals=OuterRef('pk'),endpoint_id=OuterRef('endpoint_id'),hidden=False).order_by('-last_collected_at','-pk')
    from leakscope.phase3.local_correlations import visible_artifacts
    artifacts=visible_artifacts(endpoints=endpoints).filter(pk=OuterRef('artifact_id'),endpoint_id=OuterRef('endpoint_id'))
    rows=EvidenceCorrelation.objects.filter(endpoint_id__in=Subquery(endpoints.values('pk'))).annotate(
        has_visible_proof=Exists(proof),visible_observation_id=Subquery(proof.values('pk')[:1]),
        visible_fk=Case(When(observation__hidden=False,observation__endpoint_id=F('endpoint_id'),then=Value(True)),default=Value(False),output_field=BooleanField()),
        artifact_visible=Exists(artifacts))
    return rows.filter(Q(has_visible_proof=True)|Q(visible_fk=True)|Q(artifact_visible=True))



def _meaning(kind):
    if kind.startswith('declaration.'):
        return 'Same normalized service origin in a retained API declaration. Configuration references can be examples, shared platforms or stale; inspect the schema proof before attributing reachability or ownership.'
    if kind.startswith('tracking.'):
        return 'Same public marketing or analytics project identifier in retained response evidence. Shared agencies, templates and reused configurations can explain the association.'
    if kind=='screenshot.dhash64':
        return 'Equal locally computed dHash64 values; this does not establish identical image bytes or common ownership. Use similarity mode for bounded image-distance grouping.'
    if kind.startswith('artifact.screenshot.'):
        return 'Exact saved screenshot-file hash match; this is not perceptual similarity or proof of common ownership.'
    if kind.startswith('artifact.') or kind=='http.body.sha256':
        return 'Exact saved response/artifact hash match; matching bytes do not establish common ownership.'
    if kind=='favicon.ahash64':
        return 'Equal provider favicon perceptual-hash values, not an implemented image-distance metric or proof of common ownership.'
    return 'Shared indexed fingerprint/identifier value; matches can be shared or collide and do not establish common ownership.'


@endpoint()
def correlations(request):
    limit=_integer(request.GET.get('limit'),15,30);cursor=_cursor(request.GET.get('cursor'),'correlations')
    filters={key:request.GET.get(key,'') for key in ['kind','q','organization_id']}
    if len(filters['kind'])>48 or len(filters['q'])>128:raise ValueError('Correlation filter too long')
    if cursor:
        for key,value in filters.items():
            if value and value!=cursor.get(key):raise ValueError('Cursor filters cannot change')
        filters={key:cursor[key] for key in filters}
    candidates=_correlation_candidates(_scope(filters['organization_id']))
    kinds=list(candidates.order_by('kind').values_list('kind',flat=True).distinct()[:40])
    if filters['kind']:candidates=candidates.filter(kind=filters['kind'])
    if filters['q']:candidates=candidates.filter(value__icontains=filters['q'])
    similar=request.GET.get('similar','0')
    if similar not in {'0','1','false','true',''}:raise ValueError('Invalid similarity mode')
    if similar in {'1','true'}:
        if cursor:raise ValueError('Similarity is a bounded sample and does not accept a cursor')
        from leakscope.phase3.local_correlations import screenshot_similarity
        threshold=_integer(request.GET.get('threshold'),8,8,minimum=0)
        signal_limit=_integer(request.GET.get('signal_limit'),500,500)
        output=screenshot_similarity(candidates.filter(artifact_visible=True),threshold=threshold,limit=signal_limit)
        groups=output.get('results',[])
        output.update(results=groups[:limit],truncated=bool(output.get('truncated') or len(groups)>limit),
                      next_cursor=None,has_more=False,kinds=kinds,algorithm=output.get('algorithm','dHash64'))
        return JsonResponse(sanitize(output,preserve_metrics=True))
    groups=candidates.values('kind','value').annotate(endpoint_count=Count('endpoint_id',distinct=True)).filter(endpoint_count__gte=2)
    if cursor:groups=groups.filter(Q(kind__gt=cursor['last_kind'])|Q(kind=cursor['last_kind'],value__gt=cursor['last_value']))
    selected=list(groups.order_by('kind','value')[:limit+1]);more=len(selected)>limit;selected=selected[:limit]
    members={};pairs=Q(pk__in=[])
    for group in selected:pairs|=Q(kind=group['kind'],value=group['value'])
    if selected:
        bounded=candidates.filter(pairs).annotate(group_row=Window(expression=RowNumber(),partition_by=[F('kind'),F('value')],order_by=F('endpoint_id').asc())).filter(group_row__lte=20)
        for row in bounded.select_related('endpoint').order_by('kind','value','endpoint_id'):
            observation=row.visible_observation_id or (row.observation_id if row.visible_fk else None)
            members.setdefault((row.kind,row.value),[]).append({'id':str(row.endpoint_id),'address':row.endpoint.address,'port':row.endpoint.port,'transport':row.endpoint.transport,
                'observation_id':str(observation) if observation else None,'artifact_id':str(row.artifact_id) if row.artifact_visible else None,
                'reference':{'type':'endpoint','id':str(row.endpoint_id)}})
    result=[]
    for group in selected:
        group.update(key=group['kind']+':'+group['value'],members=members.get((group['kind'],group['value']),[]),members_truncated=group['endpoint_count']>20,meaning=_meaning(group['kind']))
        result.append(group)
    next_cursor=signing.dumps(dict(filters,last_kind=selected[-1]['kind'],last_value=selected[-1]['value']),salt=CURSOR_SALT+'correlations',compress=True) if more and selected else None
    return JsonResponse(sanitize({'results':result,'next_cursor':next_cursor,'has_more':more,'kinds':kinds,
        'basis':'Locally indexed saved signals with visible proof only; no collection or indexing on browse. Older unindexed records are absent. Groups show at least two visible endpoints; membership is bounded and matching evidence does not establish ownership.'},preserve_metrics=True))


@endpoint(('POST',))
def reindex_correlations(request):
    from leakscope.phase3.local_correlations import queue_reindex
    data=_body(request)
    if data.keys()-{'limit','organization_id','artifact_ids','endpoint_ids'}:raise ValueError('Unsupported local-index option')
    members=_scope(data.get('organization_id'))
    endpoints=_member_records(visible_endpoints(),members,'service','endpoint_id')
    spec={key:value for key,value in data.items() if key!='organization_id'}
    row=queue_reindex(spec,endpoints=endpoints)
    return JsonResponse(sanitize({'run':serialize_run(row,include_activity=False)},preserve_metrics=True),status=202)
