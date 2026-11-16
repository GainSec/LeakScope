"""A conservative, evidence-backed asset graph over saved observations.

Collectors supply facts; this module normalizes their identities and preserves
provenance. Shared infrastructure and certificate names never imply ownership.
Graph reads only read these materialized records and never start collection.
"""
import hashlib
import ipaddress
import json
import re
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta
from urllib.parse import urlsplit, quote

from django.db import transaction
from django.core.exceptions import ValidationError
from django.db.models import Q, Subquery, CharField, Value
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast, Replace, Concat
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from leakscope_app.models_relationships import AssetNode, AssetEvidence, AssetRelationship
from leakscope_app.models import ServiceObservation, FindingReview
from .asset_names import normalize_domain, normalize_ip, registrable_domain


KINDS = {'organization', 'domain', 'ip', 'network', 'asn', 'service', 'certificate', 'finding', 'identifier'}
_VOLATILE = {'ttl', 'observed_at', 'expires_at', 'collected_at', 'first_collected_at',
             'last_collected_at', 'last_seen', 'first_seen', 'timestamp', 'time',
             'collection_timestamp', 'last_checked'}
_CONFIG = ContextVar('asset_graph_config', default=None)


@contextmanager
def using_config(config):
    token = _CONFIG.set(config)
    try:
        yield
    finally:
        _CONFIG.reset(token)


def _safe(value):
    # Keep the same configured-secret masking and size limits as observations.
    from .state import _json_safe
    return _json_safe(value, config=_CONFIG.get())


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def _stable(value):
    if isinstance(value, dict):
        return {key: _stable(item) for key, item in value.items() if key not in _VOLATILE}
    if isinstance(value, list):
        return [_stable(item) for item in value]
    return value


def _stamp(value, default=None):
    if isinstance(value, str):
        try:
            value = parse_datetime(value)
        except ValueError:
            value = None
    if not isinstance(value, datetime):
        return default
    return timezone.make_aware(value, timezone.get_default_timezone()) if timezone.is_naive(value) else value


@transaction.atomic
def node(kind, value, attributes=None, label=''):
    """Get a typed canonical identity; supplied attributes merge into its cache."""
    if kind not in KINDS:
        raise ValueError('Unsupported asset kind')
    if not isinstance(value, str) and not (kind == 'asn' and isinstance(value, int) and not isinstance(value, bool)):
        raise ValueError('Asset value must be a string')
    value = _safe(str(value)).strip()
    if not value:
        raise ValueError('Asset value is required')
    if kind == 'domain':
        value = normalize_domain(value, allow_wildcard=True)
    elif kind == 'ip':
        value = normalize_ip(value)
    elif kind == 'network':
        if '-' in value:
            start, end = [ipaddress.ip_address(part.strip()) for part in value.split('-', 1)]
            if start.version != end.version or int(start) > int(end):
                raise ValueError('Invalid network range')
            value = str(start) + '-' + str(end)
        else:
            value = str(ipaddress.ip_network(value, strict=False))
    elif kind == 'asn':
        value = re.sub(r'^AS', '', value, flags=re.I)
        if not value.isdecimal() or not 0 <= int(value) <= 4294967295:
            raise ValueError('Invalid ASN')
        value = str(int(value))
    elif kind == 'organization':
        value = ' '.join(value.split())
    if len(value) > 500:
        raise ValueError('Asset value is too long')
    now = timezone.now()
    key = _hash([kind, value.casefold() if kind == 'organization' else value])
    attrs = _safe(attributes or {})
    if not isinstance(attrs, dict):
        raise ValueError('Asset attributes must be an object')
    label = _safe(str(label))[:500]
    item, created = AssetNode.objects.get_or_create(key=key, defaults={
        'kind': kind, 'value': value, 'label': label or value[:500], 'attributes': attrs,
        'first_seen': now, 'last_seen': now})
    if not created:
        item = AssetNode.objects.select_for_update().get(pk=item.pk)
        redirected=False
        if kind=='organization':
            from .organization_lifecycle import canonical,aliases
            if item.organization_status=='merged':
                item=canonical(item,lock=True);redirected=True
            if 'aliases' in attrs: attrs['aliases']=aliases(item.attributes.get('aliases',[]),attrs['aliases'],excluding=item.value)
        item.attributes = dict(item.attributes, **attrs)
        item.last_seen = now
        if label and not redirected:
            item.label = label
        item.save(update_fields=['attributes', 'last_seen', 'label'])
    return item


