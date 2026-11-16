from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase
from django.utils import timezone
from leakscope.build import engine
from leakscope.build.blocks.base import Block, Cost, Done, Waiting, BlockError, register, unregister
from leakscope.services.runs import write_state, serialize_run
from leakscope_app.models import Workflow, WorkflowRun, Run, TagAssignment
from tests.test_build_blocks import observe

JENKINS = {'rules': [{'field': 'technology', 'op': 'is', 'value': 'jenkins'}]}


def step(identifier, block, **settings):
    return {'id': identifier, 'block': block, 'settings': settings}


LOCAL = {'steps': [step('s1', 'saved_results', limit=50), step('s2', 'filter', conditions=JENKINS), step('s3', 'tag', tags=['built'])]}


class Probe(Block):
    """Test block: waits on a job the test controls, or spends, or fails."""
    key, name, group, reach, takes, gives = 'probe', 'Probe', 'find', 'credits', 'any', 'endpoint'
    calls = []

    def cost(self, settings, items_in):
        return Cost(pages=2)

    def begin(self, ctx, items, settings, allowance):
        Probe.calls.append(('begin', dict(allowance)))
        Probe.seen = WorkflowRun.objects.get(pk=ctx.workflow_run_id).waiting
        if Probe.mode == 'error': raise BlockError('Provider refused the query')
        if Probe.mode == 'crash': raise RuntimeError('token=supersecret exploded')
        if Probe.mode == 'done': return Done(items, pages=2, left_out=Probe.left_out)
        return Waiting([str(Probe.child.pk)], {'note': 'x'})

    def resume(self, ctx, items, settings, state, runs):
        Probe.calls.append(('resume', state, sorted(runs)))
        return Done(items, pages=1, note='resumed')


class EngineCase(TestCase):
    def setUp(self):
        Probe.calls, Probe.mode, Probe.left_out = [], 'wait', 0
        Probe.child = Run.objects.create(kind='lookup', state='running')
        register(Probe)
        self.addCleanup(unregister, 'probe')
        patcher = patch.object(engine.advance, 'apply_async')
        self.dispatch = patcher.start()
        self.addCleanup(patcher.stop)

    def launch(self, definition, name='Flow'):
        workflow = Workflow.objects.create(name=name, definition=definition)
        with self.captureOnCommitCallbacks(execute=True):
            run = engine.start(workflow, engine.estimate(definition)['token'])
        return workflow, run

    def slice(self, run):
        engine.advance.apply(args=[str(run.pk)])
        run.refresh_from_db()
        return serialize_run(run)


