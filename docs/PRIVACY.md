# Privacy and network behavior

- No analytics, telemetry, account registration, advertising or automatic update requests are included.
- Searches send your query to the configured catalogues. An optional SearXNG service receives searches when configured. Source sites receive requests for the links you inspect or save.
- Preferences, catalogue cache and download history remain on your computer. History contains saved paths, sizes and SHA-256 hashes. These are not uploaded by the app.
- Reports and provenance records can contain search terms, URLs, query strings and local filenames. Review them before sharing. Media job logs can contain URLs and errors from the selected site.
- HTTP credentials in URLs, private destinations, unsafe ports and unsafe redirect destinations are rejected by the built-in request client. The opt-in fake-IP proxy setting only relaxes the reserved benchmarking address range used by some trusted local proxies.
- The media backend makes its own requests after the initial URL check. Its subsequent requests do not use the built-in client's destination/robots guard. Download only URLs you trust. External plugins, config files, remote components and JavaScript runtimes are disabled by the adapter.
- A search is not a download. File access, identity, rights and content completeness are separate claims. Authentication and DRM bypass are not supported.

The public source and builds are created from an explicit source allowlist. Development downloads, browsing reports, machine settings, caches, old source archives and local paths are excluded. CI scans tracked source for common secrets and personal paths before packaging. Such scans supplement manual review and cannot prove the absence of every possible secret.
