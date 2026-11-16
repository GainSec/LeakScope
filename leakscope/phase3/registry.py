"""Explicit, bounded registration evidence; registration does not imply ownership.

RDAP contacts only IANA and the five RIR RDAP hosts. Netlas expansion makes one
first-page request per collection, using the existing credential-safe transport.
No discovered address or link is crawled, resolved, scanned, or automatically
expanded. This module has no ORM dependency.
"""
import ipaddress
import json
import re
import threading
import time
from urllib.parse import urlencode, urljoin, urlsplit

import requests
from urllib3.exceptions import HTTPError as Urllib3HTTPError
from urllib3.util import Timeout

from .providers import ProviderRateLimit, _redact, _request

MAX_RDAP_BYTES = 2 * 1024 * 1024
RDAP_TIMEOUT = 12
MAX_REDIRECTS = 2
BOOTSTRAP_TTL = 86400
RIR_HOSTS = frozenset(('rdap.arin.net', 'rdap.db.ripe.net', 'rdap.apnic.net',
                       'rdap.afrinic.net', 'rdap.lacnic.net'))
_bootstrap_cache = {}
_bootstrap_lock = threading.Lock()


def _text(value, limit=1000):
    return value.strip()[:limit] if isinstance(value, str) else ''


def _validated_url(url, *, bootstrap=False):
    if not isinstance(url, str) or len(url) > 2048 or any(ord(c) < 33 or ord(c) == 127 for c in url):
        raise ValueError('RDAP returned an unsafe destination URL')
    try:
        parsed = urlsplit(url)
        allowed = {'data.iana.org'} if bootstrap else RIR_HOSTS
        if (parsed.scheme != 'https' or parsed.hostname not in allowed or
                parsed.username is not None or parsed.password is not None or
                parsed.port not in (None, 443) or parsed.query or parsed.fragment or
                '%' in parsed.path or '\\' in url or '//' in parsed.path or
                any(part in ('.', '..') for part in parsed.path.split('/'))):
            raise ValueError
        if bootstrap and parsed.path not in ('/rdap/ipv4.json', '/rdap/ipv6.json'):
            raise ValueError
    except (ValueError, UnicodeError):
        raise ValueError('RDAP destination is not an approved HTTPS registry endpoint') from None
    return url


