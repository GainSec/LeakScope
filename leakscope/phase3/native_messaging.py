"""Bounded broker version/status negotiation before authentication or messages."""
import re
import struct
import time


class _Fields:
    def __init__(self,data):self.data=data;self.offset=0;self.items=0
    def take(self,size):
        if not 0<=size<=16384 or self.offset+size>len(self.data):raise ValueError('Invalid AMQP field length')
        value=self.data[self.offset:self.offset+size];self.offset+=size;return value
    def integer(self,fmt):return struct.unpack(fmt,self.take(struct.calcsize(fmt)))[0]
    def longstr(self):return self.take(self.integer('!I'))
    def text(self,data):return data.decode('utf-8',errors='replace')[:500]
    def name(self):
        data=self.take(self.integer('!B'))
        if not data or len(data)>128 or any(byte<33 or byte>126 for byte in data):
            raise ValueError('Invalid bounded AMQP field name')
        return data.decode('ascii')
    def value(self,depth=0):
        self.items+=1
        if depth>3 or self.items>128:raise ValueError('AMQP properties exceed nesting/item limits')
        kind=self.take(1)
        fixed={b't':1,b'b':1,b'B':1,b'U':2,b'u':2,b's':2,b'I':4,b'i':4,b'f':4,b'L':8,b'l':8,b'd':8,b'T':8,b'D':5}
        if kind in fixed:
            value=self.take(fixed[kind]);return bool(value[0]) if kind==b't' else None
        if kind==b'S':return self.text(self.longstr())
        if kind==b'x':self.longstr();return None
        if kind==b'V':return None
        if kind in (b'F',b'A'):
            size=self.integer('!I');end=self.offset+size
            if end>len(self.data):raise ValueError('Invalid AMQP nested field length')
            count=0;names=set()
            while self.offset<end:
                count+=1
                if count>64:raise ValueError('AMQP properties exceed field limit')
                if kind==b'F':
                    name=self.name()
                    if name in names:raise ValueError('Duplicate nested AMQP property')
                    names.add(name)
                self.value(depth+1)
            if self.offset!=end:raise ValueError('Invalid AMQP nested field boundary')
            return None
        raise ValueError('Unsupported AMQP property type')


def _amqp(sock,reader,address,port):
    sock.sendall(b'AMQP\x00\x00\x09\x01')
    header=reader.exact(7)
    operation='AMQP protocol header / Connection.Start only; no authentication, channels, queues or messages'
    if header.startswith(b'AMQP'):
        value=header+reader.exact(1)
        if value not in (b'AMQP\x00\x01\x00\x00',b'AMQP\x03\x01\x00\x00'):
            raise ValueError('Unrecognized AMQP version advertisement')
        return {'operation':operation,'protocol_version':'.'.join(str(i) for i in value[5:]),
                'version_advertisement_only':True,'sasl_required':value[4]==3},'amqp'
    kind,channel,size=struct.unpack('!BHI',header)
    if kind!=1 or channel!=0 or not 18<=size<=16384:raise ValueError('Invalid AMQP initial method frame')
    body=reader.exact(size)
    if reader.exact(1)!=b'\xce' or body[:6]!=struct.pack('!HHBB',10,10,0,9):
        raise ValueError('AMQP response is not Connection.Start for version 0-9-1')
    fields=_Fields(body[6:]);table_size=fields.integer('!I');table_end=fields.offset+table_size
    if table_end>len(fields.data):raise ValueError('Invalid AMQP server property length')
    properties={};keys=set()
    while fields.offset<table_end:
        key=fields.name()
        if key in keys or len(keys)>=64:raise ValueError('Duplicate or excessive AMQP server properties')
        keys.add(key);value=fields.value()
        if key in {'product','version','platform','copyright','information'} and isinstance(value,str):properties[key]=value
    if fields.offset!=table_end:raise ValueError('Invalid AMQP server property boundary')
    mechanisms=fields.text(fields.longstr()).split();locales=fields.text(fields.longstr()).split()
    if fields.offset!=len(fields.data) or not mechanisms or len(mechanisms)>16 or not locales or len(locales)>16:
        raise ValueError('Invalid AMQP mechanism or locale fields')
    return {'operation':operation,'protocol_version':'0.9.1','server_properties':properties,
            'authentication_mechanisms':mechanisms,'locales':locales,'authentication_attempted':False},'amqp'


