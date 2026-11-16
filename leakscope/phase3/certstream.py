"""Bounded certificate-stream ingestion and explicit watch matching."""
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone as datetime_timezone
from urllib.parse import urlsplit

from django.db import close_old_connections, connection
from django.db.models import F
from django.utils import timezone
import websocket
from websocket._abnf import ABNF, frame_buffer

from leakscope_app.models import StreamHealth, StreamSettings, WatchRule
from .state import acquire_lease, ingest_observation, normalize_address, set_tags, tag_names
from .asset_names import normalize_domain

MAX_MESSAGE = 1024 * 1024
log = logging.getLogger(__name__)


def queue_capture(*args, **kwargs):
    # Jobs owns durable dispatch; lazy import keeps stream matching independently testable.
    from .jobs import queue_capture as dispatch
    return dispatch(*args, **kwargs)


class _BoundedFrames(frame_buffer):
    def recv_strict(self, size):
        if size > MAX_MESSAGE:
            raise ValueError('Certificate frame exceeds 1 MiB')
        return super().recv_strict(size)


class BoundedWebSocket(websocket.WebSocket):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.frame_buffer = _BoundedFrames(self._recv, False)
        self._message_bytes = 0

    def recv_frame(self):
        frame = super().recv_frame()
        if frame.opcode in (ABNF.OPCODE_TEXT, ABNF.OPCODE_BINARY):
            self._message_bytes = 0
        if frame.opcode in (ABNF.OPCODE_TEXT, ABNF.OPCODE_BINARY, ABNF.OPCODE_CONT):
            self._message_bytes += len(frame.data)
            if self._message_bytes > MAX_MESSAGE:
                raise ValueError('Certificate message exceeds 1 MiB')
        return frame


def decode_event(payload):
    if not isinstance(payload, (str, bytes)) or len(payload.encode() if isinstance(payload, str) else payload) > MAX_MESSAGE:
        raise ValueError('Certificate message exceeds 1 MiB')
    def reject_constant(value):
        raise ValueError('Invalid JSON numeric value')
    value = json.loads(payload, parse_constant=reject_constant)
    if not isinstance(value, dict):
        raise ValueError('Certificate message must be an object')
    return value


def domain_matches(domain, pattern):
    if not isinstance(pattern, str):
        return False
    subdomains_only = pattern.startswith('*.')
    try:
        base = normalize_address(pattern[2:] if subdomains_only else pattern)
        host = normalize_domain(domain, allow_wildcard=True)
    except ValueError:
        return False
    return host.endswith('.' + base) or (not subdomains_only and host == base)


def stream_connection(settings):
    mode = settings.mode
    if mode == 'builtin':
        url = os.environ.get('LEAKSCOPE_CERTSTREAM_BUILTIN_URL', 'ws://certstream:8080/')
    elif mode == 'external':
        url = settings.external_url
    else:
        raise ValueError('Select builtin or external certificate stream mode')
    try:
        parts = urlsplit(url)
        if parts.scheme not in {'ws', 'wss'} or not parts.hostname or parts.fragment or len(url) > 2000:
            raise ValueError
        if parts.port is not None and not 1 <= parts.port <= 65535:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError('Configure a valid ws:// or wss:// certificate stream URL for the selected mode') from None
    return url, mode


def _bounded(value, maximum=300):
    if isinstance(value, dict):
        return {str(key)[:80]: str(item)[:maximum] for key, item in list(value.items())[:15]}
    return str(value or '')[:maximum]


