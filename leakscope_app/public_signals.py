"""Paginated retained public signals, plus explicit one-request Entra discovery."""
import math
from uuid import UUID

from django.db.models import CharField, Count, IntegerField, Max, Min, Q, Value
from django.db.models.fields.json import KT
from django.db.models.functions import Cast
from django.http import JsonResponse

from leakscope.phase3 import relationships as store
from leakscope.phase3.asset_jobs import queue_enrichment
from leakscope.phase3.asset_names import normalize_domain
from leakscope.phase3.usage_policy import validate_demo
from .evidence_pivots import SIGNAL_KINDS
from .operations import _integer
from .workbench import endpoint, _body
from .workspace import _scope, _correlation_candidates

BASIS = ('Visible retained marketing project IDs, API service declarations and Microsoft Entra tenant declarations. '
         'Single-endpoint observations are included. Shared agencies, copied configuration and third-party services '
         'can explain matches; these are research leads, not ownership or reachability proof. '
         'Filters narrow displayed proof counts. Browsing makes no network requests.')
KINDS = sorted(SIGNAL_KINDS | {'entra.tenant'})


@endpoint()
def public_signals(request):
    members = _scope(request.GET.get('organization_id', ''))
    query = request.GET.get('q', '').strip()
    if len(query) > 200 or any(ord(char) < 32 for char in query):
        raise ValueError('Signal filter exceeds supported limits')
    kind = request.GET.get('kind', '')
    if kind and kind not in KINDS:
        raise ValueError('Unsupported public signal kind')
    page = _integer(request.GET, 'page', 1, 1, 1000000)
    size = _integer(request.GET, 'page_size', 25, 1, 100)
    signals = _correlation_candidates(members).filter(kind__in=SIGNAL_KINDS)
    proofs = store.visible_evidence().filter(source='public_identity:entra', subject__kind='domain',
        data__namespace='entra.tenant',
        data__value__regex=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
    if members is not None:
        proofs = proofs.filter(subject__in=members)
    if query:
        signals = signals.filter(Q(kind__icontains=query) | Q(value__icontains=query) | Q(endpoint__address__icontains=query))
        if query.casefold() not in 'entra.tenant':
            proofs = proofs.filter(Q(data__value__icontains=query) | Q(subject__value__icontains=query))
    if kind:
        signals = signals.filter(kind=kind)
        if kind != 'entra.tenant':
            proofs = proofs.none()
    columns = ['kind', 'value', 'endpoint_count', 'domain_count', 'reference_id', 'last_seen', 'reference_type']
    signal_groups = signals.order_by().values('kind', 'value').annotate(
        endpoint_count=Count('endpoint_id', distinct=True), domain_count=Value(0, output_field=IntegerField()),
        reference_id=Min(Cast('endpoint_id', CharField())), last_seen=Max('last_seen'),
        reference_type=Value('endpoint', output_field=CharField())).values(*columns)
    entra_groups = proofs.order_by().annotate(kind=Value('entra.tenant', output_field=CharField()), value=Cast(KT('data__value'), CharField())).values(
        'kind', 'value').annotate(endpoint_count=Value(0, output_field=IntegerField()), domain_count=Count('subject_id', distinct=True),
        reference_id=Min(Cast('id', CharField())), last_seen=Max('last_collected_at'),
        reference_type=Value('evidence', output_field=CharField())).values(*columns)
    combined = signal_groups.union(entra_groups).order_by('kind', 'value')
    total = combined.count()
    selected = list(combined[(page - 1) * size:page * size])
    signal_pairs = Q(pk__in=[])
    for row in selected:
        if row['reference_type'] == 'endpoint':
            signal_pairs |= Q(kind=row['kind'], value=row['value'], endpoint_id=UUID(row['reference_id']))
    signal_proof = {(row['kind'], row['value'], str(row['endpoint_id'])): row for row in signals.filter(signal_pairs).values(
        'kind', 'value', 'endpoint_id', 'observation_id', 'visible_observation_id', 'visible_fk', 'artifact_id', 'artifact_visible')}
    results = []
    for row in selected:
        identifier = str(UUID(row.pop('reference_id')))
        reference = {'type': row.pop('reference_type'), 'id': identifier}
        proof_ref = dict(reference)
        if reference['type'] == 'endpoint':
            proof = signal_proof[(row['kind'], row['value'], identifier)]
            observation = proof['observation_id'] if proof['visible_fk'] else proof['visible_observation_id']
            if observation:
                proof_ref['observation_id'] = str(observation)
            if proof['artifact_visible'] and proof['artifact_id']:
                proof_ref['artifact_id'] = str(proof['artifact_id'])
            preview = {'kind': row['kind'], 'value': row['value']}
        else:
            preview = {'evidence_id': identifier}
        row.update(reference=reference, proof_refs=[proof_ref], preview_payload=preview, basis=BASIS)
        results.append(row)
    return JsonResponse({'results': results, 'total': total, 'page': page, 'page_size': size,
        'pages': max(1, math.ceil(total / size)), 'kinds': KINDS, 'network_contact': False, 'basis': BASIS})


@endpoint(('POST',))
def entra(request):
    data = _body(request)
    if data.keys() - {'domain', 'demo'}:
        raise ValueError('Entra discovery accepts domain and demo only')
    domain = normalize_domain(data.get('domain'))
    return JsonResponse(queue_enrichment('identity_metadata', domain, max_requests=1, demo=validate_demo(data.get('demo', False))))


signals = public_signals
