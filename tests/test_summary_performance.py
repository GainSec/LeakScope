import gzip
import json
from unittest.mock import patch
from django.test import TestCase
from leakscope_app.models import Search, Gitlab, Elastic, FindingReview, FingerprintLog, Run
from leakscope.services.identity import fingerprint


class SummaryPerformanceTests(TestCase):
    def setUp(self):
        search = Search.objects.create(type='gitlab', keyword='fixture', network='', country='', provider='shodan')
        self.first = Gitlab.objects.create(search=search, provider='shodan', ip='192.0.2.10', port='443', confirmed=True)
        self.second = Gitlab.objects.create(search=search, provider='zoomeye', ip='192.0.2.11', port='8443', for_later=True)
        self.elastic = Elastic.objects.create(search=search, provider='zoomeye', ip='192.0.2.20', port='19200', confirmed=True, for_later=True)
        for row in [self.first, self.second]:
            FingerprintLog.objects.create(type='gitlab', ip=row.ip, port=row.port,
                fingerprint=fingerprint('gitlab', row.ip, row.port), status='hidden')
        FindingReview.objects.create(type='gitlab', object_id=self.first.pk, hidden=False)
        FindingReview.objects.create(type='elastic', object_id=self.elastic.pk, hidden=True)

    def test_all_legacy_counters_and_providers_use_three_queries(self):
        from leakscope_app.services.findings import summary_counts
        with self.assertNumQueries(3):
            totals, by_type, providers = summary_counts()
        self.assertEqual(totals, {'total': 3, 'confirmed': 2, 'for_later': 2, 'hidden': 2})
        self.assertEqual(by_type, {'gitlab': 2, 'elastic': 1})
        self.assertEqual(providers, {'shodan': 1, 'zoomeye': 2})

    def test_blank_provider_remains_unknown(self):
        from leakscope_app.services.findings import summary_counts
        Gitlab.objects.filter(pk=self.first.pk).update(provider='')
        _, _, providers = summary_counts()
        self.assertEqual(providers, {'unknown': 1, 'zoomeye': 2})

    @patch('leakscope.tasks.get_config', return_value={'config': {}})
    def test_summary_contract_and_query_budget(self, config):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get('/api/summary')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['counts']['total'], 3)
        self.assertEqual(data['counts']['hidden'], 2)
        self.assertEqual(data['counts']['confirmed'], 2)
        self.assertEqual(data['counts']['for_later'], 2)
        self.assertEqual({r['provider']: r['count'] for r in data['by_provider']}, {'shodan': 1, 'zoomeye': 2})
        self.assertIn('types', data)
        self.assertIn('observations', data)
        self.assertLessEqual(len(queries), 25, 'Overview query count must not grow with legacy service types')

    @patch('leakscope.tasks.get_config', return_value={'config': {}})
    def test_negotiated_json_compression_preserves_complete_run_details(self, config):
        Run.objects.create(task_id='gzip-fixture', kind='lookup', state='succeeded',
                           result=json.dumps({'evidence': 'fixture-evidence-' * 20000}))
        plain = self.client.get('/api/summary')
        compressed = self.client.get('/api/summary', HTTP_ACCEPT_ENCODING='gzip')
        self.assertEqual(compressed.status_code, 200)
        self.assertEqual(compressed.get('Content-Encoding'), 'gzip')
        self.assertIn('Accept-Encoding', compressed.get('Vary', ''))
        self.assertEqual(json.loads(gzip.decompress(compressed.content)), plain.json())
        self.assertLess(len(compressed.content), len(plain.content) // 4)
