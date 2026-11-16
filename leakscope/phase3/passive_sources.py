"""Metered lookups of existing public indexes; never submit scans or probe targets."""
import copy
import csv
import io
import ipaddress
import json
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlencode, urlsplit, urlunsplit

import requests

from .asset_names import normalize_domain, registrable_domain

SOURCES={'crtsh','certspotter','hackertarget','urlscan','gleif'}
BUCKETS={'crtsh':'public_ct','certspotter':'public_ct','hackertarget':'domain_api','urlscan':'domain_api','gleif':'org_api','commoncrawl':'domain_api','wayback':'domain_api'}
HOSTS={'crt.sh','api.certspotter.com','api.hackertarget.com','urlscan.io','api.gleif.org','index.commoncrawl.org','web.archive.org'}
MAX_BYTES=2*1024*1024
SHARED_CERTIFICATE_ROOT_THRESHOLD=10


class CollectionBlocked(ValueError):
    def __init__(self,reason):self.reason=reason


class LookupError(ValueError):
    """Only fixed, safe status classifications cross the collector boundary."""


def _now():
    return datetime.now(timezone.utc).isoformat()


def _text(value,maximum=1600):
    return value[:maximum] if isinstance(value,str) else ''


def _root(value):
    name=normalize_domain(value,allow_wildcard=True)
    root=registrable_domain(name)
    if '.' not in root or root.endswith(('.local','.localhost','.internal','.invalid','.test')):
        raise ValueError('Enter a publicly registrable domain')
    return name,root


def _in_scope(name,root):
    name=name.removeprefix('*.')
    return name==root or name.endswith('.'+root)


def _request(url,*,source,config,spend,context,text=False):
    """One charged outgoing request, bounded time/bytes, no redirect or retry."""
    parsed=urlsplit(url)
    if parsed.scheme!='https' or parsed.hostname not in HOSTS or parsed.username or parsed.password or parsed.port not in (None,443):
        raise LookupError('Provider URL is invalid')
    started=time.monotonic()
    deadline=context.get('deadline',started+20)
    if not isinstance(deadline,(int,float)) or isinstance(deadline,bool):deadline=started+20
    deadline=min(deadline,started+20)
    remaining=deadline-started
    if remaining<=0:raise CollectionBlocked('deadline')
    headers={'Accept':'text/plain' if text else 'application/json','User-Agent':'LeakScope-passive-discovery/1'}
    key=config.get('CERTSPOTTER_API_KEY' if source=='certspotter' else 'URLSCAN_API_KEY' if source=='urlscan' else '', '')
    if key:
        if not isinstance(key,str) or len(key)>4096 or any(ord(c)<32 or ord(c)==127 for c in key):raise LookupError('Provider credential configuration is invalid')
        headers['Authorization' if source=='certspotter' else 'api-key']=('Bearer '+key) if source=='certspotter' else key
    if not spend(BUCKETS[source]):raise CollectionBlocked('budget')
    response=None
    try:
        timeout=min(20,max(.1,remaining))
        response=requests.get(url,headers=headers,timeout=(min(5,timeout),timeout),stream=True,allow_redirects=False)
        if response.status_code==429:raise LookupError('Provider rate limit HTTP 429; no automatic retry was made')
        if response.status_code in (401,403):raise LookupError('Provider access denied HTTP '+str(response.status_code))
        if 300<=response.status_code<400:raise LookupError('Provider redirect refused')
        if not 200<=response.status_code<300:raise LookupError('Provider returned HTTP '+str(response.status_code))
        try:length=int(response.headers.get('Content-Length','0'))
        except (ValueError,TypeError):length=0
        if length>MAX_BYTES:raise LookupError('Provider response exceeded the 2 MiB limit')
        chunks=[];size=0
        for chunk in response.iter_content(chunk_size=16384):
            if time.monotonic()>=deadline:raise LookupError('Collection deadline reached while reading provider response')
            if not chunk:continue
            size+=len(chunk)
            if size>MAX_BYTES:raise LookupError('Provider response exceeded the 2 MiB limit')
            chunks.append(chunk)
        raw=b''.join(chunks).decode('utf-8')
        if text:return raw
        try:return json.loads(raw)
        except (ValueError,RecursionError):raise LookupError('Provider returned invalid JSON') from None
    except (requests.RequestException,UnicodeError):
        raise LookupError('Provider request failed or timed out; no automatic retry was made') from None
    finally:
        if response is not None:response.close()


