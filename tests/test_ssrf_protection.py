import socket
import unittest
from unittest import mock

import aiohttp

from utils.network_security import (
    NetworkSecurityError,
    create_isolated_session,
    secure_request,
    validate_outbound_headers,
    validate_public_url,
    validate_smtp_target,
)


def dns_answers(*addresses):
    answers = []
    for address in addresses:
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        sockaddr = (address, 0, 0, 0) if family == socket.AF_INET6 else (address, 0)
        answers.append((family, socket.SOCK_STREAM, 6, "", sockaddr))
    return answers


class UrlValidationTests(unittest.TestCase):
    def test_rejects_loopback_private_and_special_hosts(self):
        for url in (
            "http://127.0.0.1/",
            "http://[::1]/",
            "http://10.0.0.8/",
            "http://localhost/",
            "http://metadata/",
            "http://service.local/",
            "http://service.internal/",
            "http://host.home.arpa/",
        ):
            with self.subTest(url=url), self.assertRaises(NetworkSecurityError):
                validate_public_url(url)

    def test_rejects_dangerous_protocol_userinfo_and_port(self):
        for url in (
            "file:///etc/passwd",
            "gopher://public.example/resource",
            "http://user:pass@public.example/",
            "http://public.example:22/",
        ):
            with self.subTest(url=url), self.assertRaises(NetworkSecurityError):
                with mock.patch("utils.network_security.socket.getaddrinfo", return_value=dns_answers("93.184.216.34")):
                    validate_public_url(url)

    def test_rejects_private_and_mixed_dns_answers(self):
        for answers in (
            dns_answers("10.0.0.1"),
            dns_answers("93.184.216.34", "192.168.1.8"),
        ):
            with self.assertRaises(NetworkSecurityError):
                with mock.patch("utils.network_security.socket.getaddrinfo", return_value=answers):
                    validate_public_url("https://public.example/path")

    def test_accepts_all_public_dns_answers(self):
        answers = dns_answers("93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946")
        with mock.patch("utils.network_security.socket.getaddrinfo", return_value=answers):
            target = validate_public_url("https://public.example/path")
        self.assertEqual(target.port, 443)
        self.assertEqual(target.addresses, ("93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"))

    def test_rejects_routing_headers(self):
        for name in ("Host", "Proxy-Authorization", "X-Forwarded-Host"):
            with self.subTest(name=name), self.assertRaises(NetworkSecurityError):
                validate_outbound_headers({name: "internal.example"})


class IsolatedRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_isolated_session_has_no_cookie_jar_or_environment_proxy(self):
        with mock.patch("utils.network_security.socket.getaddrinfo", return_value=dns_answers("93.184.216.34")):
            target = validate_public_url("https://public.example/path")
        session = await create_isolated_session(target)
        try:
            self.assertIsInstance(session.cookie_jar, aiohttp.DummyCookieJar)
            self.assertFalse(session.trust_env)
            session.cookie_jar.update_cookies({"xianyu_cookie": "secret"})
            self.assertEqual(list(session.cookie_jar), [])
        finally:
            await session.close()

    async def test_secure_request_never_follows_redirects(self):
        class FakeResponse:
            status = 302

        class FakeRequestContext:
            async def __aenter__(self):
                return FakeResponse()

            async def __aexit__(self, *_args):
                return False

        class FakeSession:
            def __init__(self):
                self.kwargs = None
                self.closed = False

            def request(self, *_args, **kwargs):
                self.kwargs = kwargs
                return FakeRequestContext()

            async def close(self):
                self.closed = True

        fake_session = FakeSession()
        with mock.patch("utils.network_security.socket.getaddrinfo", return_value=dns_answers("93.184.216.34")), \
                mock.patch("utils.network_security.create_isolated_session", mock.AsyncMock(return_value=fake_session)):
            async with secure_request("GET", "https://public.example/redirect") as response:
                self.assertEqual(response.status, 302)
        self.assertIs(fake_session.kwargs["allow_redirects"], False)
        self.assertTrue(fake_session.closed)


class SmtpValidationTests(unittest.TestCase):
    def test_allows_only_secure_smtp_modes_on_public_dns(self):
        with mock.patch("utils.network_security.socket.getaddrinfo", return_value=dns_answers("93.184.216.34")):
            self.assertEqual(validate_smtp_target("smtp.example.com", 465, True, False), ("93.184.216.34",))
            self.assertEqual(validate_smtp_target("smtp.example.com", 587, False, True), ("93.184.216.34",))
            for args in ((25, False, False), (465, False, True), (587, True, False), (587, False, False)):
                with self.subTest(args=args), self.assertRaises(NetworkSecurityError):
                    validate_smtp_target("smtp.example.com", *args)

    def test_rejects_private_smtp_dns(self):
        with mock.patch("utils.network_security.socket.getaddrinfo", return_value=dns_answers("127.0.0.1")):
            with self.assertRaises(NetworkSecurityError):
                validate_smtp_target("smtp.example.com", 587, False, True)


if __name__ == "__main__":
    unittest.main()
