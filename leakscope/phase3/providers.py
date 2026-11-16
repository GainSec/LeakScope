"""Bounded provider transports and normalization without ORM dependencies.

Search is explicitly initiated by the caller. Netlas Responses Search returns
at most 20 documents per request and uses a start offset, not a page-size
parameter (current official Netlas API reference). No paid count query is made
to manufacture a total when the response does not supply one.
"""
import base64
import hashlib
import ipaddress
import json
import math
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import requests

from .usage_policy import validate_demo, demo_metadata

PROVIDERS={
    'shodan':{'label':'Shodan','key':'SHODAN_API_KEY','base':'https://api.shodan.io'},
    'zoomeye':{'label':'ZoomEye','key':'ZOOMEYE_API_KEY','base':'https://api.zoomeye.ai/v2'},
    'netlas':{'label':'Netlas','key':'NETLAS_API_KEY','base':'https://app.netlas.io/api'},
}
MAX_PAGE_BYTES=10*1024*1024
MAX_OBSERVATIONS=10000


class ProviderRateLimit(ValueError):
    def __init__(self,provider,retry_after=60):
        self.provider=provider
        self.retry_after=retry_after
        super().__init__(f'{PROVIDERS[provider]["label"]} rate limit reached; retry after {retry_after} seconds')


def available_providers(config):
    return [{'key':key,'label':row['label'],'configured':bool(config.get(row['key']))} for key,row in PROVIDERS.items()]


def _provider(provider,config):
    if not isinstance(provider,str) or provider not in PROVIDERS:raise ValueError('Unsupported provider')
    if not isinstance(config,dict):raise ValueError('Invalid provider configuration')
    key=config.get(PROVIDERS[provider]['key'],'')
    if not isinstance(key,str) or not key.strip():raise ValueError('Missing '+PROVIDERS[provider]['key'])
    if len(key)>4096 or any(ord(c)<32 for c in key):raise ValueError('Invalid provider key configuration')
    return key


def _redact(value,config):
    value=str(value)
    for name,secret in config.items():
        if isinstance(name,str) and name.upper().endswith('_KEY') and isinstance(secret,str) and secret:
            value=value.replace(secret,'[REDACTED]')
    return value


def _retry_after(value):
    try:return max(1,min(86400,int(value)))
    except (TypeError,ValueError):pass
    try:
        when=parsedate_to_datetime(value)
        if when.tzinfo is None:when=when.replace(tzinfo=timezone.utc)
        return max(1,min(86400,math.ceil((when-datetime.now(timezone.utc)).total_seconds())))
    except (TypeError,ValueError,OverflowError):return 60


def _request(provider,path,config,*,method='GET',params=None,body=None,allow_list=False):
    key=_provider(provider,config)
    from .rate_limit import throttle, defer
    callback = config.get('_rate_limit') or throttle
    callback('netlas:certificates' if provider == 'netlas' and path.startswith('/certs') else provider)
    headers={'Accept':'application/json','User-Agent':'LeakScope/phase3'}
    params=dict(params or {})
    if provider=='shodan':params['key']=key
    elif provider=='zoomeye':headers['API-KEY']=key
    else:headers['Authorization']='Bearer '+key
    response=None
    try:
        response=requests.request(method,PROVIDERS[provider]['base']+path,params=params or None,json=body,headers=headers,timeout=(5,30),stream=True,allow_redirects=False)
        if response.status_code==429:
            retry_after=_retry_after(response.headers.get('Retry-After'))
            defer(provider,retry_after)
            raise ProviderRateLimit(provider,retry_after)
        if not 200<=response.status_code<300:
            raise ValueError(PROVIDERS[provider]['label']+f' returned HTTP {response.status_code}; check permissions and account limits')
        try:size_hint=int(response.headers.get('Content-Length','0'))
        except (TypeError,ValueError):size_hint=0
        if size_hint>MAX_PAGE_BYTES:raise ValueError('Provider page exceeded the 10 MiB limit')
        chunks=[];size=0
        for chunk in response.iter_content(chunk_size=16384):
            if not chunk:continue
            size+=len(chunk)
            if size>MAX_PAGE_BYTES:raise ValueError('Provider page exceeded the 10 MiB limit')
            chunks.append(chunk)
        try:payload=json.loads(b''.join(chunks).decode('utf-8'))
        except (ValueError,UnicodeError,RecursionError):raise ValueError('Provider returned invalid JSON') from None
        if allow_list and isinstance(payload,list):return payload
        if not isinstance(payload,dict):raise ValueError('Provider returned an invalid JSON object')
        if provider=='zoomeye' and payload.get('code',60000)!=60000:raise ValueError('ZoomEye reported a provider error; check query syntax and account limits')
        if payload.get('error'):raise ValueError(PROVIDERS[provider]['label']+' reported a provider error')
        return payload
    except requests.RequestException:
        raise ValueError(PROVIDERS[provider]['label']+' request failed or timed out; no automatic retry was made') from None
    finally:
        if response is not None:response.close()