def _proof(source,url,via,signal,strength=30,**extra):
    return {'source':source,'url':url,'via':via,'signal':signal,'strength':strength,'observed_at':_now(),**extra}


def _add(result,name,proof,relation='subdomain'):
    try:name,_=_root(name)
    except (ValueError,TypeError):return
    candidate=result.setdefault('_index',{}).get(name)
    if candidate is None:
        if result['_limit'] is not None and len(result['candidates'])>=result['_limit']:
            _note(result,'Source candidate limit reached; omitted names were not retained')
            return
        candidate={'domain':name,'relation':relation,'evidence':[],'identifiers':[]}
        result['candidates'].append(candidate);result['_index'][name]=candidate
    if proof not in candidate['evidence'] and len(candidate['evidence'])<12:candidate['evidence'].append(proof)


def _note(result,message):
    if message not in result['notes']:result['notes'].append(message)


def _list(value):
    return value if isinstance(value,list) else []


def _domain_candidates(source,seed,root,data,result,url):
    if not isinstance(data,list):raise LookupError('Provider returned an invalid certificate record list')
    for row in data:
        if not isinstance(row,dict):continue
        if source=='crtsh':
            names=_text(row.get('name_value'),MAX_BYTES).splitlines()
            certificate_id=str(row['id'])[:128] if isinstance(row.get('id'),(str,int)) and not isinstance(row.get('id'),bool) else ''
            source_url='https://crt.sh/?'+urlencode({'id':certificate_id}) if certificate_id.isdigit() else url
            signal='historical_certificate_name'
        else:
            names=_list(row.get('dns_names'))
            certificate_id=str(row['id'])[:128] if isinstance(row.get('id'),(str,int)) and not isinstance(row.get('id'),bool) else ''
            source_url=url
            signal='certificate_san'
        valid=[]
        for name in names:
            try:valid.append(_root(name)[0])
            except (ValueError,TypeError):continue
        if not any(_in_scope(name,root) for name in valid):
            result['cacheable']=False
            _note(result,('crt.sh' if source=='crtsh' else 'Cert Spotter')+' returned a certificate without a DNS name in the requested scope; unrelated record was ignored')
            continue
        names=valid
        certificate_roots={_root(name)[1] for name in names}
        for name in names:
            try:name,_=_root(name)
            except (ValueError,TypeError):continue
            scoped=_in_scope(name,root)
            hosting=not scoped and len(certificate_roots)>SHARED_CERTIFICATE_ROOT_THRESHOLD
            proof=_proof(source,source_url,root,'shared_hosting_certificate' if hosting else signal,10 if hosting else 30 if scoped else 35,
                certificate_root_count=len(certificate_roots),
                certificate_id=certificate_id,not_before=_text(row.get('not_before'),80),not_after=_text(row.get('not_after'),80),
                first_seen=_text(row.get('entry_timestamp'),80),record_type='certificate',
                excerpt=('Certificate index reported this name; historical issuance does not prove current reachability or ownership.' if source=='crtsh' else 'Certificate includes this name alongside more than ten distinct domain roots; low-priority reference until independent ownership evidence exists.' if hosting else 'Unexpired certificate issuance includes this DNS name; shared SANs do not establish corporate ownership.'))
            _add(result,name,proof,'subdomain' if scoped else 'related')
    if source=='certspotter' and data:
        last=data[-1].get('id') if isinstance(data[-1],dict) else None
        if not result['_demo'] and isinstance(last,str) and last and last!=result.get('_after'):
            result['continuation']={'after':last}
        elif not result['_demo']:_note(result,'Cert Spotter page has no advancing issuance ID; pagination could not safely continue')
        _note(result,'Cert Spotter contains unexpired issuances only; expired certificate history requires a historical source such as crt.sh')


