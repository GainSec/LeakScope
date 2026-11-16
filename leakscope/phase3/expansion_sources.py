"""Bounded domain leads with provenance; no automatic ownership or capture.

Every remote API request is reserved through the caller's durable spend function.
DNS reservations count logical record lookups, not resolver transport retries.
Sources return evidence for review; they never mutate organization scope.
"""
import copy
import ipaddress
import json
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin, urlsplit

import dns.exception
import dns.resolver
from django.db.models import Q, Subquery

from leakscope.intelligence.clients import _clean, _post, firecrawl_connection
from .asset_names import normalize_domain, registrable_domain
from . import free_registry
from .providers import _request
from .web_services import _public_url, HOST_SERVICES

MAX_CANDIDATES = None
MAX_PAGES = 20
MAX_TEXT = 48000
MAX_HTML = 96000
MAX_LOCAL_ROWS = None
SOURCES = {'saved', 'certificates', 'web', 'registration', 'dns', 'variants', 'netlas'}
IDENTIFIER = re.compile(r'\b(?:GTM-[A-Z0-9]{4,20}|G-[A-Z0-9]{6,20}|UA-\d{3,15}-\d{1,5}|AW-\d{5,15})\b')
DOMAIN_TEXT = re.compile(r'https?://[^\s<>"\]\[()]+', re.I)
BARE_DOMAIN = re.compile(r'(?<![\w@:/.-])(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}(?![\w-]|\.[a-z0-9])', re.I)
REDACTED = re.compile(r'redact|privacy|not disclosed|data protected|proxy', re.I)
EXTERNAL_SERVICES = tuple(item[0] for item in HOST_SERVICES) + ('facebook.com','linkedin.com','twitter.com','x.com','instagram.com','github.com','githubusercontent.com','t.co','google.com','microsoft.com','apple.com','w3.org','schema.org')


def _now():
    return datetime.now(timezone.utc).isoformat()


def _result():
    return {'candidates': [], 'leads': [], 'notes': []}


def _note(result, message):
    if message not in result['notes']:
        result['notes'].append(message)


def _rows(value, maximum=MAX_CANDIDATES):
    return value[:maximum] if isinstance(value, list) else []


def _string(value, maximum=MAX_TEXT):
    return value[:maximum] if isinstance(value, str) else ''


def _deadline(context):
    value=context.get('deadline')
    return value if isinstance(value,(float,int)) and not isinstance(value,bool) else time.monotonic()+30


def _reserve(spend, bucket, context, result):
    if time.monotonic() >= _deadline(context):
        result['blocked']='deadline'
        _note(result, 'Collection deadline reached; no further requests were made.')
        return False
    if not spend(bucket):
        result['blocked']='budget'
        _note(result, bucket+' request budget exhausted or collection stopped.')
        return False
    return True


def _domain(value):
    try:
        value=normalize_domain(value, allow_wildcard=True)
        if '.' not in value or value.endswith(('.local','.localhost','.internal','.invalid','.test')):
            return None
        registrable_domain(value)
        return value
    except (ValueError,TypeError):
        return None


def _same_root(value, other):
    try: return registrable_domain(value)==registrable_domain(other)
    except (ValueError,TypeError): return False


def _first_party(value, context):
    return any(_same_root(value, seed) for seed in context.get('seed_domains', []) if isinstance(seed, str))


def _service(value):
    return any(value==suffix or value.endswith('.'+suffix) for suffix in EXTERNAL_SERVICES)


def _proof(source, signal, via, *, url='', excerpt='', strength=25, observed_at=None):
    return {'source':source,'signal':signal,'via':str(via)[:500],
            'url':_public_url(url) or '', 'excerpt':str(excerpt)[:1600],
            'strength':max(0,min(100,int(strength))), 'observed_at':observed_at or _now()}


def _add(result, domain, proof, relation='related', identifiers=None, dns_status=None, *, index=None):
    domain=_domain(domain)
    if not domain: return
    candidate=index.get(domain) if index is not None else next((item for item in result['candidates'] if item['domain']==domain),None)
    if not candidate:
        candidate={'domain':domain,'relation':relation,'evidence':[],'identifiers':[]}
        result['candidates'].append(candidate)
        if index is not None:index[domain]=candidate
    if proof not in candidate['evidence'] and len(candidate['evidence'])<12:
        candidate['evidence'].append(proof)
    if candidate['relation']=='related' and relation!='related': candidate['relation']=relation
    for identifier in identifiers or []:
        if identifier not in candidate['identifiers'] and len(candidate['identifiers'])<12:
            candidate['identifiers'].append(identifier)
    if dns_status: candidate['dns_status']=dns_status


