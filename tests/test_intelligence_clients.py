import json
import hashlib
from datetime import datetime
from unittest.mock import patch
import requests
from django.test import SimpleTestCase

from leakscope.intelligence.clients import ask_endpoint, research_queries


class Response:
    def __init__(self, data=None, status=200, raw=None):
        self.status_code=status
        self.raw=raw if raw is not None else json.dumps(data).encode()
        self.headers={}
        self.closed=False
    def iter_content(self,chunk_size=8192):
        for index in range(0,len(self.raw),chunk_size):yield self.raw[index:index+chunk_size]
    def close(self):self.closed=True


class IntelligenceClientsTests(SimpleTestCase):
    config={'LLM_API_BASE':'http://localhost:11434/v1','LLM_MODEL':'fixture-model','LLM_API_KEY':'llm-secret-fixture','FIRECRAWL_API_KEY':'firecrawl-secret-fixture'}

    @patch('leakscope.intelligence.clients.requests.post')
    def test_self_hosted_firecrawl_works_without_an_api_key(self, post):
        post.return_value = Response({'success': True, 'data': {'web': [{'url': 'https://example.test/docs', 'title': 'Fixture docs', 'markdown': 'Public documentation'}]}})
        result = research_queries('Fixture documentation', {'FIRECRAWL_API_URL': 'http://firecrawl:3002/v2'})
        self.assertEqual(len(result['sources']), 1)
        self.assertEqual(post.call_args.args[0], 'http://firecrawl:3002/v2/search')
        self.assertNotIn('Authorization', post.call_args.kwargs['headers'])

    @patch('leakscope.intelligence.clients.requests.post')
    def test_cloud_firecrawl_still_requires_a_key_before_a_request(self, post):
        for cfg in ({}, {'FIRECRAWL_API_URL': 'https://api.firecrawl.dev/v2'}):
            with self.assertRaisesMessage(ValueError, 'key'): research_queries('Fixture docs', cfg)
        post.assert_not_called()
    def response(self,answer='A grounded answer.'):
        return Response({'choices':[{'message':{'content':answer}}],'model':'fixture-model','usage':{'prompt_tokens':20,'completion_tokens':8,'total_tokens':28}})

    @patch('leakscope.intelligence.clients.requests.post')
    def test_ask_posts_only_bounded_explicit_context(self,post):
        response=self.response();post.return_value=response
        result=ask_endpoint('What is this?',{'findings':[{'id':1,'title':'Acme'}]},self.config)
        self.assertEqual(result['answer'],'A grounded answer.')
        self.assertEqual(result['usage']['total_tokens'],28)
        url=post.call_args.args[0];kwargs=post.call_args.kwargs
        self.assertEqual(url,'http://localhost:11434/v1/chat/completions')
        self.assertEqual(kwargs['headers']['Authorization'],'Bearer llm-secret-fixture')
        self.assertTrue(kwargs['stream']);self.assertFalse(kwargs['allow_redirects'])
        self.assertNotIn('tools',kwargs['json'])
        self.assertIn('untrusted',kwargs['json']['messages'][0]['content'].lower())
        self.assertTrue(response.closed)

    @patch('leakscope.intelligence.clients.requests.post')
    def test_local_model_key_is_optional(self,post):
        post.return_value=self.response();config=dict(self.config,LLM_API_KEY='')
        ask_endpoint('Explain',{},config)
        self.assertNotIn('Authorization',post.call_args.kwargs['headers'])

    @patch('leakscope.intelligence.clients.requests.post')
    def test_empty_optional_numeric_config_uses_defaults(self,post):
        post.return_value=self.response()
        ask_endpoint('Explain',{},dict(self.config,LLM_TIMEOUT='',LLM_MAX_TOKENS=None))
        self.assertEqual(post.call_args.kwargs['timeout'],(5,45))
        self.assertEqual(post.call_args.kwargs['json']['max_tokens'],1500)

    @patch('leakscope.intelligence.clients.requests.post')
    def test_bad_config_and_input_do_not_contact_endpoint(self,post):
        for config in ({},dict(self.config,LLM_API_BASE='file:///tmp/key'),dict(self.config,LLM_API_BASE='https://user:password@example.test/v1'),dict(self.config,LLM_API_BASE='http://localhost/v1?key=secret')):
            with self.assertRaises(ValueError):ask_endpoint('Explain',{},config)
        for question,context in [('',{}),('x'*8001,{}),('Explain',{'blob':'x'*70000})]:
            with self.assertRaises(ValueError):ask_endpoint(question,context,self.config)
        post.assert_not_called()

    @patch('leakscope.intelligence.clients.requests.post')
    def test_reflected_secrets_are_removed_from_request_and_response(self,post):
        post.return_value=self.response('llm-secret-fixture firecrawl-secret-fixture')
        result=ask_endpoint('llm-secret-fixture',{'text':'firecrawl-secret-fixture'},self.config)
        payload=json.dumps(post.call_args.kwargs['json'])
        for secret in ('llm-secret-fixture','firecrawl-secret-fixture'):
            self.assertNotIn(secret,payload)
            self.assertNotIn(secret,result['answer'])

    @patch('leakscope.intelligence.clients.requests.post')
    def test_errors_never_expose_remote_body_or_exception(self,post):
        for response in (Response(raw=b'llm-secret-fixture',status=503),Response(raw=b'not json'),Response({'choices':[]})):
            post.return_value=response
            with self.assertRaises(ValueError) as caught:ask_endpoint('Explain',{},self.config)
            self.assertNotIn('llm-secret-fixture',str(caught.exception))
            self.assertTrue(response.closed)
        post.side_effect=requests.Timeout('llm-secret-fixture')
        with self.assertRaises(ValueError) as caught:ask_endpoint('Explain',{},self.config)
        self.assertNotIn('llm-secret-fixture',str(caught.exception))

    @patch('leakscope.intelligence.clients.requests.post')
    def test_oversize_response_is_rejected_and_closed(self,post):
        response=Response(raw=b'x'*(1024*1024+1));post.return_value=response
        with self.assertRaisesMessage(ValueError,'limit'):ask_endpoint('Explain',{},self.config)
        self.assertTrue(response.closed)

    def source_response(self):
        return Response({'success':True,'data':{'web':[{'url':'https://vendor.example/docs','title':'Acme Store documentation','description':'AcmeStore is a database. Its login title is Acme Database Console.','markdown':'AcmeStore presents a title of Acme Database Console.'},{'url':'javascript:alert(1)','title':'Untrusted'}]}})

    @patch('leakscope.intelligence.clients.requests.post')
    def test_research_returns_sources_without_llm(self,post):
        post.return_value=self.source_response()
        result=research_queries('AcmeStore database',{'FIRECRAWL_API_KEY':'firecrawl-secret-fixture'})
        self.assertEqual(len(result['sources']),1);self.assertEqual(result['suggestions'],[])
        self.assertTrue(result['warnings']);self.assertEqual(post.call_count,1)
        self.assertEqual(post.call_args.kwargs['json']['limit'],5)
        source=result['sources'][0]
        self.assertEqual(source['content_sha256'],hashlib.sha256(source['excerpt'].encode()).hexdigest())
        self.assertIsNotNone(datetime.fromisoformat(source['retrieved_at'].replace('Z','+00:00')).tzinfo)

    @patch('leakscope.intelligence.clients.requests.post')
    def test_grounded_suggestions_keep_only_verified_source_ids(self,post):
        model={'suggestions':[{'title':'Acme console','technology':'AcmeStore','rationale':'Documented login title','signals':[{'field':'title','value':'Acme Database Console','source_ids':['S1']}]},{'title':'Fake source','technology':'Acme','rationale':'Unknown','signals':[{'field':'title','value':'Fake','source_ids':['S99']}]}]}
        post.side_effect=[self.source_response(),self.response(json.dumps(model))]
        result=research_queries('AcmeStore database',self.config)
        self.assertEqual(len(result['suggestions']),1)
        self.assertEqual(result['suggestions'][0]['signals'][0]['source_ids'],['S1'])
        self.assertTrue(result['warnings'])

    @patch('leakscope.intelligence.clients.requests.post')
    def test_unsubstantiated_and_unknown_fingerprints_are_rejected(self,post):
        for field,value in [('title','Invented Console'),('shell','AcmeStore')]:
            post.side_effect=[self.source_response(),self.response(json.dumps({'suggestions':[{'title':'Candidate','technology':'AcmeStore','rationale':'A guess','signals':[{'field':field,'value':value,'source_ids':['S1']}]}]}))]
            result=research_queries('AcmeStore',self.config)
            self.assertEqual(result['suggestions'],[])
            self.assertTrue(result['warnings'])

    @patch('leakscope.intelligence.clients.requests.post')
    def test_malformed_model_synthesis_preserves_fetched_sources(self,post):
        post.side_effect=[self.source_response(),self.response('not JSON')]
        result=research_queries('AcmeStore',self.config)
        self.assertEqual(len(result['sources']),1)
        self.assertEqual(result['suggestions'],[])
        self.assertIn('JSON',' '.join(result['warnings']))

    @patch('leakscope.intelligence.clients.requests.post')
    def test_excessively_nested_model_json_preserves_fetched_sources(self,post):
        post.side_effect=[self.source_response(),self.response('['*50000+'0'+']'*50000)]
        result=research_queries('AcmeStore',self.config)
        self.assertEqual(len(result['sources']),1)
        self.assertEqual(result['suggestions'],[])
        self.assertIn('JSON',' '.join(result['warnings']))

    @patch('leakscope.intelligence.clients.requests.post')
    def test_model_timeout_preserves_paid_research_sources(self,post):
        post.side_effect=[self.source_response(),requests.Timeout('llm-secret-fixture')]
        result=research_queries('AcmeStore',self.config)
        self.assertEqual(len(result['sources']),1)
        self.assertEqual(result['suggestions'],[])
        self.assertIn('timed out',' '.join(result['warnings']))
        self.assertNotIn('llm-secret-fixture',json.dumps(result))

    @patch('leakscope.intelligence.clients.requests.post')
    def test_research_requires_configuration_and_bounded_topic(self,post):
        with self.assertRaises(ValueError):research_queries('AcmeStore',{})
        for topic in ('','x'*501):
            with self.assertRaises(ValueError):research_queries(topic,self.config)
        post.assert_not_called()

    @patch('leakscope.intelligence.clients.requests.post')
    def test_malformed_candidate_field_is_omitted_without_crashing(self,post):
        candidate={'title':'Candidate','technology':'AcmeStore','rationale':'A guess','signals':[{'field':['title'],'value':'AcmeStore','source_ids':['S1']}]}
        post.side_effect=[self.source_response(),self.response(json.dumps({'suggestions':[candidate]}))]
        result=research_queries('AcmeStore',self.config)
        self.assertEqual(result['suggestions'],[])
        self.assertTrue(result['warnings'])

    @patch('leakscope.intelligence.clients.requests.post')
    def test_non_unicode_context_is_rejected_before_request(self,post):
        with self.assertRaises(ValueError):ask_endpoint('Explain',{'text':'\ud800'},self.config)
        post.assert_not_called()

    @patch('leakscope.intelligence.clients.requests.post')
    def test_research_uses_configured_api_prefix_and_bounds_sources(self,post):
        row={'url':'https://vendor.example/docs','title':'x'*500,'markdown':'x'*20000}
        post.return_value=Response({'data':{'web':[dict(row,url=row['url']+str(i)) for i in range(10)]}})
        result=research_queries('AcmeStore',{'FIRECRAWL_API_KEY':'key','FIRECRAWL_API_URL':'http://localhost:3002/v2'})
        self.assertEqual(post.call_args.args[0],'http://localhost:3002/v2/search')
        self.assertEqual(len(result['sources']),5)
        self.assertTrue(all(len(s['excerpt'])<=6000 and len(s['title'])<=300 for s in result['sources']))
