"""Bounded OT service metadata exchanges. No login, process reads, or writes.

Most adapters send one frame. MMS and FINS/TCP use two identification frames;
CoAP/TCP adds mandatory CSM and bounded connection signaling to its one GET.
The caller owns TCP lifetime and supplies a deadline-aware Reader/RecordedSocket.
UDP creates exactly one connected socket to one selected unicast destination;
no retries, broadcast, resource traversal, or following server-selected ports.
Sources and interoperability limits are exposed through CAPABILITIES below.
"""
import ipaddress
import math
import os
import re
import socket
import struct
import time

from .wire import RecordedSocket, unicast_destination

_MAX_REPLY = 8192


def _text(raw):
    value = raw.split(b'\0', 1)[0].strip()
    if not value or any(byte < 32 or byte > 126 for byte in value):
        raise ValueError('Invalid or empty OT identity text')
    return value.decode('ascii')


def _fixed_text(raw):
    if b'\0' not in raw:
        raise ValueError('Unterminated fixed OT identity field')
    return _text(raw)


def _dnp_crc(raw):
    value = 0
    for byte in raw:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (0xa6bc if value & 1 else 0)
    return struct.pack('<H', value ^ 0xffff)


def _dnp3(sock, read, address, port):
    # One explicit candidate: target link 1, local master 1024. No reset/broadcast.
    header = bytes.fromhex('056405c901000004')
    sock.sendall(header + _dnp_crc(header))
    reply = read.exact(10)
    # DIR describes sender role independently of the secondary LINK_STATUS
    # function. Only DIR/DFC may vary; PRM and the reserved FCB bit stay clear.
    if (reply[:3] != b'\x05\x64\x05' or reply[3] not in (0x0b, 0x1b, 0x8b, 0x9b)
            or reply[4:8] != bytes.fromhex('00040100')
            or reply[8:] != _dnp_crc(reply[:8])):
        raise ValueError('Invalid DNP3 link-status response, address or CRC')
    return {'operation': 'REQUEST_LINK_STATUS', 'target_link_address': 1,
            'source_link_address': 1024, 'target_role': 'master' if reply[3] & 0x80 else 'outstation',
            'receiver_buffer_full': bool(reply[3] & 0x10),
            'routing_limit': 'One candidate target link address (1), local source 1024; other logical addresses are not searched.'}, 'dnp3'


def _ads(sock, read, address, port):
    # AMS NetIds are configured logical addresses. This is one conventional
    # IPv4-derived candidate, not discovery or a route-add operation.
    peer = ipaddress.ip_address(sock.getpeername()[0])
    local = ipaddress.ip_address(sock.getsockname()[0])
    if peer.version != 4 or local.version != 4:
        raise ValueError('ADS identity requires an IPv4-derived AMS NetId candidate')
    target = peer.packed + b'\x01\x01' + struct.pack('<H', 10000)
    source = local.packed + b'\x01\x01' + struct.pack('<H', 32905)
    invoke = os.urandom(4)
    packet = target + source + struct.pack('<HHII', 1, 4, 0, 0) + invoke
    sock.sendall(b'\0\0' + struct.pack('<I', len(packet)) + packet)
    prefix = read.exact(6)
    length = struct.unpack('<I', prefix[2:])[0]
    if prefix[:2] != b'\0\0' or length != 56:
        raise ValueError('Invalid ADS device-info frame length')
    body = read.exact(length)
    command, flags, data_length, error = struct.unpack('<HHII', body[16:28])
    if (body[:8] != source or body[8:16] != target or command != 1 or flags != 5
            or data_length != 24 or error or body[28:32] != invoke):
        raise ValueError('Invalid ADS command, route, error or invoke correlation')
    result, major, minor, build = struct.unpack('<IBBH', body[32:40])
    if result:
        raise ValueError('ADS READ_DEVICE_INFO was refused')
    return {'operation': 'READ_DEVICE_INFO', 'device_name': _text(body[40:56]),
            'version': f'{major}.{minor}.{build}',
            'ams_net_id': '.'.join(str(byte) for byte in target[:6]), 'ams_port': 10000,
            'routing_limit': 'One IPv4-derived candidate AMS NetId at System Service port 10000; configured NetIds/routes may differ.'}, 'ads'


