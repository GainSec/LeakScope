import json
from unittest.mock import patch
from django.test import TestCase
from leakscope.phase3.state import ingest_observation
from leakscope_app.models import SavedLookup, Run

class ServiceCoverageAPITests(TestCase):
    def post(self,path,data):return self.client.post(path,json.dumps(data),content_type='application/json')

    def test_discover_types_expose_catalog_and_ics(self):
        rows=self.client.get('/api/types').json()['results'];bykey={row['key']:row for row in rows}
        self.assertGreaterEqual(len(rows),85)
        self.assertEqual(bykey['modbus']['category'],'ot')
        self.assertIn('ICS',bykey['modbus']['category_label'])
        self.assertFalse(bykey['modbus']['legacy'])
        self.assertTrue(bykey['gitlab']['legacy'])

    @patch('leakscope.phase3.catalog.profile_query',create=True)
    def test_fingerprint_preview_uses_response_evidence_and_preserves_scope(self,profile):
        profile.return_value={'query':'http.body:"cluster_uuid"','supported':True,'reason':'Response field','basis':['https://example.test/spec']}
        r=self.post('/api/query-preview',{'types':['elastic'],'provider':'netlas','query_mode':'fingerprint','network':'192.0.2.0/24','country':'US','keyword':'Acme'})
        self.assertEqual(r.status_code,200,r.content)
        q=r.json()['queries'][0]
        self.assertIn('cluster_uuid',q['query']);self.assertNotIn('port:',q['query'])
        self.assertIn('192.0.2.0/24',q['query']);self.assertIn('US',q['query']);self.assertIn('Acme',q['query'])
        self.assertTrue(q['supported']);self.assertTrue(q['basis'])

    @patch('leakscope.phase3.jobs.queue_lookup')
    @patch('leakscope.phase3.catalog.profile_query',create=True)
    def test_shodan_ot_discovery_uses_normalized_jobs(self,profile,queue):
        profile.return_value={'query':'"Device Identification"','supported':True,'reason':'Protocol response','basis':[]}
        queue.return_value=Run.objects.create(kind='lookup',provider='shodan')
        r=self.post('/api/discover',{'types':['modbus'],'provider':'shodan','query_mode':'fingerprint','network':'192.0.2.0/24'})
        self.assertEqual(r.status_code,202,r.content);self.assertEqual(r.json()['results_url'],'/assets/')
        lookup=SavedLookup.objects.get();self.assertEqual(lookup.service_type,'modbus');self.assertEqual(lookup.scope,{'network':'192.0.2.0/24'})
        self.assertEqual(lookup.query,'"Device Identification"');queue.assert_called_once()

    @patch('leakscope.phase3.jobs.queue_lookup')
    @patch('leakscope.phase3.catalog.profile_query',create=True)
    def test_unsupported_fingerprint_is_visible_and_never_silently_uses_ports(self,profile,queue):
        profile.return_value={'query':'','supported':False,'reason':'No verified response field','basis':[]}
        data={'types':['modbus'],'provider':'netlas','query_mode':'fingerprint'}
        r=self.post('/api/query-preview',data);self.assertEqual(r.status_code,200,r.content)
        self.assertFalse(r.json()['queries'][0]['supported'])
        self.assertEqual(self.post('/api/discover',data).status_code,400)
        queue.assert_not_called();self.assertEqual(SavedLookup.objects.count(),0)

    @patch('leakscope_app.operations.queue_capture')
    def test_manual_tcp_payload_is_validated_and_forwarded(self,queue):
        endpoint=ingest_observation('fixture',{'address':'192.0.2.6','port':43127,'service_type':'unknown','transport':'tcp'})
        queue.return_value=Run.objects.create(kind='capture')
        options={'tcp':{'payload':'50494e470d0a','encoding':'hex','tls':False,'timeout_seconds':3}}
        r=self.post('/api/operations/endpoints/'+str(endpoint.pk)+'/fetch',{'actions':['tcp'],'options':options})
        self.assertEqual(r.status_code,200,r.content)
        self.assertEqual(queue.call_args.kwargs['options'],options)
        self.assertIn('tcp',self.client.get('/api/operations/endpoints/'+str(endpoint.pk)).json()['endpoint']['fetch_actions'])
        for invalid in ({'tcp':{'payload':'zz','encoding':'hex'}},{'tcp':{'payload':'aa'*4097,'encoding':'hex'}},{'tcp':{'tls':'yes'}},{'shell':{'cmd':'anything'}}):
            self.assertEqual(self.post('/api/operations/endpoints/'+str(endpoint.pk)+'/fetch',{'actions':['tcp'],'options':invalid}).status_code,400)
        self.assertEqual(queue.call_count,1)

    @patch('leakscope.phase3.jobs.fetch_endpoint.apply_async')
    def test_queued_payload_is_frozen_with_the_capture(self,submit):
        from leakscope.phase3.jobs import queue_capture
        endpoint=ingest_observation('fixture',{'address':'192.0.2.7','port':43128,'transport':'tcp'})
        opts={'tcp':{'payload':'HELLO\r\n','encoding':'text','tls':False,'timeout_seconds':3}}
        run=queue_capture(endpoint.pk,['tcp'],cooldown_seconds=0,options=opts)
        self.assertEqual(submit.call_args.kwargs['kwargs']['snapshot']['tcp_options'],opts['tcp'])
        self.assertEqual(json.loads(run.parameters)['options'],opts)

    def test_generic_udp_requires_an_explicit_payload(self):
        endpoint=ingest_observation('fixture',{'address':'192.0.2.8','port':43129,'transport':'udp'})
        r=self.post('/api/operations/endpoints/'+str(endpoint.pk)+'/fetch',{'actions':['udp'],'options':{'udp':{'payload':'','encoding':'hex'}}})
        self.assertEqual(r.status_code,400)

    def test_known_http_profile_can_capture_on_nonstandard_port(self):
        endpoint=ingest_observation('fixture',{'address':'192.0.2.9','port':43130,'service_type':'elastic'})
        actions=self.client.get('/api/operations/endpoints/'+str(endpoint.pk)).json()['endpoint']['fetch_actions']
        self.assertIn('screenshot',actions);self.assertIn('http',actions)

    @patch('leakscope.phase3.jobs.capture_endpoint')
    def test_failed_identity_retains_wire_artifacts_without_verification(self,capture):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from django.test import override_settings
        from leakscope.phase3.capture import CaptureEvidenceError
        from leakscope.phase3.jobs import fetch_endpoint
        from leakscope_app.models import EvidenceArtifact
        row=ingest_observation('fixture',{'address':'192.0.2.10','port':43131,'service_type':'modbus'})
        with TemporaryDirectory() as folder, override_settings(LEAKSCOPE_CAPTURE_DIR=folder):
            path=Path(folder)/'wire.json';path.write_text('{"sent":{"hex":"00"},"received":{"hex":"bad0"}}')
            capture.side_effect=CaptureEvidenceError('Rejected malformed reply',{'metadata':{'identity_verified':False,'partial':True},'artifacts':[{'path':str(path),'kind':'wire-transcript','mime':'application/json'}]})
            result=fetch_endpoint.apply(args=[str(row.pk),['identity']]).get()
            self.assertTrue(result['partial']);self.assertIn('Rejected malformed reply',result['errors'][0])
            self.assertEqual(EvidenceArtifact.objects.get().kind,'wire-transcript')
            self.assertFalse(row.observations.get(provider='fetch:identity').data['metadata']['identity_verified'])

    @patch('leakscope.phase3.jobs.search_provider')
    def test_http_response_keeps_matched_profile_without_claiming_native_identity(self,search):
        from leakscope.phase3.jobs import run_lookup
        lookup=SavedLookup.objects.create(name='Elasticsearch signature',provider='netlas',query='http.body:"cluster_uuid"',service_type='elastic')
        search.return_value={'observations':[{'address':'192.0.2.11','port':43132,'scheme':'http','service_type':'http','metadata':{'http':{'title':'Fixture'}}}]}
        result=run_lookup.apply(args=[str(lookup.pk)]).get()
        self.assertEqual(result['stored'],1)
        rows=self.client.get('/api/operations/endpoints?type=elastic').json()['results']
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['service_type'],'http')
        detail=self.client.get('/api/operations/endpoints/'+rows[0]['id']).json()
        self.assertEqual(detail['observations'][0]['data']['metadata']['matched_profile'],'elastic')
        self.assertNotIn('identity_verified',detail['observations'][0]['data']['metadata'])

    @patch('leakscope_app.operations.queue_capture')
    def test_invalid_mixed_transport_batch_queues_nothing(self,queue):
        tcp=ingest_observation('fixture',{'address':'192.0.2.12','port':43133,'transport':'tcp'})
        udp=ingest_observation('fixture',{'address':'192.0.2.13','port':43134,'transport':'udp'})
        response=self.post('/api/operations/fetch',{'ids':[str(tcp.pk),str(udp.pk)],'actions':['tcp']})
        self.assertEqual(response.status_code,400);queue.assert_not_called()

    @patch('leakscope.phase3.jobs.capture_endpoint')
    def test_capture_limit_is_partial_even_without_an_exception(self,capture):
        from leakscope.phase3.jobs import fetch_endpoint
        row=ingest_observation('fixture',{'address':'192.0.2.14','port':43135,'transport':'tcp'})
        capture.return_value={'partial':True,'metadata':{'identity_verified':False,'partial':True},'artifacts':[]}
        result=fetch_endpoint.apply(args=[str(row.pk),['tcp']]).get()
        self.assertTrue(result['partial'])

    @patch('leakscope.phase3.jobs.capture_endpoint')
    def test_generic_wire_history_tracks_bytes_without_fragmentation_noise(self,capture):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from django.test import override_settings
        from leakscope.phase3.jobs import fetch_endpoint
        from leakscope_app.models import ObservationChange
        row=ingest_observation('fixture',{'address':'192.0.2.15','port':43136,'transport':'tcp'})
        with TemporaryDirectory() as folder, override_settings(LEAKSCOPE_CAPTURE_DIR=folder):
            for index,(body,events) in enumerate([(b'AB',1),(b'AB',2),(b'CD',2)]):
                path=Path(folder)/str(index);path.write_bytes(body)
                capture.return_value={'metadata':{'identity_verified':False,'wire_summary':{'bytes_received':2,'events_received':events}},'artifacts':[{'path':str(path),'kind':'wire-response'}]}
                fetch_endpoint.apply(args=[str(row.pk),['tcp']]).get()
                if index==0:initial=ObservationChange.objects.filter(endpoint=row).count()
                if index==1:self.assertEqual(ObservationChange.objects.filter(endpoint=row).count(),initial)
            self.assertEqual(ObservationChange.objects.filter(endpoint=row).count(),initial+1)

    @patch('leakscope.phase3.jobs.queue_lookup')
    def test_zoomeye_discovery_preserves_requested_page_size(self,queue):
        queue.return_value=Run.objects.create(kind='lookup',provider='zoomeye')
        r=self.post('/api/discover',{'types':['elastic'],'provider':'zoomeye','query_mode':'fingerprint','page_count':3,'zoomeye_pagesize':7})
        self.assertEqual(r.status_code,202,r.content)
        row=SavedLookup.objects.get();self.assertEqual(row.page_size,7);self.assertEqual(row.max_results,21)

    @patch('leakscope.phase3.jobs.search_provider')
    def test_lookup_snapshot_honors_page_size_and_profile_changes_do_not_change_evidence(self,search):
        from leakscope.phase3.jobs import run_lookup,lookup_snapshot
        from leakscope_app.models import ObservationChange
        lookup=SavedLookup.objects.create(name='Bounded',provider='zoomeye',query='http.body="cluster_uuid"',service_type='elastic',page_count=3,page_size=7)
        search.return_value={'observations':[{'address':'192.0.2.16','port':43137,'scheme':'http','service_type':'http','metadata':{'title':'Same content'}}]}
        run_lookup.apply(args=[str(lookup.pk)],kwargs={'snapshot':lookup_snapshot(lookup)}).get()
        self.assertEqual(search.call_args.kwargs['page_size'],7)
        lookup.service_type='opensearch';lookup.save()
        run_lookup.apply(args=[str(lookup.pk)],kwargs={'snapshot':lookup_snapshot(lookup)}).get()
        self.assertEqual(ObservationChange.objects.count(),0)
        for profile in ('elastic','opensearch'):
            self.assertEqual(self.client.get('/api/operations/endpoints?type='+profile).json()['count'],1)

    @patch('leakscope.phase3.jobs.search_provider')
    def test_unclassified_candidate_profile_is_provenance_not_response_change(self,search):
        from leakscope.phase3.jobs import run_lookup
        from leakscope_app.models import ObservationChange
        lookup=SavedLookup.objects.create(name='Opaque candidate',provider='netlas',query='port:9200',service_type='elastic')
        search.return_value={'observations':[{'address':'192.0.2.17','port':43138,'metadata':{'banner':'opaque'}}]}
        run_lookup.apply(args=[str(lookup.pk)]).get()
        lookup.service_type='opensearch';lookup.save()
        search.return_value={'observations':[{'address':'192.0.2.17','port':43138,'metadata':{'banner':'opaque'}}]}
        run_lookup.apply(args=[str(lookup.pk)]).get()
        self.assertEqual(ObservationChange.objects.count(),0)
        for profile in ('elastic','opensearch'):
            self.assertEqual(self.client.get('/api/operations/endpoints?type='+profile).json()['count'],1)