def _lead(result,kind,value,*,evidence=None,relation='lead'):
    value=' '.join(_string(value,200).split())
    if not value or len(value)<3: return
    if kind=='identifier' and not IDENTIFIER.fullmatch(value): return
    if kind=='company' and ('[REDACTED]' in value or re.search(r'https?://|[<>\x00-\x1f]',value)):return
    lead={'kind':kind,'value':value}
    if evidence:lead.update(evidence=[evidence],relation=relation)
    if lead not in result['leads']:result['leads'].append(lead)


def _certificates(seed,context,result):
    if seed['kind']!='domain':return
    from uuid import UUID
    from leakscope_app.models import CertificateRecord,CertificateName
    root=registrable_domain(seed['value'])
    names=CertificateName.objects.filter(Q(name=root)|Q(name__endswith='.'+root))
    rows=CertificateRecord.objects.filter(pk__in=Subquery(names.values('certificate_id'))).order_by('pk')
    cursor=context.get('cursor') or {}
    after=cursor.get('after')
    if after:rows=rows.filter(pk__gt=UUID(after))
    size=1 if context.get('demo') else 250
    records=list(rows[:size+1]);last=after;index={}
    for record in records[:size]:
        if time.monotonic()>=_deadline(context):
            result['blocked']='deadline'
            result['continuation']={'after':last} if last else {'phase':'certificates'}
            return
        # A page bounds records, never SAN names within one retained record.
        for name in _rows(record.domains):
            relation='subdomain' if _first_party(name,context) else 'related'
            proof=_proof('local_certstream','certificate_san','certificate:'+str(record.pk),
                excerpt='Certificate also names '+str(name)+' alongside the seed domain family; co-occurrence is not ownership.',
                strength=30 if relation=='subdomain' else 35,
                observed_at=(record.observed_at or record.last_seen).isoformat())
            proof['certificate_id']=str(record.pk)
            for field in ('not_before','not_after','first_seen','last_seen'):
                value=getattr(record,field,None)
                if value is not None:proof[field]=value.isoformat()
            _add(result,name,proof,relation,index=index)
        last=str(record.pk)
    if len(records)>size:result['continuation']={'after':last}


