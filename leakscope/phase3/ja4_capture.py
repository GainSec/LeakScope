"""Offline classic-PCAP TCP/TLS fingerprints, bounded to 4 MiB / 4096 packets.

Formats follow FoxIO JA4 commit c6469a7b0dbf8fe963810162429fbfd9d1acc4d5.
No live interception, packet injection, TLS decryption or guessed fingerprints.
JA4/JA4T describe the initiator, never the observed listening endpoint.
"""
import hashlib,ipaddress,struct

MAX_CAPTURE=4*1024*1024
def _hash(value):return hashlib.sha256(value.encode()).hexdigest()[:12] if value else '000000000000'
def _grease(v):return v&0x0f0f==0x0a0a and v>>8==v&255

def tcp_fingerprint(window,options):
    kinds=[];mss=scale=0;i=0
    while i<len(options):
        k=options[i]
        if k==0:break
        kinds.append(k)
        if k==1:i+=1;continue
        if i+1>=len(options):raise ValueError('Truncated TCP option')
        n=options[i+1]
        if n<2 or i+n>len(options):raise ValueError('Invalid TCP option')
        if k==2 and n==4:mss=int.from_bytes(options[i+2:i+4],'big')
        if k==3 and n==3:scale=options[i+2]
        i+=n
    return '{}_{}_{:02d}_{}'.format(window,'-'.join(map(str,kinds)) or '00',mss,str(scale) if scale else '00')

class _Reader:
    def __init__(self,data):self.data=data;self.pos=0
    def take(self,n):
        if n<0 or self.pos+n>len(self.data):raise ValueError('Truncated TLS hello')
        out=self.data[self.pos:self.pos+n];self.pos+=n;return out
    def integer(self,n):return int.from_bytes(self.take(n),'big')
    def vector(self,n):return self.take(self.integer(n))
    def rest(self):return len(self.data)-self.pos

def _words(data):
    if len(data)%2:raise ValueError('Invalid TLS vector')
    return [int.from_bytes(data[i:i+2],'big') for i in range(0,len(data),2)]

def _alpn(value):
    if not value:return '00'
    first,last=value[0],value[-1]
    if chr(first).isascii() and chr(last).isascii() and chr(first).isalnum() and chr(last).isalnum():return chr(first)+chr(last)
    return format(first,'02x')[0]+format(last,'02x')[-1]

def _hello(kind,body):
    r=_Reader(body);version=r.integer(2);r.take(32);r.vector(1)
    if kind==1:ciphers=_words(r.vector(2));r.vector(1)
    else:ciphers=[r.integer(2)];r.take(1)
    extensions=[];values={}
    if r.rest():
        e=_Reader(r.vector(2))
        if r.rest():raise ValueError('Trailing TLS hello bytes')
        while e.rest():
            k=e.integer(2);v=e.vector(2)
            if k in values:raise ValueError('Duplicate TLS extension')
            extensions.append(k);values[k]=v
    if 43 in values:
        v=_Reader(values[43]);vs=_words(v.vector(1)) if kind==1 else [v.integer(2)];vs=[n for n in vs if not _grease(n)]
        if vs:version=max(vs)
    ver={0x0301:'10',0x0302:'11',0x0303:'12',0x0304:'13'}.get(version,'00')
    alpn=b''
    if 16 in values:
        v=_Reader(values[16]);a=_Reader(v.vector(2));alpn=a.vector(1) if a.rest() else b''
    if kind==2:
        ex=','.join(format(n,'04x') for n in extensions)
        return 'ja4s','t{}{:02d}{}_{}_{!s}'.format(ver,min(len(extensions),99),_alpn(alpn),format(ciphers[0],'04x'),_hash(ex))
    ciphers=[n for n in ciphers if not _grease(n)];extensions=[n for n in extensions if not _grease(n)]
    cipher_hash=_hash(','.join(format(n,'04x') for n in sorted(ciphers)))
    ex=','.join(format(n,'04x') for n in sorted(extensions) if n not in (0,16))
    if 13 in values:
        algorithms=_words(_Reader(values[13]).vector(2));ex+='_'+','.join(format(n,'04x') for n in algorithms)
    return 'ja4','t{}{}{:02d}{:02d}{}_{}_{}'.format(ver,'d' if 0 in values else 'i',min(len(ciphers),99),min(len(extensions),99),_alpn(alpn),cipher_hash,_hash(ex))

