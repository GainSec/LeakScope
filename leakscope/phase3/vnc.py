"""A bounded, display-only RFB screenshot client.

Wire formats follow RFC6143 sections7 and AppendixA:
https://www.rfc-editor.org/rfc/rfc6143
Only None security and RAW pixels are negotiated. No input/clipboard message is
sent. Pixel bytes are hashed separately; the retained protocol transcript makes
their omission explicit instead of presenting a complete packet capture.
"""
import hashlib
import json
import math
import os
import re
import struct
import time
import zlib
from pathlib import Path

from .state import normalize_address
from .wire import connect_tcp

MAX_DIMENSION=4096
MAX_PIXELS=4*1024*1024
MAX_FRAME_BYTES=16*1024*1024
MAX_CONTROL_BYTES=65536
MAX_RECTANGLES=256
MAX_MESSAGES=16
MAX_NAME_BYTES=4096
PIXEL_FORMAT=struct.pack('!BBBBHHHBBB3x',32,24,0,1,255,255,255,16,8,0)


class _Stream:
    def __init__(self,sock,deadline):
        self.sock=sock;self.deadline=deadline;self.started=time.monotonic()
        self.counts={'sent':0,'received':0};self.controls={'sent':bytearray(),'received':bytearray()}
        self.hashes={key:hashlib.sha256() for key in self.counts}
        self.events=[];self.rectangles=[];self.pixel_bytes=0

    def budget(self):
        remaining=self.deadline-time.monotonic()
        if remaining<=0:raise ValueError('VNC screenshot deadline exceeded')
        self.sock.settimeout(remaining)

    def record(self,direction,data,pixels=False):
        offset=self.counts[direction];self.counts[direction]+=len(data);self.hashes[direction].update(data)
        if self.counts['received']>MAX_FRAME_BYTES+2*MAX_CONTROL_BYTES:raise ValueError('VNC receive byte budget exceeded')
        if pixels:self.pixel_bytes+=len(data)
        else:
            retained=self.controls[direction]
            retained.extend(data[:max(0,MAX_CONTROL_BYTES-len(retained))])
        if len(self.events)<256:
            self.events.append({'direction':direction,'offset':offset,'bytes':len(data),
                'pixel_payload':pixels,'sha256':hashlib.sha256(data).hexdigest(),
                'elapsed_ms':round((time.monotonic()-self.started)*1000,3)})

    def send(self,data):
        if self.counts['sent']+len(data)>MAX_CONTROL_BYTES:raise ValueError('VNC request byte budget exceeded')
        remaining=memoryview(data)
        while remaining:
            self.budget();written=self.sock.send(remaining)
            if not written:raise ValueError('VNC connection closed while sending')
            self.record('sent',remaining[:written]);remaining=remaining[written:]

    def exact(self,size,pixels=False):
        if size<0 or size>MAX_FRAME_BYTES:raise ValueError('VNC response length exceeds limit')
        parts=[];received=0
        while received<size:
            self.budget();part=self.sock.recv(min(65536,size-received))
            if not part:raise ValueError('VNC response was truncated')
            self.record('received',part,pixels);parts.append(part);received+=len(part)
        return b''.join(parts)

    def document(self):
        return {'format':'leakscope-rfb-wire/1','evidence_layer':'application protocol bytes and pixel payload hashes',
            'complete_wire_retained':self.pixel_bytes==0 and all(self.counts[k]<=MAX_CONTROL_BYTES for k in self.counts),
            'pixel_bytes_omitted':self.pixel_bytes,'pixel_retention':'RAW pixel payload bytes are omitted; per-rectangle hashes and screenshot are retained',
            'sent':{'bytes':self.counts['sent'],'sha256':self.hashes['sent'].hexdigest(),'control_hex':bytes(self.controls['sent']).hex()},
            'received':{'bytes':self.counts['received'],'sha256':self.hashes['received'].hexdigest(),
                'control_hex':bytes(self.controls['received']).hex(),'control_limit':MAX_CONTROL_BYTES,
                'control_complete':self.counts['received']-self.pixel_bytes<=MAX_CONTROL_BYTES},
            'events':self.events,'rectangles':self.rectangles}


def _dimensions(width,height):
    if not 1<=width<=MAX_DIMENSION or not 1<=height<=MAX_DIMENSION or width*height>MAX_PIXELS:
        raise ValueError('VNC framebuffer dimensions exceed the snapshot limit')


def _reason(wire):
    length=struct.unpack('!I',wire.exact(4))[0]
    if length>MAX_NAME_BYTES:raise ValueError('VNC error reason exceeds limit')
    # Preserve bytes in the transcript, but remote text never becomes an error.
    wire.exact(length)


