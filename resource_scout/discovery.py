import concurrent.futures
import re
import time
from contextlib import nullcontext
from urllib.parse import quote, urlencode, urlsplit
from .net import normalize, now, ScoutError, Client


def candidate(provider, title, url, authors=None, files=None, **extra):
    return {"provider": provider, "title": title, "url": url, "authors": authors or [],
            "identity": "candidate; edition not verified", "access": "catalogue_only",
            "downloadability": "unverified", "completeness": "unknown", "rights": "unknown",
            "files": files or [], **extra}


def clean_candidate(item, provider):
    """Keep malformed catalogue fields out of ranking and generated reports."""
    if not isinstance(item, dict) or not isinstance(item.get("url"), str):
        raise ScoutError("Provider result is missing a URL")
    item = dict(item)
    item["url"] = normalize(item["url"])
    item["provider"] = provider
    if not isinstance(item.get("title"), str) or not item["title"].strip():
        item["title"] = item["url"]
    authors = item.get("authors") or []
    if isinstance(authors, str):
        authors = [authors]
    item["authors"] = [a for a in authors if isinstance(a, str)] if isinstance(authors, list) else []
    files = item.get("files") or []
    item["files"] = []
    for file in files if isinstance(files, list) else []:
        if not isinstance(file, dict) or not isinstance(file.get("url"), str):
            continue
        try:
            item["files"].append({**file, "url": normalize(file["url"])})
        except (ScoutError, ValueError):
            continue
    return item


def gutendex(client, query, limit, kind, ttl):
    data, evidence = client.json("https://gutendex.com/books/?" + urlencode({"search": query}), ttl)
    results = []
    for book in data.get("results", [])[:limit]:
        files = [{"url": u, "format": mime, "status": "provider_link"}
                 for mime, u in book.get("formats", {}).items()
                 if mime.startswith(("text/", "application/epub", "application/pdf"))]
        files.sort(key=lambda f: (not f["format"].startswith("application/epub"), not f["format"].startswith("text/plain")))
        rights = "Provider reports public domain in the USA" if book.get("copyright") is False else "unknown"
        results.append(candidate("gutendex", book["title"], f"https://www.gutenberg.org/ebooks/{book['id']}",
            [a["name"] for a in book.get("authors", [])], files, rights=rights, evidence=evidence,
            languages=book.get("languages", [])))
    return results


def openlibrary(client, query, limit, kind, ttl):
    data, evidence = client.json("https://openlibrary.org/search.json?" + urlencode({
        "q": query, "limit": limit, "fields": "key,title,author_name,first_publish_year,ebook_access,ia,edition_count"}), ttl)
    results = []
    for book in data.get("docs", [])[:limit]:
        key = book.get("key", "")
        if not key.startswith("/"):
            key = "/works/" + key
        results.append(candidate("openlibrary", book["title"], "https://openlibrary.org" + key,
            book.get("author_name", []), provider_access=book.get("ebook_access", "unknown"),
            first_publish_year=book.get("first_publish_year"), evidence=evidence))
    return results


def archive(client, query, limit, kind, ttl):
    media = {"book": "texts", "video": "movies", "audio": "audio"}.get(kind)
    # Search terms are data; neutralize Archive's query-language punctuation.
    terms = re.findall(r"\w+", query, re.UNICODE)
    q = " AND ".join('"' + term + '"' for term in terms)
    if not q:
        return []
    q = f"({q})" + (f" AND mediatype:{media}" if media else " AND mediatype:(texts OR movies OR audio)")
    params = [("q", q), ("rows", limit), ("output", "json")]
    params += [("fl[]", f) for f in ("identifier", "title", "creator", "mediatype", "licenseurl")]
    data, evidence = client.json("https://archive.org/advancedsearch.php?" + urlencode(params), ttl)
    results = []
    for item in data.get("response", {}).get("docs", [])[:limit]:
        author = item.get("creator", [])
        results.append(candidate("archive", item.get("title", item["identifier"]),
            "https://archive.org/details/" + quote(item["identifier"], safe=""),
            author if isinstance(author, list) else [author],
            archive_id=item["identifier"], media_type=item.get("mediatype"),
            rights=item.get("licenseurl", "unknown"), evidence=evidence))
    return results


def enrich_archive(client, item, ttl):
    data, evidence = client.json("https://archive.org/metadata/" + quote(item["archive_id"], safe=""), ttl)
    restricted = data.get("is_dark") or str(data.get("metadata", {}).get("access-restricted-item", "")).lower() == "true"
    item["metadata_evidence"] = evidence
    if restricted:
        item["access"] = "provider_reports_restriction"
        return
    files = []
    document = {".pdf", ".epub", ".txt"}
    video = {".mp4", ".webm", ".ogv"}
    audio = {".mp3", ".ogg", ".m4a"}
    allowed = {"texts": document, "movies": video, "audio": audio}.get(item.get("media_type"), document | video | audio)
    for f in data.get("files", []):
        name = f.get("name", "")
        if re.search(r"(?:^|/)(?:license|licence|readme|copyright)(?:[._-]|$)", name, re.I):
            continue
        if str(f.get("private", "")).lower() == "true" or not any(name.lower().endswith(ext) for ext in allowed):
            continue
        files.append({"url": "https://archive.org/download/" + quote(item["archive_id"], safe="") + "/" + quote(name, safe=""),
                      "format": f.get("format", "unknown"), "bytes_reported": f.get("size"), "status": "provider_link"})
    # Avoid hundreds of files for an item. Prefer smaller assets for a first check.
    files.sort(key=lambda f: int(f["bytes_reported"]) if str(f["bytes_reported"]).isdigit() else 10**15)
    item["files"] = files[:12]


