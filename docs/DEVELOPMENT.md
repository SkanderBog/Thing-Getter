# Build and test

Use Python 3.12 and a virtual environment on the target OS. Desktop builds use pinned PySide6, yt-dlp, PyInstaller and Pillow versions in the requirements files. The app code is MIT licensed; native dependencies have separate notices.

```sh
python -m pip install -r requirements-build.txt
python -m pip install -e . --no-deps
python scripts/make_icons.py
python -m unittest discover -s tests -v
python scripts/collect_licenses.py
python -m PyInstaller --noconfirm Thing-Getter.spec
python scripts/package.py
```

Set `QT_QPA_PLATFORM=offscreen` for headless GUI testing. On Linux, install Qt's system dependencies (see the workflow). The test fixtures bind loopback HTTP servers and do not require internet access. GUI smoke tests inject deterministic responses and exercise real Qt widgets, threads, cancellation, settings, save/export dialogs and history. They do not verify public service availability or simulate every native OS security prompt.

Run a packaged smoke test with `Thing-Getter --self-test OUTPUT_DIRECTORY`; on macOS use the executable inside `Thing-Getter.app/Contents/MacOS`, and on Windows use `Thing-Getter.exe`. The output includes JSON checks and light/dark screenshots. The packaged media helper is launched as a subprocess. `--cli` exposes the command-line interface; `ThingGetterMedia --cli` provides console output on Windows.

The GitHub workflow builds Windows x64, Linux x64, macOS Apple Silicon and macOS Intel on native runners, runs the source suite, then tests the frozen application before creating archives/installers. It uploads build artifacts but does not automatically publish a release. Release publication is a separate reviewed step. Qt libraries remain dynamically linked in the onedir package.

`scripts/audit_public.py` checks the tracked source (or the explicit public allowlist before Git initialization) for unexpected files and common credential/private-path patterns. Inspect the staged diff and history before making a public push. Never package the entire working directory: it can contain ignored private development files.

## Native dependency source and replacement

See `THIRD_PARTY_NOTICES.md` and the bundled `licenses` directory. The source build instructions above recreate the application against the pinned libraries. You can substitute a compatible locally built Qt/PySide6 library set; it must preserve the Python and Qt ABI and platform architecture. Libraries reside in `_internal/PySide6` on Windows/Linux and in the application's `Contents/Frameworks` tree on macOS. Modified macOS bundles need a fresh local ad-hoc signature (`codesign --force --deep --sign - Thing-Getter.app`). Application licensing does not prohibit reverse engineering to debug modifications to LGPL-covered libraries.
