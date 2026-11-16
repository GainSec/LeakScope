"""Small bounded identity handshakes; no data/collection/control operations.

Wire formats follow the Modbus Application Protocol (43/14), OPC UA Part 6
(Hello/Acknowledge), EtherNet/IP encapsulation ListIdentity, and IEC 104 TESTFR.
These are service-identification signals, not proof of ownership or exposure.
"""
import socket
import ssl
import struct
import time
import re
from uuid import uuid4, UUID

from bson import BSON
from .wire import RecordedSocket, unicast_destination, connect_tcp

IDENTITY_TYPES=frozenset({'redis','mysql','postgresql','memcached','mongo','ftp','sftp','ssh','modbus','opcua','ethernetip','iec104','bacnet','nfs','smb','rsync','s7','mqtt','rtsp','grpc'})


class Reader:
    def __init__(self,sock,deadline):self.sock=sock;self.deadline=deadline;self.buffer=b''
    def fill(self,maximum=4096):
        remaining=self.deadline-time.monotonic()
        if remaining<=0:raise ValueError('Native identity deadline exceeded')
        self.sock.settimeout(remaining)
        chunk=self.sock.recv(maximum)
        if not chunk:raise ValueError('Service closed the identity handshake')
        self.buffer+=chunk
    def exact(self,size):
        if not 0<=size<=65536:raise ValueError('Native response exceeds the 64 KiB limit')
        while len(self.buffer)<size:self.fill(min(4096,size-len(self.buffer)))
        data,self.buffer=self.buffer[:size],self.buffer[size:]
        return data
    def until(self,marker,limit=16384):
        while marker not in self.buffer:
            if len(self.buffer)>=limit:raise ValueError('Native response exceeds the metadata limit')
            self.fill()
        end=self.buffer.index(marker)+len(marker)
        if end>limit:raise ValueError('Native response exceeds the metadata limit')
        data,self.buffer=self.buffer[:end],self.buffer[end:]
        return data


def _redis(sock,read,address,port):
    sock.sendall(b'*1\r\n$4\r\nPING\r\n');pong=read.until(b'\r\n',4096)
    if pong.startswith(b'-NOAUTH'):
        return {'operation':'PING','authentication_required':True},'redis'
    if pong!=b'+PONG\r\n':raise ValueError('The service did not return a Redis PONG')
    sock.sendall(b'*2\r\n$4\r\nINFO\r\n$6\r\nserver\r\n')
    header=read.until(b'\r\n',4096)
    if header.startswith(b'-'):
        return {'operation':'PING + INFO server','server':{},'info_available':False},'redis'
    if not header.startswith(b'$'):raise ValueError('Invalid Redis INFO framing')
    try:length=int(header[1:-2])
    except ValueError:raise ValueError('Invalid Redis INFO length') from None
    if not 0<=length<=16384:raise ValueError('Redis INFO exceeds the metadata limit')
    body=read.exact(length);ending=read.exact(2)
    if ending!=b'\r\n':raise ValueError('Invalid Redis INFO terminator')
    allowed={'redis_version','redis_mode','os','arch_bits','multiplexing_api','process_supervised','tcp_port','uptime_in_seconds'}
    values={}
    for line in body.decode('utf-8',errors='replace').splitlines():
        if ':' in line:
            key,value=line.split(':',1)
            if key in allowed:values[key]=value[:500]
    return {'operation':'PING + INFO server','server':values},'redis'


def _mysql(sock,read,address,port):
    header=read.exact(4);size=int.from_bytes(header[:3],'little')
    if header[3]!=0 or not 10<=size<=16384:raise ValueError('Invalid MySQL greeting header')
    body=read.exact(size)
    if body[0]!=10 or b'\x00' not in body[1:]:raise ValueError('Invalid MySQL protocol greeting')
    version=body[1:].split(b'\x00',1)[0]
    if len(version)>200:raise ValueError('Invalid MySQL version string')
    return {'operation':'server greeting only; no login','protocol_version':10,'version':version.decode('utf-8',errors='replace')},'mysql'