def _crtsh(seed,root,config,spend,context,result):
    url='https://crt.sh/?'+urlencode({'q':'%.'+root,'output':'json'})
    data=_request(url,source='crtsh',config=config,spend=spend,context=context)
    _domain_candidates('crtsh',seed,root,data,result,url)


def _certspotter(seed,root,config,spend,context,result):
    params={'domain':root,'include_subdomains':'true','expand':'dns_names'}
    after=(context.get('cursor') or {}).get('after')
    if isinstance(after,str) and 0<len(after)<=200:
        params['after']=after;result['_after']=after
    url='https://api.certspotter.com/v1/issuances?'+urlencode(params)
    data=_request(url,source='certspotter',config=config,spend=spend,context=context)
    _domain_candidates('certspotter',seed,root,data,result,url)


def _hackertarget(seed,root,config,spend,context,result):
    url='https://api.hackertarget.com/hostsearch/?'+urlencode({'q':root})
    data=_request(url,source='hackertarget',config=config,spend=spend,context=context,text=True)
    if not isinstance(data,str):raise LookupError('Provider returned an invalid host-search response')
    if re.match(r'\s*(?:error\b|api\s+(?:count|key|limit)\b)',data,re.I):raise LookupError('Host-search provider rejected or rate-limited the lookup')
    try:rows=list(csv.reader(io.StringIO(data)))
    except csv.Error:raise LookupError('Provider returned malformed host-search records') from None
    if len(rows)>=50:_note(result,'HackerTarget anonymous host-search results may be truncated by upstream free-tier limits; this endpoint has no anonymous pagination')
    for row in rows:
        if len(row)!=2:continue
        try:name,_=_root(row[0].strip());address=str(ipaddress.ip_address(row[1].strip()))
        except (ValueError,TypeError):continue
        if not _in_scope(name,root):continue
        proof=_proof('hackertarget',url,root,'passive_host_record',35,addresses=[address],record_type='host_index',excerpt='Existing host-search index associates this DNS name with '+address+'; this is not a fresh resolution or ownership claim.')
        _add(result,name,proof)
        lead={'kind':'ip','value':address,'relation':'infrastructure','ambiguous':False,'evidence':[proof]}
        if not context.get('demo') and lead not in result['leads']:result['leads'].append(lead)


def _scan_details(page, task):
    # Search results already carry useful metadata; never fetch scan detail pages.
    details={}
    for source,target,maximum in [('title','title',500),('server','server',160),('asn','asn',40),('asnname','network_name',200),('country','country',8),('tlsIssuer','tls_issuer',200)]:
        value=page.get(source)
        if isinstance(value,str) and value:details[target]=value[:maximum]
    status=str(page.get('status',''))
    if re.fullmatch(r'[1-5][0-9]{2}',status):details['http_status']=status
    value=page.get('url')
    if isinstance(value,str) and len(value)<=8192 and not any(ord(c)<32 for c in value):
        try:
            parsed=urlsplit(value)
            if parsed.scheme in {'http','https'} and parsed.hostname and not parsed.username and not parsed.password:
                authority=parsed.hostname+(':'+str(parsed.port) if parsed.port else '')
                details['recorded_url']=urlunsplit((parsed.scheme,authority,parsed.path[:1024] or '/','',''))
        except ValueError:pass
    observed=None
    value=task.get('time')
    if isinstance(value,str) and len(value)<=80:
        try:
            stamp=datetime.fromisoformat(value.replace('Z','+00:00'))
            if stamp.tzinfo is not None:observed=stamp.astimezone(timezone.utc).isoformat()
        except ValueError:pass
    return details,observed


