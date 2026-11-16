"""Dated Netlas DNS documents as checkpointed expansion evidence, never live DNS."""
import time
from datetime import date, datetime, timezone
from urllib.parse import urlencode

from .asset_names import normalize_domain
from .providers import _request


def validate_indices(values):
    if not isinstance(values, list) or len(values) > 24:
        raise ValueError('Choose at most 24 Netlas DNS snapshot dates')
    result = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError('Use Netlas date labels such as 2024-01-01')
        try:
            parsed = date.fromisoformat(value)
            if parsed.isoformat() != value or parsed.year < 2000: raise ValueError()
        except ValueError:
            raise ValueError('Use Netlas date labels such as 2024-01-01') from None
        if value not in result: result.append(value)
    return result


def load_indices(config):
    rows = _request('netlas', '/indices/', config, allow_list=True)
    if not isinstance(rows, list):
        raise ValueError('Netlas returned invalid snapshot metadata')
    result = []
    for row in rows:
        if not isinstance(row, dict) or row.get('type') != 'domains': continue
        try: label = validate_indices([row.get('label')])[0]
        except ValueError: continue
        result.append({key: row.get(key) for key in ('label', 'scan_started_at', 'scan_ended_at', 'is_default', 'state', 'availability')})
    return sorted(result, key=lambda row: row['label'], reverse=True)


def _stamp(value):
    if not isinstance(value, str): return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.astimezone(timezone.utc).isoformat() if parsed.tzinfo else None
    except ValueError: return None


def collect(source, seed, *, config, spend, context):
    if source != 'netlas_dns' or seed.get('kind') != 'domain':
        raise ValueError('Netlas DNS history requires a domain seed')
    target = normalize_domain(seed['value']).removeprefix('*.')
    labels = validate_indices(context.get('netlas_indices', [])) or ['']
    cursor = context.get('cursor') or {'snapshot': 0, 'start': 0}
    if (not isinstance(cursor, dict) or set(cursor) != {'snapshot', 'start'}
        or type(cursor['snapshot']) is not int or not 0 <= cursor['snapshot'] < len(labels)
        or type(cursor['start']) is not int or not 0 <= cursor['start'] <= 9980 or cursor['start'] % 20):
        raise ValueError('Invalid Netlas DNS history cursor')
    result = {'candidates': [], 'leads': [], 'notes': [], 'cacheable': True}
    def blocked(reason):
        return {**result, 'blocked': reason, 'cacheable': False, 'continuation': dict(cursor)}
    if time.monotonic() >= context.get('deadline', time.monotonic()+40): return blocked('deadline')
    if not config.get('NETLAS_API_KEY'):
        return {**result, 'failed': True, 'cacheable': False, 'notes': ['Netlas DNS history requires a configured Netlas API key.']}
    if not spend('netlas'): return blocked('budget')
    label = labels[cursor['snapshot']]
    params = {'q': '(domain:'+target+' OR domain:*.'+target+')', 'start': cursor['start'],
              'fields': 'domain,a,aaaa,cname,mx,ns,txt,@timestamp,last_updated'}
    if label: params['indices'] = label
    url = 'https://app.netlas.io/api/domains/?'+urlencode(params)
    try:
        payload = _request('netlas', '/domains/', config, params=params)
        items = payload.get('items')
        if not isinstance(items, list) or len(items) > 20: raise ValueError('Netlas returned an invalid DNS page')
        for item in items:
            raw = item.get('data') if isinstance(item, dict) else None
            if not isinstance(raw, dict):
                result['notes'].append('Malformed DNS document omitted; coverage is partial.'); continue
            try: name = normalize_domain(raw.get('domain'))
            except (ValueError, TypeError): continue
            if name != target and not name.endswith('.'+target): continue
            records = {}
            for key in ('a', 'aaaa', 'cname', 'mx', 'ns', 'txt', 'soa', 'caa'):
                values = raw.get(key)
                if values is None: continue
                if not isinstance(values, list): values = [values]
                records[key.upper()] = values[:64]
                if len(values) > 64: result['notes'].append('DNS record values limited to 64 per type; coverage is partial.')
            observed = _stamp(raw.get('@timestamp'))
            proof = {'source': 'netlas_dns', 'signal': 'historical_dns_record', 'strength': 35,
                'url': url, 'via': target, 'observed_at': observed, 'collected_at': datetime.now(timezone.utc).isoformat(),
                'historical': True, 'record_type': 'dns_snapshot', 'index_label': label or 'provider-default',
                'dns_records': records, 'excerpt': 'Netlas DNS snapshot '+(label or '(provider default)')+
                '. Indexed time is not first-ever appearance. Historical DNS does not prove current reachability or ownership.'}
            result['candidates'].append({'domain': name, 'relation': 'historical', 'evidence': [proof]})
            if context.get('demo'): break
        if context.get('demo'):
            result['notes'].append('Demo queried one Netlas DNS page and retained at most one name; other pages and snapshots were not searched.')
        elif len(items) == 20 and cursor['start'] < 9980:
            result['continuation'] = {**cursor, 'start': cursor['start']+20}
        else:
            if len(items) == 20: result['notes'].append('Netlas 10,000-document search limit reached for this snapshot; coverage is partial.')
            if cursor['snapshot']+1 < len(labels): result['continuation'] = {'snapshot': cursor['snapshot']+1, 'start': 0}
    except ValueError as exc:
        result.update(failed=True, cacheable=False)
        result['notes'].append('Netlas DNS '+(label or 'provider-default')+': '+str(exc)+'. No automatic retry was made; unavailable history does not prove absence.')
    return result
