"""Mari Cloud connector — website crawler (same-origin, SSRF-guarded).

Crawls a public site from a start URL: sitemap.xml when present, otherwise a
same-origin BFS over links. HTML is reduced to markdown-ish text (headings
kept as #). Stdlib plus the shared SSRF guard.

SSRF guard: server/nethttp.py — the single implementation this module used to
carry its own copy of. Only http(s); the hostname is resolved and EVERY address
checked against private/loopback/link-local/reserved ranges; every redirect hop
is re-validated; the ACTUAL connected peer IP is re-checked after connect (no
DNS-rebinding TOCTOU). Dev override: MARI_CRAWL_ALLOW_LOOPBACK=1 permits loopback only
(so a local dev site can be crawled) — link-local (cloud metadata, e.g.
169.254.169.254) and private ranges are ALWAYS refused.

Incremental: cursor is the ISO timestamp of the previous crawl. On a sitemap
recrawl we send If-Modified-Since; a 304 yields an item with an empty body and
the same hash_hint (etag/last-modified), which the sync worker's hash map
skips. Link-discovery (BFS) recrawls fetch unconditionally — a 304 has no body
to extract links from — and rely on the content-hash hash_hint to skip
unchanged pages.
"""

from __future__ import annotations

import datetime
import email.utils
import hashlib
import html as html_mod
import os
import re
import urllib.parse

from . import _net

PROVIDER = {
    "key": "website",
    "name": "Website",
    "blurb": "Docs or marketing site — crawled page by page from a start URL.",
    "fields": [
        {"key": "start_url", "label": "Start URL", "secret": False,
         "placeholder": "https://docs.example.com/",
         "help": "Same-origin pages reachable from here (sitemap.xml used when present)."},
    ],
    "docs_url": "https://www.sitemaps.org/protocol.html",
}

UA = "Mozilla/5.0 (compatible; MariCloud-WebsiteConnector/1.0)"
MAX_PAGES = 200
PAGE_CAP = 1024 * 1024          # 1MB/page
PAGE_TIMEOUT = 8.0              # 8s/page

_SKIP_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".css", ".js",
             ".mjs", ".json", ".xml", ".pdf", ".zip", ".gz", ".tar", ".mp4", ".mp3",
             ".woff", ".woff2", ".ttf", ".eot", ".map", ".wasm", ".rss", ".atom")


class WebsiteError(Exception):
    pass


# ————— SSRF-guarded fetching —————
#
# The guard itself lives in server/nethttp.py — one implementation, shared with
# every connector and with brand import. This module only decides the policy it
# runs under: MARI_CRAWL_ALLOW_LOOPBACK=1 permits loopback ONLY (so a local dev
# site can be crawled). Link-local (cloud metadata, e.g. 169.254.169.254),
# private and reserved ranges are refused with or without the override.

def _allow_loopback() -> bool:
    return os.environ.get("MARI_CRAWL_ALLOW_LOOPBACK") == "1"


def _check_url(url: str) -> str | None:
    """Return an error string if the URL must not be fetched, else None."""
    return _net.check_url(url, allow_loopback=_allow_loopback())


def _fetch(url: str, extra_headers: dict | None = None,
           cap: int = PAGE_CAP, timeout: float = PAGE_TIMEOUT):
    """SSRF-guarded GET with manual redirects (each hop re-checked).

    Returns (status, body_bytes, headers_dict, final_url) — status 200 or 304 —
    or an error string. Kept as ONE function so tests can monkeypatch it.
    """
    headers = {"Accept": "text/html,application/xhtml+xml,*/*"}
    if extra_headers:
        headers.update(extra_headers)
    try:
        resp = _net.fetch(url, headers=headers, timeout=timeout, cap=cap,
                          allow_loopback=_allow_loopback(), user_agent=UA)
    except _net.Blocked as e:
        return str(e)
    except _net.NetworkError as e:
        return f"fetch failed: {e}"
    if resp.status in (200, 304):
        return resp.status, resp.body, resp.headers, resp.url
    return f"HTTP {resp.status}"


# ————— html → text —————

def _hget(headers: dict, name: str) -> str | None:
    """Case-insensitive header lookup (servers vary: 'ETag' vs 'etag')."""
    low = name.lower()
    for k, v in headers.items():
        if k.lower() == low:
            return v
    return None


