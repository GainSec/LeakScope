"""Strict name identities and an offline Public Suffix List boundary."""
import ipaddress
import re
from functools import lru_cache

import tldextract

_SUFFIXES = tldextract.TLDExtract(cache_dir=None, suffix_list_urls=(), include_psl_private_domains=True)


def normalize_ip(value):
    if not isinstance(value, str) or len(value) > 64 or '%' in value:
        raise ValueError('Enter an IPv4 or IPv6 address without a URL, port or zone')
    try: return str(ipaddress.ip_address(value.strip().strip('[]')))
    except ValueError: raise ValueError('Invalid IP address') from None


def normalize_domain(value, allow_wildcard=False):
    if not isinstance(value, str) or not value or len(value) > 1000 or any(ord(c) < 33 or ord(c) == 127 for c in value):
        raise ValueError('Enter a domain name without a URL or whitespace')
    wildcard = value.startswith('*.')
    if wildcard:
        if not allow_wildcard: raise ValueError('A wildcard certificate name is not a concrete lookup target')
        value = value[2:]
    value = value.rstrip('.')
    try: name = value.encode('idna').decode('ascii').lower()
    except UnicodeError: raise ValueError('Invalid international domain name') from None
    if not name or len(name) + (2 if wildcard else 0) > 253 or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in name.split('.')):
        raise ValueError('Invalid domain name')
    try: ipaddress.ip_address(name)
    except ValueError: pass
    else: raise ValueError('Enter a domain name, not an IP address')
    return ('*.' if wildcard else '') + name


@lru_cache(maxsize=4096)
def registrable_domain(value):
    name = normalize_domain(value, allow_wildcard=True).removeprefix('*.')
    result = _SUFFIXES(name)
    if result.suffix and not result.domain: raise ValueError('A public suffix is not an organizational domain')
    return result.top_domain_under_public_suffix or name
