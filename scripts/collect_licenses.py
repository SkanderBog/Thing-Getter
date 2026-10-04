"""Collect dependency license texts and matching Qt source attribution files."""
import importlib.metadata
import json
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path

root = Path(__file__).resolve().parents[1]
output = root / 'build/licenses'
output.mkdir(parents=True, exist_ok=True)
shutil.copy2(root / 'LICENSE', output / 'Thing-Getter-MIT.txt')
shutil.copy2(root / 'THIRD_PARTY_NOTICES.md', output / 'README.md')
for name in ('yt-dlp', 'PyInstaller', 'packaging', 'setuptools', 'altgraph', 'certifi', 'websockets', 'requests', 'urllib3', 'charset-normalizer', 'idna', 'brotli'):
    try:
        package = importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError:
        continue
    for file in package.files or []:
        if any(part.lower().startswith(('license', 'copying', 'notice', 'copyright', 'authors')) for part in file.parts):
            source = Path(package.locate_file(file))
            if source.is_file() and source.suffix != '.py':
                destination = output / name / str(file)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
python_license = Path(sys.base_prefix) / 'LICENSE.txt'
if not python_license.is_file():
    python_license = Path(sys.base_prefix) / 'lib' / f'python{sys.version_info.major}.{sys.version_info.minor}' / 'LICENSE.txt'
if python_license.is_file():
    shutil.copy2(python_license, output / 'Python-LICENSE.txt')
else:
    urllib.request.urlretrieve('https://raw.githubusercontent.com/python/cpython/3.12/LICENSE', output / 'Python-LICENSE.txt')

sources = {
    'qtbase': 'https://download.qt.io/official_releases/qt/6.11/6.11.2/submodules/qtbase-everywhere-src-6.11.2.tar.xz',
    'qtsvg': 'https://download.qt.io/official_releases/qt/6.11/6.11.2/submodules/qtsvg-everywhere-src-6.11.2.tar.xz',
    'pyside': 'https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.2-src/pyside-setup-everywhere-src-6.11.2.tar.xz',
}
for name, url in sources.items():
    archive = root / 'build' / (name + '-source.tar.xz')
    if not archive.exists():
        urllib.request.urlretrieve(url, archive)
    with tarfile.open(archive) as tar:
        for member in tar:
            path = Path(member.name)
            if not member.isfile() or '..' in path.parts or path.is_absolute():
                continue
            is_license = any(part.lower().startswith(('license', 'copying', 'notice', 'copyright', 'authors')) for part in path.parts)
            is_attribution = '3rdparty' in path.parts and (path.name.lower().startswith(('readme', 'license')) or path.name == 'qt_attribution.json')
            if (is_license or is_attribution) and member.size < 2 * 1024**2:
                destination = output / name / Path(*path.parts[1:])
                destination.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as stream:
                    destination.write_bytes(stream.read())
if sys.platform.startswith('linux'):
    urllib.request.urlretrieve('https://raw.githubusercontent.com/unicode-org/icu/release-73-2/LICENSE', output / 'ICU-73.2-LICENSE.txt')
    # Preserve packaged system-library notices; no machine configuration is read.
    docs = Path('/usr/share/doc')
    for pattern in ('lib*', 'zlib*', 'gcc*'):
        for folder in docs.glob(pattern):
            if (folder / 'copyright').is_file():
                destination = output / 'system' / folder.name
                destination.mkdir(parents=True, exist_ok=True)
                shutil.copy2(folder / 'copyright', destination / 'copyright')
(output / 'versions.json').write_text(json.dumps({'python': sys.version.split()[0], 'qt_sources': sources,
    'packages': {p: importlib.metadata.version(p) for p in ('PySide6-Essentials', 'shiboken6', 'yt-dlp', 'PyInstaller', 'certifi')}}, indent=2) + '\n')
print(f'Collected {sum(p.is_file() for p in output.rglob("*"))} license and attribution files')