def _hierarchy(item, proof):
    if item.kind != 'domain': return
    try: parent_value = registrable_domain(item.value.removeprefix('*.'))
    except ValueError: return
    if parent_value != item.value:
        relation(item,node('domain',parent_value),'subdomain_of',proof)


@transaction.atomic
def evidence(subject, source, data, source_url='', observed_at=None, expires_at=None, run=None):
    """Deduplicate facts while updating the latest observation and collection time.

    TTL and collection clocks are excluded from identity. A historical replay
    cannot replace the newer payload or shorten its validity period.
    """
    data = _safe(data)
    source = _safe(str(source))[:48]
    source_url = _safe(str(source_url or ''))[:2000]
    if not source:
        raise ValueError('Evidence source is required')
    now = timezone.now()
    observed_at = _stamp(observed_at, now)
    expires_at = _stamp(expires_at)
    key = _hash([str(subject.pk), source, _stable(data)])
    proof, created = AssetEvidence.objects.get_or_create(key=key, defaults={
        'subject': subject, 'source': source, 'source_url': source_url, 'data': data,
        'observed_at': observed_at, 'expires_at': expires_at, 'run': run,
        'first_collected_at': now, 'last_collected_at': now})
    if not created:
        proof = AssetEvidence.objects.select_for_update().get(pk=proof.pk)
        fields = ['last_collected_at']
        proof.last_collected_at = now
        if not proof.observed_at or observed_at >= proof.observed_at:
            proof.data = data
            proof.observed_at = observed_at
            proof.expires_at = expires_at
            fields.extend(['data', 'observed_at', 'expires_at'])
            if source_url:
                proof.source_url = source_url
                fields.append('source_url')
            if run:
                proof.run = run
                fields.append('run')
        proof.save(update_fields=fields)
    _hierarchy(subject, proof)
    return proof


@transaction.atomic
def relation(source, target, kind, evidence, confidence=100):
    """Record one typed fact with an explicit source; no generic owner inference."""
    if not isinstance(kind, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,47}', kind):
        raise ValueError('Invalid relationship type')
    if kind in {'owns', 'owned_by'}:
        raise ValueError('Use an evidenced, specific relationship instead of ownership')
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 100:
        raise ValueError('Confidence must be between 0 and 100')
    now = timezone.now()
    edge, created = AssetRelationship.objects.get_or_create(source=source, target=target,
        relation=kind, evidence=evidence, defaults={'confidence': int(confidence), 'first_seen': now, 'last_seen': now})
    if not created:
        edge.last_seen = now
        edge.confidence = max(edge.confidence, int(confidence))
        edge.save(update_fields=['last_seen', 'confidence'])
    if kind != 'subdomain_of':
        _hierarchy(source,evidence)
        _hierarchy(target,evidence)
    return edge


def _target(value):
    try:
        return node('ip', value)
    except ValueError:
        return node('domain', value)


def _proof(subject, record, source, run, observed_at=None):
    return evidence(subject, record.get('source') or source, record,
        source_url=record.get('source_url') or '',
        observed_at=record.get('observed_at') or observed_at,
        expires_at=record.get('expires_at'), run=run)


