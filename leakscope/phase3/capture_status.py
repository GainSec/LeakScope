"""Why an endpoint has, or lacks, a live capture — read from saved records only.

Two channels are reported. `screenshot` is a picture of something the service
displays (a web page in a browser, or a VNC framebuffer). `protocol` is a
bounded exchange in the service's own protocol. Each is one of STATES, with the
reason, the attempts behind it and the last error. Nothing here contacts an
endpoint or starts a job.
"""
import json

STATES = ('succeeded', 'partial', 'failed', 'pending', 'not_attempted', 'unsupported', 'not_applicable')
STATE_LABELS = {'succeeded': 'Captured', 'partial': 'Partial capture', 'failed': 'Capture failed', 'pending': 'Capture queued',
                'not_attempted': 'Not attempted', 'unsupported': 'Unsupported', 'not_applicable': 'Not applicable'}
IMAGE_CLASSES = {'browser': 'Browser screenshot', 'framebuffer': 'Native framebuffer capture', 'rendered': 'Rendered protocol evidence'}
IMAGE_NOTES = {
    'browser': 'A browser loaded the page and saved what it drew.',
    'framebuffer': 'Pixels sent by the service over its remote-display protocol, without input or credentials.',
    'rendered': 'Drawn by LeakScope from saved response bytes. It is not a screenshot and not a terminal session.',
}
IMAGE_KINDS = ('screenshot', 'evidence-render')
SCREEN_ACTIONS = frozenset({'screenshot'})
PROTOCOL_ACTIONS = frozenset({'identity', 'tcp', 'udp', 'http', 'httpx'})
PROTOCOL_KINDS = frozenset({'identity', 'httpx', 'headers', 'protocol-query', 'http-metadata'})
STATUS_KINDS = IMAGE_KINDS + tuple(sorted(PROTOCOL_KINDS)) + ('wire-transcript',)
# Remote displays LeakScope has no capture adapter for.
REMOTE_DISPLAYS = {'rdp': 'RDP', 'ms-wbt-server': 'RDP', 'x11': 'X11', 'spice': 'SPICE', 'teamviewer': 'TeamViewer', 'pcanywhere': 'pcAnywhere'}
ACTION_LABELS = {'screenshot': 'Screenshot', 'identity': 'Native protocol identity', 'tcp': 'Bounded TCP read', 'udp': 'Bounded UDP exchange',
                 'http': 'HTTP response', 'httpx': 'HTTP fingerprint', 'check': 'TCP reachability check'}
MODE_NOTES = {'passive': 'The lookup that saved it ran in Passive mode, which uses provider data only.',
              'demo': 'The lookup that saved it ran in Demo mode, which uses provider data only.'}


def capture_class(kind, tool='', metadata=None):
    """Which of the three image classes a saved file belongs to, or None."""
    if kind == 'evidence-render':
        return 'rendered'
    if kind != 'screenshot':
        return None
    metadata = metadata if isinstance(metadata, dict) else {}
    if metadata.get('protocol') == 'rfb' or str(metadata.get('adapter') or '').startswith('leakscope-rfb'):
        return 'framebuffer'
    return 'browser'


def _json(value):
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value) if isinstance(value, str) and value else {}
    except ValueError:
        parsed = {}
    return parsed if isinstance(parsed, dict) else {}


def _when(value):
    return value.isoformat() if hasattr(value, 'isoformat') else value if isinstance(value, str) else None


def run_actions(run):
    """Outcome of each action one capture job was asked to run."""
    parameters, result = _json(run.get('parameters')), _json(run.get('result'))
    results = result.get('results') if isinstance(result.get('results'), dict) else {}
    details = {}
    for item in result.get('error_details') or []:
        if isinstance(item, dict) and isinstance(item.get('action'), str):
            details.setdefault(item['action'], item)
    actions = [action for action in parameters.get('actions') or [] if isinstance(action, str)][:8]
    state = run.get('state')
    outcomes = []
    for action in actions:
        metadata = results.get(action) if isinstance(results.get(action), dict) else None
        detail = details.get(action)
        if state in ('queued', 'running'):
            outcome = 'pending'
        elif metadata is not None and not detail and not metadata.get('error'):
            outcome = 'partial' if metadata.get('partial') else 'succeeded'
        elif metadata is not None:
            outcome = 'partial'
        elif detail or state in ('failed', 'lost'):
            outcome = 'failed'
        elif state == 'cancelled':
            outcome = 'cancelled'
        else:
            outcome = 'not_run'
        message = str((detail or {}).get('message') or (metadata or {}).get('error') or (run.get('error') if outcome == 'failed' and not detail else '') or '')[:300]
        code = str((detail or {}).get('code') or (metadata or {}).get('error_code') or '')[:40]
        lowered = message.lower()
        # Older records say "failed or timed out"; that wording does not establish a timeout.
        timed_out = code in ('timeout', 'controller_timeout') or (('timed out' in lowered or 'deadline' in lowered) and 'or timed out' not in lowered)
        outcomes.append({'action': action, 'label': ACTION_LABELS.get(action, action), 'state': outcome, 'code': code, 'message': message, 'timed_out': timed_out})
    return outcomes


