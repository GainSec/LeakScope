"""Native, metered enumeration adapters with resumable DNS work.

Public indexes supply correlations; neither shared hosting nor routing origin is
an ownership assertion. These adapters never connect to target service ports.
"""
import copy
import ipaddress
import json
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

import dns.exception
import dns.reversename
import dns.resolver
import requests

from .asset_names import normalize_domain, registrable_domain

SOURCES={'reverse_dns','passive_dns','infrastructure','brute','alterations'}
DEFAULT_WORDLIST=['www','api','app','apps','portal','admin','mail','webmail','smtp','vpn','remote','dev','test','stage','staging','prod','production','beta','demo','status','docs','help','support','auth','login','sso','id','cdn','static','assets','media','files','storage','download','downloads','git','gitlab','jenkins','ci','build','monitor','grafana','prometheus','metrics','kibana','elastic','db','database','mysql','postgres','redis','mongo','mq','mqtt','broker','ftp','sftp','ns1','ns2','dns','internal','intranet','office','web','ws','gateway','edge','iot','ot','scada','dashboard','control']
DEFAULT_ALTERATIONS=['dev','test','stage','staging','prod','internal','api','admin','1','2']
HOSTS={'api.hackertarget.com','stat.ripe.net'}
MAX_BYTES=8*1024*1024
DNS_BATCH=24


class CollectionBlocked(Exception):
    def __init__(self,reason):self.reason=reason


def supports(source,seed):
    kind=seed.get('kind')
    if source=='reverse_dns':return kind=='ip'
    if source=='passive_dns':return kind in {'ip','network','domain'}
    if source=='infrastructure':return kind in {'ip','network','asn'}
    return source in {'brute','alterations'} and kind=='domain'


def _now():return datetime.now(timezone.utc).isoformat()


def _deadline(context):return context.get('deadline',time.monotonic()+30)


def _reserve(spend,bucket,context):
    if time.monotonic()>=_deadline(context):raise CollectionBlocked('deadline')
    if not spend(bucket):raise CollectionBlocked('budget')


def _request(url,*,spend,context,text=False):
    parsed=urlsplit(url)
    if parsed.scheme!='https' or parsed.hostname not in HOSTS or parsed.username or parsed.password or parsed.port not in (None,443):raise ValueError('Invalid enumeration provider URL')
    _reserve(spend,'domain_api',context)
    remaining=max(.1,min(20,_deadline(context)-time.monotonic()))
    response=None
    try:
        response=requests.get(url,headers={'Accept':'text/plain' if text else 'application/json','User-Agent':'LeakScope-enumeration/1'},timeout=(min(5,remaining),remaining),allow_redirects=False,stream=True)
        if response.status_code==429:raise ValueError('Provider rate limit HTTP 429; request was not retried')
        if response.status_code in {401,403}:raise ValueError('Provider denied access')
        if not 200<=response.status_code<300:raise ValueError('Provider returned HTTP '+str(response.status_code))
        chunks=[];size=0
        for chunk in response.iter_content(chunk_size=16384):
            if time.monotonic()>=_deadline(context):raise ValueError('Provider response deadline reached')
            size+=len(chunk)
            if size>MAX_BYTES:raise ValueError('Provider response exceeded 8 MiB safety bound')
            chunks.append(chunk)
        raw=b''.join(chunks).decode('utf-8')
        return raw if text else json.loads(raw)
    except (requests.RequestException,UnicodeError,json.JSONDecodeError,RecursionError):raise ValueError('Provider request failed or returned invalid data; request was not retried') from None
    finally:
        if response is not None:response.close()


def _resolve(name,kind,*,context):
    resolver=dns.resolver.Resolver();resolver.retry_servfail=False
    try:
        answer=resolver.resolve(name.rstrip('.')+'.',kind,lifetime=max(.1,min(2,_deadline(context)-time.monotonic())),search=False)
        return [item.to_text() for item in answer]
    except (dns.resolver.NXDOMAIN,dns.resolver.NoAnswer):return []


