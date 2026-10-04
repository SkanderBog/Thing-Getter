# Thing-Getter 0.3.0

The first desktop release of Thing-Getter brings catalogue search, link inspection, and bounded downloads into a native application.

- Search books, video and audio with visible source progress.
- Inspect a pasted URL and choose from the file links it contains.
- Review format, reported size and access evidence before saving a file.
- Use native save dialogs, cancel active work, and open completed downloads from the app.
- Keep recent downloads and preferences on your computer.
- Use packaged media support without installing Python or yt-dlp.
- Switch between light, dark and system appearance.

The CLI remains available from source. Public source and desktop bundles exclude private development downloads, reports, credentials and personal machine paths. Packages are built from the public source on native GitHub runners.

Downloads establish transfer evidence, not the correct edition or complete content. Default search covers named catalogues; broader web search needs a configured SearXNG service. Authentication, DRM and CAPTCHA handling are not included.

These releases are unsigned and are not notarized by Apple. See the installation guide for platform instructions and the verification record for automated checks and their limits.

## Downloads

| Platform | Recommended | Alternative |
| --- | --- | --- |
| Windows 10 (1809+) / 11, x64 | [Installer](https://github.com/SkanderBog/Thing-Getter/releases/download/v0.3.0/Thing-Getter-0.3.0-windows-x64-setup.exe) | [Portable ZIP](https://github.com/SkanderBog/Thing-Getter/releases/download/v0.3.0/Thing-Getter-0.3.0-windows-x64.zip) |
| macOS 13+, Apple Silicon | [DMG](https://github.com/SkanderBog/Thing-Getter/releases/download/v0.3.0/Thing-Getter-0.3.0-macos-arm64.dmg) | [App ZIP](https://github.com/SkanderBog/Thing-Getter/releases/download/v0.3.0/Thing-Getter-0.3.0-macos-arm64.zip) |
| macOS 13+, Intel | [DMG](https://github.com/SkanderBog/Thing-Getter/releases/download/v0.3.0/Thing-Getter-0.3.0-macos-x64.dmg) | [App ZIP](https://github.com/SkanderBog/Thing-Getter/releases/download/v0.3.0/Thing-Getter-0.3.0-macos-x64.zip) |
| Linux x64, Ubuntu 24.04+ | [Debian package](https://github.com/SkanderBog/Thing-Getter/releases/download/v0.3.0/Thing-Getter-0.3.0-linux-x64.deb) | [Portable tar.gz](https://github.com/SkanderBog/Thing-Getter/releases/download/v0.3.0/Thing-Getter-0.3.0-linux-x64.tar.gz) |

No Python installation is needed. Extract portable archives completely before launching. Linux requires glibc 2.39+ and Qt platform libraries; the Debian package declares them.

[Installation instructions](https://github.com/SkanderBog/Thing-Getter/blob/main/docs/INSTALL.md) · [Privacy](https://github.com/SkanderBog/Thing-Getter/blob/main/docs/PRIVACY.md) · [Checksums](https://github.com/SkanderBog/Thing-Getter/releases/download/v0.3.0/SHA256SUMS.txt)

## Verification

All four [native build jobs passed](https://github.com/SkanderBog/Thing-Getter/actions/runs/37175987332): 92 source tests on each platform, plus 26 checks of each frozen app and another 26 after installing, extracting or mounting its native package. The public Git history passed Gitleaks. [Full verification record and limitations](https://github.com/SkanderBog/Thing-Getter/blob/main/docs/VERIFICATION.md).