def _postgres(sock,read,address,port):
    sock.sendall(struct.pack('!II',8,80877103));response=read.exact(1)
    if response not in (b'S',b'N'):raise ValueError('Invalid PostgreSQL SSLRequest response')
    return {'operation':'SSLRequest only; no login','ssl_supported':response==b'S'},'postgresql'


def _memcached(sock,read,address,port):
    sock.sendall(b'stats\r\n');body=read.until(b'END\r\n',16384)
    stats={}
    for line in body.decode('utf-8',errors='replace').splitlines():
        if line=='END':continue
        parts=line.split(' ',2)
        if len(parts)!=3 or parts[0]!='STAT':raise ValueError('Invalid Memcached stats response')
        if len(stats)<64:stats[parts[1][:100]]=parts[2][:500]
    if not stats:raise ValueError('Memcached returned no stats identity metadata')
    return {'operation':'stats; no item/key dump','stats':stats},'memcached'


def _mongo(sock,read,address,port):
    body=struct.pack('<I',0)+b'\x00'+BSON.encode({'hello':1,'$db':'admin'})
    sock.sendall(struct.pack('<iiii',16+len(body),1,0,2013)+body)
    header=read.exact(16);size,_,response_to,opcode=struct.unpack('<iiii',header)
    if response_to!=1 or opcode!=2013 or not 21<=size<=65536:raise ValueError('Invalid MongoDB hello header')
    body=read.exact(size-16)
    flags=struct.unpack('<I',body[:4])[0]
    if flags&~1 or body[4]!=0:raise ValueError('Unsupported MongoDB hello framing')
    document=body[5:-4] if flags&1 else body[5:]
    try:result=BSON(document).decode()
    except Exception:raise ValueError('Invalid MongoDB hello document') from None
    if 'ok' not in result:raise ValueError('MongoDB hello document has no status')
    allowed={'ok','helloOk','isWritablePrimary','ismaster','secondary','maxWireVersion','minWireVersion','logicalSessionTimeoutMinutes','msg','code','codeName'}
    return {'operation':'hello only; no collection reads','hello':{key:value for key,value in result.items() if key in allowed}},'mongo'


def _ssh(sock,read,address,port):
    for _ in range(10):
        line=read.until(b'\n',4096)
        if line.startswith((b'SSH-2.0-',b'SSH-1.99-')):
            return {'operation':'server identification only; no authentication','banner':line.decode('utf-8',errors='replace').strip()[:500],'sftp_subsystem_verified':False},'ssh'
    raise ValueError('No SSH server identification was received')


def _ftp(sock,read,address,port):
    line=read.until(b'\n',4096).decode('utf-8',errors='replace').strip()
    if not line.startswith(('220 ','220-')) or 'ftp' not in line.lower():raise ValueError('Greeting did not identify an FTP service')
    return {'operation':'server greeting only; no login','banner':line[:1000]},'ftp'


def _modbus(sock,read,address,port):
    sock.sendall(bytes.fromhex('000100000005012b0e0100'))
    tid,protocol,length,unit=struct.unpack('!HHHB',read.exact(7))
    if tid!=1 or protocol!=0 or unit!=1 or not 2<=length<=4096:raise ValueError('Invalid Modbus MBAP header')
    body=read.exact(length-1)
    if body[0]==0xab and len(body)==2:
        return {'operation':'Read Device Identification (43/14)','exception_code':body[1],'identity_available':False},'modbus'
    if len(body)<7 or body[:3]!=bytes([43,14,1]):raise ValueError('Invalid Modbus device identity response')
    objects=[];position=7
    if body[6]>32:raise ValueError('Modbus identity object count exceeds limit')
    for _ in range(body[6]):
        if position+2>len(body):raise ValueError('Truncated Modbus identity object')
        object_id,size=body[position:position+2];position+=2
        if position+size>len(body):raise ValueError('Truncated Modbus identity value')
        objects.append({'id':object_id,'value':body[position:position+size].decode('utf-8',errors='replace')});position+=size
    if position!=len(body):raise ValueError('Invalid Modbus identity trailing bytes')
    return {'operation':'Read Device Identification (43/14)','objects':objects,'more_follows':bool(body[4])},'modbus'