def _dns(name,kind,spend,context):
    # Rate state belongs to one source batch; work is serial and no burst pool is used.
    rate=max(1,min(1000,int(context.get('dns_rate',20))))
    next_at=context.get('_dns_next',0)
    delay=next_at-time.monotonic()
    if delay>0:
        if time.monotonic()+delay>=_deadline(context):raise CollectionBlocked('deadline')
        time.sleep(delay)
    _reserve(spend,'dns',context);context['_dns_next']=time.monotonic()+1/rate
    try:return _resolve(name,kind,context=context)
    except (dns.exception.DNSException,OSError):
        context['_dns_errors']=context.get('_dns_errors',0)+1
        return []


def _proof(source,signal,via,**extra):
    return {'source':source,'signal':signal,'via':via,'strength':20,'observed_at':_now(),**extra}


def _add(result,name,proof,*,resolved=False):
    try:
        name=normalize_domain(name,allow_wildcard=False)
        if '.' not in registrable_domain(name):return
    except (ValueError,TypeError):return
    candidate=next((c for c in result['candidates'] if c['domain']==name),None)
    if candidate is None:
        candidate={'domain':name,'relation':'related','evidence':[],'dns_status':'resolved' if resolved else 'unchecked'};result['candidates'].append(candidate)
    if proof not in candidate['evidence']:candidate['evidence'].append(proof)


def _lead(result,kind,value,proof):
    lead={'kind':kind,'value':value,'relation':'infrastructure','ambiguous':False,'evidence':[proof]}
    if lead not in result['leads']:result['leads'].append(lead)


def _ptr(seed,spend,context,result,cursor):
    address=ipaddress.ip_address(seed['value'])
    values=_dns(str(dns.reversename.from_address(str(address))),'PTR',spend,context)
    for name in values:
        _add(result,name,_proof('dns','reverse_dns',str(address),addresses=[str(address)],record_type='PTR',excerpt='PTR name for this address; provider-managed reverse DNS does not prove organization ownership.'))


def _passive(seed,spend,context,result,cursor):
    value=seed['value']
    if seed['kind']=='ip':
        address=ipaddress.ip_address(value)
        if address.version!=4:
            result['notes'].append('HackerTarget reverse-IP index accepts IPv4/CIDR; IPv6 reverse-IP discovery is unavailable from this source.');return
        value=str(address)
    if seed['kind']=='network':
        network=ipaddress.ip_network(value,strict=False)
        if network.version!=4:
            result['notes'].append('HackerTarget reverse-IP index accepts IPv4/CIDR; IPv6 network discovery is unavailable from this source.');return
        index=cursor.get('subnet',0)
        count=1 if network.prefixlen>=24 else 2**(24-network.prefixlen)
        subnet=network if network.prefixlen>=24 else ipaddress.ip_network((int(network.network_address)+index*256,24))
        value=str(subnet)
    elif seed['kind']=='domain':value=normalize_domain(value.removeprefix('*.'))
    url='https://api.hackertarget.com/reverseiplookup/?'+urlencode({'q':value})
    data=_request(url,spend=spend,context=context,text=True)
    if not isinstance(data,str):raise ValueError('Reverse-IP provider returned invalid data')
    if re.match(r'\s*(?:error\b|api\s+(?:count|key|limit)\b)',data,re.I):raise ValueError('Reverse-IP provider rejected or rate-limited the request')
    names=data.splitlines()
    for name in names:
        _add(result,name.strip(),_proof('hackertarget_reverse_ip','shared_ip',value,url=url,addresses=[value] if seed['kind']=='ip' else [],record_type='reverse_ip_index',excerpt='Existing reverse-IP index co-hosting relationship; shared infrastructure is not corporate ownership.'))
    if len(names)>=50:result['notes'].append('HackerTarget anonymous reverse-IP results may be truncated by its 50-result free-tier limit; no anonymous pagination is available.')
    if seed['kind']=='network' and index+1<count and not context.get('demo'):result['continuation']={'subnet':index+1}


