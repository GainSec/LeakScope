"""Bounded evidence capture with fixed argv and a private artifact boundary."""
import hashlib
import ipaddress
import json
import os
import re
import signal
import socket
import subprocess
import tempfile
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit, quote
from uuid import uuid4

from .native import identify, IDENTITY_TYPES, identity_transports, canonical_identity_service
from .browser_proxy import same_origin_proxy
from .wire import WireRecorder, exchange, validate_tcp_options, validate_udp_options, _resolve
from .fingerprints import HTTP_CAPTURE_TYPES
from .capture_diagnostics import CaptureDiagnosticError, CURL_CODES, CODES

MAX_BODY=2*1024*1024
MAX_ARTIFACTS=16*1024*1024
_HTTP_PORTS={
    'elastic':{9200},'opensearch':{9200},'couchdb':{5984,6984},'influxdb':{8086},'qdrant':{6333},'weaviate':{8080},
    'minio':{9000,9001},'cephrgw':{7480},'swift':{8080},'seaweedfs':{9333,8888},'webhdfs':{9870,50070},'yarn':{8088},
    'ipfs':{5001},'synology':{5000,5001},'qnap':{8080,443},'webdav':{80,443},'druid':{8888,8082},'pinot':{9000,8099},
    'trino':{8080},'presto':{8080},'opentsdb':{4242},'victoriametrics':{8428},'clickhouse':{8123},'neo4j':{7474},'couchbase':{8091},
    'grafana':{3000},'prometheus':{9090},'jenkins':{8080},'sonarqube':{9000},'mongoexpress':{8081},
}
_ALIASES={'ethernetip-1':'ethernetip','grpcs':'grpc','postgres':'postgresql','mongodb':'mongo','mariadb':'mysql','modbus-tcp':'modbus','opc-ua':'opcua','ethernet-ip':'ethernetip','iec-104':'iec104'}
_HTTP_SERVICES=set(_HTTP_PORTS)|set(HTTP_CAPTURE_TYPES)|{'http','https'}
_WEB_CANDIDATE_PORTS={80,443,8000,8080,8443}


def _http_available(scheme,service,port):
    if service in ('grpc','grpcs'):return False
    return scheme in ('http','https') or service in _HTTP_SERVICES or (service in ('','unknown','unclassified') and port in _WEB_CANDIDATE_PORTS)


class CaptureEvidenceError(ValueError):
    """The attempt failed, but its result contains actual partial wire evidence."""
    def __init__(self,message,result):
        super().__init__(message);self.result=result
        self.code=(result.get('metadata') or {}).get('error_code','capture_failed')


def supported_actions(endpoint):
    _,port,scheme,service,transport=_endpoint(endpoint)
    actions=['check','tcp'] if transport=='tcp' else ['udp']
    if transport in identity_transports(service):actions.append('identity')
    if service=='vnc' and transport=='tcp':actions.append('screenshot')
    if transport=='tcp' and _http_available(scheme,service,port):actions.extend(('http','httpx','screenshot'))
    return actions


def _wire_artifacts(root,recorder,metadata):
    files=[(json.dumps(recorder.document(),ensure_ascii=False).encode(),'application/json','wire-transcript','.json')]
    if recorder.sent:files.append((bytes(recorder.sent),'application/octet-stream','wire-request','.bin'))
    if recorder.received:files.append((bytes(recorder.received),'application/octet-stream','wire-response','.bin'))
    artifacts=[]
    try:
        for body,mime,kind,suffix in files:artifacts.append(_persist(root,body,mime,kind,'native',metadata,suffix))
    except Exception:
        for item in artifacts:Path(item['path']).unlink(missing_ok=True)
        raise
    return artifacts


