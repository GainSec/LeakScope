"""Bounded DNS evidence coverage over saved visible domain scope."""
from collections import Counter
import re
from uuid import UUID

from django.db.models import Subquery
from django.db.models.fields.json import KT
from django.db.models.functions import Substr
from django.http import JsonResponse
from django.utils import timezone

from leakscope.phase3 import relationships as store, organization_lifecycle as lifecycle
from .workbench import endpoint
from .workspace import _scope

DOMAIN_LIMIT = 1000
EVIDENCE_LIMIT = 1000
BASIS = ('Counts describe visible retained DNS answers for saved domains in the selected scope. '
         'Expired records are historical. Missing records mean no displayed saved proof, not an absent DNS record. '
         'Only bounded samples are displayed; browsing performs no DNS lookups or provider requests.')


@endpoint()
def domain_coverage(request):
    organization_id = request.GET.get('organization_id', '')
    members = _scope(organization_id)
    organization = lifecycle.canonical(lifecycle.organization(UUID(organization_id))) if organization_id else None
    query = request.GET.get('q', '').strip()
    if len(query) > 200 or any(ord(char) < 32 for char in query):
        raise ValueError('Domain filter exceeds supported limits')
    domains = (members if members is not None else store._visible_nodes()).filter(kind='domain')
    if query:
        domains = domains.filter(value__icontains=query)
    total = domains.count()
    selected = list(domains.order_by('value', 'pk')[:DOMAIN_LIMIT])
    now = timezone.now()
    rows = {item.pk: {'id': str(item.pk), 'name': item.value, 'records': Counter(), 'sources': [],
        'freshness': {'current': 0, 'expired': 0, 'unknown': 0}, 'last_observed_at': None, 'last_collected_at': None,
        'reference': {'type': 'asset', 'id': str(item.pk)}} for item in selected}
    proofs = store.visible_evidence().filter(source='dns', subject_id__in=domains.values('pk'))
    evidence_total = proofs.count()
    saved = list(proofs.filter(subject_id__in=rows).annotate(record_type=Substr(KT('data__type'), 1, 24)).values(
        'subject_id', 'record_type', 'observed_at', 'last_collected_at', 'expires_at').order_by('-last_collected_at', 'pk')[:EVIDENCE_LIMIT])
    types = set()
    for proof in saved:
        row = rows[proof['subject_id']]
        record_type = proof['record_type']
        if isinstance(record_type, str) and re.fullmatch(r'[A-Z][A-Z0-9_-]{0,23}', record_type):
            row['records'][record_type] += 1
            types.add(record_type)
        row['sources'] = ['dns']
        expiry = proof['expires_at']
        row['freshness']['unknown' if expiry is None else 'expired' if expiry <= now else 'current'] += 1
        for field, key in [('observed_at', 'last_observed_at'), ('last_collected_at', 'last_collected_at')]:
            value = proof[field]
            if value and (row[key] is None or value > row[key]):
                row[key] = value
    return JsonResponse({'scope': {'id': str(organization.pk) if organization else None,
        'label': (organization.label or organization.value) if organization else 'Saved workspace',
        'kind': 'organization' if organization else 'workspace'}, 'generated_at': now,
        'domains': list(rows.values()), 'record_types': sorted(types), 'network_contact': False,
        'coverage': {'limited': total > len(rows) or evidence_total > len(saved),
            'domains_total': total, 'domains_sampled': len(rows), 'scope_evidence': evidence_total,
            'sampled_evidence': len(saved), 'limits': {'domains': DOMAIN_LIMIT, 'evidence': EVIDENCE_LIMIT}, 'basis': BASIS}})


coverage = domain_coverage