def _infrastructure(seed,spend,context,result,cursor):
    value=seed['value']
    if seed['kind']=='asn':
        number=int(str(value).upper().removeprefix('AS'))
        if not 1<=number<=4294967295:raise ValueError('Invalid ASN')
        value='AS'+str(number);endpoint='announced-prefixes'
    else:
        value=str(ipaddress.ip_network(value,strict=False)) if seed['kind']=='network' else str(ipaddress.ip_address(value));endpoint='prefix-overview' if seed['kind']=='network' else 'network-info'
    url='https://stat.ripe.net/data/'+endpoint+'/data.json?'+urlencode({'resource':value})
    response=_request(url,spend=spend,context=context)
    if not isinstance(response,dict) or not isinstance(response.get('data'),dict):raise ValueError('RIPEstat returned invalid routing data')
    data=response['data']
    proof=_proof('ripestat','routing_infrastructure',value,url=url,record_type='routing_index',excerpt='Observed routing origin/prefix relationship; a hosting ASN may serve many unrelated organizations.')
    prefixes=[x.get('prefix') for x in data.get('prefixes',[]) if isinstance(x,dict)] if seed['kind']=='asn' else [data.get('prefix') or data.get('resource')]
    for prefix in prefixes:
        try:prefix=str(ipaddress.ip_network(prefix,strict=False))
        except (ValueError,TypeError):continue
        _lead(result,'network',prefix,proof)
    for item in data.get('asns',[]):
        try:number=int(item.get('asn') if isinstance(item,dict) else item)
        except (ValueError,TypeError):continue
        if 1<=number<=4294967295:_lead(result,'asn','AS'+str(number),proof)


def _scoped(name,context):
    for seed in context.get('seed_domains',[]):
        try:root=registrable_domain(seed)
        except (ValueError,TypeError):continue
        if name==root or name.endswith('.'+root):return True
    return False


def _guesses(source,name,context):
    root=registrable_domain(name);base=name.removeprefix('*.')
    words=context.get('wordlist') or DEFAULT_WORDLIST
    if source=='brute':
        names=[]
        for word in words:
            if not isinstance(word,str):continue
            word=word.lower()
            if all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?',label) for label in word.split('.')) and len(word+'.'+base)<=253:names.append(word+'.'+base)
        return list(dict.fromkeys(names))
    if base==root:return []
    label,parent=base.split('.',1)
    words=context.get('alteration_words') or DEFAULT_ALTERATIONS
    names=[]
    for word in words:
        if not isinstance(word,str) or not re.fullmatch(r'[a-z0-9-]{1,30}',word):continue
        names.extend([label+'-'+word+'.'+parent,word+'-'+label+'.'+parent,label+word+'.'+parent])
    if re.search(r'\d+$',label):
        number=re.search(r'\d+$',label);stem=label[:number.start()]
        names.extend(stem+str(int(number.group())+offset)+'.'+parent for offset in (-1,1,2) if int(number.group())+offset>=0)
    return [name for name in dict.fromkeys(names) if len(name)<=253 and len(name.split('.')[0])<=63]


def _wildcard_answers(parent,state,spend,context):
    controls=state['control_sets'].get(parent)
    if controls is None:
        length=min(35,253-len(parent)-1)
        controls={'names':[('ls-'+uuid4().hex)[:length]+'.'+parent for _ in range(3)],'index':0,'record':0,'answers':[]}
        state['control_sets'][parent]=controls
    while controls['index']<3:
        name=controls['names'][controls['index']];kind=('A','AAAA')[controls['record']]
        controls['answers']=list(dict.fromkeys(controls['answers']+_dns(name,kind,spend,context)))
        controls['record']+=1
        if controls['record']==2:controls['index']+=1;controls['record']=0
    return controls['answers']


