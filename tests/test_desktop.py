import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from resource_scout.net import Client, Cancelled
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
try:
    from desktop.app import application, suggested_filename
    from desktop.state import Store
    from desktop.smoke import exercise
    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False


@unittest.skipUnless(QT_AVAILABLE, 'Install the desktop extra to run Qt tests')
class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def test_gui_workflows(self):
        with tempfile.TemporaryDirectory() as directory:
            checks = exercise(directory)
            self.assertGreaterEqual(len(checks), 20)

    def test_portable_filename(self):
        self.assertEqual(suggested_filename({'title': 'CON'}, {'url': 'https://example.org/file', 'format': 'application/pdf'}), 'download-CON.pdf')
        self.assertEqual(suggested_filename({'title': 'A/B: study'}, {'url': 'https://example.org/file', 'format': 'text/plain'}), 'A_B_ study.txt')
        self.assertEqual(suggested_filename({'title': 'Book'}, {'url': 'https://example.org/book.epub'}), 'Book.epub')

    def test_corrupt_preferences_recover(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'preferences.json'
            path.write_text('{broken')
            self.assertEqual(Store(directory).data['max_mb'], 100)
            path.write_text(json.dumps({'theme': 'bad', 'max_mb': -4, 'downloads': [{'path': 4}, {'path': '/file', 'bytes': 'bad'}]}))
            state = Store(directory)
            self.assertEqual(state.data['theme'], 'System')
            self.assertEqual(state.data['max_mb'], 1)
            self.assertEqual(state.data['downloads'], [])

    def test_unicode_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            store.data['download_directory'] = str(Path(directory) / '书籍')
            store.save()
            self.assertEqual(Store(directory).data['download_directory'], store.data['download_directory'])


class CancellationTests(unittest.TestCase):
    def test_cancellation_interrupts_retry_wait(self):
        event = threading.Event()
        client = Client(':memory:', cancel_event=event)
        event.set()
        try:
            with self.assertRaises(Cancelled):
                client.pause(60)
            with self.assertRaises(Cancelled):
                client.remaining()
        finally:
            client.close()