def _urlscan(seed,root,config,spend,context,result):
    size=1 if context.get('demo') else 100
    params={'q':'page.domain:'+root,'size':size}
    after=(context.get('cursor') or {}).get('search_after')
    if isinstance(after,list) and 1<=len(after)<=4 and all(isinstance(v,(str,int)) and not isinstance(v,bool) and len(str(v))<=200 for v in after):params['search_after']=','.join(str(v) for v in after)
    url='https://urlscan.io/api/v1/search/?'+urlencode(params)
    data=_request(url,source='urlscan',config=config,spend=spend,context=context)
    if not isinstance(data,dict) or not isinstance(data.get('results'),list):raise LookupError('Provider returned an invalid scan-index result list')
    rows=data['results']
    # has_more only denotes totals beyond 10,000; page length controls exhaustion.
    more=len(rows)>=size
    if len(rows)<size and (data.get('has_more') or isinstance(data.get('total'),int) and data['total']>len(rows)):
        _note(result,'URLscan returned a short final page with inconsistent total metadata; pagination uses page length and does not follow supplied URLs')
    if more and rows and not context.get('demo'):
        last=rows[-1].get('sort') if isinstance(rows[-1],dict) else None
        if isinstance(last,list) and last and last!=after:result['continuation']={'search_after':last}
        else:_note(result,'URLscan reported more existing scans but returned no advancing sort cursor; pagination stopped safely')
    if data.get('search_date_limit_days') is not None:_note(result,'URLscan account history window is limited to '+str(data['search_date_limit_days'])[:20]+' days by the provider')
    for row in rows:
        if not isinstance(row,dict):continue
        page=row.get('page') if isinstance(row.get('page'),dict) else {}
        task=row.get('task') if isinstance(row.get('task'),dict) else {}
        key=_text(row.get('_id'),64)
        source_url='https://urlscan.io/result/'+key+'/' if re.fullmatch(r'[A-Za-z0-9-]{16,64}',key) else url
        details,observed=_scan_details(page,task)
        addresses=[]
        try:addresses=[str(ipaddress.ip_address(page.get('ip')))]
        except (ValueError,TypeError):pass
        for value in (page.get('domain'),task.get('domain')):
            try:name,_=_root(value)
            except (ValueError,TypeError):continue
            if not _in_scope(name,root):continue
            _add(result,name,_proof('urlscan',source_url,root,'historical_page_domain',35,record_type='existing_scan',addresses=addresses,last_seen=observed,observed_at=observed,collected_at=_now(),historical=True,scan_metadata=details,excerpt='Existing public scan page/task hostname and available metadata; no new scan was submitted and resource domains are excluded. Shared hosting and metadata are not proof of ownership.'))


def _entity_names(row):
    attributes=row.get('attributes') if isinstance(row.get('attributes'),dict) else {}
    entity=attributes.get('entity') if isinstance(attributes.get('entity'),dict) else {}
    legal=entity.get('legalName') if isinstance(entity.get('legalName'),dict) else {}
    values=[legal.get('name')]
    for key in ('otherNames','transliteratedOtherNames'):
        values.extend(item.get('name') for item in _list(entity.get(key))[:20] if isinstance(item,dict))
    return list(dict.fromkeys(name.strip() for name in values if isinstance(name,str) and 2<len(name.strip())<=200 and not any(ord(c)<32 for c in name)))


def _normalized_company(value):
    value=re.sub(r'[^\w\s]',' ',value.casefold())
    words=value.split()
    while words and words[-1] in {'corp','corporation','inc','incorporated','llc','ltd','limited','co','company','plc'}:words.pop()
    return ' '.join(words)


