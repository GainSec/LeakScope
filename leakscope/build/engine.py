"""Runs a workflow as short slices. A block either finishes at once or queues existing jobs and waits;
a later slice collects their results. The workflow owns an ordinary Run of kind "workflow"."""
import copy
import hashlib
import json
import re
from datetime import datetime, timedelta
from uuid import uuid4

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from leakscope.services.config import redact
from leakscope.services import runs as run_service
from leakscope.services.run_events import emit
from leakscope_app.models import Run, Workflow, WorkflowRun
from . import blocks, conditions  # noqa: F401  (imports every block module)
from .kinds import FIELDS, placeholders
from .blocks.base import accepted, fits
from .blocks.base import BlockError, Context, Done, Waiting, get

LIMIT_DEFAULTS = {'pages': 5, 'contacts': 25, 'items': 500, 'runs_per_day': 24}
LIMIT_MAX = {'pages': 200, 'contacts': 1000, 'items': 5000, 'runs_per_day': 288}
SCHEDULE_MINUTES = (5, 43200)
MAX_STEPS = 40
POLL_SECONDS = 5
WAIT_SECONDS = 3600
CEILING = {'credits': ('pages', 'provider page ceiling reached'), 'target': ('contacts', 'target contact ceiling reached')}
_ID = re.compile(r'\w{1,32}')
END = '__end__'       # in a run's items: what reached the end of any path


def sources(steps, index):
    """The outputs a step takes its items from: its "after" list, or the step before it."""
    if not index: return []
    return steps[index].get('after') or [steps[index - 1]['id']]


def _refs(step, block):
    """The names of a step's outputs: its id, or id:side for a block with sides."""
    return [step['id'] + ':' + port for port in block.ports] if block is not None and block.ports else [step['id']]


def _block(key):
    try: return get(key)
    except (KeyError, TypeError): return None


