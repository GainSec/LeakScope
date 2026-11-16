from django.test import SimpleTestCase, TestCase

from leakscope.intelligence.queries import compile_queries
from leakscope.phase3.catalog import catalog_entry, profile_query
from leakscope.phase3.capture import supported_actions


class GrpcDeviceQueriesTests(SimpleTestCase):
    def test_types_and_catalog_expose_profiles_without_reference_ports(self):
        from leakscope_app.workbench import _types
        rows = {row['key']: row for row in _types()}
        self.assertIn('grpc', rows)
        self.assertEqual(rows['plc']['ports'], [])
        self.assertIn('identity', rows['grpc']['fetch_actions'])
    def test_grpc_queries_match_indexed_protocol_or_response_without_ports(self):
        for provider in ('shodan', 'zoomeye', 'netlas'):
            dto = profile_query('grpc', provider)
            self.assertTrue(dto['supported'])
            self.assertIn('application/grpc', dto['query'])
            self.assertNotRegex(dto['query'], r'\b(?:port|product)\s*[:=]')
            self.assertIn('candidate', dto['reason'])
        self.assertFalse(catalog_entry('grpc')['http_capture'])
        self.assertTrue(catalog_entry('grpc')['identity'])

    def test_generic_protocol_and_device_have_no_invented_default_ports(self):
        for key in ('grpc', 'plc'):
            self.assertEqual(catalog_entry(key)['ports'], [])
            for provider in ('shodan', 'zoomeye', 'netlas'):
                for mode in ('ports', 'nonstandard'):
                    dto = profile_query(key, provider, mode)
                    self.assertFalse(dto['supported'])
                    self.assertEqual(dto['query'], '')
                    self.assertIn('reference port', dto['reason'])

    def test_plc_is_a_device_class_and_not_a_protocol_claim(self):
        self.assertEqual(profile_query('plc', 'zoomeye')['query'], 'device="plc"')
        self.assertEqual(profile_query('plc', 'shodan')['query'], 'device:plc')
        self.assertIn('classification', profile_query('plc', 'zoomeye')['reason'])
        self.assertFalse(profile_query('plc', 'netlas')['supported'])
        self.assertIn('device', profile_query('plc', 'netlas')['reason'])
        self.assertFalse(catalog_entry('plc')['identity'])

    def test_device_signal_is_translated_and_never_dropped_on_netlas(self):
        dtos = compile_queries([{'field': 'device', 'value': 'plc'}, {'field': 'country', 'value': 'us'}])
        self.assertEqual(dtos['zoomeye']['query'], 'device="plc" && country="US"')
        self.assertEqual(dtos['shodan']['query'], 'device:"plc" country:"US"')
        self.assertFalse(dtos['netlas']['supported'])
        self.assertEqual(dtos['netlas']['query'], '')

    def test_grpc_capture_is_identity_and_wire_not_screenshot(self):
        for scheme in ('', 'grpc', 'grpcs', 'https'):
            actions = supported_actions({'address': '127.0.0.1', 'service_type': 'grpc', 'transport': 'tcp', 'port': 42631, 'scheme': scheme})
            self.assertIn('identity', actions)
            self.assertIn('tcp', actions)
            self.assertNotIn('screenshot', actions)

    def test_indexed_grpc_classifier_or_header_preserves_tls_for_identity(self):
        from leakscope.phase3.providers import _normalize
        for raw in (
            {'service': 'grpc', 'url': 'https://127.0.0.1:42631/'},
            {'service': 'grpcs'},
            {'service': 'https', 'http': {'headers': {'content-type': 'application/grpc+proto'}}},
            {'service': 'https', 'banner': 'HTTP/2 200\r\nContent-Type: application/grpc\r\n\r\n'},
            {'service': 'https', 'header': 'HTTP/2 200\r\nContent-Type: application/grpc\r\n\r\n'},
        ):
            row = _normalize('zoomeye', dict(ip='127.0.0.1', port=42631, **raw), {})
            self.assertEqual(row['service_type'], 'grpc')
            self.assertEqual(row['scheme'], 'https')
        row = _normalize('zoomeye', {'ip': '127.0.0.1', 'port': 42631, 'service': 'https',
                         'http': {'headers': {'content-type': 'application/grpc-web+proto'}}}, {})
        self.assertEqual(row['service_type'], 'https')

    def test_zoomeye_protocol_alias_exposes_existing_identity_adapter(self):
        from leakscope.phase3.providers import _normalize
        row = _normalize('zoomeye', {'ip': '127.0.0.1', 'port': 42631, 'service': 'ethernetip-1'}, {})
        self.assertEqual(row['service_type'], 'ethernetip')
        self.assertEqual(row['metadata']['service'], 'ethernetip-1')
        self.assertIn('identity', supported_actions(row))


class DeviceEvidenceTests(TestCase):
    def test_observed_device_can_generate_a_similar_query_with_provenance(self):
        from leakscope.phase3.state import ingest_observation
        item = ingest_observation('zoomeye', {'address': '192.0.2.91', 'port': 42631,
            'service_type': 's7', 'metadata': {'device': 'plc'}})
        body = {'references': [{'type': 'endpoint', 'id': str(item.pk)}]}
        response = self.client.post('/api/intelligence/context', body, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        field = next((f for f in response.json()['fields'] if f['field'] == 'device'), None)
        self.assertIsNotNone(field)
        self.assertEqual(field['value'], 'plc')
        self.assertEqual(field['provenance']['provider'], 'zoomeye')
