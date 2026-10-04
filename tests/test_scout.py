import io
import json
import tempfile
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from resource_scout.net import Client, ScoutError, normalize, validate_url, Redirects
from resource_scout.inspect import inspect_url, sniff
from resource_scout.download import download_file
from resource_scout.discovery import search, candidate, enrich_archive, searxng, gutendex
from resource_scout.report import render

PAYLOAD = b"%PDF-1.7\n" + b"fixture bytes\n" * 16000


class Handler(BaseHTTPRequestHandler):
    short_sent = False
    counts = {}

    def log_message(self, *args):
        pass

    def do_GET(self):
        route = urlsplit(self.path).path
        Handler.counts[route] = Handler.counts.get(route, 0) + 1
        if route == "/robots.txt":
            return self.send(200, b"User-agent: *\nDisallow: /blocked\n", "text/plain")
        if route == "/redirect":
            return self.send(302, b"", "text/plain", {"Location": "/file.pdf"})
        if route == "/redirect-blocked":
            return self.send(302, b"", "text/plain", {"Location": "/blocked"})
        if route == "/json":
            return self.send(200, b'{"ok":true}', "application/json")
        if route == "/search":
            return self.send(200, b'{"results":[{"title":"Example book","url":"https://example.org/book.pdf","content":"Candidate"}]}', "application/json")
        if route == "/fake.pdf":
            return self.send(200, b"<!doctype html><html><title>Sign in</title><body>Sign in to read this book</body></html>", "application/pdf")
        if route == "/page":
            return self.send(200, b'<html><title>A sample chapter</title><a href="/file.pdf">PDF</a><a href="javascript:alert(1)">bad</a><script type="application/ld+json">{"isAccessibleForFree": false}</script></html>', "text/html")
        if route == "/denied":
            return self.send(403, b"Forbidden", "text/plain")
        if route == "/rate":
            return self.send(429, b"Wait", "text/plain", {"Retry-After": "120"})
        if route == "/short" and not Handler.short_sent:
            Handler.short_sent = True
            return self.send(200, PAYLOAD[:70000], "application/pdf", {"ETag": '"v1"'}, len(PAYLOAD))
        if route in {"/file.pdf", "/short", "/changed", "/bad-range", "/ignore"}:
            range_header = self.headers.get("Range")
            if range_header and route != "/ignore":
                start, _, end = range_header.removeprefix("bytes=").partition("-")
                start = int(start)
                stop = min(int(end) + 1, len(PAYLOAD)) if end else len(PAYLOAD)
                declared_start = start + 1 if route == "/bad-range" else start
                return self.send(206, PAYLOAD[start:stop], "application/pdf", {
                    "ETag": '"v2"' if route == "/changed" else '"v1"',
                    "Content-Range": f"bytes {declared_start}-{stop-1}/{len(PAYLOAD)}"})
            return self.send(200, PAYLOAD, "application/pdf", {"ETag": '"v1"'})
        return self.send(404, b"not found", "text/plain")

    def send(self, status, body, mime, headers=None, length=None):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", len(body) if length is None else length)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