def _dns_target(record):
    kind = str(record.get('type') or '').upper()
    value = record.get('value')
    if kind in {'A', 'AAAA'}:
        return node('ip', value), 'resolves_to'
    if kind in {'CNAME', 'NS', 'PTR', 'MX', 'SRV'}:
        if isinstance(value, dict):
            value = value.get('target') or value.get('exchange') or value.get('host')
        elif kind in {'MX', 'SRV'}:
            value = str(value).split()[-1]
        if not value or value == '.':
            return None, None
        return node('domain', value), {'CNAME': 'cname_to', 'NS': 'nameserver', 'PTR': 'ptr_name', 'MX': 'mail_exchange', 'SRV': 'service_record'}[kind]
    return None, None


def _dns_owner(value):
    """Policy and SRV owners retain their exact DNS name in the proof.

    Underscore labels are valid DNS records but not host identities. Attach such
    evidence to the concrete suffix after its last underscore label.
    """
    try:
        return _target(value)
    except ValueError:
        labels = str(value).rstrip('.').split('.')
        underscored = [index for index, label in enumerate(labels) if label.startswith('_')]
        if not underscored or not all(re.fullmatch(r'_[a-zA-Z0-9-]+', labels[index]) for index in underscored):
            raise
        return node('domain', '.'.join(labels[max(underscored) + 1:]))


def _name_relation(source):
    source = source.lower()
    if source in {'ptr', 'dns_ptr', 'dns:ptr', 'reverse_dns'}:
        return 'ptr_name'
    if 'cert' in source:
        return 'certificate_name'
    if 'indexed_dns' in source:
        return 'indexed_dns_name'
    return 'provider_name'


@transaction.atomic
def ingest_dns(result, run=None):
    root = _target(result['target'])
    for record in (result.get('records') or [])[:512]:
        if not isinstance(record, dict):
            continue
        try:
            owner = _dns_owner(record.get('name') or root.value)
            proof = _proof(owner, record, 'dns', run, result.get('observed_at'))
            target, kind = _dns_target(record)
            if target:
                relation(owner, target, kind, proof)
        except (ValueError, TypeError, IndexError):
            continue
    for service in (result.get('services') or [])[:128]:
        if not isinstance(service, dict) or not service.get('provider'):
            continue
        provider = node('organization', service['provider'], {'role': 'third_party_service'})
        kind = service.get('relation') or 'uses_service'
        if kind in {'owns', 'owned_by', 'registered_to', 'registrant_organization'}:
            kind = 'uses_service'
        supporting = [item for item in service.get('evidence', [])[:512] if isinstance(item, dict)]
        owners = {}
        for record in supporting:
            try:
                owner = _dns_owner(record.get('name') or root.value)
                owners[owner.pk] = owner
            except ValueError:
                continue
        if not owners:
            owners[root.pk] = root
        stamps = [_stamp(item.get('expires_at')) for item in supporting]
        expiries = [stamp for stamp in stamps if stamp is not None]
        for owner in owners.values():
            proof = evidence(owner, 'dns_service_signal', service,
                observed_at=result.get('observed_at'), expires_at=min(expiries) if expiries else None, run=run)
            relation(owner, provider, kind, proof, service.get('confidence', 70))
    return root


@transaction.atomic
def ingest_names(result, run=None):
    root = _target(result['target'])
    for item in (result.get('names') or [])[:512]:
        if not isinstance(item, dict) or not item.get('name'):
            continue
        try:
            name = node('domain', item['name'])
        except ValueError:
            continue
        source = str(item.get('source') or 'provider')
        label = _name_relation(source)
        proof = _proof(root, item, source, run, result.get('observed_at'))
        relation(root, name, label, proof, 100 if item.get('forward_confirmed') else 70)
    return root


