"""Durable job records, independent of Redis metadata retention."""
import json
import hashlib
from datetime import timedelta
from uuid import uuid4
from celery import signals
from celery.result import AsyncResult
from django.conf import settings
from django.db import DatabaseError
from django.utils import timezone
from leakscope_app.models import Run
from .config import get_config, redact, safe_text

TERMINAL={'succeeded','failed','skipped','cancelled','lost'}
COOPERATIVE_KINDS={'domain_expansion','cloud','correlation_index','gallery_index','evidence_render','workflow'}
# These own their lifecycle across several Celery slices: one slice succeeding is not the job succeeding.
SELF_MANAGED_KINDS={'domain_expansion','workflow'}
_STATES={'PENDING':'queued','RECEIVED':'queued','STARTED':'running','PROGRESS':'running','SUCCESS':'succeeded','FAILURE':'failed','REVOKED':'cancelled','SKIPPED':'skipped'}


def _data(value):
    config=get_config()['config']
    secrets=[]
    def collect(mapping):
        for key,item in mapping.items():
            if isinstance(item,dict):collect(item)
            elif isinstance(item,str) and item and any(word in str(key).upper() for word in ('KEY','PASSWORD','TOKEN','SECRET')):secrets.append(item)
    collect(config)
    secrets.sort(key=len,reverse=True)
    def text(item):
        item=safe_text(item)
        for secret in secrets:item=item.replace(secret,'[REDACTED]')
        return item
    def clean(item,depth=0):
        if depth>16:return '[nested metadata omitted]'
        if isinstance(item,str):return text(item)
        if isinstance(item,dict):return {text(str(key)):clean(v,depth+1) for key,v in item.items()}
        if isinstance(item,(list,tuple)):return [clean(v,depth+1) for v in item]
        if isinstance(item,float):
            import math
            return item if math.isfinite(item) else None
        return item
    return json.dumps(clean(value),default=lambda item:text(str(item)),allow_nan=False)


def _parse(value):
    try:return json.loads(value)
    except (ValueError,TypeError):return {}


def write_state(task_id,state,meta=None):
    if not isinstance(task_id,str) or not task_id:return
    try:
        run=Run.objects.filter(task_id=task_id).first()
        if run is None or run.state in TERMINAL:return
        # Expansion owns its durable lifecycle across multiple Celery slices.
        # A successful slice can leave the same research queued or paused.
        if run.kind in SELF_MANAGED_KINDS and state=='SUCCESS':return
        mapped=_STATES.get(state)
        if mapped is None:return
        if state=='FAILURE':
            from leakscope.phase3.capture_diagnostics import CaptureBatchError
            payload=({'error':redact(meta),**meta.capture_result} if isinstance(meta,CaptureBatchError) else
                     meta if isinstance(meta,dict) else {'error':redact(meta)})
        else:payload=meta if isinstance(meta,dict) else {'value':meta}
        if state=='SUCCESS' and isinstance(payload,dict) and (payload.get('requires_active') or payload.get('skipped')):mapped='skipped'
        run.state=mapped
        if mapped=='running' and run.started_at is None:run.started_at=timezone.now()
        if state=='PROGRESS':
            run.progress_current=max(0,int(payload.get('current',0) or 0))
            run.progress_total=max(0,int(payload.get('total',0) or 0))
            previous=_parse(run.result)
            run.result=_data({**(previous if isinstance(previous,dict) else {}),**payload})
        if mapped in TERMINAL:
            run.finished_at=timezone.now()
            run.result=_data(payload)
            if mapped=='failed':run.error=redact(payload.get('error') or payload.get('exc_message') or 'Job failed')
        values = {name: getattr(run,name) for name in ('state','started_at','finished_at','result','error','progress_current','progress_total')}
        values['updated_at'] = timezone.now()
        # The conditional update prevents late progress from racing a terminal
        # write and restoring a completed run to running.
        changed=Run.objects.filter(pk=run.pk,task_id=task_id,state__in=['queued','running']).update(**values)
        if changed:
            from .run_events import emit
            if state=='PROGRESS':
                signature=hashlib.sha256(_data(payload).encode()).hexdigest()
                emit(run,'Progress updated: '+str(run.progress_current)+' / '+str(run.progress_total) if run.progress_total else 'Progress updated',
                    event='progress',data=payload,task_id=task_id,dedupe_key='progress:'+task_id+':'+signature)
            elif mapped=='running':
                emit(run,'Background worker started this job.',event='started',task_id=task_id,dedupe_key='started:'+task_id)
            elif mapped in TERMINAL:
                emit(run,{'succeeded':'Job completed.','failed':'Job failed.','cancelled':'Job cancelled.','skipped':'Job skipped.','lost':'Worker stopped reporting.'}.get(mapped,'Job finished.'),
                    event='completed' if mapped=='succeeded' else mapped,level='error' if mapped=='failed' else 'info',data=payload,
                    task_id=task_id,dedupe_key='finished:'+task_id)
    except (DatabaseError,ValueError,TypeError):
        # Initial migration may import the task module before Run exists.
        return


