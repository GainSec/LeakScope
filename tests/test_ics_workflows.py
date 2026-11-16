import json
from unittest.mock import patch
from django.test import TestCase


class IcsWorkflowTests(TestCase):
    def setUp(self):
        from leakscope.phase3.state import ingest_observation
        self.parent=ingest_observation('fixture',{'address':'192.0.2.14','port':1502,'service_type':'modbus','metadata':{}})

    def post(self,path,data):
        return self.client.post('/api/ics/'+path,json.dumps(data),content_type='application/json')

    @patch('leakscope.phase3.ics_jobs.indexed_host')
    @patch('leakscope.phase3.ics_jobs.queue_capture')
    def test_related_services_are_exact_ip_all_port_and_passive_by_default(self,capture,indexed):
        from leakscope.phase3.ics_jobs import related_services
        indexed.return_value={'observations':[{'address':self.parent.address,'port':33490,'scheme':'http','service_type':'http','metadata':{}},
            {'address':'192.0.2.99','port':80,'scheme':'http','metadata':{}}],'partial':False,'errors':[]}
        result=related_services.apply(kwargs={'endpoint_id':str(self.parent.pk),'provider':'shodan','collection_mode':'passive','max_results':100}).get()
        self.assertEqual(result['stored'],1)
        self.assertEqual(result['outside_host'],1)
        indexed.assert_called_once()
        self.assertEqual(indexed.call_args.args[1],self.parent.address)
        capture.assert_not_called()
        context=self.client.get('/api/ics/context',{'endpoint':str(self.parent.pk)}).json()
        self.assertEqual(context['host_ip'],self.parent.address)
        self.assertIn(33490,[row['port'] for row in context['related']])

    @patch('leakscope.phase3.ics_jobs.queue_inspection')
    def test_inspection_is_explicit_and_validates_read_only_options(self,queue):
        from leakscope_app.models import Run
        queue.return_value=Run.objects.create(kind='ot_inspection')
        response=self.post('inspect',{'endpoint_id':str(self.parent.pk),'options':{'unit_id':1,'blocks':[{'function':3,'address':10,'count':2}]}})
        self.assertEqual(response.status_code,200,response.content)
        queue.assert_called_once()
        queue.reset_mock()
        self.assertEqual(self.post('inspect',{'endpoint_id':str(self.parent.pk),'options':{'blocks':[{'function':16,'address':0,'count':1}]}}).status_code,400)
        queue.assert_not_called()

    @patch('leakscope.phase3.ics_jobs.queue_scan')
    def test_active_scan_needs_explicit_action_and_consent(self,queue):
        from leakscope_app.models import Run
        queue.return_value=Run.objects.create(kind='port_inventory')
        data={'endpoint_id':str(self.parent.pk),'ports':'1-65535','rate_per_second':20,'max_seconds':120}
        self.assertEqual(self.post('scan',data).status_code,400)
        queue.assert_not_called()
        self.assertEqual(self.post('scan',dict(data,allow_active_scan=True)).status_code,200)

    def test_gallery_contains_screenshots_and_filters_hidden_evidence(self):
        from leakscope_app.models import EvidenceArtifact
        artifact=EvidenceArtifact.objects.create(endpoint=self.parent,kind='screenshot',mime='image/png',relative_path='fixture.png',size=10,sha256='a'*64,tool='native')
        response=self.client.get('/api/ics/gallery')
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(response.json()['results'][0]['id'],str(artifact.pk))
        self.parent.observations.update(hidden=True)
        self.assertEqual(self.client.get('/api/ics/gallery').json()['count'],0)

    def test_local_context_and_gallery_do_not_query_providers(self):
        with patch('socket.getaddrinfo',side_effect=AssertionError('Local read only')):
            response=self.client.get('/api/ics/context',{'endpoint':str(self.parent.pk)})
            self.assertEqual(response.status_code,200,response.content)
            self.assertTrue(response.json()['supported_inspection'])
            self.assertEqual(self.client.get('/api/ics/gallery').status_code,200)

    def test_vnc_capture_defaults_to_native_screenshot_on_any_port(self):
        from leakscope.phase3.state import ingest_observation
        endpoint=ingest_observation('fixture',{'address':'192.0.2.90','port':33390,'service_type':'vnc'})
        result=self.client.get('/api/operations/endpoints/'+str(endpoint.pk)).json()['endpoint']
        self.assertEqual(result['default_actions'],['screenshot'])
        catalog=self.client.get('/api/operations/catalog').json()['results']
        self.assertEqual(next(row for row in catalog if row['key']=='vnc')['default_actions'],['screenshot'])

    def test_inspection_does_not_resurrect_source_hidden_during_exchange(self):
        from leakscope.phase3.ics_jobs import inspect_points
        def hide(*args,**kwargs):
            self.parent.observations.update(hidden=True)
            return {'metadata':{'points':[]},'artifacts':[]}
        with patch('leakscope.phase3.ot_inspection.inspect_endpoint',side_effect=hide):
            result=inspect_points.apply(kwargs={'endpoint_id':str(self.parent.pk),'options':{}}).get()
        self.assertTrue(result['cancelled'])
        self.assertFalse(self.parent.observations.filter(hidden=False).exists())

    def test_related_services_do_not_resurrect_parent_hidden_during_lookup(self):
        from leakscope.phase3.ics_jobs import related_services
        def hide(*args,**kwargs):
            self.parent.observations.update(hidden=True)
            return {'observations':[{'address':self.parent.address,'port':self.parent.port,'service_type':'modbus','metadata':{}}]}
        with patch('leakscope.phase3.ics_jobs.indexed_host',side_effect=hide):
            result=related_services.apply(kwargs={'endpoint_id':str(self.parent.pk),'provider':'shodan'}).get()
        self.assertTrue(result['cancelled'])
        self.assertFalse(self.parent.observations.filter(hidden=False).exists())

    def test_legacy_screenshot_without_provenance_is_hidden_conservatively(self):
        from leakscope_app.models import EvidenceArtifact
        from leakscope.phase3.state import ingest_observation
        artifact=EvidenceArtifact.objects.create(endpoint=self.parent,kind='screenshot',mime='image/png',relative_path='fixture.png',size=10,sha256='a'*64,tool='gowitness')
        ingest_observation('fetch:screenshot',{'address':self.parent.address,'port':self.parent.port,'service_type':'modbus','metadata':{'image':'old'}})
        self.parent.observations.filter(provider='fetch:screenshot').update(hidden=True)
        self.assertTrue(self.parent.observations.filter(hidden=False).exists())
        self.assertEqual(self.client.get('/api/ics/gallery').json()['count'],0)

    def test_related_host_uses_only_fresh_saved_dns_without_an_implicit_lookup(self):
        from leakscope.phase3.state import ingest_observation
        from leakscope.phase3.relationships import ingest_dns
        from leakscope.phase3.ics_jobs import host_ip
        from django.utils import timezone
        from datetime import timedelta
        endpoint=ingest_observation('fixture',{'address':'device.example.com','port':33490,'scheme':'http','metadata':{}})
        ingest_dns({'target':endpoint.address,'records':[{'name':endpoint.address,'type':'A','value':'192.0.2.42',
            'expires_at':(timezone.now()+timedelta(minutes=5)).isoformat()}]})
        with patch('socket.getaddrinfo',side_effect=AssertionError('No implicit DNS')):
            self.assertEqual(host_ip(endpoint),'192.0.2.42')

    def test_generic_vnc_capture_does_not_resurrect_hidden_source(self):
        from leakscope.phase3.jobs import fetch_endpoint
        def hide(*args,**kwargs):
            self.parent.observations.update(hidden=True)
            return {'metadata':{},'artifacts':[]}
        with patch('leakscope.phase3.jobs.capture_endpoint',side_effect=hide):
            result=fetch_endpoint.apply(args=[str(self.parent.pk),['screenshot']]).get()
        self.assertTrue(result['cancelled'])
        self.assertFalse(self.parent.observations.filter(hidden=False).exists())

    def test_web_probe_labels_http_only_after_actual_response(self):
        from leakscope.phase3.state import ingest_observation
        from leakscope.phase3.ics_jobs import probe_web
        endpoint=ingest_observation('scan:tcp',{'address':'192.0.2.18','port':33490,'metadata':{}})
        with patch('leakscope.phase3.capture.capture_endpoint',return_value={'metadata':{'status':200,'title':'Web HMI'},'artifacts':[]}):
            success=probe_web.apply(kwargs={'endpoint_id':str(endpoint.pk),'tls':False}).get()
        self.assertTrue(success['metadata']['http_verified'])
        from leakscope_app.models import ServiceEndpoint
        self.assertEqual(ServiceEndpoint.objects.get(pk=success['endpoint_id']).scheme,'http')
        with patch('leakscope.phase3.capture.capture_endpoint',return_value={'metadata':{'status':0},'artifacts':[]}):
            failure=probe_web.apply(kwargs={'endpoint_id':str(endpoint.pk),'tls':True}).get()
        self.assertFalse(failure['metadata']['http_verified'])
        self.assertEqual(failure['endpoint_id'],str(endpoint.pk))