@transaction.atomic
def ingest_services(result,run=None):
    root=node('organization',result['target']) if result.get('target_kind')=='organization' else _target(result['target'])
    for item in (result.get('services') or [])[:32]:
        if not isinstance(item,dict) or not isinstance(item.get('provider'),str): continue
        try:
            target=node('domain',item['domain']) if item.get('category')=='web_dependency' and item.get('domain') else node('organization',item['provider'],{'role':'third_party_service'})
            for citation in (item.get('evidence') or [])[:3]:
                if not isinstance(citation,dict): continue
                url=citation.get('source_url') or ''
                parts=urlsplit(url)
                if parts.scheme not in {'http','https'} or not parts.hostname or parts.username or parts.password: continue
                proof=evidence(root,'firecrawl',{'provider':item['provider'],'category':item.get('category'),
                    'relation':'web_service_candidate','citation':citation,'assertion':'Public web service candidate; current service use and ownership unverified'},
                    source_url=url,observed_at=result.get('observed_at'),expires_at=timezone.now()+timedelta(days=1),run=run)
                relation(root,target,'web_service_candidate',proof,min(60,max(0,int(item.get('confidence',35)))))
        except (ValueError,TypeError): continue
    return root


def _network(item):
    value = item.get('cidr') or item.get('network')
    if not value and item.get('start_address') and item.get('end_address'):
        value = str(item['start_address']) + '-' + str(item['end_address'])
    return node('network', value, {key: item[key] for key in ('name', 'country', 'start_address', 'end_address') if item.get(key)}) if value else None


def _registration(network, item, run, default_source, observed_at=None):
    proof = _proof(network, item, default_source, run, observed_at)
    holder = item.get('organization')
    if holder:
        relation(network, node('organization', holder), 'registered_to', proof, item.get('confidence', 80))
    return proof


@transaction.atomic
def ingest_netblock(result, run=None):
    root = node('ip', result['target'])
    for item in (result.get('networks') or [])[:128]:
        try:
            network = _network(item)
            if network:
                proof = _registration(network, item, run, 'rdap', result.get('observed_at'))
                relation(root, network, 'within_allocation', proof)
        except (ValueError, TypeError):
            continue
    for item in (result.get('asns') or [])[:128]:
        if not isinstance(item, dict):
            item = {'asn': item}
        try:
            asn = node('asn', item.get('asn') or item.get('number'))
            proof = _proof(root, item, 'rdap', run, result.get('observed_at'))
            relation(root, asn, item.get('relation') or 'reported_asn', proof, 90)
        except (ValueError, TypeError):
            continue
    return root


@transaction.atomic
def ingest_registration(result,run=None):
    root=node('domain',result['target'])
    record=result.get('registration') or {}
    if not isinstance(record,dict) or not record.get('domain') or not record.get('source_url'): return root
    boundary=record['domain']
    owner=node('domain',boundary) if boundary else root
    proof=evidence(owner,'rdap_domain',record,source_url=record.get('source_url') or '',
        observed_at=result.get('observed_at'),expires_at=timezone.now()+timedelta(days=1),run=run)
    if owner.pk!=root.pk: relation(root,owner,'registrable_domain',proof)
    # Registration roles stay in authoritative evidence; a registrar is never an owner.
    return root


@transaction.atomic
def ingest_organization(result, run=None):
    root = node('organization', result['organization'])
    for item in (result.get('domains') or [])[:512]:
        if not isinstance(item, dict) or not item.get('name'):
            continue
        try:
            domain = node('domain', item['name'])
            proof = _proof(domain, item, 'netlas_whois', run, result.get('observed_at'))
            # The query organization may differ from the actual returned holder.
            holder = item.get('organization')
            if holder and item.get('relation') == 'registrant_organization':
                relation(domain, node('organization', holder), 'registrant_organization', proof, item.get('confidence', 80))
                if holder.casefold() != root.value.casefold():
                    relation(root,domain,'registration_candidate',proof,40)
            else:
                relation(root, domain, 'registration_candidate', proof, 60)
        except (ValueError, TypeError):
            continue
    for item in (result.get('networks') or [])[:128]:
        try:
            network = _network(item)
            if network:
                proof = _registration(network, item, run, 'netlas_whois', result.get('observed_at'))
                # Search hits remain candidates relative to the original query.
                relation(root, network, 'registration_candidate', proof, 70)
        except (ValueError, TypeError):
            continue
    for collection, kind in [('ips', 'ip'), ('asns', 'asn')]:
        for item in (result.get(collection) or [])[:128]:
            if not isinstance(item, dict):
                item = {'value': item}
            value = item.get('value') or item.get('ip') or item.get('address') or item.get('asn') or item.get('number')
            try:
                target = node(kind, value)
                proof = _proof(target, item, 'netlas_whois', run, result.get('observed_at'))
                relation(root, target, 'registration_candidate', proof, 60)
            except (ValueError, TypeError):
                continue
    for key, kind in [('seed_domains', 'domain'), ('seed_networks', 'network'), ('seed_asns', 'asn')]:
        for value in (result.get(key) or [])[:128]:
            target = node(kind, value)
            proof = evidence(root, 'user_scope', {'kind': kind, 'value': target.value}, run=run)
            relation(root, target, 'user_scope', proof)
    return root


