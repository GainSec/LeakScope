import json
import socket
import ssl
import subprocess
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
from django.test import SimpleTestCase

from leakscope.phase3.capture import capture_endpoint, supported_actions
from tests.test_phase3_capture import tcp_fixture


class ReachabilityCaptureTests(SimpleTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.config={'CAPTURE_DIR':self.tmp.name,'CAPTURE_TIMEOUT':1}

    def endpoint(self,port,scheme=''):
        return {'address':'127.0.0.1','port':port,'transport':'tcp','service_type':'unknown','scheme':scheme}

    def tls_contexts(self):
        root=Path(self.tmp.name);cert=root/'fixture.crt';key=root/'fixture.key'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(cert),'-days','1','-subj','/CN=localhost','-addext','subjectAltName=IP:127.0.0.1'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        server=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);server.load_cert_chain(cert,key)
        return server,ssl.create_default_context(cafile=str(cert))

    def test_tcp_check_sends_no_application_bytes_and_does_not_claim_identity(self):
        def dialogue(sock):self.assertEqual(sock.recv(1),b'')
        with tcp_fixture(dialogue) as port:
            result=capture_endpoint(self.endpoint(port),'check',self.config)
        metadata=result['metadata'];self.assertTrue(metadata['reachable'])
        self.assertEqual(metadata['address'],'127.0.0.1');self.assertEqual(metadata['port'],port)
        self.assertFalse(metadata['identity_verified']);self.assertFalse(metadata['tls_verified'])
        self.assertFalse(metadata['tls']);self.assertGreaterEqual(metadata['duration_ms'],0)
        self.assertNotIn('verified_type',result)
        self.assertEqual([a['kind'] for a in result['artifacts']],['reachability'])
        artifact=Path(result['artifacts'][0]['path'])
        self.assertEqual(json.loads(artifact.read_text()),metadata)
        self.assertEqual(artifact.stat().st_mode&0o777,0o600)
        self.assertEqual(result['artifacts'][0]['mime'],'application/json')

    def test_tls_check_verifies_the_certificate_without_an_application_request(self):
        server,client=self.tls_contexts()
        def dialogue(raw):
            with server.wrap_socket(raw,server_side=True) as sock:
                try:payload=sock.recv(1)
                except (ConnectionResetError,BrokenPipeError):payload=b''
                self.assertEqual(payload,b'')
        with tcp_fixture(dialogue) as port,patch('leakscope.phase3.reachability.ssl.create_default_context',return_value=client):
            result=capture_endpoint(self.endpoint(port,'https'),'check',self.config)
        self.assertTrue(result['metadata']['reachable']);self.assertTrue(result['metadata']['tls'])
        self.assertTrue(result['metadata']['tls_verified']);self.assertFalse(result['metadata']['identity_verified'])

    def test_untrusted_tls_fails_without_a_reachability_artifact(self):
        server,_=self.tls_contexts()
        def dialogue(raw):
            try:
                with server.wrap_socket(raw,server_side=True):pass
            except ssl.SSLError:pass
        with tcp_fixture(dialogue) as port,self.assertRaises(ValueError):
            capture_endpoint(self.endpoint(port,'grpcs'),'check',self.config)
        self.assertFalse(list(Path(self.tmp.name).glob('*.json')))

    def test_unreachable_tcp_does_not_manufacture_wire_or_a_positive_result(self):
        sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
        with self.assertRaises(ValueError):capture_endpoint(self.endpoint(port),'check',self.config)
        self.assertEqual(list(Path(self.tmp.name).iterdir()),[])

    def test_check_rejects_udp_and_invalid_timeout_before_connecting(self):
        from leakscope.phase3 import reachability
        with patch.object(reachability,'connect_tcp') as connect:
            with self.assertRaises(ValueError):capture_endpoint(dict(self.endpoint(42137),transport='udp'),'check',self.config)
            for timeout in (0,-1,11,float('inf'),float('nan'),True,'3'):
                with self.subTest(timeout=timeout),self.assertRaises(ValueError):
                    capture_endpoint(self.endpoint(42137),'check',dict(self.config,CAPTURE_TIMEOUT=timeout))
        connect.assert_not_called()

    def test_dns_resolution_shares_the_bounded_connection_deadline(self):
        resolve=socket.getaddrinfo
        def delayed(*args,**kwargs):
            time.sleep(.4)
            return resolve(*args,**kwargs)
        with patch('leakscope.phase3.wire.socket.getaddrinfo',side_effect=delayed):
            started=time.monotonic()
            with self.assertRaises(ValueError):
                capture_endpoint(self.endpoint(42137),'check',dict(self.config,CAPTURE_TIMEOUT=.05))
            elapsed=time.monotonic()-started
        self.assertLess(elapsed,.25)
        self.assertEqual(list(Path(self.tmp.name).iterdir()),[])

    def test_blacklisted_target_is_refused_and_capability_is_tcp_only(self):
        with self.assertRaisesMessage(ValueError,'blacklisted'):
            capture_endpoint(self.endpoint(42137),'check',dict(self.config,blacklist=['127.0.0.0/8']))
        self.assertIn('check',supported_actions(self.endpoint(42137)))
        self.assertNotIn('check',supported_actions(dict(self.endpoint(42137),transport='udp')))