class FixtureClient(Client):
    # Explicitly test-only; production has no allow-local CLI option.
    def validate(self, url):
        if urlsplit(url).hostname != "127.0.0.1":
            raise ScoutError("Fixture attempted external connection")
        return url


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.client = FixtureClient(":memory:", delay=0)

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def url(self, path):
        return self.base + path

    def partial(self, path, count=50000):
        target = self.root / "book.pdf"
        target.with_name("book.pdf.part").write_bytes(PAYLOAD[:count])
        target.with_name("book.pdf.part.json").write_text(json.dumps({
            "url": self.url(path), "validator_key": "ETag", "validator_value": '"v1"', "mime": "application/pdf"}))
        return target

    def test_probe_is_bounded_and_not_complete(self):
        result = inspect_url(self.client, self.url("/file.pdf"))
        self.assertEqual(result["bytes_observed"], 128 * 1024)
        self.assertEqual(result["access"], "file_bytes_observed")
        self.assertEqual(result["completeness"], "unknown")

    def test_html_disguised_as_pdf(self):
        result = inspect_url(self.client, self.url("/fake.pdf"))
        self.assertEqual(result["format"], "html")
        self.assertEqual(result["access"], "possible_registration_gate")

    def test_page_discovers_links_and_restriction(self):
        result = inspect_url(self.client, self.url("/page"))
        self.assertEqual(result["access"], "publisher_reports_restriction")
        self.assertEqual(len(result["files"]), 1)
        self.assertTrue(result["completeness"].startswith("possible_partial"))

    def test_403_is_unknown(self):
        result = inspect_url(self.client, self.url("/denied"))
        self.assertEqual(result["access"], "unknown")
        self.assertEqual(result["http_status"], 403)

    def test_robots_denial(self):
        result = inspect_url(self.client, self.url("/blocked"))
        self.assertIn("disallows", result["error"])
        self.assertNotIn("/blocked", Handler.counts)

    def test_redirect(self):
        result = inspect_url(self.client, self.url("/redirect"))
        self.assertEqual(result["resolved_url"], self.url("/file.pdf"))

    def test_redirect_checks_robots(self):
        result = inspect_url(self.client, self.url("/redirect-blocked"))
        self.assertIn("disallows", result["error"])

    def test_long_retry_after_is_not_ignored(self):
        count = Handler.counts.get("/rate", 0)
        inspect_url(self.client, self.url("/rate"))
        self.assertEqual(Handler.counts["/rate"] - count, 1)

    def test_cache_preserves_observation_time(self):
        first, e1 = self.client.json(self.url("/json"))
        second, e2 = self.client.json(self.url("/json"))
        self.assertEqual(first, second)
        self.assertEqual(e1["observed_at"], e2["observed_at"])
        self.assertEqual(self.client.stats["requests"], 1)
        self.assertEqual(self.client.stats["cache_hits"], 1)

    def test_failure_cooldown_avoids_repeat_request(self):
        with self.assertRaises(ScoutError):
            self.client.json(self.url("/denied"))
        requests = self.client.stats["requests"]
        with self.assertRaisesRegex(ScoutError, "cooling down"):
            self.client.json(self.url("/other-api"))
        self.assertEqual(self.client.stats["requests"], requests)

    def test_searxng_protocol(self):
        items = searxng(self.client, "Example book", 2, "book", 3600, self.base)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["url"], "https://example.org/book.pdf")
        self.assertEqual(items[0]["access"], "catalogue_only")

    def test_download_hash_and_no_overwrite(self):
        target = self.root / "book.pdf"
        report = download_file(self.client, self.url("/file.pdf"), target)
        self.assertEqual(target.read_bytes(), PAYLOAD)
        self.assertEqual(report["bytes"], len(PAYLOAD))
        self.assertEqual(len(report["sha256"]), 64)
        self.assertTrue(target.with_name("book.pdf.provenance.json").exists())
        with self.assertRaises(ScoutError):
            download_file(self.client, self.url("/file.pdf"), target)

    def test_download_resume(self):
        target = self.partial("/file.pdf")
        report = download_file(self.client, self.url("/file.pdf"), target)
        self.assertEqual(report["resumed_from_bytes"], 50000)
        self.assertEqual(target.read_bytes(), PAYLOAD)

    def test_server_ignores_range_restart(self):
        target = self.partial("/ignore")
        report = download_file(self.client, self.url("/ignore"), target)
        self.assertEqual(report["resumed_from_bytes"], 0)
        self.assertEqual(target.read_bytes(), PAYLOAD)

    def test_changed_etag_rejected(self):
        target = self.partial("/changed")
        with self.assertRaisesRegex(ScoutError, "validator changed"):
            download_file(self.client, self.url("/changed"), target)
        self.assertFalse(target.exists())

    def test_wrong_content_range_rejected(self):
        target = self.partial("/bad-range")
        with self.assertRaisesRegex(ScoutError, "Content-Range"):
            download_file(self.client, self.url("/bad-range"), target)

    def test_different_url_cannot_reuse_partial(self):
        target = self.partial("/file.pdf")
        with self.assertRaisesRegex(ScoutError, "another/unknown"):
            download_file(self.client, self.url("/ignore"), target)

    def test_size_limit(self):
        with self.assertRaisesRegex(ScoutError, "byte limit"):
            download_file(self.client, self.url("/file.pdf"), self.root / "book.pdf", max_bytes=100)
        self.assertFalse((self.root / "book.pdf").exists())

    def test_html_not_saved_as_book(self):
        with self.assertRaisesRegex(ScoutError, "received html"):
            download_file(self.client, self.url("/fake.pdf"), self.root / "book.pdf")

    def test_interrupted_transfer_auto_recovers(self):
        Handler.short_sent = False
        with patch("resource_scout.download.time.sleep"):
            report = download_file(self.client, self.url("/short"), self.root / "book.pdf")
        self.assertEqual((self.root / "book.pdf").read_bytes(), PAYLOAD)
        self.assertGreater(report["resumed_from_bytes"], 0)