def _attempt(run):
    parameters = _json(run.get('parameters'))
    return {'run_id': str(run.get('id')), 'state': run.get('state'), 'at': _when(run.get('finished_at') or run.get('created_at')),
            'origin': str(parameters.get('origin') or 'manual')[:120], 'collection_mode': str(parameters.get('collection_mode') or 'custom')[:20],
            'skipped': [str(item)[:300] for item in (parameters.get('skipped_actions') or [])[:6]],
            'routed_by_hint': isinstance((parameters.get('target') or {}).get('protocol_hint'), dict) if isinstance(parameters.get('target'), dict) else False,
            'actions': run_actions(run)}


def _channel(name, applicability, attempts, full, partial, latest_success, partial_reason):
    """One channel's state from saved files (`full`, `partial`) and job outcomes."""
    wanted = SCREEN_ACTIONS if name == 'screenshot' else PROTOCOL_ACTIONS
    outcomes = [(attempt, action) for attempt in attempts for action in attempt['actions'] if action['action'] in wanted]
    count = lambda state: sum(action['state'] == state for _, action in outcomes)
    failed = count('failed')
    last_error = next(({'action': action['action'], 'code': action['code'], 'message': action['message'], 'timed_out': action['timed_out'], 'at': attempt['at']}
                       for attempt, action in outcomes if action['state'] in ('failed', 'partial')), None)
    noun = 'screenshot' if name == 'screenshot' else 'protocol capture'
    after = ' after %d failed attempt%s' % (failed, '' if failed == 1 else 's') if failed else ''
    if full or count('succeeded'):
        state = 'succeeded'
        reason = ('%d saved' % full if name == 'screenshot' and full else 'Saved') + after + '.'
    elif partial or count('partial'):
        state = 'partial'
        reason = partial_reason or (last_error or {}).get('message') or 'Some bytes were saved, but the capture did not complete.'
    elif count('pending'):
        state, reason = 'pending', 'A ' + noun + ' is queued or running.'
    elif failed:
        state = 'failed'
        reason = '%d attempt%s failed. Last: %s' % (failed, '' if failed == 1 else 's', ((last_error or {}).get('message') or 'no reason was saved').rstrip('.') + '.')
    elif applicability['state'] != 'applicable':
        state, reason = applicability['state'], applicability['reason']
    else:
        state, reason = 'not_attempted', 'No ' + noun + ' has been requested for this endpoint.'
    timed_out = state == 'failed' and bool((last_error or {}).get('timed_out'))
    return {'state': state, 'label': STATE_LABELS[state] + (' · timed out' if timed_out else ''), 'reason': reason, 'timed_out': timed_out,
            'applicable': applicability['state'] == 'applicable', 'operation': applicability.get('operation', ''),
            'attempts': len(outcomes), 'failed_attempts': failed, 'retried': len(outcomes) > 1,
            'last_error': last_error, 'last_success_at': latest_success}


def _applicability(snapshot):
    """Whether each channel makes sense for this endpoint, before any attempt."""
    from .capture import supported_actions, _endpoint
    from .protocol_hints import service_label, reader
    try:
        actions = set(supported_actions(snapshot))
        _, port, scheme, service, transport = _endpoint(snapshot)
    except ValueError:
        unknown = {'state': 'unsupported', 'reason': 'This saved record is not a capturable network endpoint.'}
        return unknown, unknown
    named = service not in ('', 'unknown', 'unclassified')
    label = service_label(service) if named else 'This service'
    web = scheme in ('http', 'https') or (named and 'http' in actions)
    if service == 'vnc' and 'screenshot' in actions:
        screen = {'state': 'applicable', 'operation': 'Display-only no-authentication framebuffer snapshot'}
    elif web and 'screenshot' in actions:
        screen = {'state': 'applicable', 'operation': 'Browser screenshot of the page at this endpoint'}
    elif service in REMOTE_DISPLAYS:
        screen = {'state': 'unsupported', 'reason': 'LeakScope has no capture adapter for ' + REMOTE_DISPLAYS[service] + ' displays.'}
    elif named:
        screen = {'state': 'not_applicable', 'reason': label + ' is not a web page or remote display, so there is nothing to screenshot. Saved protocol bytes are drawn as an evidence image instead.'}
    else:
        screen = {'state': 'not_applicable', 'reason': 'No web page or remote display is identified at this endpoint, so no screenshot was planned.'}
    if web:
        protocol = {'state': 'applicable', 'operation': 'HTTP response and fingerprint'}
    elif service == 'vnc':
        protocol = {'state': 'not_applicable', 'reason': 'The VNC negotiation is saved with the framebuffer capture.'}
    elif 'identity' in actions:
        protocol = {'state': 'applicable', 'operation': reader(service)['operation']}
    elif named:
        protocol = {'state': 'unsupported', 'reason': reader(service)['limitation']}
    elif transport == 'tcp':
        protocol = {'state': 'applicable', 'operation': 'Bounded TCP read; this does not identify the protocol'}
    else:
        protocol = {'state': 'unsupported', 'reason': 'A UDP service needs a protocol-specific request, and none is identified.'}
    return screen, protocol


