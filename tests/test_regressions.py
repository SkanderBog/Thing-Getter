"""Relocation and fault-injection checks; no public network is required."""
import io
import json
import subprocess
import tempfile
import sys
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.client import HTTPException
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

from resource_scout.cli import main
from resource_scout.discovery import candidate, search
from resource_scout.download import download_file
from resource_scout.inspect import inspect_url, sniff
from resource_scout.media import download_video, inspect_video
from resource_scout.net import ScoutError
from test_scout import FixtureClient, Handler, PAYLOAD


class EdgeHandler(Handler):
    truncated_robots = False

    def do_GET(self):
        if self.path == '/robots.txt' and self.truncated_robots:
            return self.send(200, b'User-agent: *\n', 'text/plain', length=200)
        if self.path == '/broken-api':
            return self.send(200, b'{"results":[]}', 'application/json', length=200)
        if self.path == '/comment.pdf':
            return self.send(200, b'<!-- cached login page --><html><body>Sign in to read</body></html>', 'text/plain')
        if self.path == '/empty.pdf':
            return self.send(200, b'', 'application/pdf')
        if self.path == '/compressed.pdf':
            return self.send(200, PAYLOAD, 'application/pdf', {'Content-Encoding': 'gzip'})
        if self.path == '/no-length.pdf':
            self.send_response(200)
            self.send_header('Content-Type', 'application/pdf')
            self.end_headers()
            try:
                self.wfile.write(PAYLOAD)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        return super().do_GET()


class RequestRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), EdgeHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.client = FixtureClient(':memory:', delay=0)

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def test_truncated_api_is_not_cached_as_success(self):
        with self.assertRaises(ScoutError):
            self.client.json(self.base + '/broken-api')
        with self.assertRaisesRegex(ScoutError, 'cooling down'):
            self.client.json(self.base + '/json')

    def test_truncated_robots_defers_download(self):
        EdgeHandler.truncated_robots = True
        try:
            result = inspect_url(self.client, self.base + '/file.pdf')
        finally:
            EdgeHandler.truncated_robots = False
        self.assertIn('truncated', result['error'])
        self.assertEqual(result['access'], 'unknown')
        self.assertEqual(self.client.stats['requests'], 1)

    def test_http_protocol_errors_retry_then_return_scout_error(self):
        opener = Mock()
        opener.open.side_effect = HTTPException('invalid HTTP status line')
        with patch('resource_scout.net.build_opener', return_value=opener), patch('resource_scout.net.time.sleep'):
            with self.assertRaisesRegex(ScoutError, 'Network request failed'):
                with self.client.open(self.base + '/file.pdf', robots=False):
                    self.fail('A broken response must not be yielded')
        self.assertEqual(opener.open.call_count, 3)

    def test_commented_html_is_not_downloaded_as_text(self):
        target = self.root / 'book.txt'
        with self.assertRaisesRegex(ScoutError, 'received html'):
            download_file(self.client, self.base + '/comment.pdf', target)
        self.assertFalse(target.exists())

    def test_commented_html_probe_reports_gate(self):
        result = inspect_url(self.client, self.base + '/comment.pdf')
        self.assertEqual(result['access'], 'possible_registration_gate')

    def test_empty_file_never_published(self):
        target = self.root / 'empty.pdf'
        with self.assertRaises(ScoutError):
            download_file(self.client, self.base + '/empty.pdf', target)
        self.assertFalse(target.exists())

    def test_encoded_transfer_never_published(self):
        with self.assertRaisesRegex(ScoutError, 'Encoded transfer'):
            download_file(self.client, self.base + '/compressed.pdf', self.root / 'book.pdf')

    def test_unknown_length_honors_streaming_budget(self):
        target = self.root / 'book.pdf'
        with self.assertRaisesRegex(ScoutError, 'byte limit'):
            download_file(self.client, self.base + '/no-length.pdf', target, max_bytes=70000)
        self.assertFalse(target.exists())
        self.assertLessEqual(target.with_suffix('.pdf.part').stat().st_size, 70000)

    def test_unknown_length_has_qualified_receipt(self):
        receipt = download_file(self.client, self.base + '/no-length.pdf', self.root / 'book.pdf')
        self.assertEqual(receipt['bytes'], len(PAYLOAD))
        self.assertIn('no total length', receipt['transfer_integrity'])

    def test_symlinked_state_cannot_modify_other_file(self):
        other = self.root / 'keep.txt'
        other.write_text('keep')
        target = self.root / 'book.pdf'
        target.with_suffix('.pdf.part').symlink_to(other)
        with self.assertRaisesRegex(ScoutError, 'symlink'):
            download_file(self.client, self.base + '/file.pdf', target)
        self.assertEqual(other.read_text(), 'keep')

    def test_non_object_resume_state_is_rejected_cleanly(self):
        target = self.root / 'book.pdf'
        target.with_suffix('.pdf.part').write_bytes(PAYLOAD[:100])
        target.with_suffix('.pdf.part.json').write_text('[]')
        with self.assertRaises(ScoutError):
            download_file(self.client, self.base + '/file.pdf', target)

    def test_partial_without_validator_restarts(self):
        target = self.root / 'book.pdf'
        target.with_suffix('.pdf.part').write_bytes(b'stale data')
        target.with_suffix('.pdf.part.json').write_text(json.dumps({'url': self.base + '/file.pdf'}))
        receipt = download_file(self.client, self.base + '/file.pdf', target)
        self.assertEqual(receipt['resumed_from_bytes'], 0)
        self.assertEqual(target.read_bytes(), PAYLOAD)


