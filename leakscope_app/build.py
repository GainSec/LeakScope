"""Build: workflow API. Reads never start or change a run."""
import json
from uuid import UUID
from django.http import JsonResponse
from leakscope.build import engine
from leakscope.build.blocks import base
from leakscope.build.kinds import ENDPOINT_FIELDS, FIELDS
from leakscope.build.conditions import OPS
from leakscope.services.runs import serialize_run, get_run
from .models import Run, Workflow, WorkflowRun
from .workbench import endpoint, _body

ACTIVE = ('queued', 'running', 'paused')
OP_LABELS = {'is': 'is', 'is_not': 'is not', 'one_of': 'is one of', 'contains': 'contains', 'not_contains': 'does not contain', 'starts_with': 'starts with',
             'ends_with': 'ends with', 'matches': 'matches pattern', 'not_matches': 'does not match pattern', 'empty': 'is empty',
             'not_empty': 'is not empty', 'gt': 'is greater than', 'lt': 'is less than'}


def _problems(definition):
    try: engine.validate(definition)
    except ValueError as exc: return str(exc)
    return ''


def _run_summary(row):
    result = serialize_run(row.run, include_activity=False)['result']
    result = result if isinstance(result, dict) else {}
    return {'id': str(row.run_id), 'state': row.run.state, 'partial': bool(result.get('partial')), 'spent': result.get('spent') or {'pages': 0, 'contacts': 0},
            'created_at': row.run.created_at.isoformat(), 'finished_at': row.run.finished_at.isoformat() if row.run.finished_at else None}


def _summary(row, last):
    steps = row.definition.get('steps') if isinstance(row.definition, dict) else None
    known = [engine._block(step.get('block')) for step in steps if isinstance(step, dict)] if isinstance(steps, list) else []
    reaches = {block.reach for block in known if block}
    return {'id': str(row.pk), 'name': row.name, 'steps': len(steps) if isinstance(steps, list) else 0,
            'reaches': [reach for reach in base.REACHES if reach in reaches], 'groups': [block.group if block else 'unknown' for block in known],
            'updated_at': row.updated_at.isoformat(), 'last_run': _run_summary(last) if last else None, 'automatic': _automatic(row)}


def _detail(row):
    problems = _problems(row.definition)
    recent = WorkflowRun.objects.filter(workflow=row).defer('items', 'steps', 'definition', 'waiting').select_related('run').order_by('-run__created_at')[:10]
    return {'id': str(row.pk), 'name': row.name, 'definition': row.definition, 'problems': problems, 'automatic': _automatic(row),
            'estimate': None if problems else engine.estimate(row.definition), 'runs': [_run_summary(item) for item in recent]}


def _automatic(row):
    first = engine._block((row.definition.get('steps') or [{}])[0].get('block')) if isinstance(row.definition, dict) and isinstance(row.definition.get('steps'), list) and row.definition['steps'] and isinstance(row.definition['steps'][0], dict) else None
    return {'enabled': row.enabled, 'event': bool(first and first.event), 'schedule_minutes': row.schedule_minutes,
            'next_run_at': row.next_run_at.isoformat() if row.next_run_at else None, 'note': row.last_note}


def _draft(data):
    name, definition = data.get('name'), data.get('definition')
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 200: raise ValueError('Name must be 1–200 characters')
    if not isinstance(definition, dict): raise ValueError('A workflow definition is an object')
    if len(json.dumps(definition)) > 256 * 1024: raise ValueError('The workflow is too large to save')
    steps = definition.get('steps')
    if isinstance(steps, list) and len(steps) > engine.MAX_STEPS: raise ValueError('A workflow has at most 40 steps')
    return name.strip(), definition


def _options():
    """Named lists the editor offers: organizations to choose, technologies and tags to suggest."""
    from leakscope.phase3.state import visible_endpoints
    from .models import AssetNode, Tag
    organizations = [[str(pk), value] for pk, value in AssetNode.objects.filter(kind='organization', organization_status='active').order_by('value').values_list('pk', 'value')[:300]]
    technologies = sorted({value for value in visible_endpoints().exclude(service_type='').values_list('service_type', flat=True).distinct()[:2000]})[:500]
    from .models import SavedLookup, WatchRule
    from .workbench import _types
    services = [[entry['key'], entry.get('label') or entry['key']] for entry in _types(include_saved=True) if entry.get('discovery')]
    watches = [[str(pk), name] for pk, name in WatchRule.objects.order_by('name').values_list('pk', 'name')[:300]]
    lookups = [[str(pk), name] for pk, name in SavedLookup.objects.exclude(name__startswith='Workflow · ').exclude(provider='research').order_by('name').values_list('pk', 'name')[:500]]
    from .models import CustomQuery, ScreenshotCollection
    queries = [[str(pk), name] for pk, name in CustomQuery.objects.order_by('name').values_list('pk', 'name')[:500]]
    from .models import Workflow
    workflows = [[str(pk), name] for pk, name in Workflow.objects.order_by('name').values_list('pk', 'name')[:300]]
    return {'workflows': workflows, 'organizations': organizations, 'technologies': technologies, 'tags': list(Tag.objects.order_by('name').values_list('name', flat=True)[:500]),
            'services': sorted(services, key=lambda pair: pair[1].casefold()), 'lookups': lookups, 'watches': watches, 'queries': queries,
            'collections': list(ScreenshotCollection.objects.order_by('name').values_list('name', flat=True)[:300])}


