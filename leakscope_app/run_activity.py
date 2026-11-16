"""On-demand job activity. Reads do not start or restart collection."""
from uuid import UUID
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from leakscope_app.models import Run,RunEvent,DomainExpansion
from leakscope.services import run_events
from leakscope.services.runs import serialize_run,COOPERATIVE_KINDS
from .workbench import endpoint,_body


def _number(request,name,default,minimum,maximum):
    value=request.GET.get(name,str(default))
    if not value.isascii() or not value.isdigit() or not minimum<=int(value)<=maximum:raise ValueError('Invalid '+name)
    return int(value)


@endpoint()
def events(request,run_id):
    run=Run.objects.get(pk=UUID(str(run_id)))
    after=_number(request,'after',0,0,9223372036854775807)
    limit=_number(request,'limit',100,1,500)
    rows=list(RunEvent.objects.filter(run=run,id__gt=after).order_by('id')[:limit+1])
    visible=rows[:limit]
    return JsonResponse(run_events.sanitize({'events':[run_events.event_data(row,redacted=False) for row in visible],
        'next_cursor':visible[-1].pk if visible else after,'has_more':len(rows)>limit,
        'run':serialize_run(run),'snapshot':run_events.snapshot(run)}))


@endpoint(('POST',))
def cancel(request,run_id):
    if _body(request):raise ValueError('Cancellation takes no options')
    identifier=UUID(str(run_id))
    # Organization/expansion writers lock expansion before Run, so this endpoint
    # follows that order by letting the expansion engine own its cancellation.
    run=Run.objects.get(pk=identifier)
    if run.kind not in COOPERATIVE_KINDS:
        raise ValueError('Safe cancellation is not supported for this job type; it has not been marked cancelled. Activity and saved output remain available.')
    if run.kind=='domain_expansion':
        from leakscope.phase3.domain_expansion import finish
        expansion=DomainExpansion.objects.filter(run=run).first()
        if expansion:finish(expansion.pk,'cancelled','Stopped at user request; saved results and checkpoint retained')
        else:Run.objects.filter(pk=run.pk,state__in=['queued','running','paused']).update(state='cancelled',finished_at=timezone.now(),updated_at=timezone.now())
    else:
        Run.objects.filter(pk=run.pk,state__in=['queued','running','paused']).update(state='cancelled',finished_at=timezone.now(),updated_at=timezone.now())
    run.refresh_from_db()
    if run.state=='cancelled':
        run_events.emit(run,'Stopped at user request. Saved output is retained.',event='cancelled',dedupe_key='user-cancelled:'+run.task_id)
        try:
            from leakscope.celery import app
            app.control.revoke(run.task_id,terminate=False)
        except Exception:
            run_events.emit(run,'Stop is saved; worker revoke could not be confirmed. Cooperative cancellation remains in effect.',event='warning',level='warning',dedupe_key='revoke-unconfirmed:'+run.task_id)
    return JsonResponse({'run':run_events.sanitize(serialize_run(run)),'snapshot':run_events.snapshot(run),
        'cancellation':{'requested':run.state=='cancelled','mode':'cooperative','message':'An in-flight operation may finish before the worker observes cancellation.'}})
