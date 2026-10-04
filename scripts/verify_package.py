"""Verify release hashes and smoke-test the installed/extracted application."""
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
release = root / 'release'
for manifest in release.glob('*-SHA256SUMS.txt'):
    for line in manifest.read_text().splitlines():
        expected, name = line.split('  ', 1)
        with (release / name).open('rb') as source:
            assert hashlib.file_digest(source, 'sha256').hexdigest() == expected
for archive in release.glob('*.zip'):
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.testzip() is None
        assert all(not Path(name).is_absolute() and '..' not in Path(name).parts for name in bundle.namelist())
installed = root / 'build/installed'
output = root / 'build/smoke-installed'
output.mkdir(parents=True, exist_ok=True)
cleanup = None
if sys.platform == 'win32':
    installer = next(release.glob('*-setup.exe'))
    subprocess.run([str(installer), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', f'/DIR={installed}'], check=True, timeout=180)
    executable = installed / 'Thing-Getter.exe'
    cleanup = [str(installed / 'unins000.exe'), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART']
elif sys.platform == 'darwin':
    installed.mkdir(parents=True, exist_ok=True)
    dmg = next(release.glob('*.dmg'))
    subprocess.run(['hdiutil', 'attach', str(dmg), '-readonly', '-nobrowse', '-mountpoint', str(installed)], check=True, timeout=90)
    executable = installed / 'Thing-Getter.app/Contents/MacOS/Thing-Getter'
    cleanup = ['hdiutil', 'detach', str(installed)]
else:
    subprocess.run(['dpkg-deb', '-x', str(next(release.glob('*.deb'))), str(installed)], check=True)
    assert (installed / 'usr/share/applications/thing-getter.desktop').is_file()
    assert (installed / 'usr/share/icons/hicolor/1024x1024/apps/thing-getter.png').is_file()
    executable = installed / 'opt/thing-getter/Thing-Getter'
try:
    environment = dict(os.environ, QT_QPA_PLATFORM='offscreen')
    environment.pop('PYTHONPATH', None)
    result = subprocess.run([str(executable), '--self-test', str(output)], cwd=output, env=environment, timeout=120)
    if result.returncode:
        for file in output.glob('*.log'):
            print(file.read_text(encoding='utf-8', errors='replace'))
        raise SystemExit(result.returncode)
    assert json.loads((output / 'results.json').read_text())['passed']
finally:
    if cleanup:
        subprocess.run(cleanup, check=True, timeout=90)
print('Archive checksums, native package and installed app smoke checks passed.')
