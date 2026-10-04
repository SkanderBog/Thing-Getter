"""Small local preferences/history store; no credentials or telemetry."""
import json
import os
from pathlib import Path
from PySide6.QtCore import QStandardPaths
from resource_scout.download import atomic_json


def data_directory():
    override = os.environ.get('THING_GETTER_DATA_DIR')
    if override:
        path = Path(override)
        if not path.is_absolute():
            raise ValueError('THING_GETTER_DATA_DIR must be an absolute path')
    else:
        path = Path(QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation))
    path.mkdir(parents=True, exist_ok=True)
    return path


class Store:
    def __init__(self, root=None):
        self.root = Path(root) if root is not None else data_directory()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'preferences.json'
        self.data = {'theme': 'System', 'proxy_dns': False, 'searxng': '', 'max_mb': 100,
                     'download_directory': QStandardPaths.writableLocation(QStandardPaths.DownloadLocation), 'downloads': []}
        try:
            saved = json.loads(self.path.read_text(encoding='utf-8'))
            if isinstance(saved, dict):
                for key, default in self.data.items():
                    if key in saved and type(saved[key]) is type(default):
                        self.data[key] = saved[key]
                self.data['downloads'] = [x for x in self.data['downloads'] if isinstance(x, dict) and isinstance(x.get('path'), str) and type(x.get('bytes')) is int and isinstance(x.get('sha256'), str)][:100]
                self.data['max_mb'] = max(1, min(2048, self.data['max_mb']))
                if self.data['theme'] not in ('System', 'Light', 'Dark'):
                    self.data['theme'] = 'System'
        except (OSError, ValueError):
            pass

    def save(self):
        atomic_json(self.path, self.data)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def remember(self, result):
        for item in result.get('files', [result]):
            self.data['downloads'].insert(0, {k: item[k] for k in ('path', 'bytes', 'sha256') if k in item})
        self.data['downloads'] = self.data['downloads'][:100]
        self.save()
