import json
from unittest.mock import patch
from django.test import TestCase
from leakscope.phase3.providers import search_provider
from leakscope.phase3.state import ingest_observation
from leakscope_app.models import SavedLookup, Run, SearchProgress, ServiceObservation

CONFIG = {'SHODAN_API_KEY': 'shodan-fixture-secret', 'ZOOMEYE_API_KEY': 'zoomeye-fixture-secret', 'NETLAS_API_KEY': 'netlas-fixture-secret'}


def rows(page, size):
    return [{'ip': '192.0.2.%d' % ((page - 1) * size + i + 1), 'port': 443, 'protocol': 'tcp', 'service': 'https'} for i in range(size)]


def observation(last, **extra):
    return {'address': '192.0.2.%d' % last, 'port': 9200, 'scheme': 'http', 'service_type': 'elastic', 'metadata': {'title': 'Node'}, **extra}


class SearchStartTests(TestCase):
    @patch('leakscope.phase3.providers._page')
    def test_search_begins_at_the_requested_page_and_reports_where_it_stopped(self, page):
        page.side_effect = lambda provider, query, config, number, size: (rows(number, size), 180, size)
        result = search_provider('zoomeye', 'app="x"', CONFIG, page_count=2, page_size=50, start_page=3)
        self.assertEqual([call.args[3] for call in page.call_args_list], [3, 4])
        self.assertEqual((result['start_page'], result['next_page'], result['page_size']), (3, 5, 50))
        self.assertFalse(result['more_available'])

    @patch('leakscope.phase3.providers._page')
    def test_results_before_the_start_count_towards_the_reported_total(self, page):
        page.side_effect = lambda provider, query, config, number, size: (rows(number, size), 500, size)
        result = search_provider('zoomeye', 'app="x"', CONFIG, page_count=1, page_size=50, start_page=3)
        self.assertTrue(result['more_available'])
        self.assertEqual(result['next_page'], 4)

    def test_start_page_is_bounded(self):
        for value in (0, -1, 10001, True, '2'):
            with self.assertRaises(ValueError): search_provider('zoomeye', 'x', CONFIG, start_page=value)


class DiscoverStartTests(TestCase):
    def post(self, path, data): return self.client.post(path, json.dumps(data), content_type='application/json')

    def discover(self, **data):
        with patch('leakscope.phase3.jobs.queue_lookup') as queue:
            queue.side_effect = lambda lookup: Run.objects.create(kind='lookup', provider=lookup.provider)
            return self.post('/api/discover', {'keyword': 'acme', 'provider': 'zoomeye', **data})

    def test_default_still_starts_at_the_beginning(self):
        self.assertEqual(self.discover().status_code, 202)
        self.assertEqual(SavedLookup.objects.get().start_page, 1)

    def test_explicit_page(self):
        response = self.discover(start='page', start_value=2, zoomeye_pagesize=100)
        self.assertEqual(response.status_code, 202, response.content)
        self.assertEqual(SavedLookup.objects.get().start_page, 2)
        self.assertEqual(response.json()['start'][0]['first_result'], 101)

    def test_after_an_entry_becomes_the_page_that_holds_the_next_result(self):
        for provider, size, after, page in [('zoomeye', 20, 100, 6), ('zoomeye', 100, 100, 2), ('shodan', None, 100, 2), ('netlas', None, 100, 6), ('zoomeye', 20, 30, 2)]:
            with self.subTest(provider=provider, size=size, after=after):
                SavedLookup.objects.all().delete()
                response = self.discover(provider=provider, start='after', start_value=after, **({'zoomeye_pagesize': size} if size else {}))
                self.assertEqual(response.status_code, 202, response.content)
                self.assertEqual(SavedLookup.objects.get().start_page, page)
        self.assertEqual(response.json()['start'][0]['repeated'], 10)

    def test_invalid_start_is_rejected_and_queues_nothing(self):
        for data in ({'start': 'sideways'}, {'start': 'page', 'start_value': 0}, {'start': 'page', 'start_value': 10001},
                     {'start': 'after', 'start_value': -1}, {'start': 'page'}):
            with self.subTest(data=data):
                self.assertEqual(self.discover(**data).status_code, 400)
        self.assertEqual(SavedLookup.objects.count(), 0)

    def test_demo_always_uses_the_first_page(self):
        self.assertEqual(self.discover(start='page', start_value=4, collection_mode='demo').status_code, 202)
        self.assertEqual(SavedLookup.objects.get().start_page, 1)

    def test_continue_starts_after_the_pages_earlier_runs_fetched(self):
        from leakscope.phase3.jobs import search_progress_key
        preview = self.post('/api/query-preview', {'keyword': 'acme', 'provider': 'zoomeye', 'zoomeye_pagesize': 50, 'start': 'continue'}).json()
        self.assertEqual(preview['start'][0]['page'], 1)
        self.assertFalse(preview['start'][0]['known'])
        SearchProgress.objects.create(key=search_progress_key('zoomeye', preview['queries'][0]['query'], 50), provider='zoomeye', page_size=50, next_page=4, total=900)
        response = self.discover(start='continue', zoomeye_pagesize=50)
        self.assertEqual(response.status_code, 202, response.content)
        self.assertEqual(SavedLookup.objects.get().start_page, 4)
        self.assertEqual(response.json()['start'][0], {'type': 'generic', 'mode': 'continue', 'page': 4, 'page_size': 50, 'first_result': 151, 'repeated': 0, 'known': True, 'total': 900})
        # A different page size is a different sequence of pages.
        SavedLookup.objects.all().delete()
        self.discover(start='continue', zoomeye_pagesize=20)
        self.assertEqual(SavedLookup.objects.get().start_page, 1)

    def test_continue_refuses_a_search_that_has_nothing_further(self):
        from leakscope.phase3.jobs import search_progress_key
        query = self.post('/api/query-preview', {'keyword': 'acme', 'provider': 'zoomeye'}).json()['queries'][0]['query']
        SearchProgress.objects.create(key=search_progress_key('zoomeye', query, 20), provider='zoomeye', page_size=20, next_page=3, total=37, exhausted=True)
        response = self.discover(start='continue')
        self.assertEqual(response.status_code, 400)
        self.assertIn('already fetched every result', response.json()['error'])
        self.assertEqual(SavedLookup.objects.count(), 0)
        self.assertEqual(self.discover(start='beginning').status_code, 202)