def _tls(data):
    pos=0;handshake=b'';out=[]
    while pos+5<=len(data):
        typ=data[pos];length=int.from_bytes(data[pos+3:pos+5],'big')
        if data[pos+1]!=3 or length>18432 or pos+5+length>len(data):break
        body=data[pos+5:pos+5+length];pos+=5+length
        if typ==22:handshake+=body
        elif typ in (20,21,23):continue
        else:break
        while len(handshake)>=4:
            kind=handshake[0];n=int.from_bytes(handshake[1:4],'big')
            if n>65536:return out
            if len(handshake)<n+4:break
            body=handshake[4:n+4];handshake=handshake[n+4:]
            if kind in (1,2):
                try:out.append(_hello(kind,body))
                except (ValueError,IndexError):pass
    return out

def _packet(packet,link):
    if link==1:
        if len(packet)<14:return
        proto=int.from_bytes(packet[12:14],'big');packet=packet[14:]
        for _ in range(2):
            if proto not in (0x8100,0x88a8):break
            if len(packet)<4:return
            proto=int.from_bytes(packet[2:4],'big');packet=packet[4:]
    elif link==113:
        if len(packet)<16:return
        proto=int.from_bytes(packet[14:16],'big');packet=packet[16:]
    elif link==276:
        if len(packet)<20:return
        proto=int.from_bytes(packet[:2],'big');packet=packet[20:]
    elif link==101:proto=0x0800 if packet and packet[0]>>4==4 else 0x86dd
    else:raise ValueError('Unsupported PCAP link type; use Ethernet, Linux cooked or raw IP')
    if proto==0x0800:
        if len(packet)<20 or packet[0]>>4!=4 or packet[9]!=6:return
        ihl=(packet[0]&15)*4;total=int.from_bytes(packet[2:4],'big')
        if ihl<20 or total<ihl or total>len(packet) or int.from_bytes(packet[6:8],'big')&0x3fff:return
        src=str(ipaddress.ip_address(packet[12:16]));dst=str(ipaddress.ip_address(packet[16:20]));tcp=packet[ihl:total]
    elif proto==0x86dd:
        if len(packet)<40 or packet[0]>>4!=6 or packet[6]!=6:return
        total=40+int.from_bytes(packet[4:6],'big')
        if total>len(packet):return
        src=str(ipaddress.ip_address(packet[8:24]));dst=str(ipaddress.ip_address(packet[24:40]));tcp=packet[40:total]
    else:return
    if len(tcp)<20:return
    size=(tcp[12]>>4)*4
    if size<20 or size>len(tcp):return
    return (src,int.from_bytes(tcp[:2],'big')),(dst,int.from_bytes(tcp[2:4],'big')),int.from_bytes(tcp[4:8],'big'),int.from_bytes(tcp[8:12],'big'),tcp[13],int.from_bytes(tcp[14:16],'big'),tcp[20:size],tcp[size:]

def _assemble(parts):
    if not parts:return b''
    base=parts[0][0];chunks={};out=bytearray()
    for seq,data in parts:
        offset=(seq-base)&0xffffffff
        if offset>262144:return b''
        if offset in chunks and chunks[offset]!=data:return b''
        chunks[offset]=data
    for offset,data in sorted(chunks.items()):
        if offset>len(out):break
        overlap=len(out)-offset
        if overlap and bytes(out[offset:offset+min(overlap,len(data))])!=data[:overlap]:return b''
        if overlap<len(data):out.extend(data[overlap:])
        if len(out)>262144:return b''
    return bytes(out)

