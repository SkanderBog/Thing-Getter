"""Small HTTP client: bounded reads, retries, caching, robots and pacing.

DNS checks reduce accidental local-network access; they are not a security
sandbox against DNS rebinding or a hostile configured proxy.
"""
import hashlib
import ipaddress
import json
import socket
import sqlite3
import ssl
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener
from urllib.robotparser import RobotFileParser

UA = "ResourceScout/0.1 (local resource discovery; bounded requests)"


def now():
    return datetime.now(timezone.utc).isoformat()


class ScoutError(Exception):
    pass


class BudgetExpired(ScoutError):
    pass


class Cancelled(ScoutError):
    pass


def tls_context():
    """Retain OS trust and add bundled roots for standalone desktop installs."""
    context = ssl.create_default_context()
    try:
        import certifi
    except ImportError:
        pass  # Dependency-free CLI uses the system certificate store.
    else:
        context.load_verify_locations(cafile=certifi.where())
    return context


def normalize(url):
    p = urlsplit(url)
    if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
        raise ScoutError("Expected an HTTP(S) URL without embedded credentials")
    if any(ord(c) < 33 for c in url) or "\\" in url:
        raise ScoutError("Invalid URL characters")
    _ = p.port  # Validate numeric port syntax without imposing network policy here.
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or "/", p.query, ""))


def validate_url(url, proxy_dns=False):
    url = normalize(url)
    p = urlsplit(url)
    if p.port not in {None, 80, 443}:
        raise ScoutError("Only public HTTP/HTTPS ports are supported")
    try:
        addresses = {ipaddress.ip_address(x[4][0]) for x in socket.getaddrinfo(
            p.hostname, p.port or (443 if p.scheme == "https" else 80), type=socket.SOCK_STREAM)}
    except OSError as exc:
        raise ScoutError(f"DNS lookup failed: {exc}") from exc
    try:
        ipaddress.ip_address(p.hostname)
        hostname = False
    except ValueError:
        hostname = True
    def permitted(ip):
        synthetic = (ip.version == 4 and ip in ipaddress.ip_network("198.18.0.0/15")) or (
            ip.version == 6 and ip in ipaddress.ip_network("2001:2::/48"))
        return ip.is_global or (proxy_dns and hostname and synthetic)
    if not addresses or not all(permitted(ip) for ip in addresses):
        raise ScoutError("Non-public DNS address refused. Trusted fake-IP proxy users can use --proxy-dns")
    return url


