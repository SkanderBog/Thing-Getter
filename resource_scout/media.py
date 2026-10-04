"""Optional yt-dlp adapter. Backend requests are not confined by Client's guard."""
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from .download import atomic_json, digest
from .net import ScoutError, now, Client


def command():
    if importlib.util.find_spec("yt_dlp") is None:
        raise ScoutError('Optional backend missing: install with python -m pip install ".[video]"')
    if getattr(sys, 'frozen', False):
        helper = Path(sys.executable).with_name('ThingGetterMedia' + ('.exe' if sys.platform == 'win32' else ''))
        prefix = [str(helper), '--media-backend']
    else:
        prefix = [sys.executable, "-m", "yt_dlp"]
    return prefix + ["--ignore-config", "--no-plugin-dirs",
            "--no-remote-components", "--no-js-runtimes", "--no-playlist", "--no-progress",
            "--no-warnings", "--socket-timeout", "15", "--retries", "2",
            "--fragment-retries", "2", "--no-check-formats", "--color", "never"]


def inspect_video(client, url):
    client.validate(url)
    try:
        args = command() + ["--skip-download", "--dump-single-json", "--", url]
        options = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}
        if isinstance(client, Client) and client.cancel_event is not None:
            process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **options)
            begin = time.monotonic()
            try:
                while True:
                    client.remaining()
                    if time.monotonic() - begin > 120:
                        raise ScoutError('Media inspection timed out after 120 seconds')
                    try:
                        stdout, stderr = process.communicate(timeout=.2)
                        result = subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
                        break
                    except subprocess.TimeoutExpired:
                        continue
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate()
        else:
            result = subprocess.run(args, capture_output=True, text=True, timeout=120, **options)
    except subprocess.TimeoutExpired as exc:
        raise ScoutError("Media inspection timed out after 120 seconds") from exc
    if result.returncode:
        raise ScoutError("yt-dlp could not inspect this URL: " + result.stderr[-1500:])
    try:
        data = json.loads(result.stdout)
    except ValueError as exc:
        raise ScoutError("yt-dlp returned invalid JSON metadata") from exc
    if not isinstance(data, dict):
        raise ScoutError("yt-dlp returned invalid metadata; expected one media object")
    if data.get("_type") in {"playlist", "multi_video"}:
        raise ScoutError("Supply one video URL, not a playlist")
    formats = data.get("formats") or []
    if not isinstance(formats, list):
        raise ScoutError("yt-dlp returned an invalid format list")
    usable = [f for f in formats if isinstance(f, dict) and not f.get("has_drm") and f.get("url") and f.get("protocol") in {
        "http", "https", "m3u8", "m3u8_native", "http_dash_segments"}]
    return {"url": url, "checked_at": now(), "backend": "yt-dlp", "title": data.get("title"),
            "uploader": data.get("uploader"), "duration_seconds": data.get("duration"),
            "access": "media_metadata_retrieved", "downloadability": "extractor_reports_formats" if usable else "unknown",
            "is_live": data.get("is_live", False), "rights": data.get("license") or "unknown",
            "completeness": "unknown", "format_count": len(usable),
            "formats": [{k: f.get(k) for k in ("format_id", "ext", "height", "protocol", "filesize", "vcodec", "acodec")} for f in usable[:25]],
            "note": "Extractor metadata is not a verified transfer. DRM formats are excluded. Backend makes its own network requests."}


def download_video(client, url, directory, max_bytes=50 * 1024 * 1024, timeout=300):
    if max_bytes <= 0 or timeout <= 0:
        raise ScoutError("Media byte and time budgets must be positive")
    client.validate(url)
    backend = command()
    root = Path(directory).absolute()
    root.mkdir(parents=True, exist_ok=True)
    # Each invocation gets its own job folder; no unrelated files can be overwritten.
    job = Path(tempfile.mkdtemp(prefix="video-", dir=root))
    args = backend + ["--no-overwrites", "--restrict-filenames", "--max-downloads", "1",
        "--max-filesize", str(max_bytes), "--match-filters", "!is_live & !has_drm", "--format", "best",
        "--paths", str(job), "--output", "%(id).80s.%(ext)s", "--", url]
    log = job / "backend.log"
    started = time.monotonic()
    error = None
    with log.open("wb") as stream:
        options = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}
        process = subprocess.Popen(args, stdout=stream, stderr=stream, start_new_session=True, **options)
        try:
            while process.poll() is None:
                client.remaining()
                disk = sum(p.stat().st_size for p in job.rglob("*") if p.is_file())
                if time.monotonic() - started > timeout or disk > max_bytes + 1024 * 1024:
                    error = "Media job exceeded time/disk budget; partial files retained in " + str(job)
                    break
                time.sleep(0.2)
        finally:
            if process.poll() is None:
                # Linux/macOS process group includes any ffmpeg subprocess.
                import os, signal
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            process.wait()
    if error:
        raise ScoutError(error)
    files = [p for p in job.iterdir() if p.is_file() and p.name != "backend.log" and p.suffix in {
        ".mp4", ".webm", ".mkv", ".mp3", ".m4a", ".ogg", ".ogv", ".flv", ".mov", ".ts"}]
    # yt-dlp may use code 101 when its max-download count is reached.
    if process.returncode not in {0, 101} or not files:
        with log.open("rb") as stream:
            stream.seek(max(0, log.stat().st_size - 1500))
            details = stream.read().decode("utf-8", "replace")
        raise ScoutError(f"Media download incomplete; see {log}: {details}")
    if sum(p.stat().st_size for p in files) > max_bytes:
        raise ScoutError("Media exceeds requested byte limit; inspect job folder " + str(job))
    result = {"url": url, "completed_at": now(), "backend": "yt-dlp", "job_directory": str(job),
              "files": [{"path": str(p), "bytes": p.stat().st_size, "sha256": digest(p)} for p in files],
              "completeness": "unknown; extractor completed, content coverage not independently verified",
              "rights": "not determined by download"}
    atomic_json(job / "provenance.json", result)
    return result


def doctor():
    return {"python": sys.version.split()[0], "yt_dlp_installed": importlib.util.find_spec("yt_dlp") is not None,
            "ffmpeg": shutil.which("ffmpeg"), "core_dependencies": "Python standard library only"}