def _entity_state(row):
    attributes=row.get('attributes') if isinstance(row.get('attributes'),dict) else {}
    entity=attributes.get('entity') if isinstance(attributes.get('entity'),dict) else {}
    registration=attributes.get('registration') if isinstance(attributes.get('registration'),dict) else {}
    state={'entity_status':_text(entity.get('status'),32),'registration_status':_text(registration.get('status'),32),
        'last_seen':_text(registration.get('lastUpdateDate'),80)}
    stale=state['entity_status'].upper()=='INACTIVE' or state['registration_status'].upper() in {'LAPSED','RETIRED','ANNULLED','DUPLICATE'}
    return state,stale


def _entity_lead(result,name,lei,url,signal,relation,*,ambiguity=False,parent_lei='',state=None):
    proof=_proof('gleif',url,lei,signal,20 if ambiguity else 60,
        source_record_id=lei,record_type='legal_entity',**(state or {}),excerpt='GLEIF legal entity '+lei+' reports '+name+('. Name-search match is ambiguous; no ownership relationship is asserted.' if ambiguity else '. A legal entity record supplies an identity lead, not a website or domain ownership claim.'))
    lead={'kind':'company','value':name,'relation':relation,'ambiguous':ambiguity,'evidence':[proof]}
    if parent_lei:lead['parent_lei']=parent_lei
    if lead not in result['leads']:result['leads'].append(lead)


def _gleif_children(lei,page,config,spend,context,result,size):
    if not re.fullmatch(r'[A-Z0-9]{18}[0-9]{2}',lei):raise LookupError('Invalid GLEIF continuation')
    result['continuation']={'phase':'children','lei':lei,'page':page}
    url='https://api.gleif.org/api/v1/lei-records/'+lei+'/direct-children?'+urlencode({'page[size]':size,'page[number]':page})
    children=_request(url,source='gleif',config=config,spend=spend,context=context)
    if not isinstance(children,dict) or not isinstance(children.get('data'),list):raise LookupError('GLEIF returned an invalid subsidiary list')
    rows=children['data']
    for child in rows:
        if not isinstance(child,dict) or child.get('type')!='lei-records':continue
        child_lei=_text(child.get('id'),20)
        if not re.fullmatch(r'[A-Z0-9]{18}[0-9]{2}',child_lei):continue
        state,stale=_entity_state(child)
        for name in _entity_names(child):_entity_lead(result,name,child_lei,url,
            'legal_entity_search' if stale else 'legal_entity_child','historical' if stale else 'subsidiary',
            ambiguity=stale,parent_lei=lei,state=state)
        if stale:_note(result,'An inactive or stale subsidiary record is retained as historical review context; it was not used for automatic company expansion')
    more=bool(rows) and (len(rows)>=size or isinstance(children.get('links'),dict) and bool(children['links'].get('next')))
    if more:result['continuation']={'phase':'children','lei':lei,'page':page+1}
    else:result.pop('continuation',None)
    _note(result,'GLEIF queried direct children only; parent and ultimate-parent records were not requested')


