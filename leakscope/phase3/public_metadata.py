"""One explicit public Microsoft Entra discovery request for a selected domain."""
from datetime import timedelta
import json
import time
from urllib.parse import urlsplit
from uuid import UUID

import requests
from django.utils import timezone

from leakscope.shared import is_blacklisted
from .asset_names import normalize_domain

MAX_BODY = 256 * 1024
IDENTITY_POLICY_VERSION = 'entra-only-v3'
ENTRA_CLOUDS = {'login.microsoftonline.com': 'Microsoft public cloud',
                'login.microsoftonline.us': 'Microsoft US Government cloud',
                'login.partner.microsoftonline.cn': 'Microsoft China cloud'}
METHODS = (('entra', 'Microsoft Entra tenant metadata',
            'https://login.microsoftonline.com/{domain}/v2.0/.well-known/openid-configuration'),)
BASIS = ('A concrete Microsoft Entra issuer associates a domain with a declared tenant and cloud. '
         'It does not establish corporate ownership, physical location or verified service reachability. '
         'One fixed Microsoft discovery URL is requested. Authentication, returned endpoints and redirects are not followed.')


def _entra(data):
    if not isinstance(data, dict):
        raise ValueError('Expected a JSON object')
    issuer = data.get('issuer', '')
    try:
        parts = urlsplit(issuer if isinstance(issuer, str) else '')
        valid = parts.scheme == 'https' and parts.hostname in ENTRA_CLOUDS and parts.username is None and parts.password is None and parts.port in (None, 443)
    except ValueError:
        return []
    if not valid:
        return []
    path = parts.path.strip('/').split('/')
    if len(path) != 2 or path[1] != 'v2.0' or parts.query or parts.fragment:
        return []
    try:
        tenant = str(UUID(path[0]))
    except ValueError:
        return []
    return [{'namespace': 'entra.tenant', 'value': tenant,
             'attributes': {'issuer': issuer, 'cloud': ENTRA_CLOUDS[parts.hostname]}}]


def parse_identities(method, payload, *, with_limits=False):
    if method != 'entra':
        raise ValueError('Only Microsoft Entra discovery is supported')
    values = _entra(json.loads(payload))
    return (values, False) if with_limits else values


def _read(url, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError('Collection time budget exhausted')
    with requests.get(url, timeout=(min(4, remaining), min(6, remaining)), stream=True, allow_redirects=False,
                      headers={'User-Agent': 'LeakScope Entra discovery/1.0', 'Accept': 'application/json'}) as response:
        if response.status_code != 200:
            return response.status_code, None
        body = bytearray()
        for chunk in response.iter_content(8192):
            if time.monotonic() >= deadline:
                raise ValueError('Collection time budget exhausted')
            body.extend(chunk)
            if len(body) > MAX_BODY:
                raise ValueError('Metadata exceeded the 256 KiB response limit')
        return 200, body.decode('utf-8-sig', errors='replace')


def collect_public_metadata(domain, config=None, *, max_requests=1, timeout=15, on_progress=None):
    domain = normalize_domain(domain)
    if isinstance(max_requests, bool) or not isinstance(max_requests, int) or not 1 <= max_requests <= 6:
        raise ValueError('Entra discovery requires a valid request budget')
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 60:
        raise ValueError('Invalid Entra discovery timeout')
    if is_blacklisted(domain, {'config': config or {}}):
        raise ValueError('Domain is excluded from collection')
    now = timezone.now()
    method, label, template = METHODS[0]
    url = template.format(domain=domain)
    attempt = {'method': method, 'label': label, 'source_url': url, 'status': 'not_requested', 'count': 0}
    result = {'target': domain, 'observed_at': now.isoformat(), 'identities': [], 'attempts': [attempt],
              'requests': 0, 'partial': False, 'errors': [], 'coverage': BASIS,
              'identity_policy_version': IDENTITY_POLICY_VERSION, 'methods_omitted': []}
    deadline = time.monotonic() + timeout
    try:
        if time.monotonic() >= deadline:
            raise ValueError('Collection time budget exhausted')
        result['requests'] = 1
        status, payload = _read(url, deadline)
        attempt['http_status'] = status
        if status != 200:
            attempt['status'] = 'not_published' if status in (404, 410) else 'redirect_not_followed' if 300 <= status < 400 else 'unavailable'
            result['partial'] = status not in (404, 410)
        else:
            identities = parse_identities('entra', payload)
            attempt.update(count=len(identities), status='declared' if identities else 'no_supported_declarations', truncated=False)
            result['identities'] = [dict(item, method='entra', source_url=url, observed_at=now.isoformat()) for item in identities]
    except (requests.RequestException, ValueError, TypeError, UnicodeError, RecursionError):
        attempt['status'] = 'unavailable_or_invalid'
        result['partial'] = True
        result['errors'].append('Microsoft Entra metadata was unavailable or invalid within collection limits.')
    if on_progress:
        on_progress(dict(attempt))
    return result


def ingest_public_metadata(result, run=None):
    from . import relationships as store
    root = store.node('domain', result['target'])
    expiry = timezone.now() + timedelta(days=1)
    for record in result.get('identities', []):
        if not isinstance(record, dict) or record.get('namespace') != 'entra.tenant' or record.get('method') != 'entra':
            continue
        try:
            tenant = str(UUID(record['value']))
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
        target = store.node('identifier', 'entra.tenant:' + tenant,
                            {'namespace': 'entra.tenant', 'public_value': tenant}, label=tenant)
        proof = store.evidence(root, 'public_identity:entra', record, source_url=record.get('source_url', ''),
                               observed_at=record.get('observed_at'), expires_at=expiry, run=run)
        store.relation(root, target, 'declares_public_identifier', proof, confidence=80)
    return root
