"""Sending out: web requests and files. Every send is counted and noted with the run."""
import ipaddress
import socket
from urllib.parse import urlsplit
from leakscope.build.kinds import DOMAIN_FIELDS, ENDPOINT_FIELDS, RESPONSE_FIELDS, placeholders, render
from .base import Block, BlockError, Field, Cost, Done, register

BOTH = ('endpoint', 'domain')
MAX_BODY = 64 * 1024


def _row(item):
    return {'kind': item.get('kind'), **(item.get('fields') or {})}


def _internal(host):
    """True when a host resolves to an address inside a private, loopback, link-local or reserved range."""
    try: addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except (socket.gaierror, UnicodeError): raise ValueError('could not be resolved') from None
    for address in addresses:
        ip = ipaddress.ip_address(address.split('%')[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified: return True
    return False


@register
class WebRequest(Block):
    key, name, group, reach, takes, gives = 'web_request', 'Web request', 'send', 'send', BOTH, 'same'
    says = 'Sends an HTTP request with any method, address, headers and body, built from text and values. The reply becomes {{response_status}} and {{response_body}}.'
    fields = (Field('method', 'Method', 'choice', default='POST', choices=tuple((m, m) for m in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE'))),
              Field('url', 'Address', 'text', required=True, template=True, help='http or https. With a value it is sent once per item; without, once.'),
              Field('headers', 'Headers', 'text', template=True, help='One per line, as Name: value. Stored with the workflow as written.'),
              Field('body', 'Body', 'text', template=True),
              Field('allow_internal', 'Allow internal addresses', 'flag', default=False, help='Off: requests to loopback, link-local and private-network addresses are refused.'),
              Field('maximum', 'At most', 'number', default=50, minimum=1, maximum=500, help='Requests per run for this step.'))

    adds_fixed = RESPONSE_FIELDS

    def clean(self, settings):
        cleaned = super().clean(settings)
        if not placeholders(cleaned['url']) and urlsplit(cleaned['url']).scheme not in ('http', 'https'): raise ValueError('Address must start with http:// or https://')
        for line in cleaned['headers'].splitlines():
            if line.strip() and ':' not in line: raise ValueError('Each header is written as Name: value')
        return cleaned

    def _send(self, method, url, headers, body, allow_internal):
        import requests
        parts = urlsplit(url)
        if parts.scheme not in ('http', 'https') or not parts.hostname: return 'refused: not an http address', ''
        try:
            if not allow_internal and _internal(parts.hostname): return 'refused: internal address', ''
        except ValueError as exc: return 'failed: ' + str(exc), ''
        try:
            # Redirects are not followed: a reply cannot steer the request somewhere that was not checked.
            with requests.request(method, url, headers=headers, data=body.encode() if body else None, timeout=15, allow_redirects=False, stream=True) as reply:
                text = reply.raw.read(MAX_BODY, decode_content=True).decode('utf-8', 'replace')
                return str(reply.status_code), text[:2000]
        except requests.RequestException as exc: return 'failed: ' + type(exc).__name__, ''

    def begin(self, ctx, items, settings, allowance):
        per_item = bool(placeholders(settings['url']) or placeholders(settings['headers']) or placeholders(settings['body']))
        targets = items if per_item else items[:1]
        sent, refused, failed, skipped, cache, out = 0, 0, 0, 0, {}, []
        for item in items:
            if item not in targets and not per_item:
                out.append(item); continue
            url, heads, body = render(settings['url'], item), render(settings['headers'], item), render(settings['body'], item)
            if url is None or heads is None or body is None:
                skipped += 1; out.append(item); continue
            key = (url, heads, body)
            if key not in cache:
                if sent + refused + failed >= settings['maximum']:
                    skipped += 1; out.append(item); continue
                headers = {name.strip(): value.strip() for name, _, value in (line.partition(':') for line in heads.splitlines()) if name.strip()}
                cache[key] = self._send(settings['method'], url, headers, body, settings['allow_internal'])
                status = cache[key][0]
                if status.startswith('refused'): refused += 1
                elif status.startswith('failed'): failed += 1
                else: sent += 1
            status, text = cache[key]
            out.append({**item, 'fields': {**(item.get('fields') or {}), 'response_status': status, 'response_body': text}})
        if not per_item and len(out) > 1 and out[0].get('fields', {}).get('response_status'):
            first = out[0]['fields']
            out = [{**item, 'fields': {**(item.get('fields') or {}), 'response_status': first['response_status'], 'response_body': first['response_body']}} for item in out]
        note = str(sent) + ' sent'
        if refused: note += ' · ' + str(refused) + ' refused (internal or not http)'
        if failed: note += ' · ' + str(failed) + ' failed'
        if skipped: note += ' · ' + str(skipped) + ' not sent (empty value or over the step limit)'
        return Done(out, note=note, left_out=0)


COLUMNS = {'endpoint': ('address', 'port', 'protocol', 'technology', 'title', 'tags', 'last_seen'), 'domain': ('domain', 'root', 'source', 'status', 'confidence')}


def _cell(value):
    text = ', '.join(str(part) for part in value) if isinstance(value, (list, tuple)) else '' if value is None else str(value)
    return ' '.join(text.split())


def _columns(items):
    """The main fields of the kind, then any values the workflow named, in first-seen order."""
    base = COLUMNS.get(items[0].get('kind'), ())
    known = set(ENDPOINT_FIELDS) | set(DOMAIN_FIELDS)
    extra = list(dict.fromkeys(key for item in items for key in (item.get('fields') or {}) if key not in known))
    return list(base) + extra


def markdown(title, items):
    columns = _columns(items)
    lines = ['# ' + _cell(title), '', str(len(items)) + ' item' + ('' if len(items) == 1 else 's'), '',
             '| ' + ' | '.join(columns) + ' |', '| ' + ' | '.join('---' for _ in columns) + ' |']
    for item in items:
        fields = item.get('fields') or {}
        lines.append('| ' + ' | '.join(_cell(fields.get(column)).replace('|', '\\|') for column in columns) + ' |')
    return '\n'.join(lines) + '\n'


def plain(items):
    """One item per line: the address and port or the domain, then the remaining main fields, tab-separated."""
    lines = []
    for item in items:
        fields = item.get('fields') or {}
        if item.get('kind') == 'value':
            lines.append('\t'.join(_cell(value) for value in fields.values()).rstrip()); continue
        if item.get('kind') == 'endpoint': first, rest = _cell(fields.get('address')) + ':' + _cell(fields.get('port')), ('technology', 'title')
        else: first, rest = _cell(fields.get('domain')), ('source', 'status')
        lines.append('\t'.join([first] + [_cell(fields.get(key)) for key in rest]).rstrip())
    return '\n'.join(lines) + '\n'


@register
class ExportFile(Block):
    key, name, group, reach, takes, gives = 'export_file', 'Export file', 'send', 'local', BOTH + ('value',), 'same'
    says = 'Writes what flows through to a CSV, Markdown or text file you can download from the run for a day.'
    fields = (Field('format', 'Format', 'choice', default='csv', choices=(('csv', 'CSV'), ('md', 'Markdown table'), ('txt', 'Plain text, one per line'), ('json', 'JSON'))),)

    def begin(self, ctx, items, settings, allowance):
        from leakscope.exporters.storage import write_export_file, write_export_text
        if not items: return Done(items, note='Nothing to export')
        rows = items[:5000]
        try:
            if settings['format'] == 'md': written = write_export_text(markdown(ctx.workflow_name, rows), 'md')
            elif settings['format'] == 'txt': written = write_export_text(plain(rows), 'txt')
            else: written = write_export_file([_row(item) for item in rows], settings['format'])
        except ValueError as exc: raise BlockError(str(exc)) from None
        return Done(items, note=str(len(rows)) + ' rows · download: /export/download/' + written['token'], left_out=max(0, len(items) - 5000))