class ValidationTests(EngineCase):
    def problems(self, definition):
        with self.assertRaises(ValueError) as caught: engine.validate(definition)
        return str(caught.exception)

    def test_validate_reports_every_problem(self):
        self.assertIn('at least one step', self.problems({'steps': []}))
        self.assertIn('at least one step', self.problems('x'))
        self.assertIn('Step 1: must be a block that starts a run', self.problems({'steps': [step('a', 'tag', tags=['x'])]}))
        text = self.problems({'steps': [step('a', 'run_by_hand'), step('a', 'saved_results'), step('b', 'old_block'), step('c!', 'tag')],
                              'limits': {'pages': 0, 'contacts': 1001, 'items': 'many'}})
        for expected in ('Step 2: only the first step can start a run', 'Step 2: id "a" is used twice', 'Step 3: unknown block "old_block"', 'Step 4: id must be',
                         'Step 4: Tags is required', 'pages must be between 1 and 200', 'contacts must be between 1 and 1000', 'items must be'):
            self.assertIn(expected, text)
        self.assertIn('Step 2: Tag needs endpoint items, but Run by hand gives value', self.problems({'steps': [step('a', 'run_by_hand'), step('b', 'tag', tags=['x'])]}))
        self.assertIn('at most 40 steps', self.problems({'steps': [step('a', 'run_by_hand')] + [step('s%d' % i, 'probe') for i in range(40)]}))
        cleaned = engine.validate({'steps': [step('a', 'run_by_hand'), step('b', 'probe'), step('c', 'tag', tags=[' X '])]})
        self.assertEqual(cleaned['limits'], {'pages': 5, 'contacts': 25, 'items': 500, 'runs_per_day': 24})
        self.assertEqual(cleaned['steps'][2]['settings'], {'tags': ['X']})

    def test_estimate_flows_items_and_changes_token(self):
        first = engine.estimate(LOCAL)
        self.assertEqual((first['items'], first['pages'], first['contacts']), (50, 0, 0))
        self.assertEqual([(row['id'], row['name'], row['reach'], row['items_in'], row['items_out']) for row in first['steps']],
                         [('s1', 'Saved results that match', 'local', 0, 50), ('s2', 'Filter', 'local', 50, 50), ('s3', 'Tag', 'local', 50, 50)])
        changed = engine.estimate({'steps': [step('s1', 'saved_results', limit=49)] + LOCAL['steps'][1:]})
        self.assertNotEqual(first['token'], changed['token'])
        self.assertEqual(first['token'], engine.estimate(LOCAL)['token'])
        capped = engine.estimate({**LOCAL, 'limits': {'items': 10}})
        self.assertEqual((capped['steps'][1]['items_in'], capped['items']), (10, 10))
        paid = engine.estimate({'steps': [step('a', 'run_by_hand'), step('b', 'probe'), step('c', 'probe'), step('d', 'probe')]})
        self.assertEqual((paid['pages'], paid['steps'][1]['pages']), (5, 2))


