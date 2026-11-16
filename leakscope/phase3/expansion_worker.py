"""Checkpointed expansion pages. Collection budgets and storage are independent."""
import json
import time
from datetime import timedelta
from uuid import uuid4

from celery.exceptions import Ignore, SoftTimeLimitExceeded
from django.db import transaction
from django.utils import timezone

from leakscope.services.config import get_config
from leakscope.services.runs import _data
from leakscope.services.run_events import emit
from leakscope_app.models import DomainExpansion, DomainSourceCache, ExpansionSourcePage, Run
from . import domain_expansion as engine
from .asset_names import registrable_domain

SLICE_SECONDS = 120
ROOT_SOURCES = {'saved','certificates','crtsh','certspotter','hackertarget','urlscan','registration','brute','commoncrawl','wayback','netlas_dns'}
NATIVE = {'reverse_dns','passive_dns','infrastructure','brute','alterations'}
UNCACHED = {'saved','certificates','variants','dns','reverse_dns','brute','alterations'}


def supports(source, seed, spec=None):
    kind=seed['kind']
    if source=='passive_dns' and kind=='domain' and spec and 'hackertarget' in spec['sources']:return False
    if source in NATIVE:
        from .enumeration_sources import supports as native_supports
        return native_supports(source,seed)
    if source=='gleif':return kind=='company'
    if source in {'crtsh','certspotter','hackertarget','urlscan','certificates','registration','dns','variants','commoncrawl','wayback'}:return kind=='domain'
    if source=='netlas':return kind=='company'
    if source=='netlas_dns':return kind=='domain'
    return kind in {'domain','company','identifier'}


def checkpoint_seed(spec):
    return {'queue':[],'blocked':[],'scheduled':[],'seen_seeds':[],'processed':0,'elapsed':0,'reason':'','current_source':''}


def enqueue(cp, seed, spec):
    if seed.get('depth',0)>spec['max_depth']:return
    for source in spec['sources']:
        if not supports(source,seed,spec):continue
        # Brute-force labels only under explicit domain families, never shared hosts.
        if source in {'brute','alterations'} and not engine.first_party(spec,seed['value']):continue
        query=dict(seed)
        if source in ROOT_SOURCES and seed['kind']=='domain':query['value']=registrable_domain(seed['value'])
        key=engine.digest([source,query['kind'],query['value'].casefold()])
        if key in cp['scheduled']:continue
        cp['scheduled'].append(key)
        cp['queue'].append({'source':source,'seed':query,'cursor':{}})


@transaction.atomic
def _persist(exp, cp, coverage):
    cp['pending_queries']=len(cp.get('queue',[]))+len(cp.get('blocked',[]))
    cp['saved_candidates']=exp.candidates.count()
    updated=DomainExpansion.objects.filter(pk=exp.pk,state='running',execution_token=exp.execution_token).update(checkpoint=cp,coverage=coverage,updated_at=timezone.now())
    if not updated:raise Ignore()
    Run.objects.filter(pk=exp.run_id,state__in=['queued','running']).update(
        state='running',progress_current=cp['processed'],progress_total=cp['processed']+len(cp['queue'])+len(cp['blocked']),updated_at=timezone.now())


def _result(exp):
    exp.refresh_from_db()
    return {'expansion_id':str(exp.pk),'state':exp.state,'partial':exp.partial,'usage':exp.usage}


@transaction.atomic
def _pause(exp,cp,coverage,reason):
    cp['reason']=reason
    _persist(exp,cp,coverage)
    emit(exp.run_id,reason,event='run.paused',level='warning',
        data={'saved_candidates':cp['saved_candidates'],'pending_queries':cp['pending_queries']},execution_token=exp.execution_token)
    if not DomainExpansion.objects.filter(pk=exp.pk,state='running',execution_token=exp.execution_token).update(state='paused',partial=True,updated_at=timezone.now()):raise Ignore()
    Run.objects.filter(pk=exp.run_id,state__in=['queued','running']).update(state='paused',finished_at=timezone.now(),result=_data({'expansion_id':str(exp.pk),'reason':reason}),updated_at=timezone.now())


