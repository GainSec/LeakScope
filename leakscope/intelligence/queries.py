"""Typed provider queries. No provider requests are made by this module.

Mappings follow the public Shodan filter reference/query guide, ZoomEye's
official Python repository api.md (API v2), and Netlas response field reference.
An unavailable mapping is an error state, never permission to drop a predicate.
"""
import base64
import ipaddress
import re
from collections import OrderedDict
from urllib.parse import urlencode

from .device_queries import NATIVE_FIELDS

FIELDS = frozenset({'device', 'title', 'product', 'server', 'body', 'host', 'ip', 'port', 'country', 'asn', 'certificate.sha256', 'certificate.sha1', 'favicon.sha256', 'favicon.mmh3'}) | NATIVE_FIELDS
_MAP = {
    'shodan': {'device':'device', 'title':'http.title', 'product':'product', 'body':'http.html', 'host':'hostname', 'ip':'ip', 'port':'port', 'country':'country', 'asn':'asn', 'favicon.mmh3':'http.favicon.hash', 'certificate.sha1':'ssl.cert.fingerprint'},
    'zoomeye': {'device':'device', 'title':'title', 'product':'product', 'server':'http.header.server', 'body':'http.body', 'host':'hostname', 'ip':'ip', 'port':'port', 'country':'country', 'asn':'asn', 'favicon.mmh3':'iconhash', 'certificate.sha1':'ssl.cert.fingerprint'},
    'netlas': {'title':'http.title', 'server':'http.headers.server', 'body':'http.body', 'host':'host', 'ip':'ip', 'port':'port', 'country':'geo.country', 'asn':'whois.asn.number', 'favicon.sha256':'http.favicon.hash_sha256', 'certificate.sha256':'certificate.fingerprint_sha256', 'certificate.sha1':'certificate.fingerprint_sha1'},
}


def _quote(value, provider):
    value = str(value).replace('\\', '\\\\').replace('"', '\\"')
    if provider == 'zoomeye':
        value = value.replace('(', '\\(').replace(')', '\\)')
    return '"' + value + '"'


