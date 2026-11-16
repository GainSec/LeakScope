"""Explicit, bounded asset enrichment and locally retained certificate search."""
import math
from urllib.parse import urlsplit
from uuid import UUID

from django.db import transaction
from django.db.models import Q, Subquery
from django.http import JsonResponse
from django.utils.dateparse import parse_datetime, parse_date
from django.utils import timezone
from datetime import datetime, time

from leakscope.phase3.asset_names import normalize_domain, normalize_ip, registrable_domain
from leakscope.phase3.state import visible_endpoints
from leakscope.phase3.tags import tag_details
from leakscope.phase3.dns_enrichment import extract_names
from leakscope.phase3 import relationships as store
from .models import (CertificateRecord, CertificateName, CertificateWatchMatch, AssetNode,
                     AssetEvidence, AssetRelationship, WatchRule, CloudResource)
from leakscope.phase3 import organization_lifecycle as lifecycle
from leakscope.phase3.usage_policy import validate_demo
from .workbench import endpoint, _body, _model, _hidden, _finding
from .operations import _watch, _text, _integer


def _stamp(value):
    return value.isoformat() if value else None


def _uuid(value):
    return UUID(str(value))


def _asset(row):
    return {'id':str(row.pk), 'kind':row.kind, 'value':row.value, 'label':row.label,
            'attributes':row.attributes, 'status':row.organization_status, 'status_changed_at':_stamp(row.organization_status_changed_at), 'merged_into_id':str(row.merged_into_id) if row.merged_into_id else None, 'first_seen':_stamp(row.first_seen), 'last_seen':_stamp(row.last_seen)}


def _proof(row, include_data=False):
    value = {'id':str(row.pk), 'subject':str(row.subject_id), 'source':row.source,
             'source_url':row.source_url, 'observed_at':_stamp(row.observed_at),
             'expires_at':_stamp(row.expires_at), 'last_collected_at':_stamp(row.last_collected_at)}
    if include_data: value['data'] = row.data
    return value


def _certificate(row):
    return {'id':str(row.pk), 'key':row.key, 'fingerprint':row.fingerprint, 'serial':row.serial,
            'issuer':row.issuer, 'domains':row.domains, **tag_details('certificate',row.pk),
            **{name:_stamp(getattr(row,name)) for name in ('first_seen','last_seen','observed_at','not_before','not_after')},
            'watch_matches':[{'watch_id':item.watch_key, 'watch_name':item.watch_name,
                              'matched_names':item.matched_names, 'first_seen':_stamp(item.first_seen),
                              'last_seen':_stamp(item.last_seen)} for item in row.watch_matches.all()]}


