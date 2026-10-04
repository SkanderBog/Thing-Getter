# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, copy_metadata

root = Path(SPECPATH)
data, binaries, hidden = collect_all('yt_dlp')
data += copy_metadata('yt-dlp')
data += [(str(root / 'desktop/assets'), 'assets'), (str(root / 'build/licenses'), 'licenses'),
         (str(root / 'THIRD_PARTY_NOTICES.md'), '.'), (str(root / 'LICENSE'), '.')]
a = Analysis([str(root / 'desktop/entry.py')], pathex=[str(root)], binaries=binaries, datas=data,
    hiddenimports=hidden + ['desktop.smoke', 'PySide6.QtTest', 'PySide6.QtSvg'],
    excludes=['tkinter', 'matplotlib', 'numpy', 'PIL', 'PySide6.QtQml', 'PySide6.QtQuick',
              'PySide6.QtDesigner', 'PySide6.QtUiTools', 'PySide6.QtPdf', 'PySide6.QtPdfWidgets'],
    noarchive=False)
pyz = PYZ(a.pure)
icon = str(root / 'desktop/assets' / ('icon.icns' if sys.platform == 'darwin' else 'icon.ico'))
app = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Thing-Getter', debug=False,
    bootloader_ignore_signals=False, strip=False, upx=False, console=False, icon=icon,
    argv_emulation=False, codesign_identity=None)
helper = EXE(pyz, a.scripts, [], exclude_binaries=True, name='ThingGetterMedia', debug=False,
    bootloader_ignore_signals=False, strip=False, upx=False, console=True, icon=icon,
    codesign_identity=None)
bundle = COLLECT(app, helper, a.binaries, a.datas, strip=False, upx=False, name='Thing-Getter')
if sys.platform == 'darwin':
    bundle = BUNDLE(bundle, name='Thing-Getter.app', icon=icon, bundle_identifier='org.thinggetter.desktop',
        version='0.3.0', info_plist={'NSHighResolutionCapable': True, 'LSMinimumSystemVersion': '13.0',
        'NSHumanReadableCopyright': 'MIT licensed. Includes Qt/PySide6 under LGPLv3; see bundled notices.'})
