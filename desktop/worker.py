import threading
from PySide6.QtCore import QObject, Signal, Slot
from resource_scout.net import Client, Cancelled
from resource_scout.discovery import search
from resource_scout.inspect import inspect_url
from resource_scout.download import download_file
from resource_scout.media import inspect_video, download_video


class Worker(QObject):
    progress = Signal(object)
    succeeded = Signal(object)
    failed = Signal(str)
    done = Signal()

    def __init__(self, operation, options, store):
        super().__init__()
        self.operation, self.options, self.store = operation, options, store
        self.cancel_event = threading.Event()

    @Slot()
    def run(self):
        client = None
        try:
            client = Client(str(self.store.root / 'cache.sqlite'), proxy_dns=self.options['proxy_dns'], timeout=10,
                            cancel_event=self.cancel_event)
            if self.operation == 'search':
                result = search(client, self.options['query'], kind=self.options['kind'], verify=3,
                                endpoint=self.options.get('searxng') or None, progress=self.progress.emit)
            elif self.operation == 'inspect':
                if self.options['media']:
                    result = inspect_video(client, self.options['url'])
                else:
                    with client.budget(20):
                        result = inspect_url(client, self.options['url'])
            elif self.operation == 'download':
                if self.options['media']:
                    result = download_video(client, self.options['url'], self.options['output'], self.options['max_mb'] * 1024**2)
                else:
                    result = download_file(client, self.options['url'], self.options['output'], self.options['max_mb'] * 1024**2,
                                           progress=lambda count, total: self.progress.emit({'stage': 'transfer', 'bytes': count, 'total': total}))
            else:
                raise ValueError('Unknown operation')
            if self.cancel_event.is_set():
                raise Cancelled('Cancelled; partial downloads are retained')
            if result.get('error'):
                raise ValueError(result['error'])
            self.succeeded.emit(result)
        except Exception as error:
            self.failed.emit(str(error))
        finally:
            if client is not None:
                client.close()
            self.done.emit()
