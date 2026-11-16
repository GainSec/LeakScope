import base64
from urllib.parse import parse_qs, urlsplit
from django.test import SimpleTestCase

from leakscope.intelligence.queries import compile_queries


class QueryCompilerTests(SimpleTestCase):
    def signal(self, field, value):
        return {'field': field, 'value': value, 'source': 'elastic:1'}

    def test_title_port_and_scope_are_preserved_for_each_provider(self):
        results = compile_queries([self.signal('title', 'Acme "Store" \\ UI'), self.signal('port', 443)], scope={'network': '192.0.2.0/24', 'country': 'US'})
        for name, result in results.items():
            self.assertTrue(result['supported'], name)
            self.assertIn('192.0.2.0/24', result['query'])
            self.assertIn('443', result['query'])
            self.assertIn('US', result['query'])
            self.assertIn('\\"Store\\"', result['query'])
        self.assertIn('http.title:', results['shodan']['query'])
        self.assertIn('title=', results['zoomeye']['query'])
        self.assertIn('http.title:', results['netlas']['query'])

    def test_all_groups_alternatives_by_field(self):
        result = compile_queries([self.signal('title','Acme'), self.signal('title','Acme Store'), self.signal('port',443)])
        self.assertIn(' OR ', result['netlas']['query'])
        self.assertIn(' AND ', result['netlas']['query'])
        self.assertIn(' || ', result['zoomeye']['query'])
        self.assertIn(' && ', result['zoomeye']['query'])

    def test_any_keeps_scope_outside_or(self):
        result = compile_queries([self.signal('title','Acme'), self.signal('port',443)], mode='any', scope={'network':'192.0.2.17'})
        self.assertIn(') AND ip:', result['netlas']['query'])
        self.assertIn(') && ip=', result['zoomeye']['query'])

    def test_unknown_required_field_disables_all_providers(self):
        result = compile_queries([self.signal('title','Acme'), self.signal('unknown','anything')])
        for value in result.values():
            self.assertFalse(value['supported'])
            self.assertEqual(value['query'], '')
            self.assertEqual(value['url'], '')
            self.assertTrue(value['warnings'])

    def test_hash_algorithms_are_not_translated(self):
        result = compile_queries([self.signal('favicon.sha256','a'*64)])
        self.assertTrue(result['netlas']['supported'])
        self.assertFalse(result['shodan']['supported'])
        self.assertFalse(result['zoomeye']['supported'])
        mmh3 = compile_queries([self.signal('favicon.mmh3', '-12345')])
        self.assertIn('http.favicon.hash:',mmh3['shodan']['query'])
        self.assertIn('iconhash=',mmh3['zoomeye']['query'])
        self.assertFalse(mmh3['netlas']['supported'])

    def test_product_does_not_silently_become_a_netlas_tag(self):
        result=compile_queries([self.signal('product','Redis')])
        self.assertFalse(result['netlas']['supported'])
        self.assertTrue(result['shodan']['supported'])
        self.assertTrue(result['zoomeye']['supported'])

    def test_certificate_algorithms_use_documented_distinct_mappings(self):
        results = compile_queries([self.signal('certificate.sha256','a'*64)])
        self.assertFalse(results['shodan']['supported'])
        self.assertFalse(results['zoomeye']['supported'])
        self.assertTrue(results['netlas']['supported'])
        self.assertIn('certificate.fingerprint_sha256:', results['netlas']['query'])
        results = compile_queries([self.signal('certificate.sha1','b'*40)])
        self.assertTrue(all(result['supported'] for result in results.values()))

    def test_duplicate_signals_are_collapsed(self):
        result=compile_queries([self.signal('title','Acme')]*3)
        self.assertEqual(result['shodan']['query'].count('Acme'),1)

    def test_provider_links_round_trip(self):
        results=compile_queries([self.signal('title','Acme + "UI"')])
        for provider,result in results.items():
            parameter='query' if provider=='shodan' else 'q'
            query=parse_qs(urlsplit(result['url']).query)[parameter][0]
            if provider=='zoomeye':query=base64.b64decode(query).decode()
            self.assertEqual(query,result['query'])

    def test_inputs_are_bounded_and_invalid_values_rejected(self):
        invalid=[([],{}),([self.signal('port',0)],{}),([self.signal('ip','no-ip')],{}),([self.signal('title','x'*1001)],{}),([self.signal('title','a')]*65,{}),([self.signal('title','a')],{'mode':'bad'}),([self.signal('title','a')],{'scope':{'network':'192.0.2.0/24 OR *'}}),([self.signal('title','a')],{'scope':{'country':'US OR CA'}})]
        for signals,kwargs in invalid:
            with self.subTest(signals=str(signals)[:60],kwargs=kwargs):
                with self.assertRaises(ValueError):compile_queries(signals,**kwargs)

    def test_exclusion_scope_is_not_lost(self):
        results=compile_queries([self.signal('title','Acme')],scope={'exclude':'test,staging site'})
        self.assertIn('-"test"',results['shodan']['query'])
        self.assertIn('http.body!=',results['zoomeye']['query'])
        self.assertIn('NOT http.body:',results['netlas']['query'])