class ProviderRegressionTests(unittest.TestCase):
    def run_search(self, hits, **kwargs):
        client = Mock(stats={'requests': 0, 'cache_hits': 0})
        with patch('resource_scout.discovery.archive', return_value=hits), \
             patch('resource_scout.discovery.openlibrary', return_value=[]), \
             patch('resource_scout.discovery.gutendex', return_value=[]):
            return search(client, 'Test book', verify=0, **kwargs)

    def test_bad_result_does_not_discard_valid_matches(self):
        good = candidate('archive', 'Test book', 'https://example.org/good')
        bad = candidate('archive', 'Test book', 'javascript:alert(1)')
        result = self.run_search([bad, None, good])
        self.assertEqual([r['url'] for r in result['results']], ['https://example.org/good'])
        self.assertEqual(next(p for p in result['providers'] if p['name'] == 'archive')['invalid_result_count'], 2)

    def test_string_author_is_one_author(self):
        item = candidate('archive', 'Test book', 'https://example.org/good', authors='Jane Austen')
        result = self.run_search([item])
        self.assertEqual(result['results'][0]['authors'], ['Jane Austen'])

    def test_null_title_does_not_crash_ranking(self):
        result = self.run_search([candidate('archive', None, 'https://example.org/good')])
        self.assertEqual(result['results'][0]['title'], 'https://example.org/good')

    def test_bad_file_links_are_removed(self):
        item = candidate('archive', 'Test book', 'https://example.org/good', files=[
            None, {'url': 'file:///etc/passwd'}, {'url': 'https://example.org/book.pdf'}])
        result = self.run_search([item])
        self.assertEqual(result['results'][0]['files'], [{'url': 'https://example.org/book.pdf'}])

    def test_duplicates_preserve_one_result(self):
        items = [candidate('archive', 'Test book', 'https://EXAMPLE.org/book#one'),
                 candidate('archive', 'Test book', 'https://example.org/book#two')]
        self.assertEqual(len(self.run_search(items)['results']), 1)

    def test_empty_query_rejected_before_network(self):
        with self.assertRaisesRegex(ScoutError, 'empty'):
            search(Mock(), '   ')

    def test_stream_probe_propagates_download_method(self):
        client = Mock(stats={})
        item = candidate('archive', 'Test book', 'https://example.org/video')
        with patch('resource_scout.discovery.archive', return_value=[item]), \
             patch('resource_scout.discovery.enrich_archive'), \
             patch('resource_scout.inspect.inspect_url', return_value={
                 'access': 'stream_manifest_observed', 'downloadability': 'requires_media_backend'}):
            result = search(client, 'Test book', kind='video', verify=1)
        self.assertEqual(result['results'][0]['downloadability'], 'requires_media_backend')