def validate(definition):
    """Return the cleaned definition, or raise ValueError listing every problem by step number."""
    steps = definition.get('steps') if isinstance(definition, dict) else None
    if not isinstance(steps, list) or not steps: raise ValueError('A workflow needs at least one step')
    if len(steps) > MAX_STEPS: raise ValueError('A workflow has at most 40 steps')
    problems, cleaned, seen, previous, known, kind = [], [], set(), None, set(), None
    kinds, knowns, earlier = {}, {}, {}      # per output: its kind and value names; per earlier step id: its block
    for index, raw in enumerate(steps):
        label = 'Step ' + str(index + 1) + ': '
        raw = raw if isinstance(raw, dict) else {}
        identifier, key = raw.get('id'), raw.get('block')
        if not isinstance(identifier, str) or not _ID.fullmatch(identifier): problems.append(label + 'id must be 1–32 letters, digits or underscores')
        elif identifier in seen: problems.append(label + 'id "' + identifier + '" is used twice')
        if isinstance(identifier, str): seen.add(identifier)
        block = _block(key)
        if block is None:
            problems.append(label + 'unknown block "' + str(key)[:64] + '"')
            previous = None
            continue
        if index == 0 and not block.start: problems.append(label + 'must be a block that starts a run')
        # Where its items come from: the listed earlier steps, or the step before it.
        default = [cleaned[-1]['id']] if index and previous is not None and len(cleaned) == index else []
        wanted, chosen, giver = raw.get('after'), [], previous
        if block.start:
            if wanted: problems.append(label + 'a start block cannot come after another step')
        elif wanted == []: problems.append(label + block.name + ' is not connected to an earlier step')
        elif wanted is not None and (not isinstance(wanted, list) or len(wanted) > 8 or any(not isinstance(ref, str) for ref in wanted)):
            problems.append(label + 'comes after must list one to eight earlier steps')
        else:
            for ref in list(dict.fromkeys(wanted)) if wanted is not None else default:
                name, _, side = ref.partition(':')
                source = earlier.get(name)
                if source is None: problems.append(label + 'comes after "' + name[:32] + '", which is not an earlier step')
                elif source.ports and side not in source.ports: problems.append(label + 'choose the ' + ' or the '.join(port.capitalize() for port in source.ports) + ' side of ' + source.name)
                elif side and not source.ports: problems.append(label + source.name + ' has no "' + side[:32] + '" side')
                else: chosen.append(ref); giver = source
        if chosen:
            found = list(dict.fromkeys(kinds[ref] for ref in chosen if kinds.get(ref)))
            if len(found) > 1: problems.append(label + 'joins ' + ' and '.join(found) + ' items; a step takes one kind')
            kind = found[0] if found else None
            known = set.intersection(*(knowns[ref] for ref in chosen))
        if index and block.start: problems.append(label + 'only the first step can start a run')
        elif index and giver is not None and kind is not None and not fits(block, kind):
            problems.append(label + block.name + ' needs ' + ' or '.join(accepted(block)) + ' items, but ' + giver.name + ' gives ' + kind)
        settings = {}
        try: settings = block.clean(raw.get('settings') or {})
        except ValueError as exc: problems.append(label + str(exc))
        # Values: a setting may use {{name}} only for a field every result has at this point.
        for field in block.fields:
            used = placeholders(settings.get(field.key)) if field.type in ('text', 'tags') else []
            if used and not field.template: problems.append(label + field.label + ' cannot use values')
            for name in dict.fromkeys(used):
                if field.template and name not in known: problems.append(label + '{{' + name + '}} is not a value available at this step')
        step = {'id': identifier, 'block': key, 'settings': settings}
        if raw.get('when'):
            # "Only when": the step runs for matching results; the others pass through untouched.
            if block.start or block.ports or not (block.gives == 'same' or block.gives in accepted(block)): problems.append(label + block.name + ' cannot be limited to some results')
            else:
                try: step['when'] = conditions.clean(raw['when'])
                except ValueError as exc: problems.append(label + 'Only when: ' + str(exc))
        if chosen and chosen != default: step['after'] = chosen
        cleaned.append(step)
        if block.gives != 'same':
            # A block that gives new records starts again from that kind's own fields.
            if block.gives != kind or block.start or block.takes == 'any' or block.gives not in accepted(block): known = set(FIELDS[block.gives])
            kind = block.gives
        if settings:
            try: known |= set(block.adds(settings))
            except (KeyError, TypeError): pass
        previous = block
        if isinstance(identifier, str): earlier[identifier] = block
        for ref in _refs(step, block) if isinstance(identifier, str) else []: kinds[ref], knowns[ref] = kind, set(known)
    limits, given = {}, definition.get('limits') or {}
    if not isinstance(given, dict):
        given = {}
        problems.append('Limits must be an object')
    for name, default in LIMIT_DEFAULTS.items():
        value = given.get(name, default)
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= LIMIT_MAX[name]:
            problems.append('Limit ' + name + ' must be between 1 and ' + str(LIMIT_MAX[name]))
        else: limits[name] = value
    if problems: raise ValueError('\n'.join(problems))
    return {'steps': cleaned, 'limits': limits}


def estimate(definition):
    """Worst case for one run, and a token that changes whenever the workflow does."""
    cleaned = validate(definition)
    limits, items, rows, pages, contacts, most, outs = cleaned['limits'], 0, [], 0, 0, 0, {}
    reached, outside = set(), []
    for index, step in enumerate(cleaned['steps']):
        block = get(step['block'])
        items = min(limits['items'], sum(outs.get(ref, 0) for ref in sources(cleaned['steps'], index)))
        cost = block.cost(step['settings'], items)
        out = min(limits['items'], items if cost.items_out is None else cost.items_out)
        rows.append({'id': step['id'], 'block': step['block'], 'name': block.name, 'reach': block.reach, 'items_in': items, 'items_out': out,
                     'pages': cost.pages, 'contacts': cost.contacts})
        pages, contacts, most = pages + cost.pages, contacts + cost.contacts, max(most, out)
        for ref in _refs(step, block): outs[ref] = out
        reached |= set(block.reaches(step['settings']))
        outside.append(block.signature(step['settings']))
    totals = {'pages': min(pages, limits['pages']), 'contacts': min(contacts, limits['contacts']), 'items': most}
    # A workflow that depends on nothing outside itself keeps the token it always had.
    token = hashlib.sha256(json.dumps([cleaned, totals] + ([outside] if any(outside) else []), sort_keys=True).encode()).hexdigest()
    from .blocks.base import REACHES
    return {**totals, 'limits': limits, 'steps': rows, 'reaches': [name for name in REACHES if name in reached], 'token': token}


