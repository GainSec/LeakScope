"""Bounded offline signals from declared API service/configuration origins."""
import ipaddress
import json
import re
from urllib.parse import urlsplit
from xml.etree import ElementTree

from .asset_names import normalize_domain

DOCUMENT_LIMIT = 1024 * 1024
SIGNAL_LIMIT = 64
ENTRY_LIMIT = 64
HTTP_PROTOCOLS = {'http', 'https'}
NETWORK_PROTOCOLS = HTTP_PROTOCOLS | {
    'ws', 'wss', 'amqp', 'amqps', 'mqtt', 'mqtts', 'kafka', 'kafka-secure',
    'stomp', 'stomps', 'redis', 'rediss', 'nats', 'tcp', 'tls', 'udp',
    'grpc', 'grpcs', 'jms', 'ibmmq', 'solace', 'pulsar', 'pulsar+ssl',
}
DEFAULT_PORTS = {'http': 80, 'https': 443, 'ws': 80, 'wss': 443}
PRIVATE_SUFFIXES = ('.localhost', '.local', '.internal', '.invalid', '.test', '.example', '.home.arpa', '.onion')
VERSION = re.compile(r'([23])\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?\Z')
WSDL_1 = 'http://schemas.xmlsoap.org/wsdl/'
WSDL_2 = 'http://www.w3.org/ns/wsdl'
SOAP_ADDRESSES = ('http://schemas.xmlsoap.org/wsdl/soap/', 'http://schemas.xmlsoap.org/wsdl/soap12/')


def _origin(value, *, protocol=None, authority=False, allowed=HTTP_PROTOCOLS):
    """Return a public declared origin, with secrets and paths discarded."""
    if not isinstance(value, str) or not value or len(value) > 4096:
        return None
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 or char in '{}<>$\\' for char in value):
        return None
    if protocol is not None and (not isinstance(protocol, str) or protocol.lower() not in allowed):
        return None
    protocol = protocol.lower() if protocol else ''
    if authority:
        if '://' in value or value.startswith('//'):
            return None
        value = (protocol + ':' if protocol else '') + '//' + value
    try:
        parsed = urlsplit(value)
        scheme = parsed.scheme.lower()
        if ((not scheme and not authority) or (scheme and scheme not in allowed)
                or (protocol and scheme != protocol) or not parsed.hostname):
            return None
        port = parsed.port
        if port is not None and not 1 <= port <= 65535:
            return None
        host = parsed.hostname.rstrip('.')
        if '%' in host:
            return None
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            host = normalize_domain(host)
            if '.' not in host or host.rsplit('.', 1)[1].isdigit() or host.endswith(PRIVATE_SUFFIXES):
                return None
        else:
            if not address.is_global:
                return None
            host = '[' + str(address) + ']' if address.version == 6 else str(address)
        if port == DEFAULT_PORTS.get(scheme):
            port = None
        origin = (scheme + '://' if scheme else '') + host + (':' + str(port) if port else '')
        return origin if len(origin) <= 128 else None
    except (ValueError, TypeError, UnicodeError):
        return None


def _oauth_fields(definitions, family):
    if not isinstance(definitions, dict):
        return
    for scheme in list(definitions.values())[:ENTRY_LIMIT]:
        if not isinstance(scheme, dict) or scheme.get('type') != 'oauth2':
            continue
        if family == 'swagger':
            flows = [scheme]
        else:
            configured = scheme.get('flows')
            if not isinstance(configured, dict):
                continue
            flows = [configured.get(name) for name in ('implicit', 'password', 'clientCredentials', 'authorizationCode')]
        for flow in flows:
            if not isinstance(flow, dict):
                continue
            for field, role in [('authorizationUrl', 'oauth_authorize'), ('tokenUrl', 'oauth_token')]:
                yield 'declaration.' + family + '.' + role, flow.get(field), None, False


