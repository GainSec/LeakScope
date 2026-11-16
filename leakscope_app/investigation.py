"""Read-only, bounded views over saved evidence; never collect or refresh targets."""
import hashlib
import json
from uuid import UUID
from django.db.models import CharField, Count, Exists, OuterRef, Q, Subquery, Value
from django.db.models.functions import Cast, Replace
from django.db.models.fields.json import KeyTextTransform
from django.http import JsonResponse
from leakscope.phase3.analysis import stable_evidence
from leakscope.phase3.state import visible_endpoints
from leakscope.services.run_events import sanitize
from .models import ServiceObservation, AssetNode, AssetEvidence, AssetRelationship
from .workbench import endpoint

MAX_CHANGES = 100
RECENT_LIMIT = 12
BREAKDOWN_LIMIT = 30


def _stamp(value):
    return value.isoformat() if value else None


def provenance(source, observed_at, collected_at, *, historical=False):
    if source.startswith(('fetch:', 'scan:', 'cloud:')) or source in {'dns', 'ptr', 'httpx', 'curl', 'gowitness', 'playwright'}:
        label = 'Direct capture'
        basis = 'Saved direct collection evidence; the capture does not establish current reachability.'
    elif source.startswith(('user:', 'manual:')) or source in {'user_scope', 'manual', 'user', 'organization_lifecycle'}:
        label = 'User supplied'
        basis = 'User supplied assertion; ownership and current reachability are unverified.'
    elif source in {'inferred', 'inference', 'heuristic', 'dns_service_signal'}:
        label = 'Inferred'
        basis = 'Derived from saved evidence; this is not a direct observation or ownership confirmation.'
    elif historical or source in {'certstream', 'certificate', 'commoncrawl', 'common_crawl'}:
        label = 'Historical evidence'
        basis = 'Saved historical evidence; it does not establish current service state.'
    else:
        label = 'Provider-reported'
        basis = 'Saved provider evidence; collection time does not revalidate the service.'
    return {'label': label, 'observed_at': _stamp(observed_at),
            'collected_at': _stamp(collected_at), 'basis': basis}


def observation_summary(row):
    return {'id': str(row.pk), 'provider': row.provider, 'observed_at': _stamp(row.observed_at),
            'last_observed_at': _stamp(row.last_observed_at),
            'first_collected_at': _stamp(row.first_collected_at),
            'last_collected_at': _stamp(row.last_collected_at), 'collection_count': row.collection_count,
            'provenance': provenance(row.provider, row.observed_at, row.last_collected_at)}


COMPARE_BASIS = ('Comparison of two distinct saved records on the same endpoint and provider, not a live check. '
                 'Collector timestamps, search-profile labels and other volatile fields are excluded. '
                 'Identical payloads are deduplicated: observed_at is the initially saved provider time and '
                 'last_observed_at is the latest reported time for that duplicate record, not a continuous observed interval. '
                 'Collection times describe retention only; they do not prove chronological service transitions. '
                 'Overwritten DNS records and other unsaved snapshots cannot be reconstructed. '
                 'Count is the number of displayed changes; truncated means some fields or values were omitted.')


def _changes(before, after):
    changes = []
    truncated = False
    visited = 0
    missing = object()

    def display(value, depth=0):
        nonlocal truncated
        if value is missing:
            return None
        if isinstance(value, str):
            if len(value) > 1000:
                truncated = True
                return value[:1000] + f'… [{len(value)} chars; sha256 {hashlib.sha256(value.encode()).hexdigest()[:16]}]'
            return value
        if depth >= 4 and isinstance(value, (dict, list)):
            truncated = True
            return '[nested value omitted]'
        if isinstance(value, dict):
            keys = sorted(value)
            if len(keys) > 20:
                truncated = True
            return {key: display(value[key], depth+1) for key in keys[:20]}
        if isinstance(value, list):
            if len(value) > 20:
                truncated = True
            return [display(item, depth+1) for item in value[:20]]
        return value

    def visit(left, right, path='', depth=0):
        nonlocal truncated, visited
        visited += 1
        if visited > 5000:
            truncated = True
            return
        if left == right:
            return
        if len(changes) >= MAX_CHANGES:
            truncated = True
            return
        if isinstance(left, dict) and isinstance(right, dict) and depth < 10:
            for key in sorted(left.keys() | right.keys()):
                if len(changes) >= MAX_CHANGES or visited > 5000:
                    truncated = True
                    break
                part = key.replace('~', '~0').replace('/', '~1')
                visit(left.get(key, missing), right.get(key, missing), path+'/'+part, depth+1)
        else:
            kind = 'added' if left is missing else 'removed' if right is missing else 'changed'
            def bounded(value):
                nonlocal truncated
                result = display(value)
                encoded = json.dumps(result, sort_keys=True, ensure_ascii=False)
                if len(encoded) > 1600:
                    truncated = True
                    return encoded[:1000] + f'… [value truncated; sha256 {hashlib.sha256(encoded.encode()).hexdigest()[:16]}]'
                return result
            if len(path) > 512:
                truncated = True
                path = path[:512] + f'… [path sha256 {hashlib.sha256(path.encode()).hexdigest()[:16]}]'
            changes.append({'path': path or '/', 'kind': kind,
                            'before': bounded(left), 'after': bounded(right)})
    visit(before, after)
    return changes, truncated