def _gleif(seed,config,spend,context,result):
    size=1 if context.get('demo') else 100
    cursor=context.get('cursor') or {}
    page=cursor.get('page',1)
    if not isinstance(page,int) or isinstance(page,bool) or not 1<=page<=1000000:raise LookupError('Invalid GLEIF page cursor')
    if cursor.get('phase')=='children':
        _gleif_children(cursor.get('lei',''),page,config,spend,context,result,size);return
    params={'filter[fulltext]':seed['value'],'page[size]':size,'page[number]':page}
    country=context.get('country')
    country_valid=isinstance(country,str) and bool(re.fullmatch(r'[A-Za-z]{2}',country))
    country_unapplied=bool(country) and not country_valid
    if country_valid:params['filter[entity.legalAddress.country]']=country.upper()
    elif country_unapplied:_note(result,'GLEIF country filtering requires an ISO 3166-1 alpha-2 code; the country filter was not applied and all entity matches require review')
    url='https://api.gleif.org/api/v1/lei-records?'+urlencode(params)
    data=_request(url,source='gleif',config=config,spend=spend,context=context)
    if not isinstance(data,dict) or not isinstance(data.get('data'),list):raise LookupError('GLEIF returned an invalid entity list')
    rows=data['data'];exact=list(cursor.get('exact',[]));seen=cursor.get('seen',0)+len(rows)
    links=data.get('links') if isinstance(data.get('links'),dict) else {}
    meta=data.get('meta') if isinstance(data.get('meta'),dict) else {}
    pagination=meta.get('pagination') if isinstance(meta.get('pagination'),dict) else {}
    total=pagination.get('total')
    valid_total=isinstance(total,int) and not isinstance(total,bool) and total>=0
    truncated=bool(rows) and (bool(links.get('next')) or valid_total and total>seen or not valid_total and len(rows)>=size)
    wanted={_normalized_company(name) for name in [seed['value'],*context.get('company_names',[])] if isinstance(name,str)}
    entities=[]
    for row in rows:
        if not isinstance(row,dict) or row.get('type')!='lei-records':continue
        lei=_text(row.get('id'),20)
        if not re.fullmatch(r'[A-Z0-9]{18}[0-9]{2}',lei):continue
        names=_entity_names(row);state,stale=_entity_state(row)
        matched=any(_normalized_company(name) in wanted for name in names) and not stale
        entities.append((lei,names,matched,state,stale))
        if matched and not any(item[0]==lei for item in exact):exact.append([lei,names,state])
        if stale:_note(result,'GLEIF record has an inactive entity or stale LEI registration; current corporate relationships were not inferred. A lapsed registration alone does not establish business inactivity')
    for lei,names,matched,state,stale in entities:
        ambiguous=not matched or len(exact)!=1 or truncated or country_unapplied
        for name in names:_entity_lead(result,name,lei,'https://api.gleif.org/api/v1/lei-records/'+lei,
            'legal_entity_alias' if not ambiguous else 'legal_entity_search','historical' if stale else 'alias' if not ambiguous else 'entity_candidate',ambiguity=ambiguous,state=state)
    if not rows and page==1:_note(result,'GLEIF returned no legal entity records; absence is not proof that the company has no subsidiaries')
    if truncated:
        _note(result,'Additional GLEIF entity-search pages remain; exact names on this page are not assumed unique')
        if not context.get('demo'):result['continuation']={'phase':'search','page':page+1,'seen':seen,'exact':exact[:2]}
        return
    if not exact and rows:_note(result,'GLEIF entity-name matches require disambiguation; no parent or subsidiary lookup was performed')
    if len(exact)>1:_note(result,'Multiple exact-name legal entities require review; subsidiary expansion was not performed')
    if len(exact)!=1 or context.get('demo') or country_unapplied:return
    lei,names,state=exact[0]
    if page>1:
        for name in names:_entity_lead(result,name,lei,'https://api.gleif.org/api/v1/lei-records/'+lei,'legal_entity_alias','alias',state=state)
    _gleif_children(lei,1,config,spend,context,result,size)


