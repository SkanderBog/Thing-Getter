"""Package only the frozen application, never the development working tree."""
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
version = tomllib.loads((root / 'pyproject.toml').read_text())['project']['version']
release = root / 'release'
release.mkdir(exist_ok=True)
arch = 'arm64' if platform.machine().lower() in ('arm64', 'aarch64') else 'x64'
system = {'win32': 'windows', 'darwin': 'macos'}.get(sys.platform, 'linux')
name = f'Thing-Getter-{version}-{system}-{arch}'
dist = root / 'dist'
if sys.platform == 'darwin':
    app = dist / 'Thing-Getter.app'
    subprocess.run(['ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', str(app), str(release / (name + '.zip'))], check=True)
    stage = root / 'build/dmg'
    stage.mkdir(parents=True, exist_ok=True)
    shutil.copytree(app, stage / app.name, symlinks=True, dirs_exist_ok=True)
    (stage / 'Applications').symlink_to('/Applications', target_is_directory=True)
    subprocess.run(['hdiutil', 'create', '-volname', 'Thing-Getter', '-srcfolder', str(stage), '-ov', '-format', 'UDZO', str(release / (name + '.dmg'))], check=True)
elif sys.platform == 'win32':
    with zipfile.ZipFile(release / (name + '.zip'), 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in (dist / 'Thing-Getter').rglob('*'):
            if path.is_file():
                archive.write(path, path.relative_to(dist))
    compiler = shutil.which('ISCC') or r'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
    subprocess.run([compiler, f'/DAppVersion={version}', str(root / 'scripts/windows.iss')], check=True)
else:
    with tarfile.open(release / (name + '.tar.gz'), 'w:gz') as archive:
        archive.add(dist / 'Thing-Getter', arcname='Thing-Getter')
    stage = root / 'build/deb'
    shutil.copytree(dist / 'Thing-Getter', stage / 'opt/thing-getter', symlinks=True, dirs_exist_ok=True)
    (stage / 'DEBIAN').mkdir(parents=True, exist_ok=True)
    (stage / 'DEBIAN/control').write_text(f'Package: thing-getter\nVersion: {version}\nArchitecture: amd64\nMaintainer: Thing-Getter contributors\nSection: net\nPriority: optional\nDepends: libc6 (>= 2.39), libgl1, libegl1, libxkbcommon0, libxkbcommon-x11-0, libxcb-cursor0, libdbus-1-3, libfontconfig1, libx11-xcb1, libxcb-icccm4, libxcb-image0, libxcb-keysyms1, libxcb-render-util0, libxcb-xinerama0\nDescription: Find, inspect and save public resources\n Native desktop catalogue search and bounded downloads.\n')
    apps = stage / 'usr/share/applications'
    apps.mkdir(parents=True, exist_ok=True)
    (apps / 'thing-getter.desktop').write_text('[Desktop Entry]\nType=Application\nName=Thing-Getter\nComment=Find and save books, videos and audio\nExec=/opt/thing-getter/Thing-Getter\nIcon=thing-getter\nTerminal=false\nCategories=Network;Utility;\nStartupWMClass=Thing-Getter\n')
    icons = stage / 'usr/share/icons/hicolor/1024x1024/apps'
    icons.mkdir(parents=True, exist_ok=True)
    shutil.copy2(root / 'desktop/assets/icon.png', icons / 'thing-getter.png')
    subprocess.run(['dpkg-deb', '--root-owner-group', '--build', str(stage), str(release / (name + '.deb'))], check=True)
files = sorted(p for p in release.glob(name + '*') if p.is_file() and p.suffix != '.txt')
with (release / (name + '-SHA256SUMS.txt')).open('w') as stream:
    for path in files:
        with path.open('rb') as source:
            stream.write(f'{hashlib.file_digest(source, "sha256").hexdigest()}  {path.name}\n')
print('Packaged: ' + ', '.join(p.name for p in files))
