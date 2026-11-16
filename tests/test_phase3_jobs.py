from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase
from django.utils import timezone
from leakscope_app.models import SavedLookup, ServiceEndpoint, ServiceObservation, DispatchSlot
from leakscope.phase3.jobs import run_lookup, dispatch_due, queue_capture, next_occurrence


class CollectionJobTests(TestCase):
    def setUp(self):
        self.lookup = SavedLookup.objects.create(name='Fixture', provider='netlas', query='port:443', service_type='https', enabled=True, interval_seconds=3600, next_run_at=timezone.now()-timedelta(seconds=1))

    @patch('leakscope.phase3.jobs.search_provider')
    def test_lookup_saves_partial_observations_and_never_marks_missing_endpoints_offline(self, search):
        search.return_value = {'observations': [{'address': '192.0.2.2', 'port': 443, 'metadata': {}, 'service_type': 'https'}], 'partial': True, 'errors': ['later page failed'], 'pages': 1, 'more_available': True}
        result = run_lookup.apply(args=[str(self.lookup.pk)]).get()
        self.assertTrue(result['partial'])
        self.assertEqual(ServiceObservation.objects.count(), 1)
        search.return_value = {'observations': [], 'partial': False, 'errors': [], 'pages': 1, 'more_available': False}
        run_lookup.apply(args=[str(self.lookup.pk)]).get()
        self.assertEqual(ServiceEndpoint.objects.count(), 1)

    @patch('leakscope.phase3.jobs.run_lookup.apply_async')
    def test_due_slot_is_unique_and_missed_intervals_coalesce(self, submit):
        with self.captureOnCommitCallbacks(execute=True):
            dispatch_due()
            dispatch_due()
        self.assertEqual(DispatchSlot.objects.count(), 1)
        self.assertEqual(submit.call_count, 1)
        self.lookup.refresh_from_db()
        self.assertGreater(self.lookup.next_run_at, timezone.now())

    @patch('leakscope.phase3.jobs.enqueue')
    def test_capture_cooldown_prevents_repeated_auto_fetch(self, enqueue):
        from leakscope.phase3.state import ingest_observation
        from leakscope_app.models import Run
        endpoint = ingest_observation('netlas', {'address': '192.0.2.1', 'port': 443})
        enqueue.return_value = Run.objects.create(kind='capture')
        self.assertIsNotNone(queue_capture(endpoint.pk, ['http'], origin='fixture'))
        self.assertIsNone(queue_capture(endpoint.pk, ['http'], origin='fixture'))
        self.assertEqual(enqueue.call_count, 1)

    def test_cron_uses_selected_timezone_and_invalid_schedule_rejected(self):
        now = timezone.now()
        result = next_occurrence(interval_seconds=None, cron='0 9 * * *', timezone_name='America/New_York', now=now)
        self.assertGreater(result, now)
        self.assertLess(result, now + timedelta(days=2))
        with self.assertRaises(ValueError): next_occurrence(cron='bad cron', now=now)

    @patch('leakscope.phase3.jobs.capture_endpoint')
    def test_invalid_artifacts_do_not_turn_all_error_capture_into_success(self, capture):
        from leakscope.phase3.jobs import fetch_endpoint
        from leakscope.phase3.state import ingest_observation
        row = ingest_observation('netlas', {'address': '192.0.2.1', 'port': 80, 'scheme': 'http'})
        capture.return_value = {'metadata': {'status': 200}, 'artifacts': [{'path': '/outside/unsafe', 'kind': 'response'}]}
        with self.assertRaises(ValueError): fetch_endpoint.apply(args=[str(row.pk), ['http']]).get()

    @patch('leakscope.phase3.jobs.enqueue')
    @patch('leakscope.phase3.jobs._claim_daily_budget', side_effect=[False, True])
    def test_rejected_budget_does_not_lock_another_origins_capture(self, budget, enqueue):
        from leakscope.phase3.state import ingest_observation
        from leakscope_app.models import Run
        endpoint = ingest_observation('netlas', {'address': '192.0.2.9', 'port': 443})
        enqueue.return_value = Run.objects.create(kind='capture')
        self.assertIsNone(queue_capture(endpoint.pk, ['http'], origin='limited'))
        self.assertIsNotNone(queue_capture(endpoint.pk, ['http'], origin='allowed'))
        self.assertEqual(budget.call_count, 2)

    @patch('leakscope.phase3.jobs.enqueue')
    def test_manual_lookups_obey_shared_daily_budget(self, enqueue):
        from leakscope.phase3.jobs import queue_lookup
        from leakscope_app.models import Run
        self.lookup.max_daily_runs = 1; self.lookup.save()
        enqueue.return_value = Run.objects.create(kind='lookup')
        queue_lookup(self.lookup)
        second = queue_lookup(self.lookup)
        self.assertEqual(second.state, 'skipped')
        self.assertEqual(enqueue.call_count, 1)

    @patch('leakscope.services.runs.get_config')
    def test_durable_metadata_redacts_all_integrations_before_json_encoding(self, config):
        from leakscope.services.runs import _data
        config.return_value = {'config': {'NETLAS_API_KEY': 'netlas-secret', 'LLM_API_KEY': 'model-"secret', 'FIRECRAWL_API_KEY': 'web-secret'}}
        value = {'nested': ['netlas-secret', {'model-"secret': 'web-secret'}]}
        encoded = _data(value)
        self.assertNotIn('netlas-secret', encoded)
        self.assertNotIn('web-secret', encoded)
        import json
        self.assertNotIn('model-"secret', str(json.loads(encoded)))

    @patch('leakscope.phase3.jobs.fetch_endpoint.apply_async')
    def test_capture_snapshots_the_observed_resource_when_queued(self, submit):
        from leakscope.phase3.state import ingest_observation
        endpoint = ingest_observation('netlas', {'address': 'example.test', 'port': 443, 'scheme': 'https',
            'metadata': {'uri': 'https://example.test/admin/console?token=private'}})
        run = queue_capture(endpoint.pk, ['http'], cooldown_seconds=0)
        self.assertEqual(submit.call_args.kwargs['kwargs']['snapshot']['path'], '/admin/console')
        self.assertIn('/admin/console', run.parameters)

    @patch('leakscope.phase3.jobs.run_lookup.apply_async')
    def test_outbox_recovers_broker_publish_failure_with_same_logical_task(self, submit):
        submit.side_effect = [RuntimeError('broker unavailable'), object()]
        with self.captureOnCommitCallbacks(execute=True): dispatch_due()
        slot = DispatchSlot.objects.get()
        self.assertEqual(slot.state, 'pending')
        original_run = slot.run_id
        task_id = submit.call_args.kwargs['task_id']
        DispatchSlot.objects.filter(pk=slot.pk).update(next_attempt_at=timezone.now()-timedelta(seconds=1))
        with self.captureOnCommitCallbacks(execute=True): dispatch_due()
        slot.refresh_from_db()
        self.assertEqual(slot.state, 'submitted')
        self.assertEqual(slot.run_id, original_run)
        self.assertEqual(submit.call_args.kwargs['task_id'], task_id)
        self.assertEqual(submit.call_count, 2)

    def test_duplicate_delivery_does_not_skip_the_original_running_job(self):
        from leakscope_app.models import Run
        from leakscope.phase3.state import acquire_lease
        row = Run.objects.create(task_id='fixture-duplicate', kind='lookup', state='running')
        acquire_lease('lookup-task:fixture-duplicate', 3600)
        result = run_lookup.apply(args=[str(self.lookup.pk)], task_id='fixture-duplicate')
        row.refresh_from_db()
        self.assertEqual(row.state, 'running')
        self.assertEqual(result.state, 'IGNORED')

    def test_remote_invalid_text_is_safe_for_terminal_run_storage(self):
        from leakscope.services.runs import write_state
        from leakscope_app.models import Run
        row = Run.objects.create(task_id='fixture-invalid-text', kind='lookup')
        write_state(row.task_id, 'FAILURE', {'error': 'remote\x00failure\ud800'})
        row.refresh_from_db()
        self.assertEqual(row.state, 'failed')
        self.assertNotIn('\x00', row.error)
        self.assertNotIn('\ud800', row.error)
        self.assertNotIn('\\u0000', row.result)
        self.assertNotIn('\\ud800', row.result)

    @patch('leakscope.phase3.jobs.search_provider')
    def test_malformed_provider_row_preserves_valid_partial_collection(self, search):
        search.return_value = {'observations': [{'address': '192.0.2.4', 'port': 443}, {'address': 'bad host', 'port': 443}], 'errors': [], 'partial': False}
        result = run_lookup.apply(args=[str(self.lookup.pk)]).get()
        self.assertEqual(result['stored'], 1)
        self.assertTrue(result['partial'])
        self.assertEqual(result['invalid_rows'], 1)

    def test_capture_housekeeping_removes_only_expired_registered_files_and_leases(self):
        import tempfile
        from pathlib import Path
        from django.test import override_settings
        from leakscope_app.models import EvidenceArtifact, ExecutionLease
        from leakscope.phase3.jobs import cleanup_captures
        from leakscope.phase3.state import ingest_observation
        endpoint = ingest_observation('netlas', {'address': '192.0.2.3', 'port': 80})
        with tempfile.TemporaryDirectory() as folder, override_settings(LEAKSCOPE_CAPTURE_DIR=folder, LEAKSCOPE_CAPTURE_RETENTION_DAYS=30):
            Path(folder, 'old.txt').write_text('old')
            Path(folder, 'unregistered.txt').write_text('keep')
            artifact = EvidenceArtifact.objects.create(endpoint=endpoint, relative_path='old.txt', kind='response', size=3)
            EvidenceArtifact.objects.filter(pk=artifact.pk).update(created_at=timezone.now()-timedelta(days=31))
            ExecutionLease.objects.create(key='expired-fixture', expires_at=timezone.now()-timedelta(seconds=1))
            cleanup_captures()
            self.assertFalse(Path(folder, 'old.txt').exists())
            self.assertTrue(Path(folder, 'unregistered.txt').exists())
            self.assertFalse(EvidenceArtifact.objects.filter(pk=artifact.pk).exists())
            self.assertFalse(ExecutionLease.objects.filter(pk='expired-fixture').exists())
