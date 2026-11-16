"""The Export page can download what current lookups save, not only the older per-service findings."""
import csv
import io
from django.test import TestCase
from leakscope.phase3.state import ingest_observation, set_tags


class ResultsExportTests(TestCase):
    def setUp(self):
        self.a = ingest_observation('shodan', {'address': '192.0.2.1', 'port': 9200, 'scheme': 'http', 'service_type': 'elastic', 'title': '=cmd', 'metadata': {}})
        self.b = ingest_observation('netlas', {'address': 'app.example.com', 'port': 443, 'scheme': 'https', 'service_type': 'grafana', 'title': 'Grafana', 'metadata': {}})
        set_tags('endpoint', str(self.a.pk), ['review', 'keep'])

    def test_csv_lists_saved_results_with_lists_joined_and_formulas_defused(self):
        response = self.client.get('/export/results/?format=csv')
        self.assertEqual((response.status_code, response['Content-Type'], response['X-Total-Count'], response['X-Exported-Count']), (200, 'text/csv; charset=utf-8', '2', '2'))
        self.assertIn('attachment; filename="saved-results.csv"', response['Content-Disposition'])
        rows = {row['address']: row for row in csv.DictReader(io.StringIO(response.content.decode()))}
        self.assertEqual(set(rows), {'192.0.2.1', 'app.example.com'})
        self.assertEqual((rows['192.0.2.1']['port'], rows['192.0.2.1']['title'], sorted(rows['192.0.2.1']['tags'].split('; ')), rows['192.0.2.1']['id']), ('9200', "'=cmd", ['keep', 'review'], str(self.a.pk)))

    def test_json_can_be_narrowed_to_one_technology_and_limited(self):
        data = self.client.get('/export/results/?format=json&technology=grafana').json()
        self.assertEqual([(row['address'], row['port'], row['title']) for row in data], [('app.example.com', 443, 'Grafana')])
        limited = self.client.get('/export/results/?format=json&limit=1')
        self.assertEqual((len(limited.json()), limited['X-Total-Count'], limited['X-Exported-Count']), (1, '2', '1'))
        self.assertEqual(self.client.get('/export/results/?format=xml').status_code, 400)
        self.assertEqual(self.client.get('/export/results/?limit=0').status_code, 400)