def start(workflow, token, since=None, seed=None, ceilings=None, given_by=''):
    """Start a confirmed run. The token proves the person saw the estimate for this exact definition.
    `since` marks an automatic run: its event start then takes only records newer than that moment.
    `seed` (run as a step of another workflow) replaces what the start block would find; `ceilings` lowers its limits."""
    numbers = estimate(workflow.definition)
    if not isinstance(token, str) or token != numbers['token']: raise ValueError('The workflow changed since it was confirmed')
    frozen = copy.deepcopy(validate(workflow.definition))
    if since is not None: frozen['since'] = since.isoformat()
    if seed is not None: frozen['seed'], frozen['given_by'] = list(seed)[:frozen['limits']['items']], str(given_by)[:200]
    for name, most in (ceilings or {}).items():
        if name in ('pages', 'contacts'): frozen['limits'][name] = max(0, min(frozen['limits'][name], int(most)))
    with transaction.atomic():
        # The lock makes two quick confirmations of one workflow start one run, not two.
        Workflow.objects.select_for_update().get(pk=workflow.pk)
        if WorkflowRun.objects.filter(workflow=workflow, run__state__in=['queued', 'running', 'paused']).exists():
            raise ValueError('A run of this workflow is already active')
        run = Run.objects.create(task_id=str(uuid4()), kind='workflow', type=workflow.name[:100], progress_total=len(frozen['steps']),
            parameters=run_service._data({'workflow_id': str(workflow.pk), 'estimate': {key: numbers[key] for key in ('pages', 'contacts', 'items')}}))
        WorkflowRun.objects.create(workflow=workflow, run=run, definition=frozen, spent={'pages': 0, 'contacts': 0})
        emit(run, 'Workflow queued for a background worker.', event='queued', dedupe_key='queued:' + run.task_id)
        transaction.on_commit(lambda: _dispatch(run))
    return run


def _dispatch(run):
    try: advance.apply_async(args=[str(run.pk)], task_id=run.task_id)
    except Exception as exc:
        run_service.write_state(run.task_id, 'FAILURE', {'error': redact(exc), 'category': 'dispatch'})


def _record(row, index, **values):
    """Create or update the record for the step at index."""
    step = row.definition['steps'][index]
    block = _block(step['block'])
    base = {'id': step['id'], 'block': step['block'], 'name': block.name if block else step['block'], 'reach': block.reach if block else 'local',
            'state': 'done', 'items_in': 0, 'items_out': 0, 'pages': 0, 'contacts': 0, 'note': '', 'left_out': 0, 'run_ids': [], 'passed_by': 0}
    if index < len(row.steps): base.update(row.steps[index])
    base.update(values)
    row.steps = row.steps[:index] + [base] + row.steps[index + 1:]
    return base


def _outputs(row):
    return row.items if isinstance(row.items, dict) else {}


def _join(lists, limit=None):
    """Items from several outputs, each once."""
    seen, found = set(), []
    for item in (item for items in lists for item in items):
        key = (item.get('kind'), item.get('id'))
        if key in seen: continue
        seen.add(key); found.append(item)
    return found[:limit] if limit else found


def _keep(row, index, produced):
    """Store a finished step's outputs. An output no later step uses has reached the end of its path;
    outputs every user of which has run are dropped."""
    steps, outputs = row.definition['steps'], dict(_outputs(row))
    outputs.update(produced)
    needed = {ref for later in range(index + 1, len(steps)) for ref in sources(steps, later)}
    ended = [items for ref, items in produced.items() if ref not in needed]
    if ended: outputs[END] = _join([outputs.get(END, [])] + ended, 5000)
    row.items = {ref: items for ref, items in outputs.items() if ref in needed or ref == END}


def _result(row, final=False):
    ended = _outputs(row).get(END, [])
    stopped = next(({'step': step['id'], 'name': step['name'], 'reason': step.get('reason') or str(step['left_out']) + ' left out at a ceiling'}
                    for step in row.steps if step.get('reason') or step['left_out']), None)
    return {'workflow_id': str(row.workflow_id), 'steps': row.steps, 'spent': {'pages': row.spent.get('pages', 0), 'contacts': row.spent.get('contacts', 0)},
            'items': len(ended), 'partial': stopped is not None, 'stopped_at': stopped,
            # What the run ended with stays browsable on the Observations page, like a lookup's results.
            **({'endpoint_ids': [item['id'] for item in ended if item.get('kind') == 'endpoint'][:5000]} if final else {})}


def _save(row, **run_values):
    row.save()
    values = {'result': run_service._data(_result(row, final='finished_at' in run_values)), 'progress_current': min(row.position, len(row.definition['steps'])),
              'progress_total': len(row.definition['steps']), 'updated_at': timezone.now(), **run_values}
    return Run.objects.filter(pk=row.run_id, state__in=['queued', 'running']).update(**values)