def _integer(value,label,maximum):
    if isinstance(value,bool):raise ValueError('Invalid '+label)
    try:integer=int(value)
    except (TypeError,ValueError):raise ValueError('Invalid '+label) from None
    if not 1<=integer<=maximum or str(integer)!=str(value):raise ValueError('Invalid '+label)
    return integer


def _clip(value,config,depth=0):
    if depth>6:return '[nested metadata omitted]'
    if isinstance(value,str):return _redact(value[:8192],config)
    if isinstance(value,dict):return {_redact(str(key),config)[:100]:_clip(item,config,depth+1) for key,item in list(value.items())[:64]}
    if isinstance(value,list):return [_clip(item,config,depth+1) for item in value[:32]]
    if isinstance(value,float) and not math.isfinite(value):return None
    if value is None or isinstance(value,(bool,int,float)):return value
    return _redact(str(value)[:1000],config)


def _metadata(raw,provider,config):
    from .geography import extract_provider_geo
    geo=extract_provider_geo(raw,provider)
    value=_clip(raw,config)
    text=json.dumps(value,ensure_ascii=False)
    if len(text.encode('utf-8',errors='replace'))>65536:value={'truncated':True,'preview':text[:8000]}
    value['_provider']=provider
    if geo:value['_geo']=geo
    return value


def _timestamp(value):
    if not isinstance(value,str) or not value.strip() or len(value)>100:return None
    try:datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError:return None
    return value


def _http_hostname(uri,port):
    """Use explicit matching HTTP URL evidence, never reverse-DNS guesses."""
    if not isinstance(uri,str) or len(uri)>8192 or any(ord(char)<32 or ord(char)==127 for char in uri):return None
    try:
        parsed=urlsplit(uri)
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username is not None or parsed.password is not None:return None
        if (parsed.port or (443 if parsed.scheme=='https' else 80))!=port:return None
        host=parsed.hostname.rstrip('.')
        try:ipaddress.ip_address(host);return None,parsed.scheme
        except ValueError:pass
        host=host.encode('idna').decode('ascii').lower()
        if len(host)>253 or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?',label) for label in host.split('.')):return None
        return host,parsed.scheme
    except (ValueError,UnicodeError):return None


def _native_response_service(raw):
    """Specific indexed response forms, never a port or query-profile guess."""
    for field in ('banner','data'):
        text=raw.get(field)
        if not isinstance(text,str):continue
        text=text[:8192]
        if text.startswith(('ceph v027','ceph v2\n')):return 'ceph'
        if re.match(r'^Zookeeper version: [0-9][^\r\n]{0,200}',text) and re.search(r'(?m)^(?:Latency min/avg/max:|Mode:)',text):
            return 'zookeeper'
        if re.match(r'^E?FATAL:\s+unsupported frontend protocol \d+\.\d+: server supports \d+\.\d+ to \d+\.\d+(?:\s|$)',text):
            return 'postgresql'
        if re.match(r'^Mysql Version: [0-9][^\r\n]{0,200}(?:\r?\n|$)',text,re.I) and 'mysql_native_password' in text:
            return 'mysql'
        if (re.match(r'^STAT (?:pid|uptime|time|version) [^\r\n]+(?:\r?\n|$)',text)
                and re.search(r'(?m)^STAT version [0-9][^\r\n]*\r?$',text)
                and re.search(r'(?m)^STAT curr_connections [0-9]+\r?$',text)):
            return 'memcached'
    return ''