def _value(field, value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError('Signal values must be strings or integers')
    value = str(value).strip()
    if not value or len(value) > 1000 or any(ord(c)<32 or ord(c)==127 for c in value):
        raise ValueError('Signal values must contain 1–1000 printable characters')
    if field == 'port':
        if not value.isdecimal() or not 1 <= int(value) <= 65535:
            raise ValueError('Port must be between 1 and 65535')
        value = str(int(value))
    elif field == 'ip':
        try:value = str(ipaddress.ip_address(value))
        except ValueError:raise ValueError('Signal IP is not a valid address') from None
    elif field == 'country':
        if not re.fullmatch('[A-Za-z]{2}',value):raise ValueError('Country must be a two-letter code')
        value=value.upper()
    elif field == 'asn':
        value=value.upper().removeprefix('AS')
        if not value.isdecimal() or not 1 <= int(value) <= 4294967295:raise ValueError('Invalid ASN')
        value=str(int(value))
    elif field.endswith('.sha256'):
        if not re.fullmatch('[0-9a-fA-F]{64}',value):raise ValueError('SHA-256 signals require 64 hexadecimal characters')
        value=value.lower()
    elif field.endswith('.sha1'):
        if not re.fullmatch('[0-9a-fA-F]{40}', value): raise ValueError('SHA-1 signals require 40 hexadecimal characters')
        value=value.lower()
    elif field == 'favicon.mmh3':
        if not re.fullmatch('-?[0-9]+',value) or not -(2**31) <= int(value) < 2**31:raise ValueError('MMH3 signals require a signed 32-bit integer')
        value=str(int(value))
    return value


def _scope(scope):
    if scope is None:return {}
    if not isinstance(scope,dict) or set(scope)-{'network','country','exclude'}:raise ValueError('Unsupported scope field')
    out={}
    for key in ('network','country','exclude'):
        value=scope.get(key,'')
        if value is None:value=''
        if not isinstance(value,str) or len(value)>1000 or any(ord(c)<32 or ord(c)==127 for c in value):raise ValueError('Invalid scope value')
        value=value.strip()
        if not value:continue
        if key=='network':
            try:value=str(ipaddress.ip_network(value,strict=False)) if '/' in value else str(ipaddress.ip_address(value))
            except ValueError:raise ValueError('Scope network must be an IP address or CIDR') from None
        if key=='country':value=_value('country',value)
        out[key]=value
    return out


def _url(provider, query):
    if provider=='zoomeye':
        query=base64.b64encode(query.encode()).decode()
        base='https://www.zoomeye.ai/searchResult'
    else:base={'shodan':'https://www.shodan.io/search','netlas':'https://app.netlas.io/responses/'}[provider]
    return base+'?'+urlencode({'query' if provider=='shodan' else 'q':query})


def _scope_constraints(provider,scope):
    constraints=[]
    if scope.get('network'):
        network=scope['network'];field=('cidr' if provider=='zoomeye' else 'net') if '/' in network else 'ip'
        if provider=='netlas':field='ip'
        constraints.append(field+('=' if provider=='zoomeye' else ':')+_quote(network,provider))
    if scope.get('country'):
        field='geo.country' if provider=='netlas' else 'country'
        constraints.append(field+('=' if provider=='zoomeye' else ':')+_quote(scope['country'],provider))
    for excluded in filter(None,(s.strip() for s in scope.get('exclude','').split(','))):
        quoted=_quote(excluded,provider)
        constraints.append('-'+quoted if provider=='shodan' else ('http.body!='+quoted if provider=='zoomeye' else 'NOT http.body:'+quoted))
    return constraints


def compile_queries(signals, mode='all', scope=None):
    """Group alternate values per field; ALL combines field groups, ANY terms.

    Shodan only documents comma alternatives within one filter. Cross-field
    ANY is therefore unavailable there rather than being rewritten as AND.
    """
    if not isinstance(signals,list) or not 1 <= len(signals) <= 64:raise ValueError('Select 1–64 evidence signals')
    if mode not in ('all','any'):raise ValueError('Combination mode must be all or any')
    scope=_scope(scope)
    groups=OrderedDict()
    for signal in signals:
        if not isinstance(signal,dict) or not isinstance(signal.get('field'),str):raise ValueError('Each signal needs a field and value')
        field=signal['field']
        if len(field)>100:raise ValueError('Invalid signal field')
        value=_value(field,signal.get('value'))
        if value not in groups.setdefault(field,[]):groups[field].append(value)
    if any(field in NATIVE_FIELDS for field in groups):
        from .device_queries import compile_device_queries
        return compile_device_queries(groups,mode,scope)
    result={}
    for provider, mapping in _MAP.items():
        warnings=[]
        for field in groups:
            if field not in mapping:warnings.append(f'{field} has no verified equivalent for {provider}; it was not dropped.')
        if provider=='shodan':
            if mode=='any' and len(groups)>1:warnings.append('Shodan does not have a verified cross-field OR mapping. Use ALL or one signal field.')
            if any(',' in value for values in groups.values() for value in values):warnings.append('Literal commas cannot be safely distinguished from Shodan filter alternatives.')
        if warnings:
            result[provider]={'query':'','url':'','supported':False,'warnings':warnings}
            continue
        parts=[]
        for field,values in groups.items():
            field_name=mapping[field]
            if provider=='shodan':
                parts.append(field_name+':'+','.join(_quote('AS'+v if field=='asn' else v,provider) for v in values))
            else:
                operator='=' if provider=='zoomeye' else ':'
                terms=[field_name+operator+_quote(v,provider) for v in values]
                parts.append('('+(' || ' if provider=='zoomeye' else ' OR ').join(terms)+')' if len(terms)>1 else terms[0])
        and_join=' && ' if provider=='zoomeye' else (' ' if provider=='shodan' else ' AND ')
        any_join=' || ' if provider=='zoomeye' else ' OR '
        query=and_join.join(parts) if mode=='all' or provider=='shodan' else any_join.join(parts)
        constraints=_scope_constraints(provider,scope)
        if constraints:query=(query if provider=='shodan' else '('+query+')')+and_join+and_join.join(constraints)
        result[provider]={'query':query,'url':_url(provider,query),'supported':True,'warnings':[]}
    return result


def discovery_queries(provider, organization='', domain=''):
    """Exact provider queries that find an organization's hosts: by the name in certificates, and by host names under a domain.

    Shodan filter reference: `ssl` searches certificate data and `hostname` matches a domain and its subdomains; it has no
    filter for the certificate subject organization. ZoomEye search guide: `ssl="…"` and `hostname="…"`. Netlas field
    reference: `host:(example.com OR *.example.com)`. For the name, Netlas gets the quoted phrase alone, which searches its
    default fields: `certificate.subject.organization` is documented but was refused with HTTP 403 when run for real.
    """
    if provider not in _MAP: raise ValueError('Unknown provider')
    found = []
    if organization:
        name = _quote(_value('title', organization), provider)
        found.append(('organization', {'shodan': 'ssl:' + name, 'zoomeye': 'ssl=' + name, 'netlas': name}[provider]))
    if domain:
        domain = str(domain).strip().lower().rstrip('.')
        if not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}', domain): raise ValueError('Not a domain name')
        found.append(('domain', 'host:(' + domain + ' OR *.' + domain + ')' if provider == 'netlas' else _MAP[provider]['host'] + (':' if provider == 'shodan' else '=') + _quote(domain, provider)))
    return found
