"""Atlas drilldowns must keep the saved-evidence cohort in Observations."""
import json
from unittest.mock import patch
from django.test import TestCase
from leakscope.phase3.state import ingest_observation
from leakscope.phase3 import relationships as graph
from leakscope_app.models import Run


class AtlasObservationTests(TestCase):
    def item(self, address, provider='shodan', technology='http', **metadata):
        return ingest_observation(provider, {'address': address, 'port': 8443,
            'service_type': technology, 'metadata': metadata})

    def query(self, **params):
        response = self.client.get('/api/operations/endpoints', params)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_metadata_identity_and_product_search_keep_classifier_cohort(self):
        wanted = self.item('192.0.2.1', product='Jenkins')
        self.item('192.0.2.2', product='Redis')
        hidden = self.item('192.0.2.3', product='Jenkins'); hidden.observations.update(hidden=True)
        with patch('requests.get', side_effect=AssertionError('Browse must not collect')):
            result = self.query(technology='jenkins', q='Jenkins', provider='shodan')
        self.assertEqual([row['id'] for row in result['results']], [str(wanted.pk)])

    def test_organization_and_saved_time_context_intersect_existing_records(self):
        wanted = self.item('192.0.2.1', product='Jenkins'); self.item('192.0.2.2', product='Jenkins')
        org = graph.node('organization', 'Scoped fixture'); service = graph._service(wanted)
        proof = graph.evidence(org, 'user_scope', {'kind': 'service', 'value': service.value})
        graph.relation(org, service, 'user_scope', proof)
        self.assertEqual(self.query(organization_id=str(org.pk))['count'], 1)
        self.assertEqual(self.query(organization_id=str(org.pk), since='2099-01-01T00:00:00Z')['count'], 0)

    def test_local_geographic_context_is_not_ignored(self):
        self.item('192.0.2.1', product='Jenkins')
        # No local GeoIP for documentation-only IPs; unknown stays available.
        self.assertEqual(self.query(country='US')['count'], 0)
        self.assertEqual(self.query(country='unknown', continent='unknown')['count'], 1)

    def test_direct_source_alias_in_both_legacy_and_classifier_views(self):
        wanted = self.item('192.0.2.1', provider='fetch:identity', technology='modbus')
        self.item('192.0.2.2', technology='modbus')
        for context in [{}, {'technology': 'modbus'}]:
            self.assertEqual([row['id'] for row in self.query(provider='direct', **context)['results']], [str(wanted.pk)])

    def test_run_and_legacy_type_stay_intersected_with_classifier(self):
        wanted = self.item('192.0.2.1', product='Jenkins'); self.item('192.0.2.2', product='Jenkins')
        run = Run.objects.create(kind='lookup', provider='shodan', state='succeeded',
            result=json.dumps({'endpoint_ids': [str(wanted.pk)], 'stored': 1}))
        result = self.query(run=str(run.pk), type='http', technology='jenkins')
        self.assertEqual([row['id'] for row in result['results']], [str(wanted.pk)])
        self.assertEqual(result['run']['id'], str(run.pk))
        self.assertEqual(self.query(run=str(run.pk), type='redis', technology='jenkins')['count'], 0)

    def test_invalid_scope_fails_instead_of_silently_broadening(self):
        for params in [{'since': 'bad'}, {'organization_id': 'bad'}, {'since': '2099-01-01T00:00:00Z', 'until': '2020-01-01T00:00:00Z'}]:
            self.assertEqual(self.client.get('/api/operations/endpoints', params).status_code, 400)