def _dispatch(exp):
    task_id=exp.run.task_id
    try:engine.run_expansion.apply_async(args=[str(exp.pk)],task_id=task_id)
    except Exception:
        # A failed dispatch remains explicitly resumable; never auto-submit twice.
        with transaction.atomic():
            row=DomainExpansion.objects.select_for_update().get(pk=exp.pk)
            if row.state=='queued' and row.run.task_id==task_id:
                cp=dict(row.checkpoint);cp['reason']='Dispatch failed or delivery was unconfirmed; resume checks the saved checkpoint.'
                row.checkpoint=cp;row.state='paused';row.partial=True;row.save(update_fields=['checkpoint','state','partial','updated_at'])
                Run.objects.filter(pk=row.run_id).update(state='paused',updated_at=timezone.now())


@transaction.atomic
def _yield(exp,cp,coverage):
    cp['reason']='Continuing from the saved checkpoint';cp['in_flight']=None
    _persist(exp,cp,coverage)
    emit(exp.run_id,'Worker slice completed; continuing from saved work',event='worker.yielded',
        data={'pending_queries':cp['pending_queries'],'saved_candidates':cp['saved_candidates']},execution_token=exp.execution_token)
    with transaction.atomic():
        row=DomainExpansion.objects.select_for_update().get(pk=exp.pk)
        if row.state!='running' or row.execution_token!=exp.execution_token:raise Ignore()
        row.state='queued';row.save(update_fields=['state','updated_at'])
        task_id=str(uuid4())
        Run.objects.filter(pk=row.run_id).update(task_id=task_id,state='queued',updated_at=timezone.now())
        row.run.task_id=task_id
        transaction.on_commit(lambda:_dispatch(row))


@transaction.atomic
def resume(expansion_id,data):
    if not isinstance(data,dict) or set(data)-{'budgets','max_candidates','runtime_seconds'}:raise ValueError('Unsupported resume options')
    snapshot=DomainExpansion.objects.get(pk=expansion_id)
    engine._active(snapshot,lock=True)
    exp=DomainExpansion.objects.select_for_update(of=('self',)).select_related('run','organization').get(pk=expansion_id)
    if exp.organization_id!=snapshot.organization_id:raise ValueError('Organization changed; reload research')
    cp=dict(exp.checkpoint)
    if exp.spec['demo'] or exp.state not in {'paused','lost','failed','cancelled'} or not (cp.get('queue') or cp.get('blocked') or cp.get('pending')):
        raise ValueError('This expansion has no resumable checkpoint')
    spec=dict(exp.spec);budgets=dict(spec['budgets']);incoming=data.get('budgets',{})
    if not isinstance(incoming,dict) or set(incoming)-set(engine.DEFAULT_BUDGETS):raise ValueError('Invalid request budgets')
    for key,value in incoming.items():
        value=engine.integer(value,0,engine.MAX_BUDGETS[key],key+' budget')
        if value<exp.usage.get(key,0):raise ValueError('A request limit cannot be lower than recorded usage')
        budgets[key]=value
    spec['budgets']=budgets
    if 'max_candidates' in data:spec['max_candidates']=engine.integer(data['max_candidates'],0,10000000,'storage quota')
    if spec['max_candidates'] and spec['max_candidates']<exp.candidates.count():raise ValueError('Storage quota cannot be lower than saved candidates')
    if 'runtime_seconds' in data:spec['runtime_seconds']=engine.integer(data['runtime_seconds'],60,86400,'runtime seconds')
    if spec.get('runtime_seconds',1800)<=cp.get('elapsed',0):raise ValueError('Increase the runtime ceiling to resume')
    # A crash after reservation has an ambiguous network outcome. Do not replay
    # that page automatically; retain its coverage as uncertain and continue.
    uncertain=cp.pop('in_flight',None)
    before=cp.get('in_flight_usage',{})
    http_reserved=any(exp.usage.get(key,0)>before.get(key,0) for key in engine.DEFAULT_BUDGETS if key!='dns')
    if uncertain and http_reserved and not ExpansionSourcePage.objects.filter(expansion=exp,key=uncertain).exists() and cp.get('queue'):
        job=cp['queue'].pop(0);key=engine.digest([job['source'],job['seed']['kind'],job['seed']['value']])
        coverage=dict(exp.coverage);entry=coverage.get(key,{'source':job['source'],'seed':job['seed']['value'],'kind':job['seed']['kind'],'pages':0,'requests':0,'candidates':0})
        entry.update(state='partial',notes=['Interrupted request outcome is unknown; this page was not automatically requested again.'])
        coverage[key]=entry;exp.coverage=coverage
        exp.partial=True
        exp.notes=list(dict.fromkeys([*exp.notes,'Interrupted HTTP request outcome is unknown; its page was skipped to avoid repeating a potentially charged request.']))
    cp['queue']=[*cp.get('queue',[]),*cp.get('blocked',[])];cp['blocked']=[];cp['reason']=''
    exp.spec=spec;exp.checkpoint=cp;exp.state='queued';exp.execution_token=''
    exp.save(update_fields=['spec','checkpoint','coverage','state','execution_token','notes','partial','updated_at'])
    task_id=str(uuid4())
    Run.objects.filter(pk=exp.run_id).update(task_id=task_id,state='queued',finished_at=None,error='',parameters=_data(spec),updated_at=timezone.now())
    exp.run.task_id=task_id
    emit(exp.run_id,'Expansion resumed from its saved checkpoint',event='run.resumed',
        data={'pending_queries':len(cp['queue']),'budgets':budgets,'usage':exp.usage},dedupe_key='resumed:'+task_id)
    transaction.on_commit(lambda:_dispatch(exp))
    exp.run.refresh_from_db()
    return exp


