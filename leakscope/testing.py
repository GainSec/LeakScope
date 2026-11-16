"""Test-only transport guard: fixtures may use loopback, external I/O fails."""

import ipaddress
import socket
from urllib.parse import urlsplit
from unittest.mock import patch
import requests
from django.test.runner import DiscoverRunner


def allowed_host(host):
    if isinstance(host, bytes):
        host = host.decode("ascii", errors="replace")
    host = str(host).strip("[]").rstrip(".").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class NoExternalNetwork:
    def __init__(self):
        self.blocked = []
        self.patches = []

    def check(self, host):
        if not allowed_host(host):
            self.blocked.append(str(host))
            raise AssertionError(
                "Unexpected external network request blocked: " + str(host)
            )

    def __enter__(self):
        original_request = requests.Session.request
        original_dns = socket.getaddrinfo
        original_connect = socket.socket.connect
        original_connect_ex = socket.socket.connect_ex
        guard = self

        def request(session, method, url, *args, **kwargs):
            guard.check(urlsplit(str(url)).hostname)
            return original_request(session, method, url, *args, **kwargs)

        def dns(host, *args, **kwargs):
            if host is not None:
                guard.check(host)
            return original_dns(host, *args, **kwargs)

        def connect(sock, address):
            if isinstance(address, tuple):
                guard.check(address[0])
            return original_connect(sock, address)

        def connect_ex(sock, address):
            if isinstance(address, tuple):
                guard.check(address[0])
            return original_connect_ex(sock, address)

        self.patches = [
            patch.object(requests.Session, "request", request),
            patch.object(socket, "getaddrinfo", dns),
            patch.object(socket.socket, "connect", connect),
            patch.object(socket.socket, "connect_ex", connect_ex),
        ]
        for item in self.patches:
            item.start()
        return self

    def __exit__(self, *args):
        for item in reversed(self.patches):
            item.stop()


class SafeDiscoverRunner(DiscoverRunner):
    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        self.network_guard = NoExternalNetwork()
        self.network_guard.__enter__()

    def teardown_test_environment(self, **kwargs):
        self.network_guard.__exit__()
        super().teardown_test_environment(**kwargs)
        if self.network_guard.blocked:
            raise AssertionError(
                "Tests attempted external requests: "
                + ", ".join(sorted(set(self.network_guard.blocked)))
            )