@endpoint()
def certificates(request, certificate_id=None):
    rows = CertificateRecord.objects.prefetch_related('watch_matches')
    if certificate_id:
        row = rows.get(pk=_uuid(certificate_id))
        root = AssetNode.objects.filter(kind='certificate', value=row.key).first()
        return JsonResponse({'certificate':_certificate(row), 'data':row.data, 'node_id':str(root.pk) if root else None})
    query = request.GET.get('q','').strip()
    if len(query)>253: raise ValueError('Search is too long')
    mode = request.GET.get('mode','domain')
    if mode not in {'domain','keyword'}: raise ValueError('Unknown certificate search mode')
    if query:
        if mode == 'domain':
            query = normalize_domain(query,allow_wildcard=True).removeprefix('*.')
            names = CertificateName.objects.filter(Q(name=query)|Q(name__endswith='.'+query))
        else:
            names = CertificateName.objects.filter(name__icontains=query)
        rows = rows.filter(pk__in=names.values('certificate_id'))
    if request.GET.get('watch'):
        rows = rows.filter(pk__in=CertificateWatchMatch.objects.filter(watch_key=str(_uuid(request.GET['watch']))).values('certificate_id'))
    for field, lookup in [('since','last_seen__gte'),('until','last_seen__lte')]:
        if request.GET.get(field):
            raw = request.GET[field]
            try:
                day = parse_date(raw)
                value = datetime.combine(day,time.min if field=='since' else time.max) if day else parse_datetime(raw)
            except (ValueError,TypeError):
                raise ValueError('Invalid '+field) from None
            if value is None: raise ValueError('Invalid '+field)
            if timezone.is_naive(value): value = timezone.make_aware(value)
            rows = rows.filter(**{lookup:value})
    page = _integer(request.GET,'page',1,1,1_000_000)
    size = _integer(request.GET,'page_size',25,1,100)
    count = rows.count()
    from .services.table_sorting import parameters, ordered
    from django.db.models import F, Value, Count, Min, CharField
    from django.db.models.functions import Coalesce, NullIf
    from django.db.models.fields.json import KeyTextTransform
    sort, direction = parameters(request, {'subject','issuer','names','watches','first_seen','last_seen'}, 'last_seen')
    if sort == 'subject':
        expression = Coalesce(NullIf(F('fingerprint'), Value('')), F('key'))
    elif sort == 'names':
        rows = rows.annotate(_first_name=Min('names__name'))
        expression = F('_first_name')
    elif sort == 'watches':
        rows = rows.annotate(_watch_count=Count('watch_matches', distinct=True))
        expression = F('_watch_count')
    elif sort == 'issuer':
        expression = Coalesce(NullIf(KeyTextTransform('O','issuer'), Value('')),
            NullIf(KeyTextTransform('CN','issuer'), Value('')), output_field=CharField())
    else:
        expression = F(sort)
    rows = ordered(rows, expression, direction, textual=sort in {'subject','names','issuer'})
    return JsonResponse({'results':[_certificate(row) for row in rows[(page-1)*size:page*size]],
        'count':count,'page':page,'pages':max(1,math.ceil(count/size)),
        'source':'local_certstream','external_sources_enabled':False,
        'stats':{'certificates':CertificateRecord.objects.count(),'names':CertificateName.objects.count(),
                 'matches':CertificateWatchMatch.objects.count()},
        'watches':[{'id':str(row.pk),'name':row.name} for row in WatchRule.objects.order_by('name')[:200]]})


def _list(data, name, maximum=30):
    values = data.get(name,[])
    if not isinstance(values,list) or len(values)>maximum or any(not isinstance(x,str) or len(x)>253 for x in values):
        raise ValueError('Invalid '+name)
    return list(dict.fromkeys(x.strip() for x in values if x.strip()))


def _scope_domain(value):
    value=normalize_domain(value,allow_wildcard=True)
    registrable_domain(value)
    return value


def _organization_detail(root):
    domains={};members={}
    for edge in root.outgoing.filter(relation='user_scope',evidence_id__in=Subquery(store.visible_evidence().values('pk'))).select_related('target','evidence').order_by('target__value','first_seen'):
        target=edge.target
        if target.kind=='domain':
            domains.setdefault(target.value,{'name':target.value,'node_id':str(target.pk),'source':'user_scope'})
        else:
            members.setdefault(str(target.pk),dict(_asset(target),reference=edge.evidence.data.get('reference'),url=edge.evidence.source_url))
    for edge in root.outgoing.filter(relation__in=['uses_service','references_domain','tracks_lookalike'],target__kind='domain',evidence_id__in=Subquery(store.visible_evidence().values('pk'))).select_related('target','evidence'):
        members.setdefault(str(edge.target_id),dict(_asset(edge.target),label=edge.target.value+' · '+edge.relation.replace('_',' '),relationship=edge.relation,url=edge.evidence.source_url))
    from .investigation import organization_overview
    return {'organization':_asset(root),'domains':list(domains.values()),'members':list(members.values()),
            'overview':organization_overview(root)}