class RunTests(EngineCase):
    def test_local_workflow_runs_to_success_in_one_slice(self):
        a, b, c = observe(1), observe(2, 'grafana'), observe(3)
        _, run = self.launch(LOCAL)
        self.assertEqual((run.kind, run.type, run.state), ('workflow', 'Flow', 'queued'))
        self.dispatch.assert_called_once()
        self.assertEqual(self.dispatch.call_args.kwargs['task_id'], run.task_id)
        self.dispatch.reset_mock()
        data = self.slice(run)
        self.assertEqual((data['state'], data['progress_current'], data['progress_total'], data['can_cancel']), ('succeeded', 3, 3, False))
        result = data['result']
        self.assertEqual([(row['id'], row['state'], row['items_in'], row['items_out'], row['note']) for row in result['steps']],
                         [('s1', 'done', 0, 3, '3 saved results'), ('s2', 'done', 3, 2, '2 kept · 1 dropped'), ('s3', 'done', 2, 2, '2 tagged')])
        self.assertEqual((result['items'], result['partial'], result['stopped_at'], result['spent']), (2, False, None, {'pages': 0, 'contacts': 0}))
        self.assertEqual(TagAssignment.objects.filter(tag__name='built').count(), 2)
        self.assertIsNotNone(run.finished_at)
        self.dispatch.assert_not_called()

    def test_start_requires_the_current_token(self):
        workflow = Workflow.objects.create(name='Flow', definition=LOCAL)
        for token in ('', 'stale', None):
            with self.assertRaisesMessage(ValueError, 'changed since it was confirmed'): engine.start(workflow, token)
        self.assertEqual(Run.objects.filter(kind='workflow').count(), 0)
        _, run = self.launch(LOCAL)
        frozen = WorkflowRun.objects.get(run=run)
        workflow = frozen.workflow
        workflow.definition['steps'][0]['settings']['limit'] = 1
        workflow.save()
        frozen.refresh_from_db()
        self.assertEqual(frozen.definition['steps'][0]['settings']['limit'], 50)
        self.assertEqual(Run.objects.filter(kind='workflow').count(), 1)

    def test_waiting_block_is_polled_and_resumed_once(self):
        _, run = self.launch({'steps': [step('a', 'run_by_hand'), step('b', 'probe')]})
        self.dispatch.reset_mock()
        data = self.slice(run)
        self.assertEqual(data['state'], 'running')
        self.assertEqual(data['result']['steps'][1]['state'], 'waiting')
        self.assertEqual(data['result']['steps'][1]['run_ids'], [str(Probe.child.pk)])
        self.assertEqual(self.dispatch.call_args.kwargs['countdown'], 5)
        self.slice(run)
        self.assertEqual([call[0] for call in Probe.calls], ['begin'])
        self.assertEqual(self.dispatch.call_count, 2)
        Run.objects.filter(pk=Probe.child.pk).update(state='succeeded')
        data = self.slice(run)
        self.assertEqual(data['state'], 'succeeded')
        self.assertEqual(Probe.calls[-1], ('resume', {'note': 'x'}, [str(Probe.child.pk)]))
        self.assertEqual((data['result']['steps'][1]['state'], data['result']['steps'][1]['note'], data['result']['spent']['pages']), ('done', 'resumed', 1))
        self.slice(run); self.slice(run)
        self.assertEqual([call[0] for call in Probe.calls], ['begin', 'resume'])
        self.assertEqual(self.dispatch.call_count, 2)

    def test_first_slice_success_does_not_complete_the_run(self):
        _, run = self.launch({'steps': [step('a', 'run_by_hand'), step('b', 'probe')]})
        self.slice(run)
        write_state(run.task_id, 'SUCCESS', {})
        run.refresh_from_db()
        self.assertEqual(run.state, 'running')

    def test_cancel_during_wait_stops_without_resuming(self):
        _, run = self.launch({'steps': [step('a', 'run_by_hand'), step('b', 'probe'), step('c', 'probe')]})
        self.slice(run)
        self.dispatch.reset_mock()
        self.assertTrue(serialize_run(run)['can_cancel'])
        response = self.client.post('/api/runs/%s/cancel' % run.pk)
        self.assertEqual(response.status_code, 200, response.content)
        Run.objects.filter(pk=Probe.child.pk).update(state='succeeded')
        data = self.slice(run)
        self.assertEqual(data['state'], 'cancelled')
        self.assertEqual([call[0] for call in Probe.calls], ['begin'])
        self.dispatch.assert_not_called()
        self.assertEqual(len(WorkflowRun.objects.get(run=run).items), 1)

    def test_ceiling_at_zero_ends_partial_and_names_the_step(self):
        Probe.mode = 'done'
        _, run = self.launch({'steps': [step('a', 'run_by_hand'), step('b', 'probe'), step('c', 'probe'), step('d', 'probe')], 'limits': {'pages': 2}})
        result = self.slice(run)['result']
        run.refresh_from_db()
        self.assertEqual(run.state, 'succeeded')
        self.assertTrue(result['partial'])
        self.assertEqual(result['stopped_at'], {'step': 'c', 'name': 'Probe', 'reason': 'provider page ceiling reached'})
        self.assertEqual([row['state'] for row in result['steps']], ['done', 'done', 'skipped', 'skipped'])
        self.assertEqual(Probe.calls, [('begin', {'pages': 2, 'contacts': 25})])
        self.assertEqual(result['spent'], {'pages': 2, 'contacts': 0})

    def test_a_ceiling_leaves_out_only_that_step_and_other_paths_still_run(self):
        Probe.mode = 'done'
        observe(1)
        definition = {'steps': [step('a', 'saved_results', limit=50), step('b', 'probe'), step('c', 'probe'), {'id': 'd', 'block': 'tag', 'settings': {'tags': ['still-ran']}, 'after': ['a']}], 'limits': {'pages': 2}}
        _, run = self.launch(definition)
        result = self.slice(run)['result']
        self.assertEqual([(row['id'], row['state']) for row in result['steps']], [('a', 'done'), ('b', 'done'), ('c', 'skipped'), ('d', 'done')])
        self.assertEqual((result['partial'], result['stopped_at']['step'], result['steps'][3]['note']), (True, 'c', '1 tagged'))

    def test_items_left_out_mark_the_run_partial_and_it_continues(self):
        Probe.mode, Probe.left_out = 'done', 3
        _, run = self.launch({'steps': [step('a', 'run_by_hand'), step('b', 'probe'), step('c', 'probe')]})
        result = self.slice(run)['result']
        self.assertEqual([row['state'] for row in result['steps']], ['done', 'done', 'done'])
        self.assertTrue(result['partial'])
        self.assertEqual(result['stopped_at'], {'step': 'b', 'name': 'Probe', 'reason': '3 left out at a ceiling'})

    def test_block_error_fails_the_run_with_a_safe_message(self):
        Probe.mode = 'error'
        _, run = self.launch({'steps': [step('a', 'run_by_hand'), step('b', 'probe')]})
        data = self.slice(run)
        self.assertEqual((data['state'], data['error']), ('failed', 'Step 2 (Probe): Provider refused the query'))
        self.assertEqual(data['result']['steps'][1]['state'], 'failed')

    def test_unexpected_exception_is_redacted(self):
        Probe.mode = 'crash'
        _, run = self.launch({'steps': [step('a', 'run_by_hand'), step('b', 'probe')]})
        with patch('leakscope.build.engine.redact', side_effect=lambda value: 'cleaned'):
            data = self.slice(run)
        self.assertEqual((data['state'], data['error']), ('failed', 'Step 2 (Probe): cleaned'))

    def test_removed_block_in_a_frozen_run_fails_that_step(self):
        _, run = self.launch({'steps': [step('a', 'run_by_hand'), step('b', 'probe')]})
        self.slice(run)
        unregister('probe')
        Run.objects.filter(pk=Probe.child.pk).update(state='succeeded')
        data = self.slice(run)
        self.assertEqual((data['state'], data['error']), ('failed', 'Step 2: block "probe" is no longer available'))

    def test_waiting_longer_than_an_hour_fails_the_step(self):
        _, run = self.launch({'steps': [step('a', 'run_by_hand'), step('b', 'probe')]})
        self.slice(run)
        self.dispatch.reset_mock()
        row = WorkflowRun.objects.get(run=run)
        row.waiting['since'] = (timezone.now() - timedelta(seconds=3601)).isoformat()
        row.save()
        data = self.slice(run)
        self.assertEqual((data['state'], data['error']), ('failed', 'Step 2 (Probe): did not finish within 60 minutes'))
        self.dispatch.assert_not_called()

    def test_item_ceiling_caps_what_moves_on(self):
        for last in range(1, 6): observe(last)
        _, run = self.launch({'steps': [step('s1', 'saved_results', limit=50), step('s2', 'tag', tags=['t'])], 'limits': {'items': 3}})
        result = self.slice(run)['result']
        self.assertEqual((result['steps'][0]['items_out'], result['steps'][1]['items_in'], result['partial']), (3, 3, True))
        self.assertEqual(result['stopped_at']['reason'], '2 left out at a ceiling')


