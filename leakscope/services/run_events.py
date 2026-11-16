"""Durable, redacted application events. Event writes never stop collection."""
import json
import math
import re
from uuid import UUID

from celery.exceptions import SoftTimeLimitExceeded, TaskPredicate
from django.db import DatabaseError, transaction
from leakscope_app.models import Run, RunEvent, DomainExpansion
from . import config

SENSITIVE = re.compile(r'api.?key|password|passwd|secret|credential|authorization|cookie|token|private.?key',re.I)
URL_CREDENTIALS = re.compile(r'(?<![a-z0-9+.-])([a-z][a-z0-9+.-]*://)[^\s/@]+@',re.I)
BEARER = re.compile(r'\b(Bearer|Basic)\s+[^\s,;"<>]+',re.I)
QUERY_SECRET = re.compile(r'([?&](?:api[_-]?key|key|token|secret|password|access_token|auth)=)[^&#\s"<>]*',re.I)
ASSIGNMENT = re.compile(r'\b(api[_-]?key|password|passwd|secret|access[_-]?token|authorization|cookie|set-cookie|credential|token)["\x27]?\s*[:=]\s*(?:"[^"]*"|\x27[^\x27]*\x27|[^\s,;]+)',re.I)


def sanitize(value, *, preserve_metrics=False):
    """Mask credentials, optionally retaining numeric usage metrics in evidence."""
    secrets=[]
    try:
        def collect(data):
            for key,item in data.items():
                if isinstance(item,dict):collect(item)
                elif SENSITIVE.search(str(key)) and isinstance(item,str) and item:secrets.append(item)
        collect(config.get_config()['config'])
    except (DatabaseError,ValueError,TypeError,KeyError):pass
    secrets.sort(key=len,reverse=True)
    def clean(item,depth=0):
        if depth>12:return '[nested output omitted]'
        if isinstance(item,str):
            text=config.safe_text(item)
            for secret in secrets:text=text.replace(secret,'[REDACTED]')
            text=URL_CREDENTIALS.sub(r'\1[REDACTED]@',text)
            text=BEARER.sub(r'\1 [REDACTED]',text)
            text=QUERY_SECRET.sub(r'\1[REDACTED]',text)
            return ASSIGNMENT.sub(lambda m:m.group(1)+'=[REDACTED]',text)
        if isinstance(item,dict):
            metrics={'token_count','tokens_used','input_tokens','output_tokens','total_tokens','prompt_tokens','completion_tokens','max_tokens','remaining_tokens'}
            def sensitive(key,value):
                numeric_metric=preserve_metrics and str(key).casefold() in metrics and isinstance(value,(int,float)) and not isinstance(value,bool)
                return SENSITIVE.search(str(key)) and not numeric_metric
            return {config.safe_text(key):'[REDACTED]' if sensitive(key,value) else clean(value,depth+1) for key,value in item.items()}
        if isinstance(item,(list,tuple)):return [clean(value,depth+1) for value in item]
        if isinstance(item,float) and not math.isfinite(item):return None
        if item is None or isinstance(item,(bool,int,float)):return item
        return clean(str(item),depth+1)
    return clean(value)


def emit(run_or_id,message,*,level='info',event='activity',data=None,task_id=None,dedupe_key=None,execution_token=None):
    """Append one event; a stale expansion delivery cannot append after resume."""
    try:
        identifier=run_or_id.pk if isinstance(run_or_id,Run) else UUID(str(run_or_id))
        with transaction.atomic():
            if execution_token is not None:
                expansion=DomainExpansion.objects.select_for_update().filter(run_id=identifier).only('state','execution_token').first()
                if not expansion or expansion.state!='running' or expansion.execution_token!=execution_token:return None
            row=Run.objects.select_for_update().only('pk','task_id','state').filter(pk=identifier).first()
            if row is None or task_id is not None and row.task_id!=task_id:return None
            if event in {'started','progress'} and row.state in {'succeeded','failed','cancelled','skipped','lost','paused'}:return None
            safe=sanitize({'message':str(message),'data':data if data is not None else {}})
            fields={'level':level if level in {'debug','info','warning','error'} else 'info',
                'event':re.sub(r'[^a-z0-9_.-]','_',str(event).lower())[:64] or 'activity',
                'message':safe['message'][:4000],'data':safe['data']}
            key=str(dedupe_key or '')[:160]
            if key:return RunEvent.objects.get_or_create(run=row,dedupe_key=key,defaults=fields)[0]
            return RunEvent.objects.create(run=row,**fields)
    except (SoftTimeLimitExceeded,TaskPredicate):
        raise
    except Exception:
        # Missing schema during an additive rollout or unavailable diagnostics
        # must not roll back evidence or consume collection budgets differently.
        return None


def expansion_activity(run,*,coverage=False):
    if run.kind!='domain_expansion':return None
    keys=['id','state','usage','notes','updated_at','checkpoint__current_source','checkpoint__current_seed','checkpoint__current_action','checkpoint__source_queries_completed','checkpoint__pending_queries','checkpoint__processed','checkpoint__elapsed']
    if coverage:keys+=['coverage']
    row=DomainExpansion.objects.filter(run_id=run.pk).values(*keys).first()
    if not row:return None
    from leakscope_app.models import DomainCandidate
    result={'id':str(row['id']),'state':row['state'],'saved_candidates':DomainCandidate.objects.filter(expansion_id=row['id']).count(),
        'current_source':row.get('checkpoint__current_source') or '', 'current_seed':row.get('checkpoint__current_seed') or '',
        'current_action':row.get('checkpoint__current_action') or '', 'source_queries_completed':row.get('checkpoint__source_queries_completed') or 0,
        'pending_queries':row.get('checkpoint__pending_queries'),'processed':row.get('checkpoint__processed') or 0,
        'elapsed_seconds':row.get('checkpoint__elapsed') or 0,'usage':row['usage'],'last_activity_at':row['updated_at'].isoformat()}
    if coverage:
        entries=list((row.get('coverage') or {}).values())
        result.update(notes=row['notes'],coverage=entries[:100],coverage_total=len(entries),coverage_truncated=len(entries)>100)
    return sanitize(result)


def snapshot(run):
    from .runs import _parse
    result={'result':sanitize(_parse(run.result)),'error':sanitize(run.error),'state':run.state}
    expansion=expansion_activity(run,coverage=True)
    if expansion:result['expansion']=expansion
    return result


def event_data(row,*,redacted=True):
    data={'id':row.pk,'created_at':row.created_at.isoformat(),'level':row.level,'event':row.event,'message':row.message,'data':row.data}
    return sanitize(data) if redacted else data
