"""Keyless registry contracts, using synthetic fixtures (never paid APIs)."""
import copy
import json
import time
from unittest import TestCase
from unittest.mock import Mock, patch

from leakscope.phase3 import free_registry as registry


def response(payload, status=200, headers=None):
    return status, headers or {}, json.dumps(payload).encode()


def entity(handle, name, kind='org', **extra):
    return dict(handle=handle, objectClassName='entity', vcardArray=['vcard', [
        ['fn', {}, 'text', name], ['kind', {}, 'text', kind]]], **extra)


BOOT = {'services': [[['com'], ['https://rdap.verisign.com/com/v1/']]]}
DOMAIN = {'objectClassName': 'domain', 'ldhName': 'EXAMPLE.COM', 'handle': 'D123',
          'status': ['active'], 'nameservers': [{'ldhName': 'NS1.EXAMPLE.NET'}],
          'entities': [entity('R1', 'Registrar Inc', roles=['registrar'])],
          'events': [{'eventAction': 'registration', 'eventDate': '2001-01-01T00:00:00Z'}]}
NETWORK = {'startAddress': '77.233.32.0', 'endAddress': '77.233.63.255',
           'name': 'EXAMPLE-NET', 'country': 'US'}


class FreeRegistryTests(TestCase):
    def setUp(self):
        registry._cache.clear()

    @patch('leakscope.phase3.free_registry._http_once')
    def test_subdomain_uses_authoritative_domain_and_registrar_is_not_registrant(self, http):
        http.side_effect = [response(BOOT), response(DOMAIN)]
        result = registry.registration('www.example.com')
        self.assertEqual(result['target'], 'www.example.com')
        self.assertEqual(result['domain'], 'example.com')
        self.assertEqual(result['registration']['registrars'][0]['name'], 'Registrar Inc')
        self.assertEqual(result['registration']['registrants'], [])
        self.assertTrue(result['registration']['redacted'])
        self.assertEqual(result['registration']['nameservers'], ['ns1.example.net'])
        self.assertEqual(result['requests'], 2)
        self.assertEqual(result['domains'][0]['source_url'], 'https://rdap.verisign.com/com/v1/domain/example.com')
        self.assertNotIn('organization', result['domains'][0])
        self.assertFalse(result['partial'])

    @patch('leakscope.phase3.free_registry._http_once')
    def test_registrant_identity_is_distinct_and_marked_registration_only(self, http):
        payload = copy.deepcopy(DOMAIN)
        payload['entities'].append(entity('H1', 'Example Holder', roles=['registrant']))
        http.side_effect = [response(BOOT), response(payload)]
        result = registry.registration('example.com')
        self.assertEqual(result['registration']['registrants'][0]['name'], 'Example Holder')
        self.assertEqual(result['domains'][0]['organization'], 'Example Holder')
        self.assertEqual(result['domains'][0]['relation'], 'registrant_organization')
        self.assertTrue(result['domains'][0]['registration_only'])
        self.assertFalse(result['registration']['redacted'])

    @patch('leakscope.phase3.free_registry._http_once')
    def test_domain_cache_reuses_record_but_counts_zero_new_requests(self, http):
        http.side_effect = [response(BOOT), response(DOMAIN)]
        first = registry.registration('example.com')
        first['registration']['nameservers'].clear()
        second = registry.registration('mail.example.com')
        self.assertEqual(second['requests'], 0)
        self.assertTrue(second['cached'])
        self.assertEqual(second['registration']['nameservers'], ['ns1.example.net'])
        self.assertEqual(http.call_count, 2)
        self.assertEqual(second['cache_hits'], 2)
        self.assertEqual(second['observed_at'], first['observed_at'])
        self.assertEqual(second['registration']['observed_at'], first['registration']['observed_at'])
        self.assertTrue(all(source['cache_hit'] for source in second['collection_sources']))
        self.assertFalse(any(source['cache_hit'] for source in first['collection_sources']))
        self.assertEqual([source['fetched_at'] for source in second['collection_sources']],
                         [source['fetched_at'] for source in first['collection_sources']])
        self.assertEqual(second['domains'][0]['observed_at'], first['registration']['observed_at'])

    @patch('leakscope.phase3.free_registry._http_once')
    def test_mismatched_domain_fails_and_arbitrary_links_are_not_crawled(self, http):
        http.side_effect = [response(BOOT), response(dict(DOMAIN, ldhName='OTHER.COM', links=[{'href': 'https://evil.example/'}]))]
        result = registry.registration('example.com')
        self.assertTrue(result['partial'])
        self.assertEqual(result['domains'], [])
        self.assertRegex(result['errors'][0]['error'], 'match')
        self.assertEqual(http.call_count, 2)

    @patch('leakscope.phase3.free_registry._http_once')
    def test_bootstrap_rejects_private_and_unsafe_authority_urls(self, http):
        for url in ['http://rdap.example/', 'https://127.0.0.1/', 'https://user@rdap.example/', 'https://rdap.example:444/', 'https://rdap.example/a/../']:
            registry._cache.clear()
            http.reset_mock()
            http.return_value = response({'services': [[['com'], [url]]]})
            with self.subTest(url=url):
                result = registry.registration('example.com')
                self.assertTrue(result['partial'])
                self.assertEqual(http.call_count, 1)

    @patch('leakscope.phase3.free_registry._http_once')
    def test_redirect_cannot_leave_authority_and_is_counted(self, http):
        http.side_effect = [response(BOOT), response({}, 302, {'location': 'https://evil.example/domain/example.com'})]
        result = registry.registration('example.com')
        self.assertEqual(result['requests'], 2)
        self.assertTrue(result['partial'])
        self.assertEqual(http.call_count, 2)

    @patch('leakscope.phase3.free_registry._http_once')
    def test_not_found_is_explicit_not_a_fabricated_registration(self, http):
        http.side_effect = [response(BOOT), response({}, 404)]
        result = registry.registration('example.com')
        self.assertEqual(result['status'], 'not_found')
        self.assertEqual(result['domains'], [])
        self.assertEqual(result['requests'], 2)

    @patch('leakscope.phase3.free_registry._http_once')
    def test_invalid_domain_rejected_without_io(self, http):
        for value in [True, 'https://example.com', '*.example.com', '127.0.0.1', 'com']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                registry.registration(value)
        http.assert_not_called()

    @patch('leakscope.phase3.free_registry._http_once')
    def test_private_suffix_tenant_uses_actual_registered_domain(self, http):
        http.side_effect = [response({'services': [[['io'], ['https://rdap.nic.io/']]]}),
                            response(dict(DOMAIN, ldhName='GITHUB.IO'))]
        result = registry.registration('tenant.github.io')
        self.assertEqual(result['domain'], 'github.io')
        self.assertIn('/domain/github.io', http.call_args_list[1].args[0])

    @patch('leakscope.phase3.free_registry._http_once')
    def test_malformed_authority_is_reported_without_second_request(self, http):
        http.return_value = response({'services': [[['com'], ['https:///path']]]})
        result = registry.registration('example.com')
        self.assertTrue(result['partial'])
        self.assertEqual(http.call_count, 1)

    @patch('leakscope.phase3.free_registry.dns.resolver.Resolver', side_effect=OSError('resolver failed'))
    def test_dns_failure_returns_partial_collector_result(self, dns):
        result = registry.registration('example.com')
        self.assertTrue(result['partial'])
        self.assertEqual(result['requests'], 1)
        self.assertIn('DNS', result['errors'][0]['error'])

    @patch('leakscope.phase3.free_registry._http_once')
    def test_arin_org_entity_prioritized_over_customer_contacts_and_embedded_resources(self, http):
        rows = [entity('C123', 'Example'), entity('EX-1', 'Example Industries Inc'), entity('HELP-ARIN', 'Example Help', kind='individual')]
        detail = entity('EX-1', 'Example Industries Inc', networks=[NETWORK], autnums=[{'startAutnum': 399000, 'endAutnum': 399000}])
        http.side_effect = [response({'entitySearchResults': rows}), response({'entitySearchResults': []}), response(detail), response(entity('C123', 'Example'))]
        result = registry.discover_organization('Example')
        self.assertEqual(result['networks'][0]['cidr'], '77.233.32.0/19')
        self.assertEqual(result['networks'][0]['organization'], 'Example Industries Inc')
        self.assertFalse(result['networks'][0]['matched_organization'])
        self.assertTrue(result['networks'][0]['registration_only'])
        self.assertEqual(result['asns'][0]['asn'], 399000)
        self.assertIn('/entity/EX-1', http.call_args_list[2].args[0])
        self.assertEqual(result['domains'], [])
        self.assertEqual(result['requests'], 4)
        again = registry.discover_organization('Example')
        self.assertEqual(again['requests'], 0)
        self.assertEqual(again['cache_hits'], 4)
        self.assertEqual(again['networks'][0]['observed_at'], result['networks'][0]['observed_at'])
        self.assertEqual(again['entities'][0]['observed_at'], result['entities'][0]['observed_at'])
        self.assertEqual(again['asns'][0]['observed_at'], result['asns'][0]['observed_at'])
        self.assertEqual(again['observed_at'], result['observed_at'])

    @patch('leakscope.phase3.free_registry._http_once')
    def test_ripe_inverse_org_only_accepts_matching_org_reference(self, http):
        rows = [entity('ORG-EX1-RIPE', 'Example')]
        def obj(kind, attrs):
            return {'type': kind, 'attributes': {'attribute': [{'name': k, 'value': v} for k, v in attrs]}}
        objects = [obj('inetnum', [('inetnum', '77.233.32.0 - 77.233.63.255'), ('org', 'ORG-EX1-RIPE'), ('netname', 'EX')]),
                   obj('inet6num', [('inet6num', '2602:816:a000::/40'), ('org', 'ORG-EX1-RIPE')]),
                   obj('aut-num', [('aut-num', 'AS399000'), ('org', 'ORG-EX1-RIPE')]),
                   obj('inetnum', [('inetnum', '8.8.8.0 - 8.8.8.255'), ('org', 'ORG-OTHER-RIPE')])]
        http.side_effect = [response({'entitySearchResults': []}), response({'entitySearchResults': rows}), response({'objects': {'object': objects}})]
        result = registry.discover_organization('Example')
        self.assertEqual({n['cidr'] for n in result['networks']}, {'77.233.32.0/19', '2602:816:a000::/40'})
        self.assertEqual(result['asns'][0]['asn'], 399000)
        self.assertEqual(result['requests'], 3)
        self.assertTrue(all(row['source'] == 'ripe_rest' for row in result['networks'] + result['asns']))
        self.assertIn('inverse-attribute=org', http.call_args_list[2].args[0])
        self.assertTrue(all(n['matched_organization'] for n in result['networks']))

    @patch('leakscope.phase3.free_registry._http_once')
    def test_budget_never_spends_extra_requests_and_no_paid_fallback(self, http):
        http.return_value = response({'entitySearchResults': [entity('EX-1', 'Example')]})
        result = registry.discover_organization('Example', {'NETLAS_API_KEY': 'not-used'}, max_requests=1)
        self.assertEqual(http.call_count, 1)
        self.assertEqual(result['requests'], 1)
        self.assertTrue(result['partial'])
        self.assertEqual(result['networks'], [])
        self.assertRegex(result['coverage'], 'registration')

    @patch('leakscope.phase3.free_registry._http_once')
    def test_aliases_are_searched_under_same_global_budget_and_encoded_literally(self, http):
        http.return_value = response({'entitySearchResults': []})
        result = registry.discover_organization('Example & Co', aliases=['Alias', 'Other', 'More'], max_requests=8)
        # Searching stops at half the requests, so the rest are left to read what was found.
        self.assertEqual(http.call_count, 4)
        self.assertEqual(result['requests'], 4)
        self.assertTrue(result['partial'])
        self.assertIn('Example+%26+Co', http.call_args_list[0].args[0])
        self.assertIn('Alias', http.call_args_list[2].args[0])

    @patch('leakscope.phase3.free_registry._http_once')
    def test_holders_with_the_exact_name_are_read_before_prefix_matches_on_an_alias(self, http):
        noise = [entity('AAA-%d' % n, 'Intersection %d' % n) for n in range(1, 6)]
        detail = entity('ZZZ-9', 'Example Foundation', networks=[NETWORK])
        def answer(url, deadline):
            if 'entities?' in url: return response({'entitySearchResults': noise + [entity('ZZZ-9', 'Example Foundation')] if 'rdap.arin.net' in url and 'fn=Intersect' in url else []})
            return response(detail if url.endswith('/ZZZ-9') else entity(url.rsplit('/', 1)[1], 'Intersection'))
        http.side_effect = answer
        result = registry.discover_organization('Example Foundation', aliases=['Intersect'], max_requests=8)
        self.assertIn('/entity/ZZZ-9', http.call_args_list[4].args[0])
        self.assertEqual([(n['cidr'], n['matched_organization']) for n in result['networks']], [('77.233.32.0/19', True)])

    @patch('leakscope.phase3.free_registry._http_once')
    def test_malformed_search_is_partial_but_other_registry_still_runs(self, http):
        http.side_effect = [response({'entitySearchResults': 'bad'}), response({'entitySearchResults': []})]
        result = registry.discover_organization('Example')
        self.assertTrue(result['partial'])
        self.assertEqual(result['requests'], 2)
        self.assertEqual(len(result['errors']), 1)

    @patch('leakscope.phase3.free_registry._http_once')
    def test_private_or_backwards_ranges_and_asn_ranges_do_not_create_bad_nodes(self, http):
        detail = entity('EX-1', 'Example', networks=[dict(NETWORK, startAddress='10.0.0.0', endAddress='10.0.0.255'),
            dict(NETWORK, startAddress='77.233.63.255', endAddress='77.233.32.0')],
            autnums=[{'startAutnum': 1, 'endAutnum': 4294967295}])
        http.side_effect = [response({'entitySearchResults': [entity('EX-1', 'Example')]}), response({'entitySearchResults': []}), response(detail)]
        result = registry.discover_organization('Example')
        self.assertEqual(result['networks'], [])
        self.assertEqual(result['asns'], [])
        self.assertTrue(result['partial'])

    def test_validation_of_budget_name_and_aliases(self):
        for kwargs in [{'name': ''}, {'name': 'a', 'max_requests': True}, {'name': 'a', 'max_requests': 9}, {'name': 'a', 'aliases': [True]}, {'name': 'ab*'}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                registry.discover_organization(**kwargs)


class RegistryTransportTests(TestCase):
    def setUp(self):
        registry._cache.clear()

    def test_destination_rejects_unapproved_hosts_credentials_and_paths(self):
        for url in ['http://rdap.arin.net/registry/entities', 'https://127.0.0.1/',
                    'https://evil.example/', 'https://rdap.arin.net:444/',
                    'https://x@rdap.arin.net/', 'https://rdap.arin.net/a/../x',
                    'https://rdap.arin.net/%2e%2e/x', 'https://rdap.arin.net//evil',
                    'https://rdap.arin.net/path#fragment']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                registry._validated_url(url, {'rdap.arin.net'})

    @patch('leakscope.phase3.free_registry.dns.resolver.Resolver')
    def test_dns_private_or_mixed_answer_rejected_before_connect(self, dns):
        for addresses in [['127.0.0.1'], ['8.8.8.8', '192.168.1.1'], ['::ffff:127.0.0.1']]:
            dns.return_value.resolve.return_value = [Mock(address=ip) for ip in addresses]
            with self.subTest(addresses=addresses), self.assertRaises(ValueError):
                registry._public_ip('rdap.arin.net')

    @patch('leakscope.phase3.free_registry.dns.resolver.Resolver')
    def test_dns_queries_share_deadline_and_pin_single_validated_address(self, dns):
        dns.return_value.resolve.return_value = [Mock(address='8.8.8.8')]
        self.assertEqual(registry._public_ip('rdap.arin.net', time.monotonic() + 1), '8.8.8.8')
        self.assertEqual(dns.return_value.resolve.call_count, 2)
        self.assertTrue(all(0 < call.kwargs['lifetime'] <= 1 for call in dns.return_value.resolve.call_args_list))
        with self.assertRaises(ValueError):
            registry._public_ip('rdap.arin.net', 0)

    @patch('leakscope.phase3.free_registry._http_once')
    def test_size_invalid_json_redirect_and_deadline_bounds(self, http):
        for raw in [b'!' * (registry.MAX_BYTES + 1), b'not json', b'[]']:
            registry._cache.clear()
            http.return_value = (200, {}, raw)
            with self.subTest(raw=raw[:10]), self.assertRaises(ValueError):
                registry._Budget(2).get('https://rdap.arin.net/registry/entities?fn=Example', {'rdap.arin.net'})
        http.return_value = (302, {'location': '/registry/entities?fn=Example'}, b'')
        with self.assertRaises(ValueError):
            registry._Budget(8).get('https://rdap.arin.net/registry/entities?fn=Example', {'rdap.arin.net'})
        budget = registry._Budget(8)
        budget.deadline = 0
        with self.assertRaises(ValueError):
            budget.get('https://rdap.arin.net/registry/entities?fn=Example', {'rdap.arin.net'})