def _fox(sock, read, address, port):
    sock.sendall(b'fox a 1 -1 fox hello\n{\nfox.version=s:1.0\nid=i:1\n};;\n')
    raw = read.until(b'};;\n', _MAX_REPLY)
    try: text = raw.decode('ascii')
    except UnicodeError: raise ValueError('Invalid Fox hello encoding') from None
    lines = text.splitlines()
    if not lines or lines[0] != 'fox a 0 -1 fox hello' or len(lines) < 4 or lines[1] != '{' or lines[-1] != '};;':
        raise ValueError('Invalid Fox hello response header')
    allowed = {'fox.version','hostName','hostAddress','app.name','app.version','vm.name',
               'vm.version','os.name','timeZone','hostId','vmUuid','brandId'}
    server = {}
    for line in lines[2:-1]:
        match = re.fullmatch(r'([A-Za-z.]+)=s:([^\x00-\x1f]{1,512})', line)
        if match and match[1] in allowed:
            if match[1] in server: raise ValueError('Duplicate Fox identity field')
            server[match[1]] = match[2]
    if not re.fullmatch(r'\d+(?:\.\d+){1,3}', server.get('fox.version', '')):
        raise ValueError('Fox hello did not contain a protocol version')
    return {'operation': 'initial Fox hello', 'server': server}, 'fox'


def _proconos(sock, read, address, port):
    sock.sendall(bytes.fromhex('cc01000b4002000047ee'))
    # Fixed initial identity fields only. Do not read project/source metadata.
    reply = read.exact(77)
    runtime = _fixed_text(reply[12:44])
    if reply[:2] != b'\xcc\x01' or not runtime.startswith('ProConOS V'):
        raise ValueError('Invalid ProConOS identity response')
    return {'operation': 'runtime/device identification', 'runtime': runtime,
            'device_type': _fixed_text(reply[44:77]),
            'framing_limit': 'Legacy fixed identity fields; no transaction identifier is defined by the source implementation.'}, 'proconos'


def _codesys(sock, read, address, port):
    sock.sendall(bytes.fromhex('bbbb0100000001'))
    reply = read.exact(160)
    if reply[:2] != b'\xbb\xbb': raise ValueError('Invalid CODESYS v2 identity magic')
    return {'operation': 'v2 runtime identification', 'generation': 2,
            'os_name': _fixed_text(reply[64:96]), 'os_type': _fixed_text(reply[96:128]),
            'product_type': _fixed_text(reply[128:160]),
            'framing_limit': 'One legacy little-endian identity candidate; no CODESYS v3, gateway routing, or login.'}, 'codesys'


def _pcworx(sock, read, address, port):
    # Initial communications only. The later opaque initialization (01/05) and
    # PLC-data request (01/06) in the research scanner are deliberately omitted.
    sock.sendall(bytes.fromhex('0101001a0000000078800003000c') + b'IBETH01N0_M\0')
    header = read.exact(4)
    length = int.from_bytes(header[2:], 'big')
    if header[:2] != b'\x81\x01' or not 18 <= length <= 256:
        raise ValueError('Invalid PCWorx initial communications frame')
    reply = header + read.exact(length-4)
    if reply[4:8] != b'\0\0\0\0':
        raise ValueError('Invalid PCWorx initial sequence/status')
    return {'operation': 'initial communications only', 'session_id': reply[17],
            'limitation': 'Initial protocol response only; secondary initialization semantics are undocumented, so no PLC metadata request is sent.'}, 'pcworx'


def _tl(tag, body):
    length = len(body)
    if length < 128: encoded = bytes([length])
    elif length <= 255: encoded = b'\x81' + bytes([length])
    else: encoded = b'\x82' + struct.pack('!H',length)
    return bytes([tag]) + encoded + body


def _ber(raw):
    """Definite, single-octet tags only, bounded by the enclosing wire frame."""
    pos = 0; values = {}
    while pos < len(raw):
        if len(values) >= 32 or pos+2 > len(raw): raise ValueError('Invalid BER field count/header')
        tag, length = raw[pos:pos+2]; pos += 2
        if tag & 31 == 31: raise ValueError('Unsupported BER high tag')
        if length & 128:
            count = length & 127
            if not 1 <= count <= 2 or pos+count > len(raw): raise ValueError('Invalid BER length')
            length = int.from_bytes(raw[pos:pos+count], 'big'); pos += count
        if length > _MAX_REPLY or pos+length > len(raw) or tag in values:
            raise ValueError('Invalid or duplicate BER field')
        values[tag] = raw[pos:pos+length]; pos += length
    return values


def _only(raw, tag):
    fields = _ber(raw)
    if set(fields) != {tag}: raise ValueError('Unexpected association envelope')
    return fields[tag]


def _integer(raw):
    if not 1 <= len(raw) <= 4 or raw[0] & 128: raise ValueError('Invalid association integer')
    return int.from_bytes(raw, 'big')


def _tpkt_read(read):
    header = read.exact(4)
    length = int.from_bytes(header[2:], 'big')
    if header[:2] != b'\x03\0' or not 7 <= length <= _MAX_REPLY:
        raise ValueError('Invalid RFC1006 TPKT')
    return read.exact(length-4)


def _tpkt(body): return b'\x03\0' + struct.pack('!H', len(body)+4) + body