def _link_scope(root,target,reference=None,origin='manual',name_sources=None):
    if AssetRelationship.objects.filter(source=root,target=target,relation='user_scope',evidence_id__in=Subquery(store.visible_evidence().values('pk'))).exists(): return False
    data={'kind':target.kind,'value':target.value,'assertion':'User supplied scope; ownership unverified','origin':origin}
    url=''
    if reference:
        data['reference']=reference
        if name_sources: data['name_sources']=name_sources
        kind,key=reference['type'],reference['id']
        if kind=='endpoint' or (target.kind=='service' and target.attributes.get('endpoint_id')):
            key=key if kind=='endpoint' else target.attributes['endpoint_id']
            endpoint=visible_endpoints().get(pk=key)
            data['endpoint_pk']=endpoint.pk.hex
            actual_source=next((item.get('observation_id') for item in name_sources or [] if item.get('observation_id')),None)
            if not actual_source:
                for item in name_sources or []:
                    if item.get('evidence_id'):
                        saved_proof=store.visible_evidence().filter(pk=_uuid(item['evidence_id'])).first()
                        if saved_proof and saved_proof.data.get('observation_pk'):
                            actual_source=saved_proof.data['observation_pk']
                            data['source_evidence_id']=str(saved_proof.pk)
                            break
            if actual_source: data['observation_pk']=_uuid(actual_source).hex
            url='/assets/?endpoint='+str(key)
        elif kind=='certificate': url='/certificates/?id='+str(key)
        elif kind=='cloud': url='/cloud/'
        elif kind=='asset' and target.kind!='finding': url='/relationships/?root='+str(key)
        else:
            underlying=target.attributes.get('reference') or reference
            data['finding_key']=str(underlying['type'])+':'+str(underlying['id'])
            url=f"/findings/{underlying['type']}/{underlying['id']}/"
    proof=store.evidence(root,'user_scope',data,source_url=url)
    store.relation(root,target,'user_scope',proof,confidence=100)
    return True


def _reference(data):
    if not isinstance(data,dict) or not isinstance(data.get('type'),str) or isinstance(data.get('id'),bool):
        raise ValueError('Invalid result reference')
    from leakscope.services.registry import canonical_type
    kind=data['type'] if data['type'] in {'certificate','cloud','asset'} else canonical_type(data['type'])
    key=str(_uuid(data.get('id'))) if kind in {'endpoint','certificate','cloud','asset'} else int(data.get('id'))
    if isinstance(key,int) and key<1: raise ValueError('Invalid result ID')
    return {'type':kind,'id':key}


def _reference_node(reference):
    kind,key=reference['type'],reference['id']
    if kind=='endpoint': return store._service(visible_endpoints().get(pk=key))
    if kind=='certificate': return store.ingest_certificate_graph(CertificateRecord.objects.get(pk=key))
    if kind=='cloud':
        cloud=CloudResource.objects.get(pk=key)
        return store.node('finding','cloud:'+str(key),{'reference':reference,'cloud_resource_id':str(key)},label=cloud.name)
    if kind=='asset': return store._visible_nodes().exclude(kind='organization').get(pk=key)
    row=_model(kind).objects.get(pk=key)
    if _hidden(row,kind):
        from django.core.exceptions import ObjectDoesNotExist
        raise ObjectDoesNotExist('Finding is hidden')
    finding=_finding(row,kind)
    return store.node('finding',f'{kind}:{key}',{'reference':reference},label=finding.get('label') or f'{kind} finding #{key}')


@endpoint()
def organization_detail(request,organization_id):
    original=lifecycle.organization(organization_id)
    root=lifecycle.canonical(original)
    if root.organization_status=='deleted':
        from django.core.exceptions import ObjectDoesNotExist
        raise ObjectDoesNotExist('Organization was deleted')
    result=_organization_detail(root)
    if original.pk!=root.pk: result['redirected_from']=str(original.pk)
    return JsonResponse(result)


