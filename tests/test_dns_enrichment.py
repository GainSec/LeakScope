from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import dns.exception
import dns.name
import dns.resolver
import dns.rrset
from django.test import SimpleTestCase

from leakscope.phase3.dns_enrichment import collect_dns, extract_names, resolve_names
from leakscope.phase3.service_rules import detect_services


def answer(name, kind, *values, ttl=300):
    rrset = dns.rrset.from_text(name, ttl, 'IN', kind, *values)
    return SimpleNamespace(rrset=rrset, __iter__=lambda self: iter(rrset))


class ResolverFixture:
    def __init__(self, records=None):
        self.records = records or {}
        self.calls = []

    def resolve(self, name, kind, **kwargs):
        self.calls.append((str(name).rstrip('.'), kind, kwargs))
        item = self.records.get((str(name).rstrip('.'), kind))
        if isinstance(item, Exception):
            raise item
        if item is None:
            raise dns.resolver.NoAnswer()
        return item


class DNSCollectionTests(SimpleTestCase):
    def collect(self, records=None, target='www.example.co.uk', **kwargs):
        resolver = ResolverFixture(records)
        with patch('leakscope.phase3.dns_enrichment.dns.resolver.Resolver', return_value=resolver):
            result = collect_dns(target, **kwargs)
        return result, resolver

    def test_types_are_separate_and_mail_policy_uses_registrable_domain(self):
        result, resolver = self.collect({
            ('www.example.co.uk', 'A'): answer('www.example.co.uk.', 'A', '192.0.2.1'),
            ('example.co.uk', 'MX'): answer('example.co.uk.', 'MX', '10 aspmx.l.google.com.'),
        })
        calls = {(name, kind) for name, kind, _ in resolver.calls}
        self.assertNotIn('ANY', {kind for _, kind in calls})
        self.assertTrue({'A', 'AAAA', 'CNAME', 'MX', 'NS', 'TXT', 'SOA', 'CAA', 'HTTPS', 'SVCB'} <= {kind for _, kind in calls})
        self.assertIn(('_dmarc.example.co.uk', 'TXT'), calls)
        self.assertIn(('selector1._domainkey.example.co.uk', 'TXT'), calls)
        self.assertIn(('_sip._tcp.example.co.uk', 'SRV'), calls)
        mx = next(record for record in result['records'] if record['type'] == 'MX')
        self.assertEqual(mx['name'], 'example.co.uk')
        self.assertEqual(mx['exchange'], 'aspmx.l.google.com')
        self.assertEqual(mx['preference'], 10)
        self.assertFalse(result['partial'])
        self.assertLessEqual(result['queries'], 32)

    def test_records_preserve_ttl_and_time_and_structured_values(self):
        result, _ = self.collect({
            ('example.com', 'TXT'): answer('example.com.', 'TXT', '"v=spf1 " "include:_spf.google.com -all"'),
            ('example.com', 'CAA'): answer('example.com.', 'CAA', '0 issue "letsencrypt.org"'),
            ('example.com', 'SOA'): answer('example.com.', 'SOA', 'ns1.example.com. admin.example.com. 1 2 3 4 5'),
            ('example.com', 'HTTPS'): answer('example.com.', 'HTTPS', '1 . alpn="h2"'),
        }, target='example.com')
        records = {record['type']: record for record in result['records']}
        self.assertEqual(records['TXT']['value'], 'v=spf1 include:_spf.google.com -all')
        self.assertEqual(records['CAA']['tag'], 'issue')
        self.assertEqual(records['CAA']['issuer'], 'letsencrypt.org')
        self.assertEqual(records['SOA']['mname'], 'ns1.example.com')
        self.assertEqual(records['HTTPS']['priority'], 1)
        record = records['TXT']
        self.assertEqual(record['ttl'], 300)
        self.assertEqual((datetime.fromisoformat(record['expires_at']) - datetime.fromisoformat(record['observed_at'])).total_seconds(), 300)

    def test_budget_exhaustion_is_explicit_and_no_recursive_cname_queries(self):
        result, resolver = self.collect({
            ('example.com', 'CNAME'): answer('example.com.', 'CNAME', 'elsewhere.example.net.'),
        }, target='example.com', max_queries=3)
        self.assertEqual(result['queries'], 3)
        self.assertTrue(result['partial'])
        self.assertIn('query_budget_exhausted', [item['error'] for item in result['errors']])
        self.assertFalse(any(name == 'elsewhere.example.net' for name, _, _ in resolver.calls))

    def test_timeout_keeps_successful_records_and_sets_partial(self):
        result, resolver = self.collect({
            ('example.com', 'A'): answer('example.com.', 'A', '192.0.2.1'),
            ('example.com', 'AAAA'): dns.exception.Timeout(),
        }, target='example.com')
        self.assertTrue(result['records'])
        self.assertTrue(result['partial'])
        self.assertIn('timeout', [item['error'] for item in result['errors']])
        self.assertTrue(all(0 < call[2]['lifetime'] <= 12 for call in resolver.calls))

    def test_invalid_and_wildcard_domains_never_reach_dns(self):
        for target in ('*.example.com', 'https://example.com', '192.0.2.1', ''):
            with self.subTest(target=target):
                with patch('leakscope.phase3.dns_enrichment.dns.resolver.Resolver') as resolver:
                    with self.assertRaises(ValueError):
                        collect_dns(target)
                    resolver.assert_not_called()

    def test_record_cap_is_reported(self):
        values = ['192.0.2.' + str(index) for index in range(1, 100)]
        result, _ = self.collect({('example.com', 'A'): answer('example.com.', 'A', *values)}, target='example.com')
        self.assertLessEqual(len(result['records']), 64)
        self.assertTrue(result['partial'])
        self.assertIn('record_limit', [item['error'] for item in result['errors']])

    def test_total_time_cap_stops_subsequent_queries(self):
        with patch('leakscope.phase3.dns_enrichment.time.monotonic', side_effect=[100, 101, 113]):
            result, resolver = self.collect(target='example.com')
        self.assertEqual(len(resolver.calls), 1)
        self.assertTrue(result['partial'])
        self.assertIn('time_budget_exhausted', [item['error'] for item in result['errors']])

    def test_invalid_limits_and_public_suffix_do_not_query(self):
        for kwargs in ({'max_queries': 65}, {'max_queries': -1}, {'timeout': float('nan')}, {'timeout': 0}):
            with self.assertRaises(ValueError):
                self.collect(**kwargs)
        with patch('leakscope.phase3.dns_enrichment.dns.resolver.Resolver') as resolver:
            with self.assertRaises(ValueError):
                collect_dns('co.uk')
            resolver.assert_not_called()