def _mms_association_request():
    detail = _tl(0x80,b'\x01') + _tl(0x81,b'\x05\xf1\x00') + _tl(0x82,b'\x03\x60'+b'\0'*10)
    initiate = _tl(0xa8,_tl(0x80,b'\x20\0')+_tl(0x81,b'\x01')+_tl(0x82,b'\x01')+_tl(0x83,b'\x05')+_tl(0xa4,detail))
    # No ACSE authentication mechanism, password, or application data service.
    acse = _tl(0x60,_tl(0xa1,_tl(6,bytes.fromhex('28ca220203')))+_tl(0xbe,_tl(0x28,_tl(2,b'\x03')+_tl(0xa0,initiate))))
    contexts = _tl(0x30,_tl(2,b'\x01')+_tl(6,bytes.fromhex('52010001'))+_tl(0x30,_tl(6,b'\x51\x01')))
    contexts += _tl(0x30,_tl(2,b'\x03')+_tl(6,bytes.fromhex('28ca220201'))+_tl(0x30,_tl(6,b'\x51\x01')))
    data = _tl(0x61,_tl(0x30,_tl(2,b'\x01')+_tl(0xa0,acse)))
    selector = b'\0\0\0\x01'
    pres = _tl(0x31,_tl(0xa0,_tl(0x80,b'\x01'))+_tl(0xa2,_tl(0x81,selector)+_tl(0x82,selector)+_tl(0xa4,contexts)+data))
    session = bytes.fromhex('0506130100160102140200023302000134020001') + b'\xc1'+bytes([len(pres)])+pres
    return _tpkt(b'\x02\xf0\x80'+b'\x0d'+bytes([len(session)])+session)


def _mms(sock, read, address, port):
    reference = b'\x00\x01'
    sock.sendall(_tpkt(b'\x11\xe0\0\0'+reference+b'\0\xc0\x01\x0a\xc1\x02\0\x01\xc2\x02\0\x01'))
    cc = _tpkt_read(read)
    if len(cc) < 7 or cc[0]+1 != len(cc) or cc[1] != 0xd0 or cc[2:4] != reference or cc[4:6] == b'\0\0' or cc[6] != 0:
        raise ValueError('Invalid COTP connection confirm/correlation')
    pos=7; seen=set()
    while pos<len(cc):
        if pos+2>len(cc): raise ValueError('Truncated COTP option')
        tag,length=cc[pos:pos+2];pos+=2
        if tag in seen or tag not in (0xc0,0xc1,0xc2) or not length or pos+length>len(cc):raise ValueError('Invalid COTP option')
        if tag==0xc0 and (length!=1 or not 7<=cc[pos]<=16):raise ValueError('Invalid COTP TPDU size')
        seen.add(tag);pos+=length
    sock.sendall(_mms_association_request())
    packet = _tpkt_read(read)
    if packet[:4] != b'\x02\xf0\x80\x0e' or len(packet)<6 or packet[4] != len(packet)-5:
        raise ValueError('Invalid COTP data or session ACCEPT; MMS is unverified')
    session = packet[5:]; pos=0; pres=None
    while pos<len(session):
        if pos+2>len(session):raise ValueError('Truncated session parameter')
        tag,length=session[pos:pos+2];pos+=2
        if pos+length>len(session):raise ValueError('Invalid session parameter length')
        if tag==0xc1:
            if pres is not None:raise ValueError('Duplicate session user data')
            pres=session[pos:pos+length]
        pos+=length
    if pres is None:raise ValueError('No presentation association response')
    presentation = _ber(_only(pres,0x31))
    if _only(presentation.get(0xa0,b''),0x80)!=b'\x01':raise ValueError('Invalid presentation mode')
    normal = _ber(presentation.get(0xa2,b''))
    # Exactly the two offered BER contexts must have been accepted.
    results = normal.get(0xa5,b''); pos=0; accepted=0
    while pos<len(results):
        if pos+2>len(results) or results[pos]!=0x30 or results[pos+1]&128:raise ValueError('Invalid presentation context result')
        length=results[pos+1];end=pos+2+length
        if end>len(results):raise ValueError('Truncated presentation context result')
        result=_ber(results[pos+2:end])
        if result.get(0x80)!=b'\0' or result.get(0x81)!=b'\x51\x01':raise ValueError('MMS presentation context refused')
        accepted+=1;pos=end
    if accepted!=2:raise ValueError('Missing MMS presentation context results')
    data = _ber(_only(normal.get(0x61,b''),0x30))
    if data.get(2)!=b'\x01':raise ValueError('Invalid ACSE presentation context')
    acse = _ber(_only(data.get(0xa0,b''),0x61))
    if _only(acse.get(0xa1,b''),6)!=bytes.fromhex('28ca220203'):
        raise ValueError('Association did not select the MMS application context')
    if _only(acse.get(0xa2,b''),2)!=b'\0' or any(tag in acse for tag in (0x88,0x89,0xaa)):
        raise ValueError('MMS association refused or requires authentication')
    external = _ber(_only(acse.get(0xbe,b''),0x28))
    if external.get(2)!=b'\x03':raise ValueError('Invalid MMS indirect reference')
    initiate = _ber(_only(external.get(0xa0,b''),0xa9))
    detail = _ber(initiate.get(0xa4,b''))
    if detail.get(0x80)!=b'\x01':raise ValueError('Unsupported MMS negotiated version')
    for tag,limit in ((0x81,2),(0x82,11)):
        value=detail.get(tag,b'')
        if len(value)!=limit+1 or value[0]>7 or value[-1]&((1<<value[0])-1):raise ValueError('Invalid MMS negotiated bit string')
    pdu=_integer(initiate.get(0x80,b''))
    calling=_integer(initiate.get(0x81,b''));called=_integer(initiate.get(0x82,b''))
    if not 64<=pdu<=8192 or calling>1 or called>1:raise ValueError('Invalid MMS negotiation bounds')
    return {'operation':'COTP + unauthenticated ACSE/MMS Initiate association only',
            'negotiated_max_pdu':pdu, 'version':1, 'iec61850_model_verified':False,
            'limitation':'MMS association identifies MMS; IEC 61850 data models, logical devices, and process values are not queried.'}, 'mms'