def _json_fields(document):
    if document.get('swagger') == '2.0':
        host = document.get('host')
        base_path = document.get('basePath', '/')
        schemes = document.get('schemes', [''])
        if isinstance(host, str) and isinstance(base_path, str) and base_path.startswith('/') and isinstance(schemes, list):
            for scheme in schemes[:8]:
                if not isinstance(scheme, str):
                    continue
                yield 'declaration.swagger.server', host + base_path, scheme or None, True
        yield from _oauth_fields(document.get('securityDefinitions'), 'swagger')
        return
    openapi = document.get('openapi')
    if isinstance(openapi, str) and len(openapi) <= 40 and VERSION.fullmatch(openapi) and openapi.startswith('3.'):
        servers = document.get('servers')
        if isinstance(servers, list):
            for server in servers[:ENTRY_LIMIT]:
                if isinstance(server, dict):
                    yield 'declaration.openapi.server', server.get('url'), None, False
        components = document.get('components')
        if isinstance(components, dict):
            yield from _oauth_fields(components.get('securitySchemes'), 'openapi')
        return
    asyncapi = document.get('asyncapi')
    if not isinstance(asyncapi, str) or len(asyncapi) > 40 or not VERSION.fullmatch(asyncapi):
        return
    servers = document.get('servers')
    if not isinstance(servers, dict):
        return
    field = 'host' if asyncapi.startswith('3.') else 'url'
    for server in list(servers.values())[:ENTRY_LIMIT]:
        if not isinstance(server, dict):
            continue
        protocol = server.get('protocol')
        if not isinstance(protocol, str) or not protocol:
            continue
        value = server.get(field)
        authority = field == 'host' or isinstance(value, str) and '://' not in value
        yield 'declaration.asyncapi.server', value, protocol, authority


def _wsdl_fields(text):
    # DTDs/entities are unnecessary for endpoint declarations and are refused.
    if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
        return
    try:
        root = ElementTree.fromstring(text)
    except (ElementTree.ParseError, ValueError, RecursionError):
        return
    if root.tag == '{' + WSDL_1 + '}definitions':
        for namespace in SOAP_ADDRESSES:
            path = './{' + WSDL_1 + '}service/{' + WSDL_1 + '}port/{' + namespace + '}address'
            for node in root.findall(path)[:ENTRY_LIMIT]:
                yield 'declaration.wsdl.server', node.get('location'), None, False
    elif root.tag == '{' + WSDL_2 + '}description':
        path = './{' + WSDL_2 + '}service/{' + WSDL_2 + '}endpoint'
        for node in root.findall(path)[:ENTRY_LIMIT]:
            yield 'declaration.wsdl.server', node.get('address'), None, False


def _document_fields(text, *, wrapped=False):
    if text.startswith('<'):
        yield from _wsdl_fields(text)
        return
    try:
        document = json.loads(text)
    except (ValueError, RecursionError):
        return
    if not isinstance(document, dict):
        return
    if not wrapped and not {'openapi', 'swagger', 'asyncapi'}.intersection(document):
        # httpx exports a full response as body; body_preview can be truncated.
        status = document.get('status_code')
        body = document.get('body')
        if (type(status) is int and 100 <= status <= 599 and isinstance(body, str)
                and _origin(document.get('url'))):
            yield from _document_fields(body.lstrip(), wrapped=True)
            return
    yield from _json_fields(document)


def declaration_signals(data):
    """Return namespaced configuration origins, never inferred service access."""
    if not isinstance(data, bytes) or not data or len(data) > DOCUMENT_LIMIT:
        return []
    try:
        text = data.decode('utf-8-sig').lstrip()
    except UnicodeError:
        return []
    for _ in range(4):
        if not text.startswith('HTTP/'):
            break
        sections = re.split(r'\r?\n\r?\n', text, maxsplit=1)
        if len(sections) != 2:
            return []
        text = sections[1].lstrip()
    result = set()
    for kind, value, protocol, authority in _document_fields(text):
        allowed = NETWORK_PROTOCOLS if kind == 'declaration.asyncapi.server' else HTTP_PROTOCOLS
        origin = _origin(value, protocol=protocol, authority=authority, allowed=allowed)
        if origin:
            result.add((kind, origin))
    return sorted(result)[:SIGNAL_LIMIT]