class ResolveNameTests(SimpleTestCase):
    def resolve(self, records, target='192.0.2.1', **kwargs):
        resolver = ResolverFixture(records)
        with patch('leakscope.phase3.dns_enrichment.dns.resolver.Resolver', return_value=resolver):
            result = resolve_names(target, **kwargs)
        return result, resolver

    def test_ptr_is_retained_and_forward_confirmation_is_explicit(self):
        result, _ = self.resolve({
            ('1.2.0.192.in-addr.arpa', 'PTR'): answer('1.2.0.192.in-addr.arpa.', 'PTR', 'Confirmed.Example.com.', 'unconfirmed.example.com.'),
            ('confirmed.example.com', 'A'): answer('confirmed.example.com.', 'A', '192.0.2.1'),
            ('unconfirmed.example.com', 'A'): answer('unconfirmed.example.com.', 'A', '192.0.2.2'),
        })
        names = {item['name']: item for item in result['names']}
        self.assertTrue(names['confirmed.example.com']['forward_confirmed'])
        self.assertFalse(names['unconfirmed.example.com']['forward_confirmed'])
        self.assertEqual(names['confirmed.example.com']['source'], 'dns:ptr')
        self.assertEqual(names['unconfirmed.example.com']['confirmation_status'], 'mismatch')
        self.assertFalse(result['partial'])

    def test_ipv6_confirmation_and_budget_limited_unknown(self):
        import dns.reversename
        ip = '2001:db8::1'
        reverse = dns.reversename.from_address(ip).to_text().rstrip('.')
        records = {(reverse, 'PTR'): answer(reverse + '.', 'PTR', 'v6.example.com.'),
                   ('v6.example.com', 'AAAA'): answer('v6.example.com.', 'AAAA', ip)}
        result, _ = self.resolve(records, target=ip)
        self.assertTrue(result['names'][0]['forward_confirmed'])
        result, resolver = self.resolve(records, target=ip, max_queries=1)
        self.assertIsNone(result['names'][0]['forward_confirmed'])
        self.assertEqual(result['names'][0]['confirmation_status'], 'unavailable')
        self.assertTrue(result['partial'])
        self.assertEqual(len(resolver.calls), 1)

    def test_no_ptr_is_an_empty_success_and_invalid_ip_is_rejected(self):
        result, _ = self.resolve({})
        self.assertEqual(result['names'], [])
        self.assertFalse(result['partial'])
        with self.assertRaises(ValueError):
            resolve_names('example.com')

    def test_truncated_forward_answer_does_not_claim_mismatch(self):
        result, _ = self.resolve({
            ('1.2.0.192.in-addr.arpa', 'PTR'): answer('1.2.0.192.in-addr.arpa.', 'PTR', 'large.example.com.'),
            ('large.example.com', 'A'): answer('large.example.com.', 'A', *['192.0.2.' + str(i) for i in range(2, 100)]),
        })
        self.assertTrue(result['partial'])
        self.assertIsNone(result['names'][0]['forward_confirmed'])