def summarize(snapshot, artifacts=(), runs=(), lookup_modes=(), confirmed_at=None):
    """Capture status for one endpoint from its saved files and capture jobs.

    `artifacts` and `runs` are plain dictionaries, newest first. `confirmed_at`
    is the time of a saved live observation that completed a protocol exchange,
    for collectors that keep the exchange in the observation itself.
    """
    images = {name: 0 for name in IMAGE_CLASSES}
    latest = {}
    partial_images = full_images = protocol_files = partial_files = 0
    partial_reason = ''
    latest_protocol = None
    for item in artifacts:
        kind = item.get('kind')
        metadata = item.get('metadata') if isinstance(item.get('metadata'), dict) else {}
        group = capture_class(kind, item.get('tool', ''), metadata)
        if group:
            images[group] += 1
            latest.setdefault(group, _when(item.get('created_at')))
            if group == 'rendered':
                continue
            if metadata.get('partial'):
                partial_images += 1
                quality = metadata.get('capture_quality') if isinstance(metadata.get('capture_quality'), dict) else {}
                partial_reason = partial_reason or ' '.join(str(text) for text in (quality.get('reasons') or [])[:2])[:300] or (
                    'Part of the framebuffer was not received; missing pixels are black.' if metadata.get('missing_pixels_rendered_black') else '')
            else:
                full_images += 1
        elif kind in PROTOCOL_KINDS or kind == 'wire-transcript':
            if metadata.get('partial'):
                partial_files += 1
            else:
                protocol_files += 1
                latest_protocol = latest_protocol or _when(item.get('created_at'))
    if confirmed_at:
        protocol_files += 1
        latest_protocol = latest_protocol or _when(confirmed_at)
    attempts = [_attempt(run) for run in runs]
    screen_rule, protocol_rule = _applicability(snapshot)
    screenshot = _channel('screenshot', screen_rule, attempts, full_images, partial_images,
                          latest.get('browser') or latest.get('framebuffer'), partial_reason)
    protocol = _channel('protocol', protocol_rule, attempts, protocol_files, partial_files, latest_protocol, '')
    primary = screenshot if screenshot['applicable'] or images['browser'] or images['framebuffer'] else protocol
    reachable = next((attempt['at'] for attempt in attempts for action in attempt['actions']
                      if action['action'] == 'check' and action['state'] == 'succeeded'), None)
    unreachable = next((attempt['at'] for attempt in attempts for action in attempt['actions']
                        if action['action'] == 'check' and action['state'] == 'failed'), None)
    reason = primary['reason']
    if primary['state'] == 'failed' and unreachable and not reachable:
        reason += ' The TCP connection check also failed, so the port may be closed or filtered from this network.'
    if primary['state'] == 'not_attempted':
        note = next((MODE_NOTES[mode] for mode in lookup_modes if mode in MODE_NOTES), '')
        if note:
            reason += ' ' + note
        elif reachable:
            reason += ' A TCP connection check succeeded, which is reachability, not a capture.'
    visual = next((name for name in ('browser', 'framebuffer', 'rendered') if images[name]), None)
    hint = snapshot.get('protocol_hint') if isinstance(snapshot.get('protocol_hint'), dict) else None
    return {'state': primary['state'], 'label': primary['label'], 'reason': reason,
            'channel': 'screenshot' if primary is screenshot else 'protocol',
            'screenshot': screenshot, 'protocol': protocol, 'images': images,
            'visual': {'class': visual, 'label': IMAGE_CLASSES.get(visual, 'No image'), 'note': IMAGE_NOTES.get(visual, ''), 'latest_at': latest.get(visual)},
            'reachable_at': reachable, 'unreachable_at': unreachable, 'routed_by_hint': bool(hint),
            'attempts': {'total': len(attempts), 'failed': sum(attempt['state'] in ('failed', 'lost') for attempt in attempts),
                         'retried': len(attempts) > 1, 'last_at': attempts[0]['at'] if attempts else None},
            'history': attempts[:10], 'history_truncated': len(attempts) > 10,
            'basis': 'Saved capture jobs and files only. Files older than the retention period may have been removed while the job record remains.'}