def _udp(address, port, timeout, recorder, operation):
    if not isinstance(timeout,(int,float)) or isinstance(timeout,bool):raise ValueError('Invalid OT UDP timeout')
    try: valid=math.isfinite(timeout) and 0<timeout<=10
    except (OverflowError,TypeError):valid=False
    if not valid:raise ValueError('OT UDP timeout must be positive and at most 10 seconds')
    deadline=time.monotonic()+timeout
    family,kind,protocol,_,destination=unicast_destination(address,port,deadline)
    with socket.socket(family,kind,protocol) as raw:
        remaining=deadline-time.monotonic()
        if remaining<=0:raise TimeoutError('OT UDP deadline exceeded')
        raw.settimeout(remaining);raw.connect(destination)
        wire=RecordedSocket(raw,recorder,deadline)
        request,parse=operation(raw)
        wire.send(request)
        reply=wire.recv(_MAX_REPLY+1)
        recorder.termination='datagram_received'
        if not reply or len(reply)>_MAX_REPLY:raise ValueError('OT UDP response exceeds metadata bounds or is empty')
        return parse(reply)


def _fins_operation(raw):
    local=ipaddress.ip_address(raw.getsockname()[0])
    if local.version!=4:raise ValueError('FINS identity requires an IPv4 node candidate')
    sid=os.urandom(1)
    source=bytes([0,local.packed[-1] or 99,0])
    target=b'\0\0\0'
    request=b'\x80\0\x02'+target+source+sid+b'\x05\x01\x00'
    return request,_fins_parser(request)


def _fins_parser(request):
    def parse(reply):
        if (len(reply)<54 or reply[0] not in (0xc0,0xc1) or reply[1]!=0 or reply[2]>7
                or reply[3:6]!=request[6:9] or reply[6]!=request[3] or reply[8]!=request[5]
                or (request[4] and reply[7]!=request[4])
                or reply[9:12]!=request[9:12] or reply[12:14]!=b'\0\0'):
            raise ValueError('Invalid FINS CPU identity response, status or route/SID correlation')
        return {'operation':'CPU UNIT DATA READ (05/01)', 'model':_text(reply[14:34]),
                'version':_text(reply[34:54]),'source_node':request[7],
                'destination_node':request[4],
                'routing_limit':'Local network CPU unit 0 only; no routing-table operations.'},'fins'
    return parse


def _fins_tcp_frame(read):
    header=read.exact(8)
    length=int.from_bytes(header[4:],'big')
    if header[:4]!=b'FINS' or not 8<=length<=_MAX_REPLY:raise ValueError('Invalid FINS/TCP envelope')
    body=read.exact(length)
    command,error=struct.unpack('!II',body[:8])
    if error:raise ValueError('FINS/TCP request refused')
    return command,body[8:]


def _fins_tcp(sock,read,address,port):
    sock.sendall(bytes.fromhex('46494e530000000c000000000000000000000000'))
    command,nodes=_fins_tcp_frame(read)
    if command!=1 or len(nodes)!=8:raise ValueError('Invalid FINS/TCP node negotiation response')
    source,target=struct.unpack('!II',nodes)
    if not 1<=source<=254 or not 1<=target<=254:raise ValueError('Invalid FINS/TCP negotiated nodes')
    request=b'\x80\0\x02'+bytes([0,target,0,0,source,0])+os.urandom(1)+b'\x05\x01\x00'
    sock.sendall(b'FINS'+struct.pack('!III',len(request)+8,2,0)+request)
    command,reply=_fins_tcp_frame(read)
    if command!=2:raise ValueError('Invalid FINS/TCP CPU identity command')
    return _fins_parser(request)(reply)


