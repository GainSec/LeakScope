"""Loopback TLS fixtures exercise real HTTP framing without contacting clouds."""
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

from django.test import SimpleTestCase


@skipUnless(shutil.which('openssl'), 'Local TLS certificate fixture needs openssl')
class CloudTLSFixtureTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.directory = tempfile.TemporaryDirectory()
        root = Path(cls.directory.name)
        cls.cert, key = root / 'cert.pem', root / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
            '-keyout', str(key), '-out', str(cls.cert), '-subj', '/CN=acme.s3.amazonaws.com',
            '-addext', 'subjectAltName=DNS:*.s3.amazonaws.com'], check=True, capture_output=True)
        cls.requests = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                cls.requests.append((self.headers['Host'], self.path))
                host = self.headers['Host']
                try:
                    if host.startswith('slow.'):
                        for byte in b'HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n':
                            self.wfile.write(bytes([byte])); self.wfile.flush(); time.sleep(.04)
                        return
                    body = b'x' * 70000 if host.startswith('large.') else b'<ListBucketResult><Name>acme</Name></ListBucketResult>'
                    self.send_response(302 if host.startswith('redirect.') else 200)
                    if host.startswith('redirect.'): self.send_header('Location', 'http://127.0.0.1/should-not-be-requested')
                    self.send_header('Content-Length', str(len(body)))
                    self.send_header('Connection', 'close')
                    self.end_headers(); self.wfile.write(body)
                except (OSError, ssl.SSLError): pass
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cls.cert, key)
        cls.server.socket = context.wrap_socket(cls.server.socket, server_side=True)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.directory.cleanup()
        super().tearDownClass()

    def request(self, name='acme', timeout=2):
        from leakscope.phase3.cloud import request_candidate
        context = ssl.create_default_context(cafile=str(self.cert))
        original = socket.socket
        port = self.server.server_port
        class FixtureSocket(original):
            def connect(sock, destination):
                if destination != ('8.8.8.8', 443): raise AssertionError('Unexpected pinned destination')
                return super().connect(('127.0.0.1', port))
        answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))]
        with patch('leakscope.phase3.cloud._resolve', return_value=answers), \
             patch('leakscope.phase3.cloud.socket.socket', FixtureSocket), \
             patch('leakscope.phase3.cloud.ssl.create_default_context', return_value=context):
            return request_candidate({'provider': 'aws', 'kind': 'bucket', 'name': name,
                'url': f'https://{name}.s3.amazonaws.com/'}, {}, verify=True, timeout=timeout)

    def test_tls_host_pin_and_actual_listing_request_response(self):
        result = self.request()
        self.assertEqual(result['status'], 200)
        self.assertIn(b'<Name>acme</Name>', result['body'])
        self.assertEqual(self.requests[-1], ('acme.s3.amazonaws.com', '/?max-keys=1'))
        self.assertEqual(result['resolved_ip'], '8.8.8.8')

    def test_redirect_is_not_followed_and_body_capture_is_bounded(self):
        from leakscope.phase3.cloud import MAX_BODY
        before = len(self.requests)
        self.assertEqual(self.request('redirect')['status'], 302)
        self.assertEqual(len(self.requests), before + 1)
        large = self.request('large')
        self.assertEqual(len(large['body']), MAX_BODY)
        self.assertTrue(large['truncated'])

    def test_slow_headers_cannot_extend_total_deadline(self):
        started = time.monotonic()
        with self.assertRaises(Exception): self.request('slow', timeout=.2)
        self.assertLess(time.monotonic() - started, .8)
