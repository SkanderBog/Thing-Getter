import hashlib
import json
import os
import re
import time
from http.client import HTTPException
from pathlib import Path
from .inspect import sniff
from .net import ScoutError, normalize, now


def atomic_json(path, value):
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    os.replace(temporary, path)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validator(headers):
    etag = headers.get("ETag")
    if etag and not etag.startswith("W/"):
        return "ETag", etag
    modified = headers.get("Last-Modified")
    return ("Last-Modified", modified) if modified else (None, None)


def download_file(client, url, output, max_bytes=50 * 1024 * 1024, progress=None):
    """Retry transient interrupted transfers; keep validated partials for resume."""
    if max_bytes <= 0:
        raise ScoutError("max_bytes must be positive")
    for attempt in range(3):
        try:
            return _download(client, url, output, max_bytes, progress)
        except (OSError, HTTPException) as exc:
            if attempt == 2:
                raise ScoutError(f"Transfer interrupted after 3 attempts; partial retained: {exc}") from exc
            client.pause(2 ** attempt)


def _download(client, url, output, max_bytes, progress=None):
    url = normalize(url)
    target = Path(output).absolute()
    if target.exists() or target.is_symlink():
        raise ScoutError("Output already exists; choose a new filename")
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    state_path = target.with_name(target.name + ".part.json")
    manifest = target.with_name(target.name + ".provenance.json")
    for path in (part, state_path, manifest, state_path.with_name(state_path.name + ".tmp"), manifest.with_name(manifest.name + ".tmp")):
        if path.is_symlink():
            raise ScoutError("Refusing a symlink in download state")
    if manifest.exists():
        raise ScoutError("Provenance output already exists; choose a new filename")
    state = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            pass
        if not isinstance(state, dict):
            raise ScoutError("Invalid partial state; choose a new output or remove the partial and its state")
    offset = 0
    if part.exists():
        if state.get("url") != url:
            raise ScoutError("Partial file belongs to another/unknown URL; choose a new output")
        if state.get("validator_value"):
            offset = part.stat().st_size
    if offset > max_bytes:
        raise ScoutError("Existing partial exceeds requested byte limit")
    headers = {"Range": f"bytes={offset}-", "If-Range": state["validator_value"]} if offset else {}
    with client.open(url, headers=headers) as r:
        if r.status not in {200, 206}:
            raise ScoutError(f"Download returned HTTP {r.status}; no file completed")
        length = r.headers.get("Content-Length")
        length = int(length) if length and length.isdecimal() else None
        key, value = validator(r.headers)
        resumed = False
        expected_total = length
        if r.status == 206:
            match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", r.headers.get("Content-Range", ""))
            if not match:
                raise ScoutError("Invalid or unknown-total Content-Range; resume refused")
            start, end, total = map(int, match.groups())
            if start != offset or end < start or end >= total or end != total - 1:
                raise ScoutError("Content-Range does not cover the requested remainder")
            if length is not None and length != end - start + 1:
                raise ScoutError("Content-Length disagrees with Content-Range")
            if offset and (key != state.get("validator_key") or value != state.get("validator_value")):
                raise ScoutError("Resource validator changed during resume")
            expected_total = total
            resumed = bool(offset)
        elif offset:
            offset = 0  # If-Range changed, or server ignores Range: restart safely.
        if expected_total is not None and expected_total > max_bytes:
            raise ScoutError(f"File exceeds byte limit ({expected_total:,} bytes; limit {max_bytes:,}). Choose a smaller file or increase --max-mb")
        if r.headers.get("Content-Encoding", "identity").lower() not in {"", "identity"}:
            raise ScoutError("Encoded transfer cannot be byte-validated by this prototype")
        first = r.read(min(64 * 1024, max_bytes - offset + 1))
        client.remaining()
        if resumed:
            with part.open("rb") as stream:
                fmt = sniff(stream.read(4096), state.get("mime", ""))
        else:
            fmt = sniff(first, r.headers.get("Content-Type", ""))
        if fmt in {"html", "unknown", "hls_manifest", "dash_manifest"} or not first:
            raise ScoutError(f"Expected a document/media file, received {fmt}; inspect the URL first")
        if offset + len(first) > max_bytes:
            raise ScoutError("Transfer exceeds byte limit")
        state = {"url": url, "resolved_url": r.geturl(), "validator_key": key, "validator_value": value,
                 "mime": r.headers.get("Content-Type", ""), "updated_at": now()}
        # Truncate a stale partial before updating the validator; never label old bytes as new.
        with part.open("ab" if resumed else "wb") as stream:
            atomic_json(state_path, state)
            stream.write(first)
            count = offset + len(first)
            if progress:
                progress(count, expected_total)
            while chunk := r.read(min(64 * 1024, max_bytes - count + 1)):
                client.remaining()
                count += len(chunk)
                if count > max_bytes:
                    raise ScoutError("Transfer exceeds byte limit; partial retained")
                stream.write(chunk)
                if progress:
                    progress(count, expected_total)
            stream.flush()
            os.fsync(stream.fileno())
        if expected_total is not None and count != expected_total:
            raise HTTPException(f"Truncated transfer: expected {expected_total}, received {count}")
    if expected_total is None:
        transfer = "stream_ended; server supplied no total length"
    else:
        transfer = "byte_count_matches_server"
    result = {**state, "completed_at": now(), "path": str(target), "bytes": count,
              "sha256": digest(part), "format": fmt, "resumed_from_bytes": offset,
              "transfer_integrity": transfer, "completeness": "unknown; no edition/reference comparison",
              "rights": "not determined by download", "malware_scan": "not performed"}
    # Hard-link creation fails if another process already created the target.
    os.link(part, target)
    part.unlink()
    atomic_json(manifest, result)
    state_path.unlink(missing_ok=True)
    return result