def _normalize(provider,row,config):
    if not isinstance(row,dict):return None
    raw=row.get('data') if provider=='netlas' else row
    if not isinstance(raw,dict):return None
    address=raw.get('ip_str') if provider=='shodan' else raw.get('ip')
    if isinstance(address,list):address=address[0] if address else None
    if not isinstance(address,str):return None
    try:address=str(ipaddress.ip_address(address))
    except (ValueError,TypeError):return None
    resolved_ip=address
    portinfo=raw.get('portinfo') if isinstance(raw.get('portinfo'),dict) else {}
    try:port=_integer(raw.get('port') or portinfo.get('port'),'port',65535)
    except ValueError:return None
    module=raw.get('_shodan') if isinstance(raw.get('_shodan'),dict) else {}
    protocol=raw.get('service') or module.get('module') or (raw.get('protocol') if raw.get('protocol') not in ('tcp','udp') else '') or raw.get('prot7') or portinfo.get('service') or ''
    protocol=protocol.lower() if isinstance(protocol,str) else ''
    scheme=next((kind for kind in ('https','http') if protocol==kind or protocol.startswith(kind+'-') or protocol.endswith('/'+kind)),'')
    http=raw.get('http') if isinstance(raw.get('http'),dict) else {}
    # A structured HTTP result or a real status line establishes HTTP, but a
    # TLS certificate or familiar port cannot establish its application protocol.
    http_fields=('title','headers','status','status_code','html','body','server','location')
    http_banner=any(isinstance(raw.get(field),str) and bool(re.match(
        r'^HTTP/(?:1\.[01]|2(?:\.0)?|3(?:\.0)?) [1-5][0-9]{2}(?:[ \r\n]|$)',raw[field][:100]))
        for field in ('data','header','banner'))
    if not scheme and (any(key in http and http[key] is not None for key in http_fields) or http_banner):scheme='http'
    uri=raw.get('uri') or raw.get('url')
    vhost=_http_hostname(uri,port)
    if vhost:
        host,scheme=vhost
        if host:address=host
    transport=raw.get('transport') or raw.get('prot4') or (raw.get('protocol') if raw.get('protocol') in ('tcp','udp') else 'tcp')
    transport=transport.lower() if isinstance(transport,str) else 'tcp'
    if transport not in ('tcp','udp'):return None
    web=raw.get('web') if isinstance(raw.get('web'),dict) else {}
    title=raw.get('title') or http.get('title') or web.get('title') or raw.get('product') or ''
    if isinstance(title,list):title=' '.join(str(v) for v in title[:10] if isinstance(v,(str,int)))
    if not isinstance(title,str):title=''
    service_type=scheme or {'postgres':'postgresql','mongodb':'mongo','elasticsearch':'elastic','rethinkdb':'rethink','opc-ua':'opcua','s7comm':'s7','modbus-tcp':'modbus'}.get(protocol,protocol)
    from .native import canonical_identity_service
    service_type=canonical_identity_service(service_type)
    response_service=_native_response_service(raw) if provider=='zoomeye' and not scheme else ''
    if response_service:service_type=response_service
    # "auto" is a provider module name for "not identified", never a protocol.
    # The saved response text may still open with the protocol's own
    # identification, which is a provider-reported hint and not a verification.
    from .protocol_hints import is_placeholder,protocol_hint,HINT_BASIS
    hint=protocol_hint({'metadata':raw},provider) if not scheme and not response_service and is_placeholder(service_type) else None
    if hint and hint['strength']!='strong':hint=None
    if hint:service_type=hint['service']
    elif service_type=='auto':service_type=''
    headers=http.get('headers') if isinstance(http.get('headers'),dict) else {}
    content_type=next((value for name,value in headers.items() if str(name).lower().replace('_','-')=='content-type'),'')
    if isinstance(content_type,list):content_type=content_type[0] if content_type else ''
    if not content_type:
        indexed_banner=raw.get('header') or raw.get('banner') or raw.get('data') or ''
        if isinstance(indexed_banner,str) and re.match(r'^HTTP/(?:1\.[01]|2(?:\.0)?) [1-5][0-9]{2}(?:[ \r\n]|$)',indexed_banner[:100]):
            header_block=re.split(r'\r?\n\r?\n',indexed_banner[:8192],maxsplit=1)[0]
            match=re.search(r'(?im)^content-type:[ \t]*([^\r\n]+)',header_block)
            if match:content_type=match[1]
    grpc_header=isinstance(content_type,str) and bool(re.fullmatch(r'application/grpc(?:\+[a-z0-9._-]+)?',content_type.split(';',1)[0].strip().lower()))
    if protocol in ('grpc','grpcs') or grpc_header:
        service_type='grpc'
        if protocol=='grpcs':scheme='https'
        elif not scheme:scheme='http'
    if service_type=='ethernetip-1':service_type='ethernetip'
    timestamp=next((_timestamp(raw.get(key)) for key in ('@timestamp','timestamp','update_time','last_updated') if _timestamp(raw.get(key))),None)
    source_id=row.get('_id') or row.get('id') or raw.get('_id') or raw.get('id')
    if not isinstance(source_id,(str,int)) or isinstance(source_id,bool) or not str(source_id):
        identity=json.dumps([provider,address,port,transport,scheme,uri if isinstance(uri,str) else '',raw.get('host') if isinstance(raw.get('host'),str) else ''],ensure_ascii=True)
        source_id=hashlib.sha256(identity.encode()).hexdigest()
    metadata=_metadata(raw,provider,config);metadata['resolved_ip']=resolved_ip
    if response_service:metadata['service_type_basis']='indexed response signature; live identity unverified'
    if hint:
        metadata['service_type_basis']=HINT_BASIS
        metadata['protocol_hint']={key:hint[key] for key in ('service','strength','basis','evidence','field')}
    if isinstance(uri,str):metadata['uri']=_redact(uri[:8192],config)
    return {'address':address,'port':port,'transport':transport,'scheme':scheme,'service_type':service_type[:100],'title':_redact(title[:1000],config),'metadata':metadata,'source_id':_redact(str(source_id)[:255],config),'observed_at':timestamp}


