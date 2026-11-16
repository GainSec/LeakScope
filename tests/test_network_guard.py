import socket
from unittest import TestCase
import requests


class NetworkGuardTests(TestCase):
    def test_requests_block_before_network_transport(self):
        from leakscope.testing import NoExternalNetwork

        with NoExternalNetwork():
            with self.assertRaisesRegex(AssertionError, "external"):
                requests.get("https://example.invalid/never-send")

    def test_dns_blocks_before_resolution(self):
        from leakscope.testing import NoExternalNetwork

        with NoExternalNetwork():
            with self.assertRaisesRegex(AssertionError, "external"):
                socket.getaddrinfo("example.invalid", 443)

    def test_loopback_fixture_is_allowed(self):
        from leakscope.testing import allowed_host

        self.assertTrue(allowed_host("127.0.0.1"))
        self.assertTrue(allowed_host("::1"))
        self.assertFalse(allowed_host("api.shodan.io"))
