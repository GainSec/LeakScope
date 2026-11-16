"""Loopback wire fixtures for safe OT identity requests on arbitrary ports."""
import socket
import socketserver
import struct
import threading
import time
from contextlib import contextmanager

from django.test import SimpleTestCase
from leakscope.phase3.native import Reader
from leakscope.phase3.wire import RecordedSocket, WireRecorder
try:
    from leakscope.phase3 import native_ot_extended as ot
except ImportError:
    ot = None


def exact(sock, size):
    out = b''
    while len(out) < size:
        chunk = sock.recv(size-len(out))
        if not chunk: raise AssertionError('Unexpected fixture EOF')
        out += chunk
    return out


@contextmanager
def peer(dialogue, udp=False):
    errors = []
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            try: dialogue(self.request, self.client_address)
            except Exception as exc: errors.append(exc)
    cls = socketserver.ThreadingUDPServer if udp else socketserver.ThreadingTCPServer
    with cls(('127.0.0.1', 0), Handler) as server:
        server.daemon_threads = False
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
        thread.start()
        try: yield server.server_address[1]
        finally:
            server.shutdown(); thread.join(2); server.server_close()
            if errors: raise errors[0]


def crc(data):
    value = 0
    for byte in data:
        value ^= byte
        for _ in range(8): value = (value >> 1) ^ (0xa6bc if value & 1 else 0)
    return struct.pack('<H', value ^ 0xffff)


def tl(tag, data):
    return bytes([tag]) + (bytes([len(data)]) if len(data) < 128 else b'\x81'+bytes([len(data)])) + data


def tpkt(data): return b'\x03\x00'+struct.pack('!H',len(data)+4)+data


def mms_response(context=bytes.fromhex('28ca220203'), result=0, initiate_tag=0xa9, pdu=b'\x20\x00'):
    detail=tl(0x80,b'\x01')+tl(0x81,b'\x05\xf1\x00')+tl(0x82,b'\x03'+b'\x00'*11)
    initiate=tl(initiate_tag,tl(0x80,pdu)+tl(0x81,b'\x01')+tl(0x82,b'\x01')+tl(0x83,b'\x05')+tl(0xa4,detail))
    acse=tl(0x61,tl(0xa1,tl(6,context))+tl(0xa2,tl(2,bytes([result])))+tl(0xa3,tl(0xa1,tl(2,b'\x00')))+tl(0xbe,tl(0x28,tl(2,b'\x03')+tl(0xa0,initiate))))
    data=tl(0x61,tl(0x30,tl(2,b'\x01')+tl(0xa0,acse)))
    pres=tl(0x31,tl(0xa0,tl(0x80,b'\x01'))+tl(0xa2,tl(0xa5,tl(0x30,tl(0x80,b'\x00')+tl(0x81,b'\x51\x01'))*2)+data))
    spdu=b'\x0e'+bytes([len(pres)+2])+b'\xc1'+bytes([len(pres)])+pres
    return tpkt(b'\x02\xf0\x80'+spdu)


def coap_stream(code, token=b'', body=b''):
    length=len(body)
    if length<13:header=bytes([(length<<4)|len(token)])
    elif length<269:header=bytes([0xd0|len(token),length-13])
    elif length<65805:header=bytes([0xe0|len(token)])+struct.pack('!H',length-269)
    else:header=bytes([0xf0|len(token)])+struct.pack('!I',length-65805)
    return header+bytes([code])+token+body


def read_coap_stream(sock):
    header=exact(sock,1)[0];length=header>>4;tkl=header&15
    if length>=13:
        count={13:1,14:2,15:4}[length]
        length=int.from_bytes(exact(sock,count),'big')+{13:13,14:269,15:65805}[length]
    return exact(sock,1)[0],exact(sock,tkl),exact(sock,length)


