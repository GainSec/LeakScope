"""Fixture-only Demo request budgets; never contact paid providers."""
import json
from unittest.mock import Mock, patch

from celery.exceptions import Ignore
from django.test import SimpleTestCase, TestCase
from leakscope.phase3 import providers, enrichment, registry
from tests.test_phase3_providers import Response


CONFIG={'SHODAN_API_KEY':'fixture-shodan','NETLAS_API_KEY':'fixture-netlas','ZOOMEYE_API_KEY':'fixture-zoomeye'}


def page(provider):
    rows=[{'ip_str':f'192.0.2.{i}','port':443,'transport':'tcp','http':{'title':'Fixture'},'_shodan':{'module':'https'}} for i in range(1,4)]
    if provider=='shodan':return {'matches':rows,'total':400}
    if provider=='zoomeye':return {'code':60000,'data':[{'ip':row['ip_str'],'port':443,'service':'https','protocol':'tcp'} for row in rows],'total':400}
    return {'items':[{'_id':str(i),'data':{'ip':row['ip_str'],'port':443,'prot4':'tcp','protocol':'https'}} for i,row in enumerate(rows)],'total':400}


class DemoProviderTests(SimpleTestCase):
    @patch('leakscope.phase3.providers.requests.request')
    def test_search_has_one_wire_request_and_one_usable_observation_for_every_provider(self,request):
        for name in ('shodan','zoomeye','netlas'):
            with self.subTest(provider=name):
                request.reset_mock();request.return_value=Response(page(name))
                result=providers.search_provider(name,'fixture',CONFIG,page_count=20,page_size=100,demo=True)
                self.assertEqual(request.call_count,1)
                self.assertEqual(len(result['observations']),1)
                self.assertEqual(result['pages'],1);self.assertEqual(result['total'],400)
                self.assertTrue(result['partial']);self.assertTrue(result['more_available']);self.assertTrue(result['demo'])
                policy=result['usage_policy'];self.assertEqual(policy['max_requests'],1);self.assertEqual(policy['max_results'],1)
                call=request.call_args.kwargs
                if name=='zoomeye':self.assertEqual(call['json']['pagesize'],1);self.assertEqual(call['json']['page'],1)
                elif name=='shodan':self.assertEqual(call['params']['page'],1);self.assertNotIn('size',call['params'])
                else:self.assertEqual(call['params']['start'],0);self.assertNotIn('size',call['params']);self.assertNotIn('limit',call['params'])

    @patch('leakscope.phase3.providers.requests.request')
    def test_demo_does_not_retry_or_fetch_more_pages_when_rate_limited(self,request):
        request.return_value=Response(status=429,headers={'Retry-After':'10'})
        with self.assertRaises(providers.ProviderRateLimit):providers.search_provider('netlas','fixture',CONFIG,page_count=20,demo=True)
        self.assertEqual(request.call_count,1)

    @patch('leakscope.phase3.providers.requests.request')
    def test_demo_finds_first_usable_record_on_received_page_and_never_claims_complete_sample(self,request):
        data=page('netlas');data['items'].insert(0,{'data':{'ip':'invalid','port':443}});request.return_value=Response(data)
        result=providers.search_provider('netlas','fixture',CONFIG,demo=True)
        self.assertEqual(len(result['observations']),1);self.assertEqual(result['observations'][0]['address'],'192.0.2.1')
        self.assertEqual(request.call_count,1);self.assertTrue(result['partial'])

    @patch('leakscope.phase3.providers.requests.request')
    def test_demo_false_keeps_normal_paging_and_omits_demo_metadata(self,request):
        first=page('netlas');first['items']=first['items']*7
        request.side_effect=[Response(first),Response({'items':[]})]
        result=providers.search_provider('netlas','fixture',CONFIG,page_count=2,demo=False)
        self.assertEqual(request.call_count,2);self.assertNotIn('demo',result)
        self.assertEqual(request.call_args.kwargs['params']['start'],20)

    @patch('leakscope.phase3.providers.requests.request')
    def test_demo_requires_boolean_before_any_request(self,request):
        for value in ('true',1,[],None):
            with self.subTest(value=value),self.assertRaises(ValueError):providers.search_provider('shodan','fixture',CONFIG,demo=value)
        request.assert_not_called()

    @patch('leakscope.phase3.providers.requests.request')
    def test_other_demo_collectors_require_boolean_before_any_request(self,request):
        for value in ('true',1,[],None):
            with self.subTest(value=value),self.assertRaises(ValueError):enrichment.enrich_host('example.com',CONFIG,demo=value)
            with self.subTest(value=value),self.assertRaises(ValueError):registry.discover_organization('Fixture',CONFIG,demo=value)
        request.assert_not_called()

    def test_preview_policy_is_local_and_explicit_about_fixed_page_units(self):
        for name,size in [('shodan',100),('netlas',20),('zoomeye',1)]:
            limits=providers.demo_provider_policy(name)
            self.assertEqual(limits['wire_page_size'],size);self.assertEqual(limits['max_requests'],1)
            self.assertEqual(limits['page_size_adjustable'],name=='zoomeye')
        with self.assertRaises(ValueError):providers.demo_provider_policy('unknown')