_BLOCK_STRIP = ("script", "style", "noscript", "svg", "template", "iframe",
                "nav", "header", "footer", "aside", "form")


def _strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", " ", s)


def _decode(body: bytes, ctype: str) -> str:
    charset = "utf-8"
    m = re.search(r"charset=([\w-]+)", ctype or "")
    if m:
        charset = m.group(1)
    return body.decode(charset, errors="replace")


def extract_text(html: str) -> tuple[str, str]:
    """(title, markdown-ish body) from an HTML page."""
    html = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
    title = ""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    if m:
        title = re.sub(r"\s+", " ", html_mod.unescape(_strip_tags(m.group(1)))).strip()
    for tag in _BLOCK_STRIP:
        html = re.sub(rf"<{tag}\b[^>]*>.*?</{tag}\s*>", " ", html, flags=re.I | re.S)
        html = re.sub(rf"<{tag}\b[^>]*/>", " ", html, flags=re.I)
    # headings → markdown
    def _h(m2: re.Match) -> str:
        level = int(m2.group(1))
        text = re.sub(r"\s+", " ", html_mod.unescape(_strip_tags(m2.group(2)))).strip()
        return f"\n\n{'#' * level} {text}\n\n" if text else "\n"
    html = re.sub(r"<h([1-6])\b[^>]*>(.*?)</h\1\s*>", _h, html, flags=re.I | re.S)
    if not title:
        m = re.search(r"^#\s+(.+)$", html, re.M)
        if m:
            title = m.group(1).strip()
    # structure → newlines / bullets
    html = re.sub(r"<li\b[^>]*>", "\n- ", html, flags=re.I)
    html = re.sub(r"<(?:p|div|section|article|table|tr|ul|ol|blockquote|pre|figure)\b[^>]*>",
                  "\n", html, flags=re.I)
    html = re.sub(r"</(?:p|div|section|article|table|tr|ul|ol|blockquote|pre|figure)\s*>",
                  "\n", html, flags=re.I)
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    text = html_mod.unescape(_strip_tags(html))
    # collapse whitespace: spaces/tabs within lines, ≥2 blank lines → 1
    lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in text.splitlines()]
    out: list[str] = []
    for ln in lines:
        if ln:
            out.append(ln)
        elif out and out[-1] != "":
            out.append("")
    body = "\n".join(out).strip()
    if not title:
        title = (body.splitlines() or [""])[0].lstrip("# ").strip()[:120]
    return title, body


def _extract_links(html: str, base_url: str) -> list[str]:
    links: list[str] = []
    for href in re.findall(r"""<a\b[^>]*?href\s*=\s*["']([^"'#]+)""", html, re.I):
        href = href.strip()
        if not href or href.startswith(("mailto:", "javascript:", "tel:", "data:")):
            continue
        full = urllib.parse.urljoin(base_url, href)
        full = urllib.parse.urldefrag(full)[0]
        links.append(full)
    return links


def _same_origin(a: str, b: str) -> bool:
    pa, pb = urllib.parse.urlparse(a), urllib.parse.urlparse(b)
    return (pa.scheme, pa.hostname, pa.port) == (pb.scheme, pb.hostname, pb.port)


def _norm_start(url: str) -> str:
    url = (url or "").strip()
    if url and "://" not in url:
        url = "https://" + url
    return url


# ————— sitemap —————

def _sitemap_urls(start_url: str) -> list[str]:
    """URLs from sitemap.xml (start dir first, then origin root); [] if none."""
    p = urllib.parse.urlparse(start_url)
    candidates = [urllib.parse.urljoin(start_url if start_url.endswith("/") else start_url + "/",
                                       "sitemap.xml"),
                  f"{p.scheme}://{p.netloc}/sitemap.xml"]
    seen_candidates: set[str] = set()
    for sm in candidates:
        if sm in seen_candidates:
            continue
        seen_candidates.add(sm)
        res = _fetch(sm)
        if isinstance(res, str) or res[0] != 200:
            continue
        xml = _decode(res[1], _hget(res[2], "Content-Type") or "")
        locs = [html_mod.unescape(l.strip())
                for l in re.findall(r"<loc>\s*(.*?)\s*</loc>", xml, re.I | re.S)]
        if not locs:
            continue
        if re.search(r"<sitemapindex", xml, re.I):  # sitemap index → fetch children
            urls: list[str] = []
            for child in locs[:5]:
                cres = _fetch(child)
                if isinstance(cres, str) or cres[0] != 200:
                    continue
                cxml = _decode(cres[1], _hget(cres[2], "Content-Type") or "")
                urls += [html_mod.unescape(l.strip())
                         for l in re.findall(r"<loc>\s*(.*?)\s*</loc>", cxml, re.I | re.S)]
            locs = urls
        same = [u for u in locs if _same_origin(u, start_url)]
        if same:
            return same[:MAX_PAGES]
    return []


