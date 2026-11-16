"""Browser policy, target binding, validation and saved artifact boundaries."""
import base64
import tempfile
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from django.test import TestCase, SimpleTestCase, Client
from django.utils import timezone
from datetime import timedelta
from leakscope.phase3.jobs import validate_capture_options
from leakscope.phase3.state import ingest_observation
from leakscope_app.models import ExecutionLease


class ScreenshotOptionsTests(SimpleTestCase):
    def test_default_is_playwright_strict_and_values_are_frozen(self):
        options = validate_capture_options(['screenshot'], {'screenshot': {'profile': 'web_page', 'wait_seconds': 3}})
        self.assertEqual(options['screenshot']['backend'], 'playwright')
        self.assertEqual(options['screenshot']['profile'], 'web_page')
        self.assertEqual(options['screenshot']['wait_seconds'], 3)

    def test_rejects_unknown_unbounded_and_incompatible_values(self):
        for value in [{'profile': 'anything'}, {'wait_seconds': True}, {'wait_seconds': 11},
                      {'timeout_seconds': 100}, {'full_page': 'yes'}, {'url': 'http://evil.test'},
                      {'session_id': 'bad'}, {'backend': 'gowitness', 'profile': 'web_page'}]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_capture_options(['screenshot'], {'screenshot': value})

    def test_controller_artifact_is_decoded_and_published_not_trusted_path(self):
        from leakscope.phase3.browser_client import publish_capture
        png = b'\x89PNG\r\n\x1a\nfixture'
        result = {'metadata': {'title': 'Fixture', 'partial': True}, 'partial': True,
                  'artifacts': [{'base64': base64.b64encode(png).decode(), 'kind': 'screenshot', 'mime': 'image/png', 'extension': '.png'}]}
        with tempfile.TemporaryDirectory() as directory:
            capture = publish_capture(result, Path(directory))
            self.assertEqual(Path(capture['artifacts'][0]['path']).read_bytes(), png)
            self.assertEqual(capture['artifacts'][0]['tool'], 'playwright')
            self.assertTrue(capture['partial'])
        result['artifacts'][0]['extension'] = '../escape'
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(ValueError):
            publish_capture(result, Path(directory))