@endpoint(('POST',))
@transaction.atomic
def add_organization_domains(request,organization_id):
    root=lifecycle.organization(organization_id,active=True,lock=True)
    data=_body(request)
    domains=list(dict.fromkeys(_scope_domain(value) for value in _list(data,'domains')))
    reference=_reference(data['reference']) if 'reference' in data else None
    target=normalize_ip(data['target']) if data.get('target') else None
    if target and reference: raise ValueError('Choose a result or a literal IP, not both')
    if not domains and not reference and not target: raise ValueError('Enter at least one domain or choose a result')
    if reference:
        context=_context({'reference':reference})
        allowed={item['name'] for item in context['names']}
        if any(value not in allowed for value in domains): raise ValueError('Select names present in the saved result; add other domains manually from the organization')
    added=[];already=[]
    result={}
    if reference:
        member=_reference_node(reference)
        result={'linked_result':_asset(member),'result_already_linked':not _link_scope(root,member,reference,'result_selection')}
    elif target:
        member=store.node('ip',target)
        result={'linked_result':_asset(member),'result_already_linked':not _link_scope(root,member)}
    for value in domains:
        target=store.node('domain',value)
        name_sources=[item for item in context['names'] if item['name']==value] if reference else None
        (added if _link_scope(root,target,reference,'result_selection' if reference else 'manual',name_sources) else already).append(value)
    return JsonResponse(dict(_organization_detail(root),added=added,already_linked=already,**result))


@endpoint(('GET','POST'))
def organizations(request):
    if request.method == 'GET':
        status=request.GET.get('status','active')
        if status not in {'active','archived','all'}: raise ValueError('Choose Active, Archived or All organizations')
        states=['active','archived'] if status=='all' else [status]
        rows=AssetNode.objects.filter(kind='organization',organization_status__in=states).order_by('value')[:300]
        return JsonResponse({'results':[_asset(row) for row in rows]})
    data=_body(request)
    name=_text(data,'name',required=True,maximum=200)
    aliases=_list(data,'aliases',8)
    from leakscope.phase3.asset_jobs import _spec
    validated=_spec('organization',name,aliases=aliases)
    name,aliases=validated['target'],validated['aliases']
    seeds={kind:_list(data,field) for kind,field in [('domain','domains'),('network','networks'),('asn','asns')]}
    seeds['domain']=[_scope_domain(value) for value in seeds['domain']]
    with transaction.atomic():
        root=store.node('organization',name,{'aliases':aliases})
        if root.organization_status!='active': raise ValueError('Organization is '+root.organization_status+'; restore an archive before adding scope')
        for kind,values in seeds.items():
            for value in values:
                target=store.node(kind,value)
                _link_scope(root,target)
    return JsonResponse({'organization':_asset(root)})


@endpoint(('POST',))
def discover_organization(request, organization_id):
    from leakscope.phase3.asset_jobs import queue_enrichment
    root=lifecycle.organization(organization_id,active=True)
    data=_body(request)
    budget=_integer(data,'max_requests',2,1,2)
    return JsonResponse(queue_enrichment('organization',root.value,organization_id=str(root.pk),
                        max_requests=budget,aliases=root.attributes.get('aliases',[]),include_netlas=data.get('include_netlas',False),**({'demo':True} if validate_demo(data.get('demo',False)) else {})))


@endpoint(('POST',))
def organization_services(request,organization_id):
    from leakscope.phase3.asset_jobs import queue_enrichment
    root=lifecycle.organization(organization_id,active=True)
    data=_body(request)
    return JsonResponse(queue_enrichment('services',root.value,organization_id=str(root.pk),**({'demo':True} if validate_demo(data.get('demo',False)) else {})))


@endpoint()
def organization_manage(request,organization_id):
    data=lifecycle.preview(organization_id,request.GET.get('target_id'))
    data['organization']=_asset(data['organization'])
    if data.get('target'): data['target']=_asset(data['target'])
    return JsonResponse({'preview':data})


