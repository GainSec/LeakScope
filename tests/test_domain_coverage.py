import json
from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase, RequestFactory
from django.utils import timezone
from leakscope.phase3 import relationships as store
from leakscope_app.domain_coverage import domain_coverage


class DomainCoverageTests(TestCase):
    def test_domain_cap_preserves_whole_filtered_scope_evidence_denominator(self):
        for name in ['one.com', 'two.com']:
            store.evidence(store.node('domain', name), 'dns', {'type': 'A'})
        with patch('leakscope_app.domain_coverage.DOMAIN_LIMIT', 1):
            data = json.loads(domain_coverage(RequestFactory().get('/coverage')).content)
        self.assertEqual(data['coverage']['domains_total'], 2)
        self.assertEqual(data['coverage']['scope_evidence'], 2)
        self.assertEqual(data['coverage']['sampled_evidence'], 1)
        self.assertTrue(data['coverage']['limited'])

    def test_scoped_dns_only_matrix_is_read_only_and_retains_expiry(self):
        org = store.node('organization', 'Example')
        domain = store.node('domain', 'example.com')
        proof = store.evidence(org, 'user_scope', {'domain': domain.value})
        store.relation(org, domain, 'user_scope', proof)
        store.evidence(domain, 'dns', {'type': 'TXT', 'value': 'private-text-not-in-matrix'}, expires_at=timezone.now() - timedelta(seconds=1))
        store.evidence(domain, 'dns', {'type': 'A', 'value': '192.0.2.1'}, expires_at=timezone.now() + timedelta(seconds=60))
        store.evidence(domain, 'rdap_domain', {'type': 'RDAP'})
        store.evidence(store.node('domain', 'outside.com'), 'dns', {'type': 'TXT'})
        with patch('requests.get', side_effect=AssertionError('Read only')):
            response = domain_coverage(RequestFactory().get('/coverage', {'organization_id': str(org.pk)}))
        self.assertEqual(response.status_code, 200, response.content)
        data = json.loads(response.content)
        self.assertEqual(len(data['domains']), 1)
        row = data['domains'][0]
        self.assertEqual(row['records'], {'A': 1, 'TXT': 1})
        self.assertEqual(row['freshness'], {'current': 1, 'expired': 1, 'unknown': 0})
        self.assertEqual(row['reference'], {'type': 'asset', 'id': str(domain.pk)})
        self.assertNotIn('private-text', str(response.content))
        self.assertFalse(data['network_contact'])

    def test_hidden_proof_and_domain_evidence_caps_are_transparent(self):
        from leakscope.phase3.state import ingest_observation
        endpoint = ingest_observation('shodan', {'address': '192.0.2.4', 'port': 443})
        observation = endpoint.observations.first()
        domain = store.node('domain', 'visible.com')
        store.evidence(domain, 'dns', {'type': 'TXT', 'observation_pk': observation.pk.hex})
        observation.hidden = True
        observation.save(update_fields=['hidden'])
        store.evidence(domain, 'dns', {'type': 'A'})
        store.evidence(domain, 'dns', {'type': 'AAAA'})
        with patch('leakscope_app.domain_coverage.EVIDENCE_LIMIT', 1):
            response = domain_coverage(RequestFactory().get('/coverage'))
        data = json.loads(response.content)
        self.assertTrue(data['coverage']['limited'])
        self.assertEqual(data['coverage']['scope_evidence'], 2)
        self.assertEqual(data['coverage']['sampled_evidence'], 1)
        self.assertNotIn('TXT', data['domains'][0]['records'])
