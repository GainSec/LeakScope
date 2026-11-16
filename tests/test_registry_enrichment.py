"""Registry fixtures: no live registry, target, or paid provider requests."""
import io
import json
from unittest import TestCase
from unittest.mock import Mock, patch

from leakscope.phase3 import registry


BOOTSTRAP = {'services': [[['8.0.0.0/8'], ['https://rdap.arin.net/registry/']]]}
RDAP = {
    'objectClassName': 'ip network', 'startAddress': '8.8.8.0', 'endAddress': '8.8.8.255',
    'name': 'GOGL', 'country': 'US',
    'entities': [{'roles': ['registrant'], 'vcardArray': ['vcard', [
        ['version', {}, 'text', '4.0'], ['fn', {}, 'text', 'Google LLC'],
        ['org', {}, 'text', 'Google LLC']]]}],
    'links': [{'rel': 'related', 'href': 'http://127.0.0.1/private'}],
}


class RegistryTests(TestCase):
    def setUp(self):
        registry._bootstrap_cache.clear()

    @patch('leakscope.phase3.registry._fetch_json')
    def test_invalid_ip_input_does_not_accept_integer_or_scoped_identities(self, fetch):
        for value in (True, 134744072, 'https://8.8.8.8', 'fe80::1%en0'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                registry.netblock_info(value)

    @patch('leakscope.phase3.registry._fetch_json')
    def test_private_address_has_no_public_lookup(self, fetch):
        result = registry.netblock_info('192.168.0.1')
        self.assertEqual(result['networks'], [])
        self.assertEqual(result['status'], 'not_applicable')
        self.assertFalse(result['partial'])
        fetch.assert_not_called()

    @patch('leakscope.phase3.registry._fetch_json')
    def test_allocation_is_registration_and_untrusted_links_are_never_followed(self, fetch):
        fetch.side_effect = [(BOOTSTRAP, 'https://data.iana.org/rdap/ipv4.json'),
                             (RDAP, 'https://rdap.arin.net/registry/ip/8.8.8.8')]
        result = registry.netblock_info('8.8.8.8')
        self.assertEqual(len(result['networks']), 1)
        network = result['networks'][0]
        self.assertEqual(network['cidr'], '8.8.8.0/24')
        self.assertEqual(network['organization'], 'Google LLC')
        self.assertEqual(network['relation'], 'registered_to')
        self.assertTrue(network['registration_only'])
        self.assertEqual(network['source'], 'rdap')
        self.assertEqual(result['asns'], [])
        self.assertEqual(fetch.call_count, 2)
        self.assertFalse(result['partial'])

    @patch('leakscope.phase3.registry._fetch_json')
    def test_bootstrap_ipv6_and_longest_prefix_match(self, fetch):
        bootstrap = {'services': [[['2000::/3'], ['https://rdap.apnic.net/']],
                                  [['2606:4700::/32'], ['https://rdap.arin.net/registry/']]]}
        payload = dict(RDAP, startAddress='2606:4700::', endAddress='2606:4700:ffff:ffff:ffff:ffff:ffff:ffff')
        fetch.side_effect = [(bootstrap, 'https://data.iana.org/rdap/ipv6.json'), (payload, 'https://rdap.arin.net/registry/ip/2606:4700::1111')]
        result = registry.netblock_info('2606:4700::1111')
        self.assertEqual(result['networks'][0]['cidr'], '2606:4700::/32')
        self.assertIn('/ipv6.json', fetch.call_args_list[0].args[0])
        self.assertIn('rdap.arin.net', fetch.call_args_list[1].args[0])

    @patch('leakscope.phase3.registry._fetch_json')
    def test_malicious_bootstrap_and_mismatched_range_are_errors(self, fetch):
        fetch.return_value = ({'services': [[['8.0.0.0/8'], ['https://evil.example/']]]}, '')
        result = registry.netblock_info('8.8.8.8')
        self.assertTrue(result['partial'])
        self.assertEqual(result['networks'], [])
        self.assertEqual(fetch.call_count, 1)
        registry._bootstrap_cache.clear()
        fetch.side_effect = [(BOOTSTRAP, ''), (dict(RDAP, startAddress='1.1.1.0', endAddress='1.1.1.255'), '')]
        result = registry.netblock_info('8.8.8.8')
        self.assertEqual(result['networks'], [])
        self.assertTrue(result['errors'])

    @patch('leakscope.phase3.registry._fetch_json')
    def test_bootstrap_cache_reuses_only_bootstrap(self, fetch):
        fetch.side_effect = [(BOOTSTRAP, ''), (RDAP, 'https://rdap.arin.net/registry/ip/8.8.8.8'),
                             (RDAP, 'https://rdap.arin.net/registry/ip/8.8.8.8')]
        registry.netblock_info('8.8.8.8')
        registry.netblock_info('8.8.8.8')
        self.assertEqual(fetch.call_count, 3)

    @patch('leakscope.phase3.registry._request')
    def test_org_search_two_explicit_calls_escape_names_and_preserve_actual_registrant(self, request):
        request.side_effect = [
            {'items': [{'data': {'domain': 'WWW.Example.COM.', 'registrant': {'organization': 'Other Entity'}}}]},
            {'items': [{'data': {'net': {'cidr': ['8.8.8.0/24'], 'organization': 'Example "Corp"', 'country': 'US'}, 'asn': {'asn': 15169, 'name': 'GOOGLE'}}}]},
        ]
        result = registry.discover_organization('Example "Corp"', {'NETLAS_API_KEY': 'fixture'}, aliases=['Example Group'])
        self.assertEqual(result['requests'], 2)
        self.assertEqual([c.args[1] for c in request.call_args_list], ['/whois_domains/', '/whois_ip/'])
        query = request.call_args_list[0].kwargs['params']['q']
        self.assertIn('registrant.organization:"Example \\"Corp\\""', query)
        self.assertIn('registrant.organization:"Example Group"', query)
        self.assertEqual(result['domains'][0]['name'], 'www.example.com')
        self.assertEqual(result['domains'][0]['organization'], 'Other Entity')
        self.assertFalse(result['domains'][0]['matched_organization'])
        self.assertEqual(result['networks'][0]['relation'], 'registered_to')
        self.assertEqual(result['asns'][0]['asn'], 15169)

    @patch('leakscope.phase3.registry._request')
    def test_provider_permission_failure_is_visible_partial_with_successful_other_collection(self, request):
        request.side_effect = [ValueError('Netlas returned HTTP 403; check permissions'), {'items': []}]
        result = registry.discover_organization('Example Corp', {})
        self.assertEqual(result['requests'], 2)
        self.assertTrue(result['partial'])
        self.assertIn('403', result['errors'][0]['error'])

    @patch('leakscope.phase3.registry._request')
    def test_budget_bounds_calls_and_never_silently_claims_complete(self, request):
        request.return_value = {'items': [], 'total': {'value': 100}}
        result = registry.discover_organization('Example Corp', {}, max_requests=1)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(result['requests'], 1)
        self.assertTrue(result['partial'])
        with self.assertRaises(ValueError):
            registry.discover_organization('Example Corp', {}, max_requests=3)

    @patch('leakscope.phase3.registry._request')
    def test_invalid_provider_records_do_not_become_assets(self, request):
        request.side_effect = [{'items': [{'data': {'domain': 'https://evil.test/', 'registrant': {'organization': 'Corp'}}}]},
                               {'items': [{'data': {'net': {'cidr': ['127.0.0.0/8', 'broken'], 'organization': 'Corp'}}}]}]
        result = registry.discover_organization('Corp', {})
        self.assertEqual(result['domains'], [])
        self.assertEqual(result['networks'], [])
        self.assertTrue(result['partial'])

    @patch('leakscope.phase3.registry._request')
    def test_malformed_record_preserves_later_valid_candidates(self, request):
        request.side_effect = [{'items': [{'data': {'domain': 'https://bad.test'}},
                                         {'data': {'domain': 'example.com', 'registrant': {'organization': 'Corp'}}}]},
                               {'items': []}]
        result = registry.discover_organization('Corp', {})
        self.assertEqual([row['name'] for row in result['domains']], ['example.com'])
        self.assertTrue(result['partial'])
        self.assertEqual(len(result['errors']), 1)


class TransportTests(TestCase):
    def response(self, payload, status=200, headers=None):
        response = Mock(status_code=status, headers=headers or {})
        response.raw = io.BytesIO(json.dumps(payload).encode())
        return response

    @patch('leakscope.phase3.registry.requests.Session')
    def test_http_checks_tls_bounds_redirects_and_does_not_send_credentials(self, factory):
        session = factory.return_value
        session.get.return_value = self.response({}, 302, {'Location': 'https://evil.example/rdap'})
        with self.assertRaises(ValueError):
            registry._fetch_json('https://rdap.arin.net/registry/ip/8.8.8.8', registry.time.monotonic() + 10)
        self.assertEqual(session.get.call_count, 1)
        kwargs = session.get.call_args.kwargs
        self.assertTrue(kwargs['verify'])
        self.assertFalse(kwargs['allow_redirects'])
        self.assertFalse(session.trust_env)
        self.assertNotIn('Authorization', kwargs['headers'])

    @patch('leakscope.phase3.registry.requests.Session')
    def test_body_size_and_deadline_are_bounded(self, factory):
        session = factory.return_value
        session.get.return_value = self.response({}, headers={'Content-Length': str(registry.MAX_RDAP_BYTES + 1)})
        with self.assertRaisesRegex(ValueError, 'limit'):
            registry._fetch_json('https://rdap.arin.net/registry/ip/8.8.8.8', registry.time.monotonic() + 10)
        with self.assertRaisesRegex(ValueError, 'deadline'):
            registry._fetch_json('https://rdap.arin.net/registry/ip/8.8.8.8', registry.time.monotonic() - 1)

    @patch('leakscope.phase3.registry.requests.Session')
    def test_each_body_read_updates_socket_timeout_to_remaining_deadline(self, factory):
        response = Mock(status_code=200, headers={})
        response.raw.read1.side_effect = [b'{}', b'']
        factory.return_value.get.return_value = response
        payload, url = registry._fetch_json('https://rdap.arin.net/registry/ip/8.8.8.8', registry.time.monotonic() + 0.5)
        self.assertEqual(payload, {})
        self.assertEqual(response.raw._connection.sock.settimeout.call_count, 2)
        for call in response.raw._connection.sock.settimeout.call_args_list:
            self.assertGreater(call.args[0], 0)
            self.assertLessEqual(call.args[0], 0.5)

    def test_unsafe_urls_rejected_before_network(self):
        for url in ['http://rdap.arin.net/ip/8.8.8.8', 'https://user:pw@rdap.arin.net/ip/8.8.8.8',
                    'https://rdap.arin.net.evil.example/ip/8.8.8.8', 'https://127.0.0.1/ip/8.8.8.8']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                registry._fetch_json(url, registry.time.monotonic() + 10)
