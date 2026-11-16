"""New saved response captures acquire public local signals without a manual run."""
import hashlib
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from django.db import transaction
from django.test import TestCase, override_settings

from leakscope_app.models import EvidenceArtifact, EvidenceCorrelation, Run, ServiceEndpoint, ServiceObservation


class FutureCaptureSignalTests(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        settings = override_settings(LEAKSCOPE_CAPTURE_DIR=self.root)
        settings.enable()
        self.addCleanup(settings.disable)
        self.endpoint = ServiceEndpoint.objects.create(key='future-capture', address='192.0.2.8', port=9443)
        self.observation = ServiceObservation.objects.create(endpoint=self.endpoint, provider='fetch:http', fingerprint='response')

    def capture(self, body=b"fbq('init', '123456789012345');", *, kind='http', metadata=None, commit=True, **extra):
        filename = extra.pop('relative_path', 'response.txt')
        if filename == 'response.txt':
            (self.root / filename).write_bytes(body)
        with self.captureOnCommitCallbacks(execute=commit):
            return EvidenceArtifact.objects.create(
                endpoint=self.endpoint, kind=kind, relative_path=filename,
                size=extra.pop('size', len(body)), sha256=extra.pop('sha256', hashlib.sha256(body).hexdigest()),
                mime='application/json', tool='fixture',
                metadata={'observation_pk': str(self.observation.pk)} if metadata is None else metadata,
                **extra,
            )

    def public_signals(self):
        return EvidenceCorrelation.objects.filter(kind__startswith='tracking.').union(
            EvidenceCorrelation.objects.filter(kind__startswith='declaration.'))

    def test_new_response_capture_indexes_public_ids_and_keeps_exact_artifact_proof_without_network(self):
        with patch('requests.get', side_effect=AssertionError('No HTTP')), patch('socket.getaddrinfo', side_effect=AssertionError('No DNS')):
            artifact = self.capture()
        signal = EvidenceCorrelation.objects.get(kind='tracking.meta_pixel')
        self.assertEqual(signal.value, '123456789012345')
        self.assertEqual(signal.endpoint_id, self.endpoint.pk)
        self.assertEqual(signal.artifact_id, artifact.pk)
        self.assertEqual(signal.first_seen, artifact.created_at)
        self.assertEqual(signal.last_seen, artifact.created_at)
        self.assertTrue(EvidenceCorrelation.objects.filter(kind='artifact.http.sha256', artifact=artifact).exists())

    def test_all_response_kinds_and_full_httpx_body_support_api_declarations(self):
        definition = {'openapi': '3.1.0', 'servers': [{'url': 'https://api.example.com:8443/v1?secret=discard'}]}
        for kind in ('http', 'httpx', 'response', 'http_response'):
            value = {'url': 'https://docs.example.com/schema', 'status_code': 200, 'body': json.dumps(definition)} if kind == 'httpx' else definition
            artifact = self.capture(json.dumps(value).encode(), kind=kind)
            signal = EvidenceCorrelation.objects.get(kind='declaration.openapi.server')
            self.assertEqual(signal.value, 'https://api.example.com:8443')
            self.assertEqual(signal.artifact_id, artifact.pk)
        self.assertEqual(EvidenceCorrelation.objects.filter(kind='declaration.openapi.server').count(), 1)

    def test_creation_extracts_once_and_metadata_or_other_saves_do_not_repeat_file_reads(self):
        from leakscope.phase3.local_correlations import _read_artifact
        with patch('leakscope.phase3.local_correlations._read_artifact', wraps=_read_artifact) as read:
            artifact = self.capture()
            artifact.metadata['note'] = 'curation'
            artifact.save(update_fields=['metadata'])
            artifact.save()
        self.assertEqual(read.call_count, 1)
        self.assertEqual(self.public_signals().count(), 1)

    def test_hidden_or_wrong_endpoint_source_never_opens_file_or_indexes_public_signals(self):
        hidden = ServiceObservation.objects.create(endpoint=self.endpoint, provider='fixture', fingerprint='hidden', hidden=True)
        other = ServiceEndpoint.objects.create(key='other-endpoint', address='192.0.2.9', port=9443)
        foreign = ServiceObservation.objects.create(endpoint=other, provider='fixture', fingerprint='foreign')
        for proof in (hidden, foreign):
            with patch('leakscope.phase3.local_correlations._read_artifact') as read:
                self.capture(metadata={'observation_pk': str(proof.pk)})
            read.assert_not_called()
        self.assertFalse(self.public_signals().exists())

    def test_declared_and_actual_file_limits_skip_automatic_extraction_without_losing_capture(self):
        with patch('leakscope.phase3.local_correlations._read_artifact') as read:
            large = self.capture(size=1024 * 1024 + 1)
        read.assert_not_called()
        self.assertTrue(EvidenceArtifact.objects.filter(pk=large.pk).exists())
        with self.assertLogs('leakscope_app.signals', level='WARNING'):
            mismatched = self.capture(b'x' * (1024 * 1024 + 1), size=10)
        self.assertTrue(EvidenceCorrelation.objects.filter(artifact=mismatched, kind='artifact.http.sha256').exists())
        self.assertFalse(self.public_signals().exists())

    def test_unsafe_paths_symlinks_and_modified_content_leave_original_artifact_and_hash(self):
        (self.root / 'target.txt').write_bytes(b"fbq('init', '123456789012345');")
        (self.root / 'link.txt').symlink_to(self.root / 'target.txt')
        for extra in ({'relative_path': '../target.txt'}, {'relative_path': 'link.txt'}, {'sha256': '0' * 64}):
            with self.assertLogs('leakscope_app.signals', level='WARNING'):
                artifact = self.capture(**extra)
            self.assertTrue(EvidenceArtifact.objects.filter(pk=artifact.pk).exists())
            self.assertTrue(EvidenceCorrelation.objects.filter(artifact=artifact, kind='artifact.http.sha256').exists())
        self.assertFalse(self.public_signals().exists())

    def test_extraction_error_preserves_hash_index_and_logs_no_response_content(self):
        with patch('leakscope.phase3.local_correlations.response_signals', side_effect=RuntimeError('private payload')):
            with self.assertLogs('leakscope_app.signals', level='WARNING') as logs:
                artifact = self.capture()
        self.assertNotIn('private payload', str(logs.output))
        self.assertTrue(EvidenceCorrelation.objects.filter(artifact=artifact, kind='artifact.http.sha256').exists())
        self.assertFalse(self.public_signals().exists())

    def test_outer_transaction_rollback_leaves_no_orphan_signal(self):
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            with self.assertRaises(RuntimeError):
                with transaction.atomic():
                    artifact = self.capture(commit=False)
                    raise RuntimeError('rollback')
        self.assertEqual(callbacks, [])
        self.assertFalse(EvidenceArtifact.objects.filter(pk=artifact.pk).exists())
        self.assertFalse(EvidenceCorrelation.objects.exists())

    def test_post_commit_reloads_final_source_binding_before_any_file_read(self):
        hidden = ServiceObservation.objects.create(endpoint=self.endpoint, provider='fixture', fingerprint='hidden-final', hidden=True)
        with patch('leakscope.phase3.local_correlations._read_artifact') as read:
            with self.captureOnCommitCallbacks(execute=True):
                artifact = self.capture(metadata={}, commit=False)
                self.assertFalse(self.public_signals().exists())
                artifact.metadata = {'observation_pk': str(hidden.pk)}
                artifact.save(update_fields=['metadata'])
        read.assert_not_called()
        self.assertFalse(self.public_signals().exists())

    def test_existing_artifact_manual_backfill_stays_available(self):
        from leakscope.phase3.local_correlations import execute_index
        body = b"fbq('init', '123456789012345');"
        (self.root / 'old.txt').write_bytes(body)
        artifact = EvidenceArtifact(endpoint=self.endpoint, kind='response', relative_path='old.txt', size=len(body),
            sha256=hashlib.sha256(body).hexdigest(), metadata={'observation_pk': str(self.observation.pk)}, tool='fixture')
        EvidenceArtifact.objects.bulk_create([artifact])
        self.assertFalse(self.public_signals().exists())
        run = Run.objects.create(kind='correlation_index', task_id='old-response-backfill')
        result = execute_index(run.pk, run.task_id, [str(artifact.pk)])
        self.assertEqual(result['indexed'], 1)
        self.assertEqual(EvidenceCorrelation.objects.get(kind='tracking.meta_pixel').artifact_id, artifact.pk)


class PassiveProviderSignalTests(TestCase):
    def test_normalized_provider_bodies_index_api_and_marketing_with_exact_observation_proof(self):
        from leakscope.phase3.providers import _normalize
        from leakscope.phase3.state import ingest_observation
        definition = json.dumps({'openapi': '3.1.0', 'info': {'description': 'GTM-ABC123'},
                                 'servers': [{'url': 'https://api.example.com:8443/v1'}]})
        envelope = 'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n' + definition
        cases = [
            ('shodan', {'http': {'html': definition}}),
            ('shodan', {'data': envelope}),
            ('zoomeye', {'http': {'body': definition}}),
            ('zoomeye', {'banner': envelope}),
            ('netlas', {'http': {'response': {'body': definition}}}),
        ]
        for index, (provider, fields) in enumerate(cases, 1):
            raw = {'ip_str' if provider == 'shodan' else 'ip': f'192.0.2.{index}', 'port': 9443,
                   'service': 'https', **fields}
            source = {'data': raw} if provider == 'netlas' else raw
            with patch('requests.get', side_effect=AssertionError('No HTTP')), patch('socket.getaddrinfo', side_effect=AssertionError('No DNS')):
                normalized = _normalize(provider, source, {})
                endpoint = ingest_observation(provider, normalized)
            observation = endpoint.observations.get(provider=provider)
            signals = EvidenceCorrelation.objects.filter(endpoint=endpoint, kind__in=['tracking.google_tag_manager', 'declaration.openapi.server'])
            self.assertEqual(signals.count(), 2)
            self.assertEqual(set(signals.values_list('value', flat=True)), {'GTM-ABC123', 'https://api.example.com:8443'})
            for signal in signals:
                self.assertEqual(signal.observation_id, observation.pk)
                self.assertEqual(list(signal.observations.all()), [observation])
                self.assertIsNone(signal.artifact_id)

    def test_only_known_provider_response_fields_are_examined(self):
        from leakscope.phase3.analysis import correlation_signals
        body = "fbq('init','123456789012345'); GTM-ABC123"
        ignored = {'credentials': {'body': body}, 'error': body, 'preview': body, 'title': body,
                   'data': body, 'banner': body, 'http': {'headers': {'x-secret': body}},
                   'arbitrary': {'http': {'body': body}}}
        for provider in ('shodan', 'zoomeye', 'netlas', 'fetch:http', 'unknown'):
            self.assertEqual(correlation_signals(ignored, provider), [])
        self.assertEqual(correlation_signals({'http': {'body': body}}, 'fetch:http'), [])
        self.assertEqual(correlation_signals({'data': {'http': {'body': body}}}, 'netlas'), [])

    def test_retained_body_processing_has_shared_byte_budget_and_public_signal_cap(self):
        from leakscope.phase3.analysis import correlation_signals
        from leakscope.phase3.local_correlations import response_signals
        bodies = {'http': {'html': 'x' * 700000, 'body': 'y' * 700000, 'response': {'body': 'z' * 100}}}
        with patch('leakscope.phase3.local_correlations.response_signals', wraps=response_signals) as process:
            self.assertEqual(correlation_signals(bodies, 'shodan'), [])
        lengths = [len(call.args[0]) for call in process.call_args_list]
        self.assertEqual(lengths, [700000, 100])
        self.assertLessEqual(sum(lengths), 1024 * 1024)
        self.assertEqual(correlation_signals({'http': {'body': 'é' * 600000}}, 'netlas'), [])
        definition = {'openapi': '3.1.0', 'info': {'description': ' '.join(f'GTM-ABC{index:04}' for index in range(64))},
                      'servers': [{'url': f'https://host{index}.example.com/'} for index in range(64)]}
        signals = correlation_signals({'http': {'body': json.dumps(definition)}, 'body_sha256': 'ab' * 32}, 'netlas')
        self.assertLessEqual(len([row for row in signals if row[0].startswith(('tracking.', 'declaration.'))]), 64)
        self.assertIn(('http.body.sha256', 'ab' * 32), signals)

    def test_hidden_public_body_proof_is_suppressed_without_hiding_unrelated_endpoint_evidence(self):
        from leakscope.phase3.state import ingest_observation
        from leakscope_app.workspace import _correlation_candidates
        row = {'address': '192.0.2.77', 'port': 443, 'scheme': 'https', 'metadata': {'http': {'html': 'GTM-ABC123'}}}
        endpoint = ingest_observation('shodan', row)
        proof = endpoint.observations.get(provider='shodan')
        ingest_observation('zoomeye', {**row, 'metadata': {'http': {'headers': {'server': 'visible'}}}})
        self.assertEqual(_correlation_candidates(None).filter(kind='tracking.google_tag_manager').count(), 1)
        proof.hidden = True
        proof.save(update_fields=['hidden'])
        self.assertFalse(_correlation_candidates(None).filter(kind='tracking.google_tag_manager').exists())
        ingest_observation('zoomeye', row)
        signal = EvidenceCorrelation.objects.get(kind='tracking.google_tag_manager')
        self.assertEqual(signal.observations.count(), 2)
        self.assertTrue(_correlation_candidates(None).filter(pk=signal.pk).exists())
