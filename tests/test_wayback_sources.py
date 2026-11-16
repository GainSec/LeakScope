import json
import time
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from django.test import SimpleTestCase, TestCase

from leakscope.phase3 import domain_expansion as engine
from leakscope.phase3 import historical_sources

FIELDS = ['original', 'timestamp', 'statuscode', 'mimetype', 'digest']


def response(body, status=200):
    result = Mock(status_code=status, headers={})
    result.iter_content.return_value = [json.dumps(body).encode()]
    return result


def capture(url='https://api.example.com:9443/old?token=discarded#section', timestamp='20200102030405'):
    return [url, timestamp, '200', 'text/html', 'A' * 32]


class WaybackSourceTests(SimpleTestCase):
    def setUp(self):
        self.used = 0
        self.maximum = 20
        self.context = {'deadline': time.monotonic() + 30, 'seed_domains': ['example.com']}

    def spend(self, bucket, units=1):
        self.assertEqual(bucket, 'domain_api')
        if self.used + units > self.maximum:
            return False
        self.used += units
        return True

    def collect(self, **context):
        return historical_sources.collect(
            'wayback', {'kind': 'domain', 'value': 'example.com', 'depth': 0},
            config={}, spend=self.spend, context={**self.context, **context},
        )

    def test_optional_source_reserves_shared_budget_and_supports_domain_seeds(self):
        from leakscope.phase3.expansion_worker import supports
        self.assertNotIn('wayback', engine.normalize_spec({'domains': ['example.com']})['sources'])
        spec = engine.normalize_spec({'domains': ['example.com'], 'sources': ['wayback'], 'demo': True})
        self.assertEqual(spec['sources'], ['wayback'])
        self.assertEqual(spec['budgets']['domain_api'], 1)
        self.assertTrue(supports('wayback', {'kind': 'domain'}))
        self.assertFalse(supports('wayback', {'kind': 'company'}))

    @patch('leakscope.phase3.passive_sources.requests.get')
    def test_demo_is_one_fixed_origin_index_lookup_with_historical_proof(self, get):
        get.return_value = response([FIELDS, capture()])
        result = self.collect(demo=True)
        self.assertEqual(self.used, 1)
        self.assertEqual(get.call_count, 1)
        url = urlsplit(get.call_args.args[0])
        self.assertEqual((url.scheme, url.netloc, url.path), ('https', 'web.archive.org', '/cdx/search/cdx'))
        self.assertEqual(parse_qs(url.query)['limit'], ['1'])
        self.assertEqual(parse_qs(url.query)['matchType'], ['domain'])
        self.assertFalse(get.call_args.kwargs['allow_redirects'])
        proof = result['candidates'][0]['evidence'][0]
        self.assertEqual(proof['archived_url'], 'https://api.example.com:9443/old')
        self.assertEqual(proof['observed_at'], '2020-01-02T03:04:05+00:00')
        self.assertTrue(proof['historical'])
        self.assertNotEqual(proof['observed_at'], proof['collected_at'])
        self.assertNotIn('discarded', json.dumps(result))
        self.assertNotIn('continuation', result)

    @patch('leakscope.phase3.passive_sources.requests.get')
    def test_bounded_page_continuation_does_not_repeat_page_metadata(self, get):
        get.side_effect = [response(2), response([FIELDS, capture()]), response([FIELDS, capture('https://last.example.com/')])]
        first = self.collect()
        self.assertEqual(first['continuation'], {'stage': 'page', 'page': 1, 'pages': 2})
        self.assertEqual(self.used, 2)
        last = self.collect(cursor=first['continuation'])
        self.assertEqual(self.used, 3)
        self.assertNotIn('continuation', last)
        self.assertEqual(parse_qs(urlsplit(get.call_args.args[0]).query)['page'], ['1'])
        self.assertNotIn('showNumPages', get.call_args.args[0])
        self.assertTrue(all(urlsplit(call.args[0]).netloc == 'web.archive.org' for call in get.call_args_list))

    @patch('leakscope.phase3.passive_sources.requests.get')
    def test_budget_pause_retains_the_next_page(self, get):
        get.side_effect = [response(2), response([FIELDS, capture()])]
        self.maximum = 1
        first = self.collect()
        self.assertEqual(first['blocked'], 'budget')
        self.assertEqual(first['continuation']['page'], 0)
        self.assertEqual(get.call_count, 1)
        self.maximum = 2
        resumed = self.collect(cursor=first['continuation'])
        self.assertEqual(get.call_count, 2)
        self.assertEqual(resumed['continuation']['page'], 1)

    @patch('leakscope.phase3.passive_sources.requests.get')
    def test_foreign_urls_credentials_invalid_ports_and_malformed_rows_are_omitted(self, get):
        get.return_value = response([FIELDS, capture('https://example.com.evil.net/'),
            capture('https://user:password@api.example.com/'), capture('ftp://api.example.com/'),
            capture('https://api.example.com:99999/'), ['malformed'], capture(timestamp='bad')])
        result = self.collect(demo=True)
        self.assertEqual([row['domain'] for row in result['candidates']], ['api.example.com'])
        self.assertIsNone(result['candidates'][0]['evidence'][0]['observed_at'])
        self.assertNotIn('password', json.dumps(result))
        self.assertTrue(result['notes'])

    @patch('leakscope.phase3.passive_sources.requests.get')
    def test_provider_failure_does_not_become_an_empty_success_or_auto_retry(self, get):
        for status in [302, 404, 429, 503]:
            with self.subTest(status=status):
                get.reset_mock()
                get.return_value = response({'error': 'private response body'}, status=status)
                result = self.collect(demo=True)
                self.assertTrue(result['failed'])
                self.assertFalse(result['cacheable'])
                self.assertEqual(get.call_count, 1)
                self.assertNotIn('private response body', json.dumps(result))

    @patch('leakscope.phase3.passive_sources.requests.get')
    def test_empty_index_is_explicitly_limited_to_archive_coverage(self, get):
        get.return_value = response([])
        result = self.collect(demo=True)
        self.assertEqual(result['candidates'], [])
        self.assertTrue(result['cacheable'])
        self.assertNotIn('failed', result)
        self.assertTrue(any('absence' in note for note in result['notes']))

    @patch('leakscope.phase3.passive_sources.requests.get')
    def test_malformed_headers_and_cursor_are_failures_before_any_unsafe_request(self, get):
        get.return_value = response([['original', 'timestamp'], ['https://api.example.com/', '20200102030405']])
        result = self.collect(demo=True)
        self.assertTrue(result['failed'])
        get.reset_mock()
        result = self.collect(cursor={'page': 0, 'pages': 2, 'stage': 'page', 'url': 'https://evil.example'})
        self.assertTrue(result['failed'])
        get.assert_not_called()

    @patch('leakscope.phase3.passive_sources.requests.get')
    def test_expired_deadline_does_not_spend_budget(self, get):
        result = self.collect(demo=True, deadline=time.monotonic() - 1)
        self.assertEqual(result['blocked'], 'deadline')
        self.assertEqual(self.used, 0)
        get.assert_not_called()


