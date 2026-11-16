"""Attribution and capture status of one saved endpoint, with their provenance.

Read-only: every value comes from saved observations, tag assignments, capture
jobs and files. Nothing here contacts an endpoint or a provider.
"""
from leakscope.phase3.attribution import LABELS, LEVELS, MEANINGS, RANK, SEARCH_ORIGINS, endpoint_level, origin_kind, tag_role
from .models import SavedLookup, TagAssignment

CAUTION = 'A port, keyword, tag or organization match is not an identity or ownership claim.'


def _stamp(value):
    return value.isoformat() if value else None


def tag_provenance(endpoint_id):
    """Each tag assignment with where it came from and what it can mean."""
    rows = list(TagAssignment.objects.filter(target_kind='endpoint', target_key=str(endpoint_id)).select_related('tag').order_by('tag__name', 'origin')[:200])
    keys = {row.origin.split(':', 1)[1] for row in rows if row.origin.startswith('lookup:') and len(row.origin) == 43}
    lookups = {str(row.pk): row for row in SavedLookup.objects.filter(pk__in=keys)} if keys else {}
    result = []
    for row in rows:
        kind = origin_kind(row.origin)
        key = row.origin.split(':', 1)[1] if ':' in row.origin else ''
        lookup = lookups.get(key)
        if kind == 'manual':
            label = 'Assigned by hand'
        elif kind == 'lookup':
            label = 'Lookup “' + lookup.name + '” (' + lookup.provider + ': ' + lookup.query[:120] + ')' if lookup else 'A lookup that is no longer saved'
        else:
            label = 'Assigned by ' + row.origin
        result.append({'tag': row.tag.name, 'origin': row.origin, 'origin_kind': kind, 'origin_label': label,
                       'assigned_at': _stamp(row.created_at), 'lookup_id': key if kind == 'lookup' else None, **tag_role(row.tag.name, row.origin)})
    return result


def endpoint_attribution(endpoint, observations):
    """Evidence ladder for one endpoint. `observations` are visible rows, newest first."""
    from .atlas import classify_levels
    signals = {}
    notes = []
    for row in observations:
        if row.provider == 'certstream':
            continue
        metadata = row.data.get('metadata') if isinstance(row.data, dict) and isinstance(row.data.get('metadata'), dict) else {}
        if isinstance(metadata.get('implementation'), str) and metadata['implementation'][:200] not in notes and len(notes) < 4:
            notes.append(metadata['implementation'][:200])
        source = {'provider': row.provider, 'observation_id': str(row.pk), 'observed_at': _stamp(row.last_observed_at or row.observed_at),
                  'collected_at': _stamp(row.last_collected_at)}
        for item in classify_levels(row.data, row.provider):
            if item['key'] == 'unknown':
                continue
            current = signals.get(item['key'])
            if current is None or RANK.get(item['level'], -1) > RANK.get(current['level'], -1):
                signals[item['key']] = dict(item, level_label=LABELS.get(item['level'], 'No identity evidence'), sources=[source])
            elif item['level'] == current['level'] and len(current['sources']) < 3:
                current['sources'].append(source)
    tags = tag_provenance(endpoint.pk)
    headline = endpoint_level([item['level'] for item in signals.values()], [(row['tag'], row['origin']) for row in tags])
    items = {level: [] for level in LEVELS}
    searches = {}
    for row in tags:
        if row['origin_kind'] in SEARCH_ORIGINS:
            searches.setdefault(row['origin'], {'label': row['origin_label'], 'tags': [], 'at': row['assigned_at']})['tags'].append(row['tag'])
        elif row.get('role') == 'level':
            items[row['level']].append({'kind': 'tag', 'text': 'Tagged “' + row['tag'] + '”. ' + row['origin_label'] + '.', 'source': row['origin'], 'at': row['assigned_at']})
    for origin, search in searches.items():
        items['candidate'].append({'kind': 'search', 'text': 'Returned by ' + search['label'] + '. Tags applied to every result: ' + ', '.join(sorted(search['tags'])) + '.',
                                   'source': origin, 'at': search['at']})
    for item in signals.values():
        if item['level'] in items:
            source = item['sources'][0]
            items[item['level']].append({'kind': 'evidence', 'text': item['label'] + ': ' + item['basis'], 'source': source['provider'],
                                         'at': source['observed_at'] or source['collected_at'], 'technology': item['key'], 'observation_id': source['observation_id']})
    reached = RANK.get(headline['level'], -1)
    ladder = [{'level': level, 'label': LABELS[level], 'meaning': MEANINGS[level], 'items': items[level][:12],
               'established': bool(items[level]) or (level == 'candidate' and reached >= 0 and not signals)} for level in LEVELS]
    verified = headline['level'] == 'implementation_verified'
    limits = [row['tag'] for row in tags if row.get('role') == 'limit']
    implementation = {'state': 'verified' if verified else 'unverified', 'collector_notes': notes, 'limit_tags': sorted(set(limits)),
                      'note': '' if verified else 'Not established. A shared protocol, protocol version, port or banner does not identify the implementation.'}
    return {**headline, 'label': LABELS.get(headline['level'], 'No identity evidence'), 'meaning': MEANINGS.get(headline['level'], 'No saved record names what this endpoint runs.'),
            'signals': sorted(signals.values(), key=lambda item: (-RANK.get(item['level'], -1), item['label'].casefold())), 'ladder': ladder,
            'tags': tags, 'implementation': implementation, 'caution': CAUTION,
            'basis': 'Saved observations and tag assignments only. Evidence levels come from the records themselves; tags are shown with who assigned them and cannot raise a level when a search applied them.'}


def artifact_labels(kind, tool, metadata):
    """Image class fields for one saved file, empty when it is not an image."""
    from leakscope.phase3.capture_status import IMAGE_CLASSES, IMAGE_NOTES, capture_class
    group = capture_class(kind, tool, metadata)
    return {'capture_class': group, 'capture_label': IMAGE_CLASSES[group], 'capture_note': IMAGE_NOTES[group]} if group else {'capture_class': None}
