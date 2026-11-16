"""Validated cloud-discovery HTTP boundaries."""
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone

from leakscope.phase3.cloud import catalog as cloud_catalog, plan, PROVIDERS
from leakscope.phase3.cloud_jobs import queue_cloud
from leakscope.services.runs import serialize_run, get_run, _parse
from .models import CloudResource, Run
from .workbench import endpoint, _body, _number


def resource_data(row):
    return {'id': str(row.pk), 'url': row.url, 'provider': row.provider, 'name': row.name,
            'region': row.region, 'kind': row.kind, 'state': row.state, 'evidence': row.evidence,
            'endpoint_id': str(row.endpoint_id) if row.endpoint_id else None,
            'last_run_id': str(row.last_run_id) if row.last_run_id else None,
            'first_seen': row.first_seen.isoformat(), 'last_checked': row.last_checked.isoformat()}


@endpoint()
def catalog(request):
    return JsonResponse(cloud_catalog())


@endpoint(('POST',))
def preview(request):
    return JsonResponse(plan(_body(request)))


@endpoint(('POST',))
def start(request):
    data = _body(request)
    if data.pop('active', None) is not True: raise ValueError('Starting cloud discovery requires explicit active endpoint checks')
    spec = plan(data)['spec']
    if not plan(spec)['candidates']: raise ValueError('No valid candidates; adjust names or resource types')
    run = queue_cloud(spec)
    return JsonResponse({'run': serialize_run(run), 'results_url': '/cloud/'}, status=202)


@endpoint()
def resources(request):
    rows = CloudResource.objects.all()
    provider, state, query = (request.GET.get(x, '').strip() for x in ('provider', 'state', 'q'))
    if provider:
        if provider not in PROVIDERS: raise ValueError('Unknown cloud provider')
        rows = rows.filter(provider=provider)
    if state: rows = rows.filter(state=state)
    if query: rows = rows.filter(Q(name__icontains=query[:200]) | Q(url__icontains=query[:200]))
    page = _number(request.GET.get('page'), 1, 1, 10000)
    size = _number(request.GET.get('page_size'), 30, 1, 100)
    count = rows.count()
    from .services.table_sorting import parameters, ordered
    from django.db.models import F
    sort, direction = parameters(request, {'name','provider','region','kind','state','last_checked'}, 'last_checked')
    rows = ordered(rows, F(sort), direction, textual=sort != 'last_checked')
    return JsonResponse({'results': [resource_data(row) for row in rows[(page - 1) * size:page * size]],
                         'count': count, 'page': page, 'page_size': size})


@endpoint()
def runs(request, run_id=None):
    if run_id:
        row = Run.objects.get(pk=run_id, kind='cloud')
        row = get_run(row.pk)
        return JsonResponse({'run': serialize_run(row), 'result': _parse(row.result), 'spec': _parse(row.parameters)})
    return JsonResponse({'results': [serialize_run(row) for row in Run.objects.filter(kind='cloud').order_by('-created_at')[:20]]})


@endpoint(('POST',))
def cancel(request, run_id):
    row = Run.objects.get(pk=run_id, kind='cloud')
    Run.objects.filter(pk=row.pk, state__in=['queued', 'running']).update(state='cancelled', finished_at=timezone.now())
    row.refresh_from_db()
    return JsonResponse({'run': serialize_run(row)})


@endpoint(('POST',))
def fetch(request, resource_id):
    from leakscope.phase3.jobs import queue_capture
    row = CloudResource.objects.get(pk=resource_id)
    if not row.endpoint_id: raise ValueError('This resource has no observed endpoint')
    data = _body(request)
    actions = data.get('actions')
    if not isinstance(actions, list) or not actions or any(a not in ('http', 'httpx', 'screenshot') for a in actions):
        raise ValueError('Choose HTTP capture, HTTP fingerprint or screenshot')
    run = queue_capture(row.endpoint_id, actions, cooldown_seconds=0, resource_url=row.url)
    return JsonResponse({'run': serialize_run(run)}, status=202)
