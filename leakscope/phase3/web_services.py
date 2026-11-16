"""Explicit Firecrawl research of sourced web service candidates.

Search results and embedded assets are clues, not DNS facts, active-use proof or
ownership. This collector makes one bounded search request and no model calls.
"""
import hashlib
import ipaddress
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

from leakscope.intelligence.clients import _clean, _post, firecrawl_connection
from .usage_policy import validate_demo, demo_metadata
from .asset_names import normalize_domain, normalize_ip, registrable_domain

MAX_SOURCES = 3
MAX_TEXT = 24000
MAX_HTML = 64000
MAX_SERVICES = 32
MAX_EVIDENCE = 3

# Complete DNS suffixes and explicitly named products keep arbitrary substrings
# from being mistaken for service identities.
HOST_SERVICES = (
    ('stripe.com', 'Stripe', 'payments'),
    ('okta.com', 'Okta', 'identity'),
    ('auth0.com', 'Auth0', 'identity'),
    ('google-analytics.com', 'Google Analytics', 'analytics'),
    ('googletagmanager.com', 'Google Tag Manager', 'analytics'),
    ('fonts.googleapis.com', 'Google Fonts', 'web_assets'),
    ('cloudflare.com', 'Cloudflare', 'cdn'),
    ('cloudflareinsights.com', 'Cloudflare', 'analytics'),
    ('cloudfront.net', 'Amazon CloudFront', 'cdn'),
    ('fastly.net', 'Fastly', 'cdn'),
    ('jsdelivr.net', 'jsDelivr', 'cdn'),
    ('segment.com', 'Segment', 'analytics'),
    ('segment.io', 'Segment', 'analytics'),
    ('hotjar.com', 'Hotjar', 'analytics'),
    ('intercom.io', 'Intercom', 'support'),
    ('intercomcdn.com', 'Intercom', 'support'),
    ('zendesk.com', 'Zendesk', 'support'),
    ('zdassets.com', 'Zendesk', 'support'),
    ('hubspot.com', 'HubSpot', 'crm'),
    ('hs-scripts.com', 'HubSpot', 'crm'),
    ('salesforce.com', 'Salesforce', 'crm'),
    ('sentry.io', 'Sentry', 'monitoring'),
    ('datadoghq.com', 'Datadog', 'monitoring'),
    ('datadoghq-browser-agent.com', 'Datadog', 'monitoring'),
    ('newrelic.com', 'New Relic', 'monitoring'),
    ('nr-data.net', 'New Relic', 'monitoring'),
    ('recaptcha.net', 'Google reCAPTCHA', 'bot_protection'),
    ('hcaptcha.com', 'hCaptcha', 'bot_protection'),
    ('youtube.com', 'YouTube', 'video'),
    ('youtube-nocookie.com', 'YouTube', 'video'),
    ('vimeo.com', 'Vimeo', 'video'),
    ('vimeocdn.com', 'Vimeo', 'video'),
)
TEXT_SERVICES = (
    ('Stripe', r'Stripe', 'payments'), ('Okta', r'Okta', 'identity'),
    ('Auth0', r'Auth0', 'identity'), ('Cloudflare', r'Cloudflare', 'cdn'),
    ('Google Cloud', r'Google\s+Cloud', 'cloud'),
    ('Amazon Web Services', r'Amazon\s+Web\s+Services|AWS', 'cloud'),
    ('Microsoft Azure', r'Microsoft\s+Azure|Azure', 'cloud'),
    ('Google Workspace', r'Google\s+Workspace', 'email'),
    ('Microsoft 365', r'Microsoft\s+365|Office\s+365', 'email'),
    ('SendGrid', r'SendGrid', 'email'), ('Mailgun', r'Mailgun', 'email'),
    ('Salesforce', r'Salesforce', 'crm'), ('HubSpot', r'HubSpot', 'crm'),
    ('Zendesk', r'Zendesk', 'support'), ('Intercom', r'Intercom', 'support'),
    ('Sentry', r'Sentry', 'monitoring'), ('Datadog', r'Datadog', 'monitoring'),
    ('New Relic', r'New\s+Relic', 'monitoring'),
    ('Google Analytics', r'Google\s+Analytics', 'analytics'),
    ('Google Tag Manager', r'Google\s+Tag\s+Manager', 'analytics'),
    ('Segment', r'Segment', 'analytics'), ('Hotjar', r'Hotjar', 'analytics'),
)
RELATION_CUE = re.compile(r'\b(?:uses?|using|powered\s+by|hosted\s+(?:on|by)|'
    r'integrat(?:es|ed|ion)\s+with|built\s+(?:on|with)|runs?\s+on|managed\s+by)\b', re.I)