def _kafka(sock,reader,address,port):
    correlation=0x4c530001;client=b'leakscope'
    request=struct.pack('!hhih',18,0,correlation,len(client))+client
    sock.sendall(struct.pack('!I',len(request))+request)
    size=struct.unpack('!I',reader.exact(4))[0]
    if not 10<=size<=16384:raise ValueError('Kafka ApiVersions response exceeds frame limits')
    body=reader.exact(size);reply,error,count=struct.unpack('!ihi',body[:10])
    if reply!=correlation or not 0<=count<=256 or len(body)!=10+count*6:
        raise ValueError('Kafka ApiVersions correlation or array length mismatch')
    if error not in (0,35) or (error==0 and count==0):raise ValueError('Kafka ApiVersions negotiation did not return supported metadata')
    versions=[];keys=set()
    for offset in range(10,len(body),6):
        key,minimum,maximum=struct.unpack('!hhh',body[offset:offset+6])
        if key<0 or minimum<0 or maximum<minimum or key in keys:raise ValueError('Invalid Kafka API version range')
        keys.add(key);versions.append({'api_key':key,'min_version':minimum,'max_version':maximum})
    return {'operation':'Kafka ApiVersions v0 only; no authentication, topics, producers or consumers',
            'error_code':error,'negotiation_available':error==0,'api_versions':versions},'kafka'


def _zookeeper(sock,reader,address,port):
    sock.sendall(b'srvr');body=bytearray()
    while True:
        remaining=reader.deadline-time.monotonic()
        if remaining<=0:raise ValueError('ZooKeeper server-status deadline exceeded')
        sock.settimeout(remaining)
        chunk=sock.recv(min(4096,16385-len(body)))
        if not chunk:break
        body.extend(chunk)
        if len(body)>16384:raise ValueError('ZooKeeper server status exceeds the metadata limit')
    text=body.decode('utf-8',errors='replace')
    operation='ZooKeeper srvr status only; no session, znode, client or watch enumeration'
    if text.strip()=='srvr is not executed because it is not in the whitelist.':
        return {'operation':operation,'command_restricted':True,'status_available':False},'zookeeper'
    version=re.match(r'^Zookeeper version: ([0-9][A-Za-z0-9_.+\-]{0,199})(?:,|[ \r\n]|$)',text)
    if not version:raise ValueError('Response is not a ZooKeeper server status reply')
    mode=re.search(r'(?m)^Mode: (leader|follower|standalone|observer|read-only)\r?$',text)
    not_serving='This ZooKeeper instance is not currently serving requests' in text
    if not mode and not not_serving:raise ValueError('ZooKeeper status lacks a valid serving state')
    return {'operation':operation,'version':version[1],'mode':mode[1] if mode else 'not serving',
            'command_restricted':False,'status_available':True},'zookeeper'


TCP_ADAPTERS={'amqp':_amqp,'kafka':_kafka,'zookeeper':_zookeeper}
SUPPORTED_TYPES=frozenset(TCP_ADAPTERS)
CAPABILITIES={
    'amqp':{'operation':'Protocol header and Connection.Start server properties; stop before authentication',
            'protocol':'amqp','transports':['tcp'],'sources':['https://www.rabbitmq.com/resources/specs/amqp0-9-1.pdf']},
    'kafka':{'operation':'ApiVersions v0 only; no topics or authentication',
             'protocol':'kafka','transports':['tcp'],'sources':['https://github.com/apache/kafka/blob/trunk/clients/src/main/resources/common/message/ApiVersionsRequest.json']},
    'zookeeper':{'operation':'Four-letter srvr server status; no session or znode reads',
                 'protocol':'zookeeper','transports':['tcp'],'sources':['https://zookeeper.apache.org/doc/r3.9.3/zookeeperAdmin.html#sc_4lw']},
}
UNSUPPORTED_REASONS={
    'stomp':'STOMP requires a CONNECT/STOMP session handshake. Automatic capture stops at the user\'s no-login boundary; no login, passcode, subscription or message requests are sent.'
}