def searxng(client, query, limit, kind, ttl, endpoint):
    endpoint = normalize(endpoint)
    data, evidence = client.json(endpoint.rstrip("/") + "/search?" + urlencode({
        "q": query, "format": "json", "categories": "videos" if kind == "video" else "general"}), ttl)
    results = []
    for hit in data.get("results", [])[:limit]:
        try:
            url = normalize(hit["url"])
        except (ScoutError, ValueError):
            continue
        results.append(candidate("searxng", hit.get("title", url), url, evidence=evidence,
                                 snippet=hit.get("content", "")[:600]))
    return results


def search(client, query, kind="book", limit=4, verify=3, endpoint=None, ttl=3600, *,
           providers=None, provider_timeout=12, check_timeout=12, progress=None):
    from .inspect import inspect_url
    if not query.strip():
        raise ScoutError("Search query is empty")
    if provider_timeout <= 0 or check_timeout <= 0:
        raise ScoutError("Search time budgets must be positive")
    started = now()
    functions = {"archive": archive}
    if kind in {"book", "all"}:
        functions.update(gutendex=gutendex, openlibrary=openlibrary)
    if endpoint:
        functions["searxng"] = lambda c, q, n, k, t: searxng(c, q, n, k, t, endpoint)
    if providers is not None:
        requested = set(providers)
        if not requested or requested - functions.keys():
            raise ScoutError("Choose available providers: " + ", ".join(functions))
        functions = {k: v for k, v in functions.items() if k in requested}
    def emit(event):
        if progress:
            progress(event)
    def budget(seconds):
        return client.budget(seconds) if isinstance(client, Client) else nullcontext()
    def retrieve(fn):
        begin = time.monotonic()
        with budget(provider_timeout):
            hits = fn(client, query, limit, kind, ttl)
        return hits, round(time.monotonic() - begin, 3)
    items, providers = [], []
    emit({"stage": "start", "providers": list(functions)})
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(retrieve, fn): name for name, fn in functions.items()}
        for future in concurrent.futures.as_completed(futures):
            name = futures[future]
            try:
                hits, elapsed = future.result()
                valid, invalid = [], 0
                for hit in hits:
                    try:
                        valid.append(clean_candidate(hit, name))
                    except (ScoutError, ValueError):
                        invalid += 1
                items.extend(valid)
                provider = {"name": name, "status": "ok", "count": len(valid), "elapsed_seconds": elapsed}
                if invalid:
                    provider["invalid_result_count"] = invalid
                    provider["warning"] = f"Skipped {invalid} malformed provider results"
            except Exception as exc:
                provider = {"name": name, "status": "error", "error": str(exc)[:500]}
                valid = []
            providers.append(provider)
            emit({"stage": "provider", **provider, "titles": [item["title"] for item in valid[:3]]})
    tokens = set(re.findall(r"\w+", query.casefold()))
    for item in items:
        haystack = set(re.findall(r"\w+", (item["title"] + " " + " ".join(item["authors"])).casefold()))
        item["matched_query_terms"] = sorted(tokens & haystack)
        # A transparent retrieval ordering, explicitly not a probability of identity.
        item["rank_score"] = len(tokens & haystack) / max(1, len(tokens)) + (0.1 if item["files"] else 0)
    unique = {}
    for item in sorted(items, key=lambda x: (-x["rank_score"], x["url"])):
        unique.setdefault(normalize(item["url"]), item)
    items = list(unique.values())
    checked = 0
    for index, item in enumerate(items, 1):
        item["result_number"] = index
    for item in items:
        if checked >= verify:
            break
        if item["provider"] == "openlibrary":
            item["verification_note"] = "Catalogue-only record; file-check budget preserved for another candidate"
            continue
        emit({"stage": "checking", "result_number": item["result_number"], "title": item["title"]})
        try:
            with budget(check_timeout):
                if item["provider"] == "archive":
                    enrich_archive(client, item, ttl)
                if item["access"] == "provider_reports_restriction":
                    emit({"stage": "checked", "result_number": item["result_number"], "access": item["access"]})
                    continue
                target = item["files"][0]["url"] if item["files"] else item["url"]
                checked += 1
                check = inspect_url(client, target)
            item["check"] = check
            item["access"] = check["access"]
            item["downloadability"] = check.get("downloadability", "unknown")
            if check.get("files"):
                item["files"].extend(check["files"])
            emit({"stage": "checked", "result_number": item["result_number"], "access": item["access"]})
        except Exception as exc:
            item["check"] = {"access": "unknown", "error": str(exc), "checked_at": now()}
            emit({"stage": "checked", "result_number": item["result_number"], "access": "unknown"})
    return {"query": query, "kind": kind, "started_at": started, "finished_at": now(),
            "scope": "Named providers only; no claim of internet-wide coverage",
            "providers": sorted(providers, key=lambda x: x["name"]), "results": items,
            "stats": dict(client.stats), "verification_budget": verify, "checks_performed": checked,
            "provider_timeout_seconds": provider_timeout, "check_timeout_seconds": check_timeout,
            "note": "No results means not found in sources checked. A file probe does not establish edition or completeness."}