def _initialize(wire,metadata):
    banner=wire.exact(12)
    match=re.fullmatch(rb'RFB (\d{3})\.(\d{3})\n',banner)
    if not match or int(match.group(1))!=3:raise ValueError('Unsupported or invalid RFB protocol banner')
    offered_minor=int(match.group(2));minor=offered_minor if offered_minor in (7,8) else 3
    metadata.update(server_version=banner.decode('ascii').strip(),protocol_version='3.'+str(minor))
    wire.send(f'RFB 003.{minor:03d}\n'.encode())
    if minor==3:
        security=struct.unpack('!I',wire.exact(4))[0]
        metadata['offered_security_types']=[security]
        if security==0:
            _reason(wire);raise ValueError('VNC server rejected the connection')
        if security!=1:
            metadata['authentication_required']=True
            raise ValueError('VNC authentication is required; this display-only adapter uses no credentials')
    else:
        count=wire.exact(1)[0]
        if count==0:
            _reason(wire);raise ValueError('VNC server rejected the connection')
        security_types=list(wire.exact(count));metadata['offered_security_types']=security_types
        if 1 not in security_types:
            metadata['authentication_required']=True
            raise ValueError('VNC authentication or an unsupported security type is required; no credentials were sent')
        wire.send(b'\x01')
        # None security omits SecurityResult in3.3 and3.7 (RFC6143 AppendixA).
        if minor==8:
            status=struct.unpack('!I',wire.exact(4))[0]
            if status:
                _reason(wire);raise ValueError('VNC no-authentication negotiation failed')
    metadata['security']='None'
    wire.send(b'\x01')  # Shared ClientInit: do not disconnect existing clients.
    width,height=struct.unpack('!HH',wire.exact(4));_dimensions(width,height)
    server_format=wire.exact(16)
    bpp,depth,endian,true_color=server_format[:4]
    if bpp not in (8,16,32) or not 1<=depth<=bpp or endian not in (0,1) or true_color not in (0,1):
        raise ValueError('VNC server advertised an invalid pixel format')
    length=struct.unpack('!I',wire.exact(4))[0]
    if length>MAX_NAME_BYTES:raise ValueError('VNC desktop name exceeds limit')
    name=wire.exact(length).decode('utf-8',errors='replace')
    metadata.update(width=width,height=height,desktop_name=name,title=name[:500],
                    pixel_format={'bits_per_pixel':32,'depth':24,'big_endian':False,'true_color':True,
                                  'red_shift':16,'green_shift':8,'blue_shift':0})
    wire.send(b'\0'*4+PIXEL_FORMAT)
    wire.send(struct.pack('!BBHi',2,0,1,0))  # Request only RAW encoding.
    wire.send(struct.pack('!BBHHHH',3,0,0,0,width,height))
    return width,height


def _frame(wire,width,height):
    canvas=bytearray(width*height*3);coverage=bytearray(width*height);covered=0;pixel_bytes=0
    rectangle_count=0
    for _ in range(MAX_MESSAGES):
        message=wire.exact(1)[0]
        if message==2:continue  # Bell has no body.
        if message!=0:raise ValueError('Unsupported VNC server message during snapshot')
        header=wire.exact(3);count=struct.unpack('!H',header[1:])[0]
        rectangle_count+=count
        if rectangle_count>MAX_RECTANGLES:raise ValueError('VNC rectangle count exceeds limit')
        for _ in range(count):
            x,y,w,h,encoding=struct.unpack('!HHHHi',wire.exact(12))
            if encoding!=0:raise ValueError('Unsupported VNC rectangle encoding; only RAW was requested')
            if not w or not h or x+w>width or y+h>height:raise ValueError('VNC rectangle exceeds framebuffer bounds')
            amount=w*h*4;pixel_bytes+=amount
            if pixel_bytes>MAX_FRAME_BYTES:raise ValueError('VNC frame byte budget exceeded')
            pixels=wire.exact(amount,pixels=True)
            wire.rectangles.append({'x':x,'y':y,'width':w,'height':h,'encoding':'RAW',
                                    'bytes':amount,'sha256':hashlib.sha256(pixels).hexdigest()})
            for row in range(h):
                start=row*w*4;line=pixels[start:start+w*4]
                # Requested little-endian 0x00RRGGBB arrives as B,G,R,padding.
                rgb=bytearray(w*3);rgb[0::3]=line[2::4];rgb[1::3]=line[1::4];rgb[2::3]=line[0::4]
                destination=((y+row)*width+x)*3;canvas[destination:destination+w*3]=rgb
                mark=(y+row)*width+x;covered+=coverage[mark:mark+w].count(0);coverage[mark:mark+w]=b'\x01'*w
        if covered:return bytes(canvas),covered
    raise ValueError('VNC server returned no usable framebuffer pixels within the message budget')