def _context(exp,cp,deadline):
    spec=exp.spec
    leads=DomainExpansion.objects.values_list('leads',flat=True).get(pk=exp.pk)
    return {**spec.get('context',{}),'demo':spec['demo'],'max_candidates':spec['max_candidates'],
        'company_names':list(dict.fromkeys([s['value'] for s in spec['seeds'] if s['kind']=='company']+[x['value'] for x in leads if x.get('kind')=='company'])),
        'seed_domains':[s['value'] for s in spec['seeds'] if s['kind']=='domain'],
        'dns_rate':spec.get('dns_rate',20),'wordlist':spec.get('wordlist',[]),'web_results':spec.get('web_results',20),
        'commoncrawl_index':spec.get('commoncrawl_index','CC-MAIN-2026-43'),'netlas_indices':spec.get('netlas_indices',[]),'run_id':str(exp.run_id),'deadline':deadline}


def _collect(exp,job,cp,budget,context,config):
    from . import expansion_sources, passive_sources, enumeration_sources, historical_sources, netlas_dns_history
    source=job['source'];seed=job['seed'];context={**context,'cursor':job['cursor']}
    if seed['kind']=='company' and seed['value'] not in context['company_names']:context['company_names']=[*context['company_names'],seed['value']]
    safe_context={key:value for key,value in context.items() if key not in {'deadline','run_id','max_candidates'}}
    if source=='netlas_dns':
        key=engine.digest(['netlas-dns-fields-v2',seed['value'],context.get('netlas_indices'),context.get('demo'),job['cursor'],config.get('NETLAS_API_KEY','')])
    elif source=='commoncrawl':
        key=engine.digest(['commoncrawl-index-v1',seed['value'],context.get('commoncrawl_index'),context.get('demo'),job['cursor']])
    elif source=='wayback':
        key=engine.digest(['wayback-index-v1',seed['value'],context.get('demo'),job['cursor']])
    elif source in engine.PASSIVE_SOURCES and source!='gleif':
        credential=config.get({'certspotter':'CERTSPOTTER_API_KEY','urlscan':'URLSCAN_API_KEY'}.get(source,''),'')
        key=engine.digest(['expansion-v4-pages',source,seed['value'],context.get('demo'),job['cursor'],credential])
    else:key=engine.digest(['expansion-v4-pages',source,seed['kind'],seed['value'],safe_context,config])
    cached=DomainSourceCache.objects.filter(pk=key,expires_at__gt=timezone.now()).first() if source not in UNCACHED else None
    if cached:
        data=passive_sources.normalize_result(cached.data) if source in engine.PASSIVE_SOURCES else cached.data
        return data,True
    adapter=netlas_dns_history.collect if source=='netlas_dns' else historical_sources.collect if source in {'commoncrawl','wayback'} else enumeration_sources.collect if source in NATIVE else passive_sources.collect if source in engine.PASSIVE_SOURCES else expansion_sources.collect
    result=adapter(source,seed,config=config,spend=budget,context=context)
    if not isinstance(result,dict) or not isinstance(result.get('candidates',[]),list):raise ValueError('Malformed source output')
    if source in engine.PASSIVE_SOURCES:result=passive_sources.normalize_result(result)
    if source not in UNCACHED and not budget.refused and not result.get('blocked') and (result.get('cacheable') is True or not result.get('notes')):
        DomainSourceCache.objects.update_or_create(pk=key,defaults={'data':json.loads(_data(result)),'expires_at':timezone.now()+timedelta(hours=6)})
    return result,False


