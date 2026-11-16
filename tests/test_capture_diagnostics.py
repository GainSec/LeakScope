import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from leakscope.phase3.capture_diagnostics import CaptureDiagnosticError, CaptureBatchError, error_detail
from leakscope.phase3.capture import _run


class CaptureDiagnosticTests(SimpleTestCase):
    def test_curl_exit_status_has_safe_meaning_without_exposing_stderr(self):
        class Process:
            pid=987654321
            def __init__(self,code):self.returncode=code
            def poll(self):return self.returncode
        for status,expected in ((60,'tls_certificate'),(28,'timeout'),(7,'connection_failed'),(6,'dns_resolution'),(18,'response_truncated'),(35,'tls_handshake')):
            with self.subTest(status=status),tempfile.TemporaryDirectory() as folder:
                def start(argv,**kwargs):
                    kwargs['stderr'].write(b'Authorization: secret target.example password');return Process(status)
                with patch('leakscope.phase3.capture.subprocess.Popen',side_effect=start),patch('leakscope.phase3.capture.os.killpg'),self.assertRaises(CaptureDiagnosticError) as caught:
                    _run(['curl','--url','https://target.example/'],Path(folder),1)
                self.assertEqual(caught.exception.code,expected)
                self.assertNotIn('target.example',str(caught.exception));self.assertNotIn('secret',str(caught.exception))

    def test_untrusted_exception_codes_are_not_diagnostic_codes(self):
        class RemoteError(ValueError):code='secret target.example'
        detail=error_detail(RemoteError('plain error'),'identity')
        self.assertEqual(detail['code'],'capture_failed');self.assertEqual(detail['action'],'identity')


class CaptureFailurePersistenceTests(TestCase):
    @patch('leakscope.phase3.jobs.capture_endpoint')
    def test_all_failed_capture_persists_structured_reasons_in_terminal_run(self,capture):
        from leakscope.phase3.state import ingest_observation
        from leakscope.phase3.jobs import queue_capture
        from django.test import override_settings
        endpoint=ingest_observation('fixture',{'address':'192.0.2.45','port':443,'scheme':'https','service_type':'https'})
        capture.side_effect=CaptureDiagnosticError('tls_certificate')
        with override_settings(CELERY_TASK_EAGER_PROPAGATES=False):
            with self.captureOnCommitCallbacks(execute=True):run=queue_capture(endpoint.pk,['http'],cooldown_seconds=0)
        run.refresh_from_db();data=json.loads(run.result)
        self.assertEqual(run.state,'failed')
        self.assertEqual(data['error_details'][0]['code'],'tls_certificate')
        self.assertEqual(data['error_details'][0]['action'],'http')
        self.assertIn('certificate',run.error.lower())

    def test_capture_batch_failure_survives_failure_state_serialization(self):
        from leakscope_app.models import Run
        from leakscope.services.runs import write_state
        row=Run.objects.create(task_id='fixture-capture-diagnostic',kind='capture',state='running')
        write_state(row.task_id,'FAILURE',CaptureBatchError({'results':{},'artifact_ids':[],'partial':True,
            'errors':['http: certificate verification failed'],'error_details':[{'action':'http','code':'tls_certificate','message':'Certificate validation failed'}]}))
        row.refresh_from_db()
        self.assertEqual(json.loads(row.result)['error_details'][0]['code'],'tls_certificate')