def _saved(seed,context,result):
    from uuid import UUID
    from django.db.models import Exists,OuterRef
    from leakscope_app.models import AssetNode,AssetRelationship
    from .relationships import visible_evidence,_visible_nodes
    visible=visible_evidence()
    independent=visible.exclude(source__in=['domain_expansion','domain_expansion_review',
        'domain_review_audit','organization_lifecycle','user_scope']).exclude(run__kind='domain_expansion')
    edges=AssetRelationship.objects.filter(evidence_id__in=Subquery(independent.values('pk'))).exclude(
        relation__in=['domain_candidate','user_scope']).select_related('source','target','evidence')
    supported=(Q(pk__in=Subquery(independent.values('subject_id'))) |
        Q(pk__in=Subquery(edges.values('source_id'))) | Q(pk__in=Subquery(edges.values('target_id'))))
    if seed['kind']=='domain':
        root=registrable_domain(seed['value'])
        nodes=_visible_nodes().filter(kind='domain').filter(Q(value=root)|Q(value__endswith='.'+root)).filter(supported)
    elif seed['kind']=='company':
        nodes=AssetNode.objects.filter(kind='organization',value__iexact=seed['value'],organization_status='active')
    else:nodes=AssetNode.objects.none()
    node_ids=Subquery(nodes.order_by().values('pk'))
    adjacent=edges.filter(Q(source_id__in=node_ids)|Q(target_id__in=node_ids)).annotate(
        source_known=Exists(nodes.filter(pk=OuterRef('source_id'))))
    # Resolve graph neighborhoods in SQL, without retaining every node or edge in Python.
    pivots=AssetNode.objects.filter(kind__in=['ip','service','certificate']).filter(
        Q(pk__in=Subquery(edges.filter(source_id__in=node_ids).order_by().values('target_id'))) |
        Q(pk__in=Subquery(edges.filter(target_id__in=node_ids).order_by().values('source_id'))))
    pivot_ids=Subquery(pivots.order_by().values('pk'))
    state=copy.deepcopy(context.get('cursor') or {})
    phase=state.get('phase',0);after=state.get('after')
    if not isinstance(phase,int) or isinstance(phase,bool) or not 0<=phase<=5:raise ValueError('Invalid local evidence cursor')
    matching=set(value for value in state.get('identifiers',[]) if isinstance(value,str) and IDENTIFIER.fullmatch(value))
    emitted=set();index={};remaining=1 if context.get('demo') else 250

    def fact(name,proof,signal,strength=25,relation=None,prefix='Saved source record'):
        identity=(name,str(proof.pk))
        if identity in emitted:return
        emitted.add(identity)
        relation=relation or ('subdomain' if _first_party(name,context) else 'related')
        excerpt=prefix+': '+json.dumps(proof.data,sort_keys=True,ensure_ascii=False,default=str)[:1000]
        original=_proof(proof.source,signal,'evidence:'+str(proof.pk),url=proof.source_url,
            excerpt=excerpt,strength=strength,observed_at=proof.observed_at.isoformat())
        original['evidence_id']=str(proof.pk)
        _add(result,name,original,relation,index=index)

    def continuation():
        result['continuation']={'phase':phase,'after':after,'identifiers':sorted(matching)}

    while phase<6:
        if phase==0:query=independent.filter(subject_id__in=node_ids,subject__kind='domain').select_related('subject')
        elif phase==1:
            query=AssetRelationship.objects.filter(source_id__in=node_ids,target__kind='domain',relation='user_scope',
                evidence_id__in=Subquery(visible.values('pk'))).select_related('target','evidence') if seed['kind']=='company' else edges.none()
        elif phase==2:query=adjacent
        elif phase==3:query=edges.filter(Q(source_id__in=pivot_ids,target__kind='domain')|Q(target_id__in=pivot_ids,source__kind='domain'))
        elif phase==4:
            query=independent.filter(data__icontains=seed['value']) if seed['kind']=='identifier' else independent.filter(Q(subject_id__in=node_ids)|Q(subject_id__in=pivot_ids))
        else:
            if not matching:break
            query=independent.filter(subject__kind='domain').select_related('subject')
        if after:query=query.filter(pk__gt=UUID(after))
        records=list(query.order_by('pk')[:remaining+1])
        for item in records[:remaining]:
            if time.monotonic()>=_deadline(context):
                result['blocked']='deadline';continuation();return
            if phase==0:fact(item.subject.value,item,'domain_structure')
            elif phase==1:fact(item.target.value,item.evidence,'user_scope',0,prefix='User supplied scope; ownership unverified')
            elif phase==2:
                other=item.target if item.source_known else item.source
                if other.kind=='domain':
                    dependency=item.relation in {'cname_to','nameserver','mail_exchange','service_record','web_service_candidate'}
                    relation='subdomain' if _first_party(other.value,context) else 'service_provider' if dependency else 'related'
                    fact(other.value,item.evidence,'saved_relationship',min(item.confidence,40),relation,prefix='Saved '+item.relation+' record')
            elif phase==3:
                other=item.target if item.target.kind=='domain' else item.source
                fact(other.value,item.evidence,'shared_infrastructure',15,
                    prefix='Original record on a shared IP, service or certificate; shared infrastructure is not ownership')
            else:
                text=json.dumps(item.data,ensure_ascii=False,default=str)[:MAX_HTML]
                identifiers=set(IDENTIFIER.findall(text))
                if phase==4:
                    if seed['kind']!='identifier' or seed['value'] in identifiers:
                        for identifier in sorted(identifiers):_lead(result,'identifier',identifier)
                        matching.update(identifiers)
                else:
                    for identifier in sorted(matching.intersection(identifiers)):
                        fact(item.subject.value,item,'shared_identifier',30,
                            prefix='Original saved record includes '+identifier+'; shared identifiers are correlation leads')
                        candidate=index.get(item.subject.value)
                        if candidate and identifier not in candidate['identifiers']:candidate['identifiers'].append(identifier)
            after=str(item.pk)
            remaining-=1
        if len(records)>0 and remaining==0:
            continuation();return
        phase+=1;after=None


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links=[];self.jsonld=[];self._json=False;self._parts=[]

    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='script' and attrs.get('type','').lower()=='application/ld+json':
            self._json=True;self._parts=[]
        if tag in {'a','link','script','img','iframe'} and len(self.links)<100:
            value=attrs.get('href') if tag in {'a','link'} else attrs.get('src')
            if value:
                relation=(attrs.get('rel') or '').lower().split()
                kind='canonical' if tag=='link' and 'canonical' in relation else 'alternate' if tag=='link' and 'alternate' in relation else tag
                self.links.append((value,kind))

    def handle_data(self,data):
        if self._json and sum(map(len,self._parts))<MAX_TEXT:self._parts.append(data)

    def handle_endtag(self,tag):
        if tag=='script' and self._json:
            self._json=False
            try:self.jsonld.append(json.loads(''.join(self._parts)))
            except (ValueError,RecursionError):pass