def capture_runs(endpoint_ids=None, limit=5000):
    """Saved capture jobs grouped by endpoint, newest first; (map, truncated)."""
    from leakscope_app.models import Run
    wanted = None if endpoint_ids is None else {str(value) for value in endpoint_ids}
    rows = Run.objects.filter(kind='capture').order_by('-created_at', '-pk')
    if wanted is not None and len(wanted) == 1:
        rows = rows.filter(parameters__contains=next(iter(wanted)))
    grouped = {}
    values = list(rows.values('id', 'state', 'created_at', 'finished_at', 'parameters', 'result', 'error')[:limit + 1])
    for row in values[:limit]:
        row['parameters'] = _json(row['parameters'])
        key = str(row['parameters'].get('endpoint_id') or '')
        if key and (wanted is None or key in wanted):
            grouped.setdefault(key, []).append(row)
    return grouped, len(values) > limit


def confirmed_time(observations):
    """Time of the newest saved live observation that completed a protocol exchange."""
    from .attribution import RANK, observation_level
    for row in observations:
        verdict = observation_level(row.provider, row.data)
        if not verdict['carried'] and RANK[verdict['level']] >= RANK['protocol_confirmed']:
            return row.last_collected_at
    return None


def endpoint_status(endpoint, snapshot=None, observations=None):
    """Full status for one endpoint (detail views)."""
    from leakscope_app.models import SavedLookup, TagAssignment
    from .jobs import endpoint_snapshot
    from .local_correlations import visible_artifacts
    from .state import visible_endpoints
    snapshot = snapshot or endpoint_snapshot(endpoint)
    artifacts = list(visible_artifacts(visible_endpoints().filter(pk=endpoint.pk)).order_by('-created_at').values('kind', 'tool', 'metadata', 'created_at')[:400])
    runs, _ = capture_runs([endpoint.pk], limit=200)
    lookups = [origin.split(':', 1)[1] for origin in TagAssignment.objects.filter(target_kind='endpoint', target_key=str(endpoint.pk), origin__startswith='lookup:').values_list('origin', flat=True).distinct()[:20]]
    modes = list(SavedLookup.objects.filter(pk__in=[value for value in lookups if len(value) == 36]).values_list('collection_mode', flat=True)) if lookups else []
    if observations is None:
        observations = list(endpoint.observations.filter(hidden=False).exclude(provider='certstream').order_by('-last_collected_at', '-pk')[:100])
    return summarize(snapshot, artifacts, runs.get(str(endpoint.pk), []), modes, confirmed_time(observations))


def batch_status(endpoints, services=None, confirmed=None):
    """Compact status per endpoint for lists: {id: {state,label,reason,channel,images}}.

    `services` overrides a placeholder type with a provider-hinted one, and
    `confirmed` maps endpoint IDs to the time of a saved confirmed exchange.
    """
    from .local_correlations import visible_artifacts
    rows = list(endpoints.values('pk', 'address', 'port', 'transport', 'scheme', 'service_type'))
    if not rows:
        return {}, False
    services = services or {}
    files = {}
    for item in visible_artifacts(endpoints).filter(kind__in=STATUS_KINDS).order_by('-created_at').values('endpoint_id', 'kind', 'tool', 'metadata', 'created_at').iterator(chunk_size=500):
        bucket = files.setdefault(item['endpoint_id'], [])
        if len(bucket) < 60:
            metadata = item['metadata'] if isinstance(item['metadata'], dict) else {}
            bucket.append({'kind': item['kind'], 'tool': item['tool'], 'created_at': item['created_at'],
                           'metadata': {key: metadata.get(key) for key in ('partial', 'protocol', 'adapter', 'capture_quality', 'missing_pixels_rendered_black')}})
    runs, truncated = capture_runs(None if len(rows) > 1 else [rows[0]['pk']])
    result = {}
    for row in rows:
        key = str(row['pk'])
        snapshot = {'address': row['address'], 'port': row['port'], 'transport': row['transport'], 'scheme': row['scheme'],
                    'service_type': services.get(key, row['service_type'])}
        status = summarize(snapshot, files.get(row['pk'], []), runs.get(key, []), confirmed_at=(confirmed or {}).get(key))
        result[key] = {name: status[name] for name in ('state', 'label', 'reason', 'channel', 'images')} | {
            'visual': status['visual']['class'], 'attempts': status['attempts']['total'],
            'screenshot_state': status['screenshot']['state'], 'protocol_state': status['protocol']['state']}
    return result, truncated