class OTIdentityTests(SimpleTestCase):
    def require(self):
        self.assertIsNotNone(ot, 'The extended OT native adapters must exist')

    def tcp(self, service, dialogue):
        self.require(); recorder=WireRecorder()
        with peer(dialogue) as port:
            with socket.create_connection(('127.0.0.1',port),timeout=1) as raw:
                deadline=time.monotonic()+1
                wire=RecordedSocket(raw,recorder,deadline)
                metadata,verified=ot.TCP_ADAPTERS[service](wire,Reader(wire,deadline),'127.0.0.1',port)
        return metadata,verified,recorder

    def udp(self, service, response):
        self.require(); recorder=WireRecorder('udp')
        def dialogue(request,address):
            data,sock=request
            sock.sendto(response(data),address)
        with peer(dialogue,udp=True) as port:
            metadata,verified=ot.UDP_ADAPTERS[service]('127.0.0.1',port,1,recorder)
        return metadata,verified,recorder

    def test_adapter_contract_excludes_unsafe_universal_handshakes(self):
        self.require()
        self.assertTrue({'dnp3','ads','fox','iec61850','proconos','codesys','pcworx'} <= ot.TCP_ADAPTERS.keys())
        self.assertTrue({'fins','melsec','hartip','lantronix-config','vertx-edge','coap'} <= ot.UDP_ADAPTERS.keys())
        self.assertTrue({'smux','cipio','plc','lantronix','srtp'} <= ot.UNSUPPORTED_REASONS.keys())
        self.assertNotIn('smux',ot.TCP_ADAPTERS)
        self.assertIs(ot.TCP_ADAPTERS['mms'],ot.TCP_ADAPTERS['iec61850'])

    def test_dnp3_link_status_only_validates_crc_and_addresses(self):
        def dialogue(sock,_):
            request=exact(sock,10)
            self.assertEqual(request[:8],bytes.fromhex('056405c901000004'))
            self.assertEqual(request[8:],crc(request[:8]))
            header=bytes.fromhex('0564050b00040100')
            sock.sendall(header+crc(header))
            self.assertEqual(sock.recv(1),b'')
        metadata,verified,rec=self.tcp('dnp3',dialogue)
        self.assertEqual(verified,'dnp3');self.assertEqual(metadata['operation'],'REQUEST_LINK_STATUS')
        self.assertEqual(len(rec.sent),10);self.assertEqual(len(rec.received),10)

    def test_dnp3_saved_master_role_link_status_and_all_dir_dfc_combinations(self):
        # Saved response, with no endpoint identifier: secondary link status
        # from a master-role peer. DIR does not mean primary/secondary function.
        saved=bytes.fromhex('0564058b000401002ef7')
        self.assertEqual(saved[8:],crc(saved[:8]))
        for control in (0x0b,0x1b,0x8b,0x9b):
            header=saved[:3]+bytes([control])+saved[4:8]
            reply=header+crc(header)
            def dialogue(sock,_,reply=reply):exact(sock,10);sock.sendall(reply)
            with self.subTest(control=hex(control)):
                metadata,verified,rec=self.tcp('dnp3',dialogue)
                self.assertEqual(verified,'dnp3')
                self.assertEqual(metadata['target_role'],'master' if control&0x80 else 'outstation')
                self.assertEqual(metadata['target_link_address'],1)
                self.assertEqual(metadata['source_link_address'],1024)
                self.assertEqual(metadata['receiver_buffer_full'],bool(control&0x10))
                self.assertNotIn('outstation_address',metadata)
                self.assertEqual(len(rec.sent),10);self.assertEqual(len(rec.received),10)

    def test_dnp3_master_role_keeps_prm_fcb_address_crc_and_length_checks(self):
        saved=bytes.fromhex('0564058b000401002ef7')
        invalid=[]
        for control in (0xcb,0xab,0x8a):
            header=saved[:3]+bytes([control])+saved[4:8]
            invalid.append(header+crc(header))
        header=saved[:4]+bytes.fromhex('01040100')
        invalid.extend([header+crc(header),saved[:-1]+bytes([saved[-1]^1]),saved[:-1]])
        for reply in invalid:
            def dialogue(sock,_,reply=reply):exact(sock,10);sock.sendall(reply)
            with self.subTest(reply=reply.hex()),self.assertRaises(ValueError):self.tcp('dnp3',dialogue)

    def test_dnp3_rejects_crc_wrong_function_address_and_truncation(self):
        base=bytes.fromhex('0564050b00040100')
        for response in [base+b'\x00\x00',base[:-1],bytes.fromhex('0564050000040100'),bytes.fromhex('0564050b01040100')]:
            if len(response)==8: response+=crc(response)
            def dialogue(sock,_,response=response): exact(sock,10);sock.sendall(response)
            with self.subTest(response=response.hex()),self.assertRaises(ValueError):self.tcp('dnp3',dialogue)

    def ads_dialogue(self, mutate=lambda x:x):
        def dialogue(sock,_):
            header=exact(sock,6);length=struct.unpack('<I',header[2:])[0];request=exact(sock,length)
            self.assertEqual(header[:2],b'\0\0');self.assertEqual(length,32)
            self.assertEqual(struct.unpack('<HHIII',request[16:]),(1,4,0,0,struct.unpack('<I',request[28:])[0]))
            self.assertEqual(request[:6],bytes([127,0,0,1,1,1]));self.assertEqual(struct.unpack('<H',request[6:8])[0],10000)
            info=struct.pack('<IBBH',0,3,1,4026)+b'Fixture ADS'.ljust(16,b'\0')
            body=request[8:16]+request[:8]+struct.pack('<HHIII',1,5,len(info),0,struct.unpack('<I',request[28:])[0])+info
            body=mutate(body);sock.sendall(b'\0\0'+struct.pack('<I',len(body))+body)
        return dialogue

    def test_ads_read_device_info_no_route_or_symbol_operations(self):
        metadata,verified,rec=self.tcp('ads',self.ads_dialogue())
        self.assertEqual(verified,'ads');self.assertEqual(metadata['device_name'],'Fixture ADS')
        self.assertEqual(metadata['version'],'3.1.4026');self.assertEqual(len(rec.sent),38)
        self.assertIn('candidate',metadata['routing_limit'])

    def test_ads_rejects_wrong_command_invoke_route_and_truncated_device_info(self):
        for offset in [0,8,16,18,28]:
            def mutate(body,offset=offset):return body[:offset]+bytes([body[offset]^1])+body[offset+1:]
            with self.subTest(offset=offset),self.assertRaises(ValueError):self.tcp('ads',self.ads_dialogue(mutate))
        with self.assertRaises(ValueError):self.tcp('ads',self.ads_dialogue(lambda b:b[:-1]))

    def test_fox_hello_metadata_only(self):
        def dialogue(sock,_):
            self.assertEqual(exact(sock,52),b'fox a 1 -1 fox hello\n{\nfox.version=s:1.0\nid=i:1\n};;\n')
            sock.sendall(b'fox a 0 -1 fox hello\n{\nfox.version=s:1.0\napp.name=s:Niagara\n};;\n')
        metadata,verified,rec=self.tcp('fox',dialogue)
        self.assertEqual(verified,'fox');self.assertEqual(metadata['server']['app.name'],'Niagara')
        self.assertEqual(rec.counts['sent'],1)

    def test_fox_rejects_wrong_header_or_missing_version(self):
        for reply in [b'fox a 0 9 fox hello\n{\nfox.version=s:1.0\n};;\n',b'fox a 0 -1 fox hello\n{\napp.name=s:foo\n};;\n']:
            def dialogue(sock,_,reply=reply):exact(sock,52);sock.sendall(reply)
            with self.subTest(reply=reply),self.assertRaises(ValueError):self.tcp('fox',dialogue)

    def test_proconos_fixed_identity_no_project_reads(self):
        def dialogue(sock,_):
            self.assertEqual(exact(sock,10),bytes.fromhex('cc01000b4002000047ee'))
            response=bytearray(77);response[:2]=b'\xcc\x01'
            response[12:44]=b'ProConOS V4.1'.ljust(32,b'\0');response[44:77]=b'PLC fixture'.ljust(33,b'\0')
            sock.sendall(response)
        metadata,verified,rec=self.tcp('proconos',dialogue)
        self.assertEqual(verified,'proconos');self.assertEqual(metadata['device_type'],'PLC fixture')
        self.assertNotIn('project',str(metadata).lower());self.assertEqual(len(rec.sent),10)

    def test_proconos_rejects_unrelated_magic_and_truncation(self):
        for reply in [b'\xcc\x01'+b'\0'*75,b'\xcc\x01'+b'\0'*8,b'HTTP/1.1 200 OK\r\n']:
            def dialogue(sock,_,reply=reply):exact(sock,10);sock.sendall(reply)
            with self.subTest(reply=reply),self.assertRaises(ValueError):self.tcp('proconos',dialogue)

    def test_codesys_v2_identity_is_one_candidate_endianness(self):
        def dialogue(sock,_):
            self.assertEqual(exact(sock,7),bytes.fromhex('bbbb0100000001'))
            response=bytearray(160);response[:2]=b'\xbb\xbb';response[64:96]=b'Linux'.ljust(32,b'\0')
            response[96:128]=b'ARM'.ljust(32,b'\0');response[128:160]=b'Controller'.ljust(32,b'\0')
            sock.sendall(response)
        metadata,verified,rec=self.tcp('codesys',dialogue)
        self.assertEqual(verified,'codesys');self.assertEqual(metadata['generation'],2)
        self.assertEqual(metadata['product_type'],'Controller');self.assertEqual(len(rec.sent),7)

    def test_codesys_rejects_magic_empty_identity_unterminated_and_truncation(self):
        for reply in [b'\xbb\xbb'+b'\0'*158,b'\xbb\xbb'+b'x'*158,b'\xbb\xbb'+b'\0'*100,b'\xbbx'+b'\0'*158]:
            def dialogue(sock,_,reply=reply):exact(sock,7);sock.sendall(reply)
            with self.subTest(reply=reply[:4]),self.assertRaises(ValueError):self.tcp('codesys',dialogue)

    def test_pcworx_initial_session_only_no_secondary_initialization(self):
        def dialogue(sock,_):
            self.assertEqual(exact(sock,26),bytes.fromhex('0101001a0000000078800003000c')+b'IBETH01N0_M\0')
            reply=bytearray(20);reply[:4]=bytes.fromhex('81010014');reply[17]=7;sock.sendall(reply)
            self.assertEqual(sock.recv(1),b'')
        metadata,verified,rec=self.tcp('pcworx',dialogue)
        self.assertEqual(verified,'pcworx');self.assertEqual(metadata['session_id'],7);self.assertEqual(len(rec.sent),26)

    def test_pcworx_rejects_wrong_command_sequence_and_frame_length(self):
        for reply in [bytes.fromhex('81020014')+b'\0'*16,bytes.fromhex('81010014')+b'\0\x01'+b'\0'*14,bytes.fromhex('8101ffff')]:
            def dialogue(sock,_,reply=reply):exact(sock,26);sock.sendall(reply)
            with self.subTest(reply=reply[:6]),self.assertRaises(ValueError):self.tcp('pcworx',dialogue)

    def mms_dialogue(self,response=None,cc=None):
        def dialogue(sock,_):
            head=exact(sock,4);self.assertEqual(head[:2],b'\x03\0');request=exact(sock,struct.unpack('!H',head[2:])[0]-4)
            self.assertEqual(request[1],0xe0)
            sock.sendall(cc if cc is not None else tpkt(b'\x06\xd0'+request[4:6]+b'\x00\x07\x00'))
            if cc is not None:return
            head=exact(sock,4);request=exact(sock,struct.unpack('!H',head[2:])[0]-4)
            self.assertEqual(request[:4],b'\x02\xf0\x80\x0d')
            self.assertIn(bytes.fromhex('a107060528ca220203'),request)
            self.assertNotIn(b'password',request)
            sock.sendall(response or mms_response())
        return dialogue

    def test_mms_requires_acse_and_mms_initiate_response(self):
        metadata,verified,rec=self.tcp('iec61850',self.mms_dialogue())
        self.assertEqual(verified,'mms');self.assertEqual(metadata['negotiated_max_pdu'],8192)
        self.assertFalse(metadata['iec61850_model_verified']);self.assertEqual(rec.counts['sent'],2)

    def test_mms_rejects_cotp_only_wrong_context_rejected_association_and_wrong_pdu(self):
        for response in [mms_response(context=b'\x2a\x03'),mms_response(result=1),mms_response(initiate_tag=0xa8),mms_response(pdu=b'\x40\x00'),mms_response()[:-1]]:
            with self.subTest(response=response[-8:].hex()),self.assertRaises(ValueError):self.tcp('iec61850',self.mms_dialogue(response))
        with self.assertRaises(ValueError):self.tcp('iec61850',self.mms_dialogue(cc=tpkt(bytes.fromhex('06d00009000700'))))

    def fins_response(self,request):
        self.assertEqual(request[10:],b'\x05\x01\x00')
        return b'\xc0\x00\x02'+request[6:9]+request[3:6]+request[9:10]+b'\x05\x01\x00\x00'+b'CJ2M-CPU32'.ljust(20,b' ')+b'02.01'.ljust(20,b' ')

    def test_fins_controller_identity_only(self):
        metadata,verified,rec=self.udp('fins',self.fins_response)
        self.assertEqual(verified,'fins');self.assertEqual(metadata['model'],'CJ2M-CPU32')
        self.assertEqual(rec.counts,{'sent':1,'received':1});self.assertEqual(len(rec.sent),13)

    def test_fins_rejects_sid_command_route_and_truncation(self):
        for offset in [0,3,9,10,12]:
            def response(request,offset=offset):
                data=self.fins_response(request);return data[:offset]+bytes([data[offset]^(0x40 if offset==0 else 1)])+data[offset+1:]
            with self.subTest(offset=offset),self.assertRaises(ValueError):self.udp('fins',response)
        with self.assertRaises(ValueError):self.udp('fins',lambda r:self.fins_response(r)[:25])

    def slmp_response(self,request):
        self.assertEqual(request,bytes.fromhex('500000ffff03000600100001010000'))
        return bytes.fromhex('d00000ffff030014000000')+b'Q03UDECPU'.ljust(16,b' ')+b'\x4b\x00'

    def test_melsec_slmp_only_reads_model(self):
        metadata,verified,rec=self.udp('melsec',self.slmp_response)
        self.assertEqual(verified,'melsec');self.assertEqual(metadata['model'],'Q03UDECPU')
        self.assertEqual(metadata['model_code'],75);self.assertEqual(len(rec.sent),15)

    def test_melsec_rejects_wrong_type_route_length_status_and_truncation(self):
        for offset in [0,2,7,9]:
            def response(request,offset=offset):
                data=self.slmp_response(request);return data[:offset]+bytes([data[offset]^1])+data[offset+1:]
            with self.subTest(offset=offset),self.assertRaises(ValueError):self.udp('melsec',response)
        with self.assertRaises(ValueError):self.udp('melsec',lambda r:self.slmp_response(r)[:-1])

    def hart_response(self,request):
        self.assertEqual(request[:4],b'\x01\x00\x00\x00');self.assertEqual(request[6:8],b'\x00\x0d')
        self.assertEqual(request[8],1)
        return b'\x01\x01\x00\x00'+request[4:6]+b'\x00\x0d'+request[8:]

    def test_hartip_session_init_only(self):
        metadata,verified,rec=self.udp('hartip',self.hart_response)
        self.assertEqual(verified,'hartip');self.assertEqual(metadata['version'],1)
        self.assertEqual(len(rec.sent),13);self.assertEqual(rec.counts['sent'],1)

    def test_hartip_rejects_wrong_type_id_sequence_status_length(self):
        for offset in [0,1,2,3,4,6,8]:
            def response(request,offset=offset):
                data=self.hart_response(request);return data[:offset]+bytes([data[offset]^1])+data[offset+1:]
            with self.subTest(offset=offset),self.assertRaises(ValueError):self.udp('hartip',response)

    def test_lantronix_firmware_query_does_not_read_configuration(self):
        def response(request):
            self.assertEqual(request,b'\x00\x00\x00\xf6')
            return b'\x00\x00\x00\xf7'+b'\x00'*4+b'X1'+b'\x00'*10+b'\x00\x20\x4a\x01'
        metadata,verified,rec=self.udp('lantronix-config',response)
        self.assertEqual(verified,'lantronix-config');self.assertEqual(metadata['software_type'],'X1')
        self.assertEqual(len(rec.sent),4)

    def test_lantronix_rejects_configuration_reply_and_short_firmware(self):
        for reply in [b'\0\0\0\xf9'+b'\0'*120,b'\0\0\0\xf7'+b'\0'*10]:
            with self.subTest(reply=reply[:4]),self.assertRaises(ValueError):self.udp('lantronix-config',lambda _,reply=reply:reply)

    def test_vertx_discovery_only(self):
        reply=b'discovered;087;00:06:8E:01:02:03;VertXController;127.0.0.1;2;V2000;2.2.7.18;02/27/2007;'
        def response(request):self.assertEqual(request,b'discover;013;');return reply
        metadata,verified,rec=self.udp('vertx-edge',response)
        self.assertEqual(verified,'vertx-edge');self.assertEqual(metadata['model'],'V2000');self.assertEqual(len(rec.sent),13)

    def test_vertx_saved_edge_eh400_discovery_preserves_frame_with_anonymized_identifiers(self):
        # Saved 84-byte record; same-width MAC suffix/IP anonymized in fixture.
        reply=b'discovered;084;00:06:8E:00:00:01;EdgeEH400;192.0.2.100;1;EH400;3.8.0.103;02/05/2019;'
        self.assertEqual(len(reply),84)
        def response(request):self.assertEqual(request,b'discover;013;');return reply
        metadata,verified,rec=self.udp('vertx-edge',response)
        self.assertEqual(verified,'vertx-edge');self.assertEqual(metadata['device_type'],'EdgeEH400')
        self.assertEqual(metadata['model'],'EH400');self.assertEqual(metadata['version'],'3.8.0.103')
        self.assertEqual(rec.counts,{'sent':1,'received':1});self.assertEqual(len(rec.received),84)

    def test_vertx_family_expansion_rejects_unverified_names_and_keeps_field_validation(self):
        def framed(name,mac=b'00:06:8E:00:00:01',address=b'192.0.2.100'):
            reply=b'discovered;000;'+mac+b';'+name+b';'+address+b';1;EH400;3.8.0.103;02/05/2019;'
            return reply.replace(b';000;',b';'+str(len(reply)).zfill(3).encode()+b';',1)
        replies=[framed(name) for name in (b'GenericDevice',b'EdgeUnknown',b'VertXUnknown',b'EdgeEH400 ',b'EdgeEH400\x00',b'Edge'+b'x'*80)]
        replies += [framed(b'EdgeEH400',mac=b'00:06:8E:bad'),framed(b'EdgeEH400',address=b'invalid'),
                    framed(b'EdgeEH400')[:-1],framed(b'EdgeEH400').replace(b';084;',b';085;',1)]
        for reply in replies:
            with self.subTest(reply=reply[:60]),self.assertRaises(ValueError):self.udp('vertx-edge',lambda _,reply=reply:reply)

    def test_vertx_rejects_non_discovery_bad_mac_or_truncated(self):
        for reply in [b'command_blink_on;042;00:06:8e:00:00:00;30;',b'discovered;090;00:06:8E:01:02:03;VertXController;127.0.0.1;2;V2000;2.2.7.18;02/27/2007;',b'discovered;090;bad;VertXController;127.0.0.1;2;V2000;2.2.7.18;date;',b'discovered;090;']:
            with self.subTest(reply=reply[:25]),self.assertRaises(ValueError):self.udp('vertx-edge',lambda _,reply=reply:reply)

    def coap_response(self,request):
        self.assertEqual(request[0],0x44);self.assertEqual(request[1],1)
        self.assertEqual(request[8:],b'\xbb.well-known\x04core\x61\x28')
        return b'\x64\x45'+request[2:8]+b'\xc1\x28\xff</sensor>;rt="temperature-c"'

    def test_coap_get_discovery_and_preserve_wire(self):
        metadata,verified,rec=self.udp('coap',self.coap_response)
        self.assertEqual(verified,'coap');self.assertIn('</sensor>',metadata['links'])
        self.assertEqual(rec.counts['sent'],1);self.assertEqual(rec.counts['received'],1)

    def test_coap_rejects_message_id_token_type_options_truncation_and_oversize(self):
        for offset in [0,2,4,8]:
            def response(request,offset=offset):
                data=self.coap_response(request);return data[:offset]+bytes([data[offset]^1])+data[offset+1:]
            with self.subTest(offset=offset),self.assertRaises(ValueError):self.udp('coap',response)
        for reply in [b'\x64',b'x'*8193]:
            with self.subTest(length=len(reply)),self.assertRaises(ValueError):self.udp('coap',lambda _,reply=reply:reply)

    def coap_tcp_dialogue(self,reply=None,first=b'\x00\xe1',signals=b''):
        def dialogue(sock,_):
            self.assertEqual(read_coap_stream(sock),(225,b'',b'\x22\x20\x00'))
            sock.sendall(first)
            if first!=b'\x00\xe1':return
            code,token,body=read_coap_stream(sock)
            self.assertEqual(code,1);self.assertEqual(len(token),4)
            self.assertEqual(body,b'\xbb.well-known\x04core\x61\x28')
            response=coap_stream(69,token,b'\xc1\x28\xff</a>') if reply is None else reply(token)
            sock.sendall(signals+response)
        return dialogue

    def test_coap_tcp_csm_then_discovery_get_with_distinct_stream_framing(self):
        metadata,verified,rec=self.tcp('coap',self.coap_tcp_dialogue())
        self.assertEqual(verified,'coap');self.assertEqual(metadata['links'],'</a>')
        self.assertEqual(metadata['transport_framing'],'RFC 8323 TCP')
        self.assertEqual(metadata['peer_max_message_size'],1152)
        self.assertEqual(rec.counts['sent'],2)
        self.assertEqual(rec.sent[:5],bytes.fromhex('30e1222000'))
        self.assertEqual(ot.CAPABILITIES['coap']['transports'],['tcp','udp'])

    def test_coap_tcp_extended_lengths_and_fragmented_response(self):
        for payload in [b'</a>'+b' '*20,b'</a>'+b' '*300]:
            def reply(token,payload=payload):return coap_stream(69,token,b'\xc1\x28\xff'+payload)
            metadata,verified,rec=self.tcp('coap',self.coap_tcp_dialogue(reply))
            self.assertEqual(metadata['links'].encode(),payload)
        def dialogue(sock,_):
            read_coap_stream(sock);sock.sendall(b'\x00\xe1')
            code,token,body=read_coap_stream(sock)
            for byte in coap_stream(69,token,b'\xc1\x28\xff</a>'):sock.sendall(bytes([byte]))
        metadata,verified,rec=self.tcp('coap',dialogue)
        self.assertEqual(metadata['links'],'</a>')

    def test_coap_tcp_requires_valid_initial_csm_before_get(self):
        for first in [coap_stream(69,b'test'),b'\x00\xe4',b'\x00\xe5',b'\x09\xe1',
                      coap_stream(225,body=b'\x10'),coap_stream(225,body=b'\x25'+b'\x01'*5),
                      coap_stream(225,body=b'\x21\x00'),coap_stream(225,body=b'\x41\x00'),
                      coap_stream(225,body=b'\x20\x20'),b'\xe0\xff\xff',b'\xf0\x00\x00\x00\x00',b'\xd0']:
            def dialogue(sock,_,first=first):
                self.assertEqual(read_coap_stream(sock)[0],225);sock.sendall(first)
            with self.subTest(first=first.hex()),self.assertRaises(ValueError):self.tcp('coap',dialogue)

    def test_coap_tcp_unknown_critical_csm_emits_abort_and_no_get(self):
        def dialogue(sock,_):
            self.assertEqual(read_coap_stream(sock)[0],225)
            sock.sendall(coap_stream(225,body=b'\x10'))
            self.assertEqual(read_coap_stream(sock),(229,b'',b''))
            self.assertEqual(sock.recv(1),b'')
        with self.assertRaises(ValueError):self.tcp('coap',dialogue)

    def test_coap_tcp_honors_peer_max_size_and_ignores_elective_csm_options(self):
        def dialogue(sock,_):
            read_coap_stream(sock);sock.sendall(coap_stream(225,body=b'\x21\x40\x20\x21\xff'))
            code,token,body=read_coap_stream(sock)
            self.assertEqual(code,1)
            sock.sendall(coap_stream(69,token,b'\xc1\x28\xff</a>'))
        metadata,verified,rec=self.tcp('coap',dialogue)
        self.assertEqual(metadata['peer_max_message_size'],64)

    def test_coap_tcp_cumulative_csm_and_empty_keepalive_are_bounded(self):
        signals=coap_stream(225,body=b'\x21\x40')+coap_stream(225)+coap_stream(0)
        metadata,verified,rec=self.tcp('coap',self.coap_tcp_dialogue(signals=signals))
        self.assertEqual(metadata['peer_max_message_size'],64);self.assertEqual(rec.counts['sent'],2)
        with self.assertRaises(ValueError):self.tcp('coap',self.coap_tcp_dialogue(signals=coap_stream(225)*5))

    def test_coap_tcp_ping_pong_uses_identical_token_without_resource_operations(self):
        def dialogue(sock,_):
            read_coap_stream(sock);sock.sendall(coap_stream(225))
            code,token,body=read_coap_stream(sock)
            sock.sendall(coap_stream(226,b'health'))
            self.assertEqual(read_coap_stream(sock),(227,b'health',b''))
            sock.sendall(coap_stream(69,token,b'\xc1\x28\xff</a>'))
        metadata,verified,rec=self.tcp('coap',dialogue)
        self.assertEqual(rec.counts['sent'],3);self.assertEqual(metadata['links'],'</a>')

    def test_coap_tcp_release_and_abort_close_without_echo_or_new_connection(self):
        for signal in [coap_stream(228,body=b'\x2cother.invalid'),coap_stream(229)]:
            def dialogue(sock,_,signal=signal):
                read_coap_stream(sock);sock.sendall(coap_stream(225))
                self.assertEqual(read_coap_stream(sock)[0],1)
                sock.sendall(signal)
                self.assertEqual(sock.recv(1),b'')
            with self.subTest(signal=signal.hex()),self.assertRaises(ValueError):self.tcp('coap',dialogue)

    def test_coap_tcp_rejects_wrong_token_code_options_payload_and_udp_framing(self):
        replies=[lambda t:coap_stream(69,b'wrong',b'\xc1\x28\xff</a>'),
                 lambda t:coap_stream(1,t),lambda t:coap_stream(0,t),
                 lambda t:coap_stream(69,t,b'\xc0\xff</a>'),
                 lambda t:coap_stream(69,t,b'\xc1\x28\xb0\xff</a>'),
                 lambda t:coap_stream(69,t,b'\xc1\x28\xe0\xff\xff\xff</a>'),
                 lambda t:coap_stream(69,t,b'\xc1\x28\xff'),
                 lambda t:coap_stream(69,t,b'\xc1\x28\xffinvalid'),
                 lambda t:coap_stream(69,t,b'\xc1\x28\xff\xff'),
                 lambda t:b'\x64\x45\x00\x01'+t+b'\xc1\x28\xff</a>',
                 lambda t:b'\xe4\x20\x00',lambda t:coap_stream(69,t,b'\xc1\x28\xff</a>')[:-1]]
        for index,reply in enumerate(replies):
            with self.subTest(index=index),self.assertRaises(ValueError):self.tcp('coap',self.coap_tcp_dialogue(reply))

    def test_coap_tcp_auth_error_is_metadata_and_release_does_not_follow_address(self):
        metadata,verified,rec=self.tcp('coap',self.coap_tcp_dialogue(lambda t:coap_stream(129,t)))
        self.assertTrue(metadata['authentication_required']);self.assertEqual(rec.counts['sent'],2)
        for signal in [coap_stream(228,body=b'\x2cother.invalid'),coap_stream(229)]:
            with self.subTest(signal=signal.hex()),self.assertRaises(ValueError):self.tcp('coap',self.coap_tcp_dialogue(lambda t,signal=signal:signal))

    def test_udp_rejects_broadcast_multicast_and_invalid_deadlines_without_socket(self):
        self.require()
        for service,adapter in ot.UDP_ADAPTERS.items():
            for address in ['255.255.255.255','224.0.0.1','::','ff02::1']:
                with self.subTest(service=service,address=address),self.assertRaises(ValueError):adapter(address,12345,1,WireRecorder('udp'))
            for timeout in [0,-1,True,float('nan'),float('inf'),10**1000]:
                with self.subTest(service=service,timeout=str(timeout)[:20]),self.assertRaises(ValueError):adapter('127.0.0.1',12345,timeout,WireRecorder('udp'))

    def test_slmp_identity_over_tcp_has_one_framed_request(self):
        def dialogue(sock,_):
            request=exact(sock,15);sock.sendall(self.slmp_response(request))
        metadata,verified,rec=self.tcp('melsec',dialogue)
        self.assertEqual(verified,'melsec');self.assertEqual(metadata['model'],'Q03UDECPU')
        self.assertEqual(len(rec.sent),15)

    def test_hartip_session_init_over_tcp(self):
        def dialogue(sock,_):sock.sendall(self.hart_response(exact(sock,13)))
        metadata,verified,rec=self.tcp('hartip',dialogue)
        self.assertEqual(verified,'hartip');self.assertEqual(len(rec.sent),13)

    def test_fins_tcp_negotiates_nodes_then_only_reads_cpu_identity(self):
        def dialogue(sock,_):
            self.assertEqual(exact(sock,20),bytes.fromhex('46494e530000000c000000000000000000000000'))
            sock.sendall(bytes.fromhex('46494e530000001000000001000000000000000100000002'))
            header=exact(sock,16);self.assertEqual(header,bytes.fromhex('46494e53000000150000000200000000'))
            request=exact(sock,13);self.assertEqual(request[4],2);self.assertEqual(request[7],1)
            reply=self.fins_response(request)
            sock.sendall(b'FINS'+struct.pack('!III',len(reply)+8,2,0)+reply)
        metadata,verified,rec=self.tcp('fins',dialogue)
        self.assertEqual(verified,'fins');self.assertEqual(metadata['model'],'CJ2M-CPU32')
        self.assertEqual(metadata['destination_node'],2);self.assertEqual(rec.counts['sent'],2)

    def test_fins_tcp_rejects_invalid_negotiation_before_cpu_request(self):
        for reply in [bytes.fromhex('46494e530000001000000002000000000000000100000002'),
                      bytes.fromhex('46494e530000001000000001000000010000000100000002'),
                      bytes.fromhex('46494e530000001000000001000000000000010000000002'),
                      bytes.fromhex('46494e530000ffff')]:
            def dialogue(sock,_,reply=reply):exact(sock,20);sock.sendall(reply)
            with self.subTest(reply=reply),self.assertRaises(ValueError):self.tcp('fins',dialogue)

    def test_stream_variants_reject_truncated_and_wrong_type_replies(self):
        for service,request_len,reply in [('melsec',15,b'x'*9),('melsec',15,bytes.fromhex('d00000ffff030014000000')),
                                         ('hartip',13,b'\x01\x01\x00\x00\x00\x01\xff\xff'),('hartip',13,b'\x01')]:
            def dialogue(sock,_,reply=reply,request_len=request_len):exact(sock,request_len);sock.sendall(reply)
            with self.subTest(service=service,reply=reply),self.assertRaises(ValueError):self.tcp(service,dialogue)

    def test_udp_timeout_sends_at_most_one_request(self):
        self.require();rec=WireRecorder('udp')
        def dialogue(request,address):pass
        with peer(dialogue,udp=True) as port:
            with self.assertRaises((TimeoutError,OSError)):ot.UDP_ADAPTERS['coap']('127.0.0.1',port,.05,rec)
        self.assertEqual(rec.counts['sent'],1);self.assertEqual(rec.counts['received'],0)