class PassiveNamesTests(SimpleTestCase):
    def test_provider_hostname_and_certificate_names_remain_distinct(self):
        metadata = {'_provider': 'shodan', 'hostnames': ['WWW.Example.com.', '192.0.2.1'],
                    'ssl': {'cert': {'subject': {'CN': 'www.example.com'},
                                     'extensions': {'subjectAltName': 'DNS:*.example.com, DNS:api.example.com, IP Address:192.0.2.1'}}}}
        with patch('dns.resolver.Resolver') as resolver:
            names = extract_names(metadata)
        resolver.assert_not_called()
        found = {(item['name'], item['source']) for item in names}
        self.assertIn(('www.example.com', 'shodan:hostname'), found)
        self.assertIn(('www.example.com', 'shodan:certificate'), found)
        self.assertIn(('*.example.com', 'shodan:certificate'), found)
        self.assertIn(('api.example.com', 'shodan:certificate'), found)
        self.assertFalse(any(item['name'] == '192.0.2.1' for item in names))

    def test_zoomeye_netlas_names_and_indexed_dns_have_separate_sources(self):
        for provider, metadata, expected in (
            ('zoomeye', {'domain': ['www.example.com'], 'ssl': {'subject': {'CN': '*.example.com'}}}, ('www.example.com', 'zoomeye:hostname')),
            ('netlas', {'data': {'host': 'www.example.com', 'certificate': {'names': ['*.example.com']},
                                  'dns': {'a': ['192.0.2.1'], 'ptr': ['mail.example.com']}}}, ('mail.example.com', 'netlas:indexed_dns')),
        ):
            names = extract_names(metadata, provider)
            self.assertIn(expected, {(item['name'], item['source']) for item in names})
            self.assertIn(('*.example.com', provider + ':certificate'), {(item['name'], item['source']) for item in names})

    def test_metadata_is_bounded_and_arbitrary_banner_text_is_ignored(self):
        metadata = {'hostnames': [f'host{i}.example.com' for i in range(1000)],
                    'banner': 'DNS:admin.example.net', 'http': {'html': 'DNS:admin.example.org'}}
        names = extract_names(metadata, 'shodan')
        self.assertLessEqual(len(names), 256)
        self.assertFalse(any('admin.' in item['name'] for item in names))
        self.assertEqual(extract_names(None), [])

    def test_structured_netlas_and_shodan_extension_lists(self):
        netlas = {'certificate': {'extensions': {'subject_alt_name': {'dns_names': ['*.example.com']}}}}
        shodan = {'ssl': {'cert': {'extensions': [{'name': 'subjectAltName', 'data': 'DNS:mail.example.com'}]}}}
        self.assertIn('*.example.com', [item['name'] for item in extract_names(netlas, 'netlas')])
        self.assertIn('mail.example.com', [item['name'] for item in extract_names(shodan, 'shodan')])

    def test_deep_name_arrays_have_a_depth_bound(self):
        names = ['deep.example.com']
        for _ in range(20):
            names = [names]
        self.assertEqual(extract_names({'hostnames': names}, 'shodan'), [])