class DemoEnrichmentTests(SimpleTestCase):
    @patch('leakscope.phase3.providers.requests.request')
    def test_enrichment_uses_first_selected_supported_collection_only(self,request):
        request.return_value=Response({'items':[{'data':{'domain':f'{i}.example.com'}} for i in range(4)]})
        result=enrichment.enrich_host('example.com',CONFIG,['dns','certificates','host'],demo=True)
        self.assertEqual(request.call_count,1);self.assertTrue(request.call_args.args[1].endswith('/domains/'))
        self.assertEqual(len(result['results']['dns']),1)
        self.assertCountEqual(result['omitted_collections'],['certificates','host'])
        self.assertTrue(result['demo']);self.assertTrue(result['partial'])
        self.assertEqual(request.call_args.kwargs['params'],{'q':'domain:"example.com"','start':0})

    @patch('leakscope.phase3.providers.requests.request')
    def test_unsupported_ipv6_host_summary_does_not_consume_demo_request_budget(self,request):
        request.return_value=Response({'items':[{'data':{'domain':'example.com'}}]})
        result=enrichment.enrich_host('2001:db8::1',CONFIG,['host','dns','certificates'],demo=True)
        self.assertEqual(request.call_count,1);self.assertTrue(request.call_args.args[1].endswith('/domains/'))
        self.assertCountEqual(result['omitted_collections'],['host','certificates'])

    @patch('leakscope.phase3.providers.requests.request')
    def test_demo_selected_collection_failure_does_not_fall_back_to_other_paid_collection(self,request):
        request.return_value=Response(status=403)
        with self.assertRaises(ValueError):enrichment.enrich_host('example.com',CONFIG,['host','dns'],demo=True)
        self.assertEqual(request.call_count,1)


class DemoRegistryTests(SimpleTestCase):
    @patch('leakscope.phase3.registry._request')
    def test_whois_demo_makes_one_request_and_retains_one_usable_candidate(self,request):
        request.return_value={'items':[{'data':{'domain':'bad domain'}},{'data':{'domain':'one.example','registrant':{'organization':'Fixture'}}},{'data':{'domain':'two.example','registrant':{'organization':'Fixture'}}}],'total':100}
        result=registry.discover_organization('Fixture',CONFIG,max_requests=2,demo=True)
        self.assertEqual(request.call_count,1);self.assertEqual(request.call_args.args[1],'/whois_domains/')
        self.assertEqual(result['requests'],1);self.assertEqual(len(result['domains']),1)
        self.assertEqual(result['domains'][0]['name'],'one.example')
        self.assertTrue(result['partial']);self.assertTrue(result['demo'])
        self.assertEqual(result['omitted_collections'],['/whois_ip/'])
        self.assertEqual(request.call_args.kwargs['params']['start'],0);self.assertNotIn('size',request.call_args.kwargs['params'])

    @patch('leakscope.phase3.registry._request')
    def test_whois_demo_denial_never_spends_second_request(self,request):
        request.side_effect=ValueError('HTTP403')
        result=registry.discover_organization('Fixture',CONFIG,max_requests=2,demo=True)
        self.assertEqual(request.call_count,1);self.assertEqual(result['requests'],1);self.assertTrue(result['partial'])


