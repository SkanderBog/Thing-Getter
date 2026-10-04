# Install Thing-Getter

Download files only from the [GitHub releases page](https://github.com/SkanderBog/Thing-Getter/releases). No Python installation is required. SHA-256 checksums accompany each release.

## Windows (x64)

Use the `windows-x64-setup.exe` installer. It installs for your user without administrator access and adds a Start menu shortcut and uninstaller. An optional desktop shortcut is offered.

The `windows-x64.zip` is portable: extract the entire folder and launch `Thing-Getter.exe`. Keep the `_internal` directory beside it. Windows 10/11 x64 is the intended target; automated builds are exercised on Windows Server 2025. The unsigned installer/application may show a SmartScreen warning. Verify its source and checksum before choosing to run it.

## macOS

Choose `macos-arm64` for Apple Silicon or `macos-x64` for Intel. Open the DMG and drag **Thing-Getter.app** into Applications. A zipped application is provided as an alternative. macOS 13 or later is the intended minimum; packaged tests run on macOS 15.

The application is ad-hoc signed for runtime integrity, but has no Apple Developer ID signature or notarization. Gatekeeper may block the first launch. After verifying the download, use **System Settings → Privacy & Security → Open Anyway** if macOS offers that option. Organization-managed Macs may prohibit unsigned apps.

## Linux (x64)

On Ubuntu 24.04 or compatible Debian-family desktops, download the `.deb` and install it with your software manager, or:

```sh
sudo apt install ./Thing-Getter-*-linux-x64.deb
```

Launch **Thing-Getter** from the application menu. The portable `.tar.gz` can be extracted anywhere; run `Thing-Getter/Thing-Getter` inside it. Keep its contents together. The bundle targets glibc 2.39+ and needs a graphical desktop and the usual Qt platform libraries; the Debian package declares those dependencies. Other Linux distributions are not automatically tested.

## Data and removal

Preferences, cache and history use the operating system's per-user application-data location under `ThingGetter/Thing-Getter`. Downloaded files remain in the folders you selected. Clear history and catalogue cache from the app before uninstalling if desired. Clearing history does not delete downloads. Direct downloads can also leave `.part`, `.part.json` and `.provenance.json` sidecars beside the file. Media jobs use a separate `video-*` folder with a log and provenance record.

## Verify a download

Compare the hash against `SHA256SUMS.txt` from the same release:

```sh
# Linux
sha256sum -c SHA256SUMS.txt --ignore-missing
# macOS: compute the selected file's hash and compare it with SHA256SUMS.txt
shasum -a 256 Thing-Getter-0.3.0-macos-arm64.dmg
```

On Windows, use PowerShell `Get-FileHash` with `-Algorithm SHA256`. Checksums detect changed files; they are not code-signing certificates.