def _wire_failure(root,recorder,error,metadata):
    # Refused connections and silent failures have no application exchange to retain.
    if not recorder.sent and not recorder.received:
        if isinstance(error,ValueError):raise error from None
        raise ValueError(str(error)) from None
    if not recorder.termination:recorder.termination='identity_rejected'
    code=getattr(error,'code','protocol_rejected')
    metadata=dict(metadata,identity_verified=False,partial=True,error=str(error)[:1000],error_code=code if code in CODES else 'capture_failed',wire_summary=recorder.summary())
    result={'metadata':metadata,'partial':True,'artifacts':_wire_artifacts(root,recorder,metadata)}
    raise CaptureEvidenceError(str(error),result) from None


def _curl_failure(root,trace,headers,body,error,url):
    if not trace.is_file() or trace.is_symlink() or trace.stat().st_size>MAX_ARTIFACTS:raise error from None
    data=trace.read_bytes()
    events=re.findall(rb'^(=> Send|<= Recv) (?:header|data), ([0-9]+) bytes',data,re.M)
    if not events:raise error from None
    summary={'events_sent':sum(direction==b'=> Send' for direction,_ in events),
             'events_received':sum(direction==b'<= Recv' for direction,_ in events),
             'bytes_sent':sum(int(size) for direction,size in events if direction==b'=> Send'),
             'bytes_received':sum(int(size) for direction,size in events if direction==b'<= Recv'),
             'transport':'tcp','tls':url.startswith('https:'),'connection_established':True,
             'termination':'http_capture_failed','receive_limit_reached':False,'evidence_layer':'curl application trace'}
    code=getattr(error,'code','capture_failed')
    metadata={'url':url,'identity_verified':False,'partial':True,'error':str(error)[:1000],'error_code':code if code in CODES else 'capture_failed','wire_summary':summary}
    files=[(trace,'text/plain','http-trace','.trace')]
    if headers.is_file() and headers.stat().st_size<=65536:files.append((headers,'text/plain','headers','.headers'))
    if body.is_file() and body.stat().st_size<=MAX_BODY:files.append((body,'application/octet-stream','response','.body'))
    # Preserve the bounded trace first; optional files must fit the combined budget.
    selected=[];total=0
    for item in files:
        size=item[0].stat().st_size
        if total+size<=MAX_ARTIFACTS:selected.append(item);total+=size
    result=_publish(root,selected,metadata,'curl');result['partial']=True
    raise CaptureEvidenceError(str(error),result) from None


def _endpoint(endpoint):
    if not isinstance(endpoint,dict):raise ValueError('Invalid capture endpoint')
    address=endpoint.get('address')
    if not isinstance(address,str) or not address or len(address)>253 or any(ord(c)<33 for c in address):raise ValueError('Invalid endpoint address')
    address=address.strip('[]').rstrip('.')
    try:address=str(ipaddress.ip_address(address))
    except ValueError:
        try:address=address.encode('idna').decode('ascii').lower()
        except UnicodeError:raise ValueError('Invalid endpoint hostname') from None
        if not all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?',part) for part in address.split('.')):raise ValueError('Invalid endpoint hostname')
    port=endpoint.get('port')
    if isinstance(port,bool):raise ValueError('Invalid endpoint port')
    try:
        number=int(port)
        if not 1<=number<=65535 or str(number)!=str(port):raise ValueError
    except (ValueError,TypeError):raise ValueError('Invalid endpoint port') from None
    scheme=endpoint.get('scheme') or ''
    if not isinstance(scheme,str) or (scheme and not re.fullmatch('[a-zA-Z][a-zA-Z0-9+.-]{0,15}',scheme)):raise ValueError('Unsupported endpoint scheme')
    scheme=scheme.lower()
    service=str(endpoint.get('service_type') or '').lower()
    if service=='auto':service=''  # a provider module name for "not identified", never a protocol
    service=canonical_identity_service(_ALIASES.get(service,service))
    transport=endpoint.get('transport') or 'tcp'
    if transport not in ('tcp','udp'):raise ValueError('Unsupported endpoint transport')
    return address,number,scheme,service,transport


def _child_env(workdir):
    return {'PATH':'/usr/local/bin:/usr/bin:/bin','LANG':'C.UTF-8','TMPDIR':str(workdir),'XDG_CACHE_HOME':str(workdir/'cache'),'XDG_CONFIG_HOME':str(workdir/'config')}