class BrowserSessionAPITests(TestCase):
    def setUp(self):
        self.endpoint = ingest_observation('fixture', {'address':'example.com','port':443,'scheme':'https','service_type':'https'})
        self.other = ingest_observation('fixture', {'address':'other.example.com','port':443,'scheme':'https','service_type':'https'})
        self.session = str(uuid4())

    @patch('leakscope.phase3.browser_client.request')
    def test_session_is_bound_to_visible_endpoint_and_input_not_saved(self, controller):
        controller.return_value = {'id': self.session, 'expires_at': (timezone.now()+timedelta(minutes=10)).isoformat(), 'viewport': {'width':1280,'height':800}}
        response = self.client.post('/api/browser/sessions', {'endpoint_id': str(self.endpoint.pk), 'options':{'profile':'web_page'}}, content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        data=response.json()['session']
        self.assertEqual(data['id'], self.session)
        self.assertIn('/frame',data['frame_url'])
        lease=ExecutionLease.objects.get(pk='browser-session:'+self.session)
        self.assertEqual(lease.data['endpoint_id'],str(self.endpoint.pk))
        controller.return_value={'ok':True}
        self.assertEqual(self.client.post('/api/browser/sessions/'+self.session+'/input', {'type':'text','text':'test'}, content_type='application/json').status_code,200)
        self.assertNotIn('test',str(ExecutionLease.objects.get(pk=lease.pk).data))
        self.endpoint.observations.update(hidden=True)
        self.assertEqual(self.client.get('/api/browser/sessions/'+self.session).status_code,404)

    def test_invalid_uuid_input_and_csrf_are_rejected(self):
        self.assertEqual(self.client.get('/api/browser/sessions/not-uuid').status_code,400)
        self.assertEqual(Client(enforce_csrf_checks=True).post('/api/browser/sessions',{}, content_type='application/json').status_code,403)

    @patch('leakscope.phase3.jobs.enqueue')
    def test_session_capture_cannot_be_reused_by_another_endpoint(self, enqueue):
        from leakscope.phase3.jobs import queue_capture
        ExecutionLease.objects.create(key='browser-session:'+self.session,expires_at=timezone.now()+timedelta(minutes=5),data={'endpoint_id':str(self.endpoint.pk)})
        with self.assertRaises(ValueError):
            queue_capture(self.other.pk,['screenshot'],options={'screenshot':{'session_id':self.session}},cooldown_seconds=0)
        enqueue.assert_not_called()


class BrowserSnapshotTests(TestCase):
    def setUp(self):
        self.endpoint=ingest_observation('fixture',{'address':'example.com','port':443,'scheme':'https','service_type':'https'})
        self.sid=str(uuid4())
        ExecutionLease.objects.create(key='browser-session:'+self.sid,expires_at=timezone.now()+timedelta(minutes=5),data={'endpoint_id':str(self.endpoint.pk),'options':{'profile':'web_page'}})

    @patch('leakscope.phase3.jobs.enqueue')
    @patch('leakscope.phase3.browser_client.request')
    def test_capture_freezes_pixels_before_queue_and_survives_session_close(self,controller,enqueue):
        from leakscope.phase3.browser_client import capture
        from leakscope.phase3.jobs import endpoint_snapshot
        png=b'\x89PNG\r\n\x1a\nfirst-frame'
        controller.return_value={'metadata':{'manual_session':True},'artifacts':[{'kind':'screenshot','mime':'image/png','extension':'.png','base64':base64.b64encode(png).decode()}]}
        enqueue.return_value=None
        with tempfile.TemporaryDirectory() as directory, self.settings(LEAKSCOPE_CAPTURE_DIR=directory):
            with patch('leakscope_app.browser.serialize_run',return_value={'id':'fixture'}):
                response=self.client.post('/api/browser/sessions/'+self.sid+'/capture',{},content_type='application/json')
            self.assertEqual(response.status_code,200,response.content)
            frozen=enqueue.call_args.kwargs['kwargs']['snapshot']
            self.assertIn('snapshot_id',frozen['screenshot_options'])
            ExecutionLease.objects.filter(pk='browser-session:'+self.sid).delete()
            result=capture(frozen,{'SCREENSHOT_OPTIONS':frozen['screenshot_options']},Path(directory))
            self.assertEqual(Path(result['artifacts'][0]['path']).read_bytes(),png)
            self.assertEqual(controller.call_count,1)

    @patch('leakscope.phase3.browser_client.request')
    def test_hidden_endpoint_session_can_still_close_without_frames(self,controller):
        controller.return_value={'closed':True}
        self.endpoint.observations.update(hidden=True)
        self.assertEqual(self.client.get('/api/browser/sessions/'+self.sid+'/frame').status_code,404)
        response=self.client.post('/api/browser/sessions/'+self.sid+'/close',{},content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        self.assertFalse(ExecutionLease.objects.filter(pk='browser-session:'+self.sid).exists())

    @patch('leakscope.phase3.browser_client.request')
    def test_snapshot_lease_failure_removes_untracked_published_files(self,controller):
        from django.db import DatabaseError
        controller.return_value={'metadata':{},'artifacts':[{'kind':'screenshot','mime':'image/png','extension':'.png','base64':base64.b64encode(b'\x89PNG\r\n\x1a\nfixture').decode()}]}
        with tempfile.TemporaryDirectory() as directory, self.settings(LEAKSCOPE_CAPTURE_DIR=directory):
            with patch('leakscope_app.browser.ExecutionLease.objects.create',side_effect=DatabaseError('fixture')):
                self.assertEqual(self.client.post('/api/browser/sessions/'+self.sid+'/capture',{},content_type='application/json').status_code,500)
            self.assertFalse(list(Path(directory).iterdir()))

    def test_claim_renews_near_expiry_snapshot_before_housekeeping(self):
        from leakscope.phase3 import browser_client as client
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'frame.png';path.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
            sid=str(uuid4());now=timezone.now()
            row=ExecutionLease.objects.create(key='browser-snapshot:'+sid,expires_at=now+timedelta(seconds=1),data={'endpoint_id':str(self.endpoint.pk),'capture':{'metadata':{},'artifacts':[{'path':str(path)}]}})
            client.capture({'id':str(self.endpoint.pk)},{'SCREENSHOT_OPTIONS':{'snapshot_id':sid}},root)
            with patch('django.utils.timezone.now',return_value=now+timedelta(seconds=2)):
                client.cleanup_snapshots(root)
            self.assertTrue(path.exists())
            row.refresh_from_db();self.assertGreater(row.expires_at,now+timedelta(minutes=1))