def _json_entities(value,result,base,limit=0,context=None):
    context=context or {}
    if limit>6:return
    if isinstance(value,list):
        for item in value[:20]:_json_entities(item,result,base,limit+1,context)
    elif isinstance(value,dict):
        kind=value.get('@type','')
        if kind=='Organization' or (isinstance(kind,list) and 'Organization' in kind):
            for field in ('legalName','name'):_lead(result,'company',value.get(field))
            for field in ('url','sameAs'):
                values=value.get(field,[])
                if isinstance(values,str):values=[values]
                for url in _rows(values,10):
                    safe=_public_url(urljoin(base,url)) if isinstance(url,str) else None
                    if safe:
                        host=urlsplit(safe).hostname
                        _add(result,host,_proof('firecrawl','official_statement',base,url=base,excerpt='Organization structured metadata references '+safe+'; publisher assertion requires corroboration.',strength=50),'subdomain' if _first_party(host,context) else 'service_provider' if _service(host) else 'related')
        for field in ('@graph','parentOrganization','subOrganization','brand'):
            _json_entities(value.get(field),result,base,limit+1,context)


def _statement(text,seed,context):
    anchors=[seed['value'],*context.get('company_names',[]),*context.get('seed_domains',[])]
    for sentence in re.split(r'[\n!?]|\.(?=\s|$)',text[:MAX_TEXT]):
        if not any(isinstance(anchor,str) and len(anchor)>2 and anchor.casefold() in sentence.casefold() for anchor in anchors):continue
        if re.search(r'\b(?:acquired|acquisition|formerly|merged\s+with)\b',sentence,re.I):return 'acquired_brand','acquisition_statement',65,sentence
        if re.search(r'\b(?:subsidiary\s+of|owned\s+by|operated\s+by|operates?|part\s+of|legal\s+name)\b',sentence,re.I):return 'subsidiary','legal_statement',65,sentence
        if re.search(r'\b(?:our\s+(?:(?:other|additional)\s+)?(?:brands?|companies|websites?)|official\s+website)\b',sentence,re.I):return 'related','official_statement',55,sentence
    return 'related','web_search_result',25,''


