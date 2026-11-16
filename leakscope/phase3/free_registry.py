"""Bounded, keyless registration evidence; no paid fallback or target scanning."""
import copy
import http.client
import ipaddress
import json
import re
import socket
import ssl
import threading
import time
from datetime import datetime, timezone
from urllib.parse import quote, urlencode, urljoin, urlsplit

import dns.exception
import dns.resolver
import tldextract

from .asset_names import normalize_domain
from .registry import _asn, _organization_names, _text

MAX_BYTES = 2 * 1024 * 1024
MAX_RECORDS = 100
TIMEOUT = 30
CACHE_TTL = 3600
BOOTSTRAP_URL = 'https://data.iana.org/rdap/dns.json'
_PUBLIC_SUFFIXES = tldextract.TLDExtract(cache_dir=None, suffix_list_urls=(), include_psl_private_domains=False)
_cache = {}
_cache_lock = threading.Lock()


class _NotFound(ValueError):
    pass


def _validated_url(url, allowed_hosts):
    if not isinstance(url, str) or len(url) > 2048 or any(ord(c) < 33 or ord(c) == 127 for c in url):
        raise ValueError('Registry returned an unsafe destination URL')
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.hostname not in allowed_hosts or
                parsed.username is not None or parsed.password is not None or
                parsed.port not in (None, 443) or parsed.fragment or '\\' in url or
                '%' in parsed.path or '//' in parsed.path or
                any(p in ('.', '..') for p in parsed.path.split('/'))):
            raise ValueError
        # IP literals can never be an authoritative registry destination.
        try:
            ipaddress.ip_address(parsed.hostname)
        except ValueError:
            pass
        else:
            raise ValueError
    except (ValueError, UnicodeError):
        raise ValueError('Registry destination is not an approved HTTPS endpoint') from None
    return url


