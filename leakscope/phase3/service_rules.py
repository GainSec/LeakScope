"""Conservative service signals; DNS configuration never establishes ownership."""

import re


# A suffix must be a complete DNS suffix, never an arbitrary substring.
RULES = {
    'CNAME': (
        ('cloudfront.net', 'Amazon CloudFront', 'cdn', 'uses_cdn', 90),
        ('azurewebsites.net', 'Microsoft Azure', 'hosting', 'uses_hosting', 85),
        ('azureedge.net', 'Microsoft Azure', 'cdn', 'uses_cdn', 85),
        ('fastly.net', 'Fastly', 'cdn', 'uses_cdn', 85),
        ('github.io', 'GitHub Pages', 'hosting', 'uses_hosting', 85),
        ('herokudns.com', 'Heroku', 'hosting', 'uses_hosting', 85),
        ('vercel-dns.com', 'Vercel', 'hosting', 'uses_hosting', 85),
        ('netlify.app', 'Netlify', 'hosting', 'uses_hosting', 85),
        ('cdn.cloudflare.net', 'Cloudflare', 'cdn', 'uses_cdn', 85),
    ),
    'MX': (
        ('google.com', 'Google Workspace', 'email', 'uses_email', 90),
        ('googlemail.com', 'Google Workspace', 'email', 'uses_email', 90),
        ('protection.outlook.com', 'Microsoft 365', 'email', 'uses_email', 90),
        ('mimecast.com', 'Mimecast', 'email', 'uses_email', 90),
        ('pphosted.com', 'Proofpoint', 'email', 'uses_email', 90),
        ('zoho.com', 'Zoho', 'email', 'uses_email', 90),
    ),
    'NS': (
        ('ns.cloudflare.com', 'Cloudflare', 'dns', 'uses_dns', 90),
        ('awsdns.com', 'Amazon Route 53', 'dns', 'uses_dns', 90),
        ('awsdns.net', 'Amazon Route 53', 'dns', 'uses_dns', 90),
        ('awsdns.org', 'Amazon Route 53', 'dns', 'uses_dns', 90),
        ('awsdns.co.uk', 'Amazon Route 53', 'dns', 'uses_dns', 90),
        ('googledomains.com', 'Google Cloud DNS', 'dns', 'uses_dns', 90),
        ('domaincontrol.com', 'GoDaddy', 'dns', 'uses_dns', 90),
        ('azure-dns.com', 'Microsoft Azure DNS', 'dns', 'uses_dns', 90),
    ),
    'SPF': (
        ('_spf.google.com', 'Google Workspace', 'email', 'authorizes_sender', 75),
        ('spf.protection.outlook.com', 'Microsoft 365', 'email', 'authorizes_sender', 75),
        ('sendgrid.net', 'SendGrid', 'email', 'authorizes_sender', 75),
        ('amazonses.com', 'Amazon SES', 'email', 'authorizes_sender', 75),
        ('mailgun.org', 'Mailgun', 'email', 'authorizes_sender', 75),
        ('spf.mtasv.net', 'Postmark', 'email', 'authorizes_sender', 75),
    ),
}

VERIFICATION = (
    ('google-site-verification=', 'Google'),
    ('ms=ms', 'Microsoft'),
    ('facebook-domain-verification=', 'Meta'),
    ('atlassian-domain-verification=', 'Atlassian'),
    ('apple-domain-verification=', 'Apple'),
    ('adobe-idp-site-verification=', 'Adobe'),
)


def _suffix(value, suffix):
    value = str(value).lower().rstrip('.')
    return value == suffix or value.endswith('.' + suffix)


def detect_services(records):
    """Return typed signals with unmodified supporting record dictionaries.

    SPF authorizes sending, verification TXT merely marks configured verification,
    and CAA authorizes certificate issuance. None proves active product use.
    """
    signals = {}

    def add(provider, category, relation, confidence, record, note=''):
        key = (provider, category, relation)
        signal = signals.setdefault(key, {
            'provider': provider, 'category': category, 'relation': relation,
            'confidence': confidence, 'evidence': [],
        })
        if record not in signal['evidence']:
            signal['evidence'].append(record)
        if note:
            signal['note'] = note

    for record in records[:256]:
        kind = record.get('type', '').upper()
        value = str(record.get('value', ''))
        if kind in ('CNAME', 'MX', 'NS'):
            target = record.get('exchange' if kind == 'MX' else 'target') or value.split()[-1:]
            if isinstance(target, list):
                target = target[0] if target else ''
            for suffix, provider, category, relation, confidence in RULES[kind]:
                if _suffix(target, suffix):
                    add(provider, category, relation, confidence, record)
            if kind == 'NS' and re.fullmatch(r'ns-\d+\.awsdns-\d+\.(?:com|net|org|co\.uk)\.?', str(target), re.I):
                add('Amazon Route 53', 'dns', 'uses_dns', 90, record)
        elif kind == 'TXT':
            if value.lower().startswith('v=spf1 '):
                for target in re.findall(r'(?:^|\s)\+?(?:include:|redirect=)([^\s]+)', value, re.I):
                    for suffix, provider, category, relation, confidence in RULES['SPF']:
                        if _suffix(target, suffix):
                            add(provider, category, relation, confidence, record, 'Sender authorization; not proof of active sending.')
            for prefix, provider in VERIFICATION:
                if value.lower().startswith(prefix):
                    add(provider, 'verification', 'verification_marker', 30, record,
                        'Verification marker may be stale; not proof of current service use.')
        elif kind == 'CAA' and record.get('tag') in ('issue', 'issuewild'):
            issuer = str(record.get('issuer', '')).split(';', 1)[0].strip().lower().rstrip('.')
            if issuer:
                add(issuer, 'certificate_authority', 'authorizes_issuer', 95, record,
                    'Certificate issuer authorization; not hosting evidence.')
    return list(signals.values())