def normalize_result(result):
    """Reclassify shared-certificate references using saved facts, without I/O.

    More than ten distinct registrable roots is a conservative shared-hosting
    heuristic, not a test of ownership. Full issuance counts emitted by the
    collector survive candidate caps; old cached counts are lower bounds from
    the names retained in that payload. Scoped DNS names stay normal findings.
    """
    if not isinstance(result,dict):raise ValueError('Invalid passive result')
    normalized=copy.deepcopy(result)
    groups={}
    for candidate in _list(normalized.get('candidates')):
        if not isinstance(candidate,dict):continue
        try:_,root=_root(candidate.get('domain'))
        except (ValueError,TypeError):continue
        for proof in _list(candidate.get('evidence')):
            if not isinstance(proof,dict) or proof.get('source') not in {'crtsh','certspotter'}:continue
            certificate_id=proof.get('certificate_id')
            if not isinstance(certificate_id,str) or not certificate_id:continue
            groups.setdefault((proof['source'],certificate_id),set()).add(root)
    for candidate in _list(normalized.get('candidates')):
        if not isinstance(candidate,dict):continue
        try:name,_=_root(candidate.get('domain'))
        except (ValueError,TypeError):continue
        for proof in _list(candidate.get('evidence')):
            if not isinstance(proof,dict) or proof.get('source') not in {'crtsh','certspotter'}:continue
            certificate_id=proof.get('certificate_id')
            known=proof.get('certificate_root_count',0)
            known=known if isinstance(known,int) and not isinstance(known,bool) and known>=0 else 0
            observed=len(groups.get((proof['source'],certificate_id),set())) if isinstance(certificate_id,str) else 0
            count=max(known,observed)
            if count:proof['certificate_root_count']=count
            try:_,queried_root=_root(proof.get('via'))
            except (ValueError,TypeError):queried_root=''
            scoped=bool(queried_root) and _in_scope(name,queried_root)
            if not scoped and count>SHARED_CERTIFICATE_ROOT_THRESHOLD:
                proof['signal']='shared_hosting_certificate'
                proof['strength']=min(proof.get('strength',10),10) if isinstance(proof.get('strength'),int) and not isinstance(proof.get('strength'),bool) else 10
                proof['excerpt']='Certificate includes this name alongside more than ten distinct domain roots; low-priority reference until independent ownership evidence exists.'
    return normalized


def collect(source,seed,*,config,spend,context):
    if source not in SOURCES:raise ValueError('Unknown passive discovery source')
    if not isinstance(seed,dict) or seed.get('kind') not in {'domain','company','identifier'} or not isinstance(seed.get('value'),str):raise ValueError('Invalid passive discovery seed')
    if not isinstance(config,dict) or not isinstance(context,dict) or not callable(spend):raise ValueError('Invalid passive discovery context')
    result={'candidates':[],'leads':[],'notes':[],'cacheable':True,'_limit':1 if context.get('demo') else None,'_demo':bool(context.get('demo'))}
    if source=='gleif' and seed['kind']!='company' or source!='gleif' and seed['kind']!='domain':
        result.pop('_limit');result.pop('_demo');return result
    if source!='gleif':_,root=_root(seed['value'])
    try:
        if source=='crtsh':_crtsh(seed,root,config,spend,context,result)
        elif source=='certspotter':_certspotter(seed,root,config,spend,context,result)
        elif source=='hackertarget':_hackertarget(seed,root,config,spend,context,result)
        elif source=='urlscan':_urlscan(seed,root,config,spend,context,result)
        else:_gleif(seed,config,spend,context,result)
    except CollectionBlocked as exc:
        result['cacheable']=False;result['blocked']=exc.reason
        _note(result,'Collection '+exc.reason+' reached; the current source cursor was retained for resume')
        result.setdefault('continuation',copy.deepcopy(context.get('cursor') or {}))
    except LookupError as exc:
        result['cacheable']=False;result['failed']=True;result.pop('continuation',None);_note(result,source+': '+str(exc))
    except (ValueError,TypeError,KeyError,AttributeError,OverflowError):
        result['cacheable']=False;result['failed']=True;result.pop('continuation',None);_note(result,source+': provider response could not be safely parsed')
    # Only configured credentials are masked; no remote error body is retained.
    def clean(value):
        if isinstance(value,str):
            for key in ('CERTSPOTTER_API_KEY','URLSCAN_API_KEY'):
                secret=config.get(key)
                if isinstance(secret,str) and secret:value=value.replace(secret,'[REDACTED]')
            return value
        if isinstance(value,list):return [clean(item) for item in value]
        if isinstance(value,dict):return {key:clean(item) for key,item in value.items()}
        return value
    result.pop('_limit');result.pop('_demo');result.pop('_after',None);result.pop('_index',None)
    return normalize_result(clean(result))
