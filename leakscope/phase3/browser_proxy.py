"""Ephemeral same-origin proxy for bounded screenshot browser navigation."""
import select
import socket
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


@contextmanager
def same_origin_proxy(host,port,timeout=30,byte_limit=16*1024*1024):
    lock=threading.Lock();state={'bytes':0,'blocked':0,'expired':False};deadline=time.monotonic()+timeout
    def permitted(authority,scheme='http'):
        try:
            parsed=urlsplit(authority if '://' in authority else '//'+authority)
            requested_port=parsed.port or (443 if scheme=='https' else 80)
            okay=parsed.hostname and parsed.hostname.rstrip('.').lower()==host.rstrip('.').lower() and requested_port==port and not parsed.username and not parsed.password
        except ValueError:okay=False
        if not okay:
            with lock:state['blocked']+=1
        return okay
    def relay(client,upstream):
        sockets=[client,upstream]
        while sockets and time.monotonic()<deadline:
            ready,_,_=select.select(sockets,[],[],min(1,max(0,deadline-time.monotonic())))
            for sender in ready:
                data=sender.recv(8192)
                if not data:return
                with lock:
                    state['bytes']+=len(data)
                    over=state['bytes']>byte_limit
                if over:return
                (upstream if sender is client else client).sendall(data)
        state['expired']=True
    class Handler(BaseHTTPRequestHandler):
        timeout=3
        def log_message(self,*args):pass
        def do_CONNECT(self):
            if not permitted(self.path,'https'):
                self.send_error(403,'Capture scope blocked');return
            try:
                with socket.create_connection((host,port),timeout=3) as upstream:
                    self.send_response(200,'Connection established');self.end_headers()
                    relay(self.connection,upstream)
            except OSError:pass
        def do_GET(self):self.forward()
        def do_HEAD(self):self.forward()
        def forward(self):
            if not permitted(self.path):
                self.send_error(403,'Capture scope blocked');return
            try:
                parsed=urlsplit(self.path)
                if parsed.scheme!='http':self.send_error(400);return
                target=(parsed.path or '/')+('?' + parsed.query if parsed.query else '')
                authority='['+host+']' if ':' in host else host
                headers=[f'{self.command} {target} HTTP/1.1',f'Host: {authority}:{port}','Connection: close']
                for name,value in self.headers.items():
                    if name.lower() not in ('host','connection','proxy-connection','proxy-authorization','transfer-encoding','content-length'):
                        headers.append(name+': '+value)
                with socket.create_connection((host,port),timeout=3) as upstream:
                    upstream.sendall(('\r\n'.join(headers)+'\r\n\r\n').encode('iso-8859-1'))
                    relay(self.connection,upstream)
            except (OSError,UnicodeError):pass
    with ThreadingHTTPServer(('127.0.0.1',0),Handler) as server:
        server.daemon_threads=True
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:yield f'http://127.0.0.1:{server.server_address[1]}',state
        finally:server.shutdown();thread.join(timeout=2)