def _slmp_tcp(sock,read,address,port):
    request,parse=_slmp_operation(None)
    sock.sendall(request)
    header=read.exact(9)
    length=int.from_bytes(header[7:9],'little')
    if header[:7]!=bytes.fromhex('d00000ffff0300') or length!=20:raise ValueError('Invalid SLMP identity frame')
    return parse(header+read.exact(length))


def _hart_tcp(sock,read,address,port):
    request,parse=_hart_operation(None)
    sock.sendall(request)
    header=read.exact(8)
    if header[6:8]!=b'\0\x0d':raise ValueError('Invalid HART-IP session-init frame length')
    return parse(header+read.exact(5))


def _slmp_operation(raw):
    request=bytes.fromhex('500000ffff03000600100001010000')
    def parse(reply):
        if (len(reply)!=29 or reply[:7]!=bytes.fromhex('d00000ffff0300')
                or int.from_bytes(reply[7:9],'little')!=20 or reply[9:11]!=b'\0\0'):
            raise ValueError('Invalid SLMP Read Type Name response length, route or status')
        return {'operation':'SLMP Read Type Name (0101/0000)', 'model':_text(reply[11:27]),
                'model_code':int.from_bytes(reply[27:29],'little'),
                'routing_limit':'Binary 3E, local station CPU 03FF. 3E has no transaction identifier; a new connected socket has one outstanding request.'},'melsec'
    return request,parse


def _hart_operation(raw):
    sequence=os.urandom(2)
    request=b'\x01\0\0\0'+sequence+b'\0\x0d\x01'+struct.pack('!I',5000)
    def parse(reply):
        if len(reply)!=13 or reply[:4]!=b'\x01\x01\0\0' or reply[4:6]!=sequence or reply[6:9]!=b'\0\x0d\x01':
            raise ValueError('Invalid HART-IP v1 session-init response/status/correlation')
        inactivity=int.from_bytes(reply[9:13],'big')
        if not 1<=inactivity<=600000:raise ValueError('Invalid HART-IP session inactivity interval')
        return {'operation':'Session Initiate only', 'version':1,'inactivity_close_ms':inactivity,
                'limitation':'Legacy v1 metadata only. No HART commands, subscriptions, secure v2 login, or server-selected port following; session expires by inactivity.'},'hartip'
    return request,parse


def _lantronix_operation(raw):
    def parse(reply):
        if not 24<=len(reply)<=30 or reply[:4]!=b'\0\0\0\xf7':raise ValueError('Invalid Lantronix firmware query response')
        return {'operation':'Query for Firmware Version (F6)', 'software_type':_text(reply[8:10]),
                'firmware_header_hex':reply[4:20].hex(), 'device_information_hex':reply[20:].hex(),
                'limitation':'Legacy 77FE firmware discovery only; setup-record read/write and reset functions are excluded.'},'lantronix-config'
    return b'\0\0\0\xf6',parse


def _vertx_operation(raw):
    def parse(reply):
        try:fields=reply.decode('ascii').rstrip('\0\r\n').split(';')
        except UnicodeError:raise ValueError('Invalid VertX discovery encoding') from None
        if (len(fields)!=10 or fields[0]!='discovered' or not re.fullmatch(r'\d{3}',fields[1]) or int(fields[1])!=len(reply)
                or not re.fullmatch(r'(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}',fields[2])
                or len(fields[3])>32 or fields[3] not in ('VertXController','EdgeController','EdgeEH400') or fields[-1]!=''
                or not fields[5].isdigit()):raise ValueError('Invalid VertX discovery response')
        try:ipaddress.ip_address(fields[4])
        except ValueError:raise ValueError('Invalid VertX advertised address') from None
        for value in fields[6:9]:_text(value.encode('ascii'))
        return {'operation':'discover only','mac':fields[2],'device_type':fields[3],
                'advertised_address':fields[4],'model':fields[6],'version':fields[7],'build_date':fields[8],
                'correlation_limit':'Discovery has no transaction token; reply is restricted to the one connected UDP peer.'},'vertx-edge'
    return b'discover;013;',parse


