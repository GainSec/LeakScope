"""Demo request budgets tested at the HTTP payload boundary; no live integrations."""
import json
from unittest.mock import patch
from django.test import SimpleTestCase, TestCase
from leakscope.intelligence.clients import ask_endpoint, research_queries
from leakscope.phase3.web_services import research_services
from tests.test_intelligence_clients import Response

CONFIG={'LLM_API_BASE':'http://model.fixture/v1','LLM_MODEL':'fixture-model','LLM_API_KEY':'fixture-private',
        'FIRECRAWL_API_URL':'http://firecrawl.fixture/v2'}


def answer(text='Fixture answer',finish='stop'):
    return Response({'choices':[{'message':{'content':text},'finish_reason':finish}], 'usage':{'completion_tokens':8}})


def sources():
    return Response({'success':True,'data':{'web':[
        {'url':'https://vendor.example/'+str(i),'title':'Fixture Server','markdown':'Fixture Server has an HTTP title of Fixture Server.'}
        for i in range(4)]}})


def suggestion(title='First'):
    return {'title':title,'technology':'Fixture Server','rationale':'Documented value in S1',
            'signals':[{'field':'title','value':'Fixture Server','source_ids':['S1']}]}


class DemoIntelligenceClientTests(SimpleTestCase):
    @patch('leakscope.intelligence.clients.requests.post')
    def test_ask_demo_sends_one_bounded_sample_and_preserves_question_and_model(self,post):
        post.return_value=answer()
        context={'findings':[{'reference':'F'+str(i),'facts':{'body':'公開 evidence '*2000,'title':'Fixture'}} for i in range(3)]}
        original=json.dumps(context)
        config=dict(CONFIG,LLM_MAX_TOKENS=1500)
        result=ask_endpoint('Explain all selected findings?',context,config,demo=True)
        self.assertEqual(post.call_count,1)
        payload=post.call_args.kwargs['json'];user=json.loads(payload['messages'][1]['content'])
        self.assertEqual(payload['model'],'fixture-model');self.assertEqual(payload['max_tokens'],256)
        self.assertEqual(user['question'],'Explain all selected findings?')
        self.assertLessEqual(len(json.dumps(user['untrusted_selected_evidence'],ensure_ascii=False).encode()),4096)
        self.assertEqual(user['untrusted_selected_evidence']['findings'][0]['reference'],'F0')
        self.assertEqual(len(user['untrusted_selected_evidence']['findings']),1)
        self.assertTrue(result['demo']);self.assertTrue(result['partial'])
        self.assertTrue(result['usage_policy']['context_limited'])
        self.assertEqual(result['usage_policy']['selected_findings'],3)
        self.assertEqual(result['usage_policy']['sampled_findings'],1)
        self.assertEqual(result['context_sent'],user['untrusted_selected_evidence'])
        self.assertEqual(json.dumps(context),original);self.assertEqual(config['LLM_MAX_TOKENS'],1500)
        self.assertEqual(set(payload),{'model','max_tokens','messages'})

    @patch('leakscope.intelligence.clients.requests.post')
    def test_ask_demo_respects_lower_configured_limit_and_reports_truncation(self,post):
        post.return_value=answer(finish='length')
        result=ask_endpoint('Explain',{},dict(CONFIG,LLM_MAX_TOKENS=64),demo=True)
        self.assertEqual(post.call_args.kwargs['json']['max_tokens'],64)
        self.assertTrue(result['usage_policy']['response_truncated'])
        self.assertIn('token',' '.join(result['warnings']).lower())

    @patch('leakscope.intelligence.clients.requests.post')
    def test_context_budget_is_enforced_after_redaction_and_deep_data_sampling(self,post):
        post.return_value=answer()
        context={'findings':[{'reference':'F1','facts':{'body':'a'*8000}}],
                 'deep':{'a':{'b':{'c':{'d':{'e':{'f':{'g':'nested'}}}}}}}}
        result=ask_endpoint('Explain',context,dict(CONFIG,LLM_API_KEY='a'),demo=True)
        sent=json.loads(post.call_args.kwargs['json']['messages'][1]['content'])['untrusted_selected_evidence']
        size=len(json.dumps(sent,ensure_ascii=False).encode('utf-8'))
        self.assertLessEqual(size,4096)
        self.assertEqual(result['usage_policy']['sent_context_bytes'],size)
        self.assertTrue(result['usage_policy']['context_limited'])
        self.assertEqual(post.call_count,1)

    @patch('leakscope.intelligence.clients.requests.post')
    def test_normal_ask_payload_and_return_shape_are_unchanged(self,post):
        post.return_value=answer()
        result=ask_endpoint('Explain',{'findings':[]},CONFIG)
        self.assertEqual(post.call_args.kwargs['json']['max_tokens'],1500)
        self.assertEqual(set(result),{'answer','model','usage'})
        self.assertNotIn('demo',post.call_args.kwargs['json'])

    @patch('leakscope.intelligence.clients.requests.post')
    def test_demo_flag_is_strict_and_invalid_values_never_make_requests(self,post):
        for value in ['true','false',1,None,{},[]]:
            for fn,args in [(ask_endpoint,('Explain',{},CONFIG)),(research_queries,('Fixture',CONFIG))]:
                with self.subTest(value=value,fn=fn),self.assertRaises(ValueError):fn(*args,demo=value)
        post.assert_not_called()

    @patch('leakscope.intelligence.clients.requests.post')
    def test_demo_research_searches_one_source_and_synthesizes_one_grounded_draft(self,post):
        post.side_effect=[sources(),answer(json.dumps({'suggestions':[suggestion(),suggestion('Second')]}))]
        result=research_queries('Fixture Server',CONFIG,demo=True)
        self.assertEqual(post.call_count,2)
        search,model=post.call_args_list
        self.assertEqual(search.kwargs['json']['limit'],1)
        self.assertEqual(model.kwargs['json']['max_tokens'],256)
        self.assertIn('at most one',model.kwargs['json']['messages'][1]['content'].lower())
        self.assertEqual(len(result['sources']),1);self.assertEqual(len(result['suggestions']),1)
        self.assertTrue(result['partial']);self.assertTrue(result['demo'])
        self.assertEqual(result['usage_policy']['search_requests'],1)
        self.assertEqual(result['usage_policy']['llm_requests'],1)

    @patch('leakscope.intelligence.clients.requests.post')
    def test_demo_research_does_not_make_an_extra_call_without_sources_or_model(self,post):
        post.return_value=sources()
        result=research_queries('Fixture',{'FIRECRAWL_API_URL':'http://firecrawl.fixture/v2'},demo=True)
        self.assertEqual(post.call_count,1);self.assertEqual(result['usage_policy']['llm_requests'],0)
        self.assertEqual(len(result['sources']),1)
        post.reset_mock();post.return_value=Response({'data':{'web':[]}})
        result=research_queries('Fixture',CONFIG,demo=True)
        self.assertEqual(post.call_count,1);self.assertEqual(result['usage_policy']['llm_requests'],0)

    @patch('leakscope.intelligence.clients.requests.post')
    def test_demo_research_reports_no_model_request_when_model_config_is_invalid(self,post):
        post.return_value=sources()
        result=research_queries('Fixture',dict(CONFIG,LLM_MAX_TOKENS=True),demo=True)
        self.assertEqual(post.call_count,1)
        self.assertEqual(result['usage_policy']['llm_requests'],0)
        self.assertEqual(len(result['sources']),1)

    @patch('leakscope.intelligence.clients.requests.post')
    def test_demo_research_counts_model_request_when_its_answer_is_not_valid_json(self,post):
        post.side_effect=[sources(),answer('Not JSON')]
        result=research_queries('Fixture',CONFIG,demo=True)
        self.assertEqual(post.call_count,2)
        self.assertEqual(result['usage_policy']['llm_requests'],1)
        self.assertEqual(result['suggestions'],[])

    @patch('leakscope.intelligence.clients.requests.post')
    def test_demo_research_retains_grounding_and_no_retry_on_model_failure(self,post):
        post.side_effect=[sources(),ValueError('Fixture failure')]
        result=research_queries('Fixture',CONFIG,demo=True)
        self.assertEqual(post.call_count,2);self.assertEqual(len(result['sources']),1)
        self.assertEqual(result['suggestions'],[]);self.assertTrue(result['warnings'])

    @patch('leakscope.phase3.web_services._post')
    def test_demo_services_request_one_page_and_keep_its_real_dependency_analysis(self,post):
        post.return_value={'success':True,'data':{'web':[
            {'url':'https://example.com/','html':'<script src="https://js.stripe.com/v3"></script>'},
            {'url':'https://example.com/next','html':'<script src="https://browser.sentry-cdn.com/next.js"></script>'}]}}
        result=research_services('example.com',CONFIG,demo=True)
        self.assertEqual(post.call_count,1);self.assertEqual(post.call_args.args[1]['limit'],1)
        self.assertEqual(post.call_args.args[1]['scrapeOptions']['formats'],['markdown','html'])
        self.assertEqual(len(result['sources']),1)
        self.assertEqual([x['provider'] for x in result['services']],['Stripe'])
        self.assertEqual(result['requests'],1);self.assertTrue(result['demo']);self.assertTrue(result['partial'])
        self.assertEqual(result['usage_policy']['source_limit'],1)

    @patch('leakscope.phase3.web_services._post')
    def test_demo_services_save_one_candidate_even_when_the_one_page_has_many(self,post):
        post.return_value={'success':True,'data':{'web':[{'url':'https://example.com/',
            'html':'<script src="https://js.stripe.com/v3"></script><script src="https://static.cloudflareinsights.com/beacon.js"></script>'}]}}
        result=research_services('example.com',CONFIG,demo=True)
        self.assertEqual(len(result['services']),1)
        self.assertEqual(result['usage_policy']['service_limit'],1)
        self.assertEqual(post.call_count,1)

    @patch('leakscope.phase3.web_services._post')
    def test_invalid_demo_service_flag_starts_no_request(self,post):
        with self.assertRaises(ValueError):research_services('example.com',CONFIG,demo='true')
        post.assert_not_called()