class WaybackEngineTests(TestCase):
    def start(self, **data):
        with patch('leakscope.phase3.domain_expansion.run_expansion.apply_async'):
            return engine.start(data)

    @patch('leakscope.phase3.passive_sources.requests.get')
    def test_expansion_persists_archival_provenance_and_reuses_root_cache(self, get):
        get.return_value = response([FIELDS, capture()])
        first = self.start(domains=['seed.example.com'], sources=['wayback'], demo=True)
        engine.run_expansion(str(first.pk))
        first.refresh_from_db()
        self.assertEqual(first.state, 'succeeded')
        proof = first.candidates.get(domain='api.example.com').evidence[0]
        self.assertTrue(proof['historical'])
        self.assertEqual(proof['archive'], 'Wayback Machine')
        self.assertEqual(proof['archived_url'], 'https://api.example.com:9443/old')
        self.assertEqual(proof['observed_at'], '2020-01-02T03:04:05+00:00')
        self.assertTrue(proof['collected_at'])
        second = self.start(domains=['www.example.com'], sources=['wayback'], demo=True)
        engine.run_expansion(str(second.pk))
        second.refresh_from_db()
        self.assertEqual(get.call_count, 1)
        self.assertEqual(second.usage['domain_api'], 0)

    def test_archival_proof_fields_are_retained_and_query_credentials_are_removed(self):
        safe = engine._safe_evidence([{
            'source': 'commoncrawl', 'url': 'https://index.commoncrawl.org/CC-MAIN-2026-43-index',
            'historical': True, 'collected_at': '2026-11-10T12:00:00+00:00',
            'archived_url': 'https://api.example.com:9443/old?token=secret#part',
            'archive': 'CC-MAIN-2026-43', 'mime': 'text/html', 'status': '200',
        }, {'source': 'wayback', 'archived_url': 'https://user:password@api.example.com/'}])
        self.assertEqual(safe[0]['archived_url'], 'https://api.example.com:9443/old')
        self.assertTrue(safe[0]['historical'])
        self.assertEqual(safe[0]['collected_at'], '2026-11-10T12:00:00+00:00')
        self.assertEqual(safe[0]['archive'], 'CC-MAIN-2026-43')
        self.assertNotIn('archived_url', safe[1])
        self.assertNotIn('secret', json.dumps(safe))