@transaction.atomic
def ingest_certificate_graph(record):
    certificate = node('certificate', record.key, {'certificate_id': str(record.pk),
        'fingerprint': record.fingerprint, 'historical': True}, label=record.fingerprint or str(record.pk))
    proof = evidence(certificate, 'certstream', {'certificate_id': str(record.pk),
        'domains': record.domains, 'issuer': record.issuer, 'historical': True,
        'scope': 'locally_saved_watch_matches'}, source_url='/api/assets/certificates/' + str(record.pk),
        observed_at=record.observed_at or record.last_seen)
    for value in (record.domains or [])[:512]:
        try:
            relation(certificate, node('domain', value), 'certificate_name', proof)
        except ValueError:
            continue
    return certificate


def _service(endpoint):
    host = '[' + endpoint.address + ']' if ':' in endpoint.address else endpoint.address
    value = endpoint.transport + '://' + host + ':' + str(endpoint.port)
    if endpoint.scheme:
        value += '/' + endpoint.scheme
    return node('service', value, {'endpoint_id': str(endpoint.pk), 'port': endpoint.port,
        'transport': endpoint.transport, 'scheme': endpoint.scheme}, label=host + ':' + str(endpoint.port))


@transaction.atomic
def ingest_observation_graph(observation):
    if observation.hidden or observation.provider == 'certstream':
        return None
    endpoint = observation.endpoint
    service = _service(endpoint)
    root = _target(endpoint.address)
    proof = evidence(service, observation.provider, {'observation_id': str(observation.pk),
        'observation_pk': observation.pk.hex, 'endpoint_id': str(endpoint.pk),
        'source_id': observation.source_id, 'historical': True},
        source_url='/api/operations/endpoints/' + str(endpoint.pk),
        observed_at=observation.last_observed_at or observation.observed_at or observation.last_collected_at)
    relation(root, service, 'observed_service', proof)
    from .dns_enrichment import extract_names
    metadata = (observation.data or {}).get('metadata') or {}
    names = extract_names(metadata, provider=observation.provider)
    if names:
        # Tie every name to this observation rather than an unrelated DNS lookup.
        for item in names[:512]:
            if not isinstance(item, dict) or not item.get('name'):
                continue
            try:
                target = node('domain', item['name'])
                kind = _name_relation(str(item.get('source', '')))
                relation(service, target, kind, proof, 70)
            except ValueError:
                continue
    return service


@transaction.atomic
def ingest_cloud_graph(resource):
    service = _service(resource.endpoint) if resource.endpoint_id else node('service', resource.url,
        {'cloud_resource_id': str(resource.pk), 'kind': resource.kind}, label=resource.name)
    proof = evidence(service, 'cloud:' + resource.provider,
        {'cloud_resource_id': str(resource.pk), 'url': resource.url, 'kind': resource.kind,
         'state': resource.state, 'evidence': resource.evidence, 'historical': True},
        source_url='/api/operations/cloud/resources?q=' + quote(resource.url, safe=''), observed_at=resource.last_checked, run=resource.last_run)
    provider = node('organization', resource.provider, {'role': 'cloud_provider'})
    relation(service, provider, 'hosted_by', proof)
    try:
        address = _target(urlsplit(resource.url).hostname or '')
        relation(address, service, 'observed_resource', proof)
    except ValueError:
        pass
    return service


