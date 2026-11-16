from unittest.mock import patch
from django.test import TestCase
from leakscope.phase3 import asset_jobs as jobs
from leakscope_app.models import AssetEvidence, AssetRelationship


class FreeRegistryJobTests(TestCase):
    @patch('leakscope.phase3.asset_jobs.free_registry.discover_organization')
    @patch('leakscope.phase3.asset_jobs.discover_organization')
    def test_default_discovery_never_uses_paid_provider_and_has_separate_cache(self, paid, free):
        free.return_value={'organization':'Acme','networks':[],'domains':[],'asns':[],'partial':False,'errors':[],'requests':3,'keyless':True}
        first=jobs.queue_enrichment('organization','Acme')
        self.assertEqual(first['run']['provider'],'rdap')
        paid.assert_not_called()
        free.assert_called_once()
        paid.return_value={'organization':'Acme','domains':[],'networks':[],'asns':[],'partial':False,'errors':[],'requests':2}
        second=jobs.queue_enrichment('organization','Acme',include_netlas=True)
        self.assertFalse(second['cached'])
        paid.assert_called_once()
        free.assert_called_once() # free result cache used independently within opt-in run
        self.assertTrue(second['run']['parameters']['include_netlas'])
        self.assertEqual(second['run']['result']['requests'],2)
        self.assertTrue(second['run']['result']['free_cache_reused'])
        self.assertEqual(second['run']['result']['free_cached_requests'],3)
        self.assertFalse(second['run']['result']['keyless'])

    @patch('leakscope.phase3.asset_jobs.free_registry.registration')
    def test_subdomain_registration_keeps_registry_boundary_and_registrar_distinct(self, collect):
        collect.return_value={'target':'www.example.com','domain':'example.com','registration':{
            'domain':'example.com','registrars':['Registrar Inc'],'registrants':[], 'redacted':True,
            'source_url':'https://rdap.example/domain/example.com'},'domains':[],'partial':False,'errors':[]}
        response=self.client.post('/api/assets/registration',{'target':'www.example.com'},content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(response.json()['run']['state'],'succeeded')
        proof=AssetEvidence.objects.get(source='enrichment:registration')
        self.assertEqual(proof.subject.value,'www.example.com')
        self.assertEqual(proof.data['result']['domain'],'example.com')
        self.assertFalse(AssetRelationship.objects.filter(relation='registrant_organization').exists())
        self.assertTrue(self.client.get('/api/assets/context?target=www.example.com').json()['actions']['registration'])

    @patch('leakscope.phase3.asset_jobs.free_registry.discover_organization')
    @patch('leakscope.phase3.asset_jobs.discover_organization')
    def test_optional_paid_failure_keeps_free_candidates(self,paid,free):
        free.return_value={'organization':'Acme','domains':[], 'networks':[{'cidr':'192.0.2.0/24','source':'rdap','source_url':'https://rdap.arin.net/registry/ip/192.0.2.0'}], 'errors':[],'partial':False}
        paid.side_effect=ValueError('Paid collection unavailable')
        result=jobs.queue_enrichment('organization','Acme',include_netlas=True)['run']['result']
        self.assertTrue(result['partial'])
        self.assertEqual(result['networks'][0]['cidr'],'192.0.2.0/24')
        self.assertTrue(AssetRelationship.objects.filter(relation='registration_candidate').exists())

    @patch('leakscope.phase3.asset_jobs.free_registry.registration')
    def test_missing_registration_is_saved_as_partial_result_without_authoritative_proof(self,collect):
        collect.return_value={'target':'example.com','domain':'example.com','registration':{},'status':'not_found','reason':'No public record','partial':True,'errors':[]}
        result=jobs.queue_enrichment('registration','example.com')['run']['result']
        self.assertEqual(result['status'],'not_found')
        self.assertFalse(AssetEvidence.objects.filter(source='rdap_domain').exists())
        self.assertTrue(AssetEvidence.objects.filter(source='enrichment:registration').exists())

    def test_paid_opt_in_requires_boolean(self):
        with self.assertRaises(ValueError): jobs.queue_enrichment('organization','Acme',include_netlas='yes')
