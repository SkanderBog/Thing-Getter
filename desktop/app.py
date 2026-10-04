"""Native Qt interface; network and file work runs outside the UI thread."""
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit
from PySide6.QtCore import Qt, QThread, QUrl, QSize, QTimer
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QKeySequence
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout,
    QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QProgressBar, QPushButton, QScrollArea, QSpinBox, QSplitter,
    QTabWidget, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget)
from resource_scout import __version__
from resource_scout.net import normalize
from resource_scout.report import render
from resource_scout.workflow import access_label, progress_text, reported_size
from desktop.state import Store
from desktop.worker import Worker


def asset(name):
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    return base / 'assets' / name


def label(text='', style=None):
    widget = QLabel(str(text))
    widget.setTextFormat(Qt.PlainText)
    widget.setWordWrap(True)
    if style:
        widget.setObjectName(style)
    return widget


def suggested_filename(item, file):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(item.get('title') or 'download')).strip(' .')[:90]
    if not name or re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', name, re.I):
        name = 'download-' + name
    check = item.get('check', {})
    mime = file.get('format', '')
    if file.get('url') == check.get('url'):
        mime = check.get('mime') or mime
    formats = {'application/pdf': '.pdf', 'application/epub+zip': '.epub', 'text/plain': '.txt',
               'video/mp4': '.mp4', 'audio/mpeg': '.mp3', 'video/webm': '.webm', 'text': '.txt', 'pdf': '.pdf'}
    extension = formats.get(mime.split(';')[0])
    if not extension:
        suffix = Path(urlsplit(file.get('url', '')).path).suffix.lower()
        extension = suffix if suffix in {'.pdf', '.epub', '.txt', '.mp4', '.webm', '.mp3', '.ogg', '.m4a'} else '.bin'
    return name if name.lower().endswith(extension) else name + extension