class DemoLegacyTaskTests(TestCase):
    @patch('leakscope.tasks.publish_state')
    @patch('leakscope.tasks.ProgressRecorder')
    @patch('leakscope.tasks.dispatch_discovery')
    @patch('leakscope.tasks.get_config',return_value={'config':CONFIG})
    @patch('leakscope.phase3.providers.requests.request')
    def test_legacy_custom_demo_forces_first_page_one_saved_match_and_no_handlers(self,request,config,dispatch,progress,publish):
        from leakscope import tasks
        from leakscope_app.models import CustomQuery,ServiceEndpoint
        query=CustomQuery.objects.create(name='Fixture',provider='shodan',query_template='fixture',default_type='gitlab',active_probe_default=True)
        request.return_value=Response(page('shodan'))
        with self.assertRaises(Ignore):tasks.check_custom_query.run(query.pk,page=20,page_count=20,active_probe=True,demo=True)
        self.assertEqual(request.call_count,1);self.assertEqual(request.call_args.kwargs['params']['page'],1)
        dispatch.assert_not_called();self.assertEqual(ServiceEndpoint.objects.count(),1)
        meta=publish.call_args.kwargs['meta'];self.assertTrue(meta['demo']);self.assertTrue(meta['partial']);self.assertEqual(meta['total'],1)
        self.assertEqual(len(meta['endpoint_ids']),1);self.assertEqual(meta['results_url'],'/assets/')
        query.refresh_from_db();self.assertTrue(query.active_probe_default)

    @patch('leakscope.tasks.publish_state')
    @patch('leakscope.tasks.ProgressRecorder')
    @patch('leakscope.tasks.dispatch_discovery')
    @patch('leakscope.tasks.get_config',return_value={'config':CONFIG})
    @patch('leakscope.phase3.providers.requests.request')
    def test_legacy_main_demo_caps_zoom_wire_page_and_never_runs_legacy_handlers(self,request,config,dispatch,progress,publish):
        from leakscope import tasks
        from leakscope_app.models import Search,ServiceEndpoint
        search=Search.objects.create(type='gitlab',provider='zoomeye')
        request.return_value=Response(page('zoomeye'))
        with self.assertRaises(Ignore):tasks.check_main.run(search.pk,type='gitlab',provider='zoomeye',page=10,page_count=20,zoomeye_pagesize=100,active_probe=True,demo=True)
        self.assertEqual(request.call_count,1);self.assertEqual(request.call_args.kwargs['json']['page'],1)
        self.assertEqual(request.call_args.kwargs['json']['pagesize'],1)
        dispatch.assert_not_called();self.assertEqual(ServiceEndpoint.objects.count(),1)

    @patch('leakscope.tasks.publish_state')
    @patch('leakscope.tasks.ProgressRecorder')
    @patch('leakscope.tasks.get_config',return_value={'config':CONFIG})
    @patch('leakscope.phase3.providers.requests.request')
    def test_legacy_main_demo_does_not_try_additional_query_variants(self,request,config,progress,publish):
        from leakscope import tasks
        from leakscope_app.models import Search
        search=Search.objects.create(type='registry',provider='shodan')
        request.return_value=Response(status=400)
        with self.assertRaises(Ignore):tasks.check_main.run(search.pk,type='registry',provider='shodan',demo=True)
        self.assertEqual(request.call_count,1)
        self.assertEqual(publish.call_args.kwargs['state'],'FAILURE')

    @patch('leakscope.tasks.publish_state')
    @patch('leakscope.tasks.ProgressRecorder')
    @patch('leakscope.tasks.dispatch_discovery',return_value={})
    @patch('leakscope.tasks.get_config',return_value={'config':CONFIG})
    @patch('leakscope.tasks.get_shodan_client')
    def test_normal_legacy_custom_query_keeps_existing_pages_and_handler_behavior(self,client,config,dispatch,progress,publish):
        from leakscope import tasks
        from leakscope_app.models import CustomQuery
        query=CustomQuery.objects.create(name='Fixture',provider='shodan',query_template='fixture',default_type='gitlab',active_probe_default=True)
        client.return_value.host_search.return_value=page('shodan')
        with self.assertRaises(Ignore):tasks.check_custom_query.run(query.pk,page=2,page_count=2,demo=False)
        self.assertEqual(client.return_value.host_search.call_count,2)
        self.assertEqual([c.kwargs['page'] for c in client.return_value.host_search.call_args_list],[2,3])
        self.assertEqual(dispatch.call_count,6);self.assertTrue(dispatch.call_args.kwargs['active_probe'])

    @patch('leakscope.tasks.ProgressRecorder')
    @patch('leakscope.tasks.get_config',return_value={'config':CONFIG})
    @patch('leakscope.phase3.providers.requests.request')
    def test_legacy_demo_never_falls_back_to_another_provider(self,request,config,progress):
        from leakscope import tasks
        from leakscope_app.models import Search,CustomQuery
        search=Search.objects.create(type='gitlab',provider='shodan')
        query=CustomQuery.objects.create(name='Fixture',provider='netlas',query_template='fixture',default_type='gitlab')
        with self.assertRaises(ValueError):tasks.check_main.run(search.pk,type='gitlab',provider='invalid',demo=True)
        with self.assertRaises(ValueError):tasks.check_custom_query.run(query.pk,provider='netlas',demo=True)
        request.assert_not_called()
        query.refresh_from_db();self.assertNotEqual(query.last_status,'running')

    @patch('leakscope.phase3.providers.requests.request')
    def test_legacy_demo_profile_fallback_is_labeled_unverified(self,request):
        from leakscope.tasks import _demo_query_result
        from leakscope_app.models import ServiceObservation
        request.return_value=Response({'matches':[{'ip_str':'192.0.2.21','port':49111}], 'total':1})
        result=_demo_query_result('shodan','fixture',CONFIG,'mongo')
        self.assertEqual(result['stored'],1)
        metadata=ServiceObservation.objects.get().data['metadata']
        self.assertIn('identity not verified',metadata['service_type_basis'])