def analyze_pcap(data):
    if not isinstance(data,bytes) or not 24<=len(data)<=MAX_CAPTURE:raise ValueError('PCAP must be between 24 bytes and 4 MiB')
    formats={b'\xd4\xc3\xb2\xa1':'<',b'\xa1\xb2\xc3\xd4':'>',b'\x4d\x3c\xb2\xa1':'<',b'\xa1\xb2\x3c\x4d':'>'}
    endian=formats.get(data[:4])
    if not endian:raise ValueError('Upload a classic PCAP; convert PCAPNG before import')
    major,minor,_,_,snap,link=struct.unpack(endian+'HHIIII',data[4:24])
    if (major,minor)!=(2,4) or not 1<=snap<=262144:raise ValueError('Invalid PCAP header')
    flows={};active={};starts={};synacks={};pos=24;count=0
    def new_flow(pair,client=None,server=None,isn=None):
        if len(flows)>=256:raise ValueError('PCAP exceeds 256 TCP-session limit')
        index=len(flows);flows[index]={'pair':pair,'parts':{},'fingerprints':[],'client':client,'server':server,'client_isn':isn,'server_isn':None,'ended':False,'ambiguous':False};return index
    while pos<len(data):
        if pos+16>len(data):raise ValueError('Truncated PCAP packet header')
        _,_,length,original=struct.unpack(endian+'IIII',data[pos:pos+16]);pos+=16;count+=1
        if count>4096:raise ValueError('PCAP exceeds 4096-packet limit')
        if length>snap or length>original or pos+length>len(data):raise ValueError('Truncated or invalid PCAP packet')
        packet=_packet(data[pos:pos+length],link);pos+=length
        if not packet:continue
        src,dst,seq,ack,flags,window,opts,payload=packet;pair=tuple(sorted((src,dst)))
        if flags&2 and not flags&16:
            start=(src,dst,seq);index=starts.get(start)
            if index is None or flows[index]['ended']:index=new_flow(pair,src,dst,seq);starts[start]=index
            active[pair]=index
        elif flags&18==18:
            index=starts.get((dst,src,(ack-1)&0xffffffff))
            synack=(src,dst,seq,ack)
            if index is None:index=synacks.get(synack)
            if index is None:index=new_flow(pair,dst,src);active[pair]=index
            synacks[synack]=index
            flow=flows[index]
            if flow['server']!=src or flow['server_isn'] not in (None,seq):flow['ambiguous']=True
            flow['server_isn']=seq
        else:
            index=active.get(pair)
            if index is None:index=new_flow(pair);active[pair]=index
            if payload:
                possible=[]
                for i,f in flows.items():
                    if f['pair']!=pair:continue
                    isn=f['client_isn'] if src==f['client'] else f['server_isn'] if src==f['server'] else None
                    if isn is not None and ((seq-isn-1)&0xffffffff)<=262144:possible.append(i)
                if len(possible)==1:index=possible[0]
                elif len(possible)>1:payload=b''  # Never guess which session owns ambiguous bytes.
        flow=flows[index]
        if flags&2:
            if flags&16:kind='ja4ts';flow['server']=src;direction='response';subject='endpoint'
            else:kind='ja4t';flow['server']=dst;direction='request';subject='probe'
            try:value=tcp_fingerprint(window,opts)
            except ValueError:continue
            fp={'kind':kind,'value':value,'direction':direction,'subject':subject,'source':'pcap TCP handshake'}
            if fp not in flow['fingerprints']:flow['fingerprints'].append(fp)
        if payload:
            parts=flow['parts'].setdefault(src,[])
            if sum(len(p[1]) for p in parts)+len(payload)<=262144:parts.append((seq,payload))
        if flags&5:flow['ended']=True
    rows=[]
    for flow in flows.values():
        key=flow['pair']
        for src,parts in flow['parts'].items():
            for kind,value in _tls(_assemble(parts)):
                if kind=='ja4s':
                    if flow['server'] is not None and flow['server']!=src:flow['ambiguous']=True
                    flow['server']=src
                elif flow['server'] is None:flow['server']=key[1] if key[0]==src else key[0]
                fp={'kind':kind,'value':value,'direction':'response' if kind=='ja4s' else 'request','subject':'endpoint' if kind=='ja4s' else 'probe','source':'pcap TLS hello'}
                if fp not in flow['fingerprints']:flow['fingerprints'].append(fp)
        server=flow['server']
        if server and flow['fingerprints'] and not flow['ambiguous']:rows.append({'address':server[0],'port':server[1],'transport':'tcp','fingerprints':flow['fingerprints']})
    return rows
