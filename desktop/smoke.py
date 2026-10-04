"""Deterministic, offline GUI exercise used on source and packaged apps."""
import hashlib
import json
import subprocess
import time
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from desktop.app import Window, application
from desktop.state import Store
from resource_scout.media import command


def exercise(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    app = application()
    app.setQuitOnLastWindowClosed(False)
    store = Store(output / 'state')
    store.data['download_directory'] = str(output)
    window = Window(store)
    window.show()
    checks = []

    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        checks.append(name)

    def wait_idle():
        deadline = time.monotonic() + 8
        while window.thread is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.01)
        app.processEvents()
        check('worker finishes without blocking the UI', window.thread is None)

    fixture = {'query': 'A public domain book', 'providers': [{'name': 'Fixture catalogue', 'status': 'ok', 'count': 1}],
        'results': [{'title': 'A public domain book', 'authors': ['Example Author'], 'url': 'https://example.org/book',
            'provider': 'Fixture catalogue', 'access': 'file_bytes_observed',
            'files': [{'format': 'application/pdf', 'url': 'https://example.org/book.pdf', 'bytes_reported': 123456}],
            'check': {'url': 'https://example.org/book.pdf', 'access': 'file_bytes_observed', 'mime': 'application/pdf'}}]}

    def search(client, query, **kwargs):
        kwargs['progress']({'stage': 'provider', 'name': 'Fixture catalogue', 'status': 'ok', 'count': 1})
        time.sleep(.04)
        return deepcopy(fixture)

    try:
        check('native app icon loads', not window.windowIcon().isNull())
        window.search()
        check('empty search explains what to enter', 'Enter a title' in window.status.text())
        with patch('desktop.worker.search', side_effect=search):
            window.query.setText('A public domain book')
            QTest.keyClick(window.query, Qt.Key_Return)
            check('search disables duplicate work and enables cancel', not window.search_button.isEnabled() and window.cancel.isEnabled())
            wait_idle()
        check('search populates selectable files', window.results.count() == 1 and window.files.rowCount() == 1 and window.save_button.isEnabled())
        window.apply_theme('Light')
        QTest.qWait(30)
        window.grab().save(str(output / 'desktop-light.png'))
        window.apply_theme('Dark')
        window.resize(900, 700)
        QTest.qWait(30)
        window.grab().save(str(output / 'desktop-dark.png'))

        target = output / 'example.pdf'
        payload = b'%PDF-1.4\nOffline desktop test fixture\n%%EOF\n'
        def download(client, url, path, max_bytes, progress):
            progress(len(payload), len(payload))
            Path(path).write_bytes(payload)
            return {'path': str(path), 'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}
        with patch('desktop.app.QFileDialog.getSaveFileName', return_value=(str(target), '')), patch('desktop.worker.download_file', side_effect=download):
            window.save_selected()
            wait_idle()
        check('download appears in history', target.read_bytes() == payload and window.history.count() == 1)
        check('history survives restart', len(Store(store.root).data['downloads']) == 1)
        with patch('desktop.app.QFileDialog.getSaveFileName', return_value=(str(target), '')):
            window.save_selected()
        check('existing files are preserved', window.thread is None and 'already exists' in window.status.text() and target.read_bytes() == payload)
        exported = output / 'report.html'
        with patch('desktop.app.QFileDialog.getSaveFileName', return_value=(str(exported), '')):
            window.export_report()
        check('HTML export contains result', 'A public domain book' in exported.read_text(encoding='utf-8'))
        window.clear_history()
        check('clearing history keeps downloaded file', window.history.count() == 0 and target.exists())
        window.theme.setCurrentText('Dark')
        window.max_mb.setValue(25)
        window.save_preferences()
        restored = Store(store.root)
        check('preferences persist', restored.data['theme'] == 'Dark' and restored.data['max_mb'] == 25)
        window.endpoint.setText('http://example.org')
        window.save_preferences()
        check('insecure search endpoint is rejected', 'HTTPS' in window.status.text() and not store.data['searxng'])
        window.tabs.setCurrentIndex(0)
        with patch('desktop.worker.search', side_effect=ValueError('Service unavailable; try again')):
            window.search()
            wait_idle()
        check('network errors restore usable controls', 'Service unavailable' in window.status.text() and window.search_button.isEnabled())

        def waiting_search(client, *args, **kwargs):
            client.pause(4)
            return deepcopy(fixture)
        with patch('desktop.worker.search', side_effect=waiting_search):
            window.search()
            QTest.qWait(30)
            window.cancel_work()
            wait_idle()
        check('cancellation restores controls', 'Cancelled' in window.status.text() and window.search_button.isEnabled())
        window.set_results([])
        check('empty results clear stale download actions', not window.save_button.isEnabled() and not window.source_button.isEnabled())
        with patch('desktop.worker.search', side_effect=waiting_search):
            window.search()
            QTest.qWait(20)
            window.close()
            wait_idle()
        check('close cancels work and then exits', not window.isVisible())
    finally:
        if window.worker:
            window.cancel_work()
            wait_idle()
        window.close()
        app.processEvents()
    return checks


def run(output):
    import faulthandler
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    watchdog = (output / 'watchdog.log').open('w')
    faulthandler.dump_traceback_later(90, file=watchdog, exit=True)
    checks = exercise(output)
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if __import__('sys').platform == 'win32' else {}
    result = subprocess.run(command() + ['--version'], capture_output=True, text=True, timeout=60, **options)
    if result.returncode != 0 or not result.stdout.strip():
        raise AssertionError('Bundled media helper did not start: ' + result.stderr)
    checks.append('bundled media helper starts: ' + result.stdout.strip())
    import certifi
    from resource_scout.net import tls_context
    assert Path(certifi.where()).is_file() and tls_context().cert_store_stats()['x509_ca'] > 50
    checks.append('bundled HTTPS certificate roots load')
    (output / 'results.json').write_text(json.dumps({'passed': True, 'checks': checks}, indent=2) + '\n', encoding='utf-8')
    faulthandler.cancel_dump_traceback_later()
    watchdog.close()
    return 0
