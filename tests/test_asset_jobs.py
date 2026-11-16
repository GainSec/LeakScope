"""Durable asset job fixtures; no DNS, registry, paid search or target probes."""
import json
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from leakscope.phase3 import asset_jobs as jobs
from leakscope.phase3.state import set_tags, tag_names
from leakscope_app.models import (AssetEvidence, AssetRelationship, Run, WatchRule,
    CertificateRecord, ServiceEndpoint, ServiceObservation, EvidenceArtifact)


class AssetEnrichmentJobTests(TestCase):
    @patch('leakscope.phase3.asset_jobs.collect_dns')
    def test_dns_job_is_durable_materializes_evidence_and_reuses_unexpired_cache(self, collect):
        collect.return_value = {'target': 'example.com', 'records': [{'name': 'example.com', 'type': 'A', 'value': '192.0.2.1', 'ttl': 300}], 'services': [], 'partial': False, 'errors': []}
        first = jobs.queue_enrichment('dns', 'EXAMPLE.COM.')
        self.assertFalse(first['cached'])
        self.assertEqual(first['run']['state'], 'succeeded')
        self.assertEqual(first['run']['parameters']['target'], 'example.com')
        self.assertTrue(AssetRelationship.objects.filter(relation='resolves_to').exists())
        cached = jobs.queue_enrichment('dns', 'example.com')
        self.assertTrue(cached['cached'])
        self.assertIn('root_id', cached['result'])
        self.assertEqual(collect.call_count, 1)
        self.assertEqual(Run.objects.count(), 1)
        proof = AssetEvidence.objects.get(pk=cached['evidence_id'])
        self.assertGreater(proof.expires_at, timezone.now())

    @patch('leakscope.phase3.asset_jobs.resolve_names')
    def test_resolve_and_expired_cache_collect_again(self, resolve):
        resolve.return_value = {'target': '192.0.2.1', 'names': [{'name': 'ptr.example.com', 'source': 'dns:ptr', 'forward_confirmed': True, 'ttl': 60}], 'records': [], 'partial': False, 'errors': []}
        jobs.queue_enrichment('resolve', '192.0.2.1')
        AssetEvidence.objects.filter(source='enrichment:resolve').update(expires_at=timezone.now() - timedelta(seconds=1))
        jobs.queue_enrichment('resolve', '192.0.2.1')
        self.assertEqual(resolve.call_count, 2)
        self.assertTrue(AssetRelationship.objects.filter(relation='ptr_name').exists())

    @patch('leakscope.phase3.asset_jobs.netblock_info')
    def test_netblock_uses_registry_without_target_capture(self, registry):
        registry.return_value = {'target': '192.0.2.1', 'networks': [], 'asns': [], 'partial': False, 'errors': [], 'status': 'not_applicable'}
        result = jobs.queue_enrichment('netblock', '192.0.2.1')
        self.assertEqual(result['run']['state'], 'succeeded')
        registry.assert_called_once()
        self.assertFalse(ServiceEndpoint.objects.exists())

    @patch('leakscope.phase3.asset_jobs.free_registry.discover_organization')
    def test_organization_aliases_and_request_limit_are_frozen_without_credentials(self, discover):
        discover.return_value = {'organization': 'Acme', 'domains': [], 'networks': [], 'ips': [], 'asns': [], 'partial': False, 'errors': []}
        with patch('leakscope.phase3.asset_jobs.run_asset_enrichment.apply_async') as submit:
            queued = jobs.queue_enrichment('organization', 'Acme', aliases=['Acme Group'], max_requests=1)
        kwargs = submit.call_args.kwargs['kwargs']
        self.assertEqual(kwargs['aliases'], ['Acme Group'])
        self.assertEqual(kwargs['max_requests'], 1)
        self.assertNotIn('config', kwargs)
        self.assertNotIn('API_KEY', json.dumps(kwargs))
        self.assertEqual(queued['run']['provider'], 'rdap')
        discover.assert_not_called()

    @patch('leakscope.phase3.asset_jobs.collect_dns')
    def test_adapter_failure_is_durable_partial_evidence_and_redacted(self, collect):
        collect.side_effect = ValueError('failure configured-secret')
        config = {'config': {'NETLAS_API_KEY': 'configured-secret'}}
        with patch('leakscope.phase3.asset_jobs.get_config', return_value=config), patch('leakscope.phase3.state.get_config', return_value=config), patch('leakscope.services.runs.get_config', return_value=config):
            result = jobs.queue_enrichment('dns', 'example.com')
        payload = result['run']['result']
        self.assertTrue(payload['partial'])
        self.assertTrue(payload['errors'])
        self.assertNotIn('configured-secret', json.dumps(result))
        self.assertNotIn('configured-secret', json.dumps(list(AssetEvidence.objects.values_list('data', flat=True))))

    @patch('leakscope.phase3.asset_jobs.collect_dns')
    def test_partial_records_remain_visible(self, collect):
        collect.return_value = {'target': 'example.com', 'records': [{'name': 'example.com', 'type': 'A', 'value': '192.0.2.1', 'ttl': 60}], 'services': [], 'partial': True, 'errors': [{'error': 'later query timed out'}]}
        result = jobs.queue_enrichment('dns', 'example.com')
        self.assertTrue(result['run']['result']['partial'])
        self.assertTrue(AssetRelationship.objects.filter(relation='resolves_to').exists())

    @patch('leakscope.phase3.asset_jobs.collect_dns')
    def test_cancelled_run_never_collects(self, collect):
        with patch('leakscope.phase3.asset_jobs.run_asset_enrichment.apply_async') as submit:
            result = jobs.queue_enrichment('dns', 'example.com')
        run = Run.objects.get(pk=result['run']['id'])
        run.state = 'cancelled'; run.save(update_fields=['state'])
        jobs.run_asset_enrichment.apply(kwargs=submit.call_args.kwargs['kwargs'], task_id=run.task_id)
        collect.assert_not_called()

    def test_invalid_inputs_never_queue_jobs(self):
        for kind, target in [('dns', '*.example.com'), ('dns', 'https://example.com'), ('resolve', 'example.com'), ('netblock', 'not an ip'), ('unknown', 'example.com')]:
            with self.subTest(kind=kind, target=target), self.assertRaises(ValueError):
                jobs.queue_enrichment(kind, target)
        with self.assertRaises(ValueError): jobs.queue_enrichment('organization', 'Acme', max_requests=3)
        self.assertFalse(Run.objects.exists())