def _public_url(value):
    if not isinstance(value, str) or not value or len(value) > 2000 or any(ord(c) < 33 or ord(c) == 127 for c in value):
        return None
    try:
        parts = urlsplit(value)
        if parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username or parts.password:
            return None
        if parts.port is not None and not 1 <= parts.port <= 65535: return None
        host = parts.hostname.rstrip('.').lower()
        try:
            address = ipaddress.ip_address(host)
            if not address.is_global: return None
            host = str(address)
        except ValueError:
            host = normalize_domain(host)
            if '.' not in host or host.endswith(('.localhost', '.local', '.internal')): return None
        netloc = '['+host+']' if ':' in host else host
        if parts.port is not None: netloc += ':'+str(parts.port)
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, ''))
    except (ValueError, TypeError):
        return None


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.dependencies = []
        self.text = []
        self._ignored = None

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag in {'script', 'style'}: self._ignored = tag
        attribute = 'src' if tag in {'script', 'iframe', 'embed', 'img', 'source'} else None
        if tag == 'link' and 'stylesheet' in (values.get('rel') or '').lower().split(): attribute = 'href'
        if attribute and values.get(attribute) and len(self.dependencies) < 64:
            self.dependencies.append((tag+'['+attribute+']', values[attribute], self.get_starttag_text()[:700]))

    def handle_endtag(self, tag):
        if tag == self._ignored: self._ignored = None

    def handle_data(self, data):
        if not self._ignored: self.text.append(data)


def _target(target, kind):
    if kind == 'ip': return normalize_ip(target)
    if kind == 'domain':
        value = normalize_domain(target)
        registrable_domain(value)
        return value
    if kind != 'organization': raise ValueError('Unsupported web research target kind')
    if not isinstance(target, str) or not target.strip() or len(target) > 200 or any(ord(c) < 32 or ord(c) == 127 for c in target):
        raise ValueError('Enter an organization name of at most 200 characters')
    return ' '.join(target.split())


def _same_site(host, target, kind):
    if kind == 'organization': return False
    if kind == 'ip': return host == target
    return host == target or host.endswith('.'+target)


def _first_party_dependency(host, target, kind):
    if _same_site(host, target, kind): return True
    if kind != 'domain': return False
    try: return registrable_domain(host) == registrable_domain(target)
    except ValueError: return False


def _anchored(text, target):
    return bool(re.search(r'(?<![\w.-])'+re.escape(target)+r'(?![\w.-])', text, re.I))