def _coap_options(raw):
    pos=0;number=0;options={};payload=b''
    while pos<len(raw):
        byte=raw[pos];pos+=1
        if byte==255:
            if pos==len(raw):raise ValueError('Empty CoAP payload marker')
            payload=raw[pos:];break
        delta=byte>>4;length=byte&15
        def extended(value):
            nonlocal pos
            if value<13:return value
            count={13:1,14:2}.get(value)
            if count is None or pos+count>len(raw):raise ValueError('Invalid CoAP option extension')
            out=int.from_bytes(raw[pos:pos+count],'big')+(13 if count==1 else 269);pos+=count
            return out
        delta=extended(delta);length=extended(length);number+=delta
        if pos+length>len(raw) or number>65535 or len(options)>=32 or number in options:raise ValueError('Invalid CoAP options')
        options[number]=raw[pos:pos+length];pos+=length
    return options,payload


def _coap_operation(raw):
    message=os.urandom(2);token=os.urandom(4)
    request=b'\x44\x01'+message+token+b'\xbb.well-known\x04core\x61\x28'
    def parse(reply):
        if len(reply)<8 or reply[0]!=0x64 or reply[2:4]!=message or reply[4:8]!=token or reply[1] not in (69,129,131,132):
            raise ValueError('Invalid CoAP discovery response type/code/correlation')
        options,payload=_coap_options(reply[8:])
        # Unknown critical options must not be silently accepted.
        if any(number&1 and number not in (23,) for number in options):raise ValueError('Unsupported CoAP critical response option')
        if 23 in options and (len(options[23])>3 or (options[23] and (options[23][-1]&7)==7)):
            raise ValueError('Invalid CoAP Block2 option')
        content=int.from_bytes(options.get(12,b''),'big')
        if reply[1]==69 and (content!=40 or not payload):raise ValueError('CoAP discovery did not return application/link-format')
        try:links=payload.decode('utf-8')
        except UnicodeError:raise ValueError('Invalid CoAP discovery UTF-8') from None
        if reply[1]==69 and not re.match(r'\s*<[^\x00-\x20<>]*>',links):raise ValueError('Invalid CoAP core-link-format payload')
        return {'operation':'GET /.well-known/core','response_code':f'{reply[1]>>5}.{reply[1]&31:02d}',
                'authentication_required':reply[1] in (129,131),'links':links,
                'partial':23 in options and bool(int.from_bytes(options[23],'big')&8),
                'limitation':'One piggyback ACK response only; no retransmission, block traversal, redirects, resource reads, or writes.'},'coap'
    return request,parse


def _coap_stream_frame(code, token=b'', body=b''):
    # RFC 8323 section 3.2: Length counts only Options/Payload, not Code/Token.
    length=len(body)
    if length<13:header=bytes([(length<<4)|len(token)])
    elif length<269:header=bytes([0xd0|len(token),length-13])
    else:header=bytes([0xe0|len(token)])+struct.pack('!H',length-269)
    return header+bytes([code])+token+body


def _coap_stream_read(read):
    first=read.exact(1)[0];length=first>>4;tkl=first&15;extra=0
    if tkl>8:raise ValueError('Invalid CoAP/TCP token length')
    if length>=13:
        extra={13:1,14:2,15:4}[length]
        length=int.from_bytes(read.exact(extra),'big')+{13:13,14:269,15:65805}[length]
    if 2+extra+tkl+length>_MAX_REPLY:raise ValueError('CoAP/TCP frame exceeds advertised 8192-byte maximum')
    code=read.exact(1)[0];token=read.exact(tkl);body=read.exact(length)
    return code,token,body


def _coap_csm(body, maximum):
    options,payload=_coap_options(body)
    if any(number&1 for number in options):raise ValueError('Unknown critical CoAP CSM option')
    if 2 in options:
        if len(options[2])>4:raise ValueError('Invalid CoAP CSM Max-Message-Size')
        maximum=int.from_bytes(options[2],'big')
    if 4 in options and options[4]:raise ValueError('Invalid CoAP CSM Block-Wise-Transfer option')
    # Unknown elective capabilities are ignored. Missing settings retain their
    # previous values, including when another empty CSM arrives (section 5.3).
    return maximum


