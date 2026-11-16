"""Two bounded Ouroboros handshakes; protocol identity and response-family hints."""
import struct

def _head(major,n):
 if n<24:return bytes([(major<<5)|n])
 for limit,tag,fmt in [(256,24,'B'),(65536,25,'H'),(2**32,26,'I'),(2**64,27,'Q')]:
  if n<limit:return bytes([(major<<5)|tag])+struct.pack('!'+fmt,n)
 raise ValueError('integer out of range')
def encode(v):
 if v is False:return b'\xf4'
 if v is True:return b'\xf5'
 if v is None:return b'\xf6'
 if isinstance(v,int):return _head(0,v) if v>=0 else _head(1,-1-v)
 if isinstance(v,bytes):return _head(2,len(v))+v
 if isinstance(v,str):
  b=v.encode();return _head(3,len(b))+b
 if isinstance(v,(list,tuple)):return _head(4,len(v))+b''.join(encode(x) for x in v)
 if isinstance(v,dict):return _head(5,len(v))+b''.join(encode(k)+encode(x) for k,x in v.items())
 raise ValueError('unsupported CBOR type')
def decode(b):
 def item(pos,depth=0):
  if depth>32 or pos>=len(b):raise ValueError('truncated/deep CBOR')
  t=b[pos];pos+=1;major=t>>5;n=t&31
  if n==31 and major not in (4,5):raise ValueError('invalid indefinite CBOR')
  if major==7 and n in (20,21,22):return ({20:False,21:True,22:None}[n],pos)
  indefinite=n==31
  if n>=24 and not indefinite:
   sizes={24:1,25:2,26:4,27:8};z=sizes.get(n)
   if z is None or pos+z>len(b):raise ValueError('invalid CBOR length')
   n=int.from_bytes(b[pos:pos+z],'big');pos+=z
  if major==0:return n,pos
  if major==1:return -n-1,pos
  if major in (2,3):
   if indefinite or pos+n>len(b):raise ValueError('invalid CBOR string')
   v=b[pos:pos+n];return (v if major==2 else v.decode('utf8')),pos+n
  if major in (4,5):
   if not indefinite and n>16384:raise ValueError('container too large')
   result=[] if major==4 else {};count=0
   while (indefinite and pos<len(b) and b[pos]!=255) or (not indefinite and count<n):
    v,pos=item(pos,depth+1)
    if major==4:result.append(v)
    else:
     x,pos=item(pos,depth+1)
     if not isinstance(v,(int,str,bytes)) or v in result:raise ValueError('invalid or duplicate CBOR map key')
     result[v]=x
    count+=1
    if count>16384:raise ValueError('container too large')
   if indefinite:
    if pos>=len(b) or b[pos]!=255:raise ValueError('missing break')
    pos+=1
   return result,pos
  raise ValueError('unsupported CBOR token')
 v,pos=item(0)
 if pos!=len(b):raise ValueError('trailing CBOR bytes')
 return v

def mux(payload,protocol=0):
 if len(payload)>32768:raise ValueError('Mux payload exceeds 32 KiB')
 return struct.pack('!IHH',0,protocol,len(payload))+payload

def refusal_fingerprint(body):
 from .technology_signatures import NATIVE_SCOPE
 v=decode(body)
 if not isinstance(v,list) or len(v)!=2 or v[0]!=2:return None
 reason=v[1]
 if not isinstance(reason,list) or len(reason)!=3 or reason[:2]!=[2,15] or not isinstance(reason[2],str):return None
 if not 1<=len(reason[2])<=1000:return None
 return dict(NATIVE_SCOPE,kind='ouroboros',value=reason[2],source='native controlled handshake')

def identify_cardano(address,port,timeout=3,recorder=None):
 import time
 from .wire import connect_tcp,RecordedSocket
 deadline=time.monotonic()+timeout;exchanges=[]
 def exchange(request,profile):
  payload=encode(request);sent=mux(payload)
  with connect_tcp(address,port,deadline) as raw:
   if recorder is not None:recorder.connected=True;sock=RecordedSocket(raw,recorder,deadline)
   else:sock=raw
   def exact(n):
    out=b''
    while len(out)<n:
     remaining=deadline-time.monotonic()
     if remaining<=0:raise TimeoutError('Ouroboros handshake deadline exceeded')
     sock.settimeout(remaining);b=sock.recv(n-len(out))
     if not b:raise ValueError('Peer closed the Ouroboros handshake')
     out+=b
    return out
   sock.sendall(sent);header=exact(8);_,protocol,n=struct.unpack('!IHH',header)
   if protocol!=0x8000 or not 1<=n<=32768:raise ValueError('Invalid Ouroboros handshake MUX framing')
   body=exact(n);decoded=decode(body)
   exchanges.append({'probe':profile,'request_hex':sent.hex(),'response_hex':(header+body).hex(),'response':decoded})
   return body,decoded
 body,query=exchange([0,{14:[42,True,0,True],15:[42,True,0,True]}],'ouroboros.version-query.v1')
 if not isinstance(query,list) or len(query)!=2 or query[0]!=3 or not isinstance(query[1],dict):raise ValueError('No valid Ouroboros version-query response')
 versions=query[1];data=versions.get(15,versions.get(14))
 if not isinstance(data,list) or len(data)<4 or isinstance(data[0],bool) or not isinstance(data[0],int) or not 0<=data[0]<=0xffffffff:raise ValueError('Invalid Ouroboros version data')
 metadata={'operation':'Ouroboros version query and network-magic refusal only','protocol':'cardano','network_magic':data[0],'versions':[v for v in versions if isinstance(v,int)],'exchanges':exchanges,'fingerprints':[],'implementation_status':'unknown'}
 if 15 in versions:
  try:
   refusal,_=exchange([0,{15:[(data[0]+1)&0xffffffff,True,0,False]}],'ouroboros.network-magic-mismatch.v1')
   fp=refusal_fingerprint(refusal)
   if fp:metadata['fingerprints'].append(fp)
  except (ValueError,OSError) as exc:
   metadata['implementation_status']='unknown';metadata['implementation_error']=str(exc)[:300]
 return metadata,'cardano'
