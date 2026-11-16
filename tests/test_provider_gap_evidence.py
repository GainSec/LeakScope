from django.test import SimpleTestCase
from leakscope.phase3.catalog import catalog, catalog_entry, profile_query
from leakscope.phase3.fingerprints import HTTP_CAPTURE_TYPES
from leakscope.phase3.capture import supported_actions


class ProviderGapEvidenceTests(SimpleTestCase):
    def test_binary_smux_and_native_only_cardano_profiles_have_no_invented_provider_queries(self):
        missing={r['key'] for r in catalog() if not r['query_variants']['fingerprint']['shodan']['supported']}
        self.assertEqual(missing, {'smux','cardano','amaru','cardanonode'})
        for key in ('cardano','amaru','cardanonode'):
            self.assertIn('native Ouroboros',catalog_entry(key)['query_variants']['fingerprint']['shodan']['reason'])

    def test_native_response_candidates_explain_protocol_and_index_limits(self):
        expected={'monetdb':'mserver:9:', 'maxdb':'Rejected bad connect packet', 'informix':'lsrvinfx'}
        for key,marker in expected.items():
            with self.subTest(key=key):
                dto=profile_query(key,'shodan')
                self.assertTrue(dto['supported'])
                self.assertIn(marker,dto['query'])
                self.assertIn('native',dto['reason'].lower())
                self.assertIn('index',dto['reason'].lower())
                self.assertNotIn('http.title:',dto['query'])
                self.assertNotIn('shodan.module:',dto['query'])

    def test_http_companions_are_explicit_and_do_not_enable_native_screenshots(self):
        expected={'aerospike':'aerospike_node_up','milvus':'Milvus Management', 'hana':'x-sap-login-page', 'ads':'WEB SERVER V'}
        for key,marker in expected.items():
            dto=profile_query(key,'shodan')
            self.assertTrue(dto['supported'])
            self.assertIn(marker,dto['query'])
            self.assertIn('companion',dto['reason'].lower())
            self.assertNotIn(key,HTTP_CAPTURE_TYPES)
        self.assertIn('unreachable',profile_query('aerospike','shodan')['reason'])
        self.assertIn('gRPC',profile_query('milvus','shodan')['reason'])

    def test_milvus_webui_query_does_not_guess_http_on_a_native_candidate(self):
        endpoint={'address':'192.0.2.1','port':19530,'transport':'tcp','service_type':'milvus','scheme':''}
        self.assertNotIn('screenshot',supported_actions(endpoint))
        endpoint['scheme']='http'
        self.assertIn('screenshot',supported_actions(endpoint))

    def test_each_provider_keeps_classifiers_and_gains_observable_alternatives(self):
        for key,marker in [('aerospike','aerospike_node_up'),('monetdb','mserver:9:'),('milvus','Milvus Management'),('maxdb','Rejected bad connect packet'),('informix','lsrvinfx')]:
            for provider in ('zoomeye','netlas'):
                dto=profile_query(key,provider)
                self.assertTrue(dto['supported'])
                self.assertIn(marker,dto['query'])
                self.assertTrue('service=' in dto['query'] or 'prot7:' in dto['query'])
                self.assertTrue(' || ' in dto['query'] or ' OR ' in dto['query'])
        hana=profile_query('hana','netlas')
        self.assertIn('http.unknown_headers.key:x_sap_login_page',hana['query'])
        self.assertNotIn('http.headers.x_sap_login_page',hana['query'])

    def test_scope_is_provider_specific_and_public_dto_is_compatible(self):
        shodan=profile_query('milvus','shodan');zoom=profile_query('milvus','zoomeye')
        self.assertNotEqual(shodan['reason'],zoom['reason'])
        self.assertIn('classifier',zoom['reason'])
        for key in ('aerospike','hana','maxdb','informix','monetdb','milvus','ads'):
            for provider in ('shodan','zoomeye','netlas'):
                dto=profile_query(key,provider)
                self.assertEqual(set(dto),{'query','supported','reason','basis'})
                self.assertNotRegex(dto['query'],r'\b(?:port|product|app)\s*[:=]')
                self.assertTrue(dto['basis'])
                restricted=profile_query(key,provider,'nonstandard')
                self.assertTrue(restricted['supported'])
                self.assertIn(dto['query'],restricted['query'])

    def test_smux_explains_required_upstream_evidence_without_faking_other_protocol(self):
        dto=profile_query('smux','shodan')
        self.assertFalse(dto['supported']);self.assertEqual(dto['query'],'')
        self.assertIn('binary',dto['reason'])
        self.assertIn('indexed',dto['reason'])
        self.assertIn('SMUX',dto['reason'])
        for provider in ('zoomeye','netlas'):
            self.assertIn('smux',profile_query('smux',provider)['query'])