def _coap_tcp(sock, read, address, port):
    # Send CSM first without waiting for the acceptor, avoiding CSM deadlock.
    # Only Max-Message-Size is advertised; block/BERT capability is not offered.
    sock.sendall(bytes.fromhex('30e1222000'))
    peer_closed=False
    try:
        code,unused_token,body=_coap_stream_read(read)
        peer_closed=code in (228,229)
        if code!=225:raise ValueError('CoAP/TCP peer did not begin with mandatory CSM')
        maximum=_coap_csm(body,1152)
        token=os.urandom(4)
        request=_coap_stream_frame(1,token,b'\xbb.well-known\x04core\x61\x28')
        if len(request)>maximum:raise ValueError('CoAP peer CSM maximum is too small for discovery GET')
        sock.sendall(request)
        # At most three intervening signaling/empty messages, then one response.
        # No keepalive requests, observations, retries, blocks, or new resources.
        for index in range(4):
            code,response_token,body=_coap_stream_read(read)
            if code==225:
                maximum=_coap_csm(body,maximum)
                continue
            if code==0:
                if response_token or body:raise ValueError('Malformed CoAP/TCP Empty message')
                continue
            if code==226:
                options,payload=_coap_options(body)
                if any(number&1 for number in options) or (2 in options and options[2]):
                    raise ValueError('Invalid CoAP Ping signaling options')
                pong=_coap_stream_frame(227,response_token,b'\x20' if 2 in options else b'')
                if len(pong)>maximum:raise ValueError('CoAP Pong exceeds peer maximum')
                sock.sendall(pong)
                continue
            if code in (228,229):
                peer_closed=True
                raise ValueError('CoAP peer released/aborted connection; alternate addresses are not followed')
            if code not in (69,129,131,132) or response_token!=token:
                raise ValueError('Invalid CoAP/TCP response code or token correlation')
            options,payload=_coap_options(body)
            if any(number&1 for number in options):
                raise ValueError('Unsupported CoAP/TCP critical option or block transfer')
            if len(options.get(12,b''))>2:raise ValueError('Invalid CoAP content-format option')
            if code==69 and (int.from_bytes(options.get(12,b''),'big')!=40 or not payload):
                raise ValueError('CoAP/TCP discovery did not return application/link-format')
            try:links=payload.decode('utf-8')
            except UnicodeError:raise ValueError('Invalid CoAP/TCP discovery UTF-8') from None
            if code==69 and not re.match(r'\s*<[^\x00-\x20<>]*>',links):
                raise ValueError('Invalid CoAP/TCP core-link-format payload')
            return {'operation':'CSM + GET /.well-known/core','transport_framing':'RFC 8323 TCP',
                    'peer_max_message_size':maximum,'response_code':f'{code>>5}.{code&31:02d}',
                    'authentication_required':code in (129,131),'links':links,'partial':False,
                    'limitation':'One TCP CSM/GET exchange only; bounded signaling, no block traversal, redirects, resources, or authentication.'},'coap'
        raise ValueError('CoAP/TCP signaling frame budget exhausted')
    except ValueError:
        # RFC 8323 sections 3.3/5.6 require Abort for framing/CSM errors. The
        # caller closes the connection. Abort carries no credentials or data.
        if not peer_closed:
            try:sock.sendall(b'\x00\xe5')
            except (OSError,ValueError):pass
        raise


def _udp_adapter(operation):
    def adapter(address,port,timeout,recorder):return _udp(address,port,timeout,recorder,operation)
    return adapter


TCP_ADAPTERS={'dnp3':_dnp3,'ads':_ads,'fox':_fox,'proconos':_proconos,
              'codesys':_codesys,'pcworx':_pcworx,'iec61850':_mms,'mms':_mms,
              'fins':_fins_tcp,'melsec':_slmp_tcp,'hartip':_hart_tcp,'coap':_coap_tcp}
UDP_ADAPTERS={name:_udp_adapter(operation) for name,operation in {
    'fins':_fins_operation,'melsec':_slmp_operation,'hartip':_hart_operation,
    'lantronix-config':_lantronix_operation,'vertx-edge':_vertx_operation,'coap':_coap_operation}.items()}

UNSUPPORTED_REASONS={
    'smux':'RFC 1227 begins with authenticated SimpleOpen (including identity/password); no universal non-login identification exchange.',
    'cipio':'Cyclic connected CIP I/O carries process data and needs established connection state; no universal read-only identity handshake on that endpoint.',
    'plc':'PLC is a device class, not a wire protocol. Select a concrete protocol and logical address.',
    'lantronix':'TCP serial gateways may forward arbitrary bytes to attached equipment; setup port requires login/configuration. Use UDP lantronix-config firmware discovery.',
    'srtp':'GE SRTP controller identification has no sufficiently established no-login request/response validation here; opaque PLC service packets are not sent.',
}