@endpoint(('POST',))
def organization_action(request,organization_id,action):
    data=_body(request)
    if action=='merge':
        target,source,summary=lifecycle.merge(organization_id,data.get('target_id'),data.get('confirmation'))
        return JsonResponse({'organization':_asset(target),'merged_from':str(source.pk),'summary':summary})
    root,summary=lifecycle.set_status(organization_id,action,data.get('confirmation'))
    return JsonResponse({'organization':_asset(root),'summary':summary})


@endpoint()
def graph(request):
    root=request.GET.get('root')
    if root: root=str(_uuid(root))
    include_candidates=request.GET.get('include_candidates','true')
    if include_candidates not in {'true','false'}:
        raise ValueError('include_candidates must be true or false')
    return JsonResponse(store.graph(root_id=root,max_nodes=_integer(request.GET,'max_nodes',150,1,300),
        max_edges=_integer(request.GET,'max_edges',500,1,1000),max_depth=_integer(request.GET,'max_depth',3,0,5),
        include_candidates=include_candidates=='true'))


def _saved_dns_ips(target):
    """Read fresh addresses for this concrete query from visible saved DNS only."""
    if target.startswith('*.'): return []
    now=timezone.now(); addresses=[]; seen=set()
    rows=store.visible_evidence().filter(subject__kind='domain',subject__value=target,
        source__in=['dns','enrichment:dns'],expires_at__gt=now).order_by('-last_collected_at')[:40]
    for proof in rows:
        if not isinstance(proof.data,dict): continue
        result=proof.data if proof.source=='dns' else proof.data.get('result')
        if not isinstance(result,dict): continue
        if proof.source=='enrichment:dns' and result.get('target')!=target: continue
        records=[result] if proof.source=='dns' else result.get('records',[])
        if not isinstance(records,list): continue
        for record in records[:256]:
            if not isinstance(record,dict) or record.get('type') not in {'A','AAAA'}: continue
            try:
                owner=normalize_domain(record.get('query_name') or record.get('name'))
                address=normalize_ip(record.get('value'))
                expiry=parse_datetime(record.get('expires_at') or '')
                if expiry is not None and timezone.is_naive(expiry): expiry=timezone.make_aware(expiry)
            except (ValueError,TypeError): continue
            if (owner!=target or expiry is None or expiry<=now or address in seen or
                    (record['type']=='AAAA')!=(':' in address)): continue
            seen.add(address)
            addresses.append({'address':address,'source':proof.source,'evidence_id':str(proof.pk),
                'observed_at':_stamp(proof.observed_at),'expires_at':_stamp(min(expiry,proof.expires_at)),
                'query_name':owner,'answer_name':record.get('name')})
            if len(addresses)>=30: return addresses
    return addresses


