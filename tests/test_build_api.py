import json
from unittest.mock import patch
from django.test import TestCase
from leakscope.build import engine
from leakscope_app.models import Workflow, WorkflowRun, Run
from tests.test_build_blocks import observe
from tests.test_build_engine import LOCAL, step

WAITING = {'steps': [step('a', 'run_by_hand'), step('b', 'search', provider='zoomeye', query='app="x"')]}


class BuildAPITests(TestCase):
    def setUp(self):
        patcher = patch.object(engine.advance, 'apply_async')
        self.dispatch = patcher.start()
        self.addCleanup(patcher.stop)

    def post(self, path, data=None):
        return self.client.post(path, json.dumps(data or {}), content_type='application/json')

    def create(self, definition=LOCAL, name='Flow'):
        response = self.post('/api/build/workflows', {'name': name, 'definition': definition})
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def test_catalogue_lists_the_blocks(self):
        data = self.client.get('/api/build/blocks').json()
        rows = {row['key']: row for row in data['blocks']}
        self.assertEqual(set(rows), {'run_by_hand', 'saved_results', 'search', 'filter', 'keep_new', 'limit', 'tag', 'add_to_organization', 'http_fingerprint', 'fetch_response', 'screenshot',
                                     'protocol_identity', 'port_check', 'netlas_enrich', 'names_for_address', 'network_owner', 'dns_records', 'domain_registration', 'related_services', 'take_value', 'set_value',
                                     'saved_domains', 'certificate_names', 'names_of_results', 'results_for_domains', 'create_watch', 'expand_domains',
                                     'search_service', 'run_saved_lookup', 'sort', 'remove_duplicates', 'location', 'web_request', 'export_file',
                                     'new_observations', 'changed_observations', 'certificate_matches',
                                     'find_similar', 'run_custom_query', 'cloud_discovery', 'saved_cloud_resources', 'industrial_inspect', 'port_inventory', 'web_probe',
                                     'has_screenshot', 'add_to_collection', 'review_domains',
                                     'add_domains_to_organization', 'organization_domains', 'discover_organization', 'if',
                                     'chosen_results', 'chosen_domains', 'chosen_addresses', 'only_these', 'search_address',
                                     'remove_tags', 'stop_if', 'count', 'wait', 'cloud_network', 'has_dns_records', 'tenant_lookup', 'shared_evidence', 'run_workflow', 'lookup_finished'})
        self.assertEqual([(rows[key]['group'], rows[key]['reach']) for key in ('saved_results', 'search', 'screenshot', 'tag')],
                         [('trigger', 'local'), ('find', 'credits'), ('collect', 'target'), ('organize', 'local')])
        self.assertTrue(all(row['says'] for row in data['blocks']))
        self.assertEqual(data['limits'], {'defaults': {'pages': 5, 'contacts': 25, 'items': 500, 'runs_per_day': 24}, 'max': {'pages': 200, 'contacts': 1000, 'items': 5000, 'runs_per_day': 288}})
        self.assertEqual(data['conditions']['fields'][:4], ['address', 'ip', 'hostname', 'names'])
        self.assertEqual(data['fields']['domain'][:3], ['domain', 'root', 'source'])
        self.assertEqual({key: data['options'][key] for key in ('organizations', 'technologies', 'tags', 'lookups', 'watches')}, {'organizations': [], 'technologies': [], 'tags': [], 'lookups': [], 'watches': []})
        self.assertIn(['elastic', 'Elasticsearch'], data['options']['services'])
        self.assertEqual([op[1] for op in data['conditions']['ops'] if 'match' in op[0]], ['matches pattern', 'does not match pattern'])
        self.assertIn('one_of', [op[0] for op in data['conditions']['ops']])

    def test_create_save_list_and_get(self):
        created = self.create()
        self.assertEqual((created['name'], created['problems'], created['estimate']['items']), ('Flow', '', 50))
        listed = self.client.get('/api/build/workflows').json()['results']
        self.assertEqual([(row['name'], row['steps'], row['reaches'], row['groups'], row['last_run']) for row in listed],
                         [('Flow', 3, ['local'], ['trigger', 'logic', 'organize'], None)])
        saved = self.post('/api/build/workflows/%s/save' % created['id'], {'name': 'Renamed', 'definition': WAITING}).json()
        self.assertEqual((saved['name'], saved['estimate']['pages']), ('Renamed', 1))
        detail = self.client.get('/api/build/workflows/' + created['id']).json()
        self.assertEqual((detail['definition'], detail['runs']), (WAITING, []))
        self.assertEqual(self.client.get('/api/build/workflows').json()['results'][0]['reaches'], ['local', 'credits'])

    def test_an_invalid_draft_is_saved_with_its_problems(self):
        draft = self.create({'steps': [step('a', 'tag')]})
        self.assertIn('Step 1: must be a block that starts a run', draft['problems'])
        self.assertIsNone(draft['estimate'])
        self.assertEqual(self.post('/api/build/workflows/%s/estimate' % draft['id']).status_code, 400)
        self.assertEqual(self.post('/api/build/workflows/%s/run' % draft['id'], {'token': 'x'}).status_code, 400)
        empty = self.create({})
        self.assertIn('at least one step', empty['problems'])
        for bad in ({'name': '', 'definition': {}}, {'name': 'x' * 201, 'definition': {}}, {'name': 'x', 'definition': []},
                    {'name': 'x', 'definition': {'steps': [{}] * 41}}, {'name': 'x', 'definition': {'steps': 'x' * 300000}}):
            self.assertEqual(self.post('/api/build/workflows', bad).status_code, 400, bad.get('name'))
        self.assertEqual(Workflow.objects.count(), 2)

    def test_run_needs_the_token_from_the_current_estimate(self):
        observe(1)
        workflow = self.create()
        refused = self.post('/api/build/workflows/%s/run' % workflow['id'])
        self.assertEqual(refused.status_code, 409)
        self.assertEqual(Run.objects.count(), 0)
        token = refused.json()['estimate']['token']
        self.assertEqual(self.post('/api/build/workflows/%s/estimate' % workflow['id']).json()['token'], token)
        with self.captureOnCommitCallbacks(execute=True):
            started = self.post('/api/build/workflows/%s/run' % workflow['id'], {'token': token})
        self.assertEqual(started.status_code, 202, started.content)
        run = Run.objects.get()
        self.assertEqual((run.kind, started.json()['run']['id']), ('workflow', str(run.pk)))
        self.dispatch.assert_called_once()
        self.post('/api/build/workflows/%s/save' % workflow['id'], {'name': 'Flow', 'definition': WAITING})
        self.assertEqual(self.post('/api/build/workflows/%s/run' % workflow['id'], {'token': token}).status_code, 409)
        self.assertEqual(WorkflowRun.objects.get().definition['steps'][0]['block'], 'saved_results')
        engine.advance.apply(args=[str(run.pk)])
        view = self.client.get('/api/build/runs/%s' % run.pk).json()
        self.assertEqual((view['run']['state'], [row['state'] for row in view['steps']], view['workflow']['name']), ('succeeded', ['done', 'done', 'done'], 'Flow'))
        self.assertEqual((view['spent'], view['limits']['pages'], view['partial'], view['stopped_at']), ({'pages': 0, 'contacts': 0}, 5, False, None))
        detail = self.client.get('/api/build/workflows/' + workflow['id']).json()
        self.assertEqual([(row['id'], row['state'], row['partial']) for row in detail['runs']], [(str(run.pk), 'succeeded', False)])
        self.assertEqual(self.client.get('/api/build/workflows').json()['results'][0]['last_run']['state'], 'succeeded')

    def test_delete_is_refused_while_a_run_is_active_and_removes_finished_runs(self):
        workflow = self.create()
        token = self.post('/api/build/workflows/%s/estimate' % workflow['id']).json()['token']
        self.post('/api/build/workflows/%s/run' % workflow['id'], {'token': token})
        refused = self.post('/api/build/workflows/%s/delete' % workflow['id'])
        self.assertEqual((refused.status_code, refused.json()['error']), (400, 'A run of this workflow is still active'))
        self.assertEqual(Workflow.objects.count(), 1)
        Run.objects.update(state='succeeded')
        self.assertEqual(self.post('/api/build/workflows/%s/delete' % workflow['id']).status_code, 200)
        self.assertEqual((Workflow.objects.count(), WorkflowRun.objects.count(), Run.objects.count()), (0, 0, 0))

    def test_reads_do_not_write_and_methods_are_enforced(self):
        workflow = self.create()
        token = self.post('/api/build/workflows/%s/estimate' % workflow['id']).json()['token']
        run_id = self.post('/api/build/workflows/%s/run' % workflow['id'], {'token': token}).json()['run']['id']
        stamps = lambda: (list(Workflow.objects.values_list('updated_at', flat=True)), list(WorkflowRun.objects.values_list('updated_at', flat=True)), list(Run.objects.values_list('updated_at', 'state')))
        before = stamps()
        for path in ('/api/build/blocks', '/api/build/workflows', '/api/build/workflows/' + workflow['id'], '/api/build/runs/' + run_id):
            self.assertEqual(self.client.get(path).status_code, 200, path)
        self.assertEqual(stamps(), before)
        self.assertEqual(self.post('/api/build/blocks').status_code, 405)
        self.assertEqual(self.client.get('/api/build/workflows/%s/run' % workflow['id']).status_code, 405)
        self.assertEqual(self.client.get('/api/build/workflows/00000000-0000-0000-0000-000000000000').status_code, 404)
        self.assertEqual(self.client.get('/build/').status_code, 200)
        self.assertEqual(self.client.get('/build/%s/' % workflow['id']).status_code, 200)

    def test_a_malformed_draft_does_not_break_the_list(self):
        self.create({'steps': [{'block': ['x']}, 'nonsense', {'block': 'constructor'}]}, name='Broken')
        self.create()
        response = self.client.get('/api/build/workflows')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([(row['name'], row['groups']) for row in response.json()['results']], [('Broken', ['unknown', 'unknown']), ('Flow', ['trigger', 'logic', 'organize'])])

    def test_a_second_run_is_refused_and_a_stopped_run_shows_no_step_in_progress(self):
        workflow = self.create(WAITING)
        token = self.post('/api/build/workflows/%s/estimate' % workflow['id']).json()['token']
        run_id = self.post('/api/build/workflows/%s/run' % workflow['id'], {'token': token}).json()['run']['id']
        again = self.post('/api/build/workflows/%s/run' % workflow['id'], {'token': token})
        self.assertEqual((again.status_code, again.json()['error']), (400, 'A run of this workflow is already active'))
        child = Run.objects.create(kind='lookup', state='queued')
        with patch('leakscope.build.blocks.find.queue_lookup', return_value=child): engine.advance.apply(args=[run_id])
        self.assertEqual(self.client.get('/api/build/runs/' + run_id).json()['steps'][1]['state'], 'waiting')
        Run.objects.filter(pk=run_id).update(state='cancelled')
        self.assertEqual([row['state'] for row in self.client.get('/api/build/runs/' + run_id).json()['steps']], ['done', 'cancelled'])

    def test_turning_on_needs_the_confirmation_once_and_an_edit_turns_it_off(self):
        workflow = self.create()
        base = '/api/build/workflows/%s/' % workflow['id']
        refused = self.post(base + 'enable', {'schedule_minutes': 60})
        self.assertEqual((refused.status_code, 'estimate' in refused.json()), (409, True))
        token = refused.json()['estimate']['token']
        self.assertEqual(self.post(base + 'enable', {'token': token}).status_code, 400)
        self.assertEqual(self.post(base + 'enable', {'token': token, 'schedule_minutes': 2}).status_code, 400)
        on = self.post(base + 'enable', {'token': token, 'schedule_minutes': 60}).json()['automatic']
        self.assertEqual((on['enabled'], on['event'], on['schedule_minutes'], bool(on['next_run_at'])), (True, False, 60, True))
        self.assertTrue(self.client.get('/api/build/workflows').json()['results'][0]['automatic']['enabled'])
        same = self.post(base + 'save', {'name': 'Renamed', 'definition': LOCAL}).json()['automatic']
        self.assertTrue(same['enabled'])
        changed = self.post(base + 'save', {'name': 'Renamed', 'definition': WAITING}).json()['automatic']
        self.assertEqual((changed['enabled'], changed['note'][:10]), (False, 'Turned off'))
        self.post(base + 'enable', {'token': self.post(base + 'estimate').json()['token'], 'schedule_minutes': 5})
        self.assertFalse(self.post(base + 'disable').json()['automatic']['enabled'])
        event = self.create({'steps': [step('a', 'new_observations'), step('b', 'tag', tags=['new'])]}, name='Event')
        path = '/api/build/workflows/%s/' % event['id']
        on = self.post(path + 'enable', {'token': self.post(path + 'estimate').json()['token']}).json()['automatic']
        self.assertEqual((on['enabled'], on['event'], on['schedule_minutes'], on['next_run_at']), (True, True, None, None))

    def test_what_a_run_ended_with_is_browsable_in_observations(self):
        kept, other = observe(1), observe(2, 'grafana')
        workflow = self.create()
        token = self.post('/api/build/workflows/%s/estimate' % workflow['id']).json()['token']
        run_id = self.post('/api/build/workflows/%s/run' % workflow['id'], {'token': token}).json()['run']['id']
        self.assertIsNone(self.client.get('/api/build/runs/' + run_id).json()['results_url'])
        engine.advance.apply(args=[run_id])
        view = self.client.get('/api/build/runs/' + run_id).json()
        self.assertEqual(view['results_url'], '/assets/?run=' + run_id)
        listed = self.client.get('/api/operations/endpoints?run=' + run_id).json()
        self.assertEqual(([row['id'] for row in listed['results']], listed['run']['id']), ([str(kept.pk)], run_id))