def _materialize(exp,cp,coverage,deadline):
    """Save a durable response page in small, idempotent chunks."""
    pending=cp['pending'];page=ExpansionSourcePage.objects.get(pk=pending['page'],expansion=exp)
    seed=page.seed;data=page.data;spec=exp.spec
    cp['current_action']='saving results'
    initial_index=pending.get('index',0)
    def saved_event():
        if pending.get('index',0)<=initial_index:return
        count=exp.candidates.count()
        emit(exp.run_id,str(count)+' candidates saved so far',event='candidates.saved',
            data={'source':page.source,'saved_candidates':count,'page_processed':pending.get('index',0),'page_candidates':len(data.get('candidates',[]))},
            execution_token=exp.execution_token,dedupe_key='page:'+str(page.pk)+':saved:'+str(pending.get('index',0)))
    for i in range(pending.get('index',0),len(data.get('candidates',[]))):
        if time.monotonic()>=deadline:
            saved_event();return 'yield'
        try:candidate=engine.save_candidate(exp.pk,data['candidates'][i],seed,execution_token=exp.execution_token)
        except engine.StorageLimitReached:
            saved_event();return 'storage'
        pending['index']=i+1
        if candidate and candidate.status!='rejected' and candidate.category in {'domains','related'} and seed['depth']<spec['max_depth']:
            for value in dict.fromkeys([candidate.root,candidate.domain.removeprefix('*.')]):enqueue(cp,{'kind':'domain','value':value,'depth':seed['depth']+1},spec)
        if (i+1)%50==0:
            _persist(exp,cp,coverage);saved_event()
        if spec['demo'] and exp.candidates.exists():
            saved_event();return 'demo'
    saved_event()
    if not pending.get('leads_done'):
        leads=engine.save_leads(exp.pk,data.get('leads',[]),execution_token=exp.execution_token)
        for lead in leads:
            if seed['depth']<spec['max_depth'] and (engine.auto_lead(lead) or lead['kind'] in {'ip','network','asn'}):
                enqueue(cp,{'kind':lead['kind'],'value':lead['value'],'depth':seed['depth']+1},spec)
        pending['leads_done']=True
    job=cp['queue'].pop(0)
    continuation=data.get('continuation')
    if (isinstance(continuation,dict) or data.get('blocked')) and not spec['demo']:
        retry={**job,'cursor':continuation if isinstance(continuation,dict) else job['cursor'],'attempt':job.get('attempt',0)+1}
        # Detect a provider cursor that cannot advance, without infinite loops.
        if continuation==job['cursor'] and not data.get('blocked'):
            engine.note(exp.pk,page.source+' returned a repeated page cursor; collection stopped for this query',execution_token=exp.execution_token)
        elif data.get('blocked')=='budget':cp['blocked'].append(retry)
        else:cp['queue'].append(retry)
    cp['pending']=None;cp['in_flight']=None
    identity=[seed['kind'],seed['value']]
    if identity not in cp['seen_seeds']:cp['seen_seeds'].append(identity)
    cp['processed']=len(cp['seen_seeds'])
    cp['source_queries_completed']=cp.get('source_queries_completed',0)+1
    cp['current_action']='waiting for next source'
    return 'done'