def _context(data):
    names=[]; ips=[]; proofs=[]; target=''; ip_sources=[]
    asset_record = None
    reference=data.get('reference') or data
    if data.get('target'):
        target=data['target']
        try: target=normalize_ip(target)
        except ValueError: target=normalize_domain(target,allow_wildcard=True)
    elif reference.get('type') == 'asset':
        row=store._visible_nodes().exclude(kind='organization').get(pk=_uuid(reference.get('id')))
        asset_record = row
        target = row.value
        if row.kind=='domain': names=[{'name':row.value,'source':'saved_asset','wildcard':row.value.startswith('*.')}]
        proofs.append({'source':'saved_asset','node_id':str(row.pk)})
    elif reference.get('type') == 'certificate':
        row=CertificateRecord.objects.get(pk=_uuid(reference.get('id')))
        names=[{'name':name,'source':'certstream:certificate','wildcard':name.startswith('*.')} for name in row.domains]
        target=names[0]['name'] if names else ''
        proofs.append({'source':'certstream','certificate_id':str(row.pk),'observed_at':_stamp(row.observed_at)})
    elif reference.get('type') == 'cloud':
        row=CloudResource.objects.get(pk=_uuid(reference.get('id')))
        target=urlsplit(row.url).hostname or ''
        names=[{'name':target,'source':'cloud:'+row.provider,'wildcard':False}] if target else []
        proofs.append({'source':'cloud:'+row.provider,'cloud_resource_id':str(row.pk)})
    elif reference.get('type') == 'endpoint':
        row=visible_endpoints().get(pk=_uuid(reference.get('id')))
        target=row.address
        for observation in row.observations.filter(hidden=False).order_by('-last_collected_at')[:30]:
            raw=observation.data.get('metadata') or {}
            names.extend(dict(item,observation_id=str(observation.pk)) for item in extract_names(raw,observation.provider))
            proofs.append({'source':observation.provider,'observation_id':str(observation.pk),
                           'observed_at':_stamp(observation.observed_at),'collected_at':_stamp(observation.last_collected_at)})
            for value in [raw.get('resolved_ip'),raw.get('ip'),raw.get('ip_str')]:
                try: ip=normalize_ip(value)
                except (ValueError,TypeError): continue
                if ip not in ips: ips.append(ip)
    elif reference.get('type'):
        from leakscope.services.registry import canonical_type
        kind=canonical_type(reference['type'])
        row=_model(kind).objects.get(pk=int(reference.get('id')))
        if _hidden(row,kind):
            from django.core.exceptions import ObjectDoesNotExist
            raise ObjectDoesNotExist('Finding is hidden')
        finding=_finding(row,kind)
        target=finding.get('ip') or getattr(row,'ip','')
        if not target:
            try: target=urlsplit(finding.get('url') or '').hostname or ''
            except ValueError: target=''
        for key in ('metadata','data','raw_data'):
            raw=getattr(row,key,None)
            if isinstance(raw,dict): names.extend(extract_names(raw,kind))
        proofs.append({'source':'legacy:'+kind,'finding_id':row.pk})
    else:
        raise ValueError('Choose a result or a domain/IP target')
    if asset_record is not None and asset_record.kind not in {'domain', 'ip'}:
        kind = asset_record.kind
    else:
        try:
            target=normalize_ip(target)
            kind='ip'
            if target not in ips: ips.insert(0,target)
        except ValueError:
            try:
                target=normalize_domain(target,allow_wildcard=True)
                kind='domain'
                names.insert(0,{'name':target,'source':'target','wildcard':target.startswith('*.')})
            except ValueError:
                if not reference.get('type') or data.get('target'): raise
                target=f"{reference['type']} finding #{reference['id']}"
                kind='finding'
    if kind=='domain' and not target.startswith('*.'):
        ip_sources=_saved_dns_ips(target)
        for item in ip_sources:
            if item['address'] not in ips: ips.append(item['address'])
    unique=[]; seen=set()
    for name in names:
        marker=(name['name'],name['source'])
        if marker not in seen: seen.add(marker); unique.append(name)
    root=AssetNode.objects.filter(kind=kind,value=target).first()
    if root:
        saved=store.graph(root_id=str(root.pk),max_nodes=80,max_edges=200,max_depth=2)
        domain_nodes={item['id']:item for item in saved['nodes'] if item['kind']=='domain'}
        selected_services={item['id'] for item in saved['nodes'] if item['kind']=='service' and
            str(item.get('attributes',{}).get('endpoint_id'))==str(reference.get('id'))} if reference.get('type')=='endpoint' else None
        name_relations={'ptr_name','provider_name','certificate_name','indexed_dns_name','resolves_to','cname_to','observed_service'}
        for edge in saved['edges']:
            if edge['relation'] not in name_relations: continue
            if selected_services is not None and str(root.pk) not in {edge['source'],edge['target']} and not selected_services.intersection({edge['source'],edge['target']}): continue
            for node_id in (edge['source'],edge['target']):
                item=domain_nodes.get(node_id)
                if not item: continue
                marker=(item['value'],'saved:'+edge['evidence_source'])
                if marker not in seen:
                    seen.add(marker)
                    unique.append({'name':item['value'],'source':marker[1],
                                   'wildcard':item['value'].startswith('*.'),'relation':edge['relation'],'evidence_id':edge['evidence_id']})
        proof_ids={edge['evidence_id'] for edge in saved['edges']}
        relevant_ids=[str(root.pk),*domain_nodes]
        saved_proofs=store.visible_evidence().filter(Q(pk__in=proof_ids)|Q(subject_id__in=relevant_ids)).order_by('-last_collected_at')[:100]
        for item in saved_proofs:
            proof=_proof(item,True)
            if item.source.startswith('enrichment:') and isinstance(item.data,dict) and isinstance(item.data.get('result'),dict):
                proof['data']=item.data['result']
            proofs.append(proof)
    return {'target':target,'ips':ips[:30],'ip_sources':ip_sources,'names':unique[:256],'evidence':proofs,
            'node_id':str(root.pk) if root else None,
            'actions':{'resolve':bool(ips),'dns':not target.startswith('*.') and any(not item.get('wildcard') for item in unique),
                       'registration':kind=='domain' and not target.startswith('*.'),'netblock':bool(ips),'monitor':bool(unique),'services':kind in {'domain','ip'} and not target.startswith('*.')}}


