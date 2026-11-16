"""Bounded native DNS enrichment and offline extraction of indexed names.

No search-provider calls, zone walking, guessed CNAME recursion, or DNS ANY.
Config is accepted for the common adapter interface; DNS uses system resolvers.
"""

from datetime import datetime, timedelta, timezone
import ipaddress
import math
import re
import time

import dns.exception
import dns.resolver
import dns.reversename

from .asset_names import normalize_domain, normalize_ip, registrable_domain
from .service_rules import detect_services


QUERY_TYPES = ('A', 'AAAA', 'CNAME', 'MX', 'NS', 'TXT', 'SOA', 'CAA', 'HTTPS', 'SVCB')
BASE_TYPES = ('MX', 'NS', 'TXT', 'SOA', 'CAA')
SRV_LABELS = ('_sip._tcp', '_sip._udp', '_sips._tcp', '_xmpp-client._tcp', '_xmpp-server._tcp', '_autodiscover._tcp')
DKIM_SELECTORS = ('selector1', 'selector2', 'google', 'default')
MAX_RECORDS = 256
MAX_RECORDS_PER_QUERY = 64
MAX_NAMES = 256


def _limits(max_queries, timeout):
    if isinstance(max_queries, bool) or not isinstance(max_queries, int) or not 0 <= max_queries <= 64:
        raise ValueError('DNS query budget must be an integer between 0 and 64')
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 30:
        raise ValueError('DNS timeout must be between 0 and 30 seconds')
    return max_queries, float(timeout)


def _name(value):
    return str(value).lower().rstrip('.')


def _record(owner, kind, item, ttl, observed):
    record = {'name': _name(owner), 'type': kind, 'value': item.to_text(), 'ttl': ttl,
              'observed_at': observed.isoformat(), 'expires_at': (observed + timedelta(seconds=ttl)).isoformat()}
    if kind in ('A', 'AAAA'):
        record['address'] = str(ipaddress.ip_address(item.address))
    elif kind in ('CNAME', 'NS', 'PTR'):
        record['target'] = _name(item.target)
    elif kind == 'MX':
        record.update(exchange=_name(item.exchange), preference=item.preference)
    elif kind == 'TXT':
        record['raw_value'] = record['value']
        record['value'] = b''.join(item.strings).decode('utf-8', errors='replace')
    elif kind == 'SOA':
        record.update(mname=_name(item.mname), rname=_name(item.rname), serial=item.serial,
                      refresh=item.refresh, retry=item.retry, expire=item.expire, minimum=item.minimum)
    elif kind == 'CAA':
        record.update(flags=item.flags, tag=item.tag.decode('ascii', errors='replace'),
                      issuer=item.value.decode('utf-8', errors='replace'))
    elif kind == 'SRV':
        record.update(priority=item.priority, weight=item.weight, port=item.port, target=_name(item.target))
    elif kind in ('HTTPS', 'SVCB'):
        record.update(priority=item.priority, target=_name(item.target) or '.')
    return record


class _DNSBudget:
    def __init__(self, target, max_queries, timeout):
        self.maximum, self.timeout = _limits(max_queries, timeout)
        self.deadline = time.monotonic() + self.timeout
        self.resolver = dns.resolver.Resolver()
        self.result = {'target': target, 'records': [], 'queries': 0, 'errors': [], 'partial': False,
                       'observed_at': datetime.now(timezone.utc).isoformat()}
        self.stopped = False

    def error(self, owner, kind, error):
        self.result['partial'] = True
        item = {'name': owner, 'type': kind, 'error': error}
        if item not in self.result['errors']:
            self.result['errors'].append(item)

    def query(self, owner, kind):
        if self.stopped:
            return None
        remaining = self.deadline - time.monotonic()
        if self.result['queries'] >= self.maximum or remaining <= 0:
            self.error(owner, kind, 'query_budget_exhausted' if remaining > 0 else 'time_budget_exhausted')
            self.stopped = True
            return None
        self.result['queries'] += 1
        try:
            answer = self.resolver.resolve(owner + '.', kind, lifetime=min(remaining, 3.0), search=False)
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            return []
        except dns.exception.Timeout:
            self.error(owner, kind, 'timeout')
            return None
        except dns.resolver.NoNameservers:
            self.error(owner, kind, 'no_nameservers')
            return None
        except (dns.exception.DNSException, OSError, ValueError):
            self.error(owner, kind, 'dns_error')
            return None
        rrset = answer.rrset
        if rrset is None:
            return []
        output = []
        observed = datetime.now(timezone.utc)
        for index, item in enumerate(rrset):
            if index >= MAX_RECORDS_PER_QUERY or len(self.result['records']) >= MAX_RECORDS:
                self.error(owner, kind, 'record_limit')
                if len(self.result['records']) >= MAX_RECORDS:
                    self.stopped = True
                break
            try:
                record = _record(rrset.name, kind, item, int(rrset.ttl), observed)
            except (AttributeError, ValueError, UnicodeError):
                self.error(owner, kind, 'malformed_record')
                continue
            # DNS wire messages are bounded, but individual text records can be large.
            if len(record['value']) > 8192:
                self.error(owner, kind, 'record_value_limit')
                continue
            record['query_name'] = owner
            output.append(record)
            self.result['records'].append(record)
        return output