def _png(width,height,rgb):
    def chunk(kind,data):
        return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data)&0xffffffff)
    stride=width*3
    rows=b''.join(b'\0'+rgb[start:start+stride] for start in range(0,len(rgb),stride))
    return (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!IIBBBBB',width,height,8,2,0,0,0))+
            chunk(b'IDAT',zlib.compress(rows))+chunk(b'IEND',b''))


def _artifacts(root,wire,metadata,png=None):
    from .capture import _persist
    files=[]
    try:
        files.append(_persist(root,json.dumps(wire.document(),ensure_ascii=False).encode(),'application/json',
                              'wire-transcript','native',{'protocol':'rfb','pixel_bytes_omitted':wire.pixel_bytes},'.json'))
        if png is not None:files.append(_persist(root,png,'image/png','screenshot','native',metadata,'.png'))
        return files
    except Exception:
        for item in files:Path(item['path']).unlink(missing_ok=True)
        raise


def capture_vnc(endpoint,config):
    """Capture one bounded no-auth RFB frame, without keyboard/mouse/clipboard.

    A partial failure raises the shared CaptureEvidenceError with its honest
    protocol transcript, so normal capture jobs can retain negotiation evidence.
    Unsupported authentication is never retried or guessed.
    """
    from .capture import CaptureEvidenceError, _enforce_blacklist
    if not isinstance(endpoint,dict) or not isinstance(config,dict):raise ValueError('Invalid VNC capture inputs')
    address=normalize_address(endpoint.get('address'));port=endpoint.get('port')
    if isinstance(port,bool) or not isinstance(port,(int,str)):raise ValueError('Invalid VNC port')
    try:port=int(port)
    except (ValueError,TypeError):raise ValueError('Invalid VNC port') from None
    if not 1<=port<=65535:raise ValueError('Invalid VNC port')
    if endpoint.get('transport','tcp')!='tcp':raise ValueError('VNC screenshot requires TCP')
    timeout=config.get('VNC_TIMEOUT',config.get('CAPTURE_TIMEOUT',10))
    if isinstance(timeout,bool) or not isinstance(timeout,(int,float)) or not math.isfinite(timeout) or not .05<=timeout<=15:
        raise ValueError('VNC timeout must be between0.05 and15 seconds')
    _enforce_blacklist(address,port,'tcp',config)
    root=Path(config.get('CAPTURE_DIR') or os.environ.get('LEAKSCOPE_CAPTURE_DIR') or '/data/artifacts/captures').expanduser().resolve()
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    deadline=time.monotonic()+timeout;wire=None
    metadata={'protocol':'rfb','operation':'Shared no-auth RAW framebuffer snapshot','read_only':True,
              'input_events_sent':0,'authentication_required':False,'adapter':'leakscope-rfb/1',
              'limitations':'No authentication, TLS or compressed encodings; raw pixels are omitted from the protocol transcript.'}
    try:
        with connect_tcp(address,port,deadline) as sock:
            wire=_Stream(sock,deadline)
            width,height=_initialize(wire,metadata)
            rgb,covered=_frame(wire,width,height);wire.budget()
        partial=covered<width*height
        metadata.update(pixels_received=covered,pixels_total=width*height,partial=partial,
                        screenshot_available=True,missing_pixels_rendered_black=partial,
                        wire_summary={'transport':'tcp','connection_established':True,'bytes_sent':wire.counts['sent'],
                                      'bytes_received':wire.counts['received'],'pixel_bytes_omitted':wire.pixel_bytes})
        return {'metadata':metadata,'artifacts':_artifacts(root,wire,metadata,_png(width,height,rgb)),
                'verified_type':'vnc','partial':partial}
    except (OSError,ValueError) as exc:
        error=str(exc) if isinstance(exc,ValueError) else 'VNC screenshot request failed or timed out'
        if wire is None:raise ValueError(error) from None
        metadata.update(partial=True,screenshot_available=False,error=error,
            wire_summary={'transport':'tcp','connection_established':True,'bytes_sent':wire.counts['sent'],
                          'bytes_received':wire.counts['received'],'pixel_bytes_omitted':wire.pixel_bytes})
        result={'metadata':metadata,'artifacts':_artifacts(root,wire,metadata),'partial':True}
        raise CaptureEvidenceError(error,result) from None