@endpoint()
def compare(request, endpoint_id):
    target = visible_endpoints().get(pk=UUID(str(endpoint_id)))
    before_id = UUID(request.GET.get('before', ''))
    after_id = UUID(request.GET.get('after', ''))
    if before_id == after_id:
        raise ValueError('Choose two distinct saved observations')
    rows = target.observations.filter(hidden=False)
    before = rows.get(pk=before_id)
    after = rows.get(pk=after_id)
    if before.provider != after.provider:
        raise ValueError('Choose observations from the same provider')
    safe = sanitize({'before': stable_evidence(before.data), 'after': stable_evidence(after.data)}, preserve_metrics=True)
    changes, truncated = _changes(safe['before'], safe['after'])
    return JsonResponse(sanitize({'before': observation_summary(before), 'after': observation_summary(after),
        'changes': changes, 'count': len(changes), 'truncated': truncated, 'basis': COMPARE_BASIS}, preserve_metrics=True))


def organization_overview(root):
    from leakscope.phase3 import relationships as store
    links = root.outgoing.filter(evidence_id__in=Subquery(store.visible_evidence().values('pk'))).filter(
        Q(relation='user_scope') | Q(relation__in=['uses_service', 'references_domain', 'tracks_lookalike'], target__kind='domain'))
    members = AssetNode.objects.filter(pk__in=Subquery(links.values('target_id'))).exclude(pk=root.pk)
    proofs = store.visible_evidence().filter(Q(subject=root) | Q(subject_id__in=Subquery(members.values('pk'))) |
                                           Q(pk__in=Subquery(links.values('evidence_id'))))
    # Compare canonical text inside the database: invalid legacy JSON values
    # cannot raise UUID-cast errors or produce an unbounded Python/SQL IN list.
    service_members = members.filter(kind='service').annotate(
        endpoint_key=Replace(KeyTextTransform('endpoint_id', 'attributes'), Value('-'), Value(''), output_field=CharField())).filter(
        endpoint_key=Replace(Cast(OuterRef('pk'), CharField()), Value('-'), Value('')))
    endpoints = visible_endpoints().annotate(organization_member=Exists(service_members)).filter(organization_member=True)
    source_rows = list(proofs.values('source').annotate(count=Count('pk')).order_by('-count', 'source')[:BREAKDOWN_LIMIT+1])
    kind_rows = list(members.values('kind').annotate(count=Count('pk')).order_by('-count', 'kind')[:BREAKDOWN_LIMIT+1])
    tech_rows = list(ServiceObservation.objects.filter(endpoint_id__in=Subquery(endpoints.values('pk')), hidden=False)
        .exclude(data__service_type='').exclude(data__service_type__isnull=True)
        .values('data__service_type').annotate(count=Count('endpoint_id', distinct=True))
        .order_by('-count', 'data__service_type')[:BREAKDOWN_LIMIT+1])
    recent = list(proofs.select_related('subject').order_by('-last_collected_at', '-pk')[:RECENT_LIMIT+1])
    observation_ids = []
    for row in recent[:RECENT_LIMIT]:
        try:
            observation_ids.append(UUID(str(row.data.get('observation_pk'))))
        except (ValueError, TypeError, AttributeError):
            pass
    observations = {row.pk: row for row in ServiceObservation.objects.filter(pk__in=observation_ids, hidden=False)}
    evidence = []
    for row in recent[:RECENT_LIMIT]:
        saved = None
        try:
            saved = observations.get(UUID(str(row.data.get('observation_pk'))))
        except (ValueError, TypeError, AttributeError):
            pass
        proof = provenance(saved.provider, saved.observed_at, saved.last_collected_at) if saved else provenance(
            row.source, row.observed_at, row.last_collected_at, historical=bool(row.data.get('historical')))
        evidence.append({'id': str(row.pk), 'subject_id': str(row.subject_id), 'subject_kind': row.subject.kind,
            'subject': row.subject.value, 'source': row.source, 'observed_at': proof['observed_at'],
            'collected_at': proof['collected_at'], 'provenance': proof,
            'reference': {'type': 'asset', 'id': str(row.subject_id)}})
    return sanitize({'basis': 'Saved evidence for explicit organization scope and linked results only; links do not establish ownership. '
                     'Counts exclude hidden evidence. Recent records are deduplicated saved evidence, not a complete history; overwritten DNS snapshots are unavailable.',
        'counts': {'assets': members.count(), 'scope_domains': links.filter(relation='user_scope', target__kind='domain').values('target_id').distinct().count(),
            'linked_results': links.filter(~Q(target__kind='domain') | ~Q(relation='user_scope')).values('target_id').distinct().count(), 'endpoints': endpoints.count(),
            'evidence': proofs.count(), 'relationships': links.count()},
        'kinds': kind_rows[:BREAKDOWN_LIMIT], 'sources': source_rows[:BREAKDOWN_LIMIT],
        'technologies': [{'technology': row['data__service_type'], 'count': row['count']} for row in tech_rows[:BREAKDOWN_LIMIT] if isinstance(row['data__service_type'], str)],
        'recent_evidence': evidence, 'recent_limit': RECENT_LIMIT, 'recent_truncated': len(recent)>RECENT_LIMIT,
        'breakdown_truncated': any(len(rows)>BREAKDOWN_LIMIT for rows in (source_rows, kind_rows, tech_rows))})
