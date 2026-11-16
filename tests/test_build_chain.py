"""Run another workflow as a step, and start when a search or lookup finishes."""
import json
from django.utils import timezone
from datetime import timedelta
from leakscope.build import engine
from leakscope.build.blocks.base import get
from leakscope.build.kinds import domain_item
from leakscope.phase3.state import tag_names
from leakscope.services.runs import serialize_run
from leakscope_app.models import Run, SavedLookup, Workflow, WorkflowRun
from tests.test_build_blocks import observe
from tests.test_build_engine import EngineCase, Probe, step


class RunWorkflowTests(EngineCase):
    def child(self, *steps, name='Child'):
        return Workflow.objects.create(name=name, definition={'steps': list(steps) or [step('a', 'saved_results', limit=50), step('b', 'tag', tags=['from-child'])]})

    def advance(self, run):
        engine.advance.apply(args=[str(run.pk)])
        run.refresh_from_db()
        return serialize_run(run)['result']

    def test_the_child_runs_on_the_results_handed_to_it_and_the_parent_goes_on_after(self):
        a, b, c = observe(1), observe(2, technology='nginx'), observe(3)
        child = self.child()
        parent = {'steps': [step('a', 'saved_results', limit=50), step('b', 'filter', conditions={'rules': [{'field': 'technology', 'op': 'is', 'value': 'jenkins'}]}),
                            step('c', 'run_workflow', workflow=str(child.pk)), step('d', 'tag', tags=['after'])]}
        _, run = self.launch(parent, name='Parent')
        with self.captureOnCommitCallbacks(execute=True):
            waiting = self.slice(run)['result']
        self.assertEqual([row['state'] for row in waiting['steps']], ['done', 'done', 'waiting'])
        row = WorkflowRun.objects.get(workflow=child)
        self.assertEqual(waiting['steps'][2]['run_ids'], [str(row.run_id)])
        result = self.advance(row.run)
        self.assertEqual((result['steps'][0]['state'], result['steps'][0]['items_out'], result['steps'][0]['note']), ('done', 2, 'Given 2 by “Parent”'))
        self.assertEqual([tag_names('endpoint', str(e.pk)) for e in (a, b, c)], [['from-child'], [], ['from-child']])
        done = self.slice(run)['result']
        self.assertEqual([row['state'] for row in done['steps']], ['done'] * 4)
        self.assertEqual(done['steps'][2]['note'], 'Ran “Child”: finished with 2 at its end')
        self.assertEqual(tag_names('endpoint', str(a.pk)), ['after', 'from-child'])

    def test_it_can_start_by_itself_and_its_cost_is_part_of_the_confirmation(self):
        observe(1)
        child = self.child(step('a', 'saved_results', limit=50), step('b', 'probe'))
        parent = {'steps': [step('a', 'saved_results', limit=50), step('b', 'run_workflow', workflow=str(child.pk), start='own')]}
        self.assertEqual(engine.estimate(parent)['pages'], 2)
        before = engine.estimate(parent)['token']
        child.definition = {'steps': child.definition['steps'] + [step('c', 'probe')]}; child.save()
        self.assertNotEqual(engine.estimate(parent)['token'], before)
        Probe.mode = 'done'
        _, run = self.launch(parent, name='Parent')
        with self.captureOnCommitCallbacks(execute=True): self.slice(run)
        row = WorkflowRun.objects.get(workflow=child)
        self.assertNotIn('seed', row.definition)
        self.advance(row.run)
        done = self.slice(run)['result']
        self.assertEqual((done['spent']['pages'], done['steps'][1]['pages']), (4, 4))

    def test_the_child_cannot_spend_more_than_the_parent_has_left(self):
        observe(1)
        child = self.child(step('a', 'saved_results', limit=50), step('b', 'probe'))
        parent = {'steps': [step('a', 'saved_results', limit=50), step('b', 'run_workflow', workflow=str(child.pk))], 'limits': {'pages': 1}}
        _, run = self.launch(parent)
        with self.captureOnCommitCallbacks(execute=True): self.slice(run)
        self.assertEqual(WorkflowRun.objects.get(workflow=child).definition['limits']['pages'], 1)

    def test_problems_are_said_plainly(self):
        observe(1)
        nested = self.child(step('a', 'saved_results'), step('b', 'run_workflow', workflow=str(self.child(name='Inner').pk)), name='Nested')
        block = get('run_workflow')
        with self.assertRaisesMessage(ValueError, 'no longer exists'): block.clean({'workflow': '00000000-0000-0000-0000-000000000000'})
        with self.assertRaisesMessage(ValueError, '“Nested” itself runs another workflow'): block.clean({'workflow': str(nested.pk)})
        domains = self.child(step('a', 'saved_domains'), name='Domains')
        _, run = self.launch({'steps': [step('a', 'saved_results', limit=50), step('b', 'run_workflow', workflow=str(domains.pk))]})
        failed = self.slice(run)
        self.assertEqual(failed['state'], 'failed')
        self.assertIn('“Domains” starts with domain items, but this step was given endpoint items', failed['error'])
        busy = self.child(name='Busy')
        WorkflowRun.objects.create(workflow=busy, run=Run.objects.create(kind='workflow', state='running'), definition={}, spent={})
        _, second = self.launch({'steps': [step('a', 'saved_results', limit=50), step('b', 'run_workflow', workflow=str(busy.pk))]}, name='Second')
        self.assertIn('“Busy” is already running', self.slice(second)['error'])

    def test_a_failed_child_fails_the_step_and_the_editor_lists_workflows(self):
        from leakscope_app.build import _options
        observe(1)
        child = self.child()
        self.assertIn([str(child.pk), 'Child'], _options()['workflows'])
        _, run = self.launch({'steps': [step('a', 'saved_results', limit=50), step('b', 'run_workflow', workflow=str(child.pk))]})
        with self.captureOnCommitCallbacks(execute=True): self.slice(run)
        Run.objects.filter(pk=WorkflowRun.objects.get(workflow=child).run_id).update(state='failed', error='Step 2 (Tag): broke', finished_at=timezone.now())
        self.assertIn('“Child” failed: Step 2 (Tag): broke', self.slice(run)['error'])