def _finish(row, state, error=''):
    changed = _save(row, state=state, error=error, finished_at=timezone.now())
    # The step records carry the counts; the items themselves are not kept once the run is over.
    if changed: WorkflowRun.objects.filter(pk=row.pk).update(items={}, waiting={})
    if changed:
        emit(row.run_id, {'succeeded': 'Workflow completed.', 'failed': 'Workflow failed: ' + error}.get(state, 'Workflow finished.'),
             event='completed' if state == 'succeeded' else state, level='error' if state == 'failed' else 'info', dedupe_key='finished:' + str(row.run_id))
    return 'stop'


def _fail(row, index, message, named=True):
    step = _record(row, index, state='failed', note=message)
    return _finish(row, 'failed', 'Step ' + str(index + 1) + (' (' + step['name'] + ')' if named else '') + ': ' + message)


def _locked(run_id):
    return WorkflowRun.objects.select_for_update(of=('self',)).select_related('workflow').filter(run_id=run_id).first()


def _expired(waiting):
    try: since = datetime.fromisoformat(waiting.get('since') or '')
    except ValueError: return True
    return timezone.now() - since > timedelta(seconds=WAIT_SECONDS)


def _touch(run_id):
    Run.objects.filter(pk=run_id, state='running').update(updated_at=timezone.now())
    return 'wait'


def _prepare(run_id, claim, children=None):
    """Under the row lock, decide the next move and claim the step. Nothing slow and no job queueing happens here:
    a block runs only after this transaction has committed, so the jobs it queues can see its records."""
    with transaction.atomic():
        row = _locked(run_id)
        if row is None: return 'stop'
        run = Run.objects.get(pk=run_id)
        if run.state in run_service.TERMINAL: return 'stop'
        if run.state == 'queued' or run.started_at is None:
            Run.objects.filter(pk=run.pk, state__in=['queued', 'running']).update(state='running', started_at=run.started_at or timezone.now())
        steps, limits, index = row.definition['steps'], row.definition['limits'], row.position
        if index >= len(steps): return _finish(row, 'succeeded')
        step = steps[index]
        block = _block(step['block'])
        if block is None: return _fail(row, index, 'block "' + str(step['block']) + '" is no longer available', named=False)
        waiting = row.waiting or {}
        if index == 0 and 'seed' in row.definition and not waiting:
            # Run as a step of another workflow: its start is replaced by what that step handed over.
            seed = row.definition['seed']
            _record(row, 0, state='done', items_out=len(seed), note='Given ' + str(len(seed)) + ' by “' + row.definition.get('given_by', '') + '”')
            _keep(row, 0, {ref: seed for ref in _refs(step, block)})
            row.position = 1
            if row.position >= len(steps): return _finish(row, 'succeeded')
            _save(row)
            return 'again'
        if waiting.get('claim'):
            # Another slice is executing this step right now, or died doing so.
            return _fail(row, index, 'did not finish within 60 minutes') if _expired(waiting) else _touch(run_id)
        outputs = _outputs(row)
        reached = _join([outputs.get(ref, []) for ref in sources(steps, index)], limits['items'])
        if index and not reached and not waiting:
            # A step that no results reach does not run, so an empty side of an If costs nothing.
            _record(row, index, state='skipped', note='No results reached this step')
            _keep(row, index, {ref: [] for ref in _refs(step, block)})
            row.position = index + 1
            if row.position >= len(steps): return _finish(row, 'succeeded')
            _save(row)
            return 'again'
        held, items = [], list(reached)
        if step.get('when') and not waiting:
            items = [item for item in reached if conditions.matches(item, step['when'])]
            held = [item for item in reached if not conditions.matches(item, step['when'])]
        plan = {'claim': claim, 'index': index, 'block': block, 'settings': step['settings'], 'items': items,
                'ctx': Context(workflow_id=str(row.workflow_id), workflow_run_id=str(row.pk), workflow_name=row.workflow.name,
                               origin='workflow:' + str(row.workflow_id), step_id=str(step['id']), since=row.definition.get('since') or ''),
                'allowance': {name: max(0, limits[name] - row.spent.get(name, 0)) for name in ('pages', 'contacts')}}
        if waiting:
            if children is None or sorted(children) != sorted(waiting['run_ids']): return {'poll': list(waiting['run_ids'])}
            if any(child.state not in run_service.TERMINAL and child.state != 'paused' for child in children.values()):
                return _fail(row, index, 'did not finish within 60 minutes') if _expired(waiting) else _touch(run_id)
            plan.update(mode='resume', state=waiting.get('state') or {}, children=children)
            row.waiting = {**waiting, 'claim': claim}
        else:
            resource, reason = CEILING.get(block.reach, (None, None))
            if resource and plan['allowance'][resource] <= 0:
                # Only this step is left out. Steps that follow it get nothing and are skipped in turn; other paths,
                # which may need no pages or contacts at all, still run.
                _record(row, index, state='skipped', reason=reason, items_in=len(reached))
                _keep(row, index, {ref: [] for ref in _refs(step, block)})
                row.position = index + 1
                if row.position >= len(steps): return _finish(row, 'succeeded')
                _save(row)
                return 'again'
            emit(run, 'Step ' + str(index + 1) + ' started: ' + block.name, event='step', dedupe_key='step-start:' + str(row.pk) + ':' + str(index))
            _record(row, index, state='running', items_in=len(items), passed_by=len(held))
            plan['mode'] = 'begin'
            row.waiting = {'claim': claim, 'since': timezone.now().isoformat(), 'held': held}
        _save(row)
        return plan