# ————— contract surface —————

def validate(config: dict) -> str | None:
    url = _norm_start(config.get("start_url", ""))
    if not url:
        return "start_url is required"
    err = _check_url(url)
    if err:
        return err
    res = _fetch(url)
    if isinstance(res, str):
        return f"could not fetch {url}: {res}"
    status, body, headers, _final = res
    ctype = (_hget(headers, "Content-Type") or "").lower()
    if status == 200 and "html" not in ctype and b"<html" not in body[:2048].lower():
        return f"{url} did not return an HTML page (Content-Type: {ctype or 'unknown'})"
    return None


def list_items(config: dict, cursor: str | None) -> tuple[list[dict], str | None]:
    start_url = _norm_start(config.get("start_url", ""))
    if not start_url:
        raise WebsiteError("start_url is required")
    err = _check_url(start_url)
    if err:
        raise WebsiteError(err)

    cond_headers: dict = {}
    if cursor:
        try:
            dt = datetime.datetime.fromisoformat(cursor.replace("Z", "+00:00"))
            cond_headers["If-Modified-Since"] = email.utils.format_datetime(
                dt.astimezone(datetime.timezone.utc), usegmt=True)
        except ValueError:
            pass

    crawl_started = datetime.datetime.now(datetime.timezone.utc)\
        .replace(microsecond=0).isoformat()

    sitemap = _sitemap_urls(start_url)
    queue: list[str] = sitemap if sitemap else [start_url]
    use_bfs = not sitemap
    visited: set[str] = set()
    items: list[dict] = []

    while queue and len(items) < MAX_PAGES:
        url = queue.pop(0)
        url = urllib.parse.urldefrag(url)[0]
        if url in visited or not _same_origin(url, start_url):
            continue
        visited.add(url)
        path_l = urllib.parse.urlparse(url).path.lower()
        if path_l.endswith(_SKIP_EXT):
            continue
        # BFS discovers links from page bodies — a 304 has no body, so a
        # conditional recrawl would collapse the frontier to the start page.
        # Fetch unconditionally when crawling by links; the content-hash
        # hash_hint still lets the sync worker skip unchanged pages.
        res = _fetch(url, extra_headers=None if use_bfs else cond_headers)
        if isinstance(res, str):
            continue  # skip unreachable/refused pages, keep crawling
        status, body, headers, final_url = res
        if not _same_origin(final_url, start_url):
            continue  # redirected off-origin
        path = urllib.parse.urlparse(final_url).path or "/"
        etag = _hget(headers, "ETag")
        last_mod = _hget(headers, "Last-Modified")
        if status == 304:
            # unchanged since cursor — empty body + stable hash_hint; the sync
            # worker's per-item hash map skips it without re-chunking.
            items.append({"path": path, "title": path, "body": "",
                          "updated_at": cursor or crawl_started,
                          "hash_hint": etag or last_mod, "unchanged": True})
            continue
        ctype = (_hget(headers, "Content-Type") or "").lower()
        if "html" not in ctype and b"<html" not in body[:2048].lower():
            continue
        html = _decode(body, ctype)
        title, text = extract_text(html)
        if last_mod:
            try:
                updated = email.utils.parsedate_to_datetime(last_mod)\
                    .astimezone(datetime.timezone.utc).isoformat()
            except (TypeError, ValueError):
                updated = crawl_started
        else:
            updated = crawl_started
        hint = etag or last_mod or hashlib.sha256(text.encode()).hexdigest()[:16]
        items.append({"path": path, "title": title or path, "body": text,
                      "updated_at": updated, "hash_hint": hint})
        if use_bfs:
            for link in _extract_links(html, final_url):
                if link not in visited and _same_origin(link, start_url):
                    queue.append(link)

    return items, crawl_started