class LookupFinishedTests(EngineCase):
    def finished(self, lookup, endpoints, state='succeeded', minutes=0):
        return Run.objects.create(kind='lookup', state=state, finished_at=timezone.now() - timedelta(minutes=minutes),
                                  parameters=json.dumps({'lookup_id': str(lookup.pk)}), result=json.dumps({'endpoint_ids': [str(e.pk) for e in endpoints]}))

    def test_it_gives_the_results_of_lookups_that_finished_since_the_last_check(self):
        a, b, c, d = (observe(n) for n in range(1, 5))
        mine = SavedLookup.objects.create(name='Jenkins sweep', provider='shodan', query='jenkins')
        other = SavedLookup.objects.create(name='Other', provider='shodan', query='x')
        made = SavedLookup.objects.create(name='Workflow · Flow', provider='shodan', query='y')
        self.finished(mine, [a, b]); self.finished(other, [c]); self.finished(made, [d]); self.finished(mine, [d], state='failed'); self.finished(mine, [d], minutes=90)
        block = get('lookup_finished')
        since = timezone.now() - timedelta(minutes=30)
        every = block.found(block.clean({}), since)
        self.assertEqual(sorted(i['id'] for i in every), sorted(str(e.pk) for e in (a, b, c)))
        only = block.found(block.clean({'lookup': str(mine.pk)}), since)
        self.assertEqual(sorted(i['id'] for i in only), sorted(str(e.pk) for e in (a, b)))
        self.assertEqual(block.pending(block.clean({'lookup': str(mine.pk)}), since), 2)
        self.assertEqual((block.start, block.event, block.gives, block.reach), (True, True, 'endpoint', 'local'))


class ConfirmationTests(EngineCase):
    def test_the_estimate_names_every_reach_including_the_inner_workflow_and_changes_with_it(self):
        observe(1)
        child = Workflow.objects.create(name='Child', definition={'steps': [step('a', 'saved_results', limit=50), step('b', 'tag', tags=['x'])]})
        plain = {'steps': [step('a', 'saved_results', limit=50), step('b', 'dns_records')]}
        self.assertEqual(engine.estimate(plain)['reaches'], ['local', 'public'])
        parent = {'steps': [step('a', 'saved_results', limit=50), step('b', 'run_workflow', workflow=str(child.pk))]}
        before = engine.estimate(parent)
        self.assertEqual(before['reaches'], ['local'])
        child.definition = {'steps': child.definition['steps'] + [step('c', 'web_request', url='https://example.com/hook')]}; child.save()
        after = engine.estimate(parent)
        self.assertEqual(after['reaches'], ['local', 'send'])
        self.assertNotEqual(after['token'], before['token'])

    def test_a_workflow_without_an_inner_workflow_keeps_its_token(self):
        import hashlib
        definition = {'steps': [step('a', 'saved_results', limit=50), step('b', 'tag', tags=['x'])]}
        numbers = engine.estimate(definition)
        totals = {key: numbers[key] for key in ('pages', 'contacts', 'items')}
        self.assertEqual(numbers['token'], hashlib.sha256(json.dumps([engine.validate(definition), totals], sort_keys=True).encode()).hexdigest())

    def test_results_that_came_in_keep_their_values_through_shared_evidence(self):
        from leakscope.build.kinds import endpoint_items
        from leakscope_app.models import EvidenceCorrelation
        from tests.test_build_blocks import CTX
        a, b = observe(1), observe(2)
        for endpoint in (a, b): EvidenceCorrelation.objects.create(endpoint=endpoint, observation=endpoint.observations.first(), kind='certificate.sha256', value='a' * 64)
        items = endpoint_items([str(a.pk)])[0]
        items[0]['fields']['cloud_provider'] = 'Amazon Web Services'
        block = get('shared_evidence')
        done = block.begin(CTX, items, block.clean({'include': 'yes'}), {})
        self.assertEqual([(i['id'], i['fields'].get('cloud_provider')) for i in done.items], [(str(a.pk), 'Amazon Web Services'), (str(b.pk), None)])
