import json
from unittest.mock import patch

from django.test import TestCase
from leakscope.phase3.jobs import run_lookup
from leakscope_app.models import Run, SavedLookup, ServiceEndpoint


class KeywordDiscoveryTests(TestCase):
    def post(self, path, **body):
        return self.client.post(path, json.dumps(body), content_type='application/json')

    def test_keyword_only_preview_is_one_generic_query_for_every_provider(self):
        for provider in ('shodan', 'zoomeye', 'netlas'):
            with self.subTest(provider=provider):
                response = self.post('/api/query-preview', provider=provider, types=[], keyword='acme')
                self.assertEqual(response.status_code, 200, response.content)
                data = response.json()
                self.assertEqual(len(data['queries']), 1)
                query = data['queries'][0]
                self.assertEqual(query['query'], '"acme"')
                self.assertEqual(query['base_type'], '')
                self.assertEqual(query['type'], 'generic')
                self.assertTrue(query['supported'])
                self.assertEqual(data['estimate']['pages'], 1)
                self.assertEqual(data['collection']['mode'], 'passive')
                self.assertFalse(data['collection']['target_requests'])
        self.assertFalse(SavedLookup.objects.exists())

    def test_generic_omitted_types_scope_and_quoting_use_existing_provider_semantics(self):
        from leakscope.intelligence.queries import _quote
        from leakscope.phase3.jobs import scoped_query
        keyword = 'Acme "Labs" (test) AND port:22'
        scope = {'network': '192.0.2.0/24', 'country': 'US', 'exclude': 'ignore'}
        for provider in ('shodan', 'zoomeye', 'netlas'):
            with self.subTest(provider=provider):
                response = self.post('/api/query-preview', provider=provider, keyword=keyword, query_mode='ports', **scope)
                self.assertEqual(response.status_code, 200, response.content)
                query = response.json()['queries'][0]
                self.assertEqual(query['base_query'], _quote(keyword, provider))
                self.assertEqual(query['query'], scoped_query(provider, _quote(keyword, provider), scope))
                self.assertNotIn('product:', query['query'])
                self.assertIn('\\"Labs\\"', query['query'])

    @patch('leakscope.phase3.jobs.queue_lookup')
    def test_empty_keyword_or_invalid_types_never_create_work(self, queue):
        for values in ({}, {'keyword': '   '}, {'keyword': '', 'network': '192.0.2.1'},
                       {'keyword': 'acme', 'types': ['not-a-service']},
                       {'keyword': 'acme', 'types': {'bad': 'shape'}},
                       {'keyword': 'acme', 'types': ['generic']}):
            with self.subTest(values=values):
                response = self.post('/api/discover', **values)
                self.assertEqual(response.status_code, 400, response.content)
        self.assertFalse(SavedLookup.objects.exists())
        queue.assert_not_called()

    @patch('leakscope.phase3.jobs.queue_lookup')
    def test_generic_lookup_is_passive_with_unassigned_service_and_exact_preview_query(self, queue):
        queue.return_value = Run.objects.create(kind='lookup')
        response = self.post('/api/discover', provider='zoomeye', keyword=' acme ', country='US')
        self.assertEqual(response.status_code, 202, response.content)
        lookup = SavedLookup.objects.get()
        self.assertEqual(lookup.query, '"acme"')
        self.assertEqual(lookup.scope['country'], 'US')
        self.assertEqual(lookup.service_type, '')
        self.assertEqual(lookup.collection_mode, 'passive')
        self.assertFalse(lookup.auto_fetch)
        self.assertEqual(lookup.actions, [])
        self.assertIn('Keyword', lookup.name)
        queue.assert_called_once_with(lookup)
        self.assertEqual(response.json()['results_url'], '/assets/')

    @patch('leakscope.phase3.jobs.queue_lookup')
    def test_demo_keyword_search_is_one_bounded_lookup(self, queue):
        queue.return_value = Run.objects.create(kind='lookup')
        response = self.post('/api/discover', provider='netlas', keyword='acme', collection_mode='demo', page_count=20)
        self.assertEqual(response.status_code, 202, response.content)
        lookup = SavedLookup.objects.get()
        self.assertEqual((lookup.page_count, lookup.page_size, lookup.max_results), (1, 1, 1))
        self.assertFalse(lookup.auto_fetch)
        self.assertEqual(lookup.collection_mode, 'demo')
        self.assertEqual(len(response.json()['runs']), 1)
        preview = self.post('/api/query-preview', keyword='acme', collection_mode='demo', page_count=20).json()
        self.assertEqual(preview['estimate']['pages'], 1)
        self.assertTrue(preview['demo'])

    @patch('leakscope.phase3.jobs.queue_capture')
    @patch('leakscope.phase3.jobs.search_provider')
    @patch('leakscope.phase3.jobs.queue_lookup')
    def test_generic_run_keeps_real_mixed_service_types_without_contacting_targets(self, queue, search, capture):
        queue.return_value = Run.objects.create(kind='lookup')
        response = self.post('/api/discover', keyword='acme')
        self.assertEqual(response.status_code, 202, response.content)
        search.return_value = {'observations': [
            {'address': '192.0.2.41', 'port': 44001, 'transport': 'tcp', 'service_type': 'modbus'},
            {'address': '192.0.2.42', 'port': 44002, 'transport': 'tcp', 'scheme': 'http', 'service_type': 'http'},
        ]}
        result = run_lookup.apply(args=[str(SavedLookup.objects.get().pk)]).get()
        self.assertEqual(result['stored'], 2)
        self.assertEqual(set(ServiceEndpoint.objects.values_list('service_type', flat=True)), {'modbus', 'http'})
        self.assertEqual(search.call_args.args[1], '"acme"')
        capture.assert_not_called()
