import re
from unittest.mock import patch
from django.test import SimpleTestCase
from leakscope.phase3 import catalog as profiles


class ServiceFingerprintTests(SimpleTestCase):
    def query(self, key, provider='shodan', mode='fingerprint'):
        return profiles.profile_query(key, provider, mode)

    def test_elasticsearch_uses_response_structure_on_every_provider(self):
        for provider, field in [('shodan', 'http.html:'), ('zoomeye', 'http.body='), ('netlas', 'http.body:')]:
            dto = self.query('elastic', provider)
            self.assertTrue(dto['supported'])
            self.assertIn(field, dto['query'])
            self.assertIn('cluster_uuid', dto['query'])
            self.assertIn('You Know, for Search', dto['query'])
            self.assertNotIn('product', dto['query'])
            self.assertNotIn('port', dto['query'])
            self.assertTrue(all(url.startswith('https://') for url in dto['basis']))

    def test_shared_ports_do_not_make_same_technology_fingerprints(self):
        for a, b in [('elastic', 'opensearch'), ('qdrant', 'weaviate'), ('homeassistant', 'openhab')]:
            self.assertNotEqual(self.query(a)['query'], self.query(b)['query'])
        self.assertTrue(self.query('timescaledb')['supported'])
        self.assertTrue(self.query('scylla')['supported'])
        self.assertIn('does not identify TimescaleDB', self.query('timescaledb')['reason'])
        self.assertIn('does not identify ScyllaDB', self.query('scylla')['reason'])

    def test_nonstandard_excludes_all_defaults_after_independent_signature(self):
        for provider in ('shodan', 'zoomeye', 'netlas'):
            base = self.query('couchdb', provider)
            dto = self.query('couchdb', provider, 'nonstandard')
            self.assertTrue(dto['supported'])
            self.assertIn(base['query'], dto['query'])
            for port in (5984, 6984):
                self.assertIn(str(port), dto['query'])
            self.assertEqual(base['basis'], dto['basis'])
        self.assertIn('-port:5984,6984', self.query('couchdb', mode='nonstandard')['query'])
        self.assertIn('port!="5984"', self.query('couchdb', 'zoomeye', 'nonstandard')['query'])
        self.assertIn('NOT port:(5984 OR 6984)', self.query('couchdb', 'netlas', 'nonstandard')['query'])

    @patch.dict(profiles.FINGERPRINTS, {"cipio": None})
    def test_no_port_fallback_for_undefined_fingerprint(self):
        for provider in ('shodan', 'zoomeye', 'netlas'):
            for mode in ('fingerprint', 'nonstandard'):
                dto = self.query('cipio', provider, mode)
                self.assertFalse(dto['supported'])
                self.assertEqual(dto['query'], '')
                self.assertTrue(dto['reason'])
            self.assertTrue(self.query('cipio', provider, 'ports')['supported'])

    def test_native_metadata_is_provider_specific_and_port_independent(self):
        self.assertIn('modbus.length:', self.query('modbus', 'netlas')['query'])
        self.assertIn('s7.module', self.query('s7', 'netlas')['query'])
        self.assertEqual('shodan.module:s7', self.query('s7')['query'])
        self.assertIn('redis_version', self.query('redis')['query'])
        self.assertIn('raw.strings:', self.query('rsync', 'netlas')['query'])
        self.assertIn('@RSYNCD', self.query('rsync')['query'])
        # An SSH greeting does not establish an SFTP subsystem.
        self.assertTrue(self.query('sftp')['supported'])
        self.assertIn('SFTP subsystem is not verified', self.query('sftp')['reason'])

    def test_iot_is_visible_with_http_capture_and_independent_queries(self):
        for key in ('homeassistant', 'openhab', 'nodered', 'thingsboard', 'tasmota', 'esphome', 'shelly', 'octoprint', 'domoticz', 'zigbee2mqtt'):
            row = profiles.catalog_entry(key)
            self.assertIn('iot', row['categories'])
            self.assertTrue(row['http_capture'])
            self.assertTrue(self.query(key)['supported'])
        self.assertIn('iot', profiles.catalog_entry('mqtt')['categories'])
        self.assertEqual(profiles.catalog_entry('modbus')['category_label'], 'ICS / SCADA / OT')
        for key in ('rtsp', 'coap', 'onvif', 'upnp'):
            self.assertIn('iot', profiles.catalog_entry(key)['categories'])
        self.assertFalse(profiles.catalog_entry('rtsp')['http_capture'])

    def test_dtos_are_complete_independent_and_inputs_validated(self):
        for row in profiles.catalog():
            self.assertEqual(set(row['query_variants']), {'fingerprint', 'nonstandard', 'ports'})
            for mode, variants in row['query_variants'].items():
                self.assertEqual(set(variants), {'shodan', 'zoomeye', 'netlas'})
                for provider, dto in variants.items():
                    self.assertEqual(set(dto), {'query', 'supported', 'reason', 'basis'})
                    self.assertIsInstance(dto['basis'], list)
                    if dto['supported']:
                        self.assertTrue(dto['query'])
                        self.assertTrue(dto['basis'])
                    elif mode != 'ports':
                        self.assertEqual(dto['query'], '')
        dto = self.query('elasticsearch')
        dto['basis'].append('broken')
        self.assertNotIn('broken', self.query('elastic')['basis'])
        with self.assertRaises(ValueError): self.query('elastic', 'nope')
        with self.assertRaises(ValueError): self.query('elastic', mode='nope')

    def test_supported_fingerprints_never_require_product_or_default_ports(self):
        for row in profiles.catalog():
            for provider, dto in row['query_variants']['fingerprint'].items():
                if dto['supported']:
                    self.assertNotRegex(dto['query'], r'\bport\s*[:=]')
                    self.assertNotRegex(dto['query'], r'\bproduct\s*[:=]')
                    self.assertNotIn('tag.name', dto['query'])
        # Coverage is substantive, independently of the larger port-candidate list.
        supported = [r for r in profiles.catalog() if any(v['supported'] for v in r['query_variants']['fingerprint'].values())]
        self.assertGreaterEqual(len(supported), 45)
        self.assertGreaterEqual(len([r for r in supported if r['category'] == 'database']), 15)
        self.assertGreaterEqual(len([r for r in supported if r['category'] == 'storage']), 8)
        self.assertGreaterEqual(len([r for r in supported if 'iot' in r['categories']]), 10)
