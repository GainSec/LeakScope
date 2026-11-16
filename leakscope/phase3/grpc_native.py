"""One bounded gRPC Health/Check call, retaining HTTP/2 application bytes.

Framing follows gRPC's HTTP/2 protocol and grpc.health.v1 health.proto. A
nonzero RPC status can identify gRPC without implying Health is implemented or
that the application is healthy. No service enumeration or application RPCs.
"""
import math
import re
import time
from urllib.parse import unquote

from h2.config import H2Configuration
from h2.connection import H2Connection
from h2.events import (ConnectionTerminated, DataReceived, ResponseReceived,
                       StreamEnded, StreamReset, TrailersReceived)
from h2.exceptions import H2Error
from h2.settings import SettingCodes, Settings

MAX_EXCHANGE = 65536
MAX_HEADERS = 8192
MAX_MESSAGE = 4096
HEALTH_PATH = '/grpc.health.v1.Health/Check'


def _varint(data, position):
    value=0
    for shift in range(0,70,7):
        if position>=len(data):raise ValueError('Truncated gRPC health protobuf')
        byte=data[position];position+=1
        if shift==63 and byte>1:raise ValueError('Oversized gRPC health protobuf integer')
        value|=(byte&127)<<shift
        if not byte&128:return value,position
    raise ValueError('Invalid gRPC health protobuf integer')


def _health_status(body):
    if len(body)<5 or body[0]!=0:raise ValueError('Invalid or compressed gRPC health response')
    length=int.from_bytes(body[1:5],'big')
    if length>MAX_MESSAGE or len(body)!=length+5:
        raise ValueError('gRPC Health/Check requires one bounded response message')
    data=body[5:];position=0;status=0
    while position<len(data):
        key,position=_varint(data,position);number,wire_type=key>>3,key&7
        if not 1<=number<(1<<29):raise ValueError('Invalid gRPC health protobuf field')
        if wire_type==0:
            value,position=_varint(data,position)
            if number==1:
                if value>(1<<31)-1:raise ValueError('Invalid gRPC health status value')
                status=value
        elif wire_type in (1,5):position+=8 if wire_type==1 else 4
        elif wire_type==2:
            length,position=_varint(data,position);position+=length
        else:raise ValueError('Unsupported gRPC health protobuf field encoding')
        if number==1 and wire_type!=0:raise ValueError('Invalid gRPC health status encoding')
        if position>len(data):raise ValueError('Truncated gRPC health protobuf field')
    return {0:'UNKNOWN',1:'SERVING',2:'NOT_SERVING',3:'SERVICE_UNKNOWN'}.get(status,'UNKNOWN_ENUM_'+str(status))


def _header_values(headers):
    values={}
    for key,value in headers:
        if key in (':status','content-type','grpc-status','grpc-message'):
            if key in values:raise ValueError('Duplicate gRPC response status or content type')
            values[key]=value
    return values