class Redirects(HTTPRedirectHandler):
    def __init__(self, client, robots):
        self.client, self.robots = client, robots

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.client.validate(newurl)
        if self.robots:
            self.client.check_robots(newurl)
        self.client.pace(newurl)
        req.timeout = min(req.timeout, self.client.remaining())
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Client:
    def __init__(self, cache=".scout/cache.sqlite", proxy_dns=False, delay=1.0, timeout=15, cancel_event=None):
        self.proxy_dns, self.delay, self.timeout = proxy_dns, delay, timeout
        self.cancel_event = cancel_event
        self.tls = None
        self.lock = threading.RLock()
        self.local = threading.local()
        self.next_request, self.robots, self.crawl_delays = {}, {}, {}
        self.stats = {"requests": 0, "cache_hits": 0}
        if cache != ":memory:":
            Path(cache).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(cache), check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, ts REAL, value TEXT)")

    def close(self):
        self.db.close()

    def validate(self, url):
        return validate_url(url, self.proxy_dns)

    @contextmanager
    def budget(self, seconds):
        """Cooperative budget shared by retries, pacing and response reads.

        OS DNS resolution is not interruptible by this standard-library client.
        """
        previous = getattr(self.local, "deadline", None)
        deadline = time.monotonic() + seconds
        self.local.deadline = min(previous, deadline) if previous is not None else deadline
        try:
            yield
        finally:
            self.local.deadline = previous

    def remaining(self):
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise Cancelled("Cancelled; partial downloads are retained for resume")
        deadline = getattr(self.local, "deadline", None)
        if deadline is None:
            return self.timeout
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise BudgetExpired("Time budget reached; try this source again or increase its timeout")
        return min(self.timeout, remaining)

    def pause(self, seconds):
        self.remaining()
        deadline = getattr(self.local, "deadline", None)
        if deadline is not None and seconds >= deadline - time.monotonic():
            raise BudgetExpired("Time budget cannot accommodate another wait or retry")
        if self.cancel_event is None:
            time.sleep(seconds)
        elif self.cancel_event.wait(seconds):
            raise Cancelled("Cancelled")

    def read(self, response, limit):
        """Read a bounded prefix without resetting the whole operation budget."""
        chunks, count = [], 0
        while count < limit:
            timeout = self.remaining()
            # urllib's HTTPResponse exposes its socket through the buffered reader.
            # Keep each blocking read within the remaining budget when available.
            sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
            if sock is not None:
                sock.settimeout(timeout)
            chunk = response.read1(min(64 * 1024, limit - count))
            if not chunk:
                break
            chunks.append(chunk)
            count += len(chunk)
        self.remaining()
        return b"".join(chunks)

    def pace(self, url):
        self.remaining()
        host = urlsplit(url).netloc
        with self.lock:
            wait = max(0, self.next_request.get(host, 0) - time.monotonic())
            self.next_request[host] = time.monotonic() + wait + max(self.delay, self.crawl_delays.get(host, 0))
        if wait:
            self.pause(wait)

    def cached(self, key, ttl):
        with self.lock:
            row = self.db.execute("SELECT ts,value FROM cache WHERE key=?", (key,)).fetchone()
            if row and time.time() - row[0] < ttl:
                self.stats["cache_hits"] += 1
                return json.loads(row[1]), round(time.time() - row[0], 2)
        return None, None

    def save(self, key, value):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?)", (key, time.time(), json.dumps(value)))
            self.db.commit()

    def check_robots(self, url):
        p = urlsplit(url)
        origin = f"{p.scheme}://{p.netloc}"
        with self.lock:
            parser = self.robots.get(origin)
        if parser is None:
            target = origin + "/robots.txt"
            try:
                with self.open(target, robots=False) as r:
                    if r.status == 404:
                        text = ""
                    elif r.status != 200:
                        raise ScoutError(f"robots.txt returned HTTP {r.status}; crawl deferred")
                    else:
                        raw = self.read(r, 256 * 1024 + 1)
                        if len(raw) > 256 * 1024:
                            raise ScoutError("robots.txt too large; crawl deferred")
                        length = r.headers.get("Content-Length", "")
                        if length.isdecimal() and len(raw) != int(length):
                            raise ScoutError("robots.txt truncated; crawl deferred")
                        text = raw.decode("utf-8", "replace")
                        if text.lstrip().lower().startswith(("<!doctype", "<html")):
                            raise ScoutError("robots.txt returned HTML; crawl deferred")
                parser = RobotFileParser(target)
                parser.parse(text.splitlines())
                with self.lock:
                    self.robots[origin] = parser
                    self.crawl_delays[p.netloc] = parser.crawl_delay(UA) or parser.crawl_delay("*") or 0
            except (OSError, URLError, HTTPException) as exc:
                raise ScoutError(f"robots.txt unavailable: {exc}") from exc
        if not parser.can_fetch(UA, url):
            raise ScoutError("robots.txt disallows this path; access remains unknown")

    @contextmanager
    def open(self, url, headers=None, robots=True):
        self.remaining()
        url = self.validate(url)
        self.remaining()
        if robots:
            self.check_robots(url)
        response = None
        for attempt in range(3):
            self.pace(url)
            with self.lock:
                self.stats["requests"] += 1
            try:
                handlers = [Redirects(self, robots)]
                if url.startswith('https://'):
                    if self.tls is None:
                        self.tls = tls_context()
                    handlers.append(HTTPSHandler(context=self.tls))
                response = build_opener(*handlers).open(Request(url, headers={
                    "User-Agent": UA, "Accept-Encoding": "identity", **(headers or {})}), timeout=self.remaining())
            except HTTPError as exc:
                response = exc
            except (URLError, OSError, HTTPException) as exc:
                if attempt == 2:
                    raise ScoutError(f"Network request failed: {exc}") from exc
                self.pause(2 ** attempt)
                continue
            if response.status in {429, 500, 502, 503, 504} and attempt < 2:
                retry = response.headers.get("Retry-After", "")
                # Do not retry earlier than a long/date-form server instruction.
                if retry and (not retry.isdecimal() or int(retry) > 10):
                    break
                response.close()
                self.pause(max(2 ** attempt, int(retry or 0)))
                continue
            break
        try:
            yield response
        finally:
            if response is not None:
                response.close()

    def json(self, url, ttl=3600):
        key = "json:" + hashlib.sha256(url.encode()).hexdigest()
        hit, age = self.cached(key, ttl)
        if hit is not None:
            return hit["data"], {"observed_at": hit["observed_at"], "cache_age_seconds": age, "url": url}
        failure_key = "provider-failure:" + urlsplit(url).netloc
        failure, age = self.cached(failure_key, 120 if ttl else 0)
        if failure is not None:
            raise ScoutError(f"Provider cooling down after failure {age}s ago: {failure['error']}")
        # Only provider adapters call this method, for documented API endpoints.
        try:
            with self.open(url, robots=False) as r:
                if r.status != 200:
                    raise ScoutError(f"Provider returned HTTP {r.status}")
                raw = self.read(r, 4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024:
                    raise ScoutError("Provider response exceeds 4 MiB")
                length = r.headers.get("Content-Length", "")
                if length.isdecimal() and len(raw) != int(length):
                    raise ScoutError("Provider response was truncated")
                data = json.loads(raw)
        except (ScoutError, OSError, ValueError, HTTPException) as exc:
            self.save(failure_key, {"error": str(exc), "observed_at": now()})
            raise ScoutError(f"Provider request failed: {exc}") from exc
        stamp = now()
        self.save(key, {"data": data, "observed_at": stamp})
        with self.lock:
            self.db.execute("DELETE FROM cache WHERE key=?", (failure_key,))
            self.db.commit()
        return data, {"observed_at": stamp, "cache_age_seconds": 0, "url": url}