@endpoint()
def context(request):
    return JsonResponse(_context(request.GET))


@endpoint(('POST',))
def enrich(request, kind):
    from leakscope.phase3.asset_jobs import queue_enrichment
    data=_body(request)
    source=_context(data)
    if kind in {'resolve','netblock'}:
        if not source['ips']: raise ValueError('Select an observed IP address')
        target=source['ips'][0]
    elif kind in {'dns','registration'}:
        target=normalize_domain(source['target'])
    elif kind=='services':
        target=source['target']
        try: target=normalize_ip(target)
        except ValueError: target=normalize_domain(target)
    else: raise ValueError('Unknown enrichment')
    demo=validate_demo(data.get('demo',False))
    return JsonResponse(queue_enrichment(kind,target,**({'demo':True} if demo else {})))


@endpoint()
def evidence(request,evidence_id=None):
    if evidence_id: return JsonResponse({'evidence':_proof(store.visible_evidence().get(pk=_uuid(evidence_id)),True)})
    rows=store.visible_evidence()
    if request.GET.get('subject'): rows=rows.filter(subject_id=_uuid(request.GET['subject']))
    return JsonResponse({'results':[_proof(row,True) for row in rows.order_by('-last_collected_at')[:100]]})


@endpoint(('POST',))
@transaction.atomic
def monitor(request):
    from leakscope.phase3.certstream import domain_matches
    data=_body(request)
    domain=normalize_domain(data.get('domain'),allow_wildcard=True)
    watch_id=data.get('watch_id')
    watches=WatchRule.objects.select_for_update()
    if watch_id:
        row=watches.get(pk=_uuid(watch_id))
        if any(domain_matches(domain,pattern) for pattern in row.domains):
            return JsonResponse({'watch':_watch(row),'already_covered':True})
        if row.auto_fetch and data.get('allow_auto_fetch') is not True:
            raise ValueError('This watch actively fetches targets. Confirm allow_auto_fetch before adding a new scope.')
        if len(row.domains)>=100: raise ValueError('Watch has too many scopes')
        row.domains=[*row.domains,domain]
        row.save(update_fields=['domains'])
    else:
        for row in watches.filter(enabled=True):
            if not row.auto_fetch and any(domain_matches(domain,pattern) for pattern in row.domains):
                return JsonResponse({'watch':_watch(row),'already_covered':True})
        row=WatchRule.objects.create(name=_text(data,'name','Monitor '+domain,maximum=200),
            domains=[domain],keywords=[],ports=[],actions=[],auto_fetch=False,enabled=True)
    return JsonResponse({'watch':_watch(row),'already_covered':False})
