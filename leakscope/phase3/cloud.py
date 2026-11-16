"""Native managed-cloud discovery: deterministic planning and bounded read-only checks.

No third-party scanner is executed and no credentials are sent to targets.
"""
import hashlib
import http.client
import ipaddress
import itertools
import json
import re
import socket
import ssl
import threading
import time
from urllib.parse import urlencode, urlsplit
from xml.etree import ElementTree as ET

from .wire import _resolve

MAX_BODY = 65536
SOURCES = {
    'bbot': 'https://github.com/blacklanternsecurity/bbot/tree/stable/bbot/modules',
    'cloudbrute': 'https://github.com/0xsha/CloudBrute/tree/master/config/modules',
}


def _provider(label, templates, regions=('',), source='bbot'):
    return {'label': label, 'templates': templates, 'regions': list(regions), 'source': source}


PROVIDERS = {
    'aws': _provider('Amazon Web Services', [('bucket', '{name}.s3.amazonaws.com'), ('bucket', '{name}.s3.{region}.amazonaws.com'), ('application', '{name}.awsapps.com')],
        ('global', 'us-east-1', 'us-east-2', 'us-west-1', 'us-west-2', 'ca-central-1', 'eu-west-1', 'eu-west-2', 'eu-west-3', 'eu-central-1',
         'eu-north-1', 'eu-south-1', 'eu-south-2', 'ap-south-1', 'ap-south-2', 'ap-northeast-1', 'ap-northeast-2', 'ap-northeast-3',
         'ap-southeast-1', 'ap-southeast-2', 'ap-southeast-3', 'ap-southeast-4', 'sa-east-1', 'af-south-1', 'me-south-1', 'me-central-1', 'il-central-1')),
    'google': _provider('Google Cloud', [('bucket', '{name}.storage.googleapis.com'), ('application', '{name}.appspot.com')]),
    'azure': _provider('Microsoft Azure', [('account', '{name}.blob.core.windows.net'), ('application', '{name}.azurewebsites.net')]),
    'digitalocean': _provider('DigitalOcean', [('bucket', '{name}.{region}.digitaloceanspaces.com'), ('application', '{name}.ondigitalocean.app')],
        ('nyc3', 'ams3', 'sfo3', 'sgp1', 'fra1', 'syd1', 'blr1', 'lon1', 'tor1')),
    'hetzner': _provider('Hetzner', [('bucket', '{name}.{region}.your-objectstorage.com')], ('fsn1', 'nbg1', 'hel1')),
    'firebase': _provider('Firebase Realtime Database', [('database', '{name}.firebaseio.com'), ('database', '{name}.{region}.firebasedatabase.app')],
        ('europe-west1', 'asia-southeast1')),
    'alibaba': _provider('Alibaba Cloud OSS', [('bucket', '{name}.oss-{region}.aliyuncs.com')],
        ('cn-hangzhou', 'cn-shanghai', 'cn-beijing', 'cn-shenzhen', 'cn-hongkong', 'ap-southeast-1', 'ap-southeast-3', 'ap-southeast-5', 'ap-northeast-1', 'eu-central-1', 'eu-west-1', 'us-west-1', 'us-east-1'), 'cloudbrute'),
    'vultr': _provider('Vultr', [('bucket', '{name}.{region}.vultrobjects.com')], ('ewr1', 'sjc1', 'ams1', 'sgp1'), 'cloudbrute'),
    'linode': _provider('Akamai / Linode', [('bucket', '{name}.{region}.linodeobjects.com')],
        ('us-east-1', 'eu-central-1', 'ap-south-1', 'us-southeast-1'), 'cloudbrute'),
    'dreamhost': _provider('DreamHost DreamObjects', [('bucket', '{name}.s3.{region}.dream.io')], ('us-east-005',), 'provider-docs'),
    'scaleway': _provider('Scaleway', [('bucket', '{name}.s3.{region}.scw.cloud')], ('fr-par', 'nl-ams', 'pl-waw', 'it-mil'), 'provider-docs'),
}
DEFAULT_PROVIDERS = list(PROVIDERS)