def _resource_path(value):
    if value is None:value='/'
    if not isinstance(value,str) or not value.startswith('/') or value.startswith('//') or len(value)>2000 or '?' in value or '#' in value or any(ord(char)<32 or ord(char)==127 for char in value) or re.search(r'%(?:0a|0d|00)',value,re.I) or re.search(r'%(?![a-fA-F0-9]{2})',value):
        raise ValueError('Capture resource must be an unambiguous same-origin path without query, fragment or control characters')
    return quote(value,safe="/%:@!$&'()*+,;=-._~")


def _enforce_blacklist(address,port,transport,config):
    entries=config.get('blacklist') or []
    if isinstance(entries,str):entries=entries.split(',')
    if not isinstance(entries,(list,tuple,set)):raise ValueError('Invalid capture blacklist configuration')
    entries=[str(entry).strip().strip('[]').rstrip('.').lower() for entry in entries if str(entry).strip()]
    if not entries:return
    def blocked(host):
        host=host.rstrip('.').lower()
        if host in entries:return True
        try:ip=ipaddress.ip_address(host)
        except ValueError:return False
        candidates=[ip]
        if isinstance(ip,ipaddress.IPv6Address) and ip.ipv4_mapped:candidates.append(ip.ipv4_mapped)
        for entry in entries:
            try:
                network=ipaddress.ip_network(entry,strict=False)
                if any(candidate in network for candidate in candidates):return True
            except ValueError:continue
        return False
    if blocked(address):raise ValueError('Capture destination is blacklisted')
    try:ipaddress.ip_address(address);return
    except ValueError:pass
    try:answers=_resolve(address,port,socket.SOCK_DGRAM if transport=='udp' else socket.SOCK_STREAM,time.monotonic()+3)
    except (OSError,ValueError):raise ValueError('Capture destination could not be resolved for blacklist validation') from None
    if not answers:raise ValueError('Capture destination has no resolved addresses')
    if any(blocked(answer[4][0]) for answer in answers):raise ValueError('Capture resolved destination is blacklisted')


def _run(argv,workdir,timeout):
    """Bound output/time/temp bytes and kill the process group on failure."""
    stdout=workdir/'.stdout';stderr=workdir/'.stderr'
    try:
        with stdout.open('wb') as out,stderr.open('wb') as err:
            process=subprocess.Popen(argv,stdout=out,stderr=err,cwd=workdir,env=_child_env(workdir),start_new_session=True)
            deadline=time.monotonic()+timeout
            try:
                while process.poll() is None:
                    if time.monotonic()>deadline:raise CaptureDiagnosticError('timeout')
                    if stdout.stat().st_size>MAX_BODY or stderr.stat().st_size>65536:raise ValueError('Capture tool output exceeded its limit')
                    # Browser scratch/profile data is ephemeral and separate
                    # from the much smaller retained-artifact budget.
                    size=0
                    for path in workdir.rglob('*'):
                        try:
                            if path.is_file() and not path.is_symlink():size+=path.stat().st_size
                        except FileNotFoundError:pass
                    if size>128*1024*1024:raise ValueError('Capture scratch data exceeded its limit')
                    time.sleep(.05)
                if process.returncode:
                    tool=Path(argv[0]).name
                    raise CaptureDiagnosticError(CURL_CODES.get(process.returncode,'tool_failed') if tool=='curl' else 'tool_failed')
            finally:
                # Also clean up browser descendants after an already-exited
                # driver; the process group belongs only to this capture.
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                if process.poll() is None:process.wait(timeout=5)
        if stdout.stat().st_size>MAX_BODY or stderr.stat().st_size>65536:raise ValueError('Capture tool output exceeded its limit')
        return stdout.read_bytes()
    except FileNotFoundError:raise CaptureDiagnosticError('tool_unavailable') from None
    except (OSError,subprocess.SubprocessError):raise ValueError('Capture tool could not complete') from None


