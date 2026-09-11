from __future__ import annotations

import socket
import unittest
from unittest.mock import patch

from analyzer.url_safety import (
    UnsafeUrlError,
    assert_fetchable_url,
    is_fetchable_url,
)

PUBLIC = "https://www.motorsport.com/f1/news/example"


def _resolves_to(*addresses: str):
    """Patch getaddrinfo so tests never depend on real DNS."""

    def fake(host, port=None, *args, **kwargs):
        family = socket.AF_INET6 if ":" in addresses[0] else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (addr, 0)) for addr in addresses]

    return patch("analyzer.url_safety.socket.getaddrinfo", side_effect=fake)


class SchemeTests(unittest.TestCase):
    def test_http_and_https_are_allowed(self) -> None:
        with _resolves_to("93.184.216.34"):
            self.assertEqual(assert_fetchable_url("http://example.com/a"), "http://example.com/a")
            self.assertEqual(assert_fetchable_url("https://example.com/a"), "https://example.com/a")

    def test_dangerous_schemes_are_rejected(self) -> None:
        for url in (
            "file:///etc/passwd",
            "ftp://example.com/x",
            "gopher://example.com/x",
            "javascript:alert(1)",
            "data:text/html,<script>",
        ):
            with self.subTest(url=url):
                with self.assertRaises(UnsafeUrlError):
                    assert_fetchable_url(url)

    def test_empty_and_hostless_urls_are_rejected(self) -> None:
        for url in ("", "   ", "http://", "https:///path"):
            with self.subTest(url=url):
                with self.assertRaises(UnsafeUrlError):
                    assert_fetchable_url(url)


class HostAndAddressTests(unittest.TestCase):
    def test_localhost_names_are_rejected(self) -> None:
        for url in (
            "http://localhost/admin",
            "http://localhost.localdomain/",
            "http://foo.localhost/",
            "http://printer.local/",
            "http://metadata.google.internal/computeMetadata/v1/",
            "http://instance-data/",
        ):
            with self.subTest(url=url):
                with self.assertRaises(UnsafeUrlError):
                    assert_fetchable_url(url, resolve_dns=False)

    def test_private_and_loopback_literals_are_rejected(self) -> None:
        for url in (
            "http://127.0.0.1/",
            "http://127.0.0.1:8080/status",
            "http://10.0.0.5/",
            "http://192.168.1.1/",
            "http://172.16.0.1/",
            "http://172.31.255.254/",
            "http://0.0.0.0/",
            "http://100.64.0.1/",
            "http://198.18.0.1/",
        ):
            with self.subTest(url=url):
                with self.assertRaises(UnsafeUrlError):
                    assert_fetchable_url(url, resolve_dns=False)

    def test_cloud_metadata_address_is_rejected(self) -> None:
        with self.assertRaises(UnsafeUrlError):
            assert_fetchable_url("http://169.254.169.254/latest/meta-data/", resolve_dns=False)

    def test_ipv6_loopback_and_unique_local_are_rejected(self) -> None:
        for url in ("http://[::1]/", "http://[fd00::1]/", "http://[fe80::1]/"):
            with self.subTest(url=url):
                with self.assertRaises(UnsafeUrlError):
                    assert_fetchable_url(url, resolve_dns=False)

    def test_a_public_address_literal_is_allowed(self) -> None:
        self.assertEqual(assert_fetchable_url("http://8.8.8.8/", resolve_dns=False), "http://8.8.8.8/")


class DnsRebindingTests(unittest.TestCase):
    def test_a_public_name_resolving_to_a_private_address_is_rejected(self) -> None:
        with _resolves_to("127.0.0.1"):
            with self.assertRaises(UnsafeUrlError) as ctx:
                assert_fetchable_url("http://evil.example.com/")
        self.assertIn("resolves to blocked address", str(ctx.exception))

    def test_a_public_name_resolving_to_metadata_is_rejected(self) -> None:
        with _resolves_to("169.254.169.254"):
            with self.assertRaises(UnsafeUrlError):
                assert_fetchable_url("http://evil.example.com/")

    def test_a_public_name_resolving_to_a_public_address_is_allowed(self) -> None:
        with _resolves_to("93.184.216.34"):
            self.assertEqual(
                assert_fetchable_url("https://example.com/article"), "https://example.com/article"
            )

    def test_dns_resolution_can_be_skipped(self) -> None:
        with _resolves_to("127.0.0.1"):
            self.assertEqual(
                assert_fetchable_url("https://example.com/a", resolve_dns=False),
                "https://example.com/a",
            )

    def test_unresolvable_host_is_rejected(self) -> None:
        with patch(
            "analyzer.url_safety.socket.getaddrinfo",
            side_effect=socket.gaierror("nodename nor servname provided"),
        ):
            with self.assertRaises(UnsafeUrlError):
                assert_fetchable_url("https://does-not-exist.invalid/")


class HelperTests(unittest.TestCase):
    def test_is_fetchable_url_returns_booleans(self) -> None:
        self.assertFalse(is_fetchable_url("http://127.0.0.1/", resolve_dns=False))
        with _resolves_to("93.184.216.34"):
            self.assertTrue(is_fetchable_url(PUBLIC))


class FetchIntegrationTests(unittest.TestCase):
    def test_fetch_article_content_refuses_private_urls(self) -> None:
        from analyzer.article_fetcher import fetch_article_content

        result = fetch_article_content("http://169.254.169.254/latest/meta-data/", {})

        self.assertEqual(result["fetch_status"], "unsafe")
        self.assertEqual(result["article_content"], "")

    def test_fetch_article_content_skips_known_non_articles(self) -> None:
        from analyzer.article_fetcher import fetch_article_content

        result = fetch_article_content("https://www.youtube.com/watch?v=x", {})

        self.assertEqual(result["fetch_status"], "skipped")

    def test_redirect_to_a_private_address_is_refused(self) -> None:
        from analyzer import article_fetcher

        class FakeResponse:
            def __init__(self, status, headers=None):
                self.status_code = status
                self.headers = headers or {}

        calls: list[str] = []

        def fake_request(target=None, **kwargs):
            calls.append(target)
            return FakeResponse(302, {"Location": "http://127.0.0.1/admin"})

        with patch.object(article_fetcher.requests, "get", side_effect=fake_request):
            with self.assertRaises(UnsafeUrlError):
                article_fetcher._fetch_following_safe_redirects(
                    "https://example.com/article", 5, {}, None, "article"
                )

        self.assertEqual(len(calls), 1, "the redirect target must never be requested")


if __name__ == "__main__":
    unittest.main()
