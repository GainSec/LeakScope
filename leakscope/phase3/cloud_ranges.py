"""Local longest-prefix lookups against explicitly refreshed official feeds."""
from functools import lru_cache
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import tempfile
import time

import requests
from django.conf import settings
from django.utils import timezone

FEEDS = {
    'aws': ('Amazon Web Services', 'https://ip-ranges.amazonaws.com/ip-ranges.json'),
    'gcp': ('Google Cloud', 'https://www.gstatic.com/ipranges/cloud.json'),
    'cloudflare': ('Cloudflare', 'https://api.cloudflare.com/client/v4/ips'),
    'github': ('GitHub', 'https://api.github.com/meta'),
}
MAX_BYTES = 8 * 1024 * 1024
MAX_PREFIXES = 100000
BASIS = ('Matches use a local snapshot of vendor-published network ranges. They describe a published network/service range, '
         'not customer ownership, a verified running service or physical location. Vendor lists omit some services and BYOIP ranges; '
         'feed dates can differ from saved observation dates. A missing match is unknown.')


def _directory():
    return Path(settings.DATA_DIR) / 'reference-feeds'


def _prefixes(provider, data):
    if not isinstance(data, dict):
        raise ValueError('Expected a feed object')
    records = []
    if provider == 'aws':
        if not isinstance(data.get('prefixes'), list) or not isinstance(data.get('ipv6_prefixes'), list):
            raise ValueError('Invalid AWS range schema')
        for row in data['prefixes'] + data['ipv6_prefixes']:
            if isinstance(row, dict):
                records.append((row.get('ip_prefix') or row.get('ipv6_prefix'), row.get('service', ''), row.get('region', '')))
    elif provider == 'gcp':
        if not isinstance(data.get('prefixes'), list):
            raise ValueError('Invalid Google Cloud range schema')
        for row in data['prefixes']:
            if isinstance(row, dict):
                records.append((row.get('ipv4Prefix') or row.get('ipv6Prefix'), row.get('service', 'Google Cloud'), row.get('scope', '')))
    elif provider == 'cloudflare':
        data = data.get('result')
        if not isinstance(data, dict) or not isinstance(data.get('ipv4_cidrs'), list) or not isinstance(data.get('ipv6_cidrs'), list):
            raise ValueError('Invalid Cloudflare range schema')
        records = [(prefix, 'Cloudflare network', 'global') for prefix in data['ipv4_cidrs'] + data['ipv6_cidrs']]
    elif provider == 'github':
        for service in ('web', 'api', 'git', 'packages', 'pages', 'actions', 'hooks', 'dependabot', 'copilot'):
            if isinstance(data.get(service), list):
                records.extend((prefix, service, '') for prefix in data[service])
    else:
        raise ValueError('Unknown network feed')
    if not records or len(records) > MAX_PREFIXES:
        raise ValueError('Invalid range feed size')
    normalized = []
    for prefix, service, region in records:
        try:
            network = ipaddress.ip_network(prefix, strict=False)
        except (ValueError, TypeError):
            continue
        if not network.is_global:
            continue
        normalized.append({'prefix': str(network), 'service': str(service)[:100], 'region': str(region)[:100]})
    if not normalized:
        raise ValueError('No usable public prefixes')
    return normalized


