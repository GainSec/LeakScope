"""Loopback-only HTTP/2 peers exercise the gRPC identification boundary."""
import json
import errno
import socket
import ssl
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase
from h2.config import H2Configuration
from h2.connection import H2Connection
from h2.events import DataReceived, RequestReceived, StreamEnded

from leakscope.phase3.grpc_native import identify_grpc
from leakscope.phase3.capture import CaptureEvidenceError, capture_endpoint
from leakscope.phase3.wire import RecordedSocket, WireRecorder


class GrpcIdentityTests(SimpleTestCase):
    @contextmanager
    def peer(self, *, content_type='application/grpc', status='0',
             body=b'\x00\x00\x00\x00\x02\x08\x01', fragmented=False,
             trailers_only=False, extra_headers=(), close_early=False, reset=False,
             tcp=False, tls_context=None, expect_no_request=False, extension_frames=0):
        listener=None
        if tcp:
            listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(1);listener.settimeout(2)
            client=None;server=None;port=listener.getsockname()[1]
        else:client, server = socket.socketpair()
        errors=[]; request={}
        def serve():
            nonlocal server
            try:
                if listener:server,_=listener.accept()
                server.settimeout(2)
                if tls_context:server=tls_context.wrap_socket(server,server_side=True)
                connection=H2Connection(config=H2Configuration(client_side=False, header_encoding='utf-8'))
                connection.initiate_connection()
                server.sendall(connection.data_to_send())
                ended=False
                while not ended:
                    data=server.recv(4096)
                    if not data:
                        if expect_no_request:return
                        raise AssertionError('Client closed before sending its request')
                    for event in connection.receive_data(data):
                        if isinstance(event, RequestReceived):request['headers']=dict(event.headers)
                        elif isinstance(event, DataReceived):request['body']=request.get('body',b'')+event.data
                        elif isinstance(event, StreamEnded):ended=True
                if close_early:return
                if reset:
                    connection.reset_stream(1)
                    server.sendall(connection.data_to_send())
                    return
                headers=[(':status','200'),('content-type',content_type),*extra_headers]
                if trailers_only:
                    if status is not None:headers.append(('grpc-status',status))
                    connection.send_headers(1,headers,end_stream=True)
                else:
                    connection.send_headers(1,headers)
                    if body:connection.send_data(1,body)
                    trailers=[('grpc-status',status)] if status is not None else [('x-fixture','no-status')]
                    connection.send_headers(1,trailers,end_stream=True)
                # Unknown frame types are ignored by HTTP/2 and require no
                # reply. They exercise the receive budget without ACK traffic
                # filling either socket's send buffer. Each frame is 16 KiB.
                extension=b'\x00\x40\x00\xf0\x00\x00\x00\x00\x00'+b'x'*16384
                wire=extension*extension_frames+connection.data_to_send()
                if fragmented:
                    for value in wire:server.sendall(bytes([value]))
                else:server.sendall(wire)
            except (BrokenPipeError,ConnectionResetError):pass
            except ssl.SSLError as exc:
                if not expect_no_request:errors.append(exc)
            except OSError as exc:
                if exc.errno != errno.ENOTCONN:errors.append(exc)
            except Exception as exc:errors.append(exc)
            finally:
                if server is not None:server.close()
        thread=threading.Thread(target=serve,daemon=True);thread.start()
        try:yield port if tcp else client,request
        finally:
            if client is not None:client.close()
            if listener is not None:listener.close()
            thread.join(3)
            if thread.is_alive():raise AssertionError('Fixture did not finish')
            if errors:raise errors[0]

    def probe(self, **response):
        recorder=WireRecorder()
        with self.peer(**response) as (sock,request):
            result=identify_grpc(RecordedSocket(sock,recorder),time.monotonic()+1,'127.0.0.1',52367)
        return result,recorder,request

    def test_health_check_identifies_grpc_on_an_arbitrary_port_and_keeps_wire(self):
        (metadata,verified),recorder,request=self.probe(fragmented=True)
        self.assertEqual(verified,'grpc')
        self.assertEqual(metadata['health_status'],'SERVING')
        self.assertEqual(metadata['grpc_status'],0)
        self.assertEqual(request['headers'][':authority'],'127.0.0.1:52367')
        self.assertEqual(request['headers'][':path'],'/grpc.health.v1.Health/Check')
        self.assertEqual(request['headers']['te'],'trailers')
        self.assertEqual(request['body'],b'\x00\x00\x00\x00\x00')
        self.assertTrue(bytes(recorder.sent).startswith(b'PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n'))
        self.assertGreater(len(recorder.received),20)
        self.assertNotIn('product',metadata)

    def test_unimplemented_and_authentication_trailers_still_identify_protocol(self):
        for status in ('12','16','7'):
            with self.subTest(status=status):
                (metadata,verified),_,_=self.probe(status=status,body=b'',trailers_only=True)
                self.assertEqual(verified,'grpc')
                self.assertEqual(metadata['grpc_status'],int(status))
                self.assertFalse(metadata['health_check_succeeded'])
                self.assertNotIn('health_status',metadata)
                self.assertEqual(metadata['authentication_required'],status=='16')
                self.assertIs(metadata['health_implemented'],False if status=='12' else None)

    def test_empty_health_response_represents_proto_default_unknown(self):
        (metadata,_),_,_=self.probe(body=b'\x00\x00\x00\x00\x00')
        self.assertEqual(metadata['health_status'],'UNKNOWN')

    def test_http2_or_grpc_web_is_not_native_grpc(self):
        for content_type in ('application/json','application/grpc-web+proto','text/html'):
            with self.subTest(content_type=content_type),self.assertRaises(ValueError):
                self.probe(content_type=content_type)

    def test_missing_or_invalid_grpc_status_does_not_verify_protocol(self):
        for status in (None,'-1','17','bad','00'):
            with self.subTest(status=status),self.assertRaises(ValueError):
                self.probe(status=status)

    def test_success_requires_exactly_one_valid_uncompressed_health_message(self):
        cases=(b'',b'\x00\x00\x00\x00\x03\x08\x01',
               b'\x01\x00\x00\x00\x02\x08\x01',
               b'\x00\x00\x00\x00\x02\x08\x01'*2,
               b'\x00\x00\x00\x00\x02\x08\x80')
        for body in cases:
            with self.subTest(body=body.hex()),self.assertRaises(ValueError):self.probe(body=body)

    def test_header_decompression_limit_rejects_oversized_metadata(self):
        with self.assertRaises(ValueError):self.probe(extra_headers=(('x-too-large','x'*20000),))

    def test_stream_reset_does_not_verify_identity(self):
        with self.assertRaises(ValueError):self.probe(reset=True)

    def test_complete_health_response_is_retained_when_peer_closes_before_settings_ack(self):
        message=b'\x08\x01\x12\xfa\x1f'+b'x'*4090
        body=b'\x00'+len(message).to_bytes(4,'big')+message
        (metadata,_),_,_=self.probe(body=body)
        self.assertEqual(metadata['health_status'],'SERVING')

    def test_metadata_body_is_bounded_before_protobuf_decoding(self):
        body=b'\x00'+(4097).to_bytes(4,'big')+b'x'*4097
        with self.assertRaisesMessage(ValueError,'metadata limit'):self.probe(body=body)

    def test_duplicate_content_type_does_not_verify_identity(self):
        with self.assertRaises(ValueError):self.probe(extra_headers=(('content-type','application/grpc'),))

    def test_eof_and_expired_deadline_cannot_verify_identity(self):
        with self.assertRaises(ValueError):self.probe(close_early=True)
        client,server=socket.socketpair()
        try:
            with self.assertRaises(ValueError):identify_grpc(client,time.monotonic()-1,'127.0.0.1',52367)
            server.setblocking(False)
            with self.assertRaises(BlockingIOError):server.recv(1)
        finally:client.close();server.close()

    def test_aggregate_wire_budget_bounds_ignored_http2_extension_frames(self):
        recorder=WireRecorder()
        with self.peer(extension_frames=5) as (sock,_),self.assertRaisesMessage(ValueError,'64 KiB'):
            identify_grpc(RecordedSocket(sock,recorder),time.monotonic()+1,'127.0.0.1',52367)
        self.assertLessEqual(len(recorder.sent)+len(recorder.received),65536)

    def _endpoint(self,port,scheme='grpc'):
        return {'address':'127.0.0.1','port':port,'transport':'tcp','scheme':scheme,'service_type':'grpc'}

    def _tls_contexts(self,protocol='h2'):
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
        cert=Path(directory.name)/'fixture.crt';key=Path(directory.name)/'fixture.key'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),
            '-out',str(cert),'-days','1','-subj','/CN=localhost','-addext','subjectAltName=IP:127.0.0.1'],
            check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        server=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);server.load_cert_chain(cert,key)
        server.set_alpn_protocols([protocol])
        return server,ssl.create_default_context(cafile=str(cert))

    def test_capture_action_uses_actual_tcp_and_saves_raw_request_and_response(self):
        with tempfile.TemporaryDirectory() as directory,self.peer(tcp=True) as (port,request):
            result=capture_endpoint(self._endpoint(port),'identity',{'CAPTURE_DIR':directory})
            self.assertEqual(result['verified_type'],'grpc')
            self.assertTrue(result['metadata']['identity_verified'])
            self.assertEqual(result['metadata']['health_status'],'SERVING')
            artifacts={item['kind']:Path(item['path']).read_bytes() for item in result['artifacts']}
            self.assertEqual(set(artifacts),{'identity','wire-transcript','wire-request','wire-response'})
            self.assertTrue(artifacts['wire-request'].startswith(b'PRI * HTTP/2.0'))
            wire=json.loads(artifacts['wire-transcript'])
            self.assertEqual(wire['received']['hex'],artifacts['wire-response'].hex())
            self.assertEqual(request['body'],b'\x00\x00\x00\x00\x00')

    def test_capture_failure_retains_real_http2_exchange_without_verified_type(self):
        with tempfile.TemporaryDirectory() as directory,self.peer(tcp=True,content_type='application/json') as (port,_):
            with self.assertRaises(CaptureEvidenceError) as caught:
                capture_endpoint(self._endpoint(port),'identity',{'CAPTURE_DIR':directory})
            result=caught.exception.result
            self.assertTrue(result['partial']);self.assertFalse(result['metadata']['identity_verified'])
            self.assertNotIn('verified_type',result)
            self.assertEqual({item['kind'] for item in result['artifacts']},{'wire-transcript','wire-request','wire-response'})

    def test_tls_capture_verifies_certificate_and_alpn_and_labels_decrypted_wire(self):
        server,client=self._tls_contexts()
        with tempfile.TemporaryDirectory() as directory,self.peer(tcp=True,tls_context=server) as (port,request):
            with patch('leakscope.phase3.native.ssl.create_default_context',return_value=client):
                result=capture_endpoint(self._endpoint(port,'grpcs'),'identity',{'CAPTURE_DIR':directory})
            summary=result['metadata']['wire_summary']
            self.assertTrue(summary['tls'])
            self.assertEqual(summary['evidence_layer'],'decrypted TLS application bytes')
            self.assertEqual(request['headers'][':scheme'],'https')
            self.assertEqual(result['verified_type'],'grpc')

    def test_untrusted_tls_and_missing_h2_alpn_do_not_send_an_rpc(self):
        for trusted,protocol in ((False,'h2'),(True,'http/1.1')):
            server,client=self._tls_contexts(protocol)
            if not trusted:client=ssl.create_default_context()
            with self.subTest(trusted=trusted,protocol=protocol),tempfile.TemporaryDirectory() as directory:
                with self.peer(tcp=True,tls_context=server,expect_no_request=True) as (port,request):
                    with patch('leakscope.phase3.native.ssl.create_default_context',return_value=client),self.assertRaises(ValueError):
                        capture_endpoint(self._endpoint(port,'grpcs'),'identity',{'CAPTURE_DIR':directory})
                self.assertEqual(request,{})
                self.assertFalse(list(Path(directory).iterdir()))
