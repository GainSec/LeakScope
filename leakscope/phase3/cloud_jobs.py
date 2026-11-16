"""Durable bounded cloud checks, integrated with observations and evidence storage."""
import json
import time
from pathlib import Path
from urllib.parse import urlsplit

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from leakscope.services.config import get_config, redact
from leakscope.services.runs import enqueue, publish_state, _data
from leakscope_app.models import CloudResource, EvidenceArtifact, Run
from .cloud import classify, plan, request_candidate, request_target
from .jobs import _claim_execution, capture_root
from .state import ingest_observation, _json_safe


def queue_cloud(spec):
    spec = plan(spec)['spec']
    return enqueue(run_cloud, kind='cloud', provider='cloud', type='cloud',
                   parameters=dict(spec, engine='native', network_contact=True), kwargs={'spec': spec})


@transaction.atomic
def _store(candidate, evidence, response, run, config):
    from .capture import _persist
    from leakscope.services.runs import _data
    endpoint = None
    existing = CloudResource.objects.filter(url=candidate['url']).exists()
    # A denial is useful evidence of a service response, but never proof of public
    # access or resource identity. Preserve that distinction in both projections.
    if existing or evidence['state'] in ('public', 'access_denied', 'reachable', 'service_response'):
        parsed = urlsplit(candidate['url'])
        service = {'aws': 'amazons3be', 'google': 'gcsbucket', 'azure': 'azureblob'}.get(candidate['provider'], 'http')
        if candidate['kind'] == 'application': service = 'http'
        endpoint = ingest_observation('cloud:' + candidate['provider'], {
            'address': parsed.hostname, 'port': 443, 'scheme': 'https', 'service_type': service,
            'title': candidate['name'] + ' · ' + evidence['state'].replace('_', ' '),
            'source_id': candidate['url'], 'observed_at': timezone.now(),
            'metadata': {'cloud_resource': candidate, 'cloud_evidence': {key: value for key, value in evidence.items()
                    if key not in {'headers', 'response_sha256', 'response_bytes'}},
                'uri': candidate['url'], 'network_contact': True, 'engine': 'LeakScope native cloud adapters',
                'identity_verified': evidence['identity_verified']}})
    if endpoint is None and not existing: return None
    with transaction.atomic():
        row, _ = CloudResource.objects.update_or_create(url=candidate['url'], defaults={
            'provider': candidate['provider'], 'name': candidate['name'], 'region': candidate['region'],
            'kind': candidate['kind'], 'state': evidence['state'], 'evidence': _json_safe(evidence, config=config),
            'last_run': run, 'last_checked': timezone.now(),
            **({'endpoint': endpoint} if endpoint else {})})
    if endpoint:
        method, request_url = request_target(candidate, config.get('_cloud_verify', True))
        document = {'candidate': candidate, 'request': {'method': response.get('method', method),
            'url': response.get('request_url', request_url)}, 'response': {'status': response['status'],
            'headers': response.get('headers') or {}, 'body_utf8': (response.get('body') or b'').decode('utf-8', errors='replace'),
            'truncated': bool(response.get('truncated')), 'resolved_ip': response.get('resolved_ip', '')},
            'assessment': evidence}
        root = capture_root(); root.mkdir(parents=True, exist_ok=True)
        artifact = _persist(root, _data(document).encode(), 'application/json', 'cloud-response', 'cloud-native',
                            {'cloud_resource_id': str(row.pk), 'resource_url': row.url}, '.json')
        path = Path(artifact['path'])
        import hashlib
        try:
            EvidenceArtifact.objects.create(endpoint=endpoint, run=run, kind='cloud-response',
                relative_path=str(path.relative_to(root)), mime='application/json', size=path.stat().st_size,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(), tool='cloud-native', metadata=artifact['metadata'])
        except Exception:
            path.unlink(missing_ok=True)
            raise
    from .relationships import ingest_cloud_graph
    ingest_cloud_graph(row)
    return row


@shared_task(bind=True, name='leakscope.phase3.run_cloud')
def run_cloud(self, spec):
    _claim_execution(self, 'cloud')
    planned = plan(spec)
    candidates, spec = planned['candidates'], planned['spec']
    config = dict(get_config()['config'], _cloud_verify=spec['verify'])
    run = Run.objects.filter(task_id=self.request.id).first()
    deadline, next_request = time.monotonic() + 600, 0
    outcomes, throttled, resource_ids = [], set(), []
    attempted, errors, cancelled, limited = 0, 0, False, planned['limited']
    for candidate in candidates:
        if run and Run.objects.filter(pk=run.pk, state='cancelled').exists(): cancelled = True; break
        if candidate['provider'] in throttled:
            outcomes.append(dict(candidate, state='skipped', reason='Provider throttled earlier in this run')); continue
        if time.monotonic() >= deadline: limited = True; break
        delay = next_request - time.monotonic()
        if delay > 0: time.sleep(min(delay, max(0, deadline - time.monotonic())))
        if run and Run.objects.filter(pk=run.pk, state='cancelled').exists(): cancelled = True; break
        if time.monotonic() >= deadline: limited = True; break
        attempted += 1
        next_request = time.monotonic() + 1 / spec['rate_per_second']
        try:
            response = request_candidate(candidate, config, verify=spec['verify'], timeout=min(6, deadline - time.monotonic()))
            evidence = classify(candidate, response)
            if evidence['state'] == 'rate_limited': throttled.add(candidate['provider'])
            row = _store(candidate, evidence, response, run, config)
            if row: resource_ids.append(str(row.pk))
            outcomes.append(dict(candidate, **evidence, resource_id=str(row.pk) if row else None))
        except Exception as exc:
            errors += 1
            outcomes.append(dict(candidate, state='error', reason=redact(exc)[:1000]))
        if run:
            Run.objects.filter(pk=run.pk, state__in=['queued', 'running', 'cancelled']).update(result=_data({
                'summary': f'{attempted} cloud checks completed so far', 'outcomes': outcomes, 'attempted': attempted,
                'stored': len(resource_ids), 'resource_ids': resource_ids, 'partial': True,
                'generated': planned['generated'], 'planned': len(candidates), 'limited': limited,
                'skipped_names': planned['skipped_names']}))
        publish_state(self, state='PROGRESS', meta={'current': len(outcomes), 'total': len(candidates),
                      'attempted': attempted, 'stored': len(resource_ids)})
    states = {}
    for item in outcomes: states[item['state']] = states.get(item['state'], 0) + 1
    result = {'summary': f"{attempted} cloud checks; {states.get('public', 0)} public listings; {len(resource_ids)} resources stored",
            'engine': 'LeakScope native cloud adapters', 'network_contact': True,
            'attempted': attempted, 'stored': len(resource_ids), 'states': states, 'resource_ids': resource_ids,
            'generated': planned['generated'], 'planned': len(candidates), 'limited': limited,
            'cancelled': cancelled, 'partial': bool(errors or throttled or cancelled or len(outcomes) < len(candidates)),
            'skipped_names': planned['skipped_names'], 'outcomes': outcomes}
    if run: Run.objects.filter(pk=run.pk, state='cancelled').update(result=_data(result))
    return result