def _parse_page(row,seed,context,result,config,*,base=None,scraped=False):
    if not isinstance(row,dict):return
    metadata=row.get('metadata') if isinstance(row.get('metadata'),dict) else {}
    status=metadata.get('statusCode')
    if metadata.get('error') or (isinstance(status,int) and status>=400):
        _note(result,'A returned web page was inaccessible; its error content was not used as relationship evidence.')
        return
    if row.get('warning'):
        _note(result,'A returned web page reported limited or incomplete scraping.')
    url=_public_url(row.get('url') or metadata.get('sourceURL') or metadata.get('url') or base)
    if not url:return
    host=urlsplit(url).hostname
    markdown=_clean(_string(row.get('markdown') or row.get('content')),config)
    description=_clean(_string(row.get('description'),2000),config)
    title=_clean(_string(row.get('title') or metadata.get('title'),300),config)
    html=_clean(_string(row.get('rawHtml') or row.get('html'),MAX_HTML),config)
    text=title+'\n'+description+'\n'+markdown
    identifiers=list(dict.fromkeys(IDENTIFIER.findall(html+'\n'+text)))[:12]
    if seed['kind']=='identifier' and seed['value'] not in identifiers:
        return
    page=_Page()
    try:page.feed(html)
    except (ValueError,RecursionError):pass
    statement_relation,statement_signal,statement_strength,statement=_statement(text,seed,context)
    relation,signal,strength=statement_relation,statement_signal,statement_strength
    stem=registrable_domain(host).split('.')[0].replace('-','')
    # Merely naming the publisher in a reporting sentence cannot attribute the
    # acquisition to that publisher. Bind an external site's identity only when
    # it is the exact subject of an explicit ownership declaration.
    subject=re.match(r'^\s*(?:the\s+)?(.{1,160}?)\s+(?:is\s+(?:an?\s+)?subsidiary\s+of|is\s+owned\s+by|is\s+operated\s+by|has\s+been\s+acquired\s+by)\b',statement,re.I)
    source_named=bool(subject and re.sub(r'[^a-z0-9]','',subject.group(1).casefold()) in {stem,re.sub(r'[^a-z0-9]','',host.casefold())})
    if seed['kind']=='identifier':relation,signal,strength='related','shared_identifier',30
    elif _first_party(host,context):relation='subdomain'
    elif (statement and not source_named) or re.search(r'/(?:news|press|blog|article|story)(?:/|$)',urlsplit(url).path,re.I):
        relation,signal,strength='related','web_search_result',25
    if seed['kind']=='company' and not context.get('seed_domains'):
        normalize=lambda value: re.sub(r'[^a-z0-9]','',value.casefold())
        company_name=normalize(seed['value'])
        legal_base=re.sub(r'(?i)\b(?:incorporated|corporation|company|holdings|limited|group|corp|inc|llc|ltd)\b','',seed['value'])
        if company_name and company_name in normalize(title) and stem in {company_name,normalize(legal_base)}:
            relation,signal,strength='related','company_domain_candidate',30
    if _service(host) and not _first_party(host,context):relation,signal,strength='service_provider','service_dependency',10
    _add(result,host,_proof('firecrawl',signal,seed['value'],url=url,excerpt=statement or text[:1200],strength=strength),relation,identifiers)
    for identifier in identifiers:
        if _first_party(host,context):_lead(result,'identifier',identifier,evidence=_proof('firecrawl','shared_identifier',host,url=url,excerpt='Website includes '+identifier+'; copied identifiers are correlation leads.',strength=30))
    _acquisition_leads(result,text,url,seed,context)
    for entity in page.jsonld:_json_entities(entity,result,url,context=context)
    links=list(page.links)
    links.extend((link,'a') for link in DOMAIN_TEXT.findall(markdown)[:100])
    links.extend(('https://'+name,'a') for name in BARE_DOMAIN.findall(markdown)[:50]
                 if free_registry._PUBLIC_SUFFIXES(name).suffix)
    links.extend((link,'a') for link in _rows(row.get('links')) if isinstance(link,str))
    seen=set()
    for link,tag in links:
        safe=_public_url(urljoin(url,link.rstrip('.,;')))
        if not safe or safe in seen:continue
        seen.add(safe);linked=urlsplit(safe).hostname
        linked_relation='subdomain' if _first_party(linked,context) else 'related'
        linked_signal,linked_strength='web_link',25
        excerpt='Page links to '+safe+'. A link alone does not establish ownership.'
        if not _first_party(linked,context) and (tag not in {'a','canonical','alternate'} or _service(linked)):
            linked_relation,linked_signal,linked_strength='service_provider','service_dependency',10
        elif statement and linked in statement:
            linked_relation,linked_signal,linked_strength=statement_relation,statement_signal,statement_strength
            excerpt=statement
        _add(result,linked,_proof('firecrawl',linked_signal,host,url=url,excerpt=excerpt,strength=linked_strength),linked_relation)


def _acquisition_leads(result,text,url,seed,context):
    names=context.get('company_names',[])
    if seed['kind']=='company':names=[seed['value'],*names]
    for name in dict.fromkeys(names):
        if not isinstance(name,str) or len(name)<3:continue
        pattern=r'(?i:'+re.escape(name)+r'\s+(?:has\s+)?(?:acquired|acquires|acquisition\s+of)\s+)([A-Z][\w&\'-]*(?:\s+[A-Z][\w&\'-]*){0,5})'
        for match in re.finditer(pattern,text[:MAX_TEXT]):
            company=re.split(r'\s+(?:to|for|with|in|as|from)\b',match.group(1),flags=re.I)[0].strip()
            if company.casefold() in {n.casefold() for n in names}:continue
            _lead(result,'company',company,relation='acquired_brand',evidence=_proof('firecrawl','acquisition_statement',name,url=url,
                excerpt=text[max(0,match.start()-80):match.end()+150],strength=60))


def _safe_failure(exc):
    message=str(exc)
    status=re.fullmatch(r'(?:Firecrawl|Netlas) endpoint returned HTTP ([1-5][0-9]{2})',message)
    if status:return ' HTTP '+status.group(1)+'.'
    if message.endswith('response exceeded the 1 MiB limit'):return ' Response size exceeded the configured 1 MiB limit.'
    if message.endswith('request failed or timed out; no automatic retry was made'):return ' Request failed or timed out.'
    return ''


