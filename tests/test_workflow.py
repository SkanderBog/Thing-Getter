import io
import json
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout, redirect_stderr
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

from resource_scout.cli import main
from resource_scout.discovery import candidate, search
from resource_scout.inspect import inspect_url
from resource_scout.net import Client, ScoutError
from resource_scout.report import render
from resource_scout.workflow import select_download, summary, reported_size
from test_scout import FixtureClient, Handler, PAYLOAD


class SlowHandler(Handler):
    def do_GET(self):
        if self.path in ('/slow-json', '/slow-probe'):
            payload = b'{"ok":true,"text":"' + b'x' * 100 + b'"}'
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.send_header('Content-Length', len(payload))
            self.end_headers()
            try:
                for byte in payload:
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                    time.sleep(.025)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        if self.path == '/retry':
            return self.send(503, b'Unavailable', 'text/plain', {'Retry-After': '1'})
        return super().do_GET()


class WorkflowIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), SlowHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.client = FixtureClient(':memory:', delay=0, timeout=2)
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def test_trickling_json_cannot_extend_budget(self):
        started = time.monotonic()
        with self.assertRaises(ScoutError), self.client.budget(.2):
            self.client.json(self.base + '/slow-json')
        self.assertLess(time.monotonic() - started, .8)

    def test_trickling_probe_cannot_extend_budget(self):
        started = time.monotonic()
        with self.client.budget(.2):
            result = inspect_url(self.client, self.base + '/slow-probe')
        self.assertIn('error', result)
        self.assertEqual(result['access'], 'unknown')
        self.assertLess(time.monotonic() - started, .8)

    def test_retry_wait_obeys_budget(self):
        started = time.monotonic()
        with self.assertRaisesRegex(ScoutError, 'budget'), self.client.budget(.2):
            self.client.json(self.base + '/retry')
        self.assertLess(time.monotonic() - started, .8)
        self.assertEqual(self.client.stats['requests'], 1)

    def test_fresh_success_clears_provider_cooldown(self):
        with self.assertRaises(ScoutError):
            self.client.json(self.base + '/denied')
        self.client.json(self.base + '/json', ttl=0)
        result, _ = self.client.json(self.base + '/json?another-query')
        self.assertTrue(result['ok'])

    def test_download_saved_result_records_selection(self):
        report = self.root / 'results.json'
        report.write_text(json.dumps({'results': [candidate('fixture', 'A book', self.base + '/page',
                                                          files=[{'url': self.base + '/file.pdf'}])]}))
        target = self.root / 'chosen.pdf'
        # CLI owns/closes its client; preserve the setUp client for tearDown.
        with patch('resource_scout.cli.Client', side_effect=lambda *a, **kw: FixtureClient(':memory:', delay=0)), \
             redirect_stdout(io.StringIO()) as output:
            code = main(['download', '--from-report', str(report), '--result', '1', '--out', str(target)])
        self.assertEqual(code, 0)
        self.assertEqual(target.read_bytes(), PAYLOAD)
        result = json.loads(output.getvalue())
        receipt = json.loads(target.with_suffix('.pdf.provenance.json').read_text())
        self.assertEqual(receipt['selected_from'], result['selected_from'])
        self.assertEqual(receipt['selected_from']['result_number'], 1)


class DiscoveryWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.client = Client(':memory:', delay=0)

    def tearDown(self):
        self.client.close()

    def test_catalogue_does_not_consume_file_check(self):
        catalogue = candidate('openlibrary', 'Test book', 'https://a.example.org/book')
        file = candidate('gutendex', 'Test book', 'https://z.example.org/book')
        with patch('resource_scout.discovery.openlibrary', return_value=[catalogue]), \
             patch('resource_scout.discovery.gutendex', return_value=[file]), \
             patch('resource_scout.discovery.archive', return_value=[]), \
             patch('resource_scout.inspect.inspect_url', return_value={'access': 'file_bytes_observed'}) as inspect:
            result = search(self.client, 'Test book', verify=1)
        inspect.assert_called_once_with(self.client, file['url'])
        self.assertEqual(result['checks_performed'], 1)
        self.assertEqual(result['results'][0]['result_number'], 1)

    def test_restricted_candidate_preserves_file_budget(self):
        restricted = candidate('archive', 'Test book', 'https://a.example.org/book')
        file = candidate('gutendex', 'Test book', 'https://z.example.org/book')
        def enrich(client, item, ttl):
            item['access'] = 'provider_reports_restriction'
        with patch('resource_scout.discovery.archive', return_value=[restricted]), \
             patch('resource_scout.discovery.openlibrary', return_value=[]), \
             patch('resource_scout.discovery.gutendex', return_value=[file]), \
             patch('resource_scout.discovery.enrich_archive', side_effect=enrich), \
             patch('resource_scout.inspect.inspect_url', return_value={'access': 'unknown'}) as inspect:
            result = search(self.client, 'Test book', verify=1)
        inspect.assert_called_once()
        self.assertEqual(result['checks_performed'], 1)

    def test_progress_arrives_before_slow_source_finishes(self):
        released = threading.Event()
        def slow(*args):
            if not released.wait(1):
                raise AssertionError('Fast result was not delivered before waiting for all sources')
            return []
        events = []
        def progress(event):
            events.append(event)
            if event['stage'] == 'provider' and event['name'] == 'archive':
                released.set()
        with patch('resource_scout.discovery.archive', return_value=[]), \
             patch('resource_scout.discovery.openlibrary', side_effect=slow):
            result = search(self.client, 'Test', verify=0, providers=['archive', 'openlibrary'], progress=progress)
        self.assertTrue(all(p['status'] == 'ok' for p in result['providers']))
        self.assertEqual(events[0]['stage'], 'start')
        self.assertEqual(events[1]['name'], 'archive')

    def test_unselected_providers_are_not_called(self):
        with patch('resource_scout.discovery.archive', return_value=[]), patch('resource_scout.discovery.gutendex') as other:
            search(self.client, 'Test', verify=0, providers=['archive'])
        other.assert_not_called()

    def test_unknown_provider_rejected_without_search(self):
        with patch('resource_scout.discovery.archive') as provider:
            with self.assertRaises(ScoutError):
                search(self.client, 'Test', providers=['typo'])
        provider.assert_not_called()

    def test_budget_is_thread_local_and_restored(self):
        errors = []
        def expired():
            with self.client.budget(.001):
                time.sleep(.01)
                try:
                    self.client.remaining()
                except ScoutError:
                    errors.append('expired')
            self.assertGreater(self.client.remaining(), 0)
        thread = threading.Thread(target=expired)
        thread.start()
        thread.join()
        self.assertEqual(errors, ['expired'])
        self.assertGreater(self.client.remaining(), 0)


class SelectionAndCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root / 'search.json'

    def tearDown(self):
        self.tmp.cleanup()

    def save(self, item):
        self.path.write_text(json.dumps({'results': [item]}))

    def test_checked_file_preferred_to_ambiguous_list(self):
        self.save({'files': [{'url': 'https://example.org/a.pdf'}, {'url': 'https://example.org/b.pdf'}],
                   'check': {'access': 'file_bytes_observed', 'url': 'https://example.org/b.pdf'}})
        self.assertEqual(select_download(self.path, 1)[0], 'https://example.org/b.pdf')

    def test_range_total_is_shown_instead_of_prefix_size(self):
        file = {'url': 'https://example.org/book'}
        check = {'url': file['url'], 'http_status': 206, 'content_range': 'bytes 0-131071/24836548', 'content_length_reported': '131072'}
        self.assertEqual(reported_size(file, check), '23.7 MiB reported')

    def test_unknown_range_total_does_not_claim_prefix_as_total(self):
        file = {'url': 'https://example.org/book'}
        check = {'url': file['url'], 'http_status': 206, 'content_range': 'bytes 0-131071/*', 'content_length_reported': '131072'}
        self.assertEqual(reported_size(file, check), '')

    def test_multiple_unchecked_files_require_selection(self):
        self.save({'files': [{'url': 'https://example.org/a.pdf'}, {'url': 'https://example.org/b.pdf'}]})
        with self.assertRaisesRegex(ScoutError, '--file'):
            select_download(self.path, 1)
        self.assertEqual(select_download(self.path, 1, 2)[0], 'https://example.org/b.pdf')

    def test_unsafe_saved_url_rejected(self):
        self.save({'files': [{'url': 'file:///etc/passwd'}]})
        with self.assertRaises(ScoutError):
            select_download(self.path, 1)

    def test_catalogue_is_not_automatically_downloaded(self):
        self.save({'url': 'https://example.org/page', 'files': []})
        with self.assertRaisesRegex(ScoutError, 'No direct file'):
            select_download(self.path, 1)
        self.assertEqual(select_download(self.path, 1, video=True)[0], 'https://example.org/page')

    def test_out_of_range_result_or_file_rejected(self):
        self.save({'files': [{'url': 'https://example.org/a.pdf'}]})
        for result, file in [(0, None), (2, None), (1, 0), (1, 2)]:
            with self.subTest(result=result, file=file), self.assertRaises(ScoutError):
                select_download(self.path, result, file)

    def test_corrupt_saved_report_rejected(self):
        self.path.write_text('[]')
        with self.assertRaises(ScoutError):
            select_download(self.path, 1)

    def test_common_options_work_after_action(self):
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['doctor', '--format', 'json', '--proxy-dns']), 0)
        self.assertIn('python', json.loads(output.getvalue()))

    def test_text_summary_is_readable_and_control_safe(self):
        text = summary({'query': 'Test', 'results': [candidate('test', '\x1b[2JBook', 'https://example.org/a')], 'checks_performed': 0})
        self.assertIn('1.', text)
        self.assertIn('Catalogue listing', text)
        self.assertNotIn('\x1b', text)

    def test_search_json_stdout_is_not_mixed_with_progress(self):
        with patch('resource_scout.discovery.archive', return_value=[]), redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()) as progress:
            code = main(['--cache', ':memory:', 'search', 'Book', '--providers', 'archive', '--verify', '0', '--format', 'json'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())['results'], [])
        self.assertIn('archive', progress.getvalue())

    def test_quiet_suppresses_progress(self):
        with patch('resource_scout.discovery.archive', return_value=[]), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as output:
            main(['--cache', ':memory:', 'search', 'Book', '--providers', 'archive', '--verify', '0', '--quiet'])
        self.assertEqual(output.getvalue(), '')

    def test_open_report_uses_saved_file_uri(self):
        output = self.root / 'report with spaces.html'
        with patch('resource_scout.discovery.archive', return_value=[]), patch('resource_scout.cli.webbrowser.open', return_value=True) as browser, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            main(['--cache', ':memory:', 'search', 'Book', '--providers', 'archive', '--verify', '0', '--html', str(output), '--open'])
        browser.assert_called_once_with(output.as_uri())
        self.assertTrue(output.exists())

    def test_report_contains_quoted_download_command(self):
        item = candidate('test', 'Book', 'https://example.org/book', files=[{'url': 'https://example.org/book.pdf'}])
        output = self.root / 'report.html'
        render({'results': [item]}, output, source_report=self.root / 'search with spaces.json', proxy_dns=True)
        text = output.read_text()
        self.assertIn('--from-report', text)
        self.assertIn('--proxy-dns', text)
        self.assertIn('--result 1', text)
        self.assertNotIn('<script', text)

    def test_failed_sources_empty_state_explains_failure(self):
        output = self.root / 'report.html'
        render({'results': [], 'providers': [{'name': 'test', 'status': 'error', 'error': 'offline'}]}, output)
        self.assertIn('Sources could not be reached', output.read_text())
