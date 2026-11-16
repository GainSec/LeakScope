"""Bounded application-byte transcripts; these are exchanges, not packet captures."""
import ipaddress
import queue
import socket
import ssl
import threading
import time

MAX_SENT=65536
MAX_RECEIVED=65536
_RESOLVERS=threading.BoundedSemaphore(4)


def _resolve(address,port,kind,deadline):
    # System DNS does not honor socket timeouts. Wait only inside the exchange
    # budget and bound outstanding resolver threads if a system resolver hangs.
    result=queue.Queue(maxsize=1)
    if not _RESOLVERS.acquire(blocking=False):raise ValueError('Capture resolver is busy')
    def resolve():
        try:
            try:result.put((socket.getaddrinfo(address,port,type=kind),None))
            except Exception as exc:result.put((None,exc))
        finally:_RESOLVERS.release()
    threading.Thread(target=resolve,daemon=True).start()
    try:answers,error=result.get(timeout=max(0,deadline-time.monotonic()))
    except queue.Empty:raise TimeoutError('Capture DNS deadline exceeded') from None
    if error is not None:raise ValueError('Capture destination could not be resolved') from None
    if not answers:raise ValueError('Capture destination has no resolved addresses')
    return answers


def connect_tcp(address,port,deadline):
    # One selected address, one socket, one connection attempt; never sweep DNS answers.
    family,kind,protocol,_,destination=_resolve(address,port,socket.SOCK_STREAM,deadline)[0]
    remaining=deadline-time.monotonic()
    if remaining<=0:raise TimeoutError('Capture connection deadline exceeded')
    sock=socket.socket(family,kind,protocol)
    try:sock.settimeout(remaining);sock.connect(destination)
    except BaseException:sock.close();raise
    return sock


class WireRecorder:
    def __init__(self,transport='tcp',tls=False):
        self.transport=transport;self.tls=tls;self.started=time.monotonic()
        self.sent=bytearray();self.received=bytearray();self.events=[]
        self.counts={'sent':0,'received':0};self.connected=False
        self.termination='';self.receive_limit_reached=False

    def record(self,direction,data):
        if not data:return
        stream=self.sent if direction=='sent' else self.received
        cap=MAX_SENT if direction=='sent' else MAX_RECEIVED
        if len(stream)+len(data)>cap:raise ValueError('Wire exchange exceeded its 64 KiB direction limit')
        offset=len(stream);stream.extend(data);self.counts[direction]+=1
        if len(self.events)<256:
            self.events.append({'direction':direction,'offset':offset,'bytes':len(data),'elapsed_ms':round((time.monotonic()-self.started)*1000,3)})

    def summary(self):
        return {'events_sent':self.counts['sent'],'events_received':self.counts['received'],
                'bytes_sent':len(self.sent),'bytes_received':len(self.received),'transport':self.transport,
                'tls':self.tls,'connection_established':self.connected,'termination':self.termination,
                'receive_limit_reached':self.receive_limit_reached,
                'evidence_layer':'decrypted TLS application bytes' if self.tls else 'application bytes'}

    def document(self):
        def value(data):return {'hex':data.hex(),'text':bytes(data).decode('utf-8',errors='replace'),'text_encoding':'UTF-8 with replacement; hex and raw artifacts are lossless'}
        return {'format':'leakscope-wire/1','summary':self.summary(),'events':self.events,
                'events_omitted':max(0,sum(self.counts.values())-len(self.events)),
                'sent':value(self.sent),'received':value(self.received)}


class RecordedSocket:
    def __init__(self,sock,recorder,deadline=None):self.sock=sock;self.recorder=recorder;self.deadline=deadline
    def __getattr__(self,name):return getattr(self.sock,name)
    def _budget(self):
        if self.deadline is not None:
            remaining=self.deadline-time.monotonic()
            if remaining<=0:raise TimeoutError('Wire exchange deadline exceeded')
            self.sock.settimeout(remaining)
    def send(self,data,*args):
        if len(self.recorder.sent)+len(data)>MAX_SENT:raise ValueError('Wire request exceeded the 64 KiB limit')
        self._budget()
        count=self.sock.send(data,*args);self.recorder.record('sent',data[:count]);return count
    def sendall(self,data):
        # Record only bytes accepted by the socket, including partial sends.
        view=memoryview(data)
        while view:
            count=self.send(view)
            if not count:raise OSError('Socket closed while sending')
            view=view[count:]
    def recv(self,size,*args):
        remaining=MAX_RECEIVED-len(self.recorder.received)
        if remaining<=0:
            self.recorder.receive_limit_reached=True
            raise ValueError('Wire response exceeded the 64 KiB limit')
        self._budget()
        data=self.sock.recv(min(size,remaining),*args);self.recorder.record('received',data);return data