def _json(data,label):
    try:value=json.loads(data)
    except (ValueError,UnicodeError,RecursionError):raise ValueError(label+' returned invalid JSON metadata') from None
    if not isinstance(value,dict):raise ValueError(label+' returned invalid metadata')
    return value


def _http_status(value):
    return isinstance(value,int) and not isinstance(value,bool) and 100<=value<=599


def _screenshot_quality(info, blocked):
    """Report browser evidence limits without guessing pixel completeness."""
    title=info.get('title',info.get('Title',''))
    title=title[:1000] if isinstance(title,str) else ''
    status=None
    for value in (info.get('response_code'),info.get('status_code'),info.get('ResponseCode')):
        if isinstance(value,str) and re.fullmatch(r'[1-5][0-9]{2}',value):value=int(value)
        if _http_status(value):status=value;break
    reasons=[];state='unassessed'
    if blocked:
        state='restricted'
        reasons.append(str(blocked)+' external requests were blocked by the same-endpoint capture policy; page rendering may be incomplete.')
    if status is None:
        reasons.append('The browser report did not contain a valid HTTP response status; successful page navigation is unconfirmed.')
    elif status>=400:
        state='http_error'
        reasons.append('The browser received HTTP '+str(status)+'; the image may show an error or access restriction instead of application content.')
    if re.search(r'checking your browser|just a moment|verify (?:you are|that you are) human|security verification|attention required\s*!?\s*\|\s*cloudflare',title,re.I):
        state='challenge'
        reasons.append('The reported page title indicates a browser verification or interstitial; application content is unconfirmed.')
    return {'title':title,'status':status,'partial':bool(reasons),
            'capture_quality':{'state':state,'reasons':reasons,'visual_completeness':'unassessed'}}


def _response_facts(body,headers,content_type):
    allowed={'server','content-type','x-powered-by'};selected={}
    for line in headers.decode('iso-8859-1').splitlines():
        if line.startswith('HTTP/'):selected={}
        elif ':' in line:
            name,value=line.split(':',1);name=name.strip().lower()
            if name in allowed:selected[name]=value.strip()[:1000]
    class Title(HTMLParser):
        def __init__(self):super().__init__();self.inside=False;self.parts=[]
        def handle_starttag(self,tag,attrs):
            if tag.lower()=='title':self.inside=True
        def handle_endtag(self,tag):
            if tag.lower()=='title':self.inside=False
        def handle_data(self,data):
            if self.inside and sum(map(len,self.parts))<2000:self.parts.append(data[:2000])
    title=''
    if 'html' in content_type.lower():
        parser=Title();parser.feed(body[:262144].decode('utf-8',errors='replace'));title=' '.join(''.join(parser.parts).split())[:1000]
    from .hmi_profiles import match_hmi
    matches=match_hmi(title,body[:262144].decode('utf-8',errors='replace'))
    return {'server':selected.get('server',''),'title':title,'body_sha256':hashlib.sha256(body).hexdigest(),'http':{'headers':selected,'title':title},
            'hmi_matches':matches,'hmi_profiles':{item['key']:True for item in matches}}


def _httpx_facts(info):
    allowed={'url','scheme','port','host','a','aaaa','status_code','title','webserver','content_type','content_length','tech','favicon','favicon_path','favicon_url','hash'}
    subset={}
    for key in allowed:
        value=info.get(key)
        if isinstance(value,str):subset[key]=value[:2000]
        elif isinstance(value,(int,bool)):subset[key]=value
        elif isinstance(value,list):subset[key]=[item[:255] for item in value[:32] if isinstance(item,str)]
    server=info.get('webserver') or info.get('server') or ''
    metadata={'server':server[:1000] if isinstance(server,str) else '','httpx':subset}
    favicon=info.get('favicon')
    if isinstance(favicon,(str,int)) and not isinstance(favicon,bool) and re.fullmatch(r'-?\d+',str(favicon)):
        if -(2**31)<=int(favicon)<2**31:metadata['favicon_mmh3']=str(favicon)
    hashes=info.get('hash')
    if isinstance(hashes,dict):
        for name in ('body_sha256','header_sha256'):
            value=hashes.get(name)
            if isinstance(value,str) and re.fullmatch('[a-fA-F0-9]{64}',value):
                metadata[name]=value.lower();subset.setdefault('hash',{})[name]=value.lower()
    return metadata