class ReviewFixTests(EngineCase):
    WAIT = {'steps': [step('a', 'run_by_hand'), step('b', 'probe')]}

    def test_the_step_is_claimed_and_saved_before_the_block_runs(self):
        _, run = self.launch(self.WAIT)
        self.slice(run)
        self.assertIn('claim', Probe.seen)
        self.assertNotIn('claim', WorkflowRun.objects.get(run=run).waiting)

    def test_a_claimed_step_is_not_started_again_and_a_dead_claim_fails(self):
        _, run = self.launch(self.WAIT)
        row = WorkflowRun.objects.get(run=run)
        row.position, row.waiting = 1, {'claim': 'another-slice', 'since': timezone.now().isoformat()}
        row.save()
        self.dispatch.reset_mock()
        self.assertEqual(self.slice(run)['state'], 'running')
        self.assertEqual(Probe.calls, [])
        self.assertEqual(self.dispatch.call_args.kwargs['countdown'], 5)
        row.waiting['since'] = (timezone.now() - timedelta(seconds=3601)).isoformat()
        row.save()
        data = self.slice(run)
        self.assertEqual((data['state'], data['error']), ('failed', 'Step 2 (Probe): did not finish within 60 minutes'))
        self.assertEqual(Probe.calls, [])

    def test_a_result_from_a_slice_that_lost_its_claim_is_ignored(self):
        _, run = self.launch(self.WAIT)
        plan = {'claim': 'stale', 'index': 1, 'block': None, 'settings': {}}
        self.assertEqual(engine._complete(str(run.pk), plan, Done([]), None), 'stop')
        self.assertEqual(WorkflowRun.objects.get(run=run).position, 0)

    def test_a_second_confirmation_while_a_run_is_active_is_refused(self):
        workflow, run = self.launch(self.WAIT)
        with self.assertRaisesMessage(ValueError, 'A run of this workflow is already active'):
            engine.start(workflow, engine.estimate(self.WAIT)['token'])
        Run.objects.filter(pk=run.pk).update(state='succeeded')
        with self.captureOnCommitCallbacks(execute=True):
            engine.start(workflow, engine.estimate(self.WAIT)['token'])
        self.assertEqual(Run.objects.filter(kind='workflow').count(), 2)

    def test_a_broken_runner_or_broker_ends_the_run_instead_of_leaving_it_running(self):
        _, run = self.launch(self.WAIT)
        with patch.object(engine, '_slice', side_effect=RuntimeError('database went away')):
            data = self.slice(run)
        self.assertEqual(data['state'], 'failed')
        self.assertIn('the workflow runner stopped', data['error'])
        _, other = self.launch(self.WAIT, name='Second')
        self.dispatch.side_effect = RuntimeError('broker unreachable')
        data = self.slice(other)
        self.assertEqual(data['state'], 'failed')
        self.assertIn('the workflow runner stopped', data['error'])

    def test_a_waiting_workflow_whose_polling_died_is_reported_lost(self):
        from leakscope.services.runs import get_run
        _, run = self.launch(self.WAIT)
        self.slice(run)
        meta = {'status': 'SUCCESS', 'result': {'workflow': True}}
        with patch('leakscope.services.runs.AsyncResult') as result:
            result.return_value.backend.get_task_meta.return_value = meta
            self.assertEqual(get_run(run.pk).state, 'running')
            Run.objects.filter(pk=run.pk).update(updated_at=timezone.now() - timedelta(seconds=3700))
            self.assertEqual(get_run(run.pk).state, 'lost')

    def test_two_tag_steps_keep_both_sets_of_tags(self):
        observe(1)
        _, run = self.launch({'steps': [step('s1', 'saved_results'), step('s2', 'tag', tags=['first']), step('s3', 'tag', tags=['second'])]})
        self.slice(run)
        self.assertEqual(sorted(TagAssignment.objects.values_list('tag__name', 'origin')),
                         sorted([('first', 'workflow:%s:s2' % WorkflowRun.objects.get(run=run).workflow_id), ('second', 'workflow:%s:s3' % WorkflowRun.objects.get(run=run).workflow_id)]))

    def test_step_ids_are_whole_words_and_finished_runs_drop_their_items(self):
        with self.assertRaisesMessage(ValueError, 'Step 1: id must be'): engine.validate({'steps': [step('a\n', 'run_by_hand')]})
        observe(1)
        _, run = self.launch(LOCAL)
        self.assertEqual(self.slice(run)['result']['items'], 1)
        self.assertEqual(WorkflowRun.objects.get(run=run).items, {})


