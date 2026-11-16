"""Items that travel between blocks. An item is a saved record of a kind, with named fields."""
import ipaddress
import re
from uuid import UUID
from django.db.models import Sum
from leakscope.phase3.state import visible_endpoints
from leakscope_app.models import ServiceObservation, TagAssignment

VALUE = {'kind': 'value', 'id': '', 'fields': {}}
ENDPOINT_FIELDS = ('address', 'ip', 'hostname', 'names', 'port', 'protocol', 'transport', 'scheme', 'technology', 'title', 'sources', 'tags',
                   'times_captured', 'first_seen', 'last_seen')

DOMAIN_FIELDS = ('domain', 'root', 'source', 'status', 'confidence', 'category', 'relation', 'dns_status', 'issuer', 'expires', 'added')
FIELDS = {'endpoint': ENDPOINT_FIELDS, 'domain': DOMAIN_FIELDS, 'value': ()}
ALL_FIELDS = set(ENDPOINT_FIELDS) | set(DOMAIN_FIELDS)
# Values some blocks put on every item they pass on.
RESPONSE_FIELDS = ('response_status', 'response_body')
LOCATION_FIELDS = ('country', 'country_code', 'city')


def domain_item(name, **fields):
    """A domain name travelling through a workflow. Its id is the name itself."""
    name = str(name).strip().lower().rstrip('.')
    parts = name.split('.')
    return {'kind': 'domain', 'id': name, 'fields': {'domain': name, 'root': '.'.join(parts[-2:]) if len(parts) > 1 else name, **{key: value for key, value in fields.items() if value not in (None, '')}}}


def _is_ip(value):
    try: ipaddress.ip_address(value)
    except ValueError: return False
    return True


def endpoint_items(ids):
    """Items for visible endpoints in the given order, and how many ids were dropped (hidden, deleted, malformed)."""
    keys = []
    for value in ids:
        try: keys.append(UUID(str(value)))
        except (ValueError, TypeError, AttributeError): continue
    if not keys: return [], len(ids)
    rows = {row.pk: row for row in visible_endpoints().filter(pk__in=keys)}
    captures = {row['endpoint_id']: row['n'] for row in ServiceObservation.objects.filter(endpoint_id__in=list(rows), hidden=False)
                .values('endpoint_id').annotate(n=Sum('collection_count'))}
    tags = {}
    for key, name in TagAssignment.objects.filter(target_kind='endpoint', target_key__in=[str(pk) for pk in rows]).values_list('target_key', 'tag__name'):
        tags.setdefault(key, set()).add(name)
    # Sources and names come from the saved observations themselves; nothing is looked up.
    from leakscope.phase3.dns_enrichment import extract_names
    sources, names = {}, {}
    for key, provider, metadata in ServiceObservation.objects.filter(endpoint_id__in=list(rows), hidden=False).exclude(provider='certstream') \
            .order_by('-last_collected_at').values_list('endpoint_id', 'provider', 'data__metadata')[:20000]:
        sources.setdefault(key, set()).add(provider)
        found = names.setdefault(key, set())
        if len(found) < 50:
            for item in extract_names(metadata, provider=provider)[:50]:
                if isinstance(item, dict) and item.get('name'): found.add(str(item['name']).lower())
    items, seen = [], set()
    for key in keys:
        row = rows.get(key)
        if row is None or key in seen: continue
        seen.add(key)
        items.append({'kind': 'endpoint', 'id': str(key), 'fields': {
            'address': row.address, 'ip': row.address if _is_ip(row.address) else '', 'hostname': '' if _is_ip(row.address) else row.address,
            'names': sorted(names.get(key, ()) | ({row.address.lower()} if not _is_ip(row.address) else set()))[:50],
            'port': row.port, 'protocol': row.scheme or row.transport, 'transport': row.transport, 'scheme': row.scheme, 'technology': row.service_type,
            'title': row.title, 'sources': sorted(sources.get(key, ())), 'tags': sorted(tags.get(str(key), ())),
            'times_captured': captures.get(key, 0), 'first_seen': row.first_seen.isoformat(), 'last_seen': row.last_seen.isoformat()}})
    return items, len(ids) - len(items)


def refresh(items):
    """Re-read endpoint items so a block never acts on a record that was hidden or deleted since the last step."""
    if not items or items[0].get('kind') != 'endpoint': return list(items), 0
    fresh, dropped = endpoint_items([item.get('id') for item in items])
    # Values a workflow named on an item (Take a value, Set a value) travel with it.
    extra = {item.get('id'): {key: value for key, value in (item.get('fields') or {}).items() if key not in ENDPOINT_FIELDS} for item in items}
    for item in fresh: item['fields'].update(extra.get(item['id']) or {})
    return fresh, dropped


NAME = re.compile(r'[a-z][a-z0-9_]{0,31}')
PLACEHOLDER = re.compile(r'\{\{\s*([a-z][a-z0-9_]{0,31})\s*\}\}')


def placeholders(text):
    """Names used as {{name}} in a setting."""
    return PLACEHOLDER.findall(text) if isinstance(text, str) else [name for part in text or [] for name in placeholders(part)]


def text_of(value):
    """A field as text: the first entry of a list, an empty string for nothing."""
    if isinstance(value, (list, tuple)): value = value[0] if value else ''
    return '' if value is None else str(value)


def render(text, item, quoted=False):
    """Fill {{name}} from the item's fields. Returns None when a named value is empty for this item.
    For a provider query (quoted=True) a value is inserted as text only: its quotes and backslashes are
    escaped, so a title or name can never end the quoted term it sits in and add query terms of its own."""
    fields, missing = item.get('fields') or {}, []
    def fill(match):
        value = text_of(fields.get(match.group(1)))
        if not value: missing.append(match.group(1))
        return value.replace('\\', '\\\\').replace('"', '\\"') if quoted else value
    result = PLACEHOLDER.sub(fill, text)
    return None if missing else result


def gone(count):
    return ' · ' + str(count) + ' no longer available' if count else ''