def execute(expansion_id,delivery_id=None):
    with transaction.atomic():
        exp=DomainExpansion.objects.select_for_update(of=('self',)).select_related('run','organization').get(pk=expansion_id)
        if exp.state!='queued' or (delivery_id and delivery_id!=exp.run.task_id):raise Ignore()
        exp.state='running';exp.execution_token=str(uuid4());exp.save(update_fields=['state','execution_token','updated_at'])
        Run.objects.filter(pk=exp.run_id,state__in=['queued','running']).update(state='running',started_at=exp.run.started_at or timezone.now(),updated_at=timezone.now())
    cp=dict(exp.checkpoint) if exp.checkpoint else checkpoint_seed(exp.spec)
    coverage=dict(exp.coverage)
    if not cp['scheduled']:
        for seed in exp.spec['seeds']:enqueue(cp,seed,exp.spec)
    cp.setdefault('source_queries_completed',sum(entry.get('pages',0) for entry in coverage.values()))
    for source in exp.spec['sources']:
        if not any(entry['source']==source for entry in coverage.values()):
            coverage['unused:'+source]={'source':source,'seed':'','kind':'','state':'skipped','pages':0,'requests':0,'candidates':0,'notes':['No applicable seed has reached this source yet.']}
    started=time.monotonic();elapsed_base=cp.get('elapsed',0)
    remaining=exp.spec.get('runtime_seconds',1800)-elapsed_base
    deadline=started+max(0,min(SLICE_SECONDS,remaining))
    budget=engine.Budget(exp.pk,deadline,execution_token=exp.execution_token)
    def checkpoint():
        cp['elapsed']=elapsed_base+max(0,time.monotonic()-started)
        _persist(exp,cp,coverage)
    try:
        engine._active(exp);config=get_config()['config']
        emit(exp.run_id,'Expansion worker is processing saved source work',event='worker.started',
            data={'pending_queries':len(cp['queue'])},execution_token=exp.execution_token)
        while cp['queue'] or cp.get('pending'):
            if time.monotonic()>=deadline:
                checkpoint()
                if cp['elapsed']>=exp.spec.get('runtime_seconds',1800):_pause(exp,cp,coverage,'Runtime ceiling reached; increase it to continue from this checkpoint')
                else:_yield(exp,cp,coverage)
                return _result(exp)
            if cp.get('pending'):
                outcome=_materialize(exp,cp,coverage,deadline)
                checkpoint()
                if outcome=='storage':
                    _pause(exp,cp,coverage,'Storage quota reached; collected source pages are retained. Increase the quota or set it to 0 to continue.')
                    return _result(exp)
                if outcome=='demo':
                    cp['queue']=[];cp['blocked']=[];cp['pending']=None;checkpoint();break
                continue
            job=cp['queue'][0];source=job['source'];seed=job['seed']
            cp['current_source']=source
            cp['current_seed']=seed['value'];cp['current_action']='collecting evidence'
            cp['source_started_at']=timezone.now().isoformat()
            page_key=engine.digest([source,seed['kind'],seed['value'],job['cursor'],job.get('attempt',0)])
            existing=ExpansionSourcePage.objects.filter(expansion=exp,key=page_key).first()
            if existing:
                cp['pending']={'page':existing.pk,'index':0};checkpoint();continue
            key=engine.digest([source,seed['kind'],seed['value']])
            coverage.pop('unused:'+source,None)
            entry=coverage.setdefault(key,{'source':source,'seed':seed['value'],'kind':seed['kind'],'pages':0,'requests':0,'candidates':0,'notes':[]})
            entry['state']='running';cp['in_flight']=page_key
            cp['in_flight_usage']=dict(DomainExpansion.objects.get(pk=exp.pk).usage)
            checkpoint()
            before=DomainExpansion.objects.get(pk=exp.pk).usage
            emit(exp.run_id,'Starting '+source+' for '+seed['value'],event='source.started',
                data={'source':source,'seed':seed['value'],'kind':seed['kind']},execution_token=exp.execution_token)
            source_started=time.monotonic()
            budget.refused=False;budget.denied.clear()
            try:
                result,cached=_collect(exp,job,cp,budget,_context(exp,cp,deadline),config)
                if budget.denied:
                    result['blocked']='budget'
                    # A legacy multi-call collector can have useful partial output.
                    # Updated adapters preserve the exact next-call cursor.
                    result.setdefault('continuation',job['cursor'])
                elif time.monotonic()>=deadline and (budget.refused or result.get('blocked')):
                    result['blocked']='slice';result.setdefault('continuation',job['cursor'])
            except SoftTimeLimitExceeded:raise
            except Exception:
                result={'candidates':[],'error':'source_failed','notes':[source+' source failed; saved results and other sources are retained. No automatic retry was made.']};cached=False
            safe=json.loads(_data(result))
            with transaction.atomic():
                current=DomainExpansion.objects.select_for_update().get(pk=exp.pk)
                if current.execution_token!=exp.execution_token or current.state!='running':raise Ignore()
                page,_=ExpansionSourcePage.objects.get_or_create(expansion=exp,key=page_key,defaults={'source':source,'seed':seed,'data':safe})
            after=DomainExpansion.objects.get(pk=exp.pk).usage
            entry['pages']+=1;entry['requests']+=sum(max(0,after.get(k,0)-before.get(k,0)) for k in engine.DEFAULT_BUDGETS)
            entry['candidates']+=len(safe.get('candidates',[]));entry['cached']=cached
            entry['notes']=list(dict.fromkeys([*entry['notes'],*[str(x)[:500] for x in safe.get('notes',[])]]))
            entry['state']='failed' if safe.get('error') or safe.get('failed') else 'paused' if safe.get('blocked') else 'partial' if safe.get('notes') else 'completed'
            emit(exp.run_id,source+' '+entry['state']+': '+str(len(safe.get('candidates',[])))+' candidate names returned',
                event='source.failed' if entry['state']=='failed' else 'source.completed',
                level='error' if entry['state']=='failed' else 'warning' if entry['state'] in {'paused','partial'} else 'info',
                data={'source':source,'seed':seed['value'],'state':entry['state'],'candidates':len(safe.get('candidates',[])),
                    'requests':sum(max(0,after.get(k,0)-before.get(k,0)) for k in engine.DEFAULT_BUDGETS),'cached':cached,
                    'elapsed_seconds':round(max(0,time.monotonic()-source_started),3),'notes':safe.get('notes',[])},execution_token=exp.execution_token)
            for message in safe.get('notes',[]):engine.note(exp.pk,str(message)[:500],execution_token=exp.execution_token)
            cp['pending']={'page':page.pk,'index':0};cp['in_flight']=None;checkpoint()
        checkpoint()
        if cp['blocked']:
            _pause(exp,cp,coverage,'Request budgets reached. Other available sources finished; increase the relevant limits to resume remaining pages.')
        else:
            cp['reason']='All selected source queries finished';cp['current_source']='';cp['current_seed']='';cp['current_action']='completed';checkpoint()
            engine.finish(exp.pk,'succeeded',execution_token=exp.execution_token)
    except Ignore:return _result(exp)
    except SoftTimeLimitExceeded:
        # The last persisted in-flight marker prevents a possibly charged page
        # from being silently replayed after a worker interruption.
        current=DomainExpansion.objects.get(pk=exp.pk)
        if current.execution_token!=exp.execution_token:return _result(exp)
        cp=current.checkpoint
        cp['elapsed']=elapsed_base+max(0,time.monotonic()-started)
        _pause(exp,cp,coverage,'Worker time slice was interrupted; saved work can be resumed')
    except ValueError:
        engine.finish(exp.pk,'cancelled','Organization became inactive during research',execution_token=exp.execution_token)
    except Exception:
        engine.finish(exp.pk,'failed','Research stopped unexpectedly; saved checkpoint and results are retained',execution_token=exp.execution_token)
    return _result(exp)
