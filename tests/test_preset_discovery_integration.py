import json
from unittest.mock import patch
from django.test import TestCase
from leakscope.phase3.state import ingest_observation
from leakscope_app.models import Run,SavedLookup

class PresetDiscoveryIntegrationTests(TestCase):
    def post(self,path,payload):return self.client.post(path,json.dumps(payload),content_type='application/json')

    def test_vendor_model_types_are_discoverable_and_keep_protocol_capabilities(self):
        rows={r['key']:r for r in self.client.get('/api/types').json()['results']}
        for key,base in [('delta_modbus','modbus'),('delta_as228t_a','modbus'),('tridium_bacnet','bacnet'),('niagara4_bacnet','bacnet')]:
            self.assertIn(key,rows)
            self.assertEqual(rows[key]['base_type'],base)
            self.assertIn('identity',rows[key]['fetch_actions'])
            self.assertIn('shodan',rows[key]['discovery_modes']['fingerprint'])
        query=self.post('/api/query-preview',{'types':['delta_as228t_a'],'provider':'shodan','query_mode':'fingerprint'})
        self.assertEqual(query.status_code,200,query.content)
        self.assertIn('AS228T-A',query.json()['queries'][0]['query'])
        self.assertNotIn('port:',query.json()['queries'][0]['query'])

    @patch('leakscope.phase3.jobs.queue_lookup')
    def test_builtin_discovery_queues_underlying_protocol_and_stays_passive(self,queue):
        queue.return_value=Run.objects.create(kind='lookup')
        response=self.post('/api/discover',{'types':['delta_as228t_a'],'provider':'shodan','query_mode':'fingerprint'})
        self.assertEqual(response.status_code,202,response.content)
        lookup=SavedLookup.objects.get()
        self.assertEqual(lookup.service_type,'modbus')
        self.assertFalse(lookup.auto_fetch)
        self.assertEqual(lookup.collection_mode,'passive')
        self.assertIn('AS228T-A',lookup.query)

    def make_preset(self):
        endpoint=ingest_observation('fetch:ot',{'address':'192.0.2.5','port':33502,'service_type':'modbus','metadata':{
            'device':{'objects':[{'id':0,'value':'Delta Electronics, Inc.'},{'id':1,'value':'AS228T-A'}]},
            'collection_scope':{'network':'192.0.2.0/24','country':'US','exclude':'honeypot'}}})
        body={'references':[{'type':'endpoint','id':str(endpoint.pk)}]}
        preview=self.post('/api/discovery-presets/preview',body)
        self.assertEqual(preview.status_code,200,preview.content)
        body.update(name='My Delta model',selected_fields=preview.json()['selected_fields'],scope=preview.json()['scope'],mode='all')
        saved=self.post('/api/discovery-presets',body)
        self.assertIn(saved.status_code,(200,201),saved.content)
        return saved.json()['preset']

    @patch('leakscope.phase3.jobs.queue_lookup')
    def test_saved_preset_survives_requests_and_keeps_scope_and_native_type(self,queue):
        queue.return_value=Run.objects.create(kind='lookup')
        preset=self.make_preset()
        rows={r['key']:r for r in self.client.get('/api/types').json()['results']}
        self.assertIn(preset['key'],rows)
        response=self.post('/api/discover',{'types':[preset['key']],'provider':'shodan','query_mode':'fingerprint','network':'192.0.2.0/25'})
        self.assertEqual(response.status_code,202,response.content)
        lookup=SavedLookup.objects.get()
        self.assertEqual(lookup.service_type,'modbus')
        self.assertEqual(lookup.scope,{'network':'192.0.2.0/25','country':'US','exclude':'honeypot'})
        self.assertIn('AS228T-A',lookup.query)
        self.assertNotIn('port:',lookup.query)

    @patch('leakscope.phase3.jobs.queue_lookup')
    def test_saved_scope_cannot_be_replaced_by_disjoint_scope_or_port_candidates(self,queue):
        preset=self.make_preset()
        for override in [{'network':'198.51.100.0/24'},{'country':'DE'},{'query_mode':'ports'}]:
            response=self.post('/api/discover',dict({'types':[preset['key']],'provider':'shodan','query_mode':'fingerprint'},**override))
            self.assertEqual(response.status_code,400,response.content)
        queue.assert_not_called()

    def test_saved_lookup_editor_keeps_preset_protocol_and_scope(self):
        preset=self.make_preset()
        response=self.post('/api/operations/lookups',{'name':'Scheduled Delta','provider':'shodan','query':'"AS228T-A"',
            'service_type':preset['key'],'scope':{},'interval_seconds':3600,'enabled':False,'collection_mode':'passive'})
        self.assertEqual(response.status_code,200,response.content)
        lookup=SavedLookup.objects.get()
        self.assertEqual(lookup.service_type,'modbus')
        self.assertEqual(lookup.scope['network'],'192.0.2.0/24')

    @patch('leakscope.phase3.jobs.queue_lookup')
    @patch('leakscope_app.discovery_catalog.profile_query')
    def test_scope_expansion_cannot_queue_an_oversized_provider_query(self,profile,queue):
        profile.return_value={'query':'x'*7900,'supported':True,'reason':'Fixture','basis':[]}
        data={'types':['modbus'],'provider':'shodan','query_mode':'fingerprint','exclude':'y'*200}
        preview=self.post('/api/query-preview',data)
        self.assertEqual(preview.status_code,200)
        self.assertFalse(preview.json()['queries'][0]['supported'])
        self.assertEqual(self.post('/api/discover',data).status_code,400)
        queue.assert_not_called()
