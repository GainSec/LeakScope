"""Native IP range context is cached on browse and explicit on refresh."""
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase, override_settings

from leakscope.phase3 import cloud_ranges
from leakscope_app.network_context import context


class NetworkContextTests(SimpleTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        setting = override_settings(DATA_DIR=self.root)
        setting.enable()
        self.addCleanup(setting.disable)
        cloud_ranges._read.cache_clear()
        self.factory = RequestFactory()

    def test_ip_view_is_cached_only_with_longest_prefix_and_dated_source(self):
        folder = self.root / 'reference-feeds'
        folder.mkdir()
        (folder / 'aws.json').write_text(json.dumps({'provider': 'aws', 'updated_at': '2026-11-11T01:00:00Z',
            'published_at': '2026-11-10', 'source_url': cloud_ranges.FEEDS['aws'][1], 'prefixes': [
                {'prefix': '8.8.0.0/16', 'service': 'AMAZON', 'region': 'global'},
                {'prefix': '8.8.8.0/24', 'service': 'EC2', 'region': 'fixture-region'},
            ]}))
        with patch('requests.get', side_effect=AssertionError('No browse request')), patch('socket.getaddrinfo', side_effect=AssertionError('No DNS')):
            response = context(self.factory.get('/api/assets/network-ranges', {'ip': '8.8.8.8'}))
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertFalse(data['network_contact'])
        self.assertEqual(data['ip'], '8.8.8.8')
        self.assertEqual(data['matches'][0]['prefix'], '8.8.8.0/24')
        self.assertEqual(data['matches'][0]['services'], ['EC2'])
        self.assertEqual(data['matches'][0]['regions'], ['fixture-region'])
        self.assertEqual(data['matches'][0]['source_url'], cloud_ranges.FEEDS['aws'][1])
        self.assertEqual(data['datasets'][0]['published_at'], '2026-11-10')

    def test_missing_private_and_ipv6_context_never_resolve_or_refresh(self):
        with patch('requests.get', side_effect=AssertionError('No request')), patch('socket.getaddrinfo', side_effect=AssertionError('No DNS')):
            for ip in ('8.8.8.8', '127.0.0.1', '2606:4700::1111'):
                data = json.loads(context(self.factory.get('/', {'ip': ip})).content)
                self.assertEqual(data['matches'], [])
                self.assertEqual(len(data['datasets']), 4)
                self.assertFalse(data['network_contact'])
            for ip in ('example.com', 'https://8.8.8.8', '8.8.8.8:443', 'fe80::1%en0', '999.8.8.8'):
                self.assertEqual(context(self.factory.get('/', {'ip': ip})).status_code, 400)

    def test_refresh_passes_only_explicit_provider_selection_and_preserves_failure_outcome(self):
        outcome = {'requests': 1, 'results': [{'provider': 'gcp', 'status': 'refresh_failed'}], 'basis': cloud_ranges.BASIS}
        with patch('leakscope.phase3.cloud_ranges.refresh', return_value=outcome) as refresh:
            response = context(self.factory.post('/', json.dumps({'providers': ['gcp']}), content_type='application/json'))
        self.assertEqual(response.status_code, 200)
        refresh.assert_called_once_with(['gcp'])
        self.assertEqual(json.loads(response.content), outcome)

    def test_invalid_refresh_or_method_makes_no_feed_request(self):
        with patch('requests.get', side_effect=AssertionError('No request')):
            for payload in ({}, {'providers': []}, {'providers': ['unknown']}, {'providers': ['aws'] * 5},
                            {'providers': 'aws'}, {'providers': ['aws'], 'url': 'https://example.com'}):
                response = context(self.factory.post('/', json.dumps(payload), content_type='application/json'))
                self.assertEqual(response.status_code, 400)
            self.assertEqual(context(self.factory.delete('/')).status_code, 405)