def catalog():
    return {'providers': [{'key': key, 'label': value['label'], 'regions': [r for r in value['regions'] if r],
             'kinds': sorted({kind for kind, _ in value['templates']}), 'pattern_source': value['source']}
             for key, value in PROVIDERS.items()],
            'engine': 'LeakScope native cloud adapters', 'api_key_required': False,
            'limits': {'max_requests': 500, 'max_names': 10, 'max_rate_per_second': 2, 'body_bytes': MAX_BODY,
                       'run_seconds': 600},
            'limitations': ['Coverage is limited to the endpoint patterns and regions shown.',
                'Names do not establish ownership. Denied access does not verify resource identity.',
                'Checks contact cloud endpoints directly. No file contents are downloaded.']}


def _choices(value, allowed, label, maximum=30):
    if not isinstance(value, list) or not value or len(value) > maximum or any(not isinstance(x, str) or x not in allowed for x in value):
        raise ValueError('Invalid ' + label)
    return list(dict.fromkeys(value))


def validate_spec(data):
    allowed = {'names', 'providers', 'regions', 'kinds', 'permutations', 'max_requests', 'rate_per_second', 'verify'}
    if not isinstance(data, dict) or set(data) - allowed:
        raise ValueError('Unknown cloud discovery options')
    names = data.get('names')
    if not isinstance(names, list) or not 1 <= len(names) <= 10:
        raise ValueError('Enter 1–10 resource names or domain names')
    normalized = []
    for name in names:
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{1,61}[A-Za-z0-9]', name):
            raise ValueError('Names must contain 3–63 letters, digits, dots or hyphens; enter names, not URLs or IP ranges')
        name = name.lower()
        if '..' in name or '.-' in name or '-.' in name: raise ValueError('Invalid resource name')
        try: ipaddress.ip_address(name)
        except ValueError: pass
        else: raise ValueError('Enter resource names, not IP addresses')
        if name not in normalized: normalized.append(name)
    providers = _choices(data.get('providers', DEFAULT_PROVIDERS), PROVIDERS, 'cloud providers')
    regions = data.get('regions', {})
    if not isinstance(regions, dict) or set(regions) - set(providers): raise ValueError('Invalid region selection')
    regions = {p: _choices(values, PROVIDERS[p]['regions'], 'regions', 50) for p, values in regions.items()}
    kinds = _choices(data.get('kinds', ['storage']), ['storage', 'applications'], 'resource types')
    for key in ('permutations', 'verify'):
        if key in data and not isinstance(data[key], bool): raise ValueError('Invalid ' + key)
    maximum = data.get('max_requests', 100)
    if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= 500:
        raise ValueError('Request limit must be an integer from 1 to 500')
    rate = data.get('rate_per_second', 1)
    if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not 0.1 <= rate <= 2:
        raise ValueError('Request rate must be 0.1–2 per second')
    return {'names': normalized, 'providers': providers, 'regions': regions, 'kinds': kinds,
            'permutations': data.get('permutations', False), 'verify': data.get('verify', True),
            'max_requests': maximum, 'rate_per_second': rate}


def _names(spec):
    result = list(spec['names'])
    if spec['permutations']:
        for name in spec['names']:
            stem = name.split('.')[0]
            for base in dict.fromkeys((name.replace('.', '-'), stem)):
                for value in (base, *(base + '-' + env for env in ('dev', 'test', 'staging', 'prod', 'backup', 'assets'))):
                    if len(value) <= 63 and value not in result: result.append(value)
    return result


def plan(data):
    spec = validate_spec(data)
    groups, seen, invalid_names = [], set(), set()
    for provider in spec['providers']:
        definition = PROVIDERS[provider]
        candidates = []
        for name in _names(spec):
            for kind, template in definition['templates']:
                if ('applications' if kind == 'application' else 'storage') not in spec['kinds']: continue
                if provider == 'aws' and kind == 'bucket' and '{region}' not in template and 'global' not in spec['regions'].get(provider, definition['regions']): continue
                if kind == 'account' and not re.fullmatch(r'[a-z0-9]{3,24}', name):
                    invalid_names.add(provider + ': ' + name); continue
                # Virtual-host TLS certificates do not cover dotted bucket labels.
                # Keep the exact name on providers supporting path-style requests.
                if '.' in name and (kind != 'bucket' or provider not in ('aws', 'google', 'scaleway', 'dreamhost')):
                    invalid_names.add(provider + ': ' + name); continue
                for region in spec['regions'].get(provider, definition['regions']) if '{region}' in template else ['']:
                    if region == 'global': continue
                    host = template.format(name=name, region=region)
                    path = '/'
                    if '.' in name:
                        host = template.format(name='cloudplaceholder', region=region).removeprefix('cloudplaceholder.')
                        path = '/' + name + '/'
                    url = 'https://' + host + path
                    if len(host) > 253 or url in seen: continue
                    seen.add(url)
                    candidates.append({'provider': provider, 'name': name, 'region': region, 'kind': kind,
                        'url': url, 'pattern_source': definition['source']})
        groups.append(candidates)
    # Round-robin prevents a region-heavy provider from using the whole run limit.
    generated = [item for group in itertools.zip_longest(*groups) for item in group if item is not None]
    return {'spec': spec, 'network_contact': False, 'generated': len(generated),
            'limited': len(generated) > spec['max_requests'], 'candidates': generated[:spec['max_requests']],
            'skipped_names': sorted(invalid_names), 'engine': 'LeakScope native cloud adapters'}