def publish_state(task,state=None,meta=None,**kwargs):
    task_id=getattr(getattr(task,'request',None),'id',None)
    write_state(task_id,state,meta)
    safe_meta=_parse(_data(meta))
    if state=='FAILURE':
        error=(safe_meta or {}).get('error','Job failed') if isinstance(safe_meta,dict) else str(safe_meta)
        safe_meta={'exc_type':'RuntimeError','exc_message':[redact(error)],'exc_module':'builtins'}
    return task.update_state(state=state,meta=safe_meta,**kwargs)


def enqueue(task, *, kind='discover', type='', provider='', parameters=None, args=None, kwargs=None):
    task_id=str(uuid4())
    run=Run.objects.create(task_id=task_id,kind=kind,type=type or '',provider=provider or '',parameters=_data(parameters or {}))
    from .run_events import emit
    emit(run,'Job queued for a background worker.',event='queued',dedupe_key='queued:'+task_id)
    try:task.apply_async(args=list(args or []),kwargs=dict(kwargs or {}),task_id=task_id)
    except Exception as exc:
        write_state(task_id,'FAILURE',{'error':redact(exc),'category':'dispatch'})
    run.refresh_from_db()
    return run


def get_run(run_id, refresh=True):
    run = Run.objects.get(pk=run_id)
    if not refresh or run.state in TERMINAL or not run.task_id:
        return run
    meta = None
    try:
        meta = AsyncResult(run.task_id).backend.get_task_meta(run.task_id)
    except Exception:
        # Broker outages preserve the record but do not disable its deadline.
        pass
    state = meta.get('status', 'PENDING') if isinstance(meta, dict) else 'PENDING'
    timeout = int(getattr(settings, 'LEAKSCOPE_RUN_TIMEOUT', 3600))
    reference = run.updated_at if run.kind in SELF_MANAGED_KINDS else run.started_at or run.created_at
    expired = timezone.now() - reference > timedelta(seconds=timeout)
    # A workflow's first slice succeeding says nothing about the workflow: it stays subject to the deadline,
    # so a polling chain that died is reported instead of showing as running.
    if _STATES.get(state) in TERMINAL and not (run.kind=='workflow' and state=='SUCCESS'):
        write_state(run.task_id, state, meta.get('result'))
    elif expired:
        changed=Run.objects.filter(pk=run.pk, task_id=run.task_id, updated_at=run.updated_at, state__in=['queued', 'running']).update(
            state='lost', error='Worker state expired or deadline exceeded', finished_at=timezone.now())
        if changed:
            from .run_events import emit
            emit(run,'Worker state expired or deadline exceeded; saved output remains available.',event='lost',level='warning',dedupe_key='lost:'+run.task_id)
    elif state != 'PENDING' and not (run.kind=='workflow' and state=='SUCCESS'):
        write_state(run.task_id, state, meta.get('result'))
    run.refresh_from_db()
    return run


def serialize_run(run, *, include_activity=True):
    from .run_events import expansion_activity
    activity=expansion_activity(run) if include_activity else None
    return {'id':str(run.pk),'task_id':run.task_id,'kind':run.kind,'type':run.type,'provider':run.provider,
            'state':run.state,'can_cancel':run.kind in COOPERATIVE_KINDS and run.state in {'queued','running','paused'},'parameters':_parse(run.parameters),'result':_parse(run.result),'error':run.error,
            'progress_current':run.progress_current,'progress_total':run.progress_total,
            'progress_mode':'indeterminate' if run.kind=='domain_expansion' or not run.progress_total else 'determinate',
            **({'activity':activity} if activity else {}),
            'created_at':run.created_at.isoformat(),'started_at':run.started_at.isoformat() if run.started_at else None,
            'finished_at':run.finished_at.isoformat() if run.finished_at else None,'updated_at':run.updated_at.isoformat()}


@signals.task_prerun.connect(weak=False)
def _started(task_id=None,**kwargs):
    write_state(task_id,'STARTED',{})


@signals.task_postrun.connect(weak=False)
def _finished(task_id=None,state=None,retval=None,**kwargs):
    if state in ('SUCCESS','FAILURE','REVOKED'):write_state(task_id,state,retval)


@signals.task_failure.connect(weak=False)
def _failed(task_id=None,exception=None,**kwargs):
    write_state(task_id,'FAILURE',exception)