def _fetch_json(url, deadline, *, bootstrap=False):
    """Stream at most 2 MiB, rejecting every redirect outside trusted registries.

    read1 performs at most one underlying read, allowing the shared monotonic
    deadline to be checked even if a server continuously trickles response data.
    The per-read timeout also bounds a stalled socket by the remaining deadline.
    """
    _validated_url(url, bootstrap=bootstrap)
    session = requests.Session()
    session.trust_env = False
    try:
        for hop in range(MAX_REDIRECTS + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError('RDAP total deadline exceeded')
            response = None
            try:
                response = session.get(url, headers={'Accept': 'application/rdap+json, application/json',
                                                     'User-Agent': 'LeakScope/registry', 'Accept-Encoding': 'identity'},
                                       timeout=Timeout(total=remaining, connect=min(3, remaining), read=min(2, remaining)),
                                       verify=True, stream=True, allow_redirects=False)
                if response.status_code in (301, 302, 303, 307, 308):
                    if hop >= MAX_REDIRECTS:
                        raise ValueError('RDAP redirect limit exceeded')
                    url = _validated_url(urljoin(url, response.headers.get('Location', '')), bootstrap=bootstrap)
                    continue
                if not 200 <= response.status_code < 300:
                    raise ValueError(f'RDAP returned HTTP {response.status_code}')
                try:
                    size_hint = int(response.headers.get('Content-Length', '0'))
                except (TypeError, ValueError):
                    size_hint = 0
                if size_hint > MAX_RDAP_BYTES:
                    raise ValueError('RDAP response exceeded the 2 MiB limit')
                chunks, size = [], 0
                # urllib3.HTTPResponse.read1 reads once instead of filling a large
                # buffer indefinitely. Automatic decompression remains disabled,
                # so a compressed body is rejected as invalid JSON, not expanded.
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ValueError('RDAP total deadline exceeded')
                    connection = getattr(response.raw, '_connection', None)
                    sock = getattr(connection, 'sock', None)
                    if sock is None:
                        # HTTP/1.1 Connection: close responses may detach the
                        # connection while retaining the response's live socket.
                        fp = getattr(getattr(response.raw, '_fp', None), 'fp', None)
                        sock = getattr(getattr(fp, 'raw', None), '_sock', None)
                    if sock is not None:
                        sock.settimeout(min(2, remaining))
                    chunk = response.raw.read1(min(16384, MAX_RDAP_BYTES - size + 1))
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_RDAP_BYTES:
                        raise ValueError('RDAP response exceeded the 2 MiB limit')
                    chunks.append(chunk)
                if time.monotonic() >= deadline:
                    raise ValueError('RDAP total deadline exceeded')
                try:
                    payload = json.loads(b''.join(chunks).decode('utf-8'))
                except (ValueError, UnicodeError, RecursionError):
                    raise ValueError('RDAP returned invalid JSON') from None
                if not isinstance(payload, dict):
                    raise ValueError('RDAP returned an invalid JSON object')
                return payload, url
            finally:
                if response is not None:
                    response.close()
    except (requests.RequestException, Urllib3HTTPError, OSError) as exc:
        raise ValueError('RDAP request failed or timed out; no automatic retry was made') from exc
    finally:
        session.close()


def _bootstrap(address, deadline):
    version = address.version
    with _bootstrap_lock:
        cached = _bootstrap_cache.get(version)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    payload, _ = _fetch_json(f'https://data.iana.org/rdap/ipv{version}.json', deadline, bootstrap=True)
    if not isinstance(payload.get('services'), list):
        raise ValueError('IANA RDAP bootstrap returned malformed services')
    with _bootstrap_lock:
        _bootstrap_cache[version] = (time.monotonic() + BOOTSTRAP_TTL, payload)
    return payload


def _registry_url(address, bootstrap):
    matches = []
    for service in bootstrap['services'][:10000]:
        if not isinstance(service, list) or len(service) != 2 or not all(isinstance(v, list) for v in service):
            continue
        ranges, urls = service
        for value in ranges[:1000]:
            try:
                network = ipaddress.ip_network(value)
            except (TypeError, ValueError):
                continue
            if network.version == address.version and address in network:
                matches.append((network.prefixlen, urls))
    if not matches:
        raise ValueError('No matching public RDAP bootstrap allocation')
    _, urls = max(matches, key=lambda item: item[0])
    for url in urls[:10]:
        try:
            return _validated_url(url).rstrip('/') + '/ip/' + str(address)
        except ValueError:
            continue
    raise ValueError('IANA bootstrap has no approved HTTPS registry destination')


def _entity_organizations(payload):
    registrants, contacts = [], []
    for entity in payload.get('entities', [])[:100] if isinstance(payload.get('entities'), list) else []:
        if not isinstance(entity, dict):
            continue
        vcard = entity.get('vcardArray')
        if not isinstance(vcard, list) or len(vcard) != 2 or not isinstance(vcard[1], list):
            continue
        names, full_names = [], []
        for field in vcard[1][:100]:
            if not isinstance(field, list) or len(field) < 4:
                continue
            value = field[3]
            if isinstance(value, list):
                value = ' / '.join(_text(v) for v in value[:10] if isinstance(v, str))
            value = _text(value)
            if value and field[0] == 'org':
                names.append(value)
            elif value and field[0] == 'fn':
                full_names.append(value)
        organizations = names or full_names
        contacts.extend(organizations)
        if isinstance(entity.get('roles'), list) and 'registrant' in entity['roles']:
            registrants.extend(organizations)
    return list(dict.fromkeys(registrants)), list(dict.fromkeys(contacts))


def _asn(value):
    if isinstance(value, str) and re.fullmatch(r'(?:AS)?[0-9]{1,10}', value, re.I):
        value = int(value[2:] if value[:2].upper() == 'AS' else value)
    if isinstance(value, int) and not isinstance(value, bool) and 0 < value <= 4294967295:
        return value
    return None


def netblock_info(ip, config=None):
    """Look up an observed public IP's registered allocation; never sweep it."""
    from .asset_names import normalize_ip
    try:
        address = ipaddress.ip_address(normalize_ip(ip))
    except (ValueError, TypeError):
        raise ValueError('A valid IPv4 or IPv6 address is required') from None
    if '%' in str(address):
        raise ValueError('Scoped IP addresses are not supported')
    result = {'target': str(address), 'networks': [], 'asns': [], 'errors': [], 'partial': False}
    if not address.is_global:
        result.update(status='not_applicable', reason='No public allocation lookup for private or special-purpose IP addresses')
        return result
    deadline = time.monotonic() + RDAP_TIMEOUT
    try:
        url = _registry_url(address, _bootstrap(address, deadline))
        payload, source_url = _fetch_json(url, deadline)
        start, end = ipaddress.ip_address(payload.get('startAddress')), ipaddress.ip_address(payload.get('endAddress'))
        if start.version != address.version or end.version != address.version or not int(start) <= int(address) <= int(end):
            raise ValueError('RDAP allocation range does not contain the requested IP')
        organizations, contacts = _entity_organizations(payload)
        for network in ipaddress.summarize_address_range(start, end):
            result['networks'].append({'cidr': str(network), 'start_address': str(start), 'end_address': str(end),
                                       'name': _text(payload.get('name')), 'organization': organizations[0] if organizations else '',
                                       'organizations': organizations, 'contact_organizations': contacts, 'country': _text(payload.get('country'), 2),
                                       'source_url': source_url or url, 'source': 'rdap', 'relation': 'registered_to',
                                       'confidence': 80, 'registration_only': True})
        origins = payload.get('arin_originas0_originautnums', [])
        for value in origins[:100] if isinstance(origins, list) else []:
            number = _asn(value)
            if number:
                result['asns'].append({'asn': number, 'source': 'rdap', 'source_url': source_url or url,
                                       'relation': 'origin_asn', 'confidence': 80})
    except (ValueError, TypeError, OverflowError) as exc:
        result['errors'].append({'source': 'rdap', 'error': str(exc)[:1000]})
        result['partial'] = True
    return result


def _organization_names(name, aliases):
    if not isinstance(name, str) or not name.strip() or len(name) > 255 or any(ord(c) < 32 for c in name):
        raise ValueError('Organization name must contain 1–255 printable characters')
    if aliases is not None and (not isinstance(aliases, (list, tuple)) or len(aliases) > 20):
        raise ValueError('Provide at most 20 organization aliases')
    names = [name.strip()]
    for alias in aliases or []:
        if not isinstance(alias, str) or not alias.strip() or len(alias) > 255 or any(ord(c) < 32 for c in alias):
            raise ValueError('Organization aliases must contain 1–255 printable characters')
        if alias.strip().casefold() not in {n.casefold() for n in names}:
            names.append(alias.strip())
    return names


def _query(field, names):
    clauses = [field + ':"' + name.replace('\\', '\\\\').replace('"', '\\"') + '"' for name in names]
    return '(' + ' OR '.join(clauses) + ')' if len(clauses) > 1 else clauses[0]


def discover_organization(name, config, *, aliases=None, max_requests=2, demo=False):
    """Expand first-page indexed registrations, retaining the returned registrant.

    Broad index matches are candidates only. Actual returned organization labels
    remain separate from the query name and never establish generic ownership.
    """
    from .asset_names import normalize_domain
    from .usage_policy import validate_demo,demo_metadata
    demo=validate_demo(demo)
    names = _organization_names(name, aliases)
    if isinstance(max_requests, bool) or not isinstance(max_requests, int) or not 0 <= max_requests <= 2:
        raise ValueError('Organization discovery permits at most two provider requests')
    if not isinstance(config, dict):
        raise ValueError('Invalid provider configuration')
    if demo:max_requests=min(max_requests,1)
    result = {'organization': names[0], 'domains': [], 'networks': [], 'ips': [], 'asns': [],
              'requests': 0, 'errors': [], 'partial': max_requests < 2}
    seen_domains, seen_networks, seen_asns = set(), set(), set()
    choices = [('/whois_domains/', 'registrant.organization'), ('/whois_ip/', 'net.organization')]
    for path, field in choices[:max_requests]:
        query = _query(field, names)
        source_url = 'https://app.netlas.io/api' + path + '?' + urlencode({'q': query, 'start': 0})
        result['requests'] += 1
        try:
            payload = _request('netlas', path, config, params={'q': query, 'start': 0})
            rows = payload.get('items')
            if not isinstance(rows, list):
                raise ValueError('Netlas returned a malformed registration result list')
            total = payload.get('total')
            if isinstance(total, dict):
                total = total.get('value')
            if len(rows) >= 20 or (isinstance(total, (int, str)) and str(total).isdigit() and int(total) > len(rows)):
                result['partial'] = True
            retained_records=0
            for row in rows[:20]:
                if demo and retained_records>=1:break
                try:
                    raw = row.get('data') if isinstance(row, dict) else None
                    if not isinstance(raw, dict):
                        raise ValueError('Netlas returned a malformed registration record')
                    collection = raw.get('registrant' if path == '/whois_domains/' else 'net')
                    collection = collection if isinstance(collection, dict) else {}
                    organization = _redact(_text(collection.get('organization')), config)
                    matched = organization.casefold() in {n.casefold() for n in names}
                    common = {'organization': organization, 'matched_organization': matched, 'source': 'netlas',
                              'source_url': source_url, 'confidence': 80 if matched else 40,
                              'registration_only': True}
                    if path == '/whois_domains/':
                        try:
                            domain = normalize_domain(raw.get('domain'))
                        except (ValueError, TypeError):
                            domain = None
                        if not domain:
                            raise ValueError('Netlas returned an invalid registration domain')
                        identity = (domain, organization)
                        if identity not in seen_domains:
                            result['domains'].append(dict(common, name=domain, relation='registrant_organization'))
                            seen_domains.add(identity)
                            retained_records+=1
                        continue
                    cidrs = collection.get('cidr', [])
                    if isinstance(cidrs, str):
                        cidrs = [cidrs]
                    if not isinstance(cidrs, list):
                        raise ValueError('Netlas returned malformed network CIDRs')
                    if not cidrs and collection.get('start_ip') and collection.get('end_ip'):
                        try:
                            cidrs = [str(n) for n in ipaddress.summarize_address_range(ipaddress.ip_address(collection['start_ip']), ipaddress.ip_address(collection['end_ip']))]
                        except (ValueError, TypeError):
                            raise ValueError('Netlas returned an invalid registration range') from None
                    for value in cidrs[:100]:
                        try:
                            network = ipaddress.ip_network(value, strict=False)
                        except (ValueError, TypeError):
                            raise ValueError('Netlas returned an invalid registration CIDR') from None
                        if not network.network_address.is_global or not network.broadcast_address.is_global:
                            raise ValueError('Netlas returned a non-public registration CIDR')
                        identity = (str(network), organization)
                        if identity not in seen_networks:
                            result['networks'].append(dict(common, cidr=str(network), relation='registered_to',
                                                           name=_redact(_text(collection.get('name')), config),
                                                           country=_text(collection.get('country'), 2),
                                                           start_address=str(network.network_address), end_address=str(network.broadcast_address)))
                            seen_networks.add(identity)
                            if demo:break
                    retained_records+=1
                    asn_record = raw.get('asn')
                    if isinstance(asn_record, dict):
                        number = _asn(asn_record.get('asn', asn_record.get('number')))
                        asn_organization = _text(asn_record.get('organization') or asn_record.get('name'))
                    else:
                        number, asn_organization = _asn(asn_record), ''
                    if number and number not in seen_asns:
                        result['asns'].append({'asn': number, 'organization': _redact(asn_organization, config),
                                               'source': 'netlas', 'source_url': source_url, 'relation': 'indexed_asn', 'confidence': 80})
                        seen_asns.add(number)
                except ValueError as exc:
                    result['errors'].append({'source': 'netlas', 'collection': path, 'error': _redact(str(exc), config)[:1000]})
                    result['partial'] = True
        except ValueError as exc:
            result['errors'].append({'source': 'netlas', 'collection': path, 'error': _redact(str(exc), config)[:1000]})
            result['partial'] = True
            if isinstance(exc, ProviderRateLimit):
                result['retry_after'] = exc.retry_after
                break
    if demo:
        result['partial']=True
        result['omitted_collections']=[path for path,_ in choices[max_requests:]]
        result.update(demo_metadata(provider='netlas',max_requests=1,max_results=1,requests_made=result['requests'],wire_page_size=20,page_size_adjustable=False))
    return result
