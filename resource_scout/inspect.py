import json
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from .net import now, normalize

PROBE_BYTES = 128 * 1024
EXTENSIONS = {".pdf", ".epub", ".txt", ".mp4", ".webm", ".mp3", ".ogg", ".m4a", ".m3u8", ".mpd"}


def sniff(raw, mime=""):
    stripped = raw.lstrip(b"\xef\xbb\xbf \r\n\t").lower()
    # Error pages may start with comments or an XHTML XML declaration.
    while stripped.startswith((b'<!--', b'<?xml')):
        marker = b'-->' if stripped.startswith(b'<!--') else b'?>'
        end = stripped.find(marker)
        if end < 0:
            break
        stripped = stripped[end + len(marker):].lstrip()
    # HTML error pages win over misleading file extensions and MIME headers.
    if stripped.startswith((b"<!doctype html", b"<html", b"<head", b"<body")):
        return "html"
    if raw.startswith(b"%PDF-"):
        return "pdf"
    if raw.startswith(b"PK\x03\x04"):
        return "zip_container"  # A prefix alone cannot validate an EPUB.
    if len(raw) > 12 and raw[4:8] == b"ftyp":
        return "mp4"
    if raw.startswith(b"\x1a\x45\xdf\xa3"):
        return "webm"
    if raw.startswith(b"OggS"):
        return "ogg"
    if raw.startswith(b"ID3") or raw[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"}:
        return "mp3"
    if stripped.startswith(b"#extm3u"):
        return "hls_manifest"
    if b"<mpd" in stripped[:512]:
        return "dash_manifest"
    if "html" in mime:
        return "html"
    if mime.startswith("text/plain") and raw and b"\x00" not in raw[:4096]:
        return "text"
    return "unknown"


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self.text, self.links, self.structured = [], [], [], []
        self.in_title = False
        self.hidden = 0
        self.script = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "title":
            self.in_title = True
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag == "script" and attrs.get("type") == "application/ld+json":
            self.script = []
        if tag in {"a", "link", "source", "video", "audio"}:
            url = attrs.get("href") or attrs.get("src")
            if url:
                self.links.append((url, attrs.get("type", ""), attrs.get("rel", "")))

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if tag == "script" and self.script is not None:
            try:
                self.structured.append(json.loads("".join(self.script)))
            except ValueError:
                pass
            self.script = None
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if self.in_title:
            self.title.append(data)
        if self.script is not None:
            self.script.append(data)
        if not self.hidden:
            self.text.append(data)


def objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from objects(child)


def inspect_url(client, url):
    report = {"url": url, "checked_at": now(), "access": "unknown", "downloadability": "unknown",
              "completeness": "unknown", "rights": "unknown", "files": [], "evidence": []}
    try:
        with client.open(url, headers={"Range": f"bytes=0-{PROBE_BYTES - 1}"}) as r:
            report.update(http_status=r.status, resolved_url=r.geturl(), mime=r.headers.get("Content-Type", ""))
            if r.status not in {200, 206}:
                report["evidence"].append(f"HTTP {r.status}; does not establish global availability")
                report["next_step"] = "Retry later or inspect the source interactively; no automatic gate bypass"
                return report
            raw = client.read(r, PROBE_BYTES)
            fmt = sniff(raw, report["mime"])
            report.update(format=fmt, bytes_observed=len(raw), content_range=r.headers.get("Content-Range"),
                          content_length_reported=r.headers.get("Content-Length"))
        if fmt == "html":
            report["access"] = "page_retrieved; work_access_unknown"
            page = Page()
            page.feed(raw.decode("utf-8", "replace"))
            report["title"] = " ".join(" ".join(page.title).split())
            body = " ".join(" ".join(page.text).split())
            if re.search(r"verify (?:you are|that you are) human|captcha|checking your browser", body, re.I):
                report["access"] = "possible_bot_challenge"
            if re.search(r"(?:sign in|log in|register) to (?:read|access|continue)", body, re.I):
                report["access"] = "possible_registration_gate"
            if re.search(r"(?:subscribe|purchase) to (?:read|access|continue|unlock)", body, re.I):
                report["access"] = "possible_subscription_gate"
            for obj in objects(page.structured):
                if obj.get("isAccessibleForFree") in (False, "false"):
                    report["access"] = "publisher_reports_restriction"
                if obj.get("license"):
                    report["rights"] = {"publisher_statement": obj["license"]}
                for field in ("contentUrl", "encodingUrl"):
                    if isinstance(obj.get(field), str):
                        page.links.append((obj[field], obj.get("encodingFormat", ""), ""))
            if re.search(r"\b(preview|excerpt|sample chapter|abridged)\b", report["title"], re.I):
                report["completeness"] = "possible_partial; title contains preview/excerpt wording"
            seen = set()
            for href, mime, rel in page.links:
                try:
                    target = normalize(urljoin(report["resolved_url"], href))
                except ValueError:
                    continue
                except Exception:
                    continue
                if rel == "license":
                    report["rights"] = {"page_license_link": target}
                path = urlsplit(target).path.lower()
                if (any(path.endswith(ext) for ext in EXTENSIONS) or str(mime).startswith(("video/", "audio/", "application/pdf", "application/epub"))) and target not in seen:
                    report["files"].append({"url": target, "format": mime or path.rsplit(".", 1)[-1], "status": "link_only; unverified"})
                    seen.add(target)
                if len(report["files"]) >= 12:
                    break
            report["evidence"].append("HTML prefix inspected; linked files have not been fetched")
            report["next_step"] = "Inspect a listed file URL; use video inspection for media players"
        elif fmt in {"hls_manifest", "dash_manifest"}:
            report.update(access="stream_manifest_observed", downloadability="requires_media_backend")
        elif fmt != "unknown" and raw:
            report.update(access="file_bytes_observed", downloadability="direct_file_observed; full transfer untested")
            report["evidence"].append("Observed file prefix only; identity, file integrity and completeness remain unverified")
        else:
            report["evidence"].append("Response received but file format not recognized")
    except Exception as exc:
        report["error"] = str(exc)[:1000]
    return report