def _version(tool):
    try:
        path=Path('/app/capture-tool-versions.json')
        if path.stat().st_size>8192:return 'unreported'
        value=json.loads(path.read_text()).get(tool,'unreported')
        return str(value)[:200]
    except (OSError,ValueError,TypeError,AttributeError):return 'unreported'


def _persist(root,data,mime,kind,tool,metadata,suffix):
    if len(data)>MAX_ARTIFACTS:raise ValueError('Capture artifact exceeded the 16 MiB limit')
    token=uuid4().hex;path=root/(token+suffix);temporary=root/('.'+token+'.tmp')
    try:
        with temporary.open('xb') as handle:
            os.chmod(temporary,0o600);handle.write(data);handle.flush();os.fsync(handle.fileno())
        os.replace(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)
    info=dict(metadata,sha256=hashlib.sha256(data).hexdigest(),bytes=len(data),tool_version=_version(tool) if tool!='native' else 'leakscope-native/1')
    return {'path':str(path.resolve()),'mime':mime,'kind':kind,'tool':tool,'metadata':info}


def _publish(root,files,metadata,tool):
    data=[];total=0
    for path,mime,kind,suffix in files:
        if not path.is_file() or path.is_symlink():raise ValueError('Capture tool did not produce the expected artifact')
        size=path.stat().st_size;total+=size
        if size>MAX_ARTIFACTS or total>MAX_ARTIFACTS:raise ValueError('Capture artifacts exceeded the 16 MiB limit')
        data.append((path.read_bytes(),mime,kind,suffix))
    artifacts=[]
    try:
        for body,mime,kind,suffix in data:artifacts.append(_persist(root,body,mime,kind,tool,metadata,suffix))
    except Exception:
        for item in artifacts:Path(item['path']).unlink(missing_ok=True)
        raise
    return {'metadata':metadata,'artifacts':artifacts}