def request_target(candidate, verify):
    if candidate['kind'] == 'application' or not verify: return 'HEAD', candidate['url']
    query = {'max-keys': '1'}
    if candidate['kind'] == 'account': query = {'comp': 'list', 'maxresults': '1'}
    if candidate['kind'] == 'database': return 'GET', candidate['url'].rstrip('/') + '/.json?shallow=true'
    return 'GET', candidate['url'] + '?' + urlencode(query)


def _blocked(host, config):
    entries = config.get('blacklist') or []
    if isinstance(entries, str): entries = entries.split(',')
    if not isinstance(entries, (tuple, list, set)): raise ValueError('Invalid blacklist configuration')
    for entry in entries:
        entry = str(entry).lower().strip().strip('[]').rstrip('.')
        if not entry: continue
        if host == entry or host.endswith('.' + entry): return True
        try:
            if ipaddress.ip_address(host) in ipaddress.ip_network(entry, strict=False): return True
        except ValueError: pass
    return False


def request_candidate(candidate, config, *, verify=True, timeout=6):
    method, url = request_target(candidate, verify)
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.port not in (None, 443) or parsed.username or parsed.password:
        raise ValueError('Invalid managed-cloud URL')
    host = parsed.hostname
    if _blocked(host, config): raise ValueError('Cloud destination is blacklisted')
    deadline = time.monotonic() + timeout
    answers = _resolve(host, 443, socket.SOCK_STREAM, deadline)
    for answer in answers:
        address = answer[4][0]
        ip = ipaddress.ip_address(address)
        if not ip.is_global or _blocked(address, config): raise ValueError('Cloud destination resolves to a private, special or blacklisted address')
    family, kind, protocol, _, destination = answers[0]
    connection = http.client.HTTPSConnection(host, timeout=timeout, context=ssl.create_default_context())
    sock = socket.socket(family, kind, protocol)
    active_socket = [sock]
    expired = threading.Event()
    def abort():
        expired.set()
        try: active_socket[0].shutdown(socket.SHUT_RDWR)
        except OSError: pass
        active_socket[0].close()
    timer = threading.Timer(max(0.01, deadline - time.monotonic()), abort)
    timer.daemon = True
    timer.start()
    body = bytearray()
    try:
        sock.settimeout(max(0.01, deadline - time.monotonic()))
        sock.connect(destination)
        tls_sock = connection._context.wrap_socket(sock, server_hostname=host, do_handshake_on_connect=False)
        active_socket[0] = tls_sock
        connection.sock = tls_sock
        connection.sock.settimeout(max(0.01, deadline - time.monotonic()))
        tls_sock.do_handshake()
        path = parsed.path + ('?' + parsed.query if parsed.query else '')
        connection.request(method, path, headers={'User-Agent': 'LeakScope-CloudDiscovery/1.0',
            'Accept-Encoding': 'identity', 'Connection': 'close'})
        response = connection.getresponse()
        if expired.is_set(): raise TimeoutError('Cloud response deadline exceeded')
        while method != 'HEAD' and not response.isclosed() and len(body) <= MAX_BODY:
            remaining = deadline - time.monotonic()
            if remaining <= 0: raise TimeoutError('Cloud response deadline exceeded')
            tls_sock.settimeout(remaining)
            chunk = response.read1(min(8192, MAX_BODY + 1 - len(body)))
            if not chunk: break
            body.extend(chunk)
        allowed = {'server', 'content-type', 'location', 'retry-after', 'x-amz-bucket-region', 'x-amz-request-id',
                   'x-ms-request-id', 'x-ms-error-code', 'x-oss-request-id', 'x-guploader-uploadid'}
        headers = {key.lower(): value[:1000] for key, value in response.getheaders() if key.lower() in allowed}
        return {'status': response.status, 'headers': headers, 'body': bytes(body[:MAX_BODY]),
                'truncated': len(body) > MAX_BODY, 'resolved_ip': destination[0], 'method': method, 'request_url': url}
    finally:
        timer.cancel()
        connection.close()
        active_socket[0].close()
        sock.close()


