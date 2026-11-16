import time
from unittest.mock import patch
from django.test import TestCase


class WebDomainResearchTests(TestCase):
    def collect(self,kind='company',value='Example Corp'):
        from leakscope.phase3.expansion_sources import collect
        return collect('web',{'kind':kind,'value':value,'depth':0},config={'FIRECRAWL_API_URL':'https://fc.example/v2'},spend=lambda bucket:True,context={'company_names':['Example Corp'],'seed_domains':['example.com'],'deadline':time.monotonic()+30})

    @patch('leakscope.phase3.expansion_sources._post')
    def test_company_search_does_not_bulk_scrape_raw_html(self,post):
        post.return_value={'data':{'web':[]}}
        self.collect()
        payload=post.call_args.args[1]
        self.assertNotIn('scrapeOptions',payload)
        self.assertIn(' OR ',payload['query'])

    @patch('leakscope.phase3.expansion_sources._post')
    def test_acquired_company_name_is_a_sourced_lead_even_without_its_domain(self,post):
        post.return_value={'data':{'web':[{'url':'https://example.com/news/acquisition','title':'Example Corp Acquires Different Brand to Expand Services','description':'Example Corp acquired Different Brand.'}]}}
        data=self.collect()
        lead=next(lead for lead in data['leads'] if lead['value']=='Different Brand')
        self.assertEqual(lead['evidence'][0]['signal'],'acquisition_statement')
        self.assertEqual(lead['evidence'][0]['url'],'https://example.com/news/acquisition')

    @patch('leakscope.phase3.expansion_sources._post')
    def test_web_provider_safe_http_error_is_not_hidden_by_generic_note(self,post):
        post.side_effect=ValueError('Firecrawl endpoint returned HTTP 429')
        data=self.collect()
        self.assertIn('HTTP 429',' '.join(data['notes']))

    @patch('leakscope.phase3.expansion_sources._post')
    def test_arbitrary_remote_exception_text_is_not_persisted(self,post):
        post.side_effect=ValueError('private credential upstream response')
        data=self.collect()
        self.assertNotIn('private credential',str(data))

    @patch('leakscope.phase3.expansion_sources._post')
    def test_company_only_search_can_bootstrap_matching_official_brand_domain(self,post):
        from leakscope.phase3.expansion_sources import collect
        post.return_value={'data':{'web':[{'url':'https://examplecorp.com/','title':'Example Corp | Official Website','description':'Example Corp website'}]}}
        result=collect('web',{'kind':'company','value':'Example Corp','depth':0},config={'FIRECRAWL_API_URL':'https://fc.example/v2'},spend=lambda bucket:True,context={'company_names':['Example Corp'],'seed_domains':[],'deadline':time.monotonic()+30})
        candidate=next(c for c in result['candidates'] if c['domain']=='examplecorp.com')
        self.assertTrue(any(p['signal']=='company_domain_candidate' for p in candidate['evidence']))
        self.assertTrue(any('official website' in call.args[1]['query'] for call in post.call_args_list))
