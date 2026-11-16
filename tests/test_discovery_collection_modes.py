import json
from unittest.mock import patch
from django.test import TestCase
from leakscope_app.models import SavedLookup, Run, Search, ServiceEndpoint
from leakscope.phase3.jobs import lookup_snapshot, queue_lookup, run_lookup


class DiscoveryCollectionAPITests(TestCase):
    def post(self,path,body):return self.client.post(path,json.dumps(body),content_type='application/json')
    def request(self,**overrides):return {'types':['elastic'],'provider':'shodan','query_mode':'fingerprint',**overrides}

    @patch('leakscope.phase3.jobs.queue_lookup')
    def test_discover_defaults_to_passive_without_enabling_fetch_actions(self,queue):
        queue.return_value=Run.objects.create(kind='lookup')
        response=self.post('/api/discover',self.request())
        self.assertEqual(response.status_code,202,response.content)
        lookup=SavedLookup.objects.get()
        self.assertEqual(lookup.collection_mode,'passive')
        self.assertFalse(lookup.auto_fetch);self.assertEqual(lookup.actions,[])
        self.assertEqual(response.json()['results_url'],'/assets/')

    @patch('leakscope.phase3.jobs.queue_lookup')
    def test_explicit_modes_are_persisted_separately_from_query_strategy(self,queue):
        queue.return_value=Run.objects.create(kind='lookup')
        for mode in ('passive','semi_passive','screenshot','full'):
            response=self.post('/api/discover',self.request(collection_mode=mode,query_mode='nonstandard'))
            self.assertEqual(response.status_code,202,response.content)
            lookup=SavedLookup.objects.latest('created_at')
            self.assertEqual(lookup.collection_mode,mode)
            self.assertEqual(lookup.auto_fetch,mode!='passive')
            self.assertEqual(lookup.actions,[])
            self.assertIn('-port:9200',lookup.query)

    @patch('leakscope.phase3.jobs.queue_lookup')
    @patch('leakscope.tasks.check_main.apply_async')
    def test_legacy_query_syntax_also_uses_provider_only_normalized_pipeline(self,legacy,queue):
        queue.return_value=Run.objects.create(kind='lookup')
        response=self.post('/api/discover',self.request(query_mode='legacy'))
        self.assertEqual(response.status_code,202,response.content)
        self.assertEqual(SavedLookup.objects.get().collection_mode,'passive')
        self.assertEqual(Search.objects.count(),0)
        legacy.assert_not_called();queue.assert_called_once()
        self.assertEqual(response.json()['results_url'],'/assets/')

    @patch('leakscope.phase3.jobs.queue_lookup')
    def test_invalid_modes_or_legacy_active_checkbox_do_not_create_work(self,queue):
        for mode in ('surprise','',None,True):
            response=self.post('/api/discover',self.request(collection_mode=mode))
            self.assertEqual(response.status_code,400)
        response=self.post('/api/discover',self.request(query_mode='legacy',active_probe=True))
        self.assertEqual(response.status_code,400)
        self.assertEqual(SavedLookup.objects.count(),0);queue.assert_not_called()

    @patch('leakscope.phase3.jobs.queue_capture')
    def test_query_preview_describes_collection_mode_without_creating_jobs(self,capture):
        response=self.post('/api/query-preview',self.request(collection_mode='full'))
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(response.json()['collection']['mode'],'full')
        self.assertEqual(SavedLookup.objects.count(),0);capture.assert_not_called()

    def test_scheduled_lookup_mode_preserves_custom_behavior_and_forces_passive(self):
        response=self.post('/api/operations/lookups',{'name':'Explicit passive','provider':'shodan','query':'"fixture"','collection_mode':'passive','actions':['http'],'auto_fetch':True})
        self.assertEqual(response.status_code,200,response.content)
        lookup=SavedLookup.objects.get()
        self.assertEqual(lookup.collection_mode,'passive');self.assertFalse(lookup.auto_fetch);self.assertEqual(lookup.actions,[])
        response=self.post('/api/operations/lookups',{'name':'Existing custom','provider':'shodan','query':'"fixture"','actions':['http'],'auto_fetch':True})
        self.assertEqual(response.status_code,200,response.content)
        lookup=SavedLookup.objects.get(name='Existing custom')
        self.assertEqual(lookup.collection_mode,'');self.assertTrue(lookup.auto_fetch);self.assertEqual(lookup.actions,['http'])
        dto=self.client.get('/api/operations/lookups').json()['results']
        self.assertEqual(next(x for x in dto if x['id']==str(lookup.pk))['collection_mode'],'')

    @patch('leakscope.phase3.jobs.enqueue')
    def test_queue_freezes_mode_with_lookup_snapshot(self,enqueue):
        lookup=SavedLookup.objects.create(name='Frozen',provider='shodan',query='"fixture"',collection_mode='semi_passive')
        enqueue.return_value=Run.objects.create(kind='lookup')
        queue_lookup(lookup)
        frozen=enqueue.call_args.kwargs['kwargs']['snapshot']
        lookup.collection_mode='passive';lookup.save()
        self.assertEqual(frozen['collection_mode'],'semi_passive')
        self.assertEqual(enqueue.call_args.kwargs['parameters']['collection_mode'],'semi_passive')


