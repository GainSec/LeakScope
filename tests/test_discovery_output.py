import json
from django.test import TestCase
from leakscope_app.models import Run, ServiceEndpoint


class DiscoveryOutputTests(TestCase):
    def setUp(self):
        from leakscope.phase3.state import ingest_observation
        self.first = ingest_observation('shodan', {'address':'192.0.2.91','port':443,'scheme':'https','service_type':'http'})
        self.other = ingest_observation('shodan', {'address':'192.0.2.92','port':502,'service_type':'modbus'})

    def lookup_run(self, **result):
        return Run.objects.create(kind='lookup',provider='shodan',state='succeeded',result=json.dumps({'stored':1,'total':1,'query':'"acme"','endpoint_ids':[str(self.first.pk)],**result}))

    def test_run_filter_returns_only_its_saved_endpoints_and_context(self):
        run = self.lookup_run()
        response = self.client.get('/api/operations/endpoints',{'run':str(run.pk)})
        self.assertEqual(response.status_code,200,response.content)
        data=response.json()
        self.assertEqual([row['id'] for row in data['results']],[str(self.first.pk)])
        self.assertEqual(data['count'],1)
        self.assertEqual(data['run']['id'],str(run.pk))
        self.assertEqual(data['run']['query'],'"acme"')
        self.assertEqual(data['run']['stored'],1)

    def test_zero_result_run_never_falls_back_to_all_observations(self):
        run=self.lookup_run(endpoint_ids=[],stored=0,total=0)
        data=self.client.get('/api/operations/endpoints',{'run':str(run.pk)}).json()
        self.assertEqual(data['results'],[])
        self.assertEqual(data['count'],0)
        self.assertEqual(data['run']['total'],0)

    def test_pending_or_corrupt_results_are_empty_and_preserve_run_state(self):
        for state,result in [('queued','{}'),('running','not-json'),('failed','null')]:
            run=Run.objects.create(kind='lookup',provider='shodan',state=state,result=result)
            response=self.client.get('/api/operations/endpoints',{'run':str(run.pk)})
            self.assertEqual(response.status_code,200,response.content)
            self.assertEqual(response.json()['results'],[])
            self.assertEqual(response.json()['run']['state'],state)

    def test_invalid_nonlookup_or_malformed_references_fail_closed(self):
        capture=Run.objects.create(kind='capture')
        bad=self.lookup_run(endpoint_ids=['not-a-uuid'])
        for value in ['invalid','00000000-0000-4000-8000-000000000001',str(capture.pk),str(bad.pk)]:
            response=self.client.get('/api/operations/endpoints',{'run':value})
            self.assertIn(response.status_code,(400,404))
            self.assertNotIn(str(self.other.pk),response.content.decode())

    def test_other_filters_stay_inside_run_and_deleted_targets_do_not_widen_results(self):
        run=self.lookup_run()
        data=self.client.get('/api/operations/endpoints',{'run':str(run.pk),'q':'192.0.2.92'}).json()
        self.assertEqual(data['count'],0)
        self.first.delete()
        self.assertEqual(self.client.get('/api/operations/endpoints',{'run':str(run.pk)}).json()['count'],0)
        self.assertEqual(self.client.get('/api/operations/endpoints').json()['count'],1)

    def test_hidden_source_does_not_reappear_through_run_filter(self):
        run=self.lookup_run()
        self.first.observations.update(hidden=True)
        data=self.client.get('/api/operations/endpoints',{'run':str(run.pk)}).json()
        self.assertEqual(data['count'],0)

    def test_hidden_run_source_stays_hidden_even_when_another_provider_is_visible(self):
        from leakscope.phase3.state import ingest_observation
        run=self.lookup_run()
        ingest_observation('netlas',{'address':'192.0.2.91','port':443,'scheme':'https','service_type':'http'})
        self.first.observations.filter(provider='shodan').update(hidden=True)
        response=self.client.get('/api/operations/endpoints',{'run':str(run.pk)})
        self.assertEqual(response.json()['count'],0)