class AutomaticStartTests(EngineCase):
    def on(self, definition, minutes=None, name='Auto'):
        workflow = Workflow.objects.create(name=name, definition=definition)
        return engine.turn_on(workflow, engine.estimate(definition)['token'], minutes)

    def tick(self):
        with self.captureOnCommitCallbacks(execute=True): return engine.tick.apply().get()

    def test_a_scheduled_workflow_starts_when_due_and_not_before(self):
        workflow = self.on(LOCAL, 30)
        self.assertEqual((self.tick(), Run.objects.filter(kind='workflow').count()), ({'waiting': 1}, 0))
        Workflow.objects.filter(pk=workflow.pk).update(next_run_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(self.tick(), {'started': 1})
        workflow.refresh_from_db()
        self.assertGreater(workflow.next_run_at, timezone.now() + timedelta(minutes=29))
        self.assertEqual(Run.objects.filter(kind='workflow').count(), 1)
        Workflow.objects.filter(pk=workflow.pk).update(next_run_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(self.tick(), {'busy': 1})
        self.assertEqual(Run.objects.filter(kind='workflow').count(), 1)

    def test_an_event_workflow_starts_only_when_something_new_exists_and_takes_only_what_is_new(self):
        observe(1)
        workflow = self.on({'steps': [step('a', 'new_observations'), step('b', 'tag', tags=['fresh'])]})
        self.assertEqual(self.tick(), {'nothing': 1})
        fresh = observe(2)
        self.assertEqual(self.tick(), {'started': 1})
        run = Run.objects.get(kind='workflow')
        data = self.slice(run)
        self.assertEqual((data['state'], data['result']['steps'][0]['items_out']), ('succeeded', 1))
        self.assertEqual(list(TagAssignment.objects.filter(tag__name='fresh').values_list('target_key', flat=True)), [str(fresh.pk)])
        self.assertEqual(self.tick(), {'nothing': 1})

    def test_a_changed_or_broken_workflow_is_turned_off_and_the_daily_limit_holds(self):
        workflow = self.on(LOCAL, 5)
        Workflow.objects.filter(pk=workflow.pk).update(definition={'steps': [step('s1', 'saved_results', limit=1)]}, next_run_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(self.tick(), {'off': 1})
        workflow.refresh_from_db()
        self.assertEqual((workflow.enabled, workflow.last_note[:10], Run.objects.filter(kind='workflow').count()), (False, 'Turned off', 0))
        capped = self.on({**LOCAL, 'limits': {'runs_per_day': 1}}, 5, name='Capped')
        for _ in range(2):
            Workflow.objects.filter(pk=capped.pk).update(next_run_at=timezone.now() - timedelta(minutes=1))
            self.tick()
            Run.objects.filter(kind='workflow').update(state='succeeded')
        capped.refresh_from_db()
        self.assertEqual((WorkflowRun.objects.filter(workflow=capped).count(), capped.last_note[:11]), (1, 'Daily limit'))
        with self.assertRaisesMessage(ValueError, 'Choose how often it runs'): self.on(LOCAL, None, name='No schedule')
        with self.assertRaisesMessage(ValueError, 'changed since it was confirmed'): engine.turn_on(Workflow.objects.create(name='x', definition=LOCAL), 'stale', 60)

    def test_certificate_matches_and_changes_fire_and_a_hand_run_uses_the_last_day(self):
        from leakscope.build.blocks.base import get, Context
        from leakscope_app.models import CertificateRecord, CertificateWatchMatch, ObservationChange, ServiceObservation
        record = CertificateRecord.objects.create(key='k')
        CertificateWatchMatch.objects.create(certificate=record, watch_key='w1', watch_name='Acme', matched_names=['sso.example.com', '*.example.com'])
        block = get('certificate_matches')
        hour_ago = timezone.now() - timedelta(hours=1)
        self.assertEqual([i['id'] for i in block.found(block.clean({}), hour_ago)], ['sso.example.com', 'example.com'])
        self.assertEqual((block.pending(block.clean({'watch': 'other'}), hour_ago), block.pending(block.clean({}), timezone.now())), (0, 0))
        done = block.begin(Context('w', 'r', 'n', 'workflow:w'), [], block.clean({}), {})
        self.assertEqual((len(done.items), done.note), (2, '2 new since a day ago'))
        endpoint = observe(1)
        ObservationChange.objects.create(endpoint=endpoint, provider='zoomeye', observation=ServiceObservation.objects.get())
        changed = get('changed_observations')
        self.assertEqual([i['id'] for i in changed.found(changed.clean({}), hour_ago)], [str(endpoint.pk)])
        self.assertTrue(all(get(key).event and get(key).start for key in ('new_observations', 'changed_observations', 'certificate_matches')))
