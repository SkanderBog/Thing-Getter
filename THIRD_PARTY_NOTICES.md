# Third-party notices

Thing-Getter and its original icon are MIT licensed. Bundles include the following independently licensed components:

| Component | Version | License and source |
| --- | --- | --- |
| Python | 3.12 (runner patch version) | [PSF license and source](https://www.python.org/downloads/source/) |
| PySide6 Essentials / Shiboken6 | 6.11.2 | [LGPLv3/GPLv3/commercial licensing](https://doc.qt.io/qtforpython-6/licenses.html); [matching source](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.2-src/) |
| Qt 6 | 6.11.2 | [Qt source archives](https://download.qt.io/official_releases/qt/6.11/6.11.2/submodules/); [third-party notices](https://doc.qt.io/qt-6/licenses.html) |
| yt-dlp | 2026.8.19 | Unlicense; [source and notices](https://github.com/yt-dlp/yt-dlp/tree/2026.08.19) |
| PyInstaller bootloader | 6.22.3 | GPLv2 with distribution exception; [source](https://github.com/pyinstaller/pyinstaller/tree/v6.22.3) |
| certifi | 2026.7.22 | MPL 2.0 certificate bundle; [source](https://github.com/certifi/python-certifi) |
| ICU (Linux Qt dependency) | 73.2 | Unicode/ICU license; [source and notices](https://github.com/unicode-org/icu/tree/release-73-2) |

License texts and available library notices are copied into each bundle's `licenses` directory. Qt/PySide6 is dynamically linked, without modifications, under the LGPL option where applicable. GPL-only optional Qt modules are not used. Consult the individual library notices for included third-party code. Build-time Pillow is not required by the application.

The application does not restrict replacement of LGPL-covered libraries or reverse engineering for debugging such replacements. [Build and replacement instructions](docs/DEVELOPMENT.md) are available alongside the full application source.