def _opcua(sock,read,address,port):
    host='['+address+']' if ':' in address else address
    url=f'opc.tcp://{host}:{port}/'.encode()
    body=struct.pack('<IIIIIi',0,65536,65536,65536,1,len(url))+url
    sock.sendall(b'HELF'+struct.pack('<I',8+len(body))+body)
    header=read.exact(8);size=struct.unpack('<I',header[4:])[0]
    if header[:4]!=b'ACKF' or size!=28:raise ValueError('OPC UA did not return a bounded Hello Acknowledge')
    version,receive,send,message,chunks=struct.unpack('<IIIII',read.exact(20))
    return {'operation':'Hello/Acknowledge transport only; no session','protocol_version':version,'receive_buffer':receive,'send_buffer':send,'max_message_size':message,'max_chunk_count':chunks},'opcua'


def _ethernetip(sock,read,address,port):
    context=b'LeakScop'
    sock.sendall(struct.pack('<HHII8sI',0x63,0,0,0,context,0))
    command,size,session,status,echo,options=struct.unpack('<HHII8sI',read.exact(24))
    if command!=0x63 or status!=0 or echo!=context or not 2<=size<=4096:raise ValueError('Invalid EtherNet/IP ListIdentity header')
    body=read.exact(size);count=struct.unpack('<H',body[:2])[0];position=2
    if not 1<=count<=16:raise ValueError('Invalid EtherNet/IP identity count')
    for _ in range(count):
        if position+4>len(body):raise ValueError('Truncated EtherNet/IP identity item')
        kind,length=struct.unpack('<HH',body[position:position+4]);position+=4
        item=body[position:position+length];position+=length
        if len(item)!=length:raise ValueError('Truncated EtherNet/IP identity value')
        if kind==0x0c:
            if len(item)<33 or len(item)<33+item[32]:raise ValueError('Invalid EtherNet/IP product identity')
            vendor,device,product=struct.unpack('<HHH',item[18:24])
            return {'operation':'ListIdentity only','vendor_id':vendor,'device_type':device,'product_code':product,'revision':f'{item[24]}.{item[25]}','product_name':item[33:33+item[32]].decode('utf-8',errors='replace')},'ethernetip'
    raise ValueError('EtherNet/IP response contained no device identity')


def _iec104(sock,read,address,port):
    sock.sendall(bytes.fromhex('680443000000'))
    if read.exact(6)!=bytes.fromhex('680483000000'):raise ValueError('IEC 104 did not return TESTFR confirmation')
    return {'operation':'TESTFR only; no STARTDT or process data','response':'TESTFR confirmation'},'iec104'