class DemoIntelligenceAPITests(TestCase):
    @patch('leakscope_app.intelligence.ask_endpoint')
    def test_ask_api_forwards_demo_and_returns_actual_sampled_context(self,ask):
        ask.return_value={'answer':'Fixture','model':'fixture','usage':{},'demo':True,'partial':True,
                          'usage_policy':{'max_output_tokens':256},'context_sent':{'findings':[],'demo_sample':True}}
        response=self.client.post('/api/intelligence/ask',{'question':'Explain','demo':True},content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(ask.call_args.kwargs,{'demo':True})
        self.assertTrue(response.json()['context']['demo_sample'])
        self.assertNotIn('context_sent',response.json())

    @patch('leakscope_app.intelligence.research_queries')
    def test_research_api_preserves_demo_limits_and_warning_metadata(self,research):
        research.return_value={'sources':[],'suggestions':[],'warnings':['Demo sample'],'demo':True,'partial':True,'usage_policy':{'search_requests':1}}
        response=self.client.post('/api/intelligence/research',{'topic':'Fixture','demo':True},content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(research.call_args.kwargs,{'demo':True})
        self.assertTrue(response.json()['demo']);self.assertEqual(response.json()['usage_policy']['search_requests'],1)

    @patch('leakscope_app.intelligence.ask_endpoint')
    @patch('leakscope_app.intelligence.research_queries')
    def test_apis_reject_nonboolean_demo_before_any_remote_call(self,research,ask):
        for value in ['true','false',1,None,{},[]]:
            for action,data in [('ask',{'question':'Explain'}),('research',{'topic':'Fixture'})]:
                response=self.client.post('/api/intelligence/'+action,dict(data,demo=value),content_type='application/json')
                self.assertEqual(response.status_code,400,(action,value,response.content))
        research.assert_not_called();ask.assert_not_called()