class LocalRegressionTests(unittest.TestCase):
    def test_failed_providers_produce_nonzero_cli_status(self):
        with patch('resource_scout.discovery.archive', side_effect=ScoutError('offline')), \
             patch('resource_scout.discovery.openlibrary', side_effect=ScoutError('offline')), \
             patch('resource_scout.discovery.gutendex', side_effect=ScoutError('offline')), \
             redirect_stdout(io.StringIO()) as output:
            code = main(['--cache', ':memory:', 'search', 'Test book', '--verify', '0'])
        self.assertEqual(code, 2)
        self.assertEqual(len(json.loads(output.getvalue())['providers']), 3)

    def test_media_timeout_is_clean_error(self):
        with patch('resource_scout.media.command', return_value=['unused']), \
             patch('resource_scout.media.subprocess.run', side_effect=subprocess.TimeoutExpired('unused', 120)):
            with self.assertRaisesRegex(ScoutError, 'timed out'):
                inspect_video(Mock(), 'https://example.org/video')

    def test_launcher_preserves_callers_output_directory(self):
        launcher = Path(__file__).resolve().parents[1] / 'scout'
        with tempfile.TemporaryDirectory(prefix='scout caller ') as tmp:
            prefix = [sys.executable, '-m', 'resource_scout'] if sys.platform == 'win32' else [str(launcher)]
            result = subprocess.run(prefix + ['--cache', ':memory:', 'inspect', 'file:///bad',
                                     '--json', 'result.json'], cwd=tmp, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertTrue((Path(tmp) / 'result.json').exists(), result.stderr)

    def test_cli_protocol_failure_is_json_without_traceback(self):
        output = io.StringIO()
        with patch('resource_scout.net.Client.open', side_effect=HTTPException('bad status')), \
             patch('resource_scout.download.time.sleep'), redirect_stderr(output), redirect_stdout(io.StringIO()):
            code = main(['--cache', ':memory:', 'download', 'https://example.org/book.pdf', '--out', str(Path(tempfile.gettempdir()) / 'unused-scout.pdf')])
        self.assertEqual(code, 2)
        self.assertIn('error', json.loads(output.getvalue()))

    def test_commented_html_signature(self):
        for prefix in [b'<!-- x -->', b'\xef\xbb\xbf<!-- one -->\n<!-- two -->\n', b'<?xml version="1.0"?>\n']:
            with self.subTest(prefix=prefix):
                self.assertEqual(sniff(prefix + b'<html>Login</html>', 'text/plain'), 'html')

    def test_comment_in_text_does_not_imply_html(self):
        self.assertEqual(sniff(b'<!-- a note -->\nA plain text book.', 'text/plain'), 'text')

    def test_media_rejects_nonpositive_budget_before_job(self):
        client = Mock()
        with tempfile.TemporaryDirectory() as tmp:
            for budget in [0, -1]:
                with self.subTest(budget=budget), self.assertRaises(ScoutError):
                    download_video(client, 'https://example.org/video', tmp, max_bytes=budget)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_media_malformed_metadata_is_clean_error(self):
        with patch('resource_scout.media.command', return_value=['unused']), \
             patch('resource_scout.media.subprocess.run', return_value=Mock(returncode=0, stdout='[]')):
            with self.assertRaises(ScoutError):
                inspect_video(Mock(), 'https://example.org/video')

    def test_media_drm_formats_are_not_offered(self):
        data = {'title': 'test', 'formats': [{'has_drm': True, 'url': 'https://example.org/video', 'protocol': 'https'}]}
        with patch('resource_scout.media.command', return_value=['unused']), \
             patch('resource_scout.media.subprocess.run', return_value=Mock(returncode=0, stdout=json.dumps(data))):
            result = inspect_video(Mock(), 'https://example.org/video')
        self.assertEqual(result['format_count'], 0)
        self.assertEqual(result['downloadability'], 'unknown')
