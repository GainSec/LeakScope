import json
from unittest.mock import patch
from django.test import TestCase, RequestFactory
from leakscope.phase3 import relationships as store
from leakscope_app.models import EvidenceCorrelation, ServiceEndpoint, ServiceObservation
from leakscope_app.public_signals import public_signals, entra

TENANT = '11111111-2222-4333-8444-555555555555'


class PublicSignalTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.org = store.node('organization', 'Example')
        self.endpoint = ServiceEndpoint.objects.create(key='signal', address='app.example.com', port=443)
        self.proof = ServiceObservation.objects.create(endpoint=self.endpoint, provider='shodan', fingerprint='signal')
        service = store._service(self.endpoint)
        scope = store.evidence(self.org, 'user_scope', {'service': service.value})
        store.relation(self.org, service, 'user_scope', scope)
        self.signal = EvidenceCorrelation.objects.create(endpoint=self.endpoint, kind='tracking.google_tag_manager', value='GTM-EXAMPLE1', observation=self.proof)

    def read(self, **query):
        with patch('requests.get', side_effect=AssertionError('No requests while browsing')):
            response = public_signals(self.factory.get('/signals', query))
        self.assertEqual(response.status_code, 200, response.content)
        return json.loads(response.content)

    def test_single_endpoint_signal_retains_exact_preview_and_visible_proof(self):
        data = self.read(organization_id=str(self.org.pk))
        self.assertEqual(data['total'], 1)
        row = data['results'][0]
        self.assertEqual(row['endpoint_count'], 1)
        self.assertEqual(row['preview_payload'], {'kind': self.signal.kind, 'value': self.signal.value})
        self.assertEqual(row['proof_refs'][0]['observation_id'], str(self.proof.pk))
        self.assertFalse(data['network_contact'])
        self.proof.hidden = True
        self.proof.save(update_fields=['hidden'])
        self.assertEqual(self.read()['results'], [])

    def test_saved_entra_domain_filter_scope_and_pagination(self):
        domain = store.node('domain', 'example.com')
        scope = store.evidence(self.org, 'user_scope', {'domain': domain.value})
        store.relation(self.org, domain, 'user_scope', scope)
        identity = store.evidence(domain, 'public_identity:entra', {'namespace': 'entra.tenant', 'value': TENANT,
            'attributes': {'issuer': 'https://login.microsoftonline.us/' + TENANT + '/v2.0', 'cloud': 'Microsoft US Government cloud'}})
        data = self.read(organization_id=str(self.org.pk), q='example.com', page_size=1)
        self.assertEqual(data['total'], 2)
        self.assertEqual(data['pages'], 2)
        self.assertEqual(data['results'][0]['kind'], 'entra.tenant')
        self.assertEqual(data['results'][0]['preview_payload'], {'evidence_id': str(identity.pk)})
        self.assertEqual(data['results'][0]['domain_count'], 1)
        self.assertEqual(self.read(kind='entra.tenant')['total'], 1)
        second = self.read(q='example.com', page_size=1, page=2)
        self.assertEqual(second['results'][0]['kind'], self.signal.kind)
        unrelated = store.node('organization', 'Unrelated')
        self.assertEqual(self.read(organization_id=str(unrelated.pk))['total'], 0)

    def test_retired_metadata_and_unsupported_signal_kinds_are_omitted(self):
        for kind in ['artifact.sha256', 'tracking.unsupported']:
            EvidenceCorrelation.objects.create(endpoint=self.endpoint, kind=kind, value='a' * 64, observation=self.proof)
        domain = store.node('domain', 'example.com')
        store.evidence(domain, 'public_identity:android', {'namespace': 'android.app', 'value': 'com.example.app'})
        store.evidence(domain, 'public_identity:entra', {'namespace': 'entra.tenant', 'value': 'not-a-tenant'})
        self.assertEqual(self.read()['total'], 1)
        for query in [{'page_size': 101}, {'kind': 'tracking.unsupported'}, {'organization_id': 'bad'}, {'page': 0}]:
            self.assertEqual(public_signals(self.factory.get('/signals', query)).status_code, 400)

    def test_entra_submission_is_explicit_one_request_and_rejects_arbitrary_options(self):
        with patch('leakscope_app.public_signals.queue_enrichment', return_value={'cached': False, 'run': {'id': 'run'}}) as queue:
            response = entra(self.factory.post('/entra', json.dumps({'domain': 'EXAMPLE.COM', 'demo': True}), content_type='application/json'))
            self.assertEqual(response.status_code, 200)
            queue.assert_called_once_with('identity_metadata', 'example.com', max_requests=1, demo=True)
        for payload in [{'domain': 'example.com', 'methods': ['android']}, {'url': 'https://example.com'}, {'domain': 'example.com', 'demo': 'false'}]:
            self.assertEqual(entra(self.factory.post('/entra', json.dumps(payload), content_type='application/json')).status_code, 400)
        self.assertEqual(entra(self.factory.get('/entra')).status_code, 405)