def _validate_options(options,transport):
    if options is None:options={}
    allowed={'payload','encoding','timeout_seconds'}|({'tls'} if transport=='tcp' else set())
    if not isinstance(options,dict) or set(options)-allowed:raise ValueError('Invalid '+transport.upper()+' capture options')
    payload=options.get('payload','');encoding=options.get('encoding','text');timeout=options.get('timeout_seconds',3);tls=options.get('tls',False)
    if not isinstance(payload,str) or len(payload)>12288:raise ValueError('Capture payload must be at most 4096 decoded bytes')
    if encoding not in ('text','hex'):raise ValueError('Capture payload encoding must be text or hex')
    if not isinstance(timeout,(int,float)) or isinstance(timeout,bool) or not 1<=timeout<=10:raise ValueError('Capture timeout must be between 1 and 10 seconds')
    if not isinstance(tls,bool):raise ValueError('TCP TLS option must be a boolean')
    try:decoded=bytes.fromhex(payload) if encoding=='hex' else payload.encode('utf-8')
    except (ValueError,UnicodeError):raise ValueError('Invalid capture payload encoding') from None
    if len(decoded)>4096:raise ValueError('Capture payload must be at most 4096 decoded bytes')
    if transport=='udp' and not decoded:raise ValueError('UDP capture requires an explicit nonempty datagram payload')
    normalized={'payload':payload,'encoding':encoding,'timeout_seconds':timeout}
    if transport=='tcp':normalized['tls']=tls
    return normalized,decoded


def validate_tcp_options(options):return _validate_options(options,'tcp')
def validate_udp_options(options):return _validate_options(options,'udp')


def unicast_destination(address,port,deadline=None):
    def validate(ip):
        if ip.is_multicast or ip.is_unspecified or str(ip)=='255.255.255.255':
            raise ValueError('UDP capture requires a unicast destination')
        if isinstance(ip,ipaddress.IPv6Address) and ip.ipv4_mapped:validate(ip.ipv4_mapped)
    try:literal=ipaddress.ip_address(address)
    except ValueError:literal=None
    if literal is not None:validate(literal)
    answers=_resolve(address,port,socket.SOCK_DGRAM,deadline) if deadline is not None else socket.getaddrinfo(address,port,type=socket.SOCK_DGRAM)
    if not answers:raise ValueError('UDP destination has no resolved addresses')
    for _,_,_,_,destination in answers:
        validate(ipaddress.ip_address(destination[0]))
    return answers[0]


def exchange(address,port,transport,options,recorder):
    options,payload=_validate_options(options,transport)
    timeout=options['timeout_seconds'];deadline=time.monotonic()+min(timeout,15)
    def remaining():
        value=deadline-time.monotonic()
        if value<=0:raise TimeoutError('Exchange deadline exceeded')
        return value
    try:
        if transport=='udp':
            family,kind,protocol,_,destination=unicast_destination(address,port,deadline)
            sock=socket.socket(family,kind,protocol)
            try:
                sock.settimeout(remaining());sock.connect(destination)
                # A connected UDP socket selects a peer; it does not establish reachability.
                wire=RecordedSocket(sock,recorder,deadline);wire.send(payload)
                sock.settimeout(remaining());wire.recv(MAX_RECEIVED)
                recorder.termination='datagram_received'
            finally:sock.close()
        else:
            with connect_tcp(address,port,deadline) as raw:
                recorder.connected=True;raw.settimeout(remaining());sock=raw
                if options['tls']:
                    # TLS authenticates the selected hostname using the worker's trust store.
                    context=ssl.create_default_context();raw.settimeout(remaining())
                    sock=context.wrap_socket(raw,server_hostname=address)
                try:
                    wire=RecordedSocket(sock,recorder,deadline)
                    if payload:
                        sock.settimeout(remaining());wire.sendall(payload)
                    while len(recorder.received)<MAX_RECEIVED:
                        sock.settimeout(remaining());data=wire.recv(min(4096,MAX_RECEIVED-len(recorder.received)))
                        if not data:recorder.termination='eof';break
                    else:
                        recorder.receive_limit_reached=True;recorder.termination='receive_limit'
                finally:
                    if sock is not raw:sock.close()
    except (OSError,TimeoutError) as exc:
        from .capture_diagnostics import CaptureDiagnosticError
        timed_out=isinstance(exc,TimeoutError) or time.monotonic()>=deadline
        recorder.termination='timeout' if timed_out else 'network_error'
        # Name the failure so a status can say "timed out" only when it did.
        raise CaptureDiagnosticError('timeout' if timed_out else 'connection_refused' if isinstance(exc,ConnectionRefusedError)
            else 'connection_reset' if isinstance(exc,ConnectionResetError) else 'connection_failed') from None
    return recorder
