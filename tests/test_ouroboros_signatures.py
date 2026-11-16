import importlib,unittest,json,pathlib,time
from tests.test_phase3_capture import tcp_fixture
from leakscope.phase3.wire import WireRecorder

class OuroborosSignatureTests(unittest.TestCase):
    def module(self):
        spec=importlib.util.find_spec('leakscope.phase3.ouroboros');self.assertIsNotNone(spec,'Ouroboros adapter is missing');return importlib.import_module(spec.name)
    def test_two_bounded_handshakes_record_typed_response_context(self):
        m=self.module();calls=[]
        def dialogue(s):
            h=s.recv(8);n=int.from_bytes(h[6:],'big');body=b''
            while len(body)<n:body+=s.recv(n-len(body))
            request=m.decode(body);calls.append(request)
            if request[1][15][-1] is True:response=[3,{14:[42,False,1,False],15:[42,False,1,False]}]
            else:
                self.assertEqual(request,[0,{15:[43,True,0,False]}]);response=[2,[2,15,'network magic mismatch']]
            s.sendall(m.mux(m.encode(response),0x8000))
        recorder=WireRecorder()
        with tcp_fixture(dialogue) as port:meta,verified=m.identify_cardano('127.0.0.1',port,2,recorder)
        self.assertEqual(len(calls),2);self.assertEqual(verified,'cardano')
        self.assertEqual(meta['fingerprints'][0]['probe'],'ouroboros.network-magic-mismatch.v1')
        self.assertEqual(meta['fingerprints'][0]['direction'],'response')
        self.assertEqual(meta['fingerprints'][0]['value'],'network magic mismatch')
        self.assertGreater(len(recorder.received),0)
    def test_incoming_mux_request_is_not_a_refusal(self):
        m=self.module()
        self.assertIsNone(m.refusal_fingerprint(m.encode([0,{15:[42,True,0,False]}])))
    def test_wrong_response_version_and_unstructured_text_are_unknown(self):
        m=self.module()
        for data in ([2,[2,14,'network magic mismatch']],[2,[0,[14,15]]],[1,15,[42,True,0,False]]):self.assertIsNone(m.refusal_fingerprint(m.encode(data)))
    def test_incomplete_or_deep_cbor_is_rejected(self):
        m=self.module()
        for data in (b'\x83\x00',b'\x81'*40+b'\x00'):
            with self.assertRaises(ValueError):m.decode(data)