def visible_evidence():
    # UUIDs are canonicalized without hyphens on both SQLite and PostgreSQL.
    # A subquery keeps hidden-source suppression in one bounded graph query.
    hidden = ServiceObservation.objects.filter(hidden=True).annotate(
        graph_key=Replace(Cast('pk', CharField()), Value('-'), Value(''))).values('graph_key')
    hidden_findings=FindingReview.objects.filter(hidden=True).annotate(
        graph_key=Concat('type',Value(':'),Cast('object_id',CharField()),output_field=CharField())).values('graph_key')
    from .state import visible_endpoints
    endpoints=visible_endpoints().annotate(graph_key=Replace(Cast('pk',CharField()),Value('-'),Value(''))).values('graph_key')
    return AssetEvidence.objects.annotate(
        graph_observation_pk=KeyTextTransform('observation_pk', 'data'),
        graph_finding_key=KeyTextTransform('finding_key','data'),
        graph_endpoint_pk=KeyTextTransform('endpoint_pk','data')).filter(
            Q(graph_observation_pk__isnull=True) | ~Q(graph_observation_pk__in=Subquery(hidden))
        ).filter(Q(graph_finding_key__isnull=True)|~Q(graph_finding_key__in=Subquery(hidden_findings))).filter(
            Q(graph_endpoint_pk__isnull=True)|Q(graph_endpoint_pk__in=Subquery(endpoints)))


def _retired_organizations(include_archived=False):
    states=['merged','deleted']+([] if include_archived else ['archived'])
    return AssetNode.objects.filter(kind='organization',organization_status__in=states).values('pk')


def _visible_edges(include_archived=False):
    return AssetRelationship.objects.exclude(source_id__in=Subquery(_retired_organizations(include_archived))).exclude(target_id__in=Subquery(_retired_organizations(include_archived))).filter(evidence_id__in=Subquery(visible_evidence().values('pk'))).select_related('evidence').defer('evidence__data')


def _visible_nodes(include_archived=False):
    visible = _visible_edges(include_archived)
    all_edges = AssetRelationship.objects.all()
    return AssetNode.objects.exclude(pk__in=Subquery(_retired_organizations(include_archived))).filter(
        Q(pk__in=Subquery(visible_evidence().values('subject_id'))) |
        Q(pk__in=Subquery(visible.values('source_id'))) | Q(pk__in=Subquery(visible.values('target_id'))) |
        (~Q(pk__in=Subquery(all_edges.values('source_id'))) & ~Q(pk__in=Subquery(all_edges.values('target_id')))))