def _web(seed,config,spend,context,result):
    base,key=firecrawl_connection(config)
    timeout=max(1,min(25,_deadline(context)-time.monotonic()))
    stage=(context.get('cursor') or {}).get('stage','homepage')
    if seed['kind']=='domain' and not seed['value'].startswith('*.') and stage!='search':
        target=_public_url('https://'+seed['value'])
        if not target:return
        if not _reserve(spend,'web',context,result):
            result['continuation']={'stage':'homepage'};return
        try:
            response=_post(base+'/scrape',{'url':target,'formats':['markdown','rawHtml','links'],'onlyMainContent':False},key,timeout,'Firecrawl')
            if not isinstance(response,dict) or response.get('success') is False or not isinstance(response.get('data'),dict):raise ValueError('Firecrawl scrape did not succeed')
            _parse_page(response.get('data'),seed,context,result,config,base=target,scraped=True)
        except (ValueError,TypeError):
            _note(result,'The homepage scrape was unavailable; the separately budgeted search may still return domain leads.')
        if context.get('demo'):return
    query=seed['value'].removeprefix('*.')
    escaped=query.replace('\\','\\\\').replace('"','\\"')
    if seed['kind']=='company':query='"'+escaped+'" '+('official website' if not context.get('seed_domains') else '(acquired OR acquisition OR subsidiary OR "operated by" OR brands)')
    elif seed['kind']=='identifier':query='"'+escaped+'"'
    else:query='"'+escaped+'" (acquired OR subsidiary OR "operated by" OR "privacy policy")'
    for qualifier in ('country','industry'):
        if isinstance(context.get(qualifier),str) and context[qualifier]:query+=' '+context[qualifier][:120]
    if not _reserve(spend,'web',context,result):
        result['continuation']={'stage':'search'};return
    limit=1 if context.get('demo') else max(1,min(100,context.get('web_results',MAX_PAGES)))
    response=_post(base+'/search',{'query':_clean(query,config)[:500],'limit':limit},key,max(1,min(25,_deadline(context)-time.monotonic())),'Firecrawl')
    if not isinstance(response,dict) or response.get('success') is False:raise ValueError('Firecrawl search did not succeed')
    data=response.get('data',{})
    rows=data.get('web',[]) if isinstance(data,dict) else data
    if not isinstance(rows,list):raise ValueError('Firecrawl returned an invalid search result list')
    for row in rows[:1] if context.get('demo') else rows:_parse_page(row,seed,context,result,config)
    if not context.get('demo') and len(rows)>=limit:_note(result,'Firecrawl returned the requested search sample; this search endpoint has no pagination cursor. Increase web results per search or refine the seed for broader coverage.')


def _rdap_get(url,allowed,spend,context,result):
    original=free_registry._validated_url(url,allowed)
    with free_registry._cache_lock:cached=free_registry._cache.get(original)
    if cached and cached[0]>time.monotonic():return copy.deepcopy(cached[1]),cached[2],cached[3]
    for hop in range(3):
        if not _reserve(spend,'rdap',context,result):return None,None,None
        status,headers,body=free_registry._http_once(url,_deadline(context))
        if status in (301,302,303,307,308):
            if hop>=2:raise ValueError('Registry redirect limit exceeded')
            url=free_registry._validated_url(urljoin(url,headers.get('location','')),allowed)
            continue
        if status==404:return {},url,_now()
        if not 200<=status<300:raise ValueError('Registry returned HTTP '+str(status))
        if len(body)>free_registry.MAX_BYTES:raise ValueError('Registry response limit exceeded')
        try:payload=json.loads(body.decode('utf-8'))
        except (ValueError,UnicodeError,RecursionError):raise ValueError('Registry returned invalid JSON') from None
        if not isinstance(payload,dict):raise ValueError('Registry returned an invalid JSON object')
        observed=_now()
        with free_registry._cache_lock:
            if len(free_registry._cache)>=512:free_registry._cache.pop(next(iter(free_registry._cache)))
            free_registry._cache[original]=(time.monotonic()+(86400 if original==free_registry.BOOTSTRAP_URL else 3600),copy.deepcopy(payload),url,observed)
        return payload,url,observed
    raise ValueError('Registry redirect limit exceeded')


