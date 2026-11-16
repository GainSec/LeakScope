from django.test import SimpleTestCase
from leakscope.phase3.catalog import catalog_entry, profile_query
from leakscope_app.workbench import _types

class QueryCompletionTests(SimpleTestCase):
    def test_native_protocol_metadata_queries_do_not_require_conventional_ports(self):
        for key in ('modbus','opcua','ethernetip','dnp3','iec104','coap','mongo','mssql','smb','kafka'):
            dto=profile_query(key,'shodan')
            self.assertTrue(dto['supported'],key)
            self.assertIn('shodan.module:',dto['query'],key)
            self.assertNotRegex(dto['query'],r'\b(?:port|product)\s*[:=]',key)

    def test_legacy_http_profiles_are_available_to_normalized_all_port_discover(self):
        rows={x['key']:x for x in _types()}
        for key in ('gitlab','jenkins','kibana','grafana','swagger','nexus','registry','rabbitmq','solr','gitea','gogs'):
            self.assertTrue(rows[key]['legacy'])
            self.assertEqual(set(rows[key]['discovery_modes']['fingerprint']),{'shodan','zoomeye','netlas'},key)
            for provider in ('shodan','zoomeye','netlas'):
                dto=profile_query(key,provider)
                self.assertTrue(dto['supported'],key)
                self.assertTrue(dto['basis'],key)
                self.assertNotRegex(dto['query'],r'\b(?:port|product)\s*[:=]',key)

    def test_unknown_profile_cannot_silently_become_a_port_search(self):
        with self.assertRaises(ValueError):profile_query('invented-protocol','shodan')

    def test_shodan_modules_match_the_published_snapshot(self):
        import json,re
        from pathlib import Path
        from leakscope.phase3.catalog import catalog
        known=json.loads((Path(__file__).parent/'fixtures/shodan-protocols-20261107.json').read_text())
        for entry in catalog():
            query=entry['query_variants']['fingerprint']['shodan']['query']
            for match in re.finditer(r'shodan\.module:([a-zA-Z0-9_,\-]+)',query):
                for module in match.group(1).split(','):
                    self.assertIn(module,known,entry['key'])

    def test_protocol_family_uses_observed_netlas_classification(self):
        from leakscope.phase3.providers import _normalize
        row=_normalize('netlas',{'data':{'ip':'192.0.2.20','port':43101,'prot4':'tcp','prot7':'postgres'}},{})
        self.assertEqual(row['service_type'],'postgresql')

    def test_specific_secure_protocol_is_not_replaced_by_netlas_protocol_family(self):
        from leakscope.phase3.providers import _normalize
        row=_normalize('netlas',{'data':{'ip':'192.0.2.21','port':9443,'prot4':'tcp','prot7':'http','protocol':'https','http':{'title':'Fixture'}}},{})
        self.assertEqual(row['scheme'],'https')

    def test_type_dto_exposes_a_specific_missing_preset_reason(self):
        rows={x['key']:x for x in _types()}
        missing=rows['smux']['query_variants']['fingerprint']['shodan']
        self.assertFalse(missing['supported'])
        self.assertIn('scanner list',missing['reason'])
        self.assertIn('zoomeye',rows['smux']['discovery_modes']['fingerprint'])

    def test_legacy_netlas_preview_uses_response_query_instead_of_port_fallback(self):
        from leakscope_app.workbench import _netlas_discovery_query
        query=_netlas_discovery_query('gitlab',{})
        self.assertIn('http.title:',query)
        self.assertNotRegex(query,r'\bport:')