class ServiceSignalTests(SimpleTestCase):
    def test_service_signals_have_typed_relations_and_supporting_raw_records(self):
        records = [
            {'name': 'example.com', 'type': 'MX', 'value': '10 aspmx.l.google.com.', 'exchange': 'aspmx.l.google.com'},
            {'name': 'www.example.com', 'type': 'CNAME', 'value': 'x.cloudfront.net.', 'target': 'x.cloudfront.net'},
            {'name': 'example.com', 'type': 'NS', 'value': 'alice.ns.cloudflare.com.', 'target': 'alice.ns.cloudflare.com'},
            {'name': 'example.com', 'type': 'TXT', 'value': 'v=spf1 include:_spf.google.com -all'},
            {'name': 'example.com', 'type': 'TXT', 'value': 'google-site-verification=old-marker'},
            {'name': 'example.com', 'type': 'CAA', 'value': '0 issue "letsencrypt.org"', 'tag': 'issue', 'issuer': 'letsencrypt.org'},
        ]
        services = detect_services(records)
        self.assertTrue(services)
        self.assertTrue(all(item['evidence'] for item in services))
        self.assertTrue(all(item['relation'] != 'owns' for item in services))
        self.assertTrue(any(item['relation'] == 'uses_email' for item in services))
        marker = next(item for item in services if item['relation'] == 'verification_marker')
        self.assertLessEqual(marker['confidence'], 40)
        caa = next(item for item in services if item['relation'] == 'authorizes_issuer')
        self.assertEqual(caa['category'], 'certificate_authority')
        self.assertNotEqual(caa['category'], 'hosting')

    def test_suffix_spoofs_and_unrelated_txt_are_not_services(self):
        records = [
            {'name': 'example.com', 'type': 'CNAME', 'value': 'cloudfront.net.attacker.example'},
            {'name': 'example.com', 'type': 'TXT', 'value': 'see https://google.com for info'},
            {'name': 'example.com', 'type': 'TXT', 'value': 'v=spf1 include:_spf.google.com.attacker.example -all'},
        ]
        self.assertEqual(detect_services(records), [])

    def test_negative_spf_include_is_not_sender_authorization(self):
        self.assertEqual(detect_services([{'type': 'TXT', 'value': 'v=spf1 -include:_spf.google.com -all'}]), [])

    def test_route53_nameserver_numbered_suffix_is_recognized(self):
        services = detect_services([{'type': 'NS', 'value': 'ns-123.awsdns-45.org.'}])
        self.assertEqual(services[0]['provider'], 'Amazon Route 53')
        self.assertEqual(services[0]['relation'], 'uses_dns')
class RetainedCertificateNamesTests(SimpleTestCase):
    def test_retained_certificate_domains_keep_source_and_wildcard(self):
        from leakscope.phase3.dns_enrichment import extract_names
        values=extract_names({'certificate':{'domains':['api.example.com','*.example.com']}},'certstream')
        self.assertEqual({item['name'] for item in values},{'api.example.com','*.example.com'})
        self.assertTrue(all(item['source']=='certstream:certificate' for item in values))