class DiscoveryCollectionJobTests(TestCase):
    def lookup(self,mode):return SavedLookup.objects.create(name='Fixture',provider='shodan',query='"fixture"',collection_mode=mode,auto_fetch=True,actions=['http'])
    def observations(self):return [
        {'address':'192.0.2.31','port':41111,'transport':'tcp','scheme':'http','service_type':'http'},
        {'address':'192.0.2.32','port':41112,'transport':'tcp','service_type':'modbus'},
        {'address':'192.0.2.33','port':41113,'transport':'tcp','service_type':'unknown'},
        {'address':'192.0.2.34','port':41114,'transport':'udp','service_type':'unknown'},
    ]
    @patch('leakscope.phase3.jobs.queue_capture')
    @patch('leakscope.phase3.jobs.search_provider')
    def test_passive_never_queues_target_contact_even_if_old_flags_are_true(self,search,capture):
        search.return_value={'observations':self.observations()}
        result=run_lookup.apply(args=[str(self.lookup('passive').pk)]).get()
        self.assertEqual(result['stored'],4);capture.assert_not_called()
        self.assertEqual(result['collection']['mode'],'passive')
        self.assertEqual(result['collection']['queued'],0)

    @patch('leakscope.phase3.jobs.queue_capture')
    @patch('leakscope.phase3.jobs.search_provider')
    def test_full_plans_from_actual_endpoint_and_keeps_unsupported_observation(self,search,capture):
        search.return_value={'observations':self.observations()};capture.return_value=Run.objects.create(kind='capture')
        result=run_lookup.apply(args=[str(self.lookup('full').pk)]).get()
        self.assertEqual(ServiceEndpoint.objects.count(),4);self.assertEqual(capture.call_count,3)
        plans=[set(call.args[1]) for call in capture.call_args_list]
        self.assertEqual(plans,[{'http','httpx','screenshot'},{'identity'},{'check','tcp'}])
        self.assertEqual(result['collection']['queued'],3);self.assertEqual(result['collection']['skipped'],1)
        self.assertTrue(all(c.kwargs['collection_mode']=='full' for c in capture.call_args_list))

    @patch('leakscope.phase3.jobs.queue_capture')
    @patch('leakscope.phase3.jobs.search_provider')
    def test_frozen_mode_wins_over_later_saved_lookup_edits(self,search,capture):
        lookup=self.lookup('semi_passive');frozen=lookup_snapshot(lookup)
        lookup.collection_mode='passive';lookup.save();search.return_value={'observations':[self.observations()[2]]}
        capture.return_value=Run.objects.create(kind='capture')
        result=run_lookup.apply(args=[str(lookup.pk)],kwargs={'snapshot':frozen}).get()
        self.assertEqual(capture.call_args.args[1],['check'])
        self.assertEqual(result['collection']['mode'],'semi_passive')

    @patch('leakscope.phase3.jobs.queue_capture')
    @patch('leakscope.phase3.jobs.search_provider')
    def test_one_capture_queue_failure_does_not_discard_provider_observations(self,search,capture):
        search.return_value={'observations':self.observations()};capture.side_effect=ValueError('fixture queue failure')
        result=run_lookup.apply(args=[str(self.lookup('semi_passive').pk)]).get()
        self.assertEqual(result['stored'],4);self.assertEqual(ServiceEndpoint.objects.count(),4)
        self.assertEqual(result['collection']['failed'],3);self.assertTrue(result['partial'])

    @patch('leakscope.phase3.jobs.queue_capture')
    @patch('leakscope.phase3.jobs.search_provider')
    def test_one_capture_planning_failure_does_not_drop_later_observations(self,search,capture):
        search.return_value={'observations':[
            {'address':'fe80::1%bad zone','port':41115,'transport':'tcp','service_type':'unknown'},
            self.observations()[2]]}
        capture.return_value=Run.objects.create(kind='capture')
        result=run_lookup.apply(args=[str(self.lookup('semi_passive').pk)]).get()
        self.assertEqual(result['stored'],2);self.assertEqual(ServiceEndpoint.objects.count(),2)
        self.assertEqual(result['collection']['failed'],1);self.assertEqual(result['collection']['queued'],1)
        self.assertTrue(result['partial'])


