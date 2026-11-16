import json,struct,tempfile,pathlib
from django.test import TestCase,override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from tests.test_phase3_capture import tcp_fixture
from leakscope.phase3.ouroboros import encode,decode,mux
from leakscope.phase3.capture import capture_endpoint
from leakscope.phase3.state import ingest_observation
from leakscope_app.models import EvidenceArtifact,ServiceEndpoint

class SignatureIntegrationTests(TestCase):
    def test_real_native_capture_creates_amaru_hint_and_retains_wire_evidence(self):
        def dialogue(sock):
            header=sock.recv(8);n=int.from_bytes(header[6:],'big');body=b''
            while len(body)<n:body+=sock.recv(n-len(body))
            query=decode(body)[1][15][-1]
            response=[3,{15:[42,False,1,False]}] if query else [2,[2,15,'network magic mismatch']]
            sock.sendall(mux(encode(response),0x8000))
        with tempfile.TemporaryDirectory() as temp,tcp_fixture(dialogue) as port:
            result=capture_endpoint({'address':'127.0.0.1','port':port,'service_type':'cardano','transport':'tcp'},'identity',{'CAPTURE_DIR':temp,'CAPTURE_TIMEOUT':2})
            self.assertEqual(result['verified_type'],'cardano');self.assertGreaterEqual(len(result['artifacts']),3)
            endpoint=ingest_observation('fetch:identity',{'address':'127.0.0.1','port':port,'service_type':result['verified_type'],'metadata':result['metadata']})
            data=endpoint.observations.latest('last_collected_at').data
            self.assertEqual(data['service_type'],'amaru');self.assertFalse(data['metadata']['identity_verified']);self.assertTrue(data['metadata']['protocol_identity_verified'])
            self.assertEqual(data['metadata']['implementation_basis'],'response signature; experimental')
            from leakscope.phase3.attribution import observation_level,LEVELS
            self.assertIn('signature_matched',LEVELS)
            self.assertEqual(observation_level('fetch:identity',data)['level'],'signature_matched')
            from leakscope_app.atlas import classify_levels
            levels={item['key']:item['level'] for item in classify_levels(data,'fetch:identity')}
            self.assertEqual(levels['amaru'],'signature_matched');self.assertEqual(levels['cardano'],'protocol_confirmed')
            self.assertTrue(all(pathlib.Path(a['path']).is_file() for a in result['artifacts']))
    def test_capture_save_keeps_original_pcap_and_typed_observations(self):
        data=(pathlib.Path(__file__).parent/'fixtures/ja4/controls.pcap').read_bytes()
        with tempfile.TemporaryDirectory() as temp,override_settings(LEAKSCOPE_CAPTURE_DIR=temp):
            response=self.client.post('/api/operations/signatures/import',{'capture':SimpleUploadedFile('controls.pcap',data),'save':'1'})
            self.assertEqual(response.status_code,200,response.content[:1000]);self.assertGreater(len(response.json()['saved']),0)
            self.assertGreater(ServiceEndpoint.objects.count(),0)
            artifact=EvidenceArtifact.objects.first();self.assertEqual((pathlib.Path(temp)/artifact.relative_path).read_bytes(),data)
    def test_mux_clock_changes_do_not_create_service_changes(self):
        fp={'kind':'ouroboros','value':'network magic mismatch','direction':'response','probe':'ouroboros.network-magic-mismatch.v1','protocol':0,'version':15,'message_type':2,'reason_code':2}
        row={'address':'192.0.2.99','port':3001,'service_type':'cardano','metadata':{'protocol':'cardano','fingerprints':[fp],'exchanges':[{'response_hex':'000000018000001b820283020f766e6574776f726b206d61676963206d69736d61746368'}]}}
        endpoint=ingest_observation('fetch:identity',row)
        row['metadata']['exchanges'][0]['response_hex']='00000002'+row['metadata']['exchanges'][0]['response_hex'][8:]
        ingest_observation('fetch:identity',row)
        self.assertEqual(endpoint.observations.count(),1)
