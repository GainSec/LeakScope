import json
import os
import socketserver
import struct
import time
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from bson import BSON
from django.test import SimpleTestCase
from leakscope.phase3.capture import capture_endpoint
from leakscope.phase3.catalog import catalog_entry


@contextmanager
def tcp_fixture(dialogue):
    errors=[]
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(2)
            try:dialogue(self.request)
            except Exception as exc:errors.append(exc)
    with socketserver.ThreadingTCPServer(('127.0.0.1',0),Handler) as server:
        server.daemon_threads=True
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:yield server.server_address[1]
        finally:server.shutdown();thread.join(2)
    if errors:raise errors[0]


class NativeCaptureTests(SimpleTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.config={'CAPTURE_DIR':self.tmp.name,'CAPTURE_TIMEOUT':1}
    def endpoint(self,port,service,scheme=''):
        return {'id':1,'address':'127.0.0.1','port':port,'transport':'tcp','scheme':scheme,'service_type':service}
    def capture(self,service,dialogue):
        with tcp_fixture(dialogue) as port:return capture_endpoint(self.endpoint(port,service),'identity',self.config)

    def test_exact_reader_does_not_record_unrequested_trailing_metadata(self):
        from leakscope.phase3.native import Reader
        from leakscope.phase3.wire import RecordedSocket,WireRecorder
        import socket
        with tcp_fixture(lambda sock:sock.sendall(b'A'*77+b'SENSITIVE_PROJECT_METADATA')) as port:
            with socket.create_connection(('127.0.0.1',port),timeout=1) as raw:
                recorder=WireRecorder();deadline=time.monotonic()+1
                wire=RecordedSocket(raw,recorder,deadline)
                self.assertEqual(Reader(wire,deadline).exact(77),b'A'*77)
                self.assertEqual(bytes(recorder.received),b'A'*77)

    def test_redis_ping_and_server_info_do_not_read_keys(self):
        def dialogue(sock):
            self.assertEqual(sock.recv(1024),b'*1\r\n$4\r\nPING\r\n');sock.sendall(b'+PONG\r\n')
            self.assertEqual(sock.recv(1024),b'*2\r\n$4\r\nINFO\r\n$6\r\nserver\r\n')
            data=b'# Server\r\nredis_version:7.4.0\r\nos:Linux\r\n';sock.sendall(b'$'+str(len(data)).encode()+b'\r\n'+data+b'\r\n')
        result=self.capture('redis',dialogue)
        self.assertEqual(result['verified_type'],'redis')
        self.assertEqual(result['metadata']['server']['redis_version'],'7.4.0')
        artifact=Path(result['artifacts'][0]['path']);self.assertTrue(artifact.is_absolute());self.assertEqual(artifact.stat().st_mode&0o777,0o600)

    def test_mysql_greeting_never_logs_in(self):
        def dialogue(sock):
            payload=b'\x0a8.4.0\x00'+struct.pack('<I',123)+b'authsalt\x00'
            sock.sendall(len(payload).to_bytes(3,'little')+b'\x00'+payload)
        result=self.capture('mysql',dialogue)
        self.assertEqual(result['metadata']['version'],'8.4.0')
        self.assertEqual(result['verified_type'],'mysql')

    def test_postgresql_only_requests_ssl_capability(self):
        def dialogue(sock):
            self.assertEqual(sock.recv(8),struct.pack('!II',8,80877103));sock.sendall(b'S')
        result=self.capture('postgresql',dialogue)
        self.assertTrue(result['metadata']['ssl_supported'])

    def test_memcached_stats_is_bounded_metadata(self):
        def dialogue(sock):
            self.assertEqual(sock.recv(100),b'stats\r\n');sock.sendall(b'STAT version 1.6.30\r\nSTAT curr_items 12\r\nEND\r\n')
        result=self.capture('memcached',dialogue)
        self.assertEqual(result['metadata']['stats']['version'],'1.6.30')

    def test_ssh_banner_does_not_claim_sftp_subsystem(self):
        result=self.capture('sftp',lambda sock:sock.sendall(b'SSH-2.0-OpenSSH_9.9\r\n'))
        self.assertEqual(result['verified_type'],'ssh')
        self.assertFalse(result['metadata']['sftp_subsystem_verified'])

    def test_ftp_greeting(self):
        result=self.capture('ftp',lambda sock:sock.sendall(b'220 Example FTP server\r\n'))
        self.assertEqual(result['verified_type'],'ftp')

    def test_mongo_hello_uses_no_collection_commands(self):
        def dialogue(sock):
            header=sock.recv(16);length,request_id,_,op=struct.unpack('<iiii',header)
            self.assertEqual(op,2013);body=sock.recv(length-16)
            doc=BSON(body[5:]).decode();self.assertEqual(doc['hello'],1);self.assertEqual(doc['$db'],'admin')
            result=struct.pack('<I',0)+b'\x00'+BSON.encode({'ok':1.0,'isWritablePrimary':True,'maxWireVersion':25})
            sock.sendall(struct.pack('<iiii',16+len(result),2,request_id,2013)+result)
        result=self.capture('mongo',dialogue)
        self.assertEqual(result['verified_type'],'mongo')
        self.assertEqual(result['metadata']['hello']['maxWireVersion'],25)

    def test_modbus_read_device_identification_only(self):
        def dialogue(sock):
            self.assertEqual(sock.recv(11),bytes.fromhex('000100000005012b0e0100'))
            body=bytes([43,14,1,1,0,0,1,0,4])+b'Acme'
            sock.sendall(struct.pack('!HHHB',1,0,len(body)+1,1)+body)
        result=self.capture('modbus',dialogue)
        self.assertEqual(result['metadata']['objects'][0]['value'],'Acme')

    def test_opcua_hello_ack_does_not_create_a_session(self):
        def dialogue(sock):
            header=sock.recv(8);self.assertEqual(header[:4],b'HELF');length=struct.unpack('<I',header[4:])[0];sock.recv(length-8)
            sock.sendall(b'ACKF'+struct.pack('<I',28)+struct.pack('<IIIII',0,65536,65536,65536,1))
        result=self.capture('opcua',dialogue)
        self.assertEqual(result['verified_type'],'opcua')
        self.assertEqual(result['metadata']['receive_buffer'],65536)

    def test_ethernetip_list_identity_only(self):
        def dialogue(sock):
            header=sock.recv(24);self.assertEqual(struct.unpack('<H',header[:2])[0],0x63)
            item=struct.pack('<H',1)+b'\x00'*16+struct.pack('<HHHBBHI',1,12,34,2,3,0,123)+bytes([4])+b'Acme'+b'\x03'
            body=struct.pack('<HHH',1,0x0c,len(item))+item
            sock.sendall(struct.pack('<HHII8sI',0x63,len(body),0,0,header[12:20],0)+body)
        result=self.capture('ethernetip',dialogue)
        self.assertEqual(result['metadata']['product_name'],'Acme')

    def test_iec104_only_testfr_not_startdt_or_interrogation(self):
        def dialogue(sock):
            self.assertEqual(sock.recv(6),bytes.fromhex('680443000000'));sock.sendall(bytes.fromhex('680483000000'))
        result=self.capture('iec104',dialogue)
        self.assertEqual(result['metadata']['response'],'TESTFR confirmation')

    def test_nfs_only_rpc_null_version_three_without_mount_or_exports(self):
        def dialogue(sock):
            marker=struct.unpack('!I',sock.recv(4))[0]
            self.assertEqual(marker,0x80000028)
            request=sock.recv(40);words=struct.unpack('!10I',request)
            self.assertEqual(words[1:],(0,2,100003,3,0,0,0,0,0))
            response=struct.pack('!6I',words[0],1,0,0,0,0)
            sock.sendall(struct.pack('!I',0x80000000|len(response))+response)
            self.assertEqual(sock.recv(1),b'')
        result=self.capture('nfs',dialogue)
        self.assertEqual(result['verified_type'],'nfs')
        self.assertEqual(result['metadata']['nfs_version'],3)
        self.assertEqual(result['metadata']['procedure'],'NULL')

    def test_nfs_accepts_empty_rpc_fragment_with_bounded_fragment_count(self):
        def dialogue(sock):
            sock.recv(4);request=sock.recv(40);xid=struct.unpack('!I',request[:4])[0]
            reply=struct.pack('!6I',xid,1,0,0,0,0)
            sock.sendall(struct.pack('!II',0,0x80000000|len(reply))+reply)
        self.assertEqual(self.capture('nfs',dialogue)['verified_type'],'nfs')

    def test_smb2_negotiate_only_no_session_setup_or_tree_connect(self):
        import uuid
        server_guid=uuid.UUID('00112233-4455-6677-8899-aabbccddeeff')
        def dialogue(sock):
            size=int.from_bytes(sock.recv(4),'big');request=sock.recv(size)
            self.assertEqual(request[:4],b'\xfeSMB')
            self.assertEqual(struct.unpack('<H',request[12:14])[0],0)
            self.assertEqual(struct.unpack('<HH',request[64:68]),(36,4))
            self.assertEqual(struct.unpack('<4H',request[100:108]),(0x0202,0x0210,0x0300,0x0302))
            header=struct.pack('<4sHHIHHIIQIIQ16s',b'\xfeSMB',64,0,0,0,1,1,0,struct.unpack('<Q',request[24:32])[0],0,0,0,b'\x00'*16)
            body=struct.pack('<HHHH16sIIIIQQHHI',65,3,0x0302,0,server_guid.bytes_le,0x40,65536,65536,65536,0,0,0,0,0)
            response=header+body;sock.sendall(len(response).to_bytes(4,'big')+response)
            self.assertEqual(sock.recv(1),b'')
        result=self.capture('smb',dialogue)
        self.assertEqual(result['metadata']['dialect'],'3.0.2')
        self.assertTrue(result['metadata']['signing_required'])
        self.assertEqual(result['metadata']['server_guid'],str(server_guid))

    def test_rsync_reads_server_greeting_without_listing_modules(self):
        def dialogue(sock):
            sock.sendall(b'@RSYNCD: 31.0\n')
            self.assertEqual(sock.recv(1),b'')
        result=self.capture('rsync',dialogue)
        self.assertEqual(result['verified_type'],'rsync')
        self.assertEqual(result['metadata']['protocol_version'],'31.0')

    def test_s7_only_cotp_and_setup_communication_without_szl(self):
        def dialogue(sock):
            self.assertEqual(sock.recv(22),bytes.fromhex('0300001611e00000001400c1020100c2020102c0010a'))
            sock.sendall(bytes.fromhex('0300001611d00014000100c1020100c2020102c0010a'))
            self.assertEqual(sock.recv(25),bytes.fromhex('0300001902f08032010000000000080000f0000001000101e0'))
            sock.sendall(bytes.fromhex('0300001b02f080320300000000000800000000f0000001000101e0'))
            self.assertEqual(sock.recv(1),b'')
        result=self.capture('s7',dialogue)
        self.assertEqual(result['verified_type'],'s7')
        self.assertEqual(result['metadata']['pdu_length'],480)

    def test_s7_cotp_alone_does_not_confirm_an_s7_service(self):
        def dialogue(sock):
            sock.recv(22);sock.sendall(bytes.fromhex('0300001611d00014000100c1020100c2020102c0010a'))
            sock.recv(25)
            payload=b'\x02\xf0\x80'+b'\x62'*20
            sock.sendall(b'\x03\x00'+struct.pack('!H',len(payload)+4)+payload)
        with self.assertRaises(ValueError):self.capture('s7',dialogue)

    def test_smb_rejects_wrong_command_and_nfs_rejects_oversize_record(self):
        def smb(sock):
            size=int.from_bytes(sock.recv(4),'big');request=sock.recv(size)
            header=struct.pack('<4sHHIHHIIQIIQ16s',b'\xfeSMB',64,0,0,1,1,1,0,struct.unpack('<Q',request[24:32])[0],0,0,0,b'\x00'*16)
            response=header+b'\x00'*64;sock.sendall(len(response).to_bytes(4,'big')+response)
        with self.assertRaises(ValueError):self.capture('smb',smb)
        def nfs(sock):
            sock.recv(44);sock.sendall(struct.pack('!I',0x80010000))
        with self.assertRaisesMessage(ValueError,'limit'):self.capture('nfs',nfs)

    def test_bacnet_uses_unicast_who_is_and_parses_direct_i_am(self):
        errors=[]
        class Handler(socketserver.BaseRequestHandler):
            def handle(handler):
                request,sock=handler.request
                try:
                    self.assertEqual(request,bytes.fromhex('810a000801001008'))
                    payload=bytes.fromhex('01001000c4')+struct.pack('!I',(8<<22)|42)+bytes.fromhex('2205c49103217b')
                    sock.sendto(bytes.fromhex('810a')+struct.pack('!H',len(payload)+4)+payload,handler.client_address)
                except Exception as exc:errors.append(exc)
        with socketserver.UDPServer(('127.0.0.1',0),Handler) as server:
            thread=threading.Thread(target=server.handle_request,daemon=True);thread.start()
            endpoint=dict(self.endpoint(server.server_address[1],'bacnet'),transport='udp')
            result=capture_endpoint(endpoint,'identity',self.config);thread.join(2)
        if errors:raise errors[0]
        self.assertEqual(result['metadata']['device_instance'],42)
        self.assertEqual(result['metadata']['vendor_id'],123)
        transcript=next(item for item in result['artifacts'] if item['kind']=='wire-transcript')
        wire=json.loads(Path(transcript['path']).read_text())
        self.assertEqual(wire['sent']['hex'],'810a000801001008')
        self.assertTrue(wire['received']['hex'].startswith('810a'))
        self.assertEqual(wire['summary']['transport'],'udp')

    def test_native_read_deadline_and_length_limit(self):
        def silent(sock):time.sleep(.2)
        with tcp_fixture(silent) as port:
            with self.assertRaisesMessage(ValueError,'timed out'):
                capture_endpoint(self.endpoint(port,'mysql'),'identity',dict(self.config,CAPTURE_TIMEOUT=.05))
        def oversized(sock):sock.sendall(b'\xff\xff\x7f\x00')
        with self.assertRaises(ValueError):self.capture('mysql',oversized)

    def test_bad_frames_do_not_confirm_a_protocol(self):
        for service in ('mysql','postgresql','redis','memcached','mongo','modbus','opcua','ethernetip','iec104','ssh','ftp','nfs','smb','rsync','s7'):
            with self.subTest(service=service):
                def dialogue(sock):
                    try:sock.sendall(b'not a valid protocol\r\n')
                    except BrokenPipeError:pass
                with self.assertRaises(ValueError):self.capture(service,dialogue)

    def test_unsupported_native_and_invalid_target_fail_before_connection(self):
        for endpoint,action in [(self.endpoint(502,'modbus'),'http'),(self.endpoint(1433,'firebird'),'identity'),(dict(self.endpoint(80,'http'),address='host; touch /tmp/x'),'http'),(dict(self.endpoint(80,'http'),port=0),'http')]:
            with self.assertRaises(ValueError):capture_endpoint(endpoint,action,self.config)

    def test_capabilities_only_claim_implemented_operations(self):
        for key in ('redis','mysql','postgresql','mongo','modbus','opcua','ethernetip','iec104','bacnet','nfs','smb','rsync','s7'):
            self.assertTrue(catalog_entry(key)['identity'])
            self.assertFalse(catalog_entry(key)['inspection'])
            self.assertFalse(catalog_entry(key)['export'])
        self.assertTrue(catalog_entry('dnp3')['identity'])
        self.assertFalse(catalog_entry('plc')['identity'])

    def test_native_url_schemes_are_accepted_for_identity_only(self):
        for service,scheme,greeting in [('ftp','ftp',b'220 FTP fixture\r\n'),('ssh','ssh',b'SSH-2.0-fixture\r\n')]:
            with self.subTest(service=service):
                with tcp_fixture(lambda sock:sock.sendall(greeting)) as port:
                    result=capture_endpoint(self.endpoint(port,service,scheme),'identity',self.config)
                self.assertEqual(result['verified_type'],service)
                with self.assertRaises(ValueError):capture_endpoint(self.endpoint(port,service,scheme),'http',self.config)


class HttpCaptureTests(SimpleTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.config={'CAPTURE_DIR':self.tmp.name}
        self.endpoint={'id':1,'address':'::1','port':8080,'scheme':'http','service_type':'http','transport':'tcp'}

    @patch('leakscope.phase3.capture._run')
    def test_curl_argv_is_fixed_ipv6_safe_and_artifacts_private(self,run):
        def fake(argv,workdir,timeout):
            self.assertEqual(argv[0],'curl');self.assertIn('--disable',argv)
            self.assertNotIn('--location',argv)
            self.assertEqual(argv[-1],'http://[::1]:8080/')
            Path(argv[argv.index('--trace')+1]).write_bytes(b'=> Send header\nGET / HTTP/1.1\n<= Recv header\nHTTP/1.1 200 OK\n')
            Path(argv[argv.index('--output')+1]).write_bytes(b'hello')
            Path(argv[argv.index('--dump-header')+1]).write_bytes(b'HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\n')
            return json.dumps({'http_code':200,'content_type':'text/plain','url_effective':argv[-1]}).encode()
        run.side_effect=fake
        result=capture_endpoint(self.endpoint,'http',self.config)
        self.assertEqual(result['metadata']['status'],200)
        self.assertEqual(len(result['artifacts']),4)
        trace=next(item for item in result['artifacts'] if item['kind']=='http-trace')
        self.assertIn(b'GET / HTTP/1.1',Path(trace['path']).read_bytes())
        for item in result['artifacts']:self.assertEqual(Path(item['path']).stat().st_mode&0o777,0o600)

    @patch('leakscope.phase3.capture._run')
    def test_httpx_probes_only_the_captured_target_without_expansion(self,run):
        run.return_value=json.dumps({'url':'http://[::1]:8080/','status_code':200,'title':'Acme','tech':['Nginx'],'webserver':'nginx','favicon':'-1234','hash':{'body_sha256':'a'*64}}).encode()+b'\n'
        result=capture_endpoint(self.endpoint,'httpx',self.config)
        argv=run.call_args.args[0]
        self.assertIn('-no-fallback-scheme',argv)
        self.assertNotIn('-tls-probe',argv);self.assertNotIn('-csp-probe',argv)
        self.assertEqual(result['metadata']['status'],200)
        self.assertIn('-favicon',argv);self.assertIn('-hash',argv)
        self.assertEqual(argv[argv.index('-hash')+1],'sha256')
        self.assertEqual(result['metadata']['server'],'nginx')
        self.assertEqual(result['metadata']['favicon_mmh3'],'-1234')
        self.assertEqual(result['metadata']['body_sha256'],'a'*64)
        self.assertNotIn('favicon_sha256',result['metadata'])

    @patch('leakscope.phase3.capture._run')
    def test_curl_preserves_actual_server_title_and_body_hash(self,run):
        body=b'<html><title>Acme Console</title></html>'
        def fake(argv,workdir,timeout):
            Path(argv[argv.index('--trace')+1]).write_bytes(b'=> Send header\nGET / HTTP/1.1\n<= Recv header\nHTTP/1.1 200 OK\n')
            Path(argv[argv.index('--output')+1]).write_bytes(body)
            Path(argv[argv.index('--dump-header')+1]).write_bytes(b'HTTP/1.1 200 OK\r\nServer: AcmeHTTP/1.2\r\nSet-Cookie: secret-cookie\r\n\r\n')
            return json.dumps({'http_code':200,'content_type':'text/html'}).encode()
        run.side_effect=fake
        result=capture_endpoint(self.endpoint,'http',self.config)
        self.assertEqual(result['metadata']['server'],'AcmeHTTP/1.2')
        self.assertEqual(result['metadata']['title'],'Acme Console')
        self.assertNotIn('secret-cookie',json.dumps(result['metadata']))
        import hashlib
        self.assertEqual(result['metadata']['body_sha256'],hashlib.sha256(body).hexdigest())

    @patch('leakscope.phase3.capture._run')
    def test_httpx_failed_rows_are_not_successful_captures(self,run):
        run.return_value=json.dumps({'url':'http://[::1]:8080/','failed':True,'status_code':0}).encode()+b'\n'
        with self.assertRaises(ValueError):capture_endpoint(self.endpoint,'httpx',self.config)

    @patch('leakscope.phase3.capture._run')
    def test_oversized_body_never_becomes_an_artifact(self,run):
        def fake(argv,workdir,timeout):
            Path(argv[argv.index('--output')+1]).write_bytes(b'x'*(2*1024*1024+1))
            return json.dumps({'http_code':200}).encode()
        run.side_effect=fake
        with self.assertRaisesMessage(ValueError,'limit'):capture_endpoint(self.endpoint,'http',self.config)
        self.assertFalse(list(Path(self.tmp.name).glob('*.body')))

    def test_child_environment_excludes_application_secrets(self):
        from leakscope.phase3.capture import _child_env
        with patch.dict(os.environ,{'SHODAN_API_KEY':'private','DATABASE_URL':'postgres://secret','OPENAI_API_KEY':'private','HTTP_PROXY':'http://credential-proxy'}):
            environment=_child_env(Path(self.tmp.name))
        for key in ('SHODAN_API_KEY','DATABASE_URL','OPENAI_API_KEY','HTTP_PROXY'):self.assertNotIn(key,environment)

    @patch('leakscope.phase3.capture._run')
    @patch('leakscope.phase3.capture.socket.getaddrinfo')
    def test_blacklisted_hostname_and_resolved_cidr_are_blocked_before_tools(self,resolve,run):
        endpoint=dict(self.endpoint,address='blocked.example')
        with self.assertRaisesMessage(ValueError,'blacklisted'):
            capture_endpoint(endpoint,'http',dict(self.config,blacklist=['BLOCKED.EXAMPLE.']))
        resolve.assert_not_called()
        resolve.return_value=[(2,1,6,'',('192.0.2.4',8080)),(2,1,6,'',('198.51.100.9',8080))]
        with self.assertRaisesMessage(ValueError,'blacklisted'):
            capture_endpoint(endpoint,'http',dict(self.config,blacklist=['198.51.100.0/24']))
        run.assert_not_called()

    @patch('leakscope.phase3.capture._run')
    def test_ipv4_mapped_blacklist_is_honored_without_blanket_private_deny(self,run):
        endpoint=dict(self.endpoint,address='::ffff:192.0.2.4')
        with self.assertRaisesMessage(ValueError,'blacklisted'):
            capture_endpoint(endpoint,'http',dict(self.config,blacklist=['192.0.2.0/24']))
        run.assert_not_called()

    @patch('leakscope.phase3.capture._run')
    def test_selected_resource_path_is_encoded_without_changing_origin(self,run):
        def fake(argv,workdir,timeout):
            self.assertEqual(argv[-1],'http://[::1]:8080/admin/Acme%20console')
            Path(argv[argv.index('--trace')+1]).write_bytes(b'=> Send header\nGET /admin/Acme%20console HTTP/1.1\n<= Recv header\nHTTP/1.1 200 OK\n')
            Path(argv[argv.index('--output')+1]).write_bytes(b'fixture')
            Path(argv[argv.index('--dump-header')+1]).write_bytes(b'HTTP/1.1 200 OK\r\n\r\n')
            return json.dumps({'http_code':200,'content_type':'text/plain'}).encode()
        run.side_effect=fake
        result=capture_endpoint(dict(self.endpoint,path='/admin/Acme console'),'http',self.config)
        self.assertTrue(result['metadata']['url'].endswith('/admin/Acme%20console'))

    @patch('leakscope.phase3.capture._run')
    def test_ambiguous_or_nonpath_resource_is_rejected_before_tool(self,run):
        for path in ('https://evil.example/','//evil.example/','/admin?token=secret','/admin#section','/admin\r\nHeader: value','/x%0d%0aHeader','/bad%path','/bad%2'):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):capture_endpoint(dict(self.endpoint,path=path),'http',self.config)
        run.assert_not_called()

    @patch('leakscope.phase3.capture._run')
    def test_screenshot_uses_fixed_chrome_and_one_target(self,run):
        def fake(argv,workdir,timeout):
            self.assertEqual(argv[:3],['gowitness','scan','single'])
            self.assertIn('/usr/local/bin/leakscope-chromium',argv)
            Path(argv[argv.index('--write-jsonl-file')+1]).write_text(json.dumps({'url':'http://[::1]:8080/','final_url':'http://[::1]:8080/'})+'\n')
            directory=Path(argv[argv.index('--screenshot-path')+1]);(directory/'fixture.png').write_bytes(b'\x89PNG\r\n\x1a\nfixture')
            return b''
        run.side_effect=fake
        result=capture_endpoint(self.endpoint,'screenshot',dict(self.config,SCREENSHOT_OPTIONS={'backend':'gowitness'}))
        self.assertEqual(len(result['artifacts']),2)
        self.assertEqual(result['artifacts'][0]['mime'],'image/png')


class ScreenshotProxyTests(SimpleTestCase):
    def test_proxy_blocks_other_ports_without_connecting_to_them(self):
        import requests
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        from leakscope.phase3.browser_proxy import same_origin_proxy
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                self.send_response(200);self.end_headers();self.wfile.write(b'fixture')
        with ThreadingHTTPServer(('127.0.0.1',0),Handler) as server:
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            with same_origin_proxy('127.0.0.1',server.server_address[1]) as (proxy,state):
                with requests.Session() as session:
                    session.trust_env=False
                    allowed=session.get(f'http://127.0.0.1:{server.server_address[1]}/',proxies={'http':proxy},timeout=2)
                    denied=session.get('http://127.0.0.1:1/',proxies={'http':proxy},timeout=2)
                self.assertEqual(allowed.content,b'fixture')
                self.assertEqual(denied.status_code,403)
                self.assertEqual(state['blocked'],1)
            server.shutdown();thread.join(2)


@unittest.skipUnless(os.environ.get('LEAKSCOPE_CAPTURE_TESTS')=='1','real capture-tool checks are opt-in inside the capture image')
class RealCaptureToolTests(SimpleTestCase):
    def test_actual_installed_tools_against_local_fixture(self):
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                body=b'<!doctype html><html><head><title>LeakScope capture fixture</title><link rel="icon" href="/favicon.ico"></head><body><h1>Verified local fixture</h1></body></html>'
                content_type='text/html'
                if self.path!='/admin/console' and self.path!='/favicon.ico':body=b'<html><title>Wrong resource</title></html>'
                if self.path=='/favicon.ico':
                    import base64
                    body=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jZ1kAAAAASUVORK5CYII=');content_type='image/png'
                self.send_response(200);self.send_header('Content-Type',content_type);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        with tempfile.TemporaryDirectory() as directory,ThreadingHTTPServer(('127.0.0.1',0),Handler) as server:
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            endpoint={'address':'127.0.0.1','port':server.server_address[1],'scheme':'http','transport':'tcp','service_type':'http','path':'/admin/console'}
            try:
                for action in ('http','httpx','screenshot'):
                    with self.subTest(action=action):
                        result=capture_endpoint(endpoint,action,{'CAPTURE_DIR':directory,'SCREENSHOT_OPTIONS':{'backend':'gowitness'}})
                        self.assertTrue(result['artifacts'])
                        self.assertNotEqual(result['metadata']['tool_version'],'unreported')
                        self.assertTrue(result['metadata']['url'].endswith('/admin/console'))
                        if action in ('http','httpx'):self.assertEqual(result['metadata']['title'],'LeakScope capture fixture')
                        if action=='httpx':
                            self.assertTrue(result['metadata']['server'])
                            self.assertEqual(len(result['metadata']['body_sha256']),64)
                            self.assertIn('favicon_mmh3',result['metadata'])
                        for artifact in result['artifacts']:
                            path=Path(artifact['path']);self.assertGreater(path.stat().st_size,0)
                            self.assertEqual(path.stat().st_mode&0o777,0o600)
            finally:server.shutdown();thread.join(2)
