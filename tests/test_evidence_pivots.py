import json
from unittest.mock import patch

from django.test import RequestFactory, TestCase

from leakscope.phase3 import relationships as store
from leakscope_app.evidence_pivots import pivot_queries
from leakscope_app.models import EvidenceCorrelation, ServiceEndpoint, ServiceObservation, QuerySuggestion, AssetEvidence


class EvidencePivotTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.endpoint = ServiceEndpoint.objects.create(key='pivot', address='192.0.2.1', port=443)
        self.observation = ServiceObservation.objects.create(endpoint=self.endpoint, provider='shodan', fingerprint='pivot')

    def post(self, data):
        with patch('requests.get', side_effect=AssertionError('Query preview must not contact a provider')), \
             patch('requests.post', side_effect=AssertionError('Query preview must not contact a provider')):
            return pivot_queries(self.factory.post('/api/intelligence/pivot-queries', json.dumps(data), content_type='application/json'))

    def identity(self, namespace='entra.tenant', value='11111111-2222-4333-8444-555555555555', source='public_identity:entra', extra=None):
        domain = store.node('domain', 'example.com')
        data = {'namespace': namespace, 'value': value, 'attributes': {}}
        data.update(extra or {})
        return store.evidence(domain, source, data)

    def signal(self, kind='tracking.google_tag_manager', value='GTM-EXAMPLE1'):
        return EvidenceCorrelation.objects.create(endpoint=self.endpoint, kind=kind, value=value, observation=self.observation)

    def test_public_identity_produces_three_body_previews_without_writes_or_ownership_scope(self):
        proof = self.identity()
        count = AssetEvidence.objects.count()
        response = self.post({'evidence_id': str(proof.pk)})
        self.assertEqual(response.status_code, 200, response.content)
        value = json.loads(response.content)
        self.assertEqual(value['term'], '11111111-2222-4333-8444-555555555555')
        self.assertEqual(value['kind'], 'entra.tenant')
        self.assertFalse(value['network_contact'])
        self.assertEqual(value['reference'], {'type': 'evidence', 'id': str(proof.pk)})
        self.assertEqual(set(value['queries']), {'shodan', 'zoomeye', 'netlas'})
        for provider, field in [('shodan', 'http.html:'), ('zoomeye', 'http.body='), ('netlas', 'http.body:')]:
            self.assertTrue(value['queries'][provider]['supported'])
            self.assertTrue(value['queries'][provider]['query'].startswith(field))
            self.assertNotIn('hostname', value['queries'][provider]['query'])
        self.assertIn('ownership', value['basis'])
        self.assertIn('unindexed', value['basis'])
        self.assertEqual(QuerySuggestion.objects.count(), 0)
        self.assertEqual(AssetEvidence.objects.count(), count)

    def test_retired_public_metadata_cannot_generate_previews(self):
        for namespace, source in [('ads.seller', 'public_identity:ads'), ('android.app', 'public_identity:android'),
                                  ('webauthn.related_origin', 'public_identity:webauthn')]:
            proof = self.identity(namespace, 'example', source=source)
            self.assertEqual(self.post({'evidence_id': str(proof.pk)}).status_code, 400)

    def test_correlation_requires_exact_visible_signal_and_escapes_quotes(self):
        literal = 'literal" OR country:"US\\tail'
        self.signal(value=literal)
        response = self.post({'kind': 'tracking.google_tag_manager', 'value': literal})
        self.assertEqual(response.status_code, 200, response.content)
        value = json.loads(response.content)
        self.assertEqual(value['term'], literal)
        self.assertEqual(value['reference'], {'type': 'endpoint', 'id': str(self.endpoint.pk)})
        self.assertEqual(value['queries']['shodan']['query'], 'http.html:"literal\\" OR country:\\"US\\\\tail"')
        self.assertEqual(value['queries']['netlas']['query'], 'http.body:"literal\\" OR country:\\"US\\\\tail"')
        self.assertEqual(self.post({'kind': 'tracking.google_tag_manager', 'value': 'GTM-MISSING'}).status_code, 404)

    def test_hidden_public_proof_and_hidden_correlation_cannot_generate_previews(self):
        proof = self.identity(extra={'observation_pk': self.observation.pk.hex})
        self.signal()
        self.observation.hidden = True
        self.observation.save(update_fields=['hidden'])
        for data in [{'evidence_id': str(proof.pk)}, {'kind': 'tracking.google_tag_manager', 'value': 'GTM-EXAMPLE1'}]:
            self.assertEqual(self.post(data).status_code, 404)

    def test_one_visible_same_endpoint_proof_retains_signal_without_revealing_hidden_proof(self):
        signal = self.signal()
        self.observation.hidden = True
        self.observation.save(update_fields=['hidden'])
        alternate = ServiceObservation.objects.create(endpoint=self.endpoint, provider='zoomeye', fingerprint='alternate')
        signal.observations.add(alternate)
        response = self.post({'kind': signal.kind, 'value': signal.value})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotIn(str(self.observation.pk), str(response.content))
        alternate.hidden = True
        alternate.save(update_fields=['hidden'])
        self.assertEqual(self.post({'kind': signal.kind, 'value': signal.value}).status_code, 404)

    def test_arbitrary_stored_records_hashes_and_extra_fields_are_rejected(self):
        for source, namespace in [('user_scope', 'android.app'), ('public_identity:android', 'android.signer'),
                                  ('public_identity:ads', 'unknown.namespace')]:
            proof = self.identity(namespace, source=source)
            self.assertEqual(self.post({'evidence_id': str(proof.pk)}).status_code, 400)
        for kind in ['favicon.sha256', 'certificate.sha256', 'screenshot.dhash64', 'tracking.password', 'declaration.secret']:
            self.signal(kind=kind, value='a' * 64)
            self.assertEqual(self.post({'kind': kind, 'value': 'a' * 64}).status_code, 400)
        proof = self.identity()
        for data in [{}, {'evidence_id': str(proof.pk), 'kind': 'tracking.meta_pixel', 'value': '123456'},
                     {'evidence_id': str(proof.pk), 'scope': {}}, {'kind': 'tracking.meta_pixel'},
                     {'kind': 'tracking.meta_pixel', 'value': '12345', 'url': 'https://example.com'},
                     {'evidence_id': 'invalid'}]:
            self.assertEqual(self.post(data).status_code, 400)

    def test_declared_non_http_origin_is_only_a_body_search_preview(self):
        self.signal(kind='declaration.asyncapi.server', value='mqtts://broker.example.com:8883')
        response = self.post({'kind': 'declaration.asyncapi.server', 'value': 'mqtts://broker.example.com:8883'})
        self.assertEqual(response.status_code, 200, response.content)
        value = json.loads(response.content)
        self.assertIn('mqtts://broker.example.com:8883', value['queries']['shodan']['query'])
        self.assertIn('indexed HTTP bodies', value['basis'])
        self.assertEqual(pivot_queries(self.factory.get('/api/intelligence/pivot-queries')).status_code, 405)
