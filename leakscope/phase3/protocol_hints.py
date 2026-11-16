"""Protocol hints read from saved provider records.

A hint says which protocol a search provider's own evidence points to, so a
bounded native reader can be selected for a service on an unusual port. It is
provider-reported evidence: it never counts as a fresh check by LeakScope, and
a port number, search keyword or organization scope is never a hint.
"""
import re

# Values a provider or importer stores when it did not name a protocol.
PLACEHOLDER_SERVICES = frozenset({'', 'auto', 'unknown', 'unclassified', 'tcpwrapped'})
HINT_BASIS = 'indexed response signature; live identity unverified'
BANNER_FIELDS = ('data', 'banner', 'header')
MAX_BANNER = 8192


def is_placeholder(service):
    return str(service or '').strip().lower() in PLACEHOLDER_SERVICES


def _ssh(match, text):
    software, _, comment = match['software'].partition(' ')
    fields = {'protocol': 'SSH', 'protocol_version': match['version'], 'software': software[:200]}
    if comment.strip():
        fields['comment'] = comment.strip()[:200]
    return fields


def _ftp(match, text):
    return {'protocol': 'FTP', 'reply_code': 220, 'greeting': match['text'].strip()[:300]}


def _rsync(match, text):
    return {'protocol': 'rsync daemon', 'protocol_version': match['version']}


def _vnc(match, text):
    return {'protocol': 'RFB (VNC)', 'protocol_version': str(int(match['major'])) + '.' + str(int(match['minor']))}


def _rtsp(match, text):
    return {'protocol': 'RTSP', 'protocol_version': '1.0', 'status': int(match['status'])}


def _redis(match, text):
    version = re.search(r'(?m)^redis_version:([^\r\n]{1,60})', text)
    return {'protocol': 'Redis', **({'redis_version': version[1].strip()} if version else {})}


def _memcached(match, text):
    version = re.search(r'(?m)^STAT version ([^\r\n]{1,60})', text)
    return {'protocol': 'Memcached', **({'version': version[1].strip()} if version else {})}


def _mail(label):
    return lambda match, text: {'protocol': label, 'greeting': match[0].strip()[:300]}


# (service, pattern anchored at the first response bytes, decoder). Each pattern
# is a protocol's own identification form, never a port or product guess.
_SIGNATURES = (
    ('ssh', re.compile(r'^SSH-(?P<version>2\.0|1\.99|1\.5)-(?P<software>[\x21-\x7e][\x20-\x7e]{0,250})'), _ssh),
    ('rsync', re.compile(r'^@RSYNCD: (?P<version>[0-9]{1,3}(?:\.[0-9]{1,3})?)'), _rsync),
    ('vnc', re.compile(r'^RFB (?P<major>[0-9]{3})\.(?P<minor>[0-9]{3})'), _vnc),
    ('rtsp', re.compile(r'^RTSP/1\.0 (?P<status>[1-5][0-9]{2})'), _rtsp),
    ('redis', re.compile(r'^(?:(?:\$[0-9]+\r?\n)?# Server\r?\nredis_version:|-NOAUTH Authentication required|-DENIED Redis is running in protected mode)'), _redis),
    ('memcached', re.compile(r'^STAT (?:pid|uptime|time|version) [^\r\n]+\r?\n(?=[\s\S]*^STAT version )', re.M), _memcached),
    ('smtp', re.compile(r'^220[ -][^\r\n]{0,300}\bE?SMTP\b[^\r\n]{0,200}', re.I), _mail('SMTP')),
    ('ftp', re.compile(r'^220[ -](?P<text>[^\r\n]{0,300}ftp[^\r\n]{0,200})', re.I), _ftp),
    ('imap', re.compile(r'^\* OK [^\r\n]{0,300}\bIMAP', re.I), _mail('IMAP')),
    ('pop3', re.compile(r'^\+OK [^\r\n]{0,300}\bPOP3?\b', re.I), _mail('POP3')),
    ('telnet', re.compile(r'^(?:\xff[\xfb-\xfe][\s\S]){2,}'), lambda match, text: {'protocol': 'Telnet', 'negotiation': 'IAC option negotiation'}),
)
# A product or CPE name is the provider's own parse of a response it does not
# show, so it is reported as a weaker hint and never selects a reader alone.
_PRODUCTS = (
    ('ssh', ('openssh', 'dropbear', 'libssh')), ('ftp', ('vsftpd', 'proftpd', 'pure-ftpd', 'filezilla server')),
    ('mysql', ('mysql', 'mariadb')), ('postgresql', ('postgresql',)), ('redis', ('redis',)), ('mongo', ('mongodb',)),
    ('memcached', ('memcached',)), ('vnc', ('tightvnc', 'realvnc', 'tigervnc', 'x11vnc')), ('mqtt', ('mosquitto',)),
)
_LABELS = {'ssh': 'SSH', 'ftp': 'FTP', 'rsync': 'rsync', 'vnc': 'VNC', 'rtsp': 'RTSP', 'redis': 'Redis', 'memcached': 'Memcached',
           'smtp': 'SMTP', 'imap': 'IMAP', 'pop3': 'POP3', 'telnet': 'Telnet', 'mysql': 'MySQL', 'postgresql': 'PostgreSQL',
           'mongo': 'MongoDB', 'mqtt': 'MQTT'}