def research_services(target, config, *, target_kind='domain', max_requests=2, timeout=45, demo=False):
    """Return web candidates with citations, making at most one Firecrawl call.

    Search-returned HTML is parsed locally and is never executed. Only first-party
    pages contribute embedded dependencies; other pages need an explicit,
    target-anchored relationship statement. The shared HTTP helper caps response
    bytes and per-read timeout; the durable job supplies the outer task deadline.
    """
    demo=validate_demo(demo)
    source_limit=1 if demo else MAX_SOURCES
    if not isinstance(config, dict): raise ValueError('Invalid Firecrawl configuration')
    target = _target(target, target_kind)
    if isinstance(max_requests, bool) or not isinstance(max_requests, int) or not 0 <= max_requests <= 2:
        raise ValueError('Web service research permits at most two requests')
    if isinstance(timeout, bool) or not isinstance(timeout, (float, int)) or not 1 <= timeout <= 45:
        raise ValueError('Web service research timeout must be 1–45 seconds')
    observed_at = datetime.now(timezone.utc).isoformat()
    result = {'target':target, 'target_kind':target_kind, 'sources':[], 'services':[],
              'requests':0, 'errors':[], 'partial':False, 'observed_at':observed_at,
              'note':'Public web service candidates; not ownership or current-use proof.'}
    if demo:
        result.update(demo_metadata(source_limit=1,service_limit=1,provider_request_limit=1),partial=True)
        result['note']+=' Demo samples one source and at most one service candidate; additional research was not performed.'
    if target_kind == 'ip' and not ipaddress.ip_address(target).is_global:
        return dict(result, not_applicable=True, note='Private or reserved IP has no public web research scope.')
    if max_requests == 0:
        return dict(result, partial=True, errors=[{'source':'firecrawl', 'error':'Request budget is zero'}])
    deadline = time.monotonic() + timeout
    signals = {}

    def error(message):
        result['partial'] = True
        result['errors'].append({'source':'firecrawl', 'error':_clean(str(message), config)[:1000]})

    def add(provider, category, domain, source, excerpt, signal, confidence):
        if provider not in signals and len(signals) >= MAX_SERVICES:
            error('Service candidate limit reached'); return
        item = signals.setdefault(provider, {'provider':provider, 'category':category,
            'relation':'web_service_candidate', 'confidence':confidence, 'evidence':[]})
        if domain: item.setdefault('domain', domain)
        item['confidence'] = max(item['confidence'], confidence)
        proof = {'source_url':source['url'], 'excerpt':_clean(excerpt, config)[:700],
                 'signal':signal, 'observed_at':observed_at, 'method':'firecrawl_search'}
        if proof not in item['evidence'] and len(item['evidence']) < MAX_EVIDENCE: item['evidence'].append(proof)

    try:
        base, key = firecrawl_connection(config)
        query = '"'+target.replace('"','')+'" (integrations OR "powered by" OR "uses" OR services)'
        payload = {'query':_clean(query, config), 'limit':source_limit,
                   'scrapeOptions':{'formats':['markdown','html']}}
        result['requests'] = 1
        response = _post(base+'/search', payload, key, min(timeout,max(1,deadline-time.monotonic())), 'Firecrawl')
        if not isinstance(response, dict) or response.get('success') is False:
            raise ValueError('Firecrawl service search did not succeed')
        data = response.get('data')
        rows = data.get('web') if isinstance(data, dict) else data
        if not isinstance(rows, list): raise ValueError('Firecrawl returned an invalid service search result list')
        if len(rows) > source_limit: error('Search sources were limited to '+str(source_limit)+' result(s)')
        seen = set()
        for row in rows[:source_limit]:
            if not isinstance(row, dict): error('Malformed search source omitted'); continue
            url = _public_url(_clean(row.get('url',''), config))
            if not url or url in seen: continue
            seen.add(url)
            title = row.get('title') if isinstance(row.get('title'), str) else ''
            body = '\n'.join(row.get(k) for k in ('description','markdown','content') if isinstance(row.get(k), str))
            html = row.get('html') or row.get('rawHtml') or ''
            if not isinstance(html,str): html = ''
            if len(body) > MAX_TEXT or len(html) > MAX_HTML: error('Search source text was truncated')
            body, html = _clean(body[:MAX_TEXT], config), _clean(html[:MAX_HTML], config)
            page = _Page(); page.feed(html); page.close()
            text = '\n'.join([_clean(title[:300],config), body, ' '.join(page.text)])[:MAX_TEXT]
            dependency_excerpt = '\n'.join(item[2] for item in page.dependencies[:4])
            excerpt = (text+'\n'+dependency_excerpt).strip()[:6000]
            source = {'id':'S'+str(len(result['sources'])+1), 'url':url,
                      'title':_clean(title[:300],config), 'excerpt':excerpt,
                      'observed_at':observed_at, 'content_sha256':hashlib.sha256((body+'\n'+html).encode()).hexdigest()}
            result['sources'].append(source)
            source_host = urlsplit(url).hostname.rstrip('.').lower()
            first_party = _same_site(source_host,target,target_kind)
            if first_party:
                for signal,value,tag in page.dependencies:
                    asset_url = _public_url(urljoin(url,value))
                    if not asset_url: continue
                    host = urlsplit(asset_url).hostname.rstrip('.').lower()
                    if _first_party_dependency(host,target,target_kind): continue
                    known = next(((provider,category) for suffix,provider,category in HOST_SERVICES
                                  if host == suffix or host.endswith('.'+suffix)),None)
                    provider,category = known or (host,'web_dependency')
                    add(provider,category,host,source,tag,signal,60 if known else 50)
            for sentence in re.split(r'(?<=[.!?])\s+|\n+',text):
                for provider,pattern,category in TEXT_SERVICES:
                    for match in re.finditer(r'(?<!\w)(?:'+pattern+r')(?!\w)',sentence,re.I):
                        snippet = sentence[max(0,match.start()-180):match.end()+180]
                        if RELATION_CUE.search(snippet) and (first_party or _anchored(snippet,target)):
                            add(provider,category,None,source,snippet,'relationship_statement',45 if first_party else 35)
        if time.monotonic() > deadline: error('Web research deadline reached while processing the response')
    except (ValueError, TypeError) as exc:
        error(exc)
    result['services'] = list(signals.values())[:1] if demo else list(signals.values())
    return result