class LookupRunTests(TestCase):
    def run_lookup(self, lookup, found, **result):
        from leakscope.phase3.jobs import run_lookup, lookup_snapshot
        with patch('leakscope.phase3.jobs.search_provider') as search:
            search.return_value = {'observations': found, 'pages': 1, 'total': 500, 'more_available': True, 'partial': True,
                                   'start_page': lookup.start_page, 'next_page': lookup.start_page + 1, 'page_size': 20, **result}
            return run_lookup.apply(args=[str(lookup.pk)], kwargs={'snapshot': lookup_snapshot(lookup)}).get(), search

    def test_run_passes_the_start_page_counts_repeats_and_remembers_progress(self):
        from leakscope.phase3.jobs import search_progress_key
        lookup = SavedLookup.objects.create(name='One', provider='zoomeye', query='app="x"', page_size=20, max_results=20, start_page=3)
        result, search = self.run_lookup(lookup, [observation(1), observation(2)])
        self.assertEqual(search.call_args.kwargs['start_page'], 3)
        self.assertEqual((result['new_results'], result['repeat_results'], result['start_page'], result['next_page']), (2, 0, 3, 4))
        result, _ = self.run_lookup(lookup, [observation(2), observation(3)])
        self.assertEqual((result['new_results'], result['repeat_results']), (1, 1))
        progress = SearchProgress.objects.get(key=search_progress_key('zoomeye', result['query'], 20))
        self.assertEqual((progress.next_page, progress.total, progress.exhausted), (4, 500, False))

    def test_progress_never_moves_backwards_and_records_the_end(self):
        lookup = SavedLookup.objects.create(name='One', provider='zoomeye', query='app="x"', page_size=20, max_results=20, start_page=5)
        self.run_lookup(lookup, [observation(1)], more_available=False, partial=False, total=81)
        lookup.start_page = 1; lookup.save()
        self.run_lookup(lookup, [observation(1)])
        progress = SearchProgress.objects.get()
        self.assertEqual((progress.next_page, progress.exhausted, progress.total), (6, True, 81))

    def test_demo_runs_do_not_change_progress(self):
        lookup = SavedLookup.objects.create(name='Demo', provider='zoomeye', query='app="x"', collection_mode='demo')
        self.run_lookup(lookup, [observation(1)])
        self.assertEqual(SearchProgress.objects.count(), 0)


class CaptureCountTests(TestCase):
    def test_each_capture_of_the_same_result_is_counted(self):
        endpoint = ingest_observation('zoomeye', observation(9))
        self.assertFalse(endpoint.repeat_capture)
        for _ in range(2): endpoint = ingest_observation('zoomeye', observation(9))
        self.assertTrue(endpoint.repeat_capture)
        self.assertEqual(ServiceObservation.objects.get().collection_count, 3)
        ingest_observation('shodan', observation(9))
        self.assertEqual(sorted(ServiceObservation.objects.values_list('provider', 'collection_count')), [('shodan', 1), ('zoomeye', 3)])
        detail = self.client.get('/api/operations/endpoints/' + str(endpoint.pk)).json()
        self.assertEqual(sorted(row['collection_count'] for row in detail['observations']), [1, 3])
        self.assertEqual(detail['endpoint']['collection_count'], 4)
        listed = self.client.get('/api/operations/endpoints').json()['results'][0]
        self.assertEqual(listed['collection_count'], 4)