def _enumerate(source,seed,spend,context,result,cursor):
    name=normalize_domain(seed['value'].removeprefix('*.'))
    if not _scoped(name,context):
        result['notes'].append('DNS enumeration skipped outside the explicit domain seed scope.');return
    guesses=_guesses(source,name,context)
    if not guesses:return
    if context.get('demo'):
        addresses=_dns(guesses[0],'A',spend,context)
        if addresses:
            result['notes'].append('Demo makes one DNS query; wildcard validation was not performed and requires a normal collection run.')
            _add(result,guesses[0],_proof('dns_'+source,'dns_demo_unvalidated',name,addresses=addresses,record_type='A',strength=10,excerpt='Demo DNS A response; wildcard validation was not performed. The answer may be wildcard DNS and does not confirm a distinct hostname or organizational ownership.'),resolved=True)
        return
    state=cursor
    state.setdefault('index',0);state.setdefault('record',0);state.setdefault('answers',[])
    state.setdefault('control_sets',{})
    count=0
    while state['index']<len(guesses) and count<DNS_BATCH:
        target=guesses[state['index']];kind=('A','AAAA')[state['record']]
        # Guess siblings share a DNS closest-encloser. Nested wordlist guesses
        # can have a different parent from the original seed.
        wildcard=_wildcard_answers(target.split('.',1)[1],state,spend,context)
        if wildcard and not any('Wildcard DNS' in note for note in result['notes']):result['notes'].append('Wildcard DNS answers were detected and matching guesses were excluded; wildcard-only names are not discoveries.')
        state['answers']=list(dict.fromkeys(state['answers']+_dns(target,kind,spend,context)))
        state['record']+=1
        if state['record']<2:continue
        distinct=[value for value in state['answers'] if value not in wildcard]
        if distinct:
            proof=_proof('dns_'+source,'dns_'+source,name,addresses=distinct,record_type='A/AAAA',excerpt='DNS-resolved '+('wordlist guess' if source=='brute' else 'hostname alteration')+' differs from wildcard controls; existence does not prove organizational ownership.')
            _add(result,target,proof,resolved=True)
            for address in distinct:
                try:address=str(ipaddress.ip_address(address))
                except (ValueError,TypeError):continue
                _lead(result,'ip',address,proof)
        state['index']+=1;state['record']=0;state['answers']=[];count+=1
    if state['index']<len(guesses):result['continuation']=copy.deepcopy(state)


def collect(source,seed,*,config,spend,context):
    if source not in SOURCES:raise ValueError('Unsupported enumeration source')
    if not isinstance(seed,dict) or not isinstance(seed.get('value'),str):raise ValueError('Invalid enumeration seed')
    result={'candidates':[],'leads':[],'notes':[],'cacheable':False}
    if not supports(source,seed):return result
    cursor=copy.deepcopy(context.get('cursor') or {})
    try:
        if source=='reverse_dns':_ptr(seed,spend,context,result,cursor)
        elif source=='passive_dns':_passive(seed,spend,context,result,cursor)
        elif source=='infrastructure':_infrastructure(seed,spend,context,result,cursor)
        else:_enumerate(source,seed,spend,context,result,cursor)
    except CollectionBlocked as exc:
        result['blocked']=exc.reason;result['continuation']=cursor
    except (ValueError,TypeError,KeyError,dns.exception.DNSException,OSError):
        result['failed']=True
        result['notes'].append(source+': provider or DNS lookup failed; other evidence is retained. No automatic request retry was made.')
    if context.get('_dns_errors'):result['notes'].append(str(context['_dns_errors'])+' DNS lookups failed or timed out; no automatic query retries were made.')
    if context.get('demo'):
        result['candidates']=result['candidates'][:1];result['leads']=[];result.pop('continuation',None);result.pop('blocked',None)
    return result