def _nfs(sock,read,address,port):
    """RFC1813 3.3.0 NULL, RFC5531 RPC v2 and TCP record marking."""
    xid=0x4c534350
    request=struct.pack('!10I',xid,0,2,100003,3,0,0,0,0,0)
    sock.sendall(struct.pack('!I',0x80000000|len(request))+request)
    pieces=[];total=0
    for _ in range(4):
        marker=struct.unpack('!I',read.exact(4))[0];size=marker&0x7fffffff;total+=size
        if total>4096:raise ValueError('NFS RPC response exceeded its record limit')
        pieces.append(read.exact(size))
        if marker&0x80000000:break
    else:raise ValueError('NFS RPC response exceeded its fragment limit')
    body=b''.join(pieces)
    if len(body)<24:raise ValueError('Truncated NFS RPC response')
    response_xid,message_type,reply_status,flavor,verifier_size=struct.unpack('!5I',body[:20])
    if response_xid!=xid or message_type!=1 or reply_status!=0 or verifier_size>400:raise ValueError('NFS RPC NULL was not accepted')
    offset=20+((verifier_size+3)//4)*4
    if offset+4!=len(body) or struct.unpack('!I',body[offset:offset+4])[0]!=0:raise ValueError('NFS v3 NULL did not return a successful void reply')
    return {'operation':'NFSv3 RPC NULL only; no MOUNT, exports or file access','rpc_version':2,'program':100003,'nfs_version':3,'procedure':'NULL','reply_verifier_flavor':flavor},'nfs'


def _smb(sock,read,address,port):
    """MS-SMB2 2.2.1/2.2.3/2.2.4; no SMB1 or SMB3.1.1 contexts."""
    offered={0x0202:'2.0.2',0x0210:'2.1',0x0300:'3.0',0x0302:'3.0.2'}
    header=struct.pack('<4sHHIHHIIQIIQ16s',b'\xfeSMB',64,0,0,0,1,0,0,1,0,0,0,b'\x00'*16)
    body=struct.pack('<HHHHI16sQ',36,len(offered),1,0,0,uuid4().bytes_le,0)+struct.pack('<4H',*offered)
    request=header+body;sock.sendall(len(request).to_bytes(4,'big')+request)
    prefix=read.exact(4);size=int.from_bytes(prefix,'big')
    if prefix[0]!=0 or not 128<=size<=16384:raise ValueError('Invalid SMB direct-TCP record length')
    response=read.exact(size)
    if response[:4]!=b'\xfeSMB' or struct.unpack('<H',response[4:6])[0]!=64 or struct.unpack('<H',response[12:14])[0]!=0:raise ValueError('Invalid SMB2 NEGOTIATE response header')
    status,flags,next_command=struct.unpack('<I',response[8:12])[0],struct.unpack('<I',response[16:20])[0],struct.unpack('<I',response[20:24])[0]
    if not flags&1 or flags&2 or next_command or struct.unpack('<Q',response[24:32])[0]!=1 or struct.unpack('<Q',response[40:48])[0]!=0:raise ValueError('Unexpected SMB2 NEGOTIATE response routing')
    if status:raise ValueError(f'SMB2 NEGOTIATE failed with status 0x{status:08x}')
    values=struct.unpack('<HHHH16sIIIIQQHHI',response[64:128])
    structure,security,dialect,contexts,guid,capabilities,max_transact,max_read,max_write,_,_,security_offset,security_length,_=values
    if structure!=65 or dialect not in offered or contexts or security&~3:raise ValueError('Invalid or unsupported SMB2 negotiated dialect')
    if security_length and (security_offset<128 or security_offset+security_length>len(response)):raise ValueError('Invalid SMB2 security-buffer bounds')
    return {'operation':'SMB2 NEGOTIATE only; no SessionSetup, TreeConnect or file access','dialect':offered[dialect],'signing_enabled':bool(security&1),'signing_required':bool(security&2),'server_guid':str(UUID(bytes_le=guid)),'capabilities':capabilities,'max_transact_size':max_transact,'max_read_size':max_read,'max_write_size':max_write,'offered_dialects':list(offered.values()),'smb311_offered':False},'smb'


def _rsync(sock,read,address,port):
    """Read the upstream rsync daemon greeting; do not choose/list a module."""
    line=read.until(b'\n',1024).decode('ascii',errors='replace').strip()
    match=re.fullmatch(r'@RSYNCD: ([0-9]{1,3})(?:\.([0-9]{1,3}))?(?: ([A-Za-z0-9 _+.-]{1,300}))?',line)
    if not match:raise ValueError('Service did not return an rsync daemon greeting')
    major=int(match[1]);minor=int(match[2] or 0)
    if not 1<=major<=255 or (major>=30 and match[2] is None):raise ValueError('Invalid rsync daemon protocol version')
    return {'operation':'server greeting only; no module list, authentication or files','protocol_version':f'{major}.{minor}','authentication_digests':(match[3] or '').split()[:16]},'rsync'


def _tpkt(read):
    header=read.exact(4)
    size=struct.unpack('!H',header[2:4])[0]
    if header[:2]!=b'\x03\x00' or not 7<=size<=4096:raise ValueError('Invalid bounded ISO-on-TCP TPKT record')
    return read.exact(size-4)


def _s7(sock,read,address,port):
    """COTP + SetupCommunication packets matched to Nmap/Snap7 sources.

    Remote TSAP 0x0102 is one explicit rack/slot profile, not a sweep. This
    does not send the SZL queries that follow negotiation in s7-info.nse.
    """
    sock.sendall(bytes.fromhex('0300001611e00000001400c1020100c2020102c0010a'))
    confirm=_tpkt(read)
    if len(confirm)<7 or confirm[0]+1!=len(confirm) or confirm[1]!=0xd0 or confirm[2:4]!=b'\x00\x14' or confirm[6]!=0:raise ValueError('COTP connection was not confirmed for the requested profile')
    sock.sendall(bytes.fromhex('0300001902f08032010000000000080000f0000001000101e0'))
    response=_tpkt(read)
    if len(response)!=23 or response[:3]!=b'\x02\xf0\x80' or response[3:5]!=b'\x32\x03':raise ValueError('Response did not contain an S7 SetupCommunication acknowledgement')
    s7=response[3:]
    reserved,reference,parameter_size,data_size=struct.unpack('!4H',s7[2:10])
    if reserved or reference or parameter_size!=8 or data_size or s7[10:12]!=b'\x00\x00' or s7[12:14]!=b'\xf0\x00':raise ValueError('S7 SetupCommunication was not accepted')
    calling,called,pdu=struct.unpack('!3H',s7[14:20])
    if calling!=1 or called!=1 or not 128<=pdu<=480:raise ValueError('S7 returned incompatible negotiated limits')
    return {'operation':'COTP + S7 SetupCommunication only; no SZL or process data','local_tsap':'0x0100','remote_tsap':'0x0102','max_amq_calling':calling,'max_amq_called':called,'pdu_length':pdu},'s7'


def _bacnet(address,port,timeout,recorder=None):
    deadline=time.monotonic()+timeout
    family,kind,protocol,_,destination=unicast_destination(address,port,deadline)
    if family!=socket.AF_INET:raise ValueError('This BACnet identity adapter supports IPv4 unicast only')
    with socket.socket(family,kind,protocol) as sock:
        remaining=deadline-time.monotonic()
        if remaining<=0:raise TimeoutError('BACnet identity deadline exceeded')
        sock.settimeout(remaining);sock.connect(destination)
        wire=RecordedSocket(sock,recorder,deadline) if recorder is not None else sock
        wire.send(bytes.fromhex('810a000801001008'));body=wire.recv(4097)
    if not 8<=len(body)<=4096 or body[0]!=0x81 or body[1] not in (0x0a,0x0b) or struct.unpack('!H',body[2:4])[0]!=len(body):raise ValueError('Invalid BACnet BVLC response')
    if body[4:8]!=bytes.fromhex('01001000'):raise ValueError('BACnet response was not a direct unicast I-Am')
    payload=body[8:]
    if len(payload)<5 or payload[0]!=0xc4:raise ValueError('Invalid BACnet device identifier')
    device=struct.unpack('!I',payload[1:5])[0]
    if device>>22!=8:raise ValueError('BACnet I-Am did not identify a Device object')
    values=[];position=5
    for tag_number in (2,9,2):
        if position>=len(payload):raise ValueError('Truncated BACnet I-Am metadata')
        tag=payload[position];position+=1;size=tag&7
        if tag>>4!=tag_number or tag&8 or not 1<=size<=4 or position+size>len(payload):raise ValueError('Invalid BACnet I-Am application value')
        values.append(int.from_bytes(payload[position:position+size],'big'));position+=size
    if position!=len(payload):raise ValueError('Unexpected BACnet I-Am trailing bytes')
    return {'operation':'unicast Who-Is / I-Am only','device_instance':device&0x3fffff,'max_apdu':values[0],'segmentation':values[1],'vendor_id':values[2]},'bacnet'


def _mqtt(sock,read,address,port):
    # MQTT 3.1.1: clean ephemeral connection, no credentials, will or subscriptions.
    client_id=('leakscope-'+uuid4().hex[:12]).encode('ascii')
    body=b'\x00\x04MQTT\x04\x02\x00\x00'+struct.pack('!H',len(client_id))+client_id
    sock.sendall(bytes((0x10,len(body)))+body)
    response=read.exact(4)
    if response[:2]!=b'\x20\x02' or response[2]!=0 or response[3]>5:
        raise ValueError('Invalid MQTT 3.1.1 clean-session CONNACK')
    code=response[3]
    if code==0:sock.sendall(b'\xe0\x00')
    return {'operation':'MQTT 3.1.1 clean CONNECT / CONNACK, then DISCONNECT if accepted; no publish or subscribe',
            'protocol_version':'3.1.1','return_code':code,'connection_accepted':code==0,
            'authentication_required':code in (4,5),'clean_session':True},'mqtt'


def _rtsp(sock,read,address,port):
    sock.sendall(b'OPTIONS * RTSP/1.0\r\nCSeq: 1\r\nUser-Agent: LeakScope\r\n\r\n')
    response=read.until(b'\r\n\r\n',8192).decode('iso-8859-1')
    lines=response.split('\r\n');match=re.fullmatch(r'RTSP/1\.0 ([1-5][0-9]{2}) [^\r\n]{0,200}',lines[0])
    if not match:raise ValueError('Service did not return an RTSP/1.0 response')
    headers={}
    for line in lines[1:]:
        if ':' in line:
            key,value=line.split(':',1);key=key.strip().lower()
            if key in headers:raise ValueError('Ambiguous RTSP response headers')
            headers[key]=value.strip()
    if headers.get('cseq')!='1':raise ValueError('RTSP response did not match the OPTIONS request CSeq')
    return {'operation':'RTSP OPTIONS only; no DESCRIBE, SETUP or PLAY','protocol_version':'1.0',
            'status':int(match[1]),'server':headers.get('server','')[:1000],
            'public':headers.get('public','')[:1000],'authentication_required':match[1]=='401'},'rtsp'


_ADAPTERS={'redis':_redis,'mysql':_mysql,'postgresql':_postgres,'memcached':_memcached,'mongo':_mongo,'ftp':_ftp,'ssh':_ssh,'sftp':_ssh,'modbus':_modbus,'opcua':_opcua,'ethernetip':_ethernetip,'iec104':_iec104,'nfs':_nfs,'smb':_smb,'rsync':_rsync,'s7':_s7,'mqtt':_mqtt,'rtsp':_rtsp}

from . import native_databases, native_ot_extended, native_messaging
from .capture_diagnostics import CaptureDiagnosticError

_ADAPTERS.update(native_databases.TCP_ADAPTERS)
_ADAPTERS.update(native_ot_extended.TCP_ADAPTERS)
_ADAPTERS.update(native_messaging.TCP_ADAPTERS)
_UDP_ADAPTERS={'bacnet':_bacnet,**native_ot_extended.UDP_ADAPTERS}
IDENTITY_TYPES=frozenset(IDENTITY_TYPES|_ADAPTERS.keys()|_UDP_ADAPTERS.keys()|{'cardano','amaru','cardanonode'})
_CAPABILITIES={**native_databases.CAPABILITIES,**native_ot_extended.CAPABILITIES,**native_messaging.CAPABILITIES}
_LIMITATIONS={**{key:value['reason'] for key,value in native_databases.CONSTRAINTS.items()},
              **native_ot_extended.UNSUPPORTED_REASONS,**native_messaging.UNSUPPORTED_REASONS}
_BASE_OPERATIONS={'redis':'PING and INFO server only','mysql':'Server greeting; no login','postgresql':'SSLRequest capability only',
    'memcached':'Server stats only','mongo':'hello metadata only','ftp':'Server greeting; no login','sftp':'SSH identification banner only',
    'ssh':'SSH identification banner only','modbus':'Read Device Identification 43/14','opcua':'Hello/Acknowledge transport only',
    'ethernetip':'ListIdentity only','iec104':'TESTFR only','nfs':'NFSv3 RPC NULL only','smb':'SMB2 NEGOTIATE only',
    'rsync':'Server greeting only','s7':'COTP and SetupCommunication only','mqtt':'Clean protocol connection; no messages or subscriptions',
    'rtsp':'OPTIONS only','grpc':'gRPC Health/Check only','bacnet':'Unicast Who-Is/I-Am only'}
_PROTOCOL_ALIASES={'amaru':'cardano','cardanonode':'cardano','cardano-node':'cardano','ouroboros':'cardano','ms-sql-s':'mssql','ms-sql':'mssql','oracle-tns':'oracle','cql':'cassandra',
    'niagara-fox':'fox','hart-ip':'hartip','iec 61850-8-1 mms':'mms','sap-hana':'hana','sap-maxdb':'maxdb',
    'mapi':'monetdb','omron-fins':'fins','fins-tcp':'fins','melsec-q':'melsec','melsec-q-tcp':'melsec',
    'melsec-q-udp':'melsec','slmp':'melsec','ge-srtp':'srtp','ibm-db2-admin':'db2-das'}


def canonical_identity_service(service):
    name=str(service or '').strip().lower()
    return _PROTOCOL_ALIASES.get(name,name)


def identity_transports(service):
    service=canonical_identity_service(service)
    if service=='cardano':return ('tcp',)
    return tuple(transport for transport,available in (('tcp',service in _ADAPTERS or service=='grpc'),('udp',service in _UDP_ADAPTERS)) if available)


def identity_capability(service):
    service=canonical_identity_service(service)
    capability=dict(_CAPABILITIES.get(service,{}))
    transports=identity_transports(service)
    capability.update(implemented=bool(transports),transports=list(transports))
    capability.setdefault('operation',_BASE_OPERATIONS.get(service,''))
    capability.setdefault('protocol','ssh' if service=='sftp' else service if transports else '')
    capability.setdefault('limitation',_LIMITATIONS.get(service,'SSH banner does not verify the SFTP subsystem.' if service=='sftp' else ''))
    capability.setdefault('sources',[])
    if service=='cardano':capability.update(operation='Version query and bounded network-magic refusal',protocol='cardano',limitation='Response signature is experimental and can be imitated; MUX framing or JA4TS alone does not identify the client.')
    return capability


def identify(address,port,service_type,transport='tcp',timeout=3,recorder=None,tls=False):
    service_type=canonical_identity_service(service_type)
    if service_type not in IDENTITY_TYPES:raise ValueError('Native identity is not implemented for this protocol')
    try:
        timeout=float(timeout)
        if not 0.05<=timeout<=10:raise ValueError
    except (TypeError,ValueError):raise ValueError('Native timeout must be between 0.05 and 10 seconds') from None
    try:
        if service_type=='cardano':
            if transport!='tcp' or tls:raise ValueError('Native Ouroboros requires plaintext TCP')
            from .ouroboros import identify_cardano
            return identify_cardano(address,port,timeout,recorder)
        if transport not in identity_transports(service_type):raise ValueError('Native identity is unavailable for this service transport')
        if transport=='udp':return _UDP_ADAPTERS[service_type](address,port,timeout,recorder)
        deadline=time.monotonic()+timeout
        with connect_tcp(address,port,deadline) as raw:
            sock=raw
            remaining=deadline-time.monotonic()
            if remaining<=0:raise TimeoutError('Native identity deadline exceeded')
            sock.settimeout(remaining)
            try:
                if tls:
                    if service_type not in ('grpc','milvus'):raise ValueError('TLS identity is implemented only for gRPC family captures')
                    context=ssl.create_default_context();context.set_alpn_protocols(['h2'])
                    sock=context.wrap_socket(raw,server_hostname=address)
                    if sock.selected_alpn_protocol()!='h2':raise ValueError('The TLS endpoint did not negotiate HTTP/2')
                if recorder is not None:
                    recorder.connected=True;recorder.tls=tls
                    wire=RecordedSocket(sock,recorder,deadline)
                else:wire=sock
                if service_type=='grpc':
                    from .grpc_native import identify_grpc
                    return identify_grpc(wire,deadline,address,port,tls=tls)
                return _ADAPTERS[service_type](wire,Reader(wire,deadline),address,port)
            finally:
                if sock is not raw:sock.close()
    except ssl.SSLCertVerificationError:raise CaptureDiagnosticError('tls_certificate') from None
    except TimeoutError:raise CaptureDiagnosticError('timeout') from None
    except ConnectionRefusedError:raise CaptureDiagnosticError('connection_refused') from None
    except ConnectionResetError:raise CaptureDiagnosticError('connection_reset') from None
    except OSError:raise CaptureDiagnosticError('connection_failed') from None
