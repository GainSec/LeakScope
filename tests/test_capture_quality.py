import json
import tempfile
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch
from django.test import SimpleTestCase
from leakscope.phase3.capture import capture_endpoint

class ScreenshotQualityTests(SimpleTestCase):
    def screenshot(self, report, blocked=0):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        endpoint={'address':'127.0.0.1','port':8080,'scheme':'http','service_type':'http','transport':'tcp'}
        def tool(argv,workdir,timeout):
            Path(argv[argv.index('--write-jsonl-file')+1]).write_text(json.dumps(dict(url='http://127.0.0.1:8080/',final_url='http://127.0.0.1:8080/',**report))+'\n')
            (Path(argv[argv.index('--screenshot-path')+1])/'fixture.png').write_bytes(b'\x89PNG\r\n\x1a\nfixture')
            return b''
        state={'bytes':0,'expired':False,'blocked':blocked}
        with patch('leakscope.phase3.capture._run',side_effect=tool),patch('leakscope.phase3.capture._version',return_value='fixture'),patch('leakscope.phase3.capture.same_origin_proxy',return_value=nullcontext(('http://127.0.0.1:1',state))):
            return capture_endpoint(endpoint,'screenshot',{'CAPTURE_DIR':temporary.name,'SCREENSHOT_OPTIONS':{'backend':'gowitness'}})

    def test_blocked_resources_are_partial_and_preserve_actual_image(self):
        result=self.screenshot({'response_code':200,'title':'Public homepage'},blocked=3)
        self.assertTrue(result.get('partial'))
        metadata=result['metadata']
        self.assertEqual(metadata['status'],200)
        self.assertEqual(metadata['title'],'Public homepage')
        self.assertEqual(metadata['capture_quality']['state'],'restricted')
        self.assertIn('3',metadata['capture_quality']['reasons'][0])
        self.assertEqual(metadata['capture_quality']['visual_completeness'],'unassessed')
        self.assertEqual(len(result['artifacts']),2)
        self.assertEqual(result['artifacts'][0]['kind'],'screenshot')
        self.assertTrue(Path(result['artifacts'][0]['path']).is_file())

    def test_browser_challenge_is_not_reported_as_complete_page(self):
        result=self.screenshot({'response_code':403,'title':'Checking your browser...'})
        self.assertTrue(result.get('partial'))
        self.assertEqual(result['metadata']['capture_quality']['state'],'challenge')
        self.assertEqual(result['metadata']['status'],403)
        self.assertTrue(any('verification' in r for r in result['metadata']['capture_quality']['reasons']))

    def test_actual_http_error_is_retained_with_quality_warning(self):
        result=self.screenshot({'response_code':404,'title':'Not found'})
        self.assertTrue(result.get('partial'))
        self.assertEqual(result['metadata']['capture_quality']['state'],'http_error')
        self.assertEqual(result['metadata']['status'],404)

    def test_clean_response_does_not_claim_visual_completeness(self):
        result=self.screenshot({'response_code':200,'title':'Public homepage'})
        self.assertFalse(result.get('partial'))
        quality=result['metadata']['capture_quality']
        self.assertEqual(quality['state'],'unassessed')
        self.assertEqual(quality['reasons'],[])
        self.assertEqual(quality['visual_completeness'],'unassessed')

    def test_missing_or_malformed_status_cannot_claim_complete_navigation(self):
        for value in [None,True,999,{},'bad']:
            with self.subTest(value=value):
                result=self.screenshot({'response_code':value,'title':'Public homepage'})
                self.assertTrue(result.get('partial'))
                self.assertIsNone(result['metadata']['status'])
                self.assertTrue(any('status' in r for r in result['metadata']['capture_quality']['reasons']))