def refresh(providers):
    if not isinstance(providers, list) or not providers or len(providers) > len(FEEDS) or any(not isinstance(key, str) or key not in FEEDS for key in providers):
        raise ValueError('Choose one to four official network feeds')
    directory = _directory()
    directory.mkdir(parents=True, exist_ok=True)
    result = []
    requests_made = 0
    deadline = time.monotonic() + 40
    for provider in dict.fromkeys(providers):
        url = FEEDS[provider][1]
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError('Refresh time budget exhausted')
            body = bytearray()
            requests_made += 1
            with requests.get(url, timeout=(min(4, remaining), min(8, remaining)), stream=True, allow_redirects=False,
                              headers={'User-Agent': 'LeakScope reference-feeds/1.0', 'Accept': 'application/json'}) as response:
                response.raise_for_status()
                if response.status_code != 200:
                    raise ValueError('Feed did not return HTTP 200')
                for chunk in response.iter_content(16384):
                    if time.monotonic() >= deadline:
                        raise ValueError('Refresh time budget exhausted')
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        raise ValueError('Feed exceeds response limit')
            data = json.loads(body)
            prefixes = _prefixes(provider, data)
            record = {'provider': provider, 'source_url': url, 'updated_at': timezone.now().isoformat(),
                      'published_at': str(data.get('createDate') or data.get('creationTime') or '')[:100],
                      'sha256': hashlib.sha256(body).hexdigest(), 'prefixes': prefixes}
            path = directory / (provider + '.json')
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode='w', dir=directory, prefix=provider + '-', delete=False) as stream:
                    temporary = Path(stream.name)
                    json.dump(record, stream, separators=(',', ':'))
                os.replace(temporary, path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            result.append({'provider': provider, 'status': 'updated', 'prefixes': len(prefixes), 'updated_at': record['updated_at']})
        except (requests.RequestException, OSError, ValueError, TypeError, RecursionError):
            result.append({'provider': provider, 'status': 'refresh_failed', 'error': 'Feed unavailable, invalid or exceeded refresh limits. Any previous snapshot is retained.'})
    return {'results': result, 'requests': requests_made, 'basis': BASIS}


@lru_cache(maxsize=16)
def _read(path, modified, size):
    if size > MAX_BYTES * 3:
        raise ValueError('Cached network feed exceeds limit')
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or not isinstance(data.get('updated_at'), str) or not isinstance(data.get('source_url'), str):
        raise ValueError('Invalid cached feed metadata')
    prefixes = data.get('prefixes')
    if not isinstance(prefixes, list) or not 0 < len(prefixes) <= MAX_PREFIXES:
        raise ValueError('Invalid cached feed')
    index = {}
    for row in prefixes:
        network = ipaddress.ip_network(row['prefix'], strict=False)
        key = (network.version, network.prefixlen, int(network.network_address))
        index.setdefault(key, []).append(row)
    data['index'] = index
    return data


def snapshots():
    result = {}
    for provider in FEEDS:
        path = _directory() / (provider + '.json')
        try:
            stat = path.stat()
            result[provider] = _read(str(path), stat.st_mtime_ns, stat.st_size)
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            continue
    return result


def dataset_status():
    data = snapshots()
    return [{'provider': provider, 'label': label, 'source_url': url,
             'status': 'ready' if provider in data else 'not_installed',
             'prefixes': len(data[provider]['prefixes']) if provider in data else 0,
             'updated_at': data[provider]['updated_at'] if provider in data else None,
             'published_at': data[provider].get('published_at') if provider in data else None}
            for provider, (label, url) in FEEDS.items()]


def lookup(ips):
    data = snapshots()
    result = []
    for value in list(dict.fromkeys(ips))[:2000]:
        try:
            address = ipaddress.ip_address(value)
        except (ValueError, TypeError):
            continue
        if not address.is_global:
            continue
        number = int(address)
        for provider, snapshot in data.items():
            for length in range(address.max_prefixlen, -1, -1):
                shift = address.max_prefixlen - length
                rows = snapshot['index'].get((address.version, length, (number >> shift) << shift))
                if rows:
                    result.append({'ip': str(address), 'provider': provider, 'provider_label': FEEDS[provider][0],
                                   'prefix': rows[0]['prefix'], 'services': sorted({row['service'] for row in rows}),
                                   'regions': sorted({row['region'] for row in rows if row['region']}),
                                   'updated_at': snapshot['updated_at'], 'source_url': snapshot['source_url']})
                    break
    return result