def _page(provider,query,config,page,page_size):
    if provider=='shodan':
        payload=_request(provider,'/shodan/host/search',config,params={'query':query,'page':page})
        rows=payload.get('matches');effective=100
    elif provider=='zoomeye':
        payload=_request(provider,'/search',config,method='POST',body={'qbase64':base64.b64encode(query.encode()).decode(),'page':page,'pagesize':page_size,'sub_type':'all','fields':'ip,port,domain,title,service,protocol,update_time,url,product,device,header,banner,continent.name,country.name,province.name,city.name,lon,lat'})
        rows=payload.get('data');effective=page_size
    else:
        payload=_request(provider,'/responses/',config,params={'q':query,'start':(page-1)*20})
        rows=payload.get('items');effective=20
    if not isinstance(rows,list):raise ValueError(PROVIDERS[provider]['label']+' returned a malformed result list')
    total=payload.get('total')
    if isinstance(total,dict):total=total.get('value')
    if isinstance(total,bool) or not isinstance(total,(int,str)) or not str(total).isdecimal():total=None
    else:total=int(total)
    return rows,total,effective


def demo_provider_policy(provider):
    """Describe the minimum requested search unit without a provider/account call.

    Shodan: https://developer.shodan.io/api (host/search pages of up to100).
    Netlas: https://docs.netlas.io/api-reference/#tag/Responses/operation/responses_search
    (search returns up to20; no search size parameter). ZoomEye exposes an integer
    pagesize in https://github.com/knownsec/ZoomEye-python/blob/master/api.md;
    request1, without claiming an undocumented account-specific billing minimum.
    """
    if not isinstance(provider,str) or provider not in PROVIDERS:
        raise ValueError('Unsupported provider')
    return {'provider':provider,'max_requests':1,'max_pages':1,'max_results':1,
            'wire_page_size':{'shodan':100,'zoomeye':1,'netlas':20}[provider],
            'page_size_adjustable':provider=='zoomeye',
            'billing_note':'Shodan and Netlas search use fixed-size pages. ZoomEye requests pagesize1; account billing and server minimums still apply. No extra count/account request is made.'}


