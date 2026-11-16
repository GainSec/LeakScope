from unittest.mock import patch
from django.test import TestCase
from leakscope.phase3 import jobs
from leakscope_app.models import SavedLookup, ServiceEndpoint, AssetEvidence, CustomQuery
from leakscope.phase3 import relationships as store


class DemoExecutionTests(TestCase):
    def setUp(self):
        self.lookup=SavedLookup.objects.create(name='Normal configured lookup',provider='shodan',query='test',service_type='http',collection_mode='full',auto_fetch=True,actions=['screenshot'],page_count=20,page_size=100,max_results=10000,enabled=False)

    @patch('leakscope.phase3.jobs.queue_capture')
    @patch('leakscope.phase3.jobs.search_provider')
    def test_one_off_demo_clamps_worker_and_preserves_saved_settings(self,search,capture):
        search.return_value={'observations':[{'address':'192.0.2.1','port':80,'metadata':{}},{'address':'192.0.2.2','port':80,'metadata':{}}],'total':200,'partial':True,'pages':1}
        response=self.client.post(f'/api/operations/lookups/{self.lookup.pk}/run',{'demo':True},content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        run=response.json()['run'];self.assertEqual(run['state'],'succeeded')
        self.assertEqual(search.call_args.kwargs,{'page_count':1,'start_page':1,'page_size':1,'demo':True})
        self.assertEqual(run['result']['stored'],1);self.assertTrue(run['result']['demo'])
        capture.assert_not_called()
        self.lookup.refresh_from_db()
        self.assertEqual((self.lookup.collection_mode,self.lookup.page_count,self.lookup.page_size,self.lookup.max_results),('full',20,100,10000))
        self.assertTrue(self.lookup.auto_fetch);self.assertFalse(self.lookup.enabled)

    @patch('leakscope.phase3.jobs.search_provider')
    def test_saved_demo_snapshot_is_frozen_minimal_for_scheduled_execution(self,search):
        self.lookup.collection_mode='demo';self.lookup.save(update_fields=['collection_mode'])
        snapshot=jobs.lookup_snapshot(self.lookup)
        self.assertEqual(snapshot['page_count'],1);self.assertEqual(snapshot['max_results'],1);self.assertFalse(snapshot['auto_fetch'])
        self.assertEqual(self.lookup.page_count,20)

    def test_discovery_demo_preview_counts_each_selected_query(self):
        response=self.client.post('/api/query-preview',{'provider':'zoomeye','types':['elastic','mongo'],'query_mode':'fingerprint','collection_mode':'demo','page_count':20,'zoomeye_pagesize':100},content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        data=response.json();self.assertFalse(data['collection']['target_requests'])
        self.assertEqual(data['estimate']['pages'],2)
        self.assertTrue(data['demo']);self.assertEqual(data['usage_policy']['max_results'],1)

    def test_research_lookup_can_save_demo_without_automatic_target_fetches(self):
        response=self.client.post('/api/operations/lookups',{'name':'Demo research','provider':'research','query':'new database technology','collection_mode':'demo','page_count':20,'auto_fetch':True},content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        self.assertFalse(response.json()['lookup']['auto_fetch'])

    def test_invalid_demo_flag_fails_before_any_task_submission(self):
        with patch('leakscope_app.operations.queue_lookup') as queue:
            response=self.client.post(f'/api/operations/lookups/{self.lookup.pk}/run',{'demo':'false'},content_type='application/json')
        self.assertEqual(response.status_code,400);queue.assert_not_called()

    @patch('leakscope.phase3.jobs.run_lookup.apply_async')
    def test_custom_query_demo_freezes_first_page_and_disables_legacy_active_probe(self,submit):
        q=CustomQuery.objects.create(name='Query',query_template='product:test',provider='shodan',default_type='elastic',active_probe_default=True)
        response=self.client.post(f'/api/queries/{q.pk}/run',{'demo':True,'page':5,'page_count':20},content_type='application/json')
        self.assertEqual(response.status_code,202,response.content)
        kwargs=submit.call_args.kwargs['kwargs']['snapshot'];self.assertEqual(kwargs['page_count'],1);self.assertEqual(kwargs['max_results'],1)
        self.assertEqual(kwargs['collection_mode'],'demo');self.assertFalse(kwargs['auto_fetch'])
        q.refresh_from_db();self.assertTrue(q.active_probe_default)

    @patch('leakscope.phase3.asset_jobs.free_registry.discover_organization')
    @patch('leakscope.phase3.asset_jobs.discover_organization')
    def test_organization_demo_separates_cache_and_paid_optin_uses_one_request(self,paid,free):
        from leakscope.phase3.asset_jobs import queue_enrichment
        org=store.node('organization','Demo Org')
        free.return_value={'organization':org.value,'networks':[],'domains':[],'entities':[],'requests':1,'errors':[],'partial':True}
        paid.return_value={'organization':org.value,'networks':[],'domains':[],'requests':1,'errors':[],'partial':True}
        demo=queue_enrichment('organization',org.value,organization_id=str(org.pk),include_netlas=True,demo=True)
        self.assertEqual(demo['run']['state'],'succeeded')
        self.assertTrue(paid.call_args.kwargs['demo']);self.assertEqual(paid.call_args.kwargs['max_requests'],1)
        self.assertEqual(free.call_args.kwargs['max_requests'],1)
        normal=queue_enrichment('organization',org.value,organization_id=str(org.pk),include_netlas=True)
        self.assertFalse(normal['cached']);self.assertNotIn('demo',paid.call_args.kwargs)
        self.assertEqual(paid.call_args.kwargs['max_requests'],2)

    @patch('leakscope.phase3.ics_jobs._request')
    @patch('leakscope.phase3.ics_jobs.queue_capture')
    @patch('leakscope.phase3.ics_jobs.get_config',return_value={'config':{'SHODAN_API_KEY':'fixture'}})
    def test_related_demo_uses_one_index_request_and_no_active_capture(self,config,capture,request):
        from leakscope.phase3.state import ingest_observation
        endpoint=ingest_observation('fixture',{'address':'192.0.2.25','port':502,'service_type':'modbus','metadata':{}})
        request.return_value={'data':[{'port':80,'transport':'tcp','product':'HTTP','timestamp':'2026-11-08T00:00:00Z'},{'port':443,'transport':'tcp','product':'HTTP'}]}
        response=self.client.post('/api/ics/related',{'endpoint_id':str(endpoint.pk),'provider':'shodan','collection_mode':'demo','max_results':100},content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        run=response.json()['run'];self.assertEqual(run['state'],'succeeded',run)
        self.assertEqual(run['result']['stored'],1);self.assertTrue(run['result']['demo'])
        request.assert_called_once();capture.assert_not_called()

    @patch('leakscope.phase3.asset_jobs.free_registry.discover_organization')
    def test_valid_full_cached_evidence_can_supply_demo_without_new_provider_call(self,free):
        from leakscope.phase3.asset_jobs import queue_enrichment
        org=store.node('organization','Cached Org')
        free.return_value={'organization':org.value,'domains':[],'networks':[{'cidr':'192.0.2.0/24'},{'cidr':'198.51.100.0/24'}],'requests':4,'partial':False,'errors':[]}
        queue_enrichment('organization',org.value,organization_id=str(org.pk))
        demo=queue_enrichment('organization',org.value,organization_id=str(org.pk),demo=True)
        self.assertTrue(demo['cached']);self.assertTrue(demo['result']['demo']);self.assertEqual(demo['result']['requests'],0)
        self.assertEqual(len(demo['result']['networks']),1);free.assert_called_once()

    def test_saved_demo_is_a_valid_model_choice(self):
        self.lookup.collection_mode='demo'
        self.lookup.scope={'network':'192.0.2.0/24'}
        self.lookup.full_clean()
        self.assertEqual(self.lookup.get_collection_mode_display(),'Demo')

    @patch('leakscope.phase3.jobs._claim_daily_budget',return_value=False)
    def test_skipped_demo_run_still_reports_effective_mode_without_spending(self,budget):
        import json
        run=jobs.queue_lookup(self.lookup,demo=True)
        self.assertEqual(run.state,'skipped');self.assertEqual(json.loads(run.parameters)['collection_mode'],'demo')
        self.assertEqual(self.lookup.collection_mode,'full')

    @patch('leakscope.phase3.jobs.search_provider')
    def test_worker_rejects_invalid_collection_mode_before_any_provider_call(self,search):
        with self.assertRaises(ValueError):
            jobs.run_lookup.run(str(self.lookup.pk),snapshot=dict(jobs.lookup_snapshot(self.lookup),collection_mode='Demo'))
        search.assert_not_called()
