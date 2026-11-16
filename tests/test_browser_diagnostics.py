"""Controller failures retain stable safe codes without disclosing transport details."""
import json
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from leakscope.phase3 import browser_client


class BrowserDiagnosticsTests(TestCase):
    def test_controller_reason_is_allowlisted_not_copied_from_error_text(self):
        with tempfile.TemporaryDirectory() as directory:
            token = Path(directory) / 'token'
            token.write_text('x' * 32)
            for code, expected in [('timeout', 'timeout'), ('tls_certificate', 'tls_certificate'), ('evil http://internal/?secret', 'browser_operation_failed')]:
                with self.subTest(code=code), patch.dict('os.environ', {'BROWSER_CONTROL_TOKEN_FILE': str(token)}), patch.object(browser_client.requests, 'Session') as factory:
                    response = factory.return_value.request.return_value.__enter__.return_value
                    response.status_code = 504
                    response.iter_content.return_value = [json.dumps({'code': code, 'error': 'secret http://internal/?token=x'}).encode()]
                    with self.assertRaises(ValueError) as caught:
                        browser_client.request('POST', '/capture', {})
                    self.assertEqual(caught.exception.code, expected)
                    self.assertEqual(caught.exception.status, 504)
                    self.assertNotIn('secret', str(caught.exception))
                    self.assertNotIn('internal', str(caught.exception))

    def test_controller_transport_timeout_has_its_own_code(self):
        with tempfile.TemporaryDirectory() as directory:
            token = Path(directory) / 'token'
            token.write_text('x' * 32)
            with patch.dict('os.environ', {'BROWSER_CONTROL_TOKEN_FILE': str(token)}), patch.object(browser_client.requests, 'Session') as factory:
                factory.return_value.request.side_effect = browser_client.requests.Timeout('secret internal URL')
                with self.assertRaises(ValueError) as caught:
                    browser_client.request('POST', '/capture', {})
                self.assertEqual(caught.exception.code, 'controller_timeout')
                self.assertNotIn('secret', str(caught.exception))
