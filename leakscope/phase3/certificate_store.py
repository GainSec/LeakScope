"""Persist certificate discoveries independently of service-port observations."""
import hashlib
import json
import re
from datetime import datetime, timezone as dt_timezone

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from leakscope_app.models import CertificateRecord, CertificateName, CertificateWatchMatch, ServiceObservation, WatchRule, ExecutionLease
from .asset_names import normalize_domain
from .state import _json_safe, tag_names, set_tags


def certificate_fingerprint(raw):
    empty_hashes={hashlib.sha256(b'').hexdigest(),hashlib.sha1(b'').hexdigest(),hashlib.sha512(b'').hexdigest()}
    for value in (raw.get('sha256'),raw.get('fingerprint')):
        if not isinstance(value,str): continue
        compact=value.replace(':','').lower()
        if len(compact) in {40,64,128} and re.fullmatch('[0-9a-f]+',compact) and compact not in empty_hashes:
            return value[:128]
    return ''


def cert_date(value):
    if isinstance(value, datetime):
        return timezone.make_aware(value, dt_timezone.utc) if timezone.is_naive(value) else value
    if value in (None, ''): return None
    try:
        number = float(value)
        if number > 100_000_000_000: number /= 1000
        return datetime.fromtimestamp(number, tz=dt_timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        try: result = parse_datetime(str(value))
        except ValueError: return None
        return timezone.make_aware(result, dt_timezone.utc) if result and timezone.is_naive(result) else result


@transaction.atomic
def store_certificate(metadata, cert_key, *, observed_at=None, matches=None, collected_at=None):
    raw = metadata.get('certificate') or {}
    domains = []
    for value in raw.get('domains', [])[:100]:
        try: value = normalize_domain(value, allow_wildcard=True)
        except (ValueError, TypeError): continue
        if value not in domains: domains.append(value)
    if not domains: raise ValueError('Certificate has no usable names')
    if not isinstance(cert_key, str) or len(cert_key) != 64: raise ValueError('Invalid certificate identity')
    now = collected_at or timezone.now()
    values = {'fingerprint': certificate_fingerprint(raw),
        'issuer': raw.get('issuer') or {}, 'serial': str(raw.get('serial') or '')[:200],
        'domains': sorted(domains), 'data': _json_safe(metadata), 'last_seen': now,
        'observed_at': cert_date(observed_at), 'not_before': cert_date(raw.get('not_before')), 'not_after': cert_date(raw.get('not_after'))}
    record, created = CertificateRecord.objects.get_or_create(key=cert_key, defaults=dict(values,first_seen=now))
    if not created:
        record = CertificateRecord.objects.select_for_update().get(pk=record.pk)
        record.first_seen = min(record.first_seen, now)
        record.last_seen = max(record.last_seen, now)
        record.domains = sorted(set(record.domains) | set(domains))
        if not certificate_fingerprint({'fingerprint':record.fingerprint}): record.fingerprint=''
        incoming_time = values['observed_at'] or now
        current_time = record.observed_at or record.last_seen
        for name in ('fingerprint','issuer','serial','not_before','not_after'):
            if values[name] and (incoming_time >= current_time or not getattr(record,name)):
                setattr(record,name,values[name])
        if incoming_time >= current_time:
            record.data = values['data']
            if values['observed_at']: record.observed_at = values['observed_at']
        record.save()
    CertificateName.objects.bulk_create([CertificateName(certificate=record, name=name) for name in domains], ignore_conflicts=True)
    for watch, matched_names in matches or []:
        match, match_created = CertificateWatchMatch.objects.get_or_create(certificate=record, watch_key=str(watch.pk), defaults={
            'watch': watch, 'watch_name': watch.name, 'matched_names': sorted(matched_names), 'first_seen':now, 'last_seen': now},
            )
        if not match_created:
            match = CertificateWatchMatch.objects.select_for_update().get(pk=match.pk)
            match.first_seen = min(match.first_seen,now)
            match.last_seen = max(match.last_seen,now)
            match.matched_names = sorted(set(match.matched_names) | set(matched_names))
            match.watch_name = watch.name
            match.save()
        set_tags('certificate', record.pk, tag_names('watch', watch.pk), origin='watch:' + str(watch.pk))
    from .relationships import ingest_certificate_graph
    ingest_certificate_graph(record)
    return record


def backfill_certificates():
    """Idempotent local migration; retain every original observation and artifact."""
    count = 0
    watches=list(WatchRule.objects.all())
    for observation in ServiceObservation.objects.filter(provider='certstream').select_related('endpoint').iterator(chunk_size=200):
        metadata = observation.data.get('metadata') or {}
        certificate = metadata.get('certificate') or {}
        if not isinstance(certificate, dict): continue
        domains = certificate.get('domains') or [observation.endpoint.address]
        metadata = dict(metadata, certificate=dict(certificate, domains=domains), legacy_observation_id=str(observation.pk))
        key = observation.source_id
        if len(key) != 64:
            key = hashlib.sha256(json.dumps(certificate, sort_keys=True, default=str).encode()).hexdigest()
        matches=[]
        for watch in watches:
            lease_key='certstream:'+hashlib.sha256(f'{key}:{observation.endpoint.address}:{watch.pk}'.encode()).hexdigest()
            if ExecutionLease.objects.filter(pk=lease_key,data__watch=str(watch.pk)).exists():
                matches.append((watch,[observation.endpoint.address]))
        if matches: metadata['legacy_watch_match_basis']='Retained exact certificate/name/watch deduplication lease'
        store_certificate(metadata, key, observed_at=observation.observed_at, collected_at=observation.last_collected_at,matches=matches)
        count += 1
    return count
