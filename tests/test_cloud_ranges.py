import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from django.test import SimpleTestCase, override_settings
from leakscope.phase3 import cloud_ranges


class CloudRangeTests(SimpleTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        settings = override_settings(DATA_DIR=self.root)
        settings.enable()
        self.addCleanup(settings.disable)

    def snapshot(self, provider, records):
        directory = self.root / 'reference-feeds'
        directory.mkdir(exist_ok=True)
        (directory / (provider + '.json')).write_text(json.dumps({
            'provider': provider, 'updated_at': '2026-11-10T00:00:00+00:00',
            'source_url': cloud_ranges.FEEDS[provider][1], 'prefixes': records,
        }))

    def test_longest_prefix_retains_overlapping_service_labels_and_ipv6(self):
        self.snapshot('aws', [
            {'prefix': '8.0.0.0/8', 'service': 'AMAZON', 'region': 'global'},
            {'prefix': '8.8.8.0/24', 'service': 'EC2', 'region': 'fixture'},
            {'prefix': '8.8.8.0/24', 'service': 'AMAZON', 'region': 'fixture'},
            {'prefix': '2606:4700::/32', 'service': 'fixture-v6', 'region': ''},
        ])
        results = cloud_ranges.lookup(['8.8.8.8', '2606:4700::1111', '127.0.0.1'])
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]['prefix'], '8.8.8.0/24')
        self.assertEqual(results[0]['services'], ['AMAZON', 'EC2'])
        self.assertEqual(results[1]['services'], ['fixture-v6'])

    def test_uninstalled_and_malformed_feed_never_makes_a_network_request(self):
        with patch('requests.get', side_effect=AssertionError('No requests on browse')):
            self.assertEqual(cloud_ranges.lookup(['8.8.8.8']), [])
            self.assertTrue(all(row['status'] == 'not_installed' for row in cloud_ranges.dataset_status()))
        directory = self.root / 'reference-feeds'
        directory.mkdir()
        (directory / 'aws.json').write_text('bad json')
        self.assertEqual(cloud_ranges.lookup(['8.8.8.8']), [])

    def test_refresh_failure_keeps_previous_snapshot(self):
        self.snapshot('aws', [{'prefix': '8.8.8.0/24', 'service': 'EC2', 'region': 'fixture'}])
        path = self.root / 'reference-feeds/aws.json'
        original = path.read_bytes()
        import requests
        with patch('requests.get', side_effect=requests.Timeout):
            result = cloud_ranges.refresh(['aws'])
        self.assertEqual(result['results'][0]['status'], 'refresh_failed')
        self.assertEqual(path.read_bytes(), original)

    def test_vendor_parser_rejects_missing_schema_and_private_ranges(self):
        for provider, data in [('aws', {'prefixes': []}), ('gcp', {}), ('cloudflare', {})]:
            with self.assertRaises(ValueError):
                cloud_ranges._prefixes(provider, data)
        rows = cloud_ranges._prefixes('gcp', {'prefixes': [{'ipv4Prefix': '8.8.8.0/24', 'scope': 'test'}, {'ipv4Prefix': '10.0.0.0/8'}]})
        self.assertEqual(len(rows), 1)