def collect_dns(domain, config=None, *, max_queries=32, timeout=12):
    """Collect a fixed set of record types and named policy/service labels.

    Domain records retain their real owner. Mail/policy records at the registrable
    domain are additional evidence, never silently attributed to a subdomain.
    """
    target = normalize_domain(domain)
    if not target or target.startswith('*.'):
        raise ValueError('A concrete DNS hostname is required')
    base = registrable_domain(target) or target
    budget = _DNSBudget(target, max_queries, timeout)
    planned = [(target, kind) for kind in QUERY_TYPES]
    if base != target:
        planned.extend((base, kind) for kind in BASE_TYPES)
    planned.append(('_dmarc.' + base, 'TXT'))
    planned.extend((label + '.' + base, 'SRV') for label in SRV_LABELS)
    planned.extend((selector + '._domainkey.' + base, 'TXT') for selector in DKIM_SELECTORS)
    for owner, kind in planned:
        budget.query(owner, kind)
        if budget.stopped:
            break
    result = budget.result
    result['services'] = detect_services(result['records'])
    result['base_domain'] = base
    result['scope_note'] = 'Bounded named-label checks; not complete zone enumeration.'
    return result


def resolve_names(address, config=None, *, max_queries=12, timeout=10):
    """Resolve PTR names and confirm each against the original address family."""
    target = normalize_ip(address)
    if not target:
        raise ValueError('An IP address is required')
    budget = _DNSBudget(target, max_queries, timeout)
    result = budget.result
    result['names'] = []
    reverse = dns.reversename.from_address(target).to_text().rstrip('.')
    ptr_records = budget.query(reverse, 'PTR') or []
    seen = set()
    for ptr in ptr_records:
        try:
            name = normalize_domain(ptr['target'])
        except (ValueError, TypeError):
            continue
        if not name or name in seen or name.startswith('*.'):
            continue
        seen.add(name)
        error_count = len(result['errors'])
        forward = budget.query(name, 'AAAA' if ipaddress.ip_address(target).version == 6 else 'A')
        found = forward is not None and any(record.get('address') == target for record in forward)
        complete = forward is not None and len(result['errors']) == error_count
        confirmed = True if found else False if complete else None
        result['names'].append({'name': name, 'source': 'dns:ptr', 'forward_confirmed': confirmed,
                                'confirmation_status': 'unavailable' if confirmed is None else 'confirmed' if confirmed else 'mismatch',
                                'observed_at': ptr['observed_at'], 'ttl': ptr['ttl'], 'expires_at': ptr['expires_at']})
    return result


def extract_names(metadata, provider=''):
    """Read known provider fields only, preserving hostname/certificate provenance.

    Wildcards remain certificate evidence. The result is bounded to 256 distinct
    (name, source) pairs and this function performs no network activity.
    """
    if not isinstance(metadata, dict):
        return []
    provider = provider or metadata.get('_provider', '')
    provider = provider if provider in ('shodan', 'zoomeye', 'netlas', 'certstream') else 'provider'
    raw = metadata.get('data') if provider == 'netlas' and isinstance(metadata.get('data'), dict) else metadata
    result, seen = [], set()
    visited = 0

    def add(value, label, *, san=False, depth=0):
        nonlocal visited
        visited += 1
        if len(result) >= MAX_NAMES or visited > 2048 or depth > 6:
            return
        if isinstance(value, (list, tuple)):
            for entry in value[:MAX_NAMES]:
                add(entry, label, san=san, depth=depth + 1)
            return
        if san and isinstance(value, dict):
            for field in ('dns_names', 'dns', 'names'):
                add(value.get(field), label, depth=depth + 1)
            return
        if not isinstance(value, str) or len(value) > 8192:
            return
        if san:
            for name in re.findall(r'(?:^|[,\s])DNS:([^,\s]+)', value, flags=re.I)[:MAX_NAMES]:
                add(name, label, depth=depth + 1)
            if re.search(r'(?:^|[,\s])DNS:', value, flags=re.I):
                return
        try:
            name = normalize_domain(value, allow_wildcard=True)
        except (ValueError, TypeError):
            return
        source = provider + ':' + label
        if not name or (name, source) in seen:
            return
        seen.add((name, source))
        result.append({'name': name, 'source': source, 'wildcard': name.startswith('*.')})

    for field in ('hostnames', 'hostname', 'host', 'domain', 'domains'):
        add(raw.get(field), 'hostname')
    certs = []
    for field in ('certificate', 'cert', 'ssl', 'tls'):
        item = raw.get(field)
        if isinstance(item, dict):
            certs.append(item)
            for inner in ('cert', 'certificate', 'parsed'):
                if isinstance(item.get(inner), dict):
                    certs.append(item[inner])
    for cert in certs[:12]:
        for field in ('domains', 'names', 'dns_names', 'common_name', 'subject_cn', 'subject_alt_name', 'subject_alternative_name', 'subjectAltName'):
            add(cert.get(field), 'certificate', san=True)
        subject = cert.get('subject')
        if isinstance(subject, dict):
            for field in ('CN', 'cn', 'common_name'):
                add(subject.get(field), 'certificate')
        extensions = cert.get('extensions')
        if isinstance(extensions, dict):
            for field in ('subjectAltName', 'subject_alt_name', 'subject_alternative_name'):
                add(extensions.get(field), 'certificate', san=True)
        elif isinstance(extensions, list):
            for extension in extensions[:64]:
                if isinstance(extension, dict) and extension.get('name') in ('subjectAltName', 'subject_alt_name'):
                    add(extension.get('data') or extension.get('value'), 'certificate', san=True)
    indexed = raw.get('dns')
    if isinstance(indexed, dict):
        for field in ('host', 'hostname', 'name', 'ptr', 'cname', 'mx', 'ns'):
            add(indexed.get(field), 'indexed_dns')
    return result
