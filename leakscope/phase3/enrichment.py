"""Explicit, bounded Netlas host/DNS/certificate enrichment.

Verified against https://docs.netlas.io/api-reference/ and the DNS Registry and
Certificates field references (2026-11-07). No count, download or retry calls
are made. Certificate name searches use the documented exact-name field.
"""
import ipaddress
import json
import re
from urllib.parse import quote

from .providers import _clip, _provider, _request, ProviderRateLimit
from .usage_policy import validate_demo, demo_metadata

COLLECTIONS = ('host', 'dns', 'certificates')
MAX_ROWS = 20


def _host(value):
    if not isinstance(value, str) or not value or len(value) > 253:
        raise ValueError('Enter an IP address or hostname')
    value = value.strip().rstrip('.')
    if value.startswith('[') and value.endswith(']'):
        value = value[1:-1]
    try:
        address = ipaddress.ip_address(value)
        return str(address), address.version
    except ValueError:
        if re.fullmatch(r'(?:\d+\.){3}\d+', value):
            raise ValueError('Invalid IPv4 address') from None
    try:
        value = value.encode('idna').decode('ascii').lower()
    except UnicodeError:
        raise ValueError('Invalid hostname') from None
    if len(value) > 253 or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in value.split('.')):
        raise ValueError('Enter an IP address or hostname without a URL, path or query')
    return value, None


def _bounded(value, config, maximum):
    value = _clip(value, config)
    encoded = json.dumps(value, ensure_ascii=True)
    if len(encoded.encode('utf-8')) <= maximum:
        return value, False
    # Count escaped JSON so storage serializers cannot expand a Unicode page
    # beyond the budget. Leave headroom for preview escaping and its wrapper.
    return {'truncated': True, 'preview': encoded[:maximum // 8]}, True


def enrich_host(host, config, collections=None, *, demo=False):
    """Return selected collections, preserving successes when another fails.

    Unsupported IP certificate queries never consume a provider request.
    IPv6 uses DNS AAAA reverse search; Host Summary currently documents only
    IPv4 and domains, so IPv6 Host Summary is explicitly unsupported.
    """
    demo=validate_demo(demo)
    host, version = _host(host)
    if collections is None:
        collections = list(COLLECTIONS)
    if not isinstance(collections, list) or not collections or len(collections) > 3 or any(not isinstance(name, str) or name not in COLLECTIONS for name in collections) or len(set(collections)) != len(collections):
        raise ValueError('Select one or more distinct host, dns or certificates collections')
    _provider('netlas', config)
    requested=list(collections)
    omitted=[]
    if demo:
        eligible=[name for name in requested if not (name=='host' and version==6) and not (name=='certificates' and version)]
        if eligible:
            collections=eligible[:1]
            omitted=[name for name in requested if name not in collections]
    results = {'host': {}, 'dns': [], 'certificates': []}
    errors, failures = [], []
    successes, retry_after = 0, None
    requests_made=0
    for name in COLLECTIONS:
        if name not in collections:
            continue
        try:
            if name == 'host':
                if version == 6:
                    raise ValueError('Host Summary does not document IPv6 support; select DNS for reverse IPv6 records')
                requests_made+=1
                payload = _request('netlas', '/host/' + quote(host, safe='') + '/', config)
                results[name], truncated = _bounded(payload, config, 16384)
            else:
                if name == 'certificates' and version:
                    raise ValueError('Certificate-by-IP enrichment is unsupported; select a hostname to search certificate names')
                query = ('domain' if version is None else 'a' if version == 4 else 'aaaa') + ':"' + host + '"'
                if name == 'certificates':
                    query = 'certificate.names.keyword:"' + host + '"'
                requests_made+=1
                payload = _request('netlas', '/domains/' if name == 'dns' else '/certs/', config, params={'q': query, 'start': 0})
                rows = payload.get('items')
                if not isinstance(rows, list):
                    raise ValueError('Netlas returned a malformed result list')
                maximum=1 if demo else MAX_ROWS
                truncated = len(rows) > maximum
                bounded = []
                for row in rows[:MAX_ROWS]:
                    if not isinstance(row, dict) or not isinstance(row.get('data'), dict):
                        if demo:
                            errors.append(name+': skipped a malformed collection record in the received Demo page')
                            continue
                        raise ValueError('Netlas returned a malformed collection record')
                    item, clipped = _bounded(row, config, 1024)
                    truncated = truncated or clipped
                    bounded.append(item)
                    if demo:break
                if demo and rows and not bounded:raise ValueError('Netlas returned no usable records on the received Demo page')
                results[name] = bounded
            successes += 1
            if truncated:
                errors.append(name + ': metadata or result count exceeded the enrichment limit and was truncated')
        except ValueError as exc:
            failures.append(exc)
            errors.append(name + ': ' + str(exc))
            if isinstance(exc, ProviderRateLimit):
                retry_after = max(retry_after or 0, exc.retry_after)
    if not successes:
        limited = next((error for error in failures if isinstance(error, ProviderRateLimit)), None)
        if limited:
            raise ProviderRateLimit('netlas', retry_after) from None
        raise ValueError('Netlas enrichment failed: ' + '; '.join(errors)) from None
    if omitted:errors.append('Demo omitted additional enrichment collections: '+', '.join(omitted))
    result = {'results': results, 'errors': errors, 'partial': bool(errors)}
    if demo:
        result['omitted_collections']=omitted
        result.update(demo_metadata(provider='netlas',max_requests=1,max_results=1,requests_made=requests_made,selected_collection=collections[0],wire_page_size=1 if collections[0]=='host' else 20,page_size_adjustable=False))
    if retry_after is not None:
        result['retry_after'] = retry_after
    return result
