import json,time
from unittest.mock import patch,Mock
from django.test import SimpleTestCase
from leakscope.phase3 import domain_expansion as engine

class HistoricalSourceTests(SimpleTestCase):
    def setUp(self):
        self.used=0;self.maximum=20;self.context={'deadline':time.monotonic()+30,'seed_domains':['example.com']}
    def spend(self,bucket,units=1):
        self.assertEqual(bucket,'domain_api')
        if self.used+units>self.maximum:return False
        self.used+=units;return True
    def collect(self,**options):
        from leakscope.phase3 import historical_sources as h
        return h.collect('commoncrawl',{'kind':'domain','value':'example.com','depth':0},config={},spend=self.spend,context={**self.context,**options})
    def test_source_is_explicit_and_uses_shared_budget_and_archive(self):
        self.assertNotIn('commoncrawl',engine.normalize_spec({'domains':['example.com']})['sources'])
        s=engine.normalize_spec({'domains':['example.com'],'sources':['commoncrawl'],'commoncrawl_index':'CC-MAIN-2026-43','budgets':{'domain_api':3}})
        self.assertEqual(s['commoncrawl_index'],'CC-MAIN-2026-43');self.assertEqual(s['budgets']['domain_api'],3)
        with self.assertRaises(ValueError):engine.normalize_spec({'domains':['example.com'],'sources':['commoncrawl'],'commoncrawl_index':'https://evil.example/index'})
    def test_disabled_archive_does_not_block_other_sources_and_preserves_valid_choice(self):
        from leakscope.phase3.historical_sources import DEFAULT_INDEX
        for invalid in ['', None, 42, 'https://invalid.example/archive']:
            with self.subTest(archive=invalid):
                spec=engine.normalize_spec({'domains':['example.com'],'sources':['saved'],'commoncrawl_index':invalid})
                self.assertEqual(spec['commoncrawl_index'],DEFAULT_INDEX)
                self.assertEqual(spec['sources'],['saved'])
                self.assertEqual(spec['budgets']['domain_api'],0)
        spec=engine.normalize_spec({'domains':['example.com'],'sources':['saved'],'commoncrawl_index':'CC-MAIN-2025-30'})
        self.assertEqual(spec['commoncrawl_index'],'CC-MAIN-2025-30')
        with self.assertRaises(ValueError):
            engine.normalize_spec({'domains':['example.com'],'sources':['commoncrawl'],'commoncrawl_index':''})
    @patch('leakscope.phase3.passive_sources.requests.get')
    def test_paged_index_records_keep_historical_time_and_scoped_urls(self,get):
        def response(body):
            r=Mock(status_code=200,headers={});r.iter_content.return_value=[body.encode()];return r
        row={'url':'https://api.example.com:9443/private?access_token=not-retained#part','timestamp':'20200102030405','status':'200','mime':'text/html','digest':'ABC'}
        get.side_effect=[response(json.dumps({'pages':2,'blocks':2,'pageSize':1})),response('\n'.join(json.dumps(x) for x in [row,{'url':'https://outside.example.net/','timestamp':'20200102030405'}]))]
        r=self.collect();self.assertEqual(self.used,2);self.assertEqual([c['domain'] for c in r['candidates']],['api.example.com']);p=r['candidates'][0]['evidence'][0]
        self.assertEqual(p['observed_at'],'2020-01-02T03:04:05+00:00');self.assertTrue(p['historical']);self.assertEqual(p['archived_url'],'https://api.example.com:9443/private');self.assertNotIn('not-retained',json.dumps(r));self.assertEqual(r['continuation']['page'],1)
        for call in get.call_args_list:
            self.assertTrue(call.args[0].startswith('https://index.commoncrawl.org/CC-MAIN-2026-43-index?'));self.assertFalse(call.kwargs['allow_redirects']);self.assertNotIn('api-key',call.kwargs['headers'])
    @patch('leakscope.phase3.passive_sources.requests.get')
    def test_budget_pause_retains_metadata_without_repeating_it(self,get):
        r=Mock(status_code=200,headers={});r.iter_content.return_value=[b'{"pages":2,"blocks":2,"pageSize":1}'];get.return_value=r;self.maximum=1
        first=self.collect();self.assertEqual(first['blocked'],'budget');self.assertEqual(first['continuation']['page'],0);self.assertEqual(get.call_count,1)
        self.maximum=2;r.iter_content.return_value=[b'{"url":"https://api.example.com/","timestamp":"20200102030405"}'];second=self.collect(cursor=first['continuation']);self.assertEqual(get.call_count,2);self.assertIn('page=0',get.call_args.args[0]);self.assertNotIn('showNumPages',get.call_args.args[0]);self.assertEqual(second['continuation']['page'],1)
    @patch('leakscope.phase3.passive_sources.requests.get')
    def test_demo_uses_one_request_and_never_claims_exhaustive(self,get):
        r=Mock(status_code=200,headers={});r.iter_content.return_value=[b'{"url":"https://a.example.com/","timestamp":"20200102030405"}\n{"url":"https://b.example.com/"}'];get.return_value=r
        result=self.collect(demo=True);self.assertEqual(self.used,1);self.assertEqual(len(result['candidates']),1);self.assertNotIn('showNumPages',get.call_args.args[0]);self.assertIn('limit=1',get.call_args.args[0]);self.assertTrue(result['notes']);self.assertNotIn('continuation',result)
    @patch('leakscope.phase3.passive_sources.requests.get')
    def test_redirect_and_remote_error_content_not_retained(self,get):
        get.return_value=Mock(status_code=302,headers={'Location':'https://target.example.com/token=secret'})
        r=self.collect(demo=True);self.assertTrue(r['failed']);self.assertEqual(get.call_count,1);self.assertNotIn('token=secret',json.dumps(r))
    @patch('leakscope.phase3.passive_sources.requests.get')
    def test_malformed_lines_credentials_and_prefix_confusion_excluded(self,get):
        r=Mock(status_code=200,headers={});r.iter_content.return_value=[b'not-json\n{"url":"https://user:password@a.example.com/"}\n{"url":"https://example.com.evil.net/"}\n{"url":"https://api.example.com/","timestamp":"bad"}'];get.return_value=r
        result=self.collect(demo=True);self.assertEqual([x['domain'] for x in result['candidates']],['api.example.com']);self.assertIsNone(result['candidates'][0]['evidence'][0]['observed_at']);self.assertNotIn('password@',json.dumps(result));self.assertTrue(result['notes'])
    @patch('leakscope.phase3.passive_sources.requests.get')
    def test_one_archive_page_advances_and_last_page_finishes(self,get):
        r=Mock(status_code=200,headers={});r.iter_content.return_value=[b'{"url":"https://last.example.com/","timestamp":"20200102030405"}'];get.return_value=r
        result=self.collect(cursor={'stage':'page','page':1,'pages':2,'index':'CC-MAIN-2026-43'});self.assertEqual(self.used,1);self.assertIn('page=1',get.call_args.args[0]);self.assertNotIn('continuation',result)
    @patch('leakscope.phase3.passive_sources.requests.get')
    def test_unsupported_seed_and_expired_deadline_never_make_requests(self,get):
        from leakscope.phase3.historical_sources import collect
        r=collect('commoncrawl',{'kind':'company','value':'Example'},config={},spend=self.spend,context=self.context);self.assertEqual(r['candidates'],[])
        self.context['deadline']=time.monotonic()-1;r=self.collect();self.assertEqual(r['blocked'],'deadline');self.assertEqual(self.used,0);get.assert_not_called()
    @patch('leakscope.phase3.passive_sources._request')
    def test_urlscan_retains_available_historical_details_without_fetching_scan_pages(self,request):
        from leakscope.phase3 import passive_sources
        request.return_value={'results':[{'_id':'12345678-1234-1234-1234-123456789012','task':{'domain':'api.example.com','time':'2020-01-02T03:04:05.000Z'},'page':{'domain':'api.example.com','url':'https://api.example.com:9443/app?token=private','title':'Research API','server':'nginx','status':'200','asn':'AS64500','asnname':'Example hosting','tlsIssuer':'Example CA','ip':'192.0.2.5'}}]}
        result=passive_sources.collect('urlscan',{'kind':'domain','value':'example.com'},config={},spend=self.spend,context=self.context)
        request.assert_called_once();proof=result['candidates'][0]['evidence'][0]
        self.assertEqual(proof['observed_at'],'2020-01-02T03:04:05+00:00');self.assertTrue(proof['historical']);self.assertEqual(proof['scan_metadata']['title'],'Research API');self.assertEqual(proof['scan_metadata']['recorded_url'],'https://api.example.com:9443/app');self.assertNotIn('private',json.dumps(result));self.assertIn('not proof of ownership',proof['excerpt'])
    @patch('leakscope.phase3.passive_sources._request')
    def test_invalid_scan_time_is_unknown_not_collection_time(self,request):
        from leakscope.phase3 import passive_sources
        request.return_value={'results':[{'task':{'domain':'example.com','time':'not-a-date'},'page':{'domain':'example.com'}}]}
        result=passive_sources.collect('urlscan',{'kind':'domain','value':'example.com'},config={},spend=self.spend,context=self.context)
        self.assertIsNone(result['candidates'][0]['evidence'][0]['observed_at'])