def identify_grpc(sock, deadline, address, port, tls=False):
    """Identify gRPC on an already connected, optionally TLS-wrapped socket.

    The caller owns socket lifetime and records sent/received bytes. ``deadline``
    is one monotonic deadline shared with DNS, connection and TLS negotiation.
    """
    transferred=0
    def budget():
        remaining=deadline-time.monotonic()
        if remaining<=0:raise ValueError('gRPC identity deadline exceeded')
        sock.settimeout(remaining)
        return remaining
    def send(data):
        nonlocal transferred
        if not data:return
        if transferred+len(data)>MAX_EXCHANGE:raise ValueError('gRPC exchange exceeds 64 KiB')
        budget();transferred+=len(data);sock.sendall(data)

    remaining=budget()
    connection=H2Connection(config=H2Configuration(client_side=True,header_encoding='utf-8'))
    connection.local_settings=Settings(client=True,initial_values={
        SettingCodes.ENABLE_PUSH:0,SettingCodes.MAX_CONCURRENT_STREAMS:1,
        SettingCodes.MAX_HEADER_LIST_SIZE:MAX_HEADERS,
    })
    # Enforce decompressed HPACK size immediately, before the peer ACKs SETTINGS.
    connection.decoder.max_header_list_size=MAX_HEADERS
    connection.initiate_connection()
    host='['+address+']' if ':' in address else address
    connection.send_headers(1,[(':method','POST'),(':scheme','https' if tls else 'http'),
        (':authority',host+':'+str(port)),(':path',HEALTH_PATH),
        ('grpc-timeout',str(max(1,min(10000,math.ceil(remaining*1000))))+'m'),
        ('content-type','application/grpc+proto'),('te','trailers'),
        ('user-agent','LeakScope-gRPC/1')])
    connection.send_data(1,b'\x00\x00\x00\x00\x00',end_stream=True)
    response=None;trailers=None;body=bytearray();ended=False;can_write=True
    try:
        send(connection.data_to_send())
        while not ended:
            budget()
            if transferred>=MAX_EXCHANGE:raise ValueError('gRPC exchange exceeds 64 KiB')
            chunk=sock.recv(min(4096,MAX_EXCHANGE-transferred))
            if not chunk:raise ValueError('Service closed the gRPC identity handshake')
            transferred+=len(chunk)
            for event in connection.receive_data(chunk):
                if isinstance(event,ResponseReceived) and event.stream_id==1:
                    if response is not None:raise ValueError('Repeated gRPC response headers')
                    response=_header_values(event.headers)
                    if 'grpc-status' in response and event.stream_ended is None:
                        raise ValueError('gRPC status must terminate the response stream')
                elif isinstance(event,TrailersReceived) and event.stream_id==1:
                    if trailers is not None:raise ValueError('Repeated gRPC response trailers')
                    trailers=_header_values(event.headers)
                elif isinstance(event,DataReceived) and event.stream_id==1:
                    if len(body)+len(event.data)>MAX_MESSAGE+5:
                        raise ValueError('gRPC health response exceeds its metadata limit')
                    body.extend(event.data)
                elif isinstance(event,StreamEnded) and event.stream_id==1:ended=True
                elif isinstance(event,StreamReset) and event.stream_id==1:
                    raise ValueError('Service reset the gRPC identity stream')
                elif isinstance(event,ConnectionTerminated):
                    if not ended:raise ValueError('Service terminated the gRPC identity connection')
            if not ended and can_write:
                try:send(connection.data_to_send())
                except OSError:
                    # A peer may finish its response and close before reading
                    # our SETTINGS ACK. Validate any buffered response first.
                    can_write=False
    except (H2Error,UnicodeError,OSError) as exc:
        raise ValueError('Invalid or incomplete gRPC HTTP/2 identity response') from exc
    if response is None or response.get(':status')!='200':
        raise ValueError('gRPC identity requires an HTTP/2 status 200 response')
    content_type=response.get('content-type','').split(';',1)[0].strip().lower()
    if not re.fullmatch(r'application/grpc(?:\+[a-z0-9._-]+)?',content_type):
        raise ValueError('HTTP/2 response is not native gRPC')
    final=trailers if trailers is not None else response
    status=final.get('grpc-status','')
    if not re.fullmatch(r'(?:[0-9]|1[0-6])',status):
        raise ValueError('Missing or invalid terminating gRPC status')
    status=int(status)
    metadata={'operation':'grpc.health.v1.Health/Check; empty service name',
        'protocol':'grpc','http_version':'2','grpc_status':status,
        'health_check_succeeded':status==0,'authentication_required':status==16,
        'permission_denied':status==7,'health_implemented':True if status==0 else False if status==12 else None,
        'service_identity_verified':False}
    if final.get('grpc-message'):
        metadata['grpc_message']=unquote(final['grpc-message'])[:1000]
    if status==0:
        if content_type not in ('application/grpc','application/grpc+proto'):
            raise ValueError('Unsupported gRPC health response serialization')
        metadata['health_status']=_health_status(bytes(body))
    return metadata,'grpc'
