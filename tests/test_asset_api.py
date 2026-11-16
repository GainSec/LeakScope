import json
from unittest.mock import patch
from django.test import TestCase


class AssetApiTests(TestCase):
    def test_graph_candidate_layer_boolean_is_explicit_and_default_compatible(self):
        with patch('leakscope_app.relationships.store.graph', return_value={'nodes': [], 'edges': []}) as graph:
            for query, expected in [({}, True), ({'include_candidates': 'true'}, True), ({'include_candidates': 'false'}, False)]:
                response = self.client.get('/api/assets/graph', query)
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(graph.call_args.kwargs['include_candidates'], expected)
            before = graph.call_count
            for value in ['maybe', '', '0', 'False']:
                self.assertEqual(self.client.get('/api/assets/graph', {'include_candidates': value}).status_code, 400)
            self.assertEqual(graph.call_count, before)

    def post(self, path, data):
        return self.client.post('/api/assets/' + path, json.dumps(data), content_type='application/json')

    def test_monitor_defaults_to_observe_only_and_deduplicates_parent_scope(self):
        from leakscope_app.models import WatchRule
        first = self.post('monitor', {'domain': 'GainSec.com'})
        self.assertEqual(first.status_code, 200, first.content)
        watch = WatchRule.objects.get()
        self.assertFalse(watch.auto_fetch)
        self.assertEqual(watch.ports, [])
        self.assertEqual(watch.actions, [])
        again = self.post('monitor', {'domain': 'api.gainsec.com'})
        self.assertTrue(again.json()['already_covered'])
        self.assertEqual(WatchRule.objects.count(), 1)

    def test_organization_alias_limit_matches_discovery_limit(self):
        self.assertEqual(self.post('organizations',{'name':'Example','aliases':['Alias '+str(n) for n in range(9)]}).status_code,400)
        for value in ['A'*201,'Alias\nCorp','Alias\x7fCorp']:
            self.assertEqual(self.post('organizations',{'name':'Example','aliases':[value]}).status_code,400)

    def test_hidden_observation_evidence_cannot_be_read_directly(self):
        from leakscope.phase3.state import ingest_observation
        from leakscope_app.models import ServiceObservation, AssetEvidence
        ingest_observation('shodan',{'address':'192.0.2.18','port':1234,'metadata':{'hostnames':['secret.example.com']}})
        proof=AssetEvidence.objects.get(source='shodan')
        ServiceObservation.objects.update(hidden=True)
        self.assertEqual(self.client.get('/api/assets/evidence/'+str(proof.pk)).status_code,404)
        self.assertNotIn('secret.example.com',str(self.client.get('/api/assets/graph').json()))

    def test_adding_to_active_watch_needs_explicit_fetch_acknowledgment(self):
        from leakscope_app.models import WatchRule
        watch = WatchRule.objects.create(name='Active', domains=['example.com'], auto_fetch=True)
        data = {'domain':'gainsec.com', 'watch_id':str(watch.pk)}
        self.assertEqual(self.post('monitor', data).status_code, 400)
        watch.refresh_from_db()
        self.assertEqual(watch.domains, ['example.com'])
        self.assertEqual(self.post('monitor', dict(data, allow_auto_fetch=True)).status_code, 200)

    def test_organization_scope_is_explicit_and_creation_does_not_query_provider(self):
        from leakscope_app.models import AssetNode, AssetRelationship
        with patch('leakscope.phase3.registry.discover_organization', side_effect=AssertionError('Explicit discovery only')):
            result = self.post('organizations', {'name':'GainSec', 'domains':['gainsec.com'], 'networks':['192.0.2.0/24']})
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(AssetNode.objects.count(), 3)
        self.assertEqual(set(AssetRelationship.objects.values_list('relation', flat=True)), {'user_scope'})
        self.assertEqual(self.client.get('/api/assets/organizations').json()['results'][0]['value'], 'GainSec')

    def test_context_preserves_provider_name_sources_and_ip_identity(self):
        from leakscope.phase3.state import ingest_observation
        endpoint = ingest_observation('shodan', {'address':'192.0.2.4','port':8443,'service_type':'https',
            'metadata': {'hostnames':['api.gainsec.com'], 'ssl': {'cert': {'subject': {'CN':'*.gainsec.com'}}}}})
        response = self.client.get('/api/assets/context', {'type':'endpoint','id':str(endpoint.pk)})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['target'], '192.0.2.4')
        self.assertIn('192.0.2.4', response.json()['ips'])
        self.assertIn('api.gainsec.com', [x['name'] for x in response.json()['names']])
        wildcard = self.client.get('/api/assets/context', {'target':'*.gainsec.com'})
        self.assertEqual(wildcard.status_code, 200)
        self.assertFalse(wildcard.json()['actions']['dns'])

    @patch('leakscope.phase3.asset_jobs.queue_enrichment')
    def test_enrichment_is_explicit_and_validated(self, queue):
        queue.return_value = {'run': {'id':'fixture'}}
        self.assertEqual(self.post('netblock', {'target':'gainsec.com'}).status_code, 400)
        queue.assert_not_called()
        self.assertEqual(self.post('dns', {'target':'api.gainsec.com'}).status_code, 200)
        queue.assert_called_once_with('dns', 'api.gainsec.com')

    def test_context_refresh_includes_completed_names_dns_and_allocation_proof(self):
        from leakscope.phase3 import relationships as store
        ip='8.8.8.8'
        store.ingest_names({'target':ip,'names':[{'name':'dns.google','source':'dns:ptr','forward_confirmed':True}]})
        store.ingest_dns({'target':'dns.google','records':[{'name':'dns.google','type':'A','value':ip,'ttl':60},
            {'name':'dns.google','type':'TXT','value':'fixture text','ttl':60}]})
        store.ingest_netblock({'target':ip,'networks':[{'cidr':'8.8.8.0/24','organization':'Google','source':'rdap'}]})
        result=self.client.get('/api/assets/context',{'target':ip}).json()
        self.assertEqual(result['target'],ip)
        self.assertIn('dns.google',[item['name'] for item in result['names']])
        self.assertIn('fixture text',str(result['evidence']))
        self.assertIn('8.8.8.0/24',str(result['evidence']))
