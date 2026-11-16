"""Bounded domain research with durable spending and explicit human review."""
import hashlib
import ipaddress
import json
import re
import time
from collections import deque
from datetime import timedelta
from uuid import UUID, uuid4
from urllib.parse import urlsplit, urlunsplit

from celery import shared_task
from celery.exceptions import Ignore
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from leakscope.services.config import get_config
from leakscope.services.runs import serialize_run, _data
from leakscope.services.run_events import emit
from leakscope_app.models import (DomainExpansion, DomainCandidate, DomainDecision,
    DomainSourceCache, AssetNode, AssetRelationship, Run)
from .asset_names import normalize_domain, registrable_domain
from . import relationships as store
from . import organization_lifecycle as lifecycle
from .usage_policy import validate_demo

SOURCES = ('saved', 'certificates', 'crtsh', 'certspotter', 'hackertarget', 'urlscan', 'gleif', 'web', 'registration', 'dns', 'variants', 'netlas', 'netlas_dns', 'reverse_dns', 'passive_dns', 'infrastructure', 'brute', 'alterations', 'commoncrawl', 'wayback')
PASSIVE_SOURCES = {'crtsh','certspotter','hackertarget','urlscan','gleif','commoncrawl','wayback'}
DEFAULT_SOURCES = ['saved', 'certificates', 'crtsh', 'certspotter', 'hackertarget', 'urlscan', 'gleif', 'passive_dns']
DEFAULT_BUDGETS = {'web': 8, 'dns': 5000, 'rdap': 20, 'netlas': 0, 'public_ct': 20, 'domain_api': 20, 'org_api': 10}
MAX_BUDGETS = {key:1000000 if key=='dns' else 10000 for key in DEFAULT_BUDGETS}
RELATIONS = {'primary_site', 'subsidiary', 'acquired_brand', 'historical', 'service_provider', 'lookalike', 'related', 'subdomain'}
TERMINAL = {'succeeded', 'failed', 'cancelled', 'lost'}


class StorageLimitReached(Exception):
    """A collected page must remain checkpointed until its quota is increased."""


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def integer(value, minimum, maximum, name):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError('Invalid '+name)
    return value


def strings(value, maximum=30, length=253):
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError('Enter at most '+str(maximum)+' values')
    result=[]
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item) > length or any(ord(c)<32 or ord(c)==127 for c in item):
            raise ValueError('Invalid text input')
        item=' '.join(item.split())
        if item not in result: result.append(item)
    return result


def domain(value):
    value=normalize_domain(value, allow_wildcard=True)
    root=registrable_domain(value)
    if '.' not in root or root.endswith(('.local', '.internal', '.localhost')):
        raise ValueError('Enter a publicly registrable domain')
    return value, root