def _complete(run_id, plan, outcome, error):
    """Under the row lock again, record what the block did. A slice that lost its claim records nothing."""
    with transaction.atomic():
        row = _locked(run_id)
        if row is None or (row.waiting or {}).get('claim') != plan['claim'] or row.position != plan['index']: return 'stop'
        index, block, limits, steps = plan['index'], plan['block'], row.definition['limits'], row.definition['steps']
        claimed, row.waiting = row.waiting, {}
        if error is not None: return _fail(row, index, error)
        if isinstance(outcome, Waiting):
            row.waiting = {'run_ids': [str(key) for key in outcome.run_ids], 'state': outcome.state, 'since': timezone.now().isoformat(), 'held': claimed.get('held') or []}
            _record(row, index, state='waiting', run_ids=row.waiting['run_ids'])
            _save(row)
            return 'wait'
        if not isinstance(outcome, Done): return _fail(row, index, 'returned no result')
        # Results the step's "only when" did not select rejoin the flow after it.
        step, extra = steps[index], {}
        if block.ports:
            sides = {port: list((outcome.branches or {}).get(port) or [])[:limits['items']] for port in block.ports}
            produced = {step['id'] + ':' + port: items for port, items in sides.items()}
            extra['branches'] = {port: len(items) for port, items in sides.items()}
            total, over = sum(extra['branches'].values()), 0
        else:
            everything = list(outcome.items) + list(claimed.get('held') or [])
            over = max(0, len(everything) - limits['items'])
            produced = {step['id']: everything[:limits['items']]}
            total = len(produced[step['id']])
        _keep(row, index, produced)
        row.spent = {'pages': row.spent.get('pages', 0) + outcome.pages, 'contacts': row.spent.get('contacts', 0) + outcome.contacts}
        _record(row, index, state='done', items_out=total, pages=outcome.pages, contacts=outcome.contacts, note=outcome.note, left_out=outcome.left_out + over, **extra)
        row.position = index + 1
        emit(row.run_id, 'Step ' + str(index + 1) + ' finished: ' + block.name + (' · ' + outcome.note if outcome.note else ''), event='step',
             dedupe_key='step-done:' + str(row.pk) + ':' + str(index))
        if row.position >= len(steps): return _finish(row, 'succeeded')
        _save(row)
        return 'again'


def _slice(run_id):
    """One step of progress. Returns 'again' (next step now), 'wait' (poll later) or 'stop'."""
    claim = uuid4().hex
    plan = _prepare(run_id, claim)
    if isinstance(plan, dict) and 'poll' in plan:
        # Child jobs are refreshed outside the lock: this can touch the result backend.
        plan = _prepare(run_id, claim, {str(key): run_service.get_run(key) for key in plan['poll']})
    if not isinstance(plan, dict) or 'poll' in plan: return 'wait' if isinstance(plan, dict) else plan
    outcome = error = None
    try:
        if plan['mode'] == 'resume': outcome = plan['block'].resume(plan['ctx'], plan['items'], plan['settings'], plan['state'], plan['children'])
        else: outcome = plan['block'].begin(plan['ctx'], plan['items'], plan['settings'], plan['allowance'])
    except BlockError as exc: error = str(exc)
    except Exception as exc: error = redact(exc)  # A block bug must end the run, never leave it running.
    return _complete(run_id, plan, outcome, error)