def capture_endpoint(endpoint_dict,action,config):
    address,port,scheme,service,transport=_endpoint(endpoint_dict)
    path=_resource_path(endpoint_dict.get('path','/'))
    if action not in ('http','httpx','screenshot','identity','tcp','udp','check'):raise ValueError('Unsupported capture action')
    if not isinstance(config,dict):raise ValueError('Invalid capture configuration')
    if action=='identity' and service not in IDENTITY_TYPES:raise ValueError('Native identity is not implemented for this protocol')
    if action=='check' and transport!='tcp':raise ValueError('TCP reachability check requires a TCP endpoint')
    options=None
    if action in ('tcp','udp'):
        if transport!=action:raise ValueError(action.upper()+' capture requires a '+action.upper()+' endpoint')
        options,_=(validate_tcp_options if action=='tcp' else validate_udp_options)(config.get(action.upper()+'_OPTIONS'))
    _enforce_blacklist(address,port,transport,config)
    if action=='screenshot' and service=='vnc':
        from .vnc import capture_vnc
        return capture_vnc(endpoint_dict,config)
    root=Path(config.get('CAPTURE_DIR') or os.environ.get('LEAKSCOPE_CAPTURE_DIR') or '/data/artifacts/captures').expanduser().resolve()
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    if action=='screenshot':
        from . import browser_client
        options=browser_client.validate_options(config.get('SCREENSHOT_OPTIONS'))
        if options['backend']=='playwright': return browser_client.capture(endpoint_dict,config,root)
    if action=='check':
        from .reachability import check_tcp
        metadata=check_tcp(address,port,tls=scheme in ('https','grpcs'),timeout=config.get('CAPTURE_TIMEOUT',3))
        return {'metadata':metadata,'artifacts':[_persist(root,json.dumps(metadata).encode(),'application/json','reachability','native',metadata,'.json')]}
    if action in ('tcp','udp'):
        recorder=WireRecorder(transport,options.get('tls',False))
        metadata={'operation':'one explicit datagram send and bounded receive' if action=='udp' else 'one connection, optional explicit send and bounded receive','identity_verified':False}
        try:exchange(address,port,transport,options,recorder)
        except ValueError as exc:_wire_failure(root,recorder,exc,metadata)
        if not recorder.sent and not recorder.received:raise ValueError('Wire exchange completed without application bytes')
        partial=recorder.receive_limit_reached
        metadata.update(wire_summary=recorder.summary(),partial=partial)
        return {'metadata':metadata,'partial':partial,'artifacts':_wire_artifacts(root,recorder,metadata)}
    if action=='identity':
        recorder=WireRecorder(transport)
        kwargs={'tls':scheme in ('https','grpcs')} if service in ('grpc','milvus') else {}
        try:metadata,verified=identify(address,port,service,transport,config.get('CAPTURE_TIMEOUT') or 3,recorder=recorder,**kwargs)
        except ValueError as exc:_wire_failure(root,recorder,exc,{'operation':'native identity attempt','service_type':service})
        recorder.termination='identity_verified'
        metadata.update(identity_verified=True,wire_summary=recorder.summary())
        result={'metadata':metadata,'verified_type':verified,'artifacts':[_persist(root,json.dumps(metadata,ensure_ascii=False,default=str).encode(),'application/json','identity','native',{'service_type':verified},'.json')]}
        try:result['artifacts'].extend(_wire_artifacts(root,recorder,{'service_type':verified}))
        except Exception:
            for item in result['artifacts']:Path(item['path']).unlink(missing_ok=True)
            raise
        return result
    if transport!='tcp':raise ValueError('HTTP capture requires a TCP endpoint')
    if scheme not in ('http','https'):
        if not _http_available(scheme,service,port):raise ValueError('HTTP capture is unavailable for this native protocol endpoint')
        scheme='https' if port in (443,8443,6984,5001) and service!='ipfs' else 'http'
    host='['+address+']' if ':' in address else address
    url=f'{scheme}://{host}:{port}'+path
    with tempfile.TemporaryDirectory(prefix='.capture-',dir=root) as directory:
        workdir=Path(directory);workdir.chmod(0o700)
        if action=='http':
            body=workdir/'response.body';headers=workdir/'response.headers';trace=workdir/'http.trace'
            try:
                output=_run(['curl','--disable','--silent','--show-error','--connect-timeout','5','--max-time','20','--max-filesize',str(MAX_BODY),'--proto','=http,https','--proto-redir','=http,https','--max-redirs','0','--trace',str(trace),'--dump-header',str(headers),'--output',str(body),'--write-out','%{json}','--url',url],workdir,25)
                info=_json(output,'curl')
                if not _http_status(info.get('http_code')):raise ValueError('curl returned no HTTP response status')
                if body.exists() and body.stat().st_size>MAX_BODY:raise ValueError('HTTP response exceeded the 2 MiB body limit')
                if headers.exists() and headers.stat().st_size>65536:raise ValueError('HTTP response headers exceeded the 64 KiB limit')
            except ValueError as exc:_curl_failure(root,trace,headers,body,exc,url)
            metadata={'url':url,'status':info.get('http_code'),'content_type':str(info.get('content_type') or '')[:200],'redirect_policy':'do not follow','tool_version':_version('curl')}
            metadata.update(_response_facts(body.read_bytes() if body.is_file() else b'',headers.read_bytes() if headers.is_file() else b'',metadata['content_type']))
            transfer=workdir/'transfer.json';transfer.write_bytes(output)
            mime=metadata['content_type'].split(';',1)[0]
            if not re.fullmatch(r'[a-zA-Z0-9!#$&^_.+-]+/[a-zA-Z0-9!#$&^_.+-]+',mime):mime='application/octet-stream'
            return _publish(root,[(body,mime,'response','.body'),(headers,'text/plain','headers','.headers'),(transfer,'application/json','transfer','.json'),(trace,'text/plain','http-trace','.trace')],metadata,'curl')
        if action=='httpx':
            with same_origin_proxy(address,port,timeout=25) as (proxy,state):
                output=_run(['httpx','-u',url,'-silent','-json','-status-code','-title','-tech-detect','-ip','-response-time','-content-type','-server','-favicon','-hash','sha256','-http-proxy',proxy,'-no-fallback-scheme','-disable-update-check','-threads','1','-rate-limit','1','-timeout','10','-retries','0','-response-size-to-read',str(MAX_BODY),'-no-stdin'],workdir,25)
            if state['bytes']>MAX_ARTIFACTS or state['expired']:raise ValueError('HTTP fingerprinting exceeded its network budget')
            rows=[_json(line,'httpx') for line in output.splitlines() if line.strip()]
            if len(rows)!=1:raise ValueError('httpx did not return one successful target result')
            info=rows[0];metadata={'url':url,'status':info.get('status_code'),'title':str(info.get('title') or '')[:1000],'technologies':[item[:200] for item in info.get('tech',[])[:32] if isinstance(item,str)] if isinstance(info.get('tech',[]),list) else [],'redirect_policy':'do not follow; no host expansion','tool_version':_version('httpx')}
            if info.get('failed') or not _http_status(metadata['status']):raise ValueError('httpx did not obtain a successful HTTP response')
            metadata.update(_httpx_facts(info));metadata['blocked_external_requests']=state['blocked']
            report=workdir/'httpx.json';report.write_bytes(output)
            return _publish(root,[(report,'application/json','httpx','.json')],metadata,'httpx')
        screenshots=workdir/'screenshots';screenshots.mkdir();report=workdir/'gowitness.jsonl'
        with same_origin_proxy(address,port,timeout=35) as (proxy,state):
            _run(['gowitness','scan','single','--url',url,'--threads','1','--timeout','20','--delay','1','--chrome-path','/usr/local/bin/leakscope-chromium','--chrome-proxy',proxy,'--screenshot-path',str(screenshots),'--screenshot-format','png','--write-jsonl','--write-jsonl-file',str(report),'--skip-network-logs','--skip-html'],workdir,35)
        if state['bytes']>MAX_ARTIFACTS or state['expired']:raise ValueError('Screenshot navigation exceeded its network budget')
        if not report.is_file() or report.stat().st_size>MAX_BODY:raise ValueError('Screenshot capture produced no bounded result')
        rows=[_json(line,'gowitness') for line in report.read_bytes().splitlines() if line.strip()]
        if len(rows)!=1:raise ValueError('Screenshot capture produced no single-target result')
        info=rows[0]
        if info.get('failed'):raise ValueError('Screenshot tool reported a failed navigation')
        final=info.get('final_url') or info.get('FinalURL') or info.get('url') or info.get('URL') or url
        try:
            parsed=urlsplit(final);final_port=parsed.port or (443 if parsed.scheme=='https' else 80)
            if parsed.scheme not in ('http','https') or parsed.hostname!=address or final_port!=port:raise ValueError
        except (TypeError,ValueError):raise ValueError('Screenshot navigation left the selected endpoint') from None
        images=list(screenshots.glob('*.png'))
        if len(images)!=1:raise ValueError('Screenshot capture produced no single image')
        if images[0].read_bytes()[:8]!=b'\x89PNG\r\n\x1a\n':raise ValueError('Screenshot tool produced an invalid PNG')
        metadata={'url':url,'final_url':final,'redirect_policy':'same selected host and port only','blocked_external_requests':state['blocked'],'tool_version':_version('gowitness'),
                  **_screenshot_quality(info,state['blocked'])}
        result=_publish(root,[(images[0],'image/png','screenshot','.png'),(report,'application/x-ndjson','browser-metadata','.jsonl')],metadata,'gowitness')
        result['partial']=metadata['partial']
        return result