def graph(root_id=None, max_nodes=150, max_edges=500, max_depth=3, *, include_candidates=True):
    """Return a bounded connected subgraph, or a bounded recent graph overview."""
    max_nodes = max(1, min(int(max_nodes), 500))
    max_edges = max(1, min(int(max_edges), 2000))
    max_depth = max(0, min(int(max_depth), 6))
    include_archived=False
    limited = False
    selected = set()
    edges = {}
    edge_filter = Q() if include_candidates else ~Q(relation='domain_candidate') & ~Q(evidence__source='domain_expansion')
    layer = {'include_candidates': include_candidates,
        'label': 'Including research candidates' if include_candidates else 'Evidence relationships'}
    if root_id:
        try:
            root_node=_visible_nodes(include_archived=True).filter(pk=root_id).first()
            if root_node is None:
                retired=AssetNode.objects.filter(pk=root_id,kind='organization',organization_status='merged').first()
                if retired:
                    from .organization_lifecycle import canonical
                    resolved=canonical(retired)
                    root_node=_visible_nodes(include_archived=True).filter(pk=resolved.pk).first()
            root=root_node.pk if root_node else None
            include_archived=bool(root_node and root_node.organization_status=='archived')
        except (ValueError, TypeError, ValidationError):
            root = None
        if root is None:
            return {'nodes': [], 'edges': [], 'limited': False, 'layer': layer,
                'caveats': ['No saved asset matches this root.']}
        selected.add(root)
        frontier = {root}
        for depth in range(max_depth):
            if not frontier or len(edges) >= max_edges:
                break
            candidates = list(_visible_edges(include_archived).filter(edge_filter).filter(Q(source_id__in=frontier) | Q(target_id__in=frontier))
                .order_by('-last_seen', 'pk')[:max_edges + 1])
            if len(candidates) > max_edges:
                limited = True
            following = set()
            for edge in candidates[:max_edges]:
                new = {edge.source_id, edge.target_id} - selected
                if len(selected) + len(new) > max_nodes or (edge.pk not in edges and len(edges) >= max_edges):
                    limited = True
                    continue
                selected.update(new)
                following.update(new)
                edges[edge.pk] = edge
            frontier = following
        if frontier and max_depth > 0:
            # Reaching the requested traversal radius is an explicit boundary.
            limited = True
        nodes = list(AssetNode.objects.filter(pk__in=selected).order_by('kind', 'value'))
    else:
        visible_nodes = _visible_nodes(include_archived)
        if not include_candidates:
            eligible_edges = _visible_edges(include_archived).filter(edge_filter)
            independent_proof = visible_evidence().exclude(source='domain_expansion')
            all_edges = AssetRelationship.objects.all()
            visible_nodes = visible_nodes.filter(
                Q(pk__in=Subquery(independent_proof.values('subject_id'))) |
                Q(pk__in=Subquery(eligible_edges.values('source_id'))) |
                Q(pk__in=Subquery(eligible_edges.values('target_id'))) |
                (~Q(pk__in=Subquery(all_edges.values('source_id'))) &
                    ~Q(pk__in=Subquery(all_edges.values('target_id'))) &
                    ~Q(pk__in=Subquery(AssetEvidence.objects.values('subject_id')))))
        nodes = list(visible_nodes.order_by('-last_seen', 'pk')[:max_nodes + 1])
        limited = len(nodes) > max_nodes
        nodes = nodes[:max_nodes]
        selected = {item.pk for item in nodes}
        candidates = list(_visible_edges(include_archived).filter(edge_filter).filter(source_id__in=selected, target_id__in=selected)
            .order_by('-last_seen', 'pk')[:max_edges + 1])
        limited = limited or len(candidates) > max_edges
        edges = {item.pk: item for item in candidates[:max_edges]}
    now = timezone.now()
    return {'nodes': [{'id': str(item.pk), 'kind': item.kind, 'value': item.value,
                'label': item.label, 'attributes': item.attributes,
                'first_seen': item.first_seen.isoformat(), 'last_seen': item.last_seen.isoformat()}
            for item in nodes],
        'edges': [{'id': str(edge.pk), 'source': str(edge.source_id), 'target': str(edge.target_id),
            'relation': edge.relation, 'confidence': edge.confidence,
            'evidence_id': str(edge.evidence_id), 'evidence_source': edge.evidence.source,
            'source_url': edge.evidence.source_url, 'evidence_source_url': edge.evidence.source_url,
            'observed_at': edge.evidence.observed_at.isoformat() if edge.evidence.observed_at else None,
            'expires_at': edge.evidence.expires_at.isoformat() if edge.evidence.expires_at else None,
            'expired': bool(edge.evidence.expires_at and edge.evidence.expires_at <= now),
            'first_seen': edge.first_seen.isoformat(), 'last_seen': edge.last_seen.isoformat(),
            'historical': True} for edge in edges.values()],
        'limited': limited, 'layer': layer,
        'caveats': ['Saved evidence is historical and does not establish current reachability or ownership.',
            'Expired DNS records describe a past observation; refresh explicitly to check the current mapping.',
            'Certificate coverage includes locally saved matches, not a complete CT archive.'] + ([] if include_candidates else [
                'Evidence relationships omit domain expansion research candidate links and expansion-only hierarchy evidence. Independently evidenced structure and declarations remain; these associations do not establish ownership.'])}