def _seen(value):
    try:
        value = float(value)
        if value > 100_000_000_000:
            value /= 1000
        return datetime.fromtimestamp(value, tz=datetime_timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _log_source(value):
    if not isinstance(value, dict):
        return {}
    try:
        parts = urlsplit(str(value.get('url') or ''))
        if parts.scheme not in {'http', 'https'} or not parts.hostname:
            return {}
        host = '[' + parts.hostname + ']' if ':' in parts.hostname else parts.hostname
        if parts.port:
            host += ':' + str(parts.port)
        return {'url': parts.scheme + '://' + host + parts.path[:500],
                'name': _bounded(value.get('name'), 200)}
    except ValueError:
        return {}


def process_event(event, watches=None):
    result = {'received': 0, 'matched': 0, 'dropped': 0}
    if not isinstance(event, dict) or event.get('message_type') != 'certificate_update':
        return result
    result['received'] = 1
    data = event.get('data')
    if not isinstance(data, dict) or not isinstance(data.get('leaf_cert'), dict):
        result['dropped'] = 1
        return result
    leaf = data['leaf_cert']
    raw_domains = leaf.get('all_domains', [])
    if not isinstance(raw_domains, list):
        result['dropped'] = 1
        return result
    domains = set()
    result['dropped'] += max(0, len(raw_domains) - 100)
    for raw in raw_domains[:100]:
        try:
            # A wildcard SAN is evidence, not a concrete hostname to fetch.
            domains.add(normalize_domain(raw, allow_wildcard=True))
        except (TypeError, ValueError):
            result['dropped'] += 1
    watches = list(WatchRule.objects.filter(enabled=True)) if watches is None else watches
    metadata = {'certificate': {
        'sha256': _bounded(leaf.get('sha256'), 128),
        'fingerprint': _bounded(leaf.get('fingerprint'), 128),
        'issuer': _bounded(leaf.get('issuer')),
        'serial': _bounded(leaf.get('serial_number'), 200),
        'not_before': _bounded(leaf.get('not_before'), 100),
        'not_after': _bounded(leaf.get('not_after'), 100),
        'domains': sorted(domains)},
        'reported_log': _log_source(data.get('source')),
        'cert_index': _bounded(data.get('cert_index'), 30),
        'update_type': _bounded(data.get('update_type'), 50),
        'discovery_basis': 'Certificate name only; service port and reachability are unverified'}
    logical = [metadata['certificate']['issuer'], metadata['certificate']['serial'],
               metadata['certificate']['not_before'], metadata['certificate']['not_after'], sorted(domains)]
    if not metadata['certificate']['serial'] or not metadata['certificate']['issuer']:
        logical = [metadata['certificate']['sha256'] or metadata['certificate']['fingerprint'], sorted(domains)]
    cert_key = hashlib.sha256(json.dumps(logical, sort_keys=True).encode()).hexdigest()
    matches = []
    for watch in watches:
        if not watch.enabled:
            continue
        patterns = watch.domains if isinstance(watch.domains, list) else []
        keywords = [k.casefold() for k in watch.keywords if isinstance(k, str) and k] if isinstance(watch.keywords, list) else []
        matched_names = []
        for domain in sorted(domains):
            try:
                unicode_domain = domain.encode('ascii').decode('idna')
            except UnicodeError:
                unicode_domain = domain
            if not (any(domain_matches(domain, p) for p in patterns) or
                    any(k in domain.casefold() or k in unicode_domain.casefold() for k in keywords)):
                continue
            matched_names.append(domain)
        if matched_names: matches.append((watch, matched_names))
    if not matches: return result
    from .certificate_store import store_certificate
    record = store_certificate(metadata, cert_key, observed_at=_seen(data.get('seen')), matches=matches)
    for watch, names in matches:
        for domain in names:
            lease_key = 'certstream:' + hashlib.sha256(f'{cert_key}:{domain}:{watch.pk}'.encode()).hexdigest()
            if not acquire_lease(lease_key, 7 * 86400, {'watch': str(watch.pk)}): continue
            result['matched'] += 1
            if watch.auto_fetch and watch.actions and not domain.startswith('*.'):
                from .asset_jobs import queue_watch_capture
                queue_watch_capture(record, watch, domain)
    return result


def _health(state, mode, counters, last_event, error=''):
    StreamHealth.objects.get_or_create(pk=1)
    values = {'state': state, 'mode': mode, 'heartbeat_at': timezone.now(),
              'last_error': error[:1500], **{key: F(key) + value for key, value in counters.items()}}
    if last_event:
        values['last_event_at'] = last_event
    StreamHealth.objects.filter(pk=1).update(**values)
    for key in counters:
        counters[key] = 0


def consume_stream(*, stop_event, max_events=0, max_seconds=0, connect=None):
    connect = connect or (lambda url: websocket.create_connection(url, timeout=1, class_=BoundedWebSocket))
    started = last_flush = time.monotonic()
    counters = {'received': 0, 'matched': 0, 'dropped': 0}
    event_count, backoff, last_event, mode, ws = 0, 1, None, 'builtin', None
    error = ''
    def expired():
        return (max_events and event_count >= max_events) or (max_seconds and time.monotonic() - started >= max_seconds)
    try:
        while not stop_event.is_set() and not expired():
            if not connection.in_atomic_block:close_old_connections()
            settings, _ = StreamSettings.objects.get_or_create(pk=1)
            mode = settings.mode
            if not settings.enabled:
                _health('disabled', mode, counters, last_event)
                stop_event.wait(1)
                continue
            try:
                url, mode = stream_connection(settings)
                _health('connecting', mode, counters, last_event)
                ws = connect(url)
                _health('connected', mode, counters, last_event)
                last_ping, last_reload = time.monotonic(), time.monotonic()
                watches = list(WatchRule.objects.filter(enabled=True))
                while not stop_event.is_set() and not expired():
                    now = time.monotonic()
                    if now - last_ping >= 30:
                        ws.ping('leakscope')
                        last_ping = now
                    if now - last_reload >= 10:
                        if not connection.in_atomic_block:close_old_connections()
                        current = StreamSettings.objects.get(pk=1)
                        if not current.enabled or stream_connection(current) != (url, mode):
                            break
                        watches = list(WatchRule.objects.filter(enabled=True))
                        last_reload = now
                    try:
                        opcode, payload = ws.recv_data(control_frame=True)
                        if opcode == ABNF.OPCODE_CLOSE:
                            raise ConnectionError('Certificate stream closed')
                        if opcode not in {ABNF.OPCODE_TEXT, ABNF.OPCODE_BINARY}:
                            continue
                        event = decode_event(payload)
                        values = process_event(event, watches)
                        for key, value in values.items():
                            counters[key] += value
                        if values['received']:
                            event_count += 1
                            last_event = timezone.now()
                            backoff = 1
                    except websocket.WebSocketTimeoutException:
                        pass
                    if now - last_flush >= 10:
                        _health('connected', mode, counters, last_event)
                        last_flush = now
            except Exception as exc:
                # URLs and stream payloads can contain credentials or private names.
                error = 'Certificate stream interrupted (' + type(exc).__name__ + '); reconnecting selected mode'
                log.warning(error)
                counters['dropped'] += 1
                _health('reconnecting', mode, counters, last_event, error)
                if not expired():
                    remaining = max_seconds - (time.monotonic() - started) if max_seconds else backoff
                    stop_event.wait(max(0, min(backoff, remaining)))
                    backoff = min(backoff * 2, 30)
            finally:
                if ws is not None:
                    try:
                        ws.close(timeout=1)
                    except Exception:
                        pass
                    ws = None
    finally:
        _health('stopped', mode, counters, last_event, error)
    return event_count
