"""How far saved evidence goes toward naming what an endpoint runs.

Evidence levels distinguish an observation from implementation verification:

candidate               matched a search (port, keyword, scope); nothing names the service
provider_reported       a search provider or imported source named the service
protocol_confirmed      a live collector completed an exchange in that protocol
signature_matched       an observed fingerprint matched a saved, spoofable rule
implementation_verified evidence specific to one implementation was verified

A level describes evidence for one technology on one endpoint. A port match,
a search keyword, a tag inherited from a lookup or membership of an
organization never raises a level, and no level is a statement of ownership.
"""
LEVELS = ('candidate', 'provider_reported', 'protocol_confirmed', 'signature_matched', 'implementation_verified')
RANK = {level: index for index, level in enumerate(LEVELS)}
LABELS = {'candidate': 'Discovery candidate', 'provider_reported': 'Provider reported',
          'protocol_confirmed': 'Live protocol confirmed', 'signature_matched': 'Implementation signature matched', 'implementation_verified': 'Implementation verified'}
MEANINGS = {
    'candidate': 'Matched a search by port, keyword or scope. Nothing saved names the service, and a match is not an identity or ownership claim.',
    'provider_reported': 'A search provider or imported source named the service from its own scan. LeakScope has not confirmed it live.',
    'protocol_confirmed': 'A live collector completed an exchange with the endpoint in this protocol. That names the protocol, not the software or the owner.',
    'implementation_verified': 'Evidence specific to one implementation was verified. A shared protocol, version, port or banner is not enough.',
    'signature_matched': 'Observed evidence matched a saved technology signature. This is a hint that can be imitated, not verified software identity or proof of current reachability.',
}
# Earlier quality values, kept so saved filters and older clients still work.
QUALITY = {'candidate': 'unverified', 'provider_reported': 'provider_reported',
           'protocol_confirmed': 'direct', 'signature_matched': 'signature', 'implementation_verified': 'direct'}
REQUESTED_BASIS = 'Requested search profile; identity not verified'
CONFIRMATION_KEYS = ('identity_verified', 'protocol_verified', 'protocol_confirmed', 'cardano_protocol_confirmed')
LIVE_PREFIXES = ('fetch:', 'native:')
# A tag inherited from one of these was applied to every result of a search.
SEARCH_ORIGINS = frozenset({'lookup', 'watch', 'query'})
# Existing tag names are reused; only provider-reported and implementation-verified are new.
TAG_LEVELS = {'identity-candidate': 'candidate', 'provider-reported': 'provider_reported',
              'protocol-confirmed': 'protocol_confirmed', 'http-response-confirmed': 'protocol_confirmed',
              'implementation-verified': 'implementation_verified'}
TAG_LIMITS = frozenset({'implementation-unverified', 'application-identity-unverified'})
TAG_FACTS = {'tcp-open': 'A TCP connection was accepted. This is reachability, not identity.',
             'browser-captured': 'A browser screenshot was saved. This is a capture fact, not identity.'}
SEARCH_TAG_NOTE = 'Applied to every result of this search. A match is not an identity or ownership claim.'


def is_true(value):
    return value is True or (isinstance(value, str) and value.strip().lower() in ('true', '1'))


def http_status(value):
    try:
        return 100 <= int(value) <= 599 and not isinstance(value, bool)
    except (TypeError, ValueError):
        return False


