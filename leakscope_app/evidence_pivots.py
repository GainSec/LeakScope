"""Proof-bound provider search previews, generated without network requests."""
from uuid import UUID

from django.core.exceptions import ObjectDoesNotExist
from django.http import JsonResponse

from leakscope.intelligence.queries import compile_queries
from leakscope.phase3 import relationships as store
from .workbench import endpoint, _body
from .workspace import _correlation_candidates

PUBLIC_NAMESPACES = frozenset({'entra.tenant'})
SIGNAL_KINDS = frozenset({
    'tracking.google_analytics', 'tracking.google_tag_manager', 'tracking.meta_pixel',
    'tracking.linkedin_partner', 'tracking.microsoft_uet', 'tracking.microsoft_clarity',
    'tracking.hubspot_portal', 'tracking.tiktok_pixel', 'tracking.hotjar_site',
    'tracking.google_ads', 'tracking.google_adsense',
    'declaration.openapi.server', 'declaration.openapi.oauth_authorize', 'declaration.openapi.oauth_token',
    'declaration.swagger.server', 'declaration.swagger.oauth_authorize', 'declaration.swagger.oauth_token',
    'declaration.asyncapi.server', 'declaration.wsdl.server',
})
BASIS = ('Literal previews search indexed HTTP bodies only and can miss unindexed declarations, scripts or metadata. '
         'Shared public identifiers can connect differently named sites, but agencies, shared services and copied '
         'configuration can span unrelated organizations. Matches are research leads, not ownership or vulnerability '
         'claims. No organization scope is added, queries are not executed, and no presets are saved.')


def _term(value):
    if not isinstance(value, str) or not value or len(value) > 1000 or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError('Saved signal must contain one to 1000 printable characters')
    return value


@endpoint(('POST',))
def pivot_queries(request):
    data = _body(request)
    basis = BASIS
    label = 'Public identifier research lead'
    if set(data) == {'evidence_id'}:
        if not isinstance(data['evidence_id'], str):
            raise ValueError('evidence_id must be a saved evidence UUID')
        try:
            identifier = UUID(data['evidence_id'])
        except (ValueError, TypeError, AttributeError):
            raise ValueError('evidence_id must be a saved evidence UUID') from None
        proof = store.visible_evidence().filter(pk=identifier).only('id', 'source', 'data').first()
        if proof is None:
            raise ObjectDoesNotExist()
        record = proof.data if isinstance(proof.data, dict) else {}
        kind = record.get('namespace')
        if proof.source != 'public_identity:entra' or kind not in PUBLIC_NAMESPACES:
            raise ValueError('This saved evidence has no supported public identifier pivot')
        term = _term(record.get('value'))
        reference = {'type': 'evidence', 'id': str(proof.pk)}
    elif set(data) == {'kind', 'value'}:
        kind = data['kind']
        if not isinstance(kind, str) or kind not in SIGNAL_KINDS:
            raise ValueError('Choose a supported public tracking or service declaration signal')
        value = _term(data['value'])
        signal = _correlation_candidates(None).filter(kind=kind, value=value).order_by('pk').values(
            'endpoint_id', 'kind', 'value').first()
        if signal is None:
            raise ObjectDoesNotExist()
        term = _term(signal['value'])
        reference = {'type': 'endpoint', 'id': str(signal['endpoint_id'])}
        if kind.startswith('declaration.'):
            label = 'Declared service research lead'
    else:
        raise ValueError('Supply exactly evidence_id, or exactly kind and value from a visible saved signal')
    queries = compile_queries([{'field': 'body', 'value': term}], mode='all', scope={})
    return JsonResponse({'term': term, 'kind': kind, 'label': label, 'queries': queries,
                         'basis': basis, 'network_contact': False, 'reference': reference})