def service_label(service):
    return _LABELS.get(service, str(service or 'Unknown').upper() if len(str(service or '')) <= 4 else str(service))


def banner_text(metadata):
    """First retained provider response text, with the field that holds it."""
    if not isinstance(metadata, dict):
        return '', ''
    for field in BANNER_FIELDS:
        value = metadata.get(field)
        if isinstance(value, str) and value.strip('\x00 \r\n\t'):
            return value[:MAX_BANNER], field
    return '', ''


def decode_banner(text):
    """Return (service, decoded fields, matched text) for a recognized response."""
    if not isinstance(text, str) or not text:
        return None
    sample = text[:MAX_BANNER]
    for service, pattern, decoder in _SIGNATURES:
        match = pattern.match(sample)
        if match:
            return service, decoder(match, sample), match[0][:300]
    return None


def reader(service):
    """What LeakScope can do for this protocol; an absent reader is stated."""
    from .native import identity_capability
    if service == 'vnc':
        return {'implemented': True, 'action': 'screenshot', 'transports': ['tcp'], 'limitation': '',
                'operation': 'Display-only no-authentication framebuffer snapshot'}
    capability = identity_capability(service)
    if capability['implemented']:
        return {'implemented': True, 'action': 'identity', 'operation': capability['operation'],
                'transports': capability['transports'], 'limitation': capability['limitation']}
    return {'implemented': False, 'action': '', 'operation': '', 'transports': [],
            'limitation': capability['limitation'] or 'LeakScope has no native ' + service_label(service) +
            ' reader; only a TCP connection check or an explicit bounded TCP read applies.'}


def protocol_hint(data, provider=''):
    """Strongest hint in one saved observation, or None.

    `strong` hints come from the response text itself; `moderate` hints come
    from a provider's product or CPE label and are reported without routing.
    """
    data = data if isinstance(data, dict) else {}
    metadata = data.get('metadata')
    if not isinstance(metadata, dict):
        metadata = {}
    text, field = banner_text(metadata)
    decoded = decode_banner(text)
    if decoded:
        service, fields, evidence = decoded
        return {'service': service, 'label': service_label(service), 'strength': 'strong', 'basis': 'banner',
                'basis_label': 'The saved provider response starts with ' + service_label(service) + ' identification text',
                'evidence': evidence, 'field': field, 'provider': str(provider or metadata.get('_provider') or '')[:32],
                'decoded': fields, 'reader': reader(service), 'verified': False}
    names = [metadata.get('product')]
    for key in ('cpe', 'cpe23'):
        values = metadata.get(key)
        if isinstance(values, list):
            names.extend(values[:8])
    haystack = ' '.join(str(name).lower() for name in names if isinstance(name, str))[:2000]
    for service, needles in _PRODUCTS:
        found = next((needle for needle in needles if needle in haystack), None)
        if found:
            return {'service': service, 'label': service_label(service), 'strength': 'moderate', 'basis': 'product',
                    'basis_label': 'Provider product or CPE label names ' + found + '; no response text was saved to check it',
                    'evidence': str(metadata.get('product') or found)[:200], 'field': 'product',
                    'provider': str(provider or metadata.get('_provider') or '')[:32], 'decoded': {},
                    'reader': reader(service), 'verified': False}
    return None


def strongest_hint(observations):
    """Pick one hint from (provider, data) pairs, newest first; strong beats moderate."""
    best = None
    for provider, data in observations:
        if str(provider).startswith(('fetch:', 'native:', 'scan:', 'analysis:')):
            continue
        hint = protocol_hint(data, provider)
        if hint and hint['strength'] == 'strong':
            return hint
        best = best or hint
    return best


def routing_service(stored_service, observations):
    """Service a capture should use, with the hint that selected it (or None).

    A stored protocol always wins; a hint only fills a placeholder.
    """
    if not is_placeholder(stored_service):
        return stored_service, None
    hint = strongest_hint(observations)
    if hint and hint['strength'] == 'strong':
        return hint['service'], hint
    # "auto" is a provider's module name for "not identified", never a protocol.
    return '' if str(stored_service or '').strip().lower() == 'auto' else stored_service or '', hint