def normalize_spec(data):
    if not isinstance(data,dict):raise ValueError('Expected a JSON object')
    companies=strings(data.get('companies',[]),30,200)
    domains=strings(data.get('domains',[]),1000)
    seeds=[];seen=set()
    def add(kind,value):
        key=(kind,value.casefold())
        if key not in seen:
            seeds.append({'kind':kind,'value':value,'depth':0});seen.add(key)
    for company in companies:add('company',company)
    for name in domains:
        name,root=domain(name);add('domain',name);add('domain',root)
    for value in strings(data.get('ips',[]),1000):
        try:add('ip',str(ipaddress.ip_address(value)))
        except ValueError:raise ValueError('Invalid IP address') from None
    for value in strings(data.get('networks',[]),1000):
        try:add('network',str(ipaddress.ip_network(value,strict=False)))
        except ValueError:raise ValueError('Invalid network') from None
    for value in strings(data.get('asns',[]),1000,16):
        value=value.upper().removeprefix('AS')
        if not value.isdigit() or not 1<=int(value)<=4294967295:raise ValueError('Invalid ASN')
        add('asn',str(int(value)))
    if not seeds:raise ValueError('Enter a company, domain, IP, network or ASN, or select an organization')
    defaults=DEFAULT_SOURCES+(['web'] if any(s['kind']=='company' for s in seeds) and not any(s['kind']=='domain' for s in seeds) else [])
    if any(s['kind'] in {'ip','network','asn'} for s in seeds):defaults+=['infrastructure','reverse_dns']
    sources=data.get('sources',defaults)
    if not isinstance(sources,list) or not sources or len(sources)>len(SOURCES) or any(not isinstance(s,str) or s not in SOURCES for s in sources):
        raise ValueError('Select supported discovery sources')
    sources=[s for s in SOURCES if s in sources]
    demo=validate_demo(data.get('demo',False))
    provided=data.get('budgets',{})
    if not isinstance(provided,dict) or set(provided)-set(DEFAULT_BUDGETS):raise ValueError('Invalid request budgets')
    budgets={key:integer(provided.get(key,default),0,MAX_BUDGETS[key],key+' budget') for key,default in DEFAULT_BUDGETS.items()}
    enabled={'web':{'web'},'dns':{'dns','reverse_dns','brute','alterations'},'rdap':{'registration'},'netlas':{'netlas','netlas_dns'},'public_ct':{'crtsh','certspotter'},'domain_api':{'hackertarget','urlscan','passive_dns','infrastructure','commoncrawl','wayback'},'org_api':{'gleif'}}
    budgets={key:(min(value,1) if demo else value) if enabled[key].intersection(sources) else 0 for key,value in budgets.items()}
    if {'netlas','netlas_dns'}.intersection(sources) and not budgets['netlas']:raise ValueError('Set an explicit Netlas request budget to enable paid discovery')
    from .netlas_dns_history import validate_indices
    netlas_indices=validate_indices(data.get('netlas_indices',[])) if 'netlas_dns' in sources else []
    focused=data.get('focused',False)
    if not isinstance(focused,bool):raise ValueError('Invalid focused research option')
    context=data.get('context',{})
    if not isinstance(context,dict) or set(context)-{'country','industry'}:raise ValueError('Invalid company context')
    context={key:strings([value],1,160)[0] for key,value in context.items() if value}
    wordlist=[value.lower() for value in strings(data.get('wordlist',[]),10000,100)]
    if any(not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?',v) or '..' in v or any(len(label)>63 for label in v.split('.')) for v in wordlist):raise ValueError('Invalid DNS wordlist label')
    from .historical_sources import validate_index, DEFAULT_INDEX
    try:archive=validate_index(data.get('commoncrawl_index',DEFAULT_INDEX))
    except ValueError:
        if 'commoncrawl' in sources:raise
        archive=DEFAULT_INDEX
    return {'commoncrawl_index':archive,'netlas_indices':netlas_indices,'seeds':seeds,'sources':sources,'demo':demo,'budgets':budgets,
            'max_depth':0 if demo else integer(data.get('max_depth',2),0,10,'expansion depth'),
            'max_candidates':1 if demo else integer(data.get('max_candidates',0),0,10000000,'storage quota'),
            'runtime_seconds':integer(data.get('runtime_seconds',1800),60,86400,'runtime seconds'),
            'dns_rate':integer(data.get('dns_rate',20),1,100,'DNS rate'),'wordlist':wordlist,
            'web_results':1 if demo else integer(data.get('web_results',20),1,100,'web results per search'),
            'context':context,'focused':focused}


def _scope(organization_id, spec):
    return digest(['organization',str(organization_id)]) if organization_id else digest(['standalone',sorted((s['kind'],s['value'].casefold()) for s in spec['seeds']),{k:v.casefold() for k,v in spec.get('context',{}).items()}])


def first_party(spec, name):
    try:
        root=registrable_domain(name)
        return any(seed['kind']=='domain' and registrable_domain(seed['value'])==root for seed in spec['seeds'])
    except (ValueError,TypeError):return False


def category(spec, name, relation, proofs):
    if first_party(spec,name):return 'domains'
    if relation=='lookalike':return 'lookalikes'
    signals={p.get('signal') for p in proofs if isinstance(p,dict)}
    if relation=='service_provider' or 'service_dependency' in signals:return 'services'
    supported={'company_domain_candidate','user_scope','saved_scope','historical_certificate_name','passive_host_record','historical_page_domain','passive_dns_record','reverse_dns','shared_ip','reverse_dns_record','reverse_ip_record','observed_page_domain','certificate_san','same_certificate','legal_statement','acquisition_statement',
        'registration_match','registrant_organization','shared_identifier','official_statement'}
    if supported.intersection(signals):return 'related'
    return 'references'


def auto_lead(lead):
    if lead.get('ambiguous'):return False
    signals={p.get('signal') for p in lead.get('evidence',[]) if isinstance(p,dict)}
    if lead['kind']=='company':return bool(signals.intersection({'legal_entity_alias','legal_entity_child','acquisition_statement','legal_statement'}))
    if lead['kind']=='identifier':return 'shared_identifier' in signals
    if lead['kind']=='domain':return bool(signals.intersection({'certificate_san','acquisition_statement','registration_match'}))
    return False


@transaction.atomic
def save_leads(expansion_id, leads, *, execution_token=None):
    exp=DomainExpansion.objects.select_for_update().get(pk=expansion_id)
    if execution_token is not None and exp.execution_token!=execution_token:raise Ignore()
    if exp.state!='running' or not isinstance(leads,list):return []
    incoming=[]
    for value in leads:
        if not isinstance(value,dict) or value.get('kind') not in {'company','domain','identifier','ip','network','asn'}:continue
        name=value.get('value')
        if not isinstance(name,str) or not name.strip() or len(name)>253 or any(ord(c)<32 or ord(c)==127 for c in name):continue
        try:
            if value['kind']=='domain':name,_=domain(name)
            elif value['kind']=='ip':name=str(ipaddress.ip_address(name))
            elif value['kind']=='network':name=str(ipaddress.ip_network(name,strict=False))
            elif value['kind']=='asn':
                name=name.upper().removeprefix('AS')
                if not name.isdigit() or not 1<=int(name)<=4294967295:continue
                name=str(int(name))
        except ValueError:continue
        proofs=value.get('evidence',[])
        if isinstance(proofs,dict):proofs=[proofs]
        lead={'kind':value['kind'],'value':name.strip(),'relation':str(value.get('relation','lead'))[:40],
            'ambiguous':bool(value.get('ambiguous',value.get('ambiguity',False))), 'evidence':_safe_evidence(proofs)}
        if isinstance(value.get('parent_lei'),str):lead['parent_lei']=value['parent_lei'][:20]
        incoming.append(lead)
    merged={digest([lead['kind'],lead['value'].casefold(),lead.get('relation'),lead.get('ambiguous')]):lead for lead in exp.leads}
    for lead in incoming:
        key=digest([lead['kind'],lead['value'].casefold(),lead.get('relation'),lead.get('ambiguous')])
        if key in merged:
            evidence={digest(p):p for p in merged[key]['evidence']+lead['evidence']}
            lead['evidence']=list(evidence.values())[:20]
        merged[key]=lead
    exp.leads=list(merged.values())
    exp.save(update_fields=['leads','updated_at'])
    return incoming


def _active(expansion, *, lock=False):
    if not expansion.organization_id:return None
    org=lifecycle.organization(expansion.organization_id)
    destination=lifecycle.canonical(org)
    if lock:
        locked={row.pk:row for row in AssetNode.objects.select_for_update().filter(pk__in=[org.pk,destination.pk]).order_by('pk')}
        current=lifecycle.canonical(locked[org.pk])
        if current.pk!=destination.pk:raise ValueError('Organization changed; reload research')
        org=locked[destination.pk]
    else:org=destination
    if org.organization_status!='active':raise ValueError('Organization is '+org.organization_status+'; restore it before changing research')
    return org


def start(data, *, parent=None, prefill=True):
    data=dict(data)
    if not isinstance(data.get('focused',False),bool):raise ValueError('Invalid focused research option')
    if data.get('focused'):prefill=False
    org=None
    if data.get('organization_id'):
        org=lifecycle.canonical(lifecycle.organization(data['organization_id']))
        if org.organization_status!='active':raise ValueError('Organization must be active for expansion')
        scopes=org.outgoing.filter(relation='user_scope',evidence_id__in=store.visible_evidence().values('pk'))
        existing=list(scopes.filter(target__kind='domain').values_list('target__value',flat=True).distinct()[:1000])
        if prefill:data['companies']=list(dict.fromkeys([org.value,*org.attributes.get('aliases',[])[:8],*strings(data.get('companies',[]),20,200)]))[:30]
        if prefill:
            data['domains']=list(dict.fromkeys([*strings(data.get('domains',[]),1000),*existing]))[:1000]
            for field,kind in [('ips','ip'),('networks','network'),('asns','asn')]:
                values=scopes.filter(target__kind=kind).values_list('target__value',flat=True).distinct()[:1000]
                data[field]=list(dict.fromkeys([*strings(data.get(field,[]),1000),*values]))[:1000]
    if data.get('reference'):
        from leakscope_app.relationships import _context
        source=_context({'reference':data['reference']})
        data['domains']=list(dict.fromkeys([*strings(data.get('domains',[]),1000),*[item['name'] for item in source['names']]]))[:1000]
    spec=normalize_spec(data)
    with transaction.atomic():
        if org:org=lifecycle.organization(org.pk,active=True,lock=True)
        run=Run.objects.create(task_id=str(uuid4()),kind='domain_expansion',provider='mixed',parameters=_data(spec))
        exp=DomainExpansion.objects.create(organization=org,parent=parent,run=run,spec=spec,
            scope_key=_scope(org.pk if org else None,spec),usage={key:0 for key in DEFAULT_BUDGETS})
        emit(run,'Expansion queued',event='expansion.queued',data={'expansion_id':str(exp.pk),'seeds':len(spec['seeds']),'sources':spec['sources']},dedupe_key='expansion.queued')
        def dispatch():
            try:run_expansion.apply_async(args=[str(exp.pk)],task_id=run.task_id)
            except Exception:
                finish(exp.pk,'failed','Job could not be dispatched; saved research is retained')
        transaction.on_commit(dispatch)
    return exp


def _candidate_json(candidate):
    return {'id':str(candidate.pk),'domain':candidate.domain,'root':candidate.root,'relation':candidate.relation,
        'confidence':candidate.confidence,'confidence_label':'strong' if candidate.confidence>=75 else 'medium' if candidate.confidence>=50 else 'low',
        'status':candidate.status,'evidence':candidate.evidence,'paths':candidate.paths,'tags':candidate.tags,
        'category':candidate.category or category(candidate.expansion.spec,candidate.domain,candidate.relation,candidate.evidence),'dns_status':candidate.dns_status,'identifiers':candidate.identifiers,'updated_at':candidate.updated_at.isoformat()}


def serialize(expansion):
    run=expansion.run
    # Run expiry is reconciled without contacting a provider or restarting work.
    if expansion.state in {'queued','running'} and run.state in TERMINAL:
        finish(expansion.pk,run.state,'Worker stopped; partial results retained' if run.state!='succeeded' else None)
        expansion.refresh_from_db();run.refresh_from_db()
    if expansion.state in {'queued','running'} and timezone.now()-expansion.updated_at>timedelta(minutes=15):
        finish(expansion.pk,'lost','Research deadline expired; partial results retained')
        expansion.refresh_from_db();run.refresh_from_db()
    checkpoint=expansion.checkpoint or {}
    pending=len(checkpoint.get('queue',[]))+len(checkpoint.get('blocked',[]))
    if checkpoint.get('pending') and not checkpoint.get('queue'):pending+=1
    coverage=list((expansion.coverage or {}).values())
    return {'id':str(expansion.pk),'organization_id':str(expansion.organization_id) if expansion.organization_id else None,
        'organization_name':expansion.organization.value if expansion.organization_id else None,
        'parent_id':str(expansion.parent_id) if expansion.parent_id else None,'seeds':expansion.spec['seeds'],
        'sources':expansion.spec['sources'],'netlas_indices':expansion.spec.get('netlas_indices',[]),'budgets':expansion.spec['budgets'],'demo':expansion.spec['demo'],
        'max_depth':expansion.spec['max_depth'],'max_candidates':expansion.spec['max_candidates'],'focused':expansion.spec.get('focused',False),
        'context':expansion.spec.get('context',{}),'state':expansion.state,'run':serialize_run(run),
        'runtime_seconds':expansion.spec.get('runtime_seconds',1800),'dns_rate':expansion.spec.get('dns_rate',20),'wordlist':expansion.spec.get('wordlist',[]),'web_results':expansion.spec.get('web_results',20),
        'resumable':bool(pending and expansion.state in {'paused','failed','lost','cancelled'} and not expansion.spec['demo']),
        'coverage':coverage,'checkpoint_summary':{'pending_seeds':pending,'processed':checkpoint.get('processed',0),'current_source':checkpoint.get('current_source',''),
            'current_seed':checkpoint.get('current_seed',''),'current_action':checkpoint.get('current_action',''),
            'source_queries_completed':checkpoint.get('source_queries_completed',0),
            'reason':checkpoint.get('reason',''),'elapsed_seconds':checkpoint.get('elapsed',0)},
        'leads':expansion.leads,'usage':expansion.usage,'notes':expansion.notes,'partial':expansion.partial,'created_at':expansion.created_at.isoformat()}


def detail(expansion, params=None):
    params=params or {}
    def page_number(key,default,maximum):
        value=params.get(key,default)
        if isinstance(value,bool) or not str(value).isdigit():raise ValueError('Invalid '+key)
        return integer(int(value),1,maximum,key)
    page=page_number('page',1,10000000);size=page_number('page_size',100,500)
    kinds=('domains','related','services','references','lookalikes')
    rows=expansion.candidates.all()
    counts={kind:0 for kind in kinds}
    counts.update({r['category']:r['n'] for r in rows.exclude(category='').values('category').annotate(n=Count('pk'))})
    legacy={kind:[] for kind in kinds}
    for candidate in rows.filter(category='').iterator(chunk_size=1000):
        kind=category(expansion.spec,candidate.domain,candidate.relation,candidate.evidence)
        legacy[kind].append(candidate.pk);counts[kind]+=1
    counts['total']=sum(counts[kind] for kind in kinds)
    group=params.get('category','all')
    if group not in (*kinds,'all','discovery'):raise ValueError('Invalid candidate category')
    if group!='all':
        groups=['domains','related'] if group=='discovery' else [group]
        rows=rows.filter(Q(category__in=groups)|Q(pk__in=[pk for kind in groups for pk in legacy[kind]]))
    status=params.get('status','');relation=params.get('relation','');query=params.get('q','')
    if status:
        if status not in {'pending','accepted','rejected'}:raise ValueError('Invalid candidate status')
        rows=rows.filter(status=status)
    if relation:
        if relation not in RELATIONS:raise ValueError('Invalid candidate relation')
        rows=rows.filter(relation=relation)
    if not isinstance(query,str) or len(query)>500:raise ValueError('Invalid candidate query')
    if query:rows=rows.filter(Q(domain__icontains=query)|Q(relation__icontains=query)|Q(tags__icontains=query))
    count=rows.count();pages=max(1,(count+size-1)//size);page=min(page,pages)
    candidates=[]
    for candidate in rows.order_by('domain','pk')[(page-1)*size:page*size]:
        candidate.expansion=expansion;candidates.append(_candidate_json(candidate))
    return {'expansion':serialize(expansion),'candidates':candidates,'count':count,'counts':counts,'page':page,'page_size':size,'pages':pages}


@transaction.atomic
def note(expansion_id, message, *, execution_token=None):
    exp=DomainExpansion.objects.select_for_update().get(pk=expansion_id)
    if execution_token is not None and exp.execution_token!=execution_token:raise Ignore()
    if message not in exp.notes and len(exp.notes)<60:
        exp.notes=[*exp.notes,message]
        emit(exp.run_id,message,event='expansion.note',level='warning',execution_token=execution_token,
            dedupe_key='note:'+digest([message,exp.execution_token]))
    exp.partial=True;exp.save(update_fields=['notes','partial','updated_at'])


@transaction.atomic
def finish(expansion_id, state, message=None, *, execution_token=None):
    exp=DomainExpansion.objects.select_for_update().get(pk=expansion_id)
    if execution_token is not None and exp.execution_token!=execution_token:return
    if exp.state in TERMINAL:return
    exp.state=state
    if message:
        exp.notes=[*exp.notes,message][:60];exp.partial=True
    exp.save(update_fields=['state','notes','partial','updated_at'])
    result={'expansion_id':str(exp.pk),'partial':exp.partial,'notes':exp.notes,'usage':exp.usage,'candidates':exp.candidates.count(),'leads':exp.leads}
    Run.objects.filter(pk=exp.run_id,state__in=['queued','running','paused']).update(state=state,finished_at=timezone.now(),result=_data(result),updated_at=timezone.now())
    emit(exp.run_id,'Expansion '+state+((': '+message) if message else ''),event='expansion.'+state,
        level='error' if state=='failed' else 'warning' if state in {'cancelled','lost'} else 'info',
        data={'saved_candidates':result['candidates'],'usage':exp.usage,'partial':exp.partial},
        dedupe_key='expansion.'+state+':'+exp.run.task_id)


class Budget:
    def __init__(self, expansion_id, deadline, execution_token=None):
        self.expansion_id=expansion_id;self.deadline=deadline;self.refused=False;self.denied=set()
        self.execution_token=execution_token

    def __call__(self,bucket,units=1):
        if bucket not in DEFAULT_BUDGETS:raise ValueError('Unknown collection budget')
        integer(units,1,50,'budget units')
        with transaction.atomic():
            snapshot=DomainExpansion.objects.get(pk=self.expansion_id)
            if self.execution_token is not None and snapshot.execution_token!=self.execution_token:
                self.refused=True;return False
            try:_active(snapshot,lock=True)
            except ValueError:
                self.refused=True;finish(snapshot.pk,'cancelled','Organization became inactive during research',execution_token=self.execution_token);return False
            exp=DomainExpansion.objects.select_for_update().get(pk=self.expansion_id)
            if self.execution_token is not None and exp.execution_token!=self.execution_token:
                self.refused=True;return False
            if exp.organization_id!=snapshot.organization_id:self.refused=True;return False
            if exp.state!='running' or time.monotonic()>=self.deadline:
                self.refused=True;return False
            usage=dict(exp.usage)
            if usage.get(bucket,0)+units>exp.spec['budgets'][bucket]:
                self.refused=True;self.denied.add(bucket);note(exp.pk,bucket+' request budget reached',execution_token=self.execution_token);return False
            usage[bucket]=usage.get(bucket,0)+units
            exp.usage=usage;exp.save(update_fields=['usage','updated_at'])
            if bucket!='dns' or usage[bucket]<=5 or usage[bucket]%100==0:
                emit(exp.run_id,bucket+' request allowance reserved: '+str(usage[bucket])+' / '+str(exp.spec['budgets'][bucket]),
                    event='request.reserved',data={'bucket':bucket,'used':usage[bucket],'limit':exp.spec['budgets'][bucket]},
                    execution_token=self.execution_token,dedupe_key='request:'+bucket+':'+str(usage[bucket]))
        return True


def _safe_evidence(proofs):
    cleaned=json.loads(_data(proofs))
    result=[]
    if not isinstance(cleaned,list):return result
    for proof in cleaned[:20]:
        if not isinstance(proof,dict):continue
        item={key:str(proof.get(key,'') or '')[:limit] for key,limit in [('source',80),('url',2000),('excerpt',1500),('observed_at',80),('signal',80),('via',253)]}
        try:
            u=urlsplit(item['url'])
            if u.scheme not in {'http','https'} or not u.hostname or u.username or u.password:item['url']=''
        except ValueError:item['url']=''
        value=proof.get('strength',20)
        item['strength']=max(0,min(100,value)) if isinstance(value,int) and not isinstance(value,bool) else 20
        for key in ('evidence_id','certificate_id','source_record_id','not_before','not_after','first_seen','last_seen','record_type','entity_status','registration_status','next_renewal_date','collected_at','archive','mime','status','index_label'):
            if isinstance(proof.get(key),(str,int)) and not isinstance(proof.get(key),bool):item[key]=str(proof[key])[:200]
        if proof.get('historical') is True:item['historical']=True
        if proof.get('source')=='netlas_dns' and isinstance(proof.get('dns_records'),dict):
            item['dns_records']={key:value[:64] for key,value in proof['dns_records'].items() if key in {'A','AAAA','CNAME','MX','NS','TXT','SOA','CAA'} and isinstance(value,list)}
        archived=proof.get('archived_url')
        if isinstance(archived,str) and len(archived)<=8192 and not any(ord(char)<32 or ord(char)==127 for char in archived):
            try:
                parsed=urlsplit(archived)
                if parsed.scheme in {'http','https'} and parsed.hostname and not parsed.username and not parsed.password:
                    port=parsed.port
                    host=parsed.hostname
                    authority=('['+host+']' if ':' in host else host)+(':'+str(port) if port else '')
                    item['archived_url']=urlunsplit((parsed.scheme,authority,parsed.path[:1024] or '/','',''))
            except ValueError:pass
        count=proof.get('certificate_root_count')
        if isinstance(count,int) and not isinstance(count,bool) and count>=0:item['certificate_root_count']=min(count,100000)
        addresses=[]
        for value in proof.get('addresses',[])[:20] if isinstance(proof.get('addresses'),list) else []:
            try:addresses.append(str(ipaddress.ip_address(value)))
            except (ValueError,TypeError):pass
        if addresses:item['addresses']=addresses
        result.append(item)
    return result


def _confidence(proofs, relation):
    weak={'shared_ip','shared_nameserver','shared_cdn','same_certificate','certificate_san','cname','name_variant','provider_name','shared_identifier'}
    stronger={'legal_statement','official_statement','acquisition','registrant_organization','registration_match','official_website','subsidiary_statement','acquisition_statement'}
    families={}
    for p in proofs:
        signal=p['signal'];strength=p['strength']
        cap=30 if signal in weak else 70 if signal in stronger else 35
        # Repeated snippets/copies from one originating document count only once.
        family=p['url'] or p['source']
        try:family=urlsplit(family).hostname or family
        except ValueError:pass
        families[family]=max(families.get(family,0),min(cap,strength))
    values=sorted(families.values(),reverse=True)
    score=values[0] if values else 0
    if len(values)>1 and values[0]>=50 and values[1]>=40:score=min(85,score+15)
    if relation in {'lookalike','service_provider','historical'}:score=min(score,35)
    return score


@transaction.atomic
def save_candidate(expansion_id, item, seed, *, execution_token=None):
    snapshot=DomainExpansion.objects.get(pk=expansion_id)
    if execution_token is not None and snapshot.execution_token!=execution_token:raise Ignore()
    org=_active(snapshot,lock=True)
    exp=DomainExpansion.objects.select_for_update().get(pk=expansion_id)
    if execution_token is not None and exp.execution_token!=execution_token:raise Ignore()
    if exp.organization_id!=snapshot.organization_id:raise ValueError('Organization changed; reload research')
    if exp.state!='running':return None
    if not isinstance(item,dict):return None
    try:name,root=domain(item.get('domain'))
    except (ValueError,TypeError):return None
    incoming_proofs=item.get('evidence',[])
    historical_dns=isinstance(incoming_proofs,list) and any(isinstance(proof,dict) and proof.get('source')=='netlas_dns' and proof.get('historical') is True for proof in incoming_proofs)
    if not historical_dns and any(seed['kind']=='domain' and seed['value']==name for seed in exp.spec['seeds']):return None
    candidate=DomainCandidate.objects.filter(expansion=exp,domain=name).first()
    if not historical_dns and candidate is None and org and AssetRelationship.objects.filter(source=org,target__kind='domain',target__value=name,relation='user_scope',evidence_id__in=store.visible_evidence().values('pk')).exists():return None
    if candidate is None and exp.spec['max_candidates'] and exp.candidates.count()>=exp.spec['max_candidates']:
        raise StorageLimitReached()
    decision=DomainDecision.objects.filter(scope_key=exp.scope_key,domain=name).first()
    if candidate is None:
        candidate=DomainCandidate(expansion=exp,domain=name,root=root,status=decision.status if decision else 'pending',tags=decision.tags if decision else [])
    previous_evidence=bool(candidate.evidence)
    proofs=_safe_evidence(item.get('evidence',[]))
    unique={digest(p):p for p in candidate.evidence+proofs}
    candidate.evidence=list(unique.values())[:40]
    rel=item.get('relation','related')
    if rel not in RELATIONS:rel='related'
    incoming_score=_confidence(proofs,rel)
    if not previous_evidence or incoming_score>candidate.confidence or (candidate.relation=='related' and rel=='subdomain'):
        candidate.relation=rel
    if first_party(exp.spec,name) and not historical_dns:candidate.relation='subdomain'
    elif candidate.relation=='subdomain':candidate.relation='related'
    candidate.confidence=_confidence(candidate.evidence,candidate.relation)
    candidate.category=category(exp.spec,name,candidate.relation,candidate.evidence)
    path={'kind':seed['kind'],'value':seed['value'],'depth':seed['depth']}
    if path not in candidate.paths:candidate.paths=[*candidate.paths,path][:20]
    candidate.identifiers=list({str(i)[:180] for i in [*candidate.identifiers,*item.get('identifiers',[])[:20]]})[:30]
    status=item.get('dns_status')
    if status in {'resolved','nxdomain','no_answer','error','unchecked'}:candidate.dns_status=status
    candidate.save()
    # Materialize a candidate relationship, never a generic ownership assertion.
    if exp.organization_id:
        org=_active(exp)
        target=store.node('domain',name)
        proof=store.evidence(target,'domain_expansion',{'expansion_id':str(exp.pk),'candidate_id':str(candidate.pk),'evidence':candidate.evidence},run=exp.run)
        store.relation(org,target,'domain_candidate',proof,confidence=candidate.confidence)
    return candidate


@shared_task(name='leakscope.domain_expansion', soft_time_limit=240, time_limit=270)
def run_expansion(expansion_id):
    from .expansion_worker import execute
    return execute(expansion_id, delivery_id=run_expansion.request.id)


def resume(expansion_id, data):
    from .expansion_worker import resume as resume_job
    return resume_job(expansion_id, data)


def selected(expansion, ids):
    ids=strings(ids,500,36)
    if not ids:raise ValueError('Select at least one candidate')
    try:keys={UUID(value) for value in ids}
    except ValueError:raise ValueError('Invalid candidate ID') from None
    rows=list(expansion.candidates.select_for_update().filter(pk__in=keys))
    if len(rows)!=len(keys):raise ValueError('Candidates must belong to this expansion')
    return rows


@transaction.atomic
def review(expansion_id,data):
    snapshot=DomainExpansion.objects.get(pk=expansion_id)
    org=_active(snapshot,lock=True)
    exp=DomainExpansion.objects.select_for_update().get(pk=expansion_id)
    if exp.organization_id!=snapshot.organization_id:raise ValueError('Organization changed; reload the expansion')
    if data.get('organization_id'):
        destination=lifecycle.canonical(lifecycle.organization(data['organization_id'],lock=True))
        if destination.organization_status!='active':raise ValueError('Choose an active organization')
        if org and destination.pk!=org.pk:raise ValueError('Review belongs to the expansion organization')
        org=destination
    action=data.get('action')
    if action not in {'accept','reject','tag'}:raise ValueError('Unsupported review action')
    if action=='accept' and org is None:raise ValueError('Choose an organization to accept domains')
    rows=selected(exp,data.get('candidate_ids',[]))
    tags=strings(data.get('tags',[]),20,64)
    for c in rows:
        if action=='tag':c.tags=sorted(set(c.tags+tags))
        else:c.status='accepted' if action=='accept' else 'rejected'
        c.save(update_fields=['status','tags','updated_at'])
        key=_scope(org.pk,exp.spec) if org else exp.scope_key
        for scope in dict.fromkeys([exp.scope_key,key]):
            previous=DomainDecision.objects.filter(scope_key=scope,domain=c.domain).first()
            status=previous.status if action=='tag' and previous else c.status
            saved_tags=sorted(set(c.tags+(previous.tags if previous else [])))
            DomainDecision.objects.update_or_create(scope_key=scope,domain=c.domain,defaults={'organization':org,'status':status,'tags':saved_tags,'candidate':c,'updated_at':timezone.now()})
        if action=='accept':
            target=store.node('domain',c.domain)
            group=category(exp.spec,c.domain,c.relation,c.evidence)
            association={'services':'uses_service','references':'references_domain','lookalikes':'tracks_lookalike'}.get(group,'user_scope')
            proof=store.evidence(org,'domain_expansion_review',{'domain':c.domain,'candidate_id':str(c.pk),'expansion_id':str(exp.pk),
                'association':association,'assertion':'User accepted domain scope; ownership remains evidence-dependent' if association=='user_scope' else 'User linked a '+group+' relationship; this does not add organizational domain scope',
                'evidence':c.evidence,'tags':c.tags},run=exp.run)
            store.relation(org,target,association,proof)
            store.relation(org,target,'domain_candidate',proof,confidence=c.confidence)
        # Persist every review transition as audit evidence, including rejection/tag changes.
        if org:store.evidence(org,'domain_review_audit',{'event_id':str(uuid4()),'candidate_id':str(c.pk),'domain':c.domain,'action':action,'tags':c.tags},run=exp.run)
    return detail(exp)


@transaction.atomic
def expand(expansion_id,data):
    parent=DomainExpansion.objects.get(pk=expansion_id)
    _active(parent,lock=True)
    parent=DomainExpansion.objects.select_for_update().get(pk=expansion_id)
    rows=selected(parent,data.get('candidate_ids',[]))
    options={key:value for key,value in parent.spec.items() if key!='seeds'}
    options.update({key:value for key,value in data.items() if key not in {'candidate_ids','organization_id','companies','domains','reference'}})
    options['domains']=[row.domain for row in rows];options['companies']=[]
    if parent.organization_id:options['organization_id']=str(parent.organization_id)
    return start(options,parent=parent,prefill=False)


@transaction.atomic
def merge_organizations(source,target):
    target_scope=_scope(target.pk,{})
    source_scope=_scope(source.pk,{})
    DomainExpansion.objects.filter(organization=source).update(organization=target,scope_key=target_scope)
    for decision in DomainDecision.objects.select_for_update().filter(organization=source):
        if decision.scope_key!=source_scope:
            decision.organization=target;decision.save(update_fields=['organization']);continue
        existing=DomainDecision.objects.filter(scope_key=target_scope,domain=decision.domain).first()
        if existing:
            if decision.updated_at>existing.updated_at:
                existing.status=decision.status;existing.tags=sorted(set(existing.tags+decision.tags));existing.candidate=decision.candidate;existing.updated_at=decision.updated_at;existing.save()
            decision.delete()
        else:
            decision.organization=target;decision.scope_key=target_scope;decision.save(update_fields=['organization','scope_key'])