def effective_page_size(provider,page_size):
    """Results per provider page: fixed for Shodan and Netlas, requested for ZoomEye."""
    return {'shodan':100,'netlas':20}.get(provider,page_size)


def search_provider(provider,query,config,*,page_count=1,page_size=100,start_page=1,demo=False):
    demo=validate_demo(demo)
    _provider(provider,config)
    if demo:
        page_count=1;start_page=1;page_size=demo_provider_policy(provider)['wire_page_size']
    if not isinstance(query,str) or not query.strip() or len(query)>8000:raise ValueError('Query must contain 1–8000 characters')
    page_count=_integer(page_count,'page count',20);page_size=_integer(page_size,'page size',100)
    if isinstance(start_page,str):raise ValueError('Invalid start page')
    start_page=_integer(start_page,'start page',10000)
    observations=[];errors=[];pages=0;total=None;seen=set();raw_seen=0;more_available=False;retry_after=None
    maximum=1 if demo else MAX_OBSERVATIONS
    for page in range(start_page,start_page+page_count):
        try:rows,reported_total,effective=_page(provider,query.strip(),config,page,page_size)
        except ValueError as exc:
            if not pages:raise
            errors.append(str(exc))
            if isinstance(exc,ProviderRateLimit):retry_after=exc.retry_after
            more_available=True
            break
        pages+=1;raw_seen+=len(rows)
        if reported_total is not None:total=reported_total
        malformed=0
        for row in rows:
            item=_normalize(provider,row,config)
            if item is None:malformed+=1;continue
            identity=(item['source_id'],item['address'],item['port'],item['transport'],item['scheme'])
            if identity in seen:continue
            seen.add(identity);observations.append(item)
            if len(observations)>=maximum:break
        if malformed:errors.append(f'Skipped {malformed} malformed record(s) on page {page}.')
        # Results on the pages before the start count towards the provider's total.
        more_available=(((page-1)*effective+len(rows)<total) if total is not None else len(rows)>=effective) or len(observations)>=MAX_OBSERVATIONS
        if demo and raw_seen>len(observations):more_available=True
        if not rows or not more_available or len(observations)>=MAX_OBSERVATIONS:break
    if errors and not observations:raise ValueError(PROVIDERS[provider]['label']+' returned no usable observations: '+' '.join(errors[:3]))
    result={'observations':observations,'partial':bool(errors or more_available),'errors':errors[:20],'pages':pages,'total':total if total is not None else len(observations),'total_is_exact':total is not None,'more_available':more_available,
            'start_page':start_page,'next_page':start_page+pages,'page_size':effective_page_size(provider,page_size)}
    if retry_after is not None:result['retry_after']=retry_after
    if demo:result.update(demo_metadata(**demo_provider_policy(provider),requests_made=pages,received_rows=raw_seen))
    return result


def _count(value):
    if isinstance(value,bool):return None
    if isinstance(value,(int,str)):
        try:return int(value)
        except ValueError:return None
    return None


def account_info(provider,config):
    _provider(provider,config)
    out={}
    if provider=='shodan':
        payload=_request(provider,'/api-info',config)
        for name in ('query_credits','scan_credits'):
            value=_count(payload.get(name))
            if value is not None:out[name]=value
    elif provider=='zoomeye':
        payload=_request(provider,'/userinfo',config,method='POST',body={})
        data=payload.get('data') if isinstance(payload.get('data'),dict) else {}
        subscription=data.get('subscription') if isinstance(data.get('subscription'),dict) else {}
        for name in ('points','zoomeye_points'):
            value=_count(subscription.get(name))
            if value is not None:out[name]=value
    else:
        payload=_request(provider,'/users/profile_data/',config)
        for section,field,name in (('requests_left','remained','requests_left'),('requests_left','limit','requests_limit'),('coins','left','coins_left'),('coins','total_coins_spent','coins_spent'),('scan_coins','left','scan_coins_left')):
            data=payload.get(section) if isinstance(payload.get(section),dict) else {}
            value=_count(data.get(field))
            if value is not None:out[name]=value
    return out