def _registration(seed,config,spend,context,result):
    if seed['kind']!='domain' or seed['value'].startswith('*.'):return
    extracted=free_registry._PUBLIC_SUFFIXES(seed['value'])
    if not extracted.suffix or not extracted.domain:raise ValueError('A registrable public domain is required')
    root=extracted.top_domain_under_public_suffix
    parent_registration=root!=registrable_domain(seed['value'])
    bootstrap,_,_=_rdap_get(free_registry.BOOTSTRAP_URL,{'data.iana.org'},spend,context,result)
    if bootstrap is None:return
    url,allowed=free_registry._domain_authority(root,bootstrap)
    data,source,observed=_rdap_get(url,allowed,spend,context,result)
    if data is None:return
    if not data:
        _note(result,'No public registration record was found for '+root+'.')
        return
    if normalize_domain(data.get('ldhName') or data.get('unicodeName'))!=root:raise ValueError('Registry domain did not match requested domain')
    if parent_registration:
        _note(result,'Public RDAP for tenant '+seed['value']+' returned parent hosting namespace '+root+' at '+source+' (observed '+observed+'). This identifies the parent provider registration, not tenant ownership; no company lead was added.')
        return
    matched=[]
    for entity in _rows(data.get('entities')):
        if not isinstance(entity,dict) or 'registrant' not in _rows(entity.get('roles')):continue
        holder,_=free_registry._identity(entity)
        holder=_clean(holder,config)
        if holder and not REDACTED.search(holder):
            _lead(result,'company',holder)
            if holder.casefold() in {name.casefold() for name in context.get('company_names',[]) if isinstance(name,str)}:matched.append(holder)
    excerpt=('Public registrant matches '+', '.join(matched) if matched else 'Public registration record for this candidate; registrant redaction or a different registrant does not establish organization ownership.')
    proof=_proof('rdap','registration_match' if matched else 'registration_record',seed['value'],url=source,
        excerpt=excerpt,strength=70 if matched else 25,observed_at=observed)
    _add(result,root,proof)


def _dns(seed,spend,context,result):
    if seed['kind']!='domain' or seed['value'].startswith('*.'):return
    resolver=dns.resolver.Resolver()
    resolver.retry_servfail=False
    start=(context.get('cursor') or {}).get('record',0)
    next_at=0
    for index,kind in enumerate(('CNAME','A','AAAA','MX','NS','TXT')):
        if index<start:continue
        delay=next_at-time.monotonic()
        if delay>0:time.sleep(delay)
        if not _reserve(spend,'dns',context,result):
            result['continuation']={'record':index};break
        next_at=time.monotonic()+1/max(1,min(100,context.get('dns_rate',20)))
        try:
            answer=resolver.resolve(seed['value']+'.',kind,lifetime=max(.1,min(2,_deadline(context)-time.monotonic())),search=False)
        except dns.resolver.NXDOMAIN:
            _add(result,seed['value'],_proof('dns','dns_'+kind.lower(),seed['value'],excerpt='DNS returned NXDOMAIN; ownership remains unverified.',strength=5),dns_status='nxdomain')
            break
        except dns.resolver.NoAnswer:continue
        except (dns.exception.DNSException,OSError):
            _note(result,'DNS '+kind+' lookup failed or timed out.')
            continue
        for item in answer:
            value=item.to_text()[:2000]
            if kind in {'A','AAAA'}:
                try:address=str(ipaddress.ip_address(value))
                except ValueError:continue
                proof=_proof('dns','dns_'+kind.lower(),seed['value'],excerpt=kind+' '+value+'; an address is not ownership proof.',strength=10)
                proof.update(addresses=[address],record_type=kind)
                _add(result,seed['value'],proof,dns_status='resolved')
                if not context.get('demo'):_lead(result,'ip',address,evidence=proof,relation='infrastructure')
                continue
            if kind in {'CNAME','NS'}:target=str(item.target).rstrip('.')
            elif kind=='MX':target=str(item.exchange).rstrip('.')
            else:
                # SPF includes and DMARC report destinations indicate service dependencies.
                text=value.replace('"','')
                targets=re.findall(r'(?:include:|redirect=|mailto:[^@\s;]+@)([A-Za-z0-9.-]+)',text)
                for target in targets:
                    _add(result,target,_proof('dns','dns_txt',seed['value'],excerpt='TXT service reference '+text,strength=10),'service_provider')
                continue
            relation='subdomain' if _first_party(target,context) else 'service_provider'
            _add(result,target,_proof('dns','dns_'+kind.lower(),seed['value'],excerpt=kind+' '+value+'; this is a DNS service relationship, not an ownership claim.',strength=20),relation)


def _variants(seed,result):
    if seed['kind']!='domain':return
    root=registrable_domain(seed['value']);label=root.split('.')[0]
    variants=[label+'.'+suffix for suffix in ('com','net','org','io','co','dev','app','ai','co.uk','com.au')]
    variants.extend(label[:i]+label[i+1:]+'.com' for i in range(min(len(label),6)))
    variants.extend(label[:i]+label[i+1]+label[i]+label[i+2:]+'.com' for i in range(min(max(len(label)-1,0),4)))
    variants.extend([label+'s.com',label+'-group.com',label+'group.com',label+'corp.com'])
    for name in list(dict.fromkeys(variants))[:24]:
        if name==root:continue
        _add(result,name,_proof('variants','name_variant',root,excerpt='Generated TLD/name variation. Registration, DNS existence, and organization relationship have not been checked.',strength=5),'lookalike',dns_status='unchecked')