def _public_ip(host, deadline=None):
    """Resolve once, reject mixed public/private answers, then pin the connection."""
    deadline = time.monotonic() + 4 if deadline is None else deadline
    try:
        resolver = dns.resolver.Resolver()
        ips = []
        for kind in ('A', 'AAAA'):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError('Registry DNS deadline exceeded')
            try:
                answer = resolver.resolve(host + '.', kind, lifetime=min(2, remaining), search=False)
            except dns.resolver.NoAnswer:
                continue
            ips.extend(item.address for item in answer)
    except (OSError, dns.exception.DNSException) as exc:
        raise ValueError('Registry DNS lookup failed; no automatic retry was made') from exc
    ips = list(dict.fromkeys(ips))
    if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
        raise ValueError('Registry DNS did not resolve exclusively to public addresses')
    return ips[0]


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address, timeout):
        super().__init__(host, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        # Connect to the vetted address, while preserving Host, SNI and certificate validation.
        raw = socket.create_connection((self.address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def _http_once(url, deadline):
    parsed = urlsplit(url)
    address = _public_ip(parsed.hostname, deadline)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError('Registry total deadline exceeded')
    conn = _PinnedHTTPS(parsed.hostname, address, min(5, remaining))
    try:
        path = parsed.path or '/'
        if parsed.query:
            path += '?' + parsed.query
        conn.request('GET', path, headers={'Accept': 'application/rdap+json, application/json',
                                          'Accept-Encoding': 'identity', 'User-Agent': 'LeakScope/keyless-registry'})
        response = conn.getresponse()
        headers = {k.lower(): v for k, v in response.getheaders()}
        if response.status in (301, 302, 303, 307, 308) or response.status == 404:
            return response.status, headers, b''
        try:
            hinted = int(headers.get('content-length', '0'))
        except ValueError:
            hinted = 0
        if hinted > MAX_BYTES:
            raise ValueError('Registry response exceeded the 2 MiB limit')
        if headers.get('content-encoding', 'identity').lower() not in ('', 'identity'):
            raise ValueError('Registry returned an unsupported compressed response')
        chunks, size = [], 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError('Registry total deadline exceeded')
            sock = conn.sock or getattr(getattr(response.fp, 'raw', None), '_sock', None)
            if sock is not None:
                sock.settimeout(min(2, remaining))
            chunk = response.read1(min(16384, MAX_BYTES - size + 1))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_BYTES:
                raise ValueError('Registry response exceeded the 2 MiB limit')
        if time.monotonic() >= deadline:
            raise ValueError('Registry total deadline exceeded')
        return response.status, headers, b''.join(chunks)
    except (OSError, http.client.HTTPException) as exc:
        raise ValueError('Registry request failed or timed out; no automatic retry was made') from exc
    finally:
        conn.close()


class _Budget:
    def __init__(self, maximum):
        self.maximum = maximum
        self.requests = 0
        self.cache_hits = 0
        self.sources = []
        self.deadline = time.monotonic() + TIMEOUT

    def get(self, url, allowed_hosts):
        original = _validated_url(url, allowed_hosts)
        with _cache_lock:
            cached = _cache.get(original)
        if cached and cached[0] > time.monotonic():
            self.cache_hits += 1
            self.sources.append({'requested_url': original, 'source_url': cached[2],
                                 'fetched_at': cached[3], 'cache_hit': True})
            return copy.deepcopy(cached[1]), cached[2]
        for hop in range(3):
            if self.requests >= self.maximum:
                raise ValueError('Keyless registry request budget exhausted')
            if time.monotonic() >= self.deadline:
                raise ValueError('Registry total deadline exceeded')
            self.requests += 1
            status, headers, body = _http_once(url, self.deadline)
            if status in (301, 302, 303, 307, 308):
                if hop >= 2:
                    raise ValueError('Registry redirect limit exceeded')
                url = _validated_url(urljoin(url, headers.get('location', '')), allowed_hosts)
                continue
            if status == 404:
                raise _NotFound('No public registration record was found')
            if not 200 <= status < 300:
                raise ValueError(f'Registry returned HTTP {status}')
            if len(body) > MAX_BYTES:
                raise ValueError('Registry response exceeded the 2 MiB limit')
            try:
                payload = json.loads(body.decode('utf-8'))
            except (ValueError, UnicodeError, RecursionError):
                raise ValueError('Registry returned invalid JSON') from None
            if not isinstance(payload, dict):
                raise ValueError('Registry returned an invalid JSON object')
            ttl = 86400 if original == BOOTSTRAP_URL else CACHE_TTL
            fetched_at = datetime.now(timezone.utc).isoformat()
            with _cache_lock:
                if len(_cache) >= 512:
                    _cache.pop(next(iter(_cache)))
                _cache[original] = (time.monotonic() + ttl, copy.deepcopy(payload), url, fetched_at)
            self.sources.append({'requested_url': original, 'source_url': url,
                                 'fetched_at': fetched_at, 'cache_hit': False})
            return payload, url
        raise ValueError('Registry redirect limit exceeded')


def _base_result(**fields):
    return dict(domains=[], networks=[], asns=[], ips=[], entities=[], requests=0,
                errors=[], partial=False, source='rdap', keyless=True, **fields)


def _finish(result, budget):
    result.update(requests=budget.requests, cached=budget.requests == 0 and budget.cache_hits > 0,
                  cache_hits=budget.cache_hits, collection_sources=budget.sources)
    observed = [source['fetched_at'] for source in budget.sources if source['requested_url'] != BOOTSTRAP_URL]
    if observed:
        result.setdefault('observed_at', max(observed))
    return result


def _error(result, source, exc):
    result['errors'].append({'source': source, 'error': str(exc)[:1000]})
    result['partial'] = True


def _list(value, limit=MAX_RECORDS):
    return value[:limit] if isinstance(value, list) else []


def _identity(item):
    fields = item.get('vcardArray')
    names, orgs, kind = [], [], ''
    for field in _list(fields[1] if isinstance(fields, list) and len(fields) == 2 else []):
        if not isinstance(field, list) or len(field) < 4:
            continue
        value = field[3]
        if isinstance(value, list):
            value = ' / '.join(_text(v) for v in value[:10] if isinstance(v, str))
        value = _text(value)
        if field[0] == 'fn' and value:
            names.append(value)
        elif field[0] == 'org' and value:
            orgs.append(value)
        elif field[0] == 'kind':
            kind = value
    return (orgs or names or [''])[0], kind


def _domain_authority(domain, bootstrap):
    services = bootstrap.get('services')
    if not isinstance(services, list):
        raise ValueError('IANA DNS bootstrap returned malformed services')
    matches = []
    for service in services[:2000]:
        if not isinstance(service, list) or len(service) != 2 or not all(isinstance(v, list) for v in service):
            continue
        for suffix in service[0][:100]:
            if isinstance(suffix, str) and (domain == suffix or domain.endswith('.' + suffix)):
                matches.append((len(suffix), service[1]))
    if not matches:
        raise ValueError('IANA DNS bootstrap has no authority for this domain suffix')
    choices = []
    for base in max(matches, key=lambda m: m[0])[1][:10]:
        try:
            host = urlsplit(base).hostname
            _validated_url(base, {host})
            if urlsplit(base).query:
                continue
            choices.append(base.rstrip('/') + '/domain/' + domain)
        except (ValueError, TypeError):
            continue
    if not choices:
        raise ValueError('IANA DNS bootstrap has no approved HTTPS domain authority')
    return choices[0], {urlsplit(url).hostname for url in choices}


def registration(domain, config=None):
    """Fetch a known domain's public registration, never perform reverse WHOIS."""
    target = normalize_domain(domain)
    extracted = _PUBLIC_SUFFIXES(target)
    if not extracted.suffix or not extracted.domain:
        raise ValueError('A registrable public domain name is required')
    root = extracted.top_domain_under_public_suffix
    result = _base_result(target=target, domain=root, registration={},
                          coverage='Public registration of this known domain; redacted registrants remain unknown.')
    budget = _Budget(4)
    try:
        bootstrap, _ = budget.get(BOOTSTRAP_URL, {'data.iana.org'})
        url, allowed = _domain_authority(root, bootstrap)
        payload, source = budget.get(url, allowed)
        observed_at = budget.sources[-1]['fetched_at']
        reported = normalize_domain(payload.get('ldhName') or payload.get('unicodeName'))
        if reported != root:
            raise ValueError('Registry domain did not match the requested domain')
        registrants, registrars, entities = [], [], []
        for item in _list(payload.get('entities')):
            if not isinstance(item, dict):
                continue
            name, _ = _identity(item)
            roles = [_text(r, 50) for r in _list(item.get('roles')) if isinstance(r, str)]
            identity = {'handle': _text(item.get('handle')), 'name': name, 'roles': roles,
                        'source': 'rdap', 'source_url': source, 'registration_only': True,
                        'observed_at': observed_at}
            entities.append(identity)
            if 'registrant' in roles and name and not re.search(r'redact|not disclosed|privacy|data protected', name, re.I):
                registrants.append(identity)
            if 'registrar' in roles:
                registrars.append(identity)
        servers = []
        for item in _list(payload.get('nameservers')):
            try:
                name = normalize_domain(item.get('ldhName') or item.get('unicodeName'))
                if name not in servers:
                    servers.append(name)
            except (ValueError, TypeError, AttributeError):
                continue
        events = [{'action': _text(e.get('eventAction'), 100), 'date': _text(e.get('eventDate'), 100)}
                  for e in _list(payload.get('events')) if isinstance(e, dict)]
        record = {'domain': root, 'handle': _text(payload.get('handle')), 'registrants': registrants,
                  'registrars': registrars, 'redacted': bool(payload.get('redacted')) or not registrants,
                  'status': [_text(s, 100) for s in _list(payload.get('status')) if isinstance(s, str)],
                  'nameservers': servers, 'events': events, 'source': 'rdap', 'source_url': source,
                  'registration_only': True, 'observed_at': observed_at}
        result.update(registration=record, entities=entities, status='found', observed_at=observed_at)
        item = {'name': root, 'registrant_organizations': [v['name'] for v in registrants],
                'source': 'rdap', 'source_url': source, 'registration_only': True, 'confidence': 80,
                'observed_at': observed_at}
        if registrants:
            item.update(organization=registrants[0]['name'], relation='registrant_organization')
        result['domains'].append(item)
    except _NotFound:
        result.update(status='not_found', reason='No public registration record was found')
    except (ValueError, TypeError, OverflowError) as exc:
        _error(result, 'rdap', exc)
    return _finish(result, budget)


def _common(name, names, source, handle, observed_at=None):
    matched = name.casefold() in {n.casefold() for n in names}
    return {'organization': name, 'matched_organization': matched, 'source': 'rdap',
            'source_url': source, 'entity_handle': handle, 'registration_only': True,
            'confidence': 80 if matched else 40, **({'observed_at': observed_at} if observed_at else {})}


def _add_network(result, item, common):
    start, end = ipaddress.ip_address(item.get('startAddress')), ipaddress.ip_address(item.get('endAddress'))
    if start.version != end.version or int(start) > int(end) or not start.is_global or not end.is_global:
        raise ValueError('Registry returned a non-public or invalid network range')
    for network in ipaddress.summarize_address_range(start, end):
        row = dict(common, cidr=str(network), start_address=str(start), end_address=str(end),
                   name=_text(item.get('name')), country=_text(item.get('country'), 2), relation='registered_to')
        if not any(n['cidr'] == row['cidr'] and n['organization'] == row['organization'] for n in result['networks']):
            if len(result['networks']) >= MAX_RECORDS:
                raise ValueError('Registry network result limit reached')
            result['networks'].append(row)


def _add_asn(result, item, common):
    first, last = _asn(item.get('startAutnum')), _asn(item.get('endAutnum'))
    if not first or first != last:
        raise ValueError('Registry ASN range was not expanded; only individual ASNs are accepted')
    if not any(a['asn'] == first and a['organization'] == common['organization'] for a in result['asns']):
        if len(result['asns']) >= MAX_RECORDS:
            raise ValueError('Registry ASN result limit reached')
        result['asns'].append(dict(common, asn=first, relation='registered_to'))


def _ripe_resources(result, payload, common, handle):
    if payload.get('errormessages'):
        raise ValueError('RIPE returned an incomplete or failed inverse search')
    objects = payload.get('objects')
    objects = objects.get('object') if isinstance(objects, dict) else None
    if not isinstance(objects, list):
        raise ValueError('RIPE returned a malformed registration result list')
    if len(objects) >= MAX_RECORDS:
        result['partial'] = True
    for item in objects[:MAX_RECORDS]:
        if not isinstance(item, dict):
            continue
        attrs = item.get('attributes')
        attrs = attrs.get('attribute') if isinstance(attrs, dict) else None
        values = {}
        for attr in _list(attrs):
            if isinstance(attr, dict) and isinstance(attr.get('name'), str):
                values.setdefault(attr['name'], []).append(_text(attr.get('value')))
        if handle not in values.get('org', []):
            continue
        kind = item.get('type')
        try:
            if kind == 'aut-num':
                number = _asn((values.get('aut-num') or [''])[0])
                _add_asn(result, {'startAutnum': number, 'endAutnum': number}, common)
                continue
            address = (values.get(kind) or [''])[0]
            if kind == 'inet6num':
                network = ipaddress.ip_network(address, strict=False)
                start, end = network.network_address, network.broadcast_address
            elif kind == 'inetnum':
                start, end = [v.strip() for v in address.split(' - ', 1)]
            else:
                continue
            _add_network(result, {'startAddress': start, 'endAddress': end,
                                  'name': (values.get('netname') or [''])[0],
                                  'country': (values.get('country') or [''])[0]}, common)
        except (ValueError, TypeError) as exc:
            _error(result, 'ripe', exc)


def discover_organization(name, config=None, *, aliases=None, max_requests=8):
    """Search bounded ARIN/RIPE registration candidates without provider credits.

    Domain RDAP cannot enumerate an organization's unknown domains. These RIR
    searches cover registered resources, not every cloud address it may use.
    """
    names = _organization_names(name, aliases)
    if any(any(c in value for c in '*?') for value in names):
        raise ValueError('Organization searches do not accept wildcard operators')
    if isinstance(max_requests, bool) or not isinstance(max_requests, int) or not 0 <= max_requests <= 8:
        raise ValueError('Keyless registration discovery permits at most eight requests')
    result = _base_result(organization=names[0],
        coverage='Bounded ARIN/RIPE registration candidates, not exhaustive ownership, cloud use or reverse-domain WHOIS.')
    budget = _Budget(max_requests)
    candidates, seen = [], set()
    # Searching may use at most half the requests, so some are always left to read what the searches found.
    searching = max(1, max_requests // 2)
    for query in names:
        for region, base in [('arin', 'https://rdap.arin.net/registry/'), ('ripe', 'https://rdap.db.ripe.net/')]:
            if budget.requests >= searching:
                result['partial'] = True
                break
            url = base + 'entities?' + urlencode({'fn': query + '*'})
            try:
                payload, source = budget.get(url, {urlsplit(base).hostname})
                rows = payload.get('entitySearchResults')
                if not isinstance(rows, list):
                    raise ValueError('Registry returned a malformed entity search result list')
                if len(rows) >= MAX_RECORDS or any('truncat' in _text(n.get('title')).lower() for n in _list(payload.get('notices')) if isinstance(n, dict)):
                    result['partial'] = True
                for row in rows[:MAX_RECORDS]:
                    if not isinstance(row, dict):
                        continue
                    holder, kind = _identity(row)
                    handle = _text(row.get('handle'), 128)
                    # Only organizations, not named individual contacts or arbitrary linked URLs.
                    if not holder or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', handle):
                        continue
                    if region == 'ripe' and not re.fullmatch(r'ORG-[A-Za-z0-9-]+-RIPE', handle, re.I):
                        continue
                    if region == 'arin' and (kind not in ('org', '') or handle.endswith('-ARIN')):
                        continue
                    key = (region, handle)
                    if key in seen:
                        continue
                    seen.add(key)
                    common = _common(holder, names, source, handle, budget.sources[-1]['fetched_at'])
                    result['entities'].append(dict(common, handle=handle, name=holder, registry=region,
                        relation='registration_candidate', ambiguous=not common['matched_organization']))
                    candidates.append((region, base, handle, holder))
            except _NotFound:
                continue
            except (ValueError, TypeError) as exc:
                _error(result, region, exc)
                if budget.requests >= budget.maximum or time.monotonic() >= budget.deadline:
                    break
        if budget.requests >= budget.maximum or time.monotonic() >= budget.deadline:
            result['partial'] = True
            break
    # ARIN customer entities may crowd out the actual organization handle in a search.
    # Holders registered under exactly the name asked for are followed up first: a prefix search on a short alias
    # can return many unrelated holders, which must not use up the requests before the real one is read.
    wanted = {value.casefold() for value in names}
    candidates.sort(key=lambda c: (c[2].startswith('C') and c[2][1:].isdigit(), c[3].casefold() not in wanted, c[0], c[2]))
    for region, base, handle, holder in candidates[:MAX_RECORDS]:
        try:
            if region == 'arin':
                url = base + 'entity/' + quote(handle, safe='')
                payload, source = budget.get(url, {'rdap.arin.net'})
                if payload.get('handle') != handle:
                    raise ValueError('Registry entity did not match the requested handle')
                actual_name, _ = _identity(payload)
                if actual_name:
                    holder = actual_name
                common = _common(holder, names, source, handle, budget.sources[-1]['fetched_at'])
                for collection, handler in [('networks', _add_network), ('autnums', _add_asn)]:
                    rows = payload.get(collection, [])
                    if not isinstance(rows, list):
                        raise ValueError('Registry entity returned malformed resources')
                    if len(rows) > MAX_RECORDS:
                        result['partial'] = True
                    for item in rows[:MAX_RECORDS]:
                        try:
                            if not isinstance(item, dict):
                                raise ValueError('Registry returned a malformed resource')
                            handler(result, item, common)
                        except (ValueError, TypeError) as exc:
                            _error(result, region, exc)
            else:
                params = [('source', 'ripe'), ('inverse-attribute', 'org'), ('query-string', handle),
                          ('type-filter', 'inetnum'), ('type-filter', 'inet6num'), ('type-filter', 'aut-num'),
                          ('flags', 'no-referenced'), ('limit', str(MAX_RECORDS))]
                url = 'https://rest.db.ripe.net/search.json?' + urlencode(params)
                payload, source = budget.get(url, {'rest.db.ripe.net'})
                common = dict(_common(holder, names, source, handle, budget.sources[-1]['fetched_at']), source='ripe_rest')
                _ripe_resources(result, payload, common, handle)
        except _NotFound:
            continue
        except (ValueError, TypeError) as exc:
            _error(result, region, exc)
            if budget.requests >= budget.maximum or time.monotonic() >= budget.deadline:
                break
    return _finish(result, budget)