def observation_level(provider, data):
    """Level and plain-language basis of one saved observation's own label.

    `carried` marks a label copied from the endpoint record by a collection
    step that did not test it, so it is never read as that step's finding.
    """
    data = data if isinstance(data, dict) else {}
    metadata = data.get('metadata')
    if not isinstance(metadata, dict):
        metadata = {}
    provider = str(provider or '')
    service = str(data.get('service_type') or '').strip().lower()
    basis = metadata.get('service_type_basis')
    basis = basis if isinstance(basis, str) else ''
    matches=metadata.get('signature_matches',[])
    if provider.startswith(LIVE_PREFIXES) and is_true(metadata.get('implementation_verified')):
        return {'level':'implementation_verified','carried':False,'basis':'A live collector verified evidence specific to this implementation.'}
    if metadata.get('signature_status')=='matched' and isinstance(matches,list) and any(isinstance(m,dict) and m.get('technology')==service for m in matches):
        return {'level':'signature_matched','carried':False,'basis':'Observed evidence matched a saved signature; experimental and can be imitated.'}
    if basis == REQUESTED_BASIS:
        return {'level': 'candidate', 'carried': False,
                'basis': 'The result matched a requested search profile; nothing in it names the service.'}
    if provider.startswith(LIVE_PREFIXES):
        if is_true(metadata.get('implementation_verified')):
            return {'level': 'implementation_verified', 'carried': False,
                    'basis': 'A live collector verified evidence specific to this implementation.'}
        if any(is_true(metadata.get(key)) for key in CONFIRMATION_KEYS):
            return {'level': 'protocol_confirmed', 'carried': False,
                    'basis': 'A live collector (' + provider + ') completed a protocol exchange with the endpoint.'}
        if service in ('http', 'https') and http_status(metadata.get('status')):
            return {'level': 'protocol_confirmed', 'carried': False,
                    'basis': 'An HTTP response was received directly from the endpoint.'}
        return {'level': 'candidate', 'carried': True,
                'basis': 'Label carried from the endpoint record; this collection step did not verify it.'}
    if provider.startswith('scan:'):
        return {'level': 'candidate', 'carried': True,
                'basis': 'A port scan found the port open; a port does not name the service.'}
    source = 'Derived from saved provider evidence' if provider.startswith('analysis:') else 'Reported by ' + (provider or 'a saved source')
    return {'level': 'provider_reported', 'carried': False,
            'basis': source + ((' (' + basis + ')') if basis else '') + '; not confirmed live.'}


def best(levels):
    """Highest level in an iterable of level names, or None."""
    ranked = [level for level in levels if level in RANK]
    return max(ranked, key=lambda level: RANK[level]) if ranked else None


def origin_kind(origin):
    return str(origin or 'manual').split(':', 1)[0]


def tag_role(tag, origin):
    """What a tag assignment can mean, given where it came from."""
    kind = origin_kind(origin)
    search = kind in SEARCH_ORIGINS
    if tag in TAG_LIMITS:
        return {'role': 'limit', 'limit': 'protocol_confirmed',
                'note': 'States that the implementation behind the protocol is not established.'}
    if tag in TAG_LEVELS:
        level = TAG_LEVELS[tag]
        if search and level != 'candidate':
            return {'role': 'grouping', 'note': SEARCH_TAG_NOTE + ' It cannot raise the evidence level.'}
        return {'role': 'level', 'level': level, 'note': SEARCH_TAG_NOTE if search else 'Assigned as a statement about this endpoint; shown beside the saved evidence, not instead of it.'}
    if tag in TAG_FACTS:
        return {'role': 'fact', 'note': TAG_FACTS[tag]}
    if search:
        return {'role': 'grouping', 'note': SEARCH_TAG_NOTE}
    return {'role': 'label', 'note': ''}


def endpoint_level(signal_levels, tags):
    """Combine evidence levels with tag statements for one endpoint.

    Returns the headline level, the evidence-only level, the highest level any
    tag states, and whether a tag says the implementation is unverified.
    """
    evidence = best(signal_levels)
    roles = [tag_role(tag, origin) for tag, origin in tags]
    stated = best(role['level'] for role in roles if role['role'] == 'level')
    limited = any(role['role'] == 'limit' for role in roles)
    entered_by_search = any(origin_kind(origin) in SEARCH_ORIGINS for _, origin in tags)
    level = best([value for value in (evidence, stated) if value])
    if level is None and entered_by_search:
        level = 'candidate'
    if limited and level and RANK[level] > RANK['protocol_confirmed']:
        level = 'protocol_confirmed'
    return {'level': level, 'evidence_level': evidence, 'tag_level': stated, 'implementation_limited': limited}