@endpoint()
def blocks(request):
    return JsonResponse({'blocks': base.catalog(), 'options': _options(), 'limits': {'defaults': engine.LIMIT_DEFAULTS, 'max': engine.LIMIT_MAX},
                         'fields': {kind: list(names) for kind, names in FIELDS.items()},
                         'conditions': {'fields': list(ENDPOINT_FIELDS), 'ops': [[op, OP_LABELS[op]] for op in OPS]}})


@endpoint()
def pick(request):
    """Saved addresses or domains matching some text, for choosing targets in the editor. Reads only."""
    from django.db.models import Count
    from leakscope.phase3.state import visible_endpoints
    from .models import DomainCandidate
    kind, text = request.GET.get('kind'), (request.GET.get('q') or '').strip()[:253]
    if kind not in ('address', 'domain'): raise ValueError('Choose addresses or domains')
    if len(text) < 2: return JsonResponse({'results': []})
    saved = visible_endpoints().filter(address__icontains=text)
    if kind == 'address':
        rows = saved.values('address').annotate(n=Count('pk')).order_by('address')[:50]
        return JsonResponse({'results': [{'value': row['address'], 'note': str(row['n']) + ' saved result' + ('' if row['n'] == 1 else 's')} for row in rows]})
    names = {name.lower() for name in saved.filter(address__regex=r'[A-Za-z]').values_list('address', flat=True).distinct()[:200] if ':' not in name}
    names |= set(DomainCandidate.objects.filter(domain__icontains=text).values_list('domain', flat=True).distinct()[:200])
    return JsonResponse({'results': [{'value': name, 'note': ''} for name in sorted(names)[:50]]})


@endpoint(('GET', 'POST'))
def workflows(request, workflow_id=None, action=None):
    if workflow_id is None:
        if request.method == 'POST':
            name, definition = _draft(_body(request))
            return JsonResponse(_detail(Workflow.objects.create(name=name, definition=definition)), status=201)
        rows = list(Workflow.objects.all()[:500])
        latest = {}
        for item in WorkflowRun.objects.filter(workflow__in=rows).defer('items', 'steps', 'definition', 'waiting').select_related('run').order_by('run__created_at'): latest[item.workflow_id] = item
        return JsonResponse({'results': [_summary(row, latest.get(row.pk)) for row in rows]})
    row = Workflow.objects.get(pk=UUID(str(workflow_id)))
    if action is None:
        if request.method != 'GET': raise ValueError('Choose save, delete, estimate or run')
        return JsonResponse(_detail(row))
    if request.method != 'POST':
        response = JsonResponse({'error': 'Method not allowed'}, status=405)
        response['Allow'] = 'POST'
        return response
    data = _body(request)
    if action == 'save':
        row.name, row.definition = _draft(data)
        row.save()
        # An edited workflow has not been confirmed: it stops starting by itself until it is turned on again.
        if row.enabled and (_problems(row.definition) or engine.estimate(row.definition)['token'] != row.confirmed_token):
            engine.turn_off(row, 'Turned off: it was changed after it was confirmed. Turn it on again to confirm the new version.')
        return JsonResponse(_detail(row))
    if action == 'disable': return JsonResponse(_detail(engine.turn_off(row)))
    if action == 'delete':
        if WorkflowRun.objects.filter(workflow=row, run__state__in=ACTIVE).exists(): raise ValueError('A run of this workflow is still active')
        Run.objects.filter(workflow_run__workflow=row).delete()
        row.delete()
        return JsonResponse({'deleted': True})
    numbers = engine.estimate(row.definition)
    if action == 'estimate': return JsonResponse(numbers)
    if action == 'enable':
        # The one confirmation for a workflow that starts by itself: the same estimate, approved once.
        if data.get('token') != numbers['token']:
            return JsonResponse({'error': 'Confirm what each run can spend before turning it on', 'estimate': numbers}, status=409)
        return JsonResponse(_detail(engine.turn_on(row, data['token'], data.get('schedule_minutes'))))
    # The only gate: a run starts only with the token of the estimate the person just saw.
    if data.get('token') != numbers['token']:
        return JsonResponse({'error': 'Confirm what this run can spend before it starts', 'estimate': numbers}, status=409)
    return JsonResponse({'run': serialize_run(engine.start(row, data['token']))}, status=202)


@endpoint()
def run(request, run_id):
    row = WorkflowRun.objects.select_related('run', 'workflow').get(run_id=UUID(str(run_id)))
    # get_run applies the deadline, so a run whose worker died is reported as lost here too.
    data = serialize_run(get_run(row.run_id))
    result = data['result'] if isinstance(data['result'], dict) else {}
    steps = result.get('steps') or []
    if data['state'] in ('cancelled', 'lost'):
        # The step that was in progress when the run was stopped did not finish.
        steps = [{**step, 'state': 'cancelled'} if step.get('state') in ('waiting', 'running') else step for step in steps]
    return JsonResponse({'run': data, 'steps': steps, 'spent': result.get('spent') or {'pages': 0, 'contacts': 0},
                         'limits': row.definition.get('limits') or {}, 'partial': bool(result.get('partial')), 'stopped_at': result.get('stopped_at'),
                         'results_url': '/assets/?run=' + str(row.run_id) if result.get('endpoint_ids') else None,
                         'definition': row.definition, 'workflow': {'id': str(row.workflow_id), 'name': row.workflow.name}})
