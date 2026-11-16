import json
from unittest.mock import patch

import requests
from django.test import SimpleTestCase

from leakscope.phase3.enrichment import enrich_host
from leakscope.phase3.providers import ProviderRateLimit
from tests.test_phase3_providers import Response


class Phase3EnrichmentTests(SimpleTestCase):
    config = {'NETLAS_API_KEY': 'netlas-fixture-secret'}

    @patch('leakscope.phase3.providers.requests.request')
    def test_domain_uses_three_documented_calls_and_private_auth(self, request):
        request.side_effect = [Response({'ip': '192.0.2.1'}), Response({'items': [{'data': {'domain': 'example.com', 'a': ['192.0.2.1']}}]}), Response({'items': [{'data': {'names': ['example.com'], 'fingerprint_sha256': 'abc'}}]})]
        result = enrich_host('EXAMPLE.COM.', self.config)
        self.assertFalse(result['partial'])
        self.assertEqual(result['results']['host']['ip'], '192.0.2.1')
        self.assertEqual(result['results']['dns'][0]['data']['domain'], 'example.com')
        self.assertEqual(len(result['results']['certificates']), 1)
        self.assertEqual(request.call_count, 3)
        calls = request.call_args_list
        self.assertEqual([c.args[1] for c in calls], ['https://app.netlas.io/api/host/example.com/', 'https://app.netlas.io/api/domains/', 'https://app.netlas.io/api/certs/'])
        self.assertEqual(calls[1].kwargs['params'], {'q': 'domain:"example.com"', 'start': 0})
        self.assertEqual(calls[2].kwargs['params'], {'q': 'certificate.names.keyword:"example.com"', 'start': 0})
        for call in calls:
            self.assertEqual(call.kwargs['headers']['Authorization'], 'Bearer netlas-fixture-secret')
            self.assertFalse(call.kwargs['allow_redirects'])
            self.assertNotIn('netlas-fixture-secret', call.args[1])

    @patch('leakscope.phase3.providers.requests.request')
    def test_enrichment_uses_shared_provider_and_certificate_throttle_buckets(self, request):
        buckets = []
        request.side_effect = [Response({'ip': '192.0.2.1'}), Response({'items': []}), Response({'items': []})]
        enrich_host('example.com', dict(self.config, _rate_limit=buckets.append))
        self.assertEqual(buckets, ['netlas', 'netlas', 'netlas:certificates'])
        self.assertEqual(request.call_count, 3)

    @patch('leakscope.phase3.providers.requests.request')
    def test_ip_dns_uses_real_fields_and_certificate_search_is_not_invented(self, request):
        request.side_effect = [Response({'ip': '192.0.2.1'}), Response({'items': []})]
        result = enrich_host('192.0.2.1', self.config)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args.kwargs['params']['q'], 'a:"192.0.2.1"')
        self.assertTrue(result['partial'])
        self.assertTrue(any('certificate' in e.lower() and 'IP' in e for e in result['errors']))

    @patch('leakscope.phase3.providers.requests.request')
    def test_ipv6_reverse_dns_and_idna_are_canonical(self, request):
        request.return_value = Response({'items': []})
        result = enrich_host('[2001:0db8::1]', self.config, ['dns'])
        self.assertFalse(result['partial'])
        self.assertEqual(request.call_args.kwargs['params']['q'], 'aaaa:"2001:db8::1"')
        request.reset_mock(); request.return_value = Response({'ip': '192.0.2.1'})
        enrich_host('BÜCHER.example.', self.config, ['host'])
        self.assertTrue(request.call_args.args[1].endswith('/host/xn--bcher-kva.example/'))

    @patch('leakscope.phase3.providers.requests.request')
    def test_invalid_host_and_collections_never_make_requests(self, request):
        for host in ('https://example.com/', 'example.com/a', '../evil', 'example.com?x=1', 'example.com" OR *', 'a\nb.example', '-bad.example', 'a..example', '999.1.2.3', '', None):
            with self.subTest(host=host), self.assertRaises(ValueError): enrich_host(host, self.config)
        for collections in ([], 'dns', ['wrong'], ['dns', 'dns'], [None], [True]):
            with self.subTest(collections=collections), self.assertRaises(ValueError): enrich_host('example.com', self.config, collections)
        with self.assertRaises(ValueError): enrich_host('example.com', {}, ['host'])
        request.assert_not_called()

    @patch('leakscope.phase3.providers.requests.request')
    def test_searches_keep_twenty_rows_and_bound_reflected_metadata(self, request):
        request.return_value = Response({'items': [{'data': {'name': str(i), 'banner': 'netlas-fixture-secret' + 'x' * 50000}} for i in range(30)]})
        result = enrich_host('example.com', self.config, ['dns', 'certificates'])
        self.assertEqual(request.call_count, 2)
        self.assertEqual(len(result['results']['dns']), 20)
        self.assertEqual(len(result['results']['certificates']), 20)
        self.assertLess(len(json.dumps(result).encode()), 65536)
        self.assertNotIn('netlas-fixture-secret', json.dumps(result))
        self.assertTrue(result['partial'])

    @patch('leakscope.phase3.providers.requests.request')
    def test_unicode_metadata_remains_bounded_when_persisted_as_ascii_json(self, request):
        request.side_effect = [Response({'names': ['é' * 200] * 30}), Response({'items': [{'data': {'name': 'é' * 300}}] * 20}), Response({'items': [{'data': {'name': 'é' * 300}}] * 20})]
        result = enrich_host('example.com', self.config)
        self.assertLess(len(json.dumps(result).encode()), 65536)
        self.assertTrue(result['partial'])

    @patch('leakscope.phase3.providers.requests.request')
    def test_certificate_rate_limit_preserves_host_and_dns_without_retry(self, request):
        request.side_effect = [Response({'ip': '192.0.2.1'}), Response({'items': []}), Response(status=429, headers={'Retry-After': '23'})]
        result = enrich_host('example.com', self.config)
        self.assertTrue(result['partial'])
        self.assertEqual(result['retry_after'], 23)
        self.assertEqual(result['results']['host']['ip'], '192.0.2.1')
        self.assertEqual(request.call_count, 3)

    @patch('leakscope.phase3.providers.requests.request')
    def test_all_failed_raises_safe_error_and_single_rate_limit_keeps_retry_after(self, request):
        request.side_effect = requests.Timeout('netlas-fixture-secret')
        with self.assertRaises(ValueError) as caught: enrich_host('example.com', self.config, ['host', 'dns'])
        self.assertNotIn('netlas-fixture-secret', str(caught.exception))
        request.side_effect = None; request.return_value = Response(status=429, headers={'Retry-After': '45'})
        with self.assertRaises(ProviderRateLimit) as caught: enrich_host('example.com', self.config, ['certificates'])
        self.assertEqual(caught.exception.retry_after, 45)
        request.reset_mock()
        with self.assertRaisesMessage(ValueError, 'IP'): enrich_host('192.0.2.1', self.config, ['certificates'])
        request.assert_not_called()

    @patch('leakscope.phase3.providers.requests.request')
    def test_malformed_collection_does_not_discard_successful_empty_collection(self, request):
        request.side_effect = [Response({'items': 'invalid'}), Response({'items': []})]
        result = enrich_host('example.com', self.config, ['dns', 'certificates'])
        self.assertTrue(result['partial'])
        self.assertEqual(result['results']['certificates'], [])
        self.assertTrue(result['errors'])