class WatchCaptureJobTests(TestCase):
    def setUp(self):
        self.watch = WatchRule.objects.create(name='Capture', domains=['example.com'], ports=[443], actions=['http'], auto_fetch=True)
        self.record = CertificateRecord.objects.create(key='a'*64, domains=['api.example.com', '*.example.com'])
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root_patch = patch('leakscope.phase3.asset_jobs.capture_root', return_value=Path(self.temp.name))
        self.root_patch.start(); self.addCleanup(self.root_patch.stop)

    @patch('leakscope.phase3.asset_jobs.capture_endpoint')
    def test_successful_http_capture_creates_observation_only_after_response(self, capture):
        artifact = Path(self.temp.name) / 'response.body'; artifact.write_bytes(b'fixture')
        def response(row, action, config):
            self.assertFalse(ServiceEndpoint.objects.exists())
            self.assertEqual(row['address'], 'api.example.com')
            return {'metadata': {'status': 200, 'title': 'Fixture'}, 'artifacts': [{'path': str(artifact), 'kind': 'response'}]}
        capture.side_effect = response
        set_tags('watch', self.watch.pk, ['review'])
        run = jobs.queue_watch_capture(self.record, self.watch, 'api.example.com')
        self.assertEqual(run.state, 'succeeded')
        endpoint = ServiceEndpoint.objects.get()
        self.assertEqual(endpoint.port, 443)
        self.assertEqual(tag_names('endpoint', endpoint.pk), ['review'])
        observation = ServiceObservation.objects.get()
        self.assertEqual(observation.provider, 'fetch:http')
        self.assertEqual(observation.data['metadata']['certificate_id'], str(self.record.pk))
        self.assertEqual(EvidenceArtifact.objects.get().run_id, run.pk)
        self.assertTrue(AssetRelationship.objects.filter(relation='observed_service').exists())

    @patch('leakscope.phase3.asset_jobs.capture_endpoint', side_effect=ValueError('refused'))
    def test_unreachable_candidate_never_creates_service_identity(self, capture):
        run = jobs.queue_watch_capture(self.record, self.watch, 'api.example.com')
        self.assertIn(run.state, ['failed', 'succeeded'])
        self.assertFalse(ServiceEndpoint.objects.exists())
        self.assertFalse(ServiceObservation.objects.exists())

    @patch('leakscope.phase3.asset_jobs.capture_endpoint', return_value={'metadata': {'title': 'No response evidence'}, 'artifacts': []})
    def test_empty_capture_result_is_not_reachability_evidence(self, capture):
        jobs.queue_watch_capture(self.record, self.watch, 'api.example.com')
        self.assertFalse(ServiceEndpoint.objects.exists())

    @patch('leakscope.phase3.asset_jobs.capture_watch_certificate.apply_async')
    def test_observe_only_and_wildcards_never_dispatch_or_create_endpoints(self, submit):
        self.assertIsNone(jobs.queue_watch_capture(self.record, self.watch, '*.example.com'))
        self.watch.auto_fetch = False
        self.assertIsNone(jobs.queue_watch_capture(self.record, self.watch, 'api.example.com'))
        submit.assert_not_called()
        self.assertFalse(ServiceEndpoint.objects.exists())

    @patch('leakscope.phase3.asset_jobs.capture_watch_certificate.apply_async')
    def test_cooldown_and_daily_budget_apply_per_candidate_port(self, submit):
        self.watch.ports = [80, 443]; self.watch.max_daily_fetches = 1
        first = jobs.queue_watch_capture(self.record, self.watch, 'api.example.com')
        self.assertIsNotNone(first)
        self.assertEqual(len(submit.call_args.kwargs['kwargs']['snapshot']['ports']), 1)
        self.assertIsNone(jobs.queue_watch_capture(self.record, self.watch, 'api.example.com'))
        self.assertEqual(submit.call_count, 1)

    @patch('leakscope.phase3.asset_jobs.capture_watch_certificate.apply_async')
    @patch('leakscope.phase3.asset_jobs.capture_endpoint')
    def test_queued_capture_keeps_frozen_ports_but_stops_if_watch_disabled(self, capture, submit):
        run = jobs.queue_watch_capture(self.record, self.watch, 'api.example.com')
        snapshot = submit.call_args.kwargs['kwargs']['snapshot']
        self.watch.ports = [8080]; self.watch.enabled = False
        self.watch.save(update_fields=['ports', 'enabled'])
        self.assertEqual(snapshot['ports'], [443])
        jobs.capture_watch_certificate.apply(kwargs={'snapshot': snapshot}, task_id=run.task_id)
        capture.assert_not_called()
        self.assertFalse(ServiceEndpoint.objects.exists())

    @patch('leakscope.phase3.asset_jobs.capture_endpoint')
    def test_invalid_artifact_does_not_persist_partial_service(self, capture):
        capture.return_value = {'metadata': {'status': 200}, 'artifacts': [{'path': '/outside/unsafe', 'kind': 'response'}]}
        jobs.queue_watch_capture(self.record, self.watch, 'api.example.com')
        self.assertFalse(ServiceEndpoint.objects.exists())
        self.assertFalse(EvidenceArtifact.objects.exists())

    @patch('leakscope.phase3.asset_jobs.capture_endpoint', return_value={'metadata': {'reachable': True}, 'artifacts': []})
    def test_connection_check_does_not_claim_http_protocol(self, capture):
        self.watch.actions = ['check']
        run = jobs.queue_watch_capture(self.record, self.watch, 'api.example.com')
        self.assertEqual(run.state, 'succeeded')
        endpoint = ServiceEndpoint.objects.get()
        self.assertEqual(endpoint.scheme, '')
        self.assertEqual(endpoint.service_type, '')

    @patch('leakscope.phase3.asset_jobs.capture_endpoint', return_value={'metadata': {'status': 200}, 'artifacts': []})
    def test_attempt_budget_reports_unattempted_ports_as_partial(self, capture):
        self.watch.ports = [80, 443]
        with patch('leakscope.phase3.asset_jobs.MAX_CAPTURE_ATTEMPTS', 1):
            run = jobs.queue_watch_capture(self.record, self.watch, 'api.example.com')
        result = json.loads(run.result)
        self.assertTrue(result['partial'])
        self.assertTrue(result['limited'])
        self.assertEqual(capture.call_count, 1)

    @patch('leakscope.phase3.asset_jobs.capture_endpoint')
    def test_partial_connection_trace_does_not_claim_http_protocol(self, capture):
        from leakscope.phase3.capture import CaptureEvidenceError
        capture.side_effect = CaptureEvidenceError('incomplete HTTP', {'metadata': {'wire_summary': {'connection_established': True}, 'partial': True}, 'artifacts': []})
        run = jobs.queue_watch_capture(self.record, self.watch, 'api.example.com')
        self.assertEqual(run.state, 'succeeded')
        endpoint = ServiceEndpoint.objects.get()
        self.assertEqual(endpoint.scheme, '')
        self.assertEqual(endpoint.service_type, '')

    @patch('leakscope.phase3.asset_jobs.capture_endpoint')
    def test_blacklist_blocks_followup_before_capture(self, capture):
        with patch('leakscope.phase3.asset_jobs.get_config', return_value={'config': {'blacklist': ['api.example.com']}}):
            jobs.queue_watch_capture(self.record, self.watch, 'api.example.com')
        capture.assert_not_called()
        self.assertFalse(ServiceEndpoint.objects.exists())


