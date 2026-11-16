import json
import socket
import socketserver
import ssl
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase

from leakscope.phase3.capture import CaptureEvidenceError, capture_endpoint, supported_actions
from leakscope.phase3.wire import WireRecorder, connect_tcp, validate_tcp_options
from tests.test_phase3_capture import tcp_fixture


class WireCaptureTests(SimpleTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.config={'CAPTURE_DIR':self.tmp.name,'CAPTURE_TIMEOUT':1}

    def endpoint(self,port,service,scheme=''):
        return {'address':'127.0.0.1','port':port,'transport':'tcp','scheme':scheme,'service_type':service}

    def capture(self,service,dialogue):
        with tcp_fixture(dialogue) as port:return capture_endpoint(self.endpoint(port,service),'identity',self.config)
    def transcript(self, result):
        item=next(item for item in result['artifacts'] if item['kind']=='wire-transcript')
        return json.loads(Path(item['path']).read_text())

    def test_unknown_tcp_payload_and_binary_response_are_real_evidence(self):
        def dialogue(sock):
            self.assertEqual(sock.recv(4096),b'\x00hello\xff')
            sock.sendall(b'\x00response\xff')
        with tcp_fixture(dialogue) as port:
            result=capture_endpoint(self.endpoint(port,'unknown'),'tcp',dict(self.config,TCP_OPTIONS={'payload':'0068656c6c6fff','encoding':'hex','timeout_seconds':1,'tls':False}))
        wire=self.transcript(result)
        self.assertEqual(wire['sent']['hex'],'0068656c6c6fff')
        self.assertEqual(wire['received']['hex'],'00726573706f6e7365ff')
        self.assertFalse(result['metadata']['identity_verified'])
        self.assertNotIn('verified_type',result)
        self.assertEqual(result['metadata']['wire_summary']['termination'],'eof')
        self.assertEqual(result['metadata']['wire_summary']['bytes_received'],10)
        self.assertEqual(Path(next(item['path'] for item in result['artifacts'] if item['kind']=='wire-response')).read_bytes(),b'\x00response\xff')

    def test_blank_payload_is_receive_only(self):
        def dialogue(sock):
            sock.sendall(b'banner\r\n');sock.shutdown(socket.SHUT_WR)
            self.assertEqual(sock.recv(1),b'')
        with tcp_fixture(dialogue) as port:
            result=capture_endpoint(self.endpoint(port,'unclassified'),'tcp',self.config)
        self.assertEqual(self.transcript(result)['sent']['hex'],'')
        self.assertEqual(result['metadata']['wire_summary']['events_sent'],0)

    def test_receive_cap_is_enforced_without_reading_extra_data(self):
        with tcp_fixture(lambda sock:sock.sendall(b'x'*65537)) as port:
            result=capture_endpoint(self.endpoint(port,'unknown'),'tcp',self.config)
        self.assertEqual(result['metadata']['wire_summary']['bytes_received'],65536)
        self.assertTrue(result['metadata']['wire_summary']['receive_limit_reached'])
        self.assertTrue(result['partial'])

    def test_payload_options_are_validated_before_connecting(self):
        cases=[{'payload':'z','encoding':'hex'},{'payload':'a'*4097},{'payload':'é'*2049}, {'timeout_seconds':0}, {'timeout_seconds':11}, {'tls':'false'}, {'encoding':'base64'}, {'payload':5}, {'surprise':True}]
        with patch('leakscope.phase3.wire.socket.create_connection') as connect:
            for options in cases:
                with self.subTest(options=str(options)[:60]),self.assertRaises(ValueError):
                    capture_endpoint(self.endpoint(1234,'unknown'),'tcp',dict(self.config,TCP_OPTIONS=options))
        connect.assert_not_called()
        self.assertEqual(len(validate_tcp_options({'payload':'ff '*4096,'encoding':'hex'})[1]),4096)

    def test_native_failure_retains_actual_exchange_without_verified_type(self):
        def dialogue(sock):
            self.assertEqual(sock.recv(1024),b'*1\r\n$4\r\nPING\r\n');sock.sendall(b'not redis\r\n')
        with tcp_fixture(dialogue) as port,self.assertRaises(CaptureEvidenceError) as caught:
            capture_endpoint(self.endpoint(port,'redis'),'identity',self.config)
        result=caught.exception.result
        self.assertTrue(result['partial'])
        self.assertFalse(result['metadata']['identity_verified'])
        self.assertNotIn('verified_type',result)
        self.assertEqual(self.transcript(result)['received']['text'],'not redis\r\n')

    def test_native_success_adds_actual_transcript(self):
        result=self.capture('postgresql',lambda sock:(self.assertEqual(sock.recv(1024),bytes.fromhex('0000000804d2162f')),sock.sendall(b'N')))
        self.assertTrue(result['metadata']['identity_verified'])
        self.assertEqual(self.transcript(result)['received']['hex'],'4e')

    def test_mqtt_clean_connect_then_disconnect_without_subscriptions(self):
        def dialogue(sock):
            header=sock.recv(2);self.assertEqual(header[0],0x10)
            body=b''
            while len(body)<header[1]:body+=sock.recv(header[1]-len(body))
            self.assertEqual(body[:10],b'\x00\x04MQTT\x04\x02\x00\x00')
            self.assertEqual(len(body),12+int.from_bytes(body[10:12],'big'))
            sock.sendall(b'\x20\x02\x00\x00')
            self.assertEqual(sock.recv(1024),b'\xe0\x00')
        result=self.capture('mqtt',dialogue)
        self.assertEqual(result['verified_type'],'mqtt')
        self.assertFalse(result['metadata']['authentication_required'])

    def test_rtsp_options_response_does_not_start_a_stream(self):
        def dialogue(sock):
            self.assertEqual(sock.recv(4096),b'OPTIONS * RTSP/1.0\r\nCSeq: 1\r\nUser-Agent: LeakScope\r\n\r\n')
            sock.sendall(b'RTSP/1.0 200 OK\r\nCSeq: 1\r\nServer: FixtureCam\r\nPublic: OPTIONS, DESCRIBE\r\n\r\n')
        result=self.capture('rtsp',dialogue)
        self.assertEqual(result['verified_type'],'rtsp')
        self.assertEqual(result['metadata']['server'],'FixtureCam')

    def test_capabilities_are_based_on_protocol_not_standard_port(self):
        for key in ('elastic','homeassistant','onvif','upnp','openhab','nodered','tasmota','shelly'):
            self.assertEqual(set(supported_actions(self.endpoint(39541,key))),{'tcp','check','http','httpx','screenshot'})
        self.assertEqual(set(supported_actions(self.endpoint(39541,'mqtt'))),{'tcp','check','identity'})
        self.assertEqual(set(supported_actions(self.endpoint(39541,'unknown'))),{'tcp','check'})
        self.assertEqual(set(supported_actions(self.endpoint(39541,'unknown','https'))),{'tcp','check','http','httpx','screenshot'})
        self.assertEqual(supported_actions(dict(self.endpoint(39541,'bacnet'),transport='udp')),['udp','identity'])
        self.assertIn('screenshot',supported_actions(self.endpoint(443,'')))
        self.assertEqual(set(supported_actions(self.endpoint(443,'modbus'))),{'check','tcp','identity'})

    def test_all_catalog_http_profiles_allow_nonstandard_http_captures(self):
        from leakscope.phase3.catalog import catalog
        for item in catalog():
            if item['http_capture']:
                with self.subTest(service=item['key']):
                    actions=supported_actions(self.endpoint(39541,item['key']))
                    self.assertTrue({'http','httpx','screenshot'}.issubset(actions))

    def test_recorder_preserves_udp_and_bounds_event_metadata(self):
        recorder=WireRecorder('udp')
        for _ in range(300):recorder.record('received',b'\xff')
        self.assertEqual(recorder.summary()['bytes_received'],300)
        self.assertLessEqual(len(recorder.document()['events']),256)
        self.assertEqual(recorder.document()['received']['hex'],'ff'*300)

    def test_tcp_blacklist_is_enforced_before_socket(self):
        with patch('leakscope.phase3.wire.socket.create_connection') as connect,self.assertRaisesMessage(ValueError,'blacklisted'):
            capture_endpoint(self.endpoint(1234,'unknown'),'tcp',dict(self.config,blacklist=['127.0.0.0/8']))
        connect.assert_not_called()

    def test_udp_one_explicit_datagram_has_actual_response(self):
        errors=[]
        class Handler(socketserver.BaseRequestHandler):
            def handle(handler):
                try:
                    request,sock=handler.request
                    self.assertEqual(request,b'\x00query\xff')
                    sock.sendto(b'\xffreply\x00',handler.client_address)
                except Exception as exc:errors.append(exc)
        with socketserver.UDPServer(('127.0.0.1',0),Handler) as server:
            server.timeout=2
            thread=threading.Thread(target=server.handle_request,daemon=True);thread.start()
            result=capture_endpoint(dict(self.endpoint(server.server_address[1],'unknown'),transport='udp'),'udp',dict(self.config,UDP_OPTIONS={'payload':'007175657279ff','encoding':'hex','timeout_seconds':1}))
            thread.join(2)
        if errors:raise errors[0]
        self.assertEqual(self.transcript(result)['received']['hex'],'ff7265706c7900')
        self.assertEqual(result['metadata']['wire_summary']['transport'],'udp')
        self.assertFalse(result['metadata']['wire_summary']['connection_established'])

    def test_udp_requires_payload_and_refuses_broadcast_or_multicast(self):
        endpoint=dict(self.endpoint(9999,'unknown'),transport='udp')
        with patch('leakscope.phase3.wire.socket.socket') as sock:
            for address in ('127.0.0.1','224.0.0.1','255.255.255.255','::','ff02::1','::ffff:224.0.0.1','::ffff:255.255.255.255'):
                options={} if address=='127.0.0.1' else {'payload':'probe'}
                with self.subTest(address=address),self.assertRaises(ValueError):
                    capture_endpoint(dict(endpoint,address=address),'udp',dict(self.config,UDP_OPTIONS=options))
        sock.assert_not_called()

    def test_tcp_timeout_retains_exchange_as_partial(self):
        def dialogue(sock):
            sock.sendall(b'partial banner');time.sleep(1.2)
        with tcp_fixture(dialogue) as port,self.assertRaises(CaptureEvidenceError) as caught:
            capture_endpoint(self.endpoint(port,'unknown'),'tcp',dict(self.config,TCP_OPTIONS={'timeout_seconds':1}))
        self.assertEqual(self.transcript(caught.exception.result)['received']['text'],'partial banner')
        self.assertEqual(caught.exception.result['metadata']['wire_summary']['termination'],'timeout')

    def test_tls_transcript_is_labeled_as_decrypted_application_exchange(self):
        root=Path(self.tmp.name);cert=root/'fixture.crt';key=root/'fixture.key'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(cert),'-days','1','-subj','/CN=localhost','-addext','subjectAltName=IP:127.0.0.1'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        server_context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);server_context.load_cert_chain(cert,key)
        client_context=ssl.create_default_context(cafile=str(cert))
        def dialogue(raw):
            with server_context.wrap_socket(raw,server_side=True) as sock:
                self.assertEqual(sock.recv(4096),b'hello TLS');sock.sendall(b'TLS response')
        with tcp_fixture(dialogue) as port,patch('leakscope.phase3.wire.ssl.create_default_context',return_value=client_context):
            result=capture_endpoint(self.endpoint(port,'unknown'),'tcp',dict(self.config,TCP_OPTIONS={'payload':'hello TLS','tls':True,'timeout_seconds':2}))
        summary=result['metadata']['wire_summary']
        self.assertTrue(summary['tls'])
        self.assertEqual(summary['evidence_layer'],'decrypted TLS application bytes')
        self.assertEqual(self.transcript(result)['sent']['text'],'hello TLS')
        self.assertEqual(self.transcript(result)['received']['text'],'TLS response')

    def test_dns_cannot_hold_exchange_past_its_deadline(self):
        def slow(*args,**kwargs):time.sleep(.2);return []
        started=time.monotonic()
        with patch('leakscope.phase3.wire.socket.getaddrinfo',side_effect=slow),self.assertRaises(TimeoutError):
            connect_tcp('slow.invalid',1234,started+.05)
        self.assertLess(time.monotonic()-started,.15)

    def test_one_connection_attempt_without_dns_answer_sweep(self):
        answers=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',1234)),(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.2',1234))]
        with patch('leakscope.phase3.wire.socket.getaddrinfo',return_value=answers),patch('leakscope.phase3.wire.socket.socket') as factory:
            factory.return_value.connect.side_effect=ConnectionRefusedError()
            with self.assertRaises(ConnectionRefusedError):connect_tcp('fixture.invalid',1234,time.monotonic()+1)
        self.assertEqual(factory.call_count,1)
        factory.return_value.connect.assert_called_once_with(('127.0.0.1',1234))

    def test_actual_curl_trace_on_nonstandard_catalog_http_port(self):
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                body=b'<title>Nonstandard fixture</title>'
                self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        with ThreadingHTTPServer(('127.0.0.1',0),Handler) as server:
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:result=capture_endpoint(self.endpoint(server.server_address[1],'elastic'),'http',self.config)
            finally:server.shutdown();thread.join(2)
        self.assertEqual(result['metadata']['title'],'Nonstandard fixture')
        trace=Path(next(item['path'] for item in result['artifacts'] if item['kind']=='http-trace')).read_bytes()
        self.assertIn(b'Send header',trace);self.assertIn(b'Recv header',trace)
        self.assertIn(b'GET / HTTP/1.1',trace)

    def test_mqtt_auth_refusal_still_proves_mqtt_without_login(self):
        def dialogue(sock):
            request=sock.recv(4096);self.assertEqual(request[0],0x10)
            sock.sendall(b'\x20\x02\x00\x05');self.assertEqual(sock.recv(1),b'')
        result=self.capture('mqtt',dialogue)
        self.assertTrue(result['metadata']['authentication_required'])
        self.assertEqual(result['verified_type'],'mqtt')

    def test_mqtt_and_rtsp_reject_wrong_framing_and_keep_evidence(self):
        for service,response in (('mqtt',b'\x20\x02\x01\x00'),('mqtt',b'\x20\x02\x00\x06'),('rtsp',b'HTTP/1.1 200 OK\r\nCSeq: 1\r\n\r\n'),('rtsp',b'RTSP/1.0 200 OK\r\nCSeq: 2\r\n\r\n')):
            def dialogue(sock):sock.recv(4096);sock.sendall(response)
            with self.subTest(service=service,response=response),self.assertRaises(CaptureEvidenceError) as caught:
                self.capture(service,dialogue)
            self.assertFalse(caught.exception.result['metadata']['identity_verified'])

    def test_silent_tcp_close_does_not_create_application_evidence(self):
        with tcp_fixture(lambda sock:None) as port,self.assertRaisesMessage(ValueError,'without application bytes'):
            capture_endpoint(self.endpoint(port,'unknown'),'tcp',self.config)
        self.assertFalse(list(Path(self.tmp.name).iterdir()))

    def test_failed_curl_preserves_actual_request_and_partial_response(self):
        def failed(argv,workdir,timeout):
            Path(argv[argv.index('--trace')+1]).write_bytes(b'=> Send header, 16 bytes (0x10)\n0000: 47 45 GET / HTTP/1.1\n<= Recv data, 7 bytes (0x7)\n0000: 70 61 partial\n')
            Path(argv[argv.index('--output')+1]).write_bytes(b'partial')
            raise ValueError('Capture tool exited with status 18')
        with patch('leakscope.phase3.capture._run',side_effect=failed),self.assertRaises(CaptureEvidenceError) as caught:
            capture_endpoint(self.endpoint(8080,'http'),'http',self.config)
        result=caught.exception.result
        self.assertTrue(result['partial'])
        self.assertFalse(result['metadata']['identity_verified'])
        self.assertEqual(result['metadata']['wire_summary']['bytes_received'],7)
        self.assertEqual({item['kind'] for item in result['artifacts']},{'http-trace','response'})

    def test_curl_connection_failure_info_is_not_exchange_evidence(self):
        def failed(argv,workdir,timeout):
            Path(argv[argv.index('--trace')+1]).write_bytes(b'== Info: Connection refused\n')
            raise ValueError('Capture tool exited with status 7')
        with patch('leakscope.phase3.capture._run',side_effect=failed),self.assertRaises(ValueError) as caught:
            capture_endpoint(self.endpoint(8080,'http'),'http',self.config)
        self.assertNotIsInstance(caught.exception,CaptureEvidenceError)
        self.assertFalse(list(Path(self.tmp.name).iterdir()))

    def test_blacklist_hostname_resolution_has_a_three_second_budget(self):
        from leakscope.phase3.capture import _enforce_blacklist
        started=time.monotonic()
        with patch('leakscope.phase3.capture._resolve',create=True,side_effect=TimeoutError) as resolve,patch('leakscope.phase3.capture.socket.getaddrinfo',return_value=[]),self.assertRaisesMessage(ValueError,'resolved'):
            _enforce_blacklist('fixture.invalid',1234,'tcp',{'blacklist':['10.0.0.0/8']})
        resolve.assert_called_once()
        self.assertGreaterEqual(resolve.call_args.args[3],started+2.9)
        self.assertLessEqual(resolve.call_args.args[3],started+3.1)