CAPABILITIES={
    'dnp3':{'operation':'REQUEST_LINK_STATUS','protocol':'dnp3','transports':['tcp'],
        'limitation':'Link status only; candidate target link 1/local source 1024; sender role follows response DIR. CRC/address/function checked; no application objects.',
        'sources':['https://github.com/dnp3/opendnp3/blob/master/cpp/lib/src/link/CRC.cpp','https://github.com/dnp3/opendnp3/blob/master/cpp/lib/src/link/LinkFrame.cpp','https://github.com/dnp3/opendnp3/blob/master/cpp/lib/src/link/LinkHeader.h','https://github.com/dnp3/opendnp3/blob/master/cpp/lib/src/link/LinkLayerConstants.h']},
    'ads':{'operation':'READ_DEVICE_INFO (command 1)','protocol':'ads','transports':['tcp'],
        'limitation':'One IPv4-derived AMS NetId candidate, logical System Service port 10000; no route creation or login.',
        'sources':['https://download.beckhoff.com/download/document/automation/twincat2/TwinCAT_2_ADS_EN.pdf']},
    'fox':{'operation':'initial hello','protocol':'fox','transports':['tcp'],
        'limitation':'Plain Fox initial metadata only; no authentication exchange.',
        'sources':['https://github.com/nmap/nmap/blob/master/scripts/fox-info.nse','https://github.com/eniac/zgrab/blob/master/ztools/scada/fox/fox.go']},
    'proconos':{'operation':'initial runtime/device identity','protocol':'proconos','transports':['tcp'],
        'limitation':'First 77 identity bytes only; additional project/source metadata is not read. Source format has no transaction identifier.',
        'sources':['https://github.com/digitalbond/Redpoint/blob/master/proconos-info.nse']},
    'codesys':{'operation':'v2 runtime identity','protocol':'codesys','transports':['tcp'],
        'limitation':'Legacy v2 little-endian candidate only, fixed identity fields; no v3 or gateway login.',
        'sources':['https://github.com/digitalbond/Redpoint/blob/master/codesys-v2-discover.nse']},
    'pcworx':{'operation':'initial communications','protocol':'pcworx','transports':['tcp'],
        'limitation':'Protocol session metadata only; undocumented secondary initialization and PLC-info requests excluded.',
        'sources':['https://github.com/nmap/nmap/blob/master/scripts/pcworx-info.nse']},
    'iec61850':{'operation':'unauthenticated COTP/ACSE/MMS association','protocol':'mms','transports':['tcp'],
        'limitation':'Verifies negotiated MMS only, never IEC 61850 model from COTP. Default selectors 0001; one unsegmented association response with definite BER/short session framing. No model, process, or report access.',
        'sources':['https://github.com/mz-automation/libiec61850/blob/master/src/mms/iso_acse/acse.c','https://github.com/mz-automation/libiec61850/blob/master/src/mms/iso_presentation/iso_presentation.c','https://github.com/mz-automation/libiec61850/blob/master/src/mms/iso_mms/client/mms_client_initiate.c']},
    'fins':{'operation':'CPU UNIT DATA READ (05/01)','protocol':'fins','transports':['tcp','udp'],
        'limitation':'Local CPU unit, IPv4 source-node candidate; model/version only. No memory/process data.',
        'sources':['https://github.com/nmap/nmap/blob/master/scripts/omron-info.nse','https://www.omron.com.tw/data_pdf/closed/mnu/v203-e1-03_itnc-eis_eix.pdf']},
    'melsec':{'operation':'SLMP Read Type Name (0101/0000)','protocol':'melsec','transports':['tcp','udp'],
        'limitation':'Binary 3E local CPU 03FF only; no transaction token exists, connected peer and one outstanding request.',
        'sources':['https://dl.mitsubishielectric.com/dl/fa/document/manual/plc/sh080956eng/sh080956engm.pdf']},
    'hartip':{'operation':'Session Initiate','protocol':'hartip','transports':['tcp','udp'],
        'limitation':'v1 session metadata only, five-second inactivity request; no HART commands, auth, or server-selected port follow.',
        'sources':['https://github.com/FieldCommGroup/WindowsHartIpClient/blob/master/HartIPConnect/HARTMessage.cs']},
    'lantronix-config':{'operation':'Firmware Version query (F6)','protocol':'lantronix-config','transports':['udp'],
        'limitation':'Firmware discovery only. Never setup record reads/writes, configuration, or reset.',
        'sources':['https://www.lantronix.com/wp-content/uploads/pdf/XPress-DR_UG_Rev_A.pdf']},
    'vertx-edge':{'operation':'discover','protocol':'vertx-edge','transports':['udp'],
        'limitation':'Unicast discover only; verified device-type labels VertXController, EdgeController and EdgeEH400 only. Never LED blink, management, or door control. Protocol has no transaction token.',
        'sources':['https://nosedookie.blogspot.com/2011/07/identifying-and-querying-hid-vertx.html']},
    'coap':{'operation':'GET /.well-known/core; CSM first for TCP','protocol':'coap','transports':['tcp','udp'],
        'limitation':'UDP: one piggyback ACK only. TCP: RFC 8323 CSM + one GET, token/framing/CSM maximum validated, at most three intervening signaling frames. No block traversal, resource/control operations, authentication, redirect or transport fallback. Provider TCP labeling is not evidence of live TCP support.',
        'sources':['https://www.rfc-editor.org/rfc/rfc7252','https://www.rfc-editor.org/rfc/rfc6690','https://www.rfc-editor.org/rfc/rfc8323']},
}

CAPABILITIES['mms'] = dict(CAPABILITIES['iec61850'])