class AssetCacheFreshnessTests(TestCase):
    @patch('leakscope.phase3.asset_jobs.collect_dns')
    def test_zero_ttl_is_never_reused_even_for_partial_results(self, collect):
        collect.return_value = {'target': 'example.com', 'records': [{'name': 'example.com', 'type': 'A', 'value': '192.0.2.1', 'ttl': 0}], 'services': [], 'partial': True, 'errors': ['other query failed']}
        jobs.queue_enrichment('dns', 'example.com')
        result = jobs.queue_enrichment('dns', 'example.com')
        self.assertFalse(result['cached'])
        self.assertEqual(collect.call_count, 2)

    @patch('leakscope.phase3.asset_jobs.collect_dns')
    def test_cancellation_during_adapter_does_not_materialize_returned_facts(self, collect):
        with patch('leakscope.phase3.asset_jobs.run_asset_enrichment.apply_async') as submit:
            queued = jobs.queue_enrichment('dns', 'example.com')
        run = Run.objects.get(pk=queued['run']['id'])
        def cancelled(*args, **kwargs):
            Run.objects.filter(pk=run.pk).update(state='cancelled')
            return {'target': 'example.com', 'records': [{'name': 'example.com', 'type': 'A', 'value': '192.0.2.1', 'ttl': 60}], 'services': []}
        collect.side_effect = cancelled
        jobs.run_asset_enrichment.apply(kwargs=submit.call_args.kwargs['kwargs'], task_id=run.task_id)
        self.assertFalse(AssetRelationship.objects.filter(relation='resolves_to').exists())
        self.assertFalse(AssetEvidence.objects.filter(source='enrichment:dns').exists())

    @patch('leakscope.phase3.asset_jobs.free_registry.discover_organization')
    def test_organization_cache_is_insensitive_to_name_and_alias_casing(self, discover):
        discover.return_value = {'organization': 'Acme', 'domains': [], 'networks': [], 'ips': [], 'asns': [], 'partial': False, 'errors': []}
        jobs.queue_enrichment('organization', 'Acme', aliases=['Acme Group'])
        result = jobs.queue_enrichment('organization', 'ACME', aliases=['ACME GROUP'])
        self.assertTrue(result['cached'])
        self.assertEqual(discover.call_count, 1)