def _netlas(seed,config,spend,context,result):
    if seed['kind']!='company':return
    key=config.get('NETLAS_API_KEY')
    if not isinstance(key,str) or not key.strip():
        _note(result,'Netlas API key is not configured; no paid request was made.')
        return
    if not _reserve(spend,'netlas',context,result):return
    value=seed['value'].replace('\\','\\\\').replace('"','\\"')
    query='registrant.organization:"'+value+'"'
    start=(context.get('cursor') or {}).get('start',0)
    if not isinstance(start,int) or isinstance(start,bool) or not 0<=start<=9980:raise ValueError('Invalid Netlas page offset')
    data=_request('netlas','/whois_domains/',config,params={'q':query,'start':start})
    if not isinstance(data,dict) or not isinstance(data.get('items'),list):raise ValueError('Netlas returned an invalid registration result list')
    source='https://app.netlas.io/api/whois_domains/?'+urlencode({'q':query,'start':start})
    for row in data['items'][:1] if context.get('demo') else data['items']:
        raw=row.get('data') if isinstance(row,dict) else None
        if not isinstance(raw,dict):continue
        holder=raw.get('registrant') if isinstance(raw.get('registrant'),dict) else {}
        organization=_clean(_string(holder.get('organization'),300),config)
        matched=organization.casefold()==seed['value'].casefold()
        _add(result,raw.get('domain'),_proof('netlas','registration_match' if matched else 'registration_record',seed['value'],url=source,
            excerpt='Indexed registrant organization: '+(organization or 'unavailable')+'. Registration-only candidate.',strength=70 if matched else 25))
    total=data.get('total')
    if isinstance(total,dict):total=total.get('value')
    more=bool(data['items']) and ((isinstance(total,int) and total>start+len(data['items'])) or (not isinstance(total,int) and len(data['items'])>=20))
    if more and not context.get('demo'):
        offset=start+len(data['items'])
        if offset<=9980:result['continuation']={'start':offset}
        else:_note(result,'Netlas search reached its upstream 10,000-result pagination ceiling; refine the company query for additional results.')


def collect(source,seed,*,config,spend,context):
    """Return bounded evidence and leads; callers own persistence and recursion."""
    if source not in SOURCES:raise ValueError('Unknown domain expansion source')
    if not isinstance(seed,dict) or seed.get('kind') not in {'domain','company','identifier'} or not isinstance(seed.get('value'),str):raise ValueError('Invalid domain expansion seed')
    if not isinstance(config,dict) or not isinstance(context,dict) or not callable(spend):raise ValueError('Invalid domain expansion collector context')
    result=_result()
    if seed['kind']=='domain':
        domain=_domain(seed['value'])
        if not domain:raise ValueError('Invalid public domain expansion seed')
        seed=dict(seed,value=domain)
    if time.monotonic()>=_deadline(context):
        result['blocked']='deadline';result['continuation']=copy.deepcopy(context.get('cursor') or {})
        _note(result,'Collection deadline reached; no further requests were made.')
        return result
    try:
        if source=='certificates':_certificates(seed,context,result)
        elif source=='saved':_saved(seed,context,result)
        elif source=='web':_web(seed,config,spend,context,result)
        elif source=='registration':_registration(seed,config,spend,context,result)
        elif source=='dns':_dns(seed,spend,context,result)
        elif source=='variants':_variants(seed,result)
        elif source=='netlas':_netlas(seed,config,spend,context,result)
    except (ValueError,TypeError,KeyError,AttributeError,OverflowError) as exc:
        # Network clients already use fixed safe errors. A generic source note
        # avoids persisting arbitrary remote exception text or credentials.
        result['failed']=True;result.pop('continuation',None)
        _note(result,source+' collection was incomplete or unavailable; no automatic retry was made.'+_safe_failure(exc))
    # Defense in depth for nested credentials embedded in returned page text.
    def clean(value):
        if isinstance(value,str):return _clean(value,config)
        if isinstance(value,list):return [clean(item) for item in value]
        if isinstance(value,dict):return {key:clean(item) for key,item in value.items()}
        return value
    return clean(result)
