"""OT classifications remain protocol candidates across arbitrary indexed ports."""
import json
from pathlib import Path
from django.test import SimpleTestCase
from leakscope.phase3.fingerprints import fingerprint_query, HTTP_CAPTURE_TYPES


class AddedOTProfilesTests(SimpleTestCase):
    def test_missing_protocol_profiles_have_independent_provider_candidates(self):
        for key in ('lantronix', 'lantronix-config', 'smux', 'vertx-edge'):
            for provider in ('zoomeye', 'netlas'):
                with self.subTest(key=key, provider=provider):
                    dto = fingerprint_query(key, provider)
                    self.assertTrue(dto['supported'], dto['reason'])
                    self.assertNotRegex(dto['query'], r'\b(?:port|product|app)\s*[:=]')
                    self.assertIn('candidate', dto['reason'])
                    self.assertIn('not live-verified', dto['reason'])
                    self.assertNotIn(key, HTTP_CAPTURE_TYPES)

    def test_shodan_modules_match_checked_snapshot_without_transport_substitution(self):
        checked = json.loads((Path(__file__).parent / 'fixtures/shodan-protocols-20261107.json').read_text())
        for key, modules in {'lantronix-config': ('lantronix-udp',), 'vertx-edge': ('vertx-edge',),
                             'fox': ('fox', 'secure-fox'), 's7': ('s7',)}.items():
            dto = fingerprint_query(key, 'shodan')
            self.assertTrue(dto['supported'], dto['reason'])
            self.assertEqual(dto['query'], 'shodan.module:' + ','.join(modules))
            self.assertTrue(all(module in checked for module in modules))
        self.assertNotIn('lantronix-udp', fingerprint_query('lantronix', 'shodan')['query'])
        self.assertFalse(fingerprint_query('smux', 'shodan')['supported'])

    def test_requested_protocol_aliases_use_zoomeye_service_field(self):
        for key, alias in {'ethernetip': 'ethernetip-1', 'fox': 'niagara-fox',
                           's7': 's7', 'srtp': 'ge-srtp', 'iec104': 'iec-60870-5-104'}.items():
            dto = fingerprint_query(key, 'zoomeye')
            self.assertIn('service="' + alias + '"', dto['query'])
            self.assertNotIn('app=', dto['query'])

    def test_udp_and_tcp_profiles_keep_distinct_transport_semantics(self):
        for key in ('lantronix-config', 'vertx-edge'):
            self.assertIn('protocol="UDP"', fingerprint_query(key, 'zoomeye')['query'])
            self.assertIn('prot4:udp', fingerprint_query(key, 'netlas')['query'])
        self.assertIn('TCP setup', fingerprint_query('lantronix', 'zoomeye')['reason'])
        self.assertIn('not an ICS-specific', fingerprint_query('smux', 'zoomeye')['reason'])

    def test_decoded_s7_fields_are_preserved(self):
        self.assertEqual(fingerprint_query('s7', 'netlas')['query'], 's7.module:* AND s7.module_type:*')