def _abandon(run_id, message):
    """The slice itself broke (database, broker). Without this the run would show as running until its deadline."""
    try:
        with transaction.atomic():
            row = _locked(run_id)
            if row is not None and row.position < len(row.definition['steps']): _fail(row, row.position, message)
    except Exception:
        Run.objects.filter(pk=run_id, state__in=['queued', 'running']).update(state='failed', error=message, finished_at=timezone.now())


@shared_task(name='leakscope.build.advance')
def advance(run_id):
    try:
        outcome = 'again'
        while outcome == 'again': outcome = _slice(run_id)
        if outcome == 'wait': advance.apply_async(args=[str(run_id)], countdown=POLL_SECONDS)
    except Exception as exc:
        _abandon(run_id, 'the workflow runner stopped: ' + redact(exc))
    return {'workflow': True}


def turn_on(workflow, token, schedule_minutes=None):
    """Switch on automatic starts after the one confirmation. Later runs do not ask."""
    numbers = estimate(workflow.definition)
    if not isinstance(token, str) or token != numbers['token']: raise ValueError('The workflow changed since it was confirmed')
    first = get(validate(workflow.definition)['steps'][0]['block'])
    if first.event: schedule_minutes = None
    elif isinstance(schedule_minutes, bool) or not isinstance(schedule_minutes, int) or not SCHEDULE_MINUTES[0] <= schedule_minutes <= SCHEDULE_MINUTES[1]:
        raise ValueError('Choose how often it runs: every 5 minutes to every 30 days')
    now = timezone.now()
    Workflow.objects.filter(pk=workflow.pk).update(enabled=True, confirmed_token=token, schedule_minutes=schedule_minutes, cursor=now, last_note='',
        next_run_at=now + timedelta(minutes=schedule_minutes) if schedule_minutes else None)
    workflow.refresh_from_db()
    return workflow


def turn_off(workflow, note=''):
    Workflow.objects.filter(pk=workflow.pk).update(enabled=False, confirmed_token='', next_run_at=None, last_note=note[:300])
    workflow.refresh_from_db()
    return workflow


def _due(workflow, now):
    """Start one automatic run if this workflow is due. Returns a short word for what happened."""
    try: numbers = estimate(workflow.definition)
    except ValueError: turn_off(workflow, 'Turned off: the workflow has problems to fix.'); return 'off'
    if numbers['token'] != workflow.confirmed_token: turn_off(workflow, 'Turned off: it was changed after it was confirmed. Turn it on again to confirm the new version.'); return 'off'
    if WorkflowRun.objects.filter(workflow=workflow, run__state__in=['queued', 'running', 'paused']).exists(): return 'busy'
    step = validate(workflow.definition)['steps'][0]
    first, since = get(step['block']), workflow.cursor or now
    if first.event:
        if not first.pending(step['settings'], since): return 'nothing'
    elif not workflow.next_run_at or workflow.next_run_at > now: return 'waiting'
    today = WorkflowRun.objects.filter(workflow=workflow, run__created_at__gte=now.replace(hour=0, minute=0, second=0, microsecond=0)).count()
    if today >= numbers['limits']['runs_per_day']:
        Workflow.objects.filter(pk=workflow.pk).update(last_note='Daily limit of ' + str(numbers['limits']['runs_per_day']) + ' runs reached; it starts again tomorrow.')
        return 'limit'
    start(workflow, workflow.confirmed_token, since=since)
    Workflow.objects.filter(pk=workflow.pk).update(cursor=now, last_note='',
        next_run_at=now + timedelta(minutes=workflow.schedule_minutes) if workflow.schedule_minutes else None)
    return 'started'


@shared_task(name='leakscope.build.tick')
def tick():
    """Every minute: start the automatic workflows that are due. One broken workflow never stops the others."""
    now, outcome = timezone.now(), {}
    for workflow in Workflow.objects.filter(enabled=True)[:500]:
        try: word = _due(workflow, now)
        except Exception as exc:
            word = 'error'
            Workflow.objects.filter(pk=workflow.pk).update(last_note=('Could not start: ' + redact(exc))[:300])
        outcome[word] = outcome.get(word, 0) + 1
    return outcome