class UnitTests(unittest.TestCase):
    def test_private_addresses_refused(self):
        for ip in ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1"]:
            with patch("socket.getaddrinfo", return_value=[(0, 0, 0, "", (ip, 443))]):
                with self.assertRaises(ScoutError):
                    validate_url("https://example.org/")

    def test_proxy_mode_does_not_allow_other_private_ips(self):
        with patch("socket.getaddrinfo", return_value=[(0, 0, 0, "", ("192.168.1.1", 443))]):
            with self.assertRaises(ScoutError):
                validate_url("https://example.org/", proxy_dns=True)

    def test_fake_dns_requires_explicit_option(self):
        with patch("socket.getaddrinfo", return_value=[(0, 0, 0, "", ("198.18.0.55", 443))]):
            with self.assertRaises(ScoutError):
                validate_url("https://example.org/")
            self.assertEqual(validate_url("https://example.org/", True), "https://example.org/")

    def test_unsafe_urls(self):
        for url in ["file:///etc/passwd", "ftp://example.org/file", "http://user:pass@example.org", "https://example.org/\nfoo"]:
            with self.assertRaises((ScoutError, ValueError)):
                normalize(url)

    def test_nonstandard_port_refused_before_dns(self):
        with self.assertRaises(ScoutError):
            validate_url("https://example.org:8080/")

    def test_query_signature_preserved(self):
        self.assertEqual(normalize("https://Example.org/file?b=2&a=X%2Fy#part"), "https://example.org/file?b=2&a=X%2Fy")

    def test_redirect_validates_destination(self):
        client = unittest.mock.Mock()
        client.validate.side_effect = ScoutError("private destination")
        with self.assertRaisesRegex(ScoutError, "private destination"):
            Redirects(client, True).redirect_request(None, None, 302, "", {}, "http://127.0.0.1/")

    def test_provider_failure_does_not_erase_other_results(self):
        client = unittest.mock.Mock(stats={"requests": 1, "cache_hits": 0})
        with patch("resource_scout.discovery.archive", side_effect=ScoutError("offline")), \
             patch("resource_scout.discovery.openlibrary", return_value=[]), \
             patch("resource_scout.discovery.gutendex", return_value=[candidate("gutendex", "Test Book", "https://example.org/book")]):
            result = search(client, "Test Book", verify=0)
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(next(p for p in result["providers"] if p["name"] == "archive")["status"], "error")

    def test_report_escapes_untrusted_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "report.html"
            render({"results": [candidate("test", '<script>alert("x")</script>', "javascript:alert(1)")]}, file)
            text = file.read_text()
            self.assertNotIn("<script>", text)
            self.assertNotIn('href="javascript:', text)
            self.assertIn("&lt;script&gt;", text)

    def test_magic_wins_over_mime(self):
        self.assertEqual(sniff(b"<html>login</html>", "application/pdf"), "html")
        self.assertEqual(sniff(b"%PDF-1.7", "text/html"), "pdf")

    def test_video_probe_selects_video_not_license_sidecar(self):
        client = unittest.mock.Mock()
        client.json.return_value = ({"files": [
            {"name": "License.txt", "size": "80"},
            {"name": "movie.mp4", "size": "5000000"},
            {"name": "private.mp4", "private": "true", "size": "20"}]}, {})
        item = candidate("archive", "Film", "https://archive.org/details/film", archive_id="film", media_type="movies")
        enrich_archive(client, item, 3600)
        self.assertEqual(len(item["files"]), 1)
        self.assertTrue(item["files"][0]["url"].endswith("movie.mp4"))

    def test_gutendex_provider_metadata_not_access_proof(self):
        client = unittest.mock.Mock()
        client.json.return_value = ({"results": [{"id": 1342, "title": "Pride and Prejudice", "copyright": False,
            "authors": [{"name": "Austen, Jane"}], "formats": {"application/epub+zip": "https://www.gutenberg.org/ebooks/1342.epub3.images"}}]}, {})
        item = gutendex(client, "Austen", 1, "book", 3600)[0]
        self.assertEqual(item["access"], "catalogue_only")
        self.assertIn("USA", item["rights"])
        self.assertEqual(len(item["files"]), 1)


if __name__ == "__main__":
    unittest.main()