class Window(QMainWindow):
    def __init__(self, store=None):
        super().__init__()
        self.store = store or Store()
        self.items, self.report = [], None
        self.thread = self.worker = None
        self.operation, self.options = None, {}
        self.close_when_idle = False
        self.setWindowTitle('Thing-Getter')
        self.setWindowIcon(QIcon(str(asset('icon.png'))))
        self.resize(1160, 820)
        self.setMinimumSize(860, 650)
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 18, 24, 18)
        brand = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(self.windowIcon().pixmap(48, 48))
        brand.addWidget(icon)
        headings = QVBoxLayout()
        headings.setContentsMargins(0, 0, 0, 0)
        headings.addWidget(label('Thing-Getter', 'brand'))
        headings.addWidget(label('Find and save books, videos and audio.', 'muted'))
        brand.addLayout(headings, 1)
        brand.addStretch()
        brand.addWidget(label('v' + __version__, 'muted'))
        layout.addLayout(brand)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        layout.addWidget(self.tabs, 1)
        self.build_search()
        self.build_downloads()
        self.build_preferences()
        self.status = label('Ready. Enter a title, author, topic or URL.', 'status')
        self.status.setAccessibleName('Activity status')
        activity = QHBoxLayout()
        activity.addWidget(self.status, 1)
        self.cancel = QPushButton('Cancel')
        self.cancel.setEnabled(False)
        self.cancel.clicked.connect(self.cancel_work)
        activity.addWidget(self.cancel)
        layout.addLayout(activity)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(5)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.make_menu()
        self.load_history()
        self.apply_theme()
        QApplication.instance().styleHints().colorSchemeChanged.connect(lambda _: self.apply_theme())

    def build_search(self):
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 18, 0, 0)
        entry = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText('Book title, author, video, or a link…')
        self.query.setAccessibleName('Search query or URL')
        self.query.setClearButtonEnabled(True)
        self.query.returnPressed.connect(self.search)
        self.kind = QComboBox()
        for title, value in [('Books', 'book'), ('Videos', 'video'), ('Audio', 'audio'), ('All resources', 'all')]:
            self.kind.addItem(title, value)
        self.kind.setAccessibleName('Resource type')
        self.search_button = QPushButton('Search')
        self.search_button.setObjectName('primary')
        self.search_button.clicked.connect(self.search)
        entry.addWidget(self.query, 1)
        entry.addWidget(self.kind)
        entry.addWidget(self.search_button)
        box.addLayout(entry)
        self.url_media = QCheckBox('This URL is a video or audio page')
        self.url_media.setVisible(False)
        self.query.textChanged.connect(lambda text: self.url_media.setVisible(text.strip().startswith(('http://', 'https://'))))
        box.addWidget(self.url_media)
        hint = QHBoxLayout()
        hint.addWidget(label('Search public catalogues. Choose Save to download a file.', 'muted'), 1)
        self.export = QPushButton('Export report…')
        self.export.setEnabled(False)
        self.export.clicked.connect(self.export_report)
        hint.addWidget(self.export)
        box.addLayout(hint)
        splitter = QSplitter()
        self.results = QListWidget()
        self.results.setAccessibleName('Search results')
        self.results.setMinimumWidth(240)
        self.results.setWordWrap(True)
        self.results.currentRowChanged.connect(self.show_item)
        splitter.addWidget(self.results)
        self.detail_scroll = QScrollArea()
        self.detail_scroll.setWidgetResizable(True)
        self.detail = QWidget()
        self.detail_box = QVBoxLayout(self.detail)
        self.detail_box.setContentsMargins(24, 20, 24, 20)
        self.heading = label('Start with something you want to find', 'title')
        self.byline = label('Search across Internet Archive, Open Library and Gutenberg catalogues.', 'muted')
        self.access = label('Results distinguish catalogue listings, file access and restricted sources.', 'access')
        self.detail_box.addWidget(self.heading)
        self.detail_box.addWidget(self.byline)
        self.detail_box.addWidget(self.access)
        self.source_button = QPushButton('Open source')
        self.source_button.setEnabled(False)
        self.source_button.clicked.connect(self.open_source)
        self.detail_box.addWidget(self.source_button, 0, Qt.AlignLeft)
        self.files = QTableWidget(0, 3)
        self.files.setHorizontalHeaderLabels(['Format', 'Reported size', 'Access'])
        self.files.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.files.verticalHeader().hide()
        self.files.setSelectionBehavior(QTableWidget.SelectRows)
        self.files.setSelectionMode(QTableWidget.SingleSelection)
        self.files.setEditTriggers(QTableWidget.NoEditTriggers)
        self.files.setAccessibleName('Available files')
        self.files.setMinimumHeight(150)
        self.files.itemSelectionChanged.connect(self.selection_changed)
        self.detail_box.addWidget(self.files)
        buttons = QHBoxLayout()
        self.inspect_button = QPushButton('Check selected file')
        self.inspect_button.clicked.connect(self.inspect_selected)
        self.save_button = QPushButton('Save selected file…')
        self.save_button.setObjectName('primary')
        self.save_button.clicked.connect(self.save_selected)
        self.media_button = QPushButton('Save media…')
        self.media_button.clicked.connect(lambda: self.save_selected(media=True))
        for button in (self.inspect_button, self.save_button, self.media_button):
            button.setEnabled(False)
            buttons.addWidget(button)
        self.metadata = label('Nothing is downloaded during a search.', 'muted')
        self.detail_box.addWidget(self.metadata)
        self.evidence_toggle = QCheckBox('Show evidence and technical details')
        self.detail_box.addWidget(self.evidence_toggle)
        self.evidence = QTextEdit()
        self.evidence.setReadOnly(True)
        self.evidence.setMinimumHeight(180)
        self.evidence.hide()
        self.evidence_toggle.toggled.connect(self.evidence.setVisible)
        self.detail_box.addWidget(self.evidence)
        self.detail_box.addStretch()
        self.detail_scroll.setWidget(self.detail)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self.detail_scroll, 1)
        right_layout.addLayout(buttons)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        box.addWidget(splitter, 1)
        self.tabs.addTab(page, 'Find resources')

    def build_downloads(self):
        page = QWidget()
        box = QVBoxLayout(page)
        box.addWidget(label('Your downloads', 'title'))
        box.addWidget(label('Completed files stay where you saved them. This history is stored only on your computer.', 'muted'))
        self.history = QListWidget()
        self.history.setAccessibleName('Completed downloads')
        box.addWidget(self.history, 1)
        actions = QHBoxLayout()
        for text, callback in [('Open file', self.open_download), ('Show folder', lambda: self.open_download(folder=True)), ('Clear history', self.clear_history)]:
            button = QPushButton(text)
            button.clicked.connect(callback)
            actions.addWidget(button)
        actions.addStretch()
        box.addLayout(actions)
        self.tabs.addTab(page, 'Downloads')

    def build_preferences(self):
        page = QWidget()
        box = QVBoxLayout(page)
        box.addWidget(label('Preferences', 'title'))
        form = QFormLayout()
        self.theme = QComboBox()
        self.theme.addItems(['System', 'Light', 'Dark'])
        self.theme.setCurrentText(self.store.data['theme'])
        self.theme.setAccessibleName('Appearance')
        self.theme.currentTextChanged.connect(lambda _: self.apply_theme(self.theme.currentText()))
        form.addRow('Appearance', self.theme)
        self.max_mb = QSpinBox()
        self.max_mb.setRange(1, 2048)
        self.max_mb.setSuffix(' MiB')
        self.max_mb.setValue(self.store.data['max_mb'])
        form.addRow('Maximum download size', self.max_mb)
        self.proxy_dns = QCheckBox('My network uses a trusted fake-IP proxy')
        self.proxy_dns.setChecked(self.store.data['proxy_dns'])
        form.addRow('Network', self.proxy_dns)
        self.endpoint = QLineEdit(self.store.data['searxng'])
        self.endpoint.setPlaceholderText('Optional: https://your-search-service.example')
        form.addRow('SearXNG service', self.endpoint)
        box.addLayout(form)
        box.addWidget(label('Leave the proxy setting off on ordinary networks. SearXNG adds broader web search when you have a compatible public service. No account or API key is needed for the built-in catalogues.', 'muted'))
        save = QPushButton('Save preferences')
        save.setObjectName('primary')
        save.clicked.connect(self.save_preferences)
        box.addWidget(save, 0, Qt.AlignLeft)
        box.addSpacing(20)
        box.addWidget(label('Privacy', 'title'))
        box.addWidget(label('Search terms and selected URLs go to the sources you request. Preferences, catalogue cache and download history stay on this computer. There is no telemetry. Exported reports can contain query strings; review them before sharing.', 'muted'))
        cache_button = QPushButton('Clear catalogue cache')
        cache_button.clicked.connect(self.clear_cache)
        box.addWidget(cache_button, 0, Qt.AlignLeft)
        box.addStretch()
        self.tabs.addTab(page, 'Preferences')

    def make_menu(self):
        menu = self.menuBar().addMenu('File')
        focus = QAction('Focus search', self)
        focus.setShortcut(QKeySequence('Ctrl+L'))
        focus.triggered.connect(lambda: (self.tabs.setCurrentIndex(0), self.query.setFocus()))
        menu.addAction(focus)
        quit_action = QAction('Quit', self)
        quit_action.setShortcut(QKeySequence.Quit)
        quit_action.triggered.connect(self.close)
        menu.addAction(quit_action)
        help_menu = self.menuBar().addMenu('Help')
        about = QAction('About Thing-Getter', self)
        about.triggered.connect(lambda: QMessageBox.about(self, 'About Thing-Getter',
            'Thing-Getter ' + __version__ + '\nFind, inspect and save public resources.\nMIT-licensed application. Qt/PySide6 and other notices are included with the app.\nhttps://github.com/SkanderBog/Thing-Getter'))
        help_menu.addAction(about)

    def apply_theme(self, choice=None):
        choice = choice or self.store.data['theme']
        dark = choice == 'Dark' or choice == 'System' and QApplication.instance().styleHints().colorScheme() == Qt.ColorScheme.Dark
        bg, card, ink, muted, edge, field = ('#142024', '#1c2b30', '#eff7f5', '#b5cac9', '#365057', '#22373c') if dark else ('#f1f5f4', '#ffffff', '#153638', '#526d70', '#cedddb', '#ffffff')
        self.setStyleSheet(f'''
        QMainWindow, QWidget {{background:{bg};color:{ink};font-size:13px;}}
        QLabel#brand {{font-size:24px;font-weight:700;}} QLabel#title {{font-size:23px;font-weight:650;}}
        QLabel#muted {{color:{muted};}} QLabel#status {{color:{muted};padding:5px 0;}}
        QLabel#access {{background:{field};border:1px solid {edge};border-radius:7px;padding:12px;}}
        QLineEdit,QComboBox,QSpinBox {{background:{field};border:1px solid {edge};border-radius:7px;padding:10px;min-height:20px;}}
        QPushButton {{background:{card};border:1px solid {edge};border-radius:7px;padding:10px 14px;min-height:20px;}}
        QPushButton:hover {{border-color:#1fa48f;}} QPushButton#primary {{background:#087c6e;color:#ffffff;border-color:#087c6e;font-weight:600;}}
        QPushButton:disabled {{color:{muted};background:{bg};border-color:{edge};}}
        QListWidget,QTableWidget,QTextEdit {{background:{card};border:1px solid {edge};border-radius:8px;selection-background-color:#087c6e;selection-color:white;}}
        QListWidget::item {{padding:12px;border-bottom:1px solid {edge};}}
        QListWidget::item:selected {{background:#087c6e;color:white;}}
        QHeaderView::section {{background:{field};color:{muted};padding:9px;border:none;}}
        QScrollArea {{border:1px solid {edge};border-radius:8px;}}
        QTabWidget::pane {{border:0;}} QTabBar::tab {{padding:12px 20px;margin-right:4px;border-bottom:3px solid transparent;}}
        QTabBar::tab:selected {{border-bottom-color:#0b9482;font-weight:600;}}
        QProgressBar {{border:0;background:{edge};}} QProgressBar::chunk {{background:#0b9482;}}
        QCheckBox {{padding:7px 0;}} QSplitter::handle {{background:{bg};width:12px;}}
        ''')

    def save_preferences(self):
        endpoint = self.endpoint.text().strip()
        try:
            if endpoint:
                endpoint = normalize(endpoint)
                if not endpoint.startswith('https://'):
                    raise ValueError('Use an HTTPS SearXNG endpoint')
            self.store.data.update(theme=self.theme.currentText(), max_mb=self.max_mb.value(),
                                   proxy_dns=self.proxy_dns.isChecked(), searxng=endpoint)
            self.store.save()
            self.status.setText('Preferences saved.')
        except Exception as error:
            self.status.setText(str(error))

    def clear_cache(self):
        if self.thread is not None:
            self.status.setText('Wait for the current operation or cancel it before clearing the cache.')
            return
        try:
            (self.store.root / 'cache.sqlite').unlink(missing_ok=True)
            self.status.setText('Catalogue cache cleared.')
        except OSError as error:
            self.status.setText(f'Could not clear the cache: {error}')

    def search(self):
        value = self.query.text().strip()
        if not value or self.thread is not None:
            if not value:
                self.status.setText('Enter a title, author, topic or URL first.')
            return
        if value.startswith(('http://', 'https://')):
            self.start('inspect', {'url': value, 'media': self.url_media.isChecked()})
        else:
            self.start('search', {'query': value, 'kind': self.kind.currentData()})

    def start(self, operation, options):
        if self.thread is not None:
            return
        self.operation = operation
        self.options = {**options, 'proxy_dns': self.store.data['proxy_dns'], 'searxng': self.store.data['searxng'], 'max_mb': self.store.data['max_mb']}
        self.worker = Worker(operation, dict(self.options), self.store)
        self.thread = QThread(self)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.on_progress)
        self.worker.succeeded.connect(self.on_success)
        self.worker.failed.connect(self.on_error)
        self.worker.done.connect(self.thread.quit)
        self.worker.done.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.on_finished)
        self.thread.finished.connect(self.thread.deleteLater)
        self.search_button.setEnabled(False)
        self.query.setEnabled(False)
        self.export.setEnabled(False)
        self.cancel.setEnabled(True)
        self.progress.setRange(0, 0)
        self.progress.show()
        self.status.setText({'search': 'Searching catalogues…', 'inspect': 'Checking this link…', 'download': 'Starting download…'}[operation])
        self.selection_changed()
        self.thread.start()

    def cancel_work(self):
        if self.worker:
            self.worker.cancel_event.set()
            self.cancel.setEnabled(False)
            self.status.setText('Cancelling… waiting for the current network read to finish.')

    def on_progress(self, event):
        if event.get('stage') == 'transfer':
            count, total = event['bytes'], event['total']
            if total:
                self.progress.setRange(0, 1000)
                self.progress.setValue(int(count / total * 1000))
                self.status.setText(f'Saving {count / 1024**2:.1f} of {total / 1024**2:.1f} MiB…')
            else:
                self.status.setText(f'Saving {count / 1024**2:.1f} MiB…')
        else:
            self.status.setText(progress_text(event))

    def on_success(self, result):
        if self.operation == 'download':
            try:
                self.store.remember(result)
                self.load_history()
                self.status.setText('Download complete. Find it in Downloads.')
            except OSError:
                self.load_history()
                self.status.setText('Download complete, but history could not be saved. Your file is in the folder you chose.')
            self.tabs.setCurrentIndex(1)
        elif self.operation == 'search':
            self.report = result
            self.set_results(result['results'])
            errors = [p['name'] for p in result['providers'] if p['status'] == 'error']
            message = f"{len(self.items)} candidates. Select a result to review its files."
            if not self.items:
                message = 'No matches in the sources checked. Try a shorter title or another source.'
            if errors:
                message += ' Unavailable: ' + ', '.join(errors) + '.'
            self.status.setText(message)
        else:
            index = self.options.get('parent_index')
            if index is not None and 0 <= index < len(self.items):
                self.items[index]['check'] = result
                self.items[index]['access'] = result['access']
                self.show_item(index)
            else:
                item = {'title': result.get('title') or result['url'], 'url': result['url'], 'authors': [], 'provider': 'Direct link',
                        'access': result['access'], 'downloadability': result.get('downloadability', 'unknown'),
                        'files': result.get('files', []), 'check': result}
                self.report = {'results': [item], 'query': item['title']}
                self.set_results([item])
            self.status.setText(access_label(result['access']))

    def on_error(self, message):
        if 'fake-IP' in message or 'Non-public DNS' in message:
            message += ' If you use a trusted fake-IP proxy, enable it in Preferences.'
        self.status.setText(message)

    def on_finished(self):
        self.thread = self.worker = None
        self.search_button.setEnabled(True)
        self.query.setEnabled(True)
        self.cancel.setEnabled(False)
        self.progress.hide()
        self.export.setEnabled(self.report is not None)
        self.selection_changed()
        if self.close_when_idle:
            QTimer.singleShot(0, self.close)

    def set_results(self, items):
        self.items = items
        self.results.clear()
        for item in items:
            authors = ', '.join(item.get('authors', []))
            text = str(item.get('title') or item['url']) + '\n' + (authors + ' · ' if authors else '') + item.get('provider', '')
            row = QListWidgetItem(text)
            row.setSizeHint(QSize(0, 90))
            self.results.addItem(row)
        if items:
            self.results.setCurrentRow(0)
        else:
            self.heading.setText('No matches in the sources checked')
            self.byline.setText('Try a shorter title, add the author, or paste a direct URL.')
            self.access.setText('An empty result does not establish that a resource is unavailable everywhere.')
            self.files.setRowCount(0)
            self.source_button.setEnabled(False)
            self.evidence.clear()
        self.export.setEnabled(self.report is not None and self.thread is None)

    def current_item(self):
        index = self.results.currentRow()
        return self.items[index] if 0 <= index < len(self.items) else None

    def show_item(self, index):
        if not 0 <= index < len(self.items):
            return
        item = self.items[index]
        self.heading.setText(str(item.get('title') or item['url']))
        self.byline.setText(', '.join(item.get('authors', [])) or item.get('provider', ''))
        self.access.setText(access_label(item.get('access', 'unknown')))
        self.source_button.setEnabled(True)
        files = list(item.get('files') or [])
        check = item.get('check') or {}
        if not files and check.get('access') == 'file_bytes_observed':
            files = [{'url': check['url'], 'format': check.get('mime') or check.get('format', 'File')}]
        self.files.setRowCount(len(files))
        for row, file in enumerate(files):
            format_name = str(file.get('format', 'File'))
            friendly = {'application/pdf': 'PDF', 'application/epub+zip': 'EPUB', 'text/plain': 'Plain text', 'text/plain; charset=utf-8': 'Plain text', 'text/html': 'Web page', 'audio/mpeg': 'MP3', 'video/mp4': 'MP4'}
            values = [friendly.get(format_name, format_name), reported_size(file, check) or 'Unknown',
                      'Checked' if file['url'] == check.get('url') and check.get('access') == 'file_bytes_observed' else 'Not checked']
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setData(Qt.UserRole, file)
                self.files.setItem(row, col, cell)
        if files:
            self.files.selectRow(0)
        self.metadata.setText('Identity and completeness remain unverified. ' + ('No direct files listed; try a media check for a video page.' if not files else 'Select a format, review its size, then choose Save.'))
        self.evidence.setPlainText(json.dumps(item, indent=2, ensure_ascii=False))
        self.selection_changed()

    def selected_file(self):
        row = self.files.currentRow()
        cell = self.files.item(row, 0) if row >= 0 else None
        return cell.data(Qt.UserRole) if cell is not None else None

    def selection_changed(self):
        idle = self.thread is None
        selected = self.selected_file() is not None
        self.inspect_button.setEnabled(idle and selected)
        self.save_button.setEnabled(idle and selected)
        item = self.current_item()
        media = item is not None and item.get('provider') not in ('openlibrary', 'gutendex') and item.get('media_type') != 'texts'
        self.media_button.setEnabled(idle and media)

    def open_source(self):
        item = self.current_item()
        if item:
            try:
                QDesktopServices.openUrl(QUrl(normalize(item['url'])))
            except Exception as error:
                self.status.setText(str(error))

    def inspect_selected(self):
        file = self.selected_file()
        if file:
            self.start('inspect', {'url': file['url'], 'media': False, 'parent_index': self.results.currentRow()})

    def save_selected(self, media=False):
        item, file = self.current_item(), self.selected_file()
        if not item or self.thread is not None:
            return
        directory = self.store.data['download_directory']
        if media:
            output = QFileDialog.getExistingDirectory(self, 'Choose a folder for this media download', directory)
            url = item['url']
        elif file:
            proposed = str(Path(directory) / suggested_filename(item, file))
            output, _ = QFileDialog.getSaveFileName(self, 'Save resource', proposed, options=QFileDialog.DontConfirmOverwrite)
            url = file['url']
            if output and Path(output).exists():
                self.status.setText('That file already exists. Choose another filename; existing files are preserved.')
                return
        else:
            return
        if not output:
            return
        self.store.data['download_directory'] = output if media else str(Path(output).parent)
        try:
            self.store.save()
        except OSError as error:
            self.status.setText(f'Could not save preferences: {error}')
            return
        self.start('download', {'url': url, 'media': media, 'output': output})

    def load_history(self):
        self.history.clear()
        for item in self.store.data['downloads']:
            row = QListWidgetItem(Path(item['path']).name + f"\n{item.get('bytes', 0):,} bytes · SHA-256 recorded")
            row.setData(Qt.UserRole, item)
            self.history.addItem(row)
        if self.history.count():
            self.history.setCurrentRow(0)

    def open_download(self, folder=False):
        item = self.history.currentItem()
        if item:
            path = Path(item.data(Qt.UserRole)['path'])
            path = path.parent if folder else path
            if path.exists():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
            else:
                self.status.setText('This file has moved or was removed.')

    def clear_history(self):
        self.store.data['downloads'] = []
        try:
            self.store.save()
        except OSError as error:
            self.status.setText(f'Could not save history: {error}')
            return
        self.load_history()
        self.status.setText('History cleared. Downloaded files were kept.')

    def export_report(self):
        if not self.report:
            return
        output, _ = QFileDialog.getSaveFileName(self, 'Export search report', 'Thing-Getter-report.html', 'HTML report (*.html)')
        if output:
            try:
                render(self.report, output)
                self.status.setText('Report exported. Review its URLs before sharing.')
            except OSError as error:
                self.status.setText(f'Could not save the report: {error}')

    def closeEvent(self, event):
        if self.thread is not None:
            self.close_when_idle = True
            self.cancel_work()
            self.status.setText('Cancelling current work before closing…')
            event.ignore()
        else:
            event.accept()


def application():
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName('Thing-Getter')
    app.setOrganizationName('ThingGetter')
    app.setApplicationVersion(__version__)
    app.setStyle('Fusion')
    return app


def run():
    app = application()
    window = Window()
    window.show()
    return app.exec()