class CollectionEvidenceTests(TestCase):
    def test_collection_policy_and_check_timing_do_not_create_false_response_changes(self):
        from leakscope.phase3.analysis import stable_evidence
        first={'metadata':{'reachable':True,'duration_ms':1.2,'collection_mode':'semi_passive','application':{'duration_ms':9}}}
        second={'metadata':{'reachable':True,'duration_ms':9.8,'collection_mode':'full','application':{'duration_ms':9}}}
        self.assertEqual(stable_evidence(first),stable_evidence(second))
        second['metadata']['application']['duration_ms']=10
        self.assertNotEqual(stable_evidence(first),stable_evidence(second))

    def test_old_lookup_insert_without_mode_column_keeps_custom_behavior(self):
        from django.db import connection
        import uuid
        row=SavedLookup.objects.create(name='Existing worker schema',provider='shodan',query='"fixture"',auto_fetch=True,actions=['http'])
        row.pk=uuid.uuid4()
        fields=[f for f in SavedLookup._meta.concrete_fields if f.name!='collection_mode']
        columns=', '.join(connection.ops.quote_name(f.column) for f in fields)
        values=[f.get_db_prep_save(getattr(row,f.attname),connection) for f in fields]
        with connection.cursor() as cursor:
            cursor.execute('INSERT INTO '+connection.ops.quote_name(SavedLookup._meta.db_table)+' ('+columns+') VALUES ('+', '.join(['%s']*len(fields))+')',values)
        saved=SavedLookup.objects.get(pk=row.pk)
        self.assertEqual(saved.collection_mode,'');self.assertTrue(saved.auto_fetch);self.assertEqual(saved.actions,['http'])

    @patch('leakscope.phase3.jobs._claim_daily_budget',return_value=False)
    def test_budget_skipped_lookup_still_records_the_selected_mode(self,budget):
        lookup=SavedLookup.objects.create(name='Limited full',provider='shodan',query='"fixture"',collection_mode='full')
        run=queue_lookup(lookup)
        self.assertEqual(run.state,'skipped')
        self.assertEqual(json.loads(run.parameters)['collection_mode'],'full')

    @patch('leakscope.phase3.jobs.run_lookup.apply_async')
    def test_scheduled_slot_records_its_frozen_mode_in_run_parameters(self,submit):
        from django.utils import timezone
        from leakscope_app.models import DispatchSlot
        from leakscope.phase3.jobs import _submit_slot
        lookup=SavedLookup.objects.create(name='Scheduled semi',provider='shodan',query='"fixture"',collection_mode='semi_passive',enabled=True)
        slot=DispatchSlot.objects.create(lookup=lookup,scheduled_for=timezone.now())
        _submit_slot(slot.pk)
        slot.refresh_from_db()
        self.assertEqual(json.loads(slot.run.parameters)['collection_mode'],'semi_passive')
        self.assertEqual(slot.snapshot['collection_mode'],'semi_passive')
