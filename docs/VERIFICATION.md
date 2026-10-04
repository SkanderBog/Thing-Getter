# Release verification: 0.3.0

Verified on 2026-10-04. The native packages were built from commit [`cf189dd38c2379481c7adc96ffb4e577079d3ea2`](https://github.com/SkanderBog/Thing-Getter/commit/cf189dd38c2379481c7adc96ffb4e577079d3ea2). [All four native jobs passed](https://github.com/SkanderBog/Thing-Getter/actions/runs/37175987332). Later documentation commits do not change the application code or release binaries.

| Native runner | Source tests | Frozen application | Native package verification |
| --- | --- | --- | --- |
| Ubuntu 24.04 x64 | 92 passed | 26 checks passed | Extracted Debian package: 26 checks passed |
| Windows Server 2025 x64 | 92 passed | 26 checks passed | Silent installer, installed app (26 checks), uninstaller passed |
| macOS 15 Apple Silicon | 92 passed | 26 checks passed | Mounted DMG app: 26 checks passed |
| macOS 15 Intel | 92 passed | 26 checks passed | Mounted DMG app: 26 checks passed |

The source suite covers URL/redirect guards, retries, truncated and mislabeled responses, transfer limits, Range/If-Range validation, cancellation with resume, report escaping, provider failures, result selection and preferences. The GUI exercise uses real Qt widgets and worker threads with deterministic network responses. It checks a loaded icon, readable font glyphs, searching, choosing and checking a second format, save/export, overwrite protection, persistent history, error recovery, cancellation and closing during work. Packaged checks also launch the bundled media helper and load the bundled HTTPS certificate roots.

Light and dark screenshots were visually reviewed, including the installed Windows package and both Mac architectures. CI uses Qt's offscreen platform for repeatability. The downloaded Linux tarball also passed all 26 checks through the current desktop's X11 display; that newer Linux host printed an optional GVFS module compatibility diagnostic, without failing the app checks. These checks do not exercise SmartScreen/Gatekeeper prompts, every native file-picker interaction, or every supported media site. Minimum OS targets are based on runtime requirements; the CI systems above are the versions actually exercised. Builds are unsigned and macOS bundles are not Developer ID signed or notarized.

A local frozen-app live check searched Gutendex and downloaded the public-domain text of *Pride and Prejudice*: 772,386 bytes, server length matched, SHA-256 `3f6bb9d6f78e0293b56acd4714dd68cb7d6d1d293402031ce9d5a216bcaf9d75`. This demonstrates that transfer, not the availability of every source or independent content completeness. The live check used the trusted fake-IP proxy option required by that test network. Its downloaded text and local provenance were not published.

## Public-content audit

The repository began with an explicit public source allowlist. Local caches, development environments, private reports, logs, downloaded content, older archives and machine configuration were excluded. Every published commit was checked for excluded trees and the originating personal home path. Gitleaks 8.30.1 found no secrets in the public Git history. Public screenshots contain only synthetic example content. These scans complement source review; they do not prove the absence of every possible sensitive value.

Each platform's package script records SHA-256 checksums, and the downloaded CI artifacts are checked against those manifests before uploading to the GitHub release. `SHA256SUMS.txt` accompanies the release. The workflow retains native screenshots and machine-readable checks as build artifacts for 14 days.