def classify(candidate, response):
    status, headers, body = response['status'], response.get('headers') or {}, response.get('body') or b''
    result = {'state': 'unverified', 'http_status': status, 'public_access': False, 'identity_verified': False,
              'sample_names': [], 'headers': headers, 'truncated': bool(response.get('truncated')),
              'response_sha256': hashlib.sha256(body).hexdigest(), 'response_bytes': len(body)}
    if status == 429:
        result.update(state='rate_limited', reason='Provider rate limit; remaining candidates for this provider are skipped'); return result
    if 300 <= status < 400:
        result.update(state='redirect', reason='Redirect recorded; destination was not followed'); return result
    if candidate['kind'] == 'application':
        result.update(state='reachable' if 200 <= status < 300 else ('not_found' if status == 404 else 'unverified'),
                      reason='Application endpoint response; storage permissions were not assessed')
        return result
    if response.get('truncated'):
        result['reason'] = 'Response exceeds the capture limit; listing could not be fully validated'; return result
    root, code = None, ''
    try:
        root = ET.fromstring(body)
        for item in root.iter(): item.tag = item.tag.split('}')[-1]
        code = root.findtext('Code') or ''
    except (ET.ParseError, ValueError): pass
    result['error_code'] = code[:100]
    if code in ('NoSuchBucket', 'NoSuchAccount', 'ResourceNotFound', 'ContainerNotFound'):
        result.update(state='not_found', reason='Provider reports that the requested resource does not exist')
    elif code in ('AccessDenied', 'AuthorizationFailure', 'AuthenticationFailed', 'AuthorizationPermissionMismatch') and status in (400, 401, 403):
        result.update(state='access_denied', reason='Provider denied this anonymous request; exact resource identity is unconfirmed')
    elif code in ('SlowDown', 'Throttling', 'TooManyRequests'):
        result.update(state='rate_limited', reason='Provider throttling; remaining candidates for this provider are skipped')
    elif status == 200 and root is not None and root.tag == 'ListBucketResult' and root.findtext('Name') == candidate['name']:
        result.update(state='public', public_access=True, identity_verified=True, reason='Anonymous bucket listing returned and bucket name matched',
                      sample_names=[x.text[:500] for x in root.findall('Contents/Key')[:5] if x.text])
    elif status == 200 and candidate['kind'] == 'account' and root is not None and root.tag == 'EnumerationResults':
        result.update(state='public', public_access=True, identity_verified=True, reason='Anonymous Azure container listing returned',
                      sample_names=[x.text[:500] for x in root.findall('Containers/Container/Name')[:5] if x.text])
    elif candidate['kind'] == 'database':
        try:
            value = json.loads(body)
            if status == 200 and (value is None or isinstance(value, dict) and 'error' not in value and all(v is True for v in value.values())):
                result.update(state='public', public_access=True, identity_verified=True,
                    reason='Anonymous Firebase shallow key listing returned', sample_names=list(value or {})[:5])
            elif status in (401, 403) and isinstance(value, dict) and value.get('error'):
                result.update(state='access_denied', reason='Firebase denied the anonymous request; identity is unconfirmed')
        except (ValueError, TypeError): pass
    if result['state'] == 'unverified':
        service_header = (headers.get('server') or '').lower()
        recognized = (('s3' in service_header or 'x-amz-request-id' in headers) and candidate['provider'] not in ('azure', 'firebase')) or (
            candidate['provider'] == 'azure' and 'x-ms-request-id' in headers) or (
            candidate['provider'] == 'alibaba' and 'x-oss-request-id' in headers) or (
            candidate['provider'] == 'google' and 'x-guploader-uploadid' in headers)
        if not body and recognized and status in (200, 204, 400, 401, 403):
            result.update(state='service_response', reason='Cloud service headers received; listing access and resource identity were not verified')
        else:
            result['reason'] = 'Response does not verify a public resource; an HTTP status alone is insufficient'
    return result
