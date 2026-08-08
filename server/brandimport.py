"""Mari — LLM-driven brand import.

Given a company/product homepage URL, harvest brand identity deterministically
(theme-color, manifest, icons, stylesheet colors, fonts) and let the local LLM
pick the accent trio + display font. Returns a candidate branding object the UI
prefills for user review — nothing is persisted server-side.

SSRF guard: only http(s), hostname resolved and every address checked against
private/loopback/link-local ranges; every redirect hop re-validated; the
ACTUAL connected peer IP re-checked after connect (no DNS-rebinding TOCTOU).
"""

from __future__ import annotations

import base64
import colorsys
import http.client
import ipaddress
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request

import llm

UA = "Mozilla/5.0 (compatible; MariCloud-BrandImport/1.0)"
PAGE_CAP = 2 * 1024 * 1024
CSS_CAP = 300 * 1024
ICON_CAP = 200 * 1024
MAX_REDIRECTS = 5
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


# ————— SSRF-guarded fetching —————

def _ip_disallowed(ip_str: str) -> bool:
    ip = ipaddress.ip_address(ip_str.split("%")[0])  # strip IPv6 scope id
    return (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def _check_url(url: str) -> str | None:
    """Return an error string if the URL must not be fetched, else None."""
    try:
        p = urllib.parse.urlparse(url)
    except ValueError:
        return "unparseable URL"
    if p.scheme not in ("http", "https"):
        return f"scheme '{p.scheme}' not allowed (http/https only)"
    host = p.hostname
    if not host:
        return "no hostname"
    try:
        infos = socket.getaddrinfo(host, p.port or (443 if p.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return f"cannot resolve host '{host}'"
    for info in infos:
        if _ip_disallowed(info[4][0]):
            return f"host '{host}' resolves to a private/loopback address — refused"
    return None


# DNS-rebinding guard: _check_url validates one resolution, but the connection
# re-resolves — so re-check the ACTUAL connected peer (getpeername) before any
# request bytes are sent. Certificate validation is untouched (the standard
# connect path does resolution + TLS with server_hostname as usual).

def _verify_peer(sock: socket.socket) -> None:
    peer = sock.getpeername()[0]
    if _ip_disallowed(peer):
        sock.close()
        raise OSError(f"connected peer {peer} is a private/loopback address — refused")


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def connect(self):  # noqa: D102
        super().connect()
        _verify_peer(self.sock)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):  # noqa: D102
        super().connect()
        _verify_peer(self.sock)


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):  # noqa: D102
        return self.do_open(_PinnedHTTPConnection, req)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):  # noqa: D102
        return self.do_open(_PinnedHTTPSConnection, req, context=self._context)


def _fetch(url: str, cap: int, timeout: float = 10.0) -> tuple[bytes, str, str] | str:
    """Fetch with manual redirect handling (each hop SSRF-checked).
    Returns (body, content_type, final_url) or an error string."""
    for _ in range(MAX_REDIRECTS + 1):
        err = _check_url(url)
        if err:
            return err
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
        opener = urllib.request.build_opener(_NoRedirect(), _PinnedHTTPHandler(),
                                             _PinnedHTTPSHandler())
        try:
            with opener.open(req, timeout=timeout) as resp:
                if resp.status in (301, 302, 303, 307, 308):
                    loc = resp.headers.get("Location")
                    if not loc:
                        return "redirect without Location"
                    url = urllib.parse.urljoin(url, loc)
                    continue
                if resp.status != 200:
                    return f"HTTP {resp.status}"
                body = resp.read(cap + 1)
                if len(body) > cap:
                    body = body[:cap]
                return body, resp.headers.get("Content-Type", ""), url
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"):
                url = urllib.parse.urljoin(url, e.headers["Location"])
                continue
            return f"HTTP {e.code}"
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError, ValueError) as e:
            return f"fetch failed: {getattr(e, 'reason', e)}"
    return "too many redirects"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


# ————— color math —————

def _norm_hex(s: str) -> str | None:
    s = s.strip().lower()
    m = re.match(r"^#([0-9a-f]{3})$", s)
    if m:
        return "#" + "".join(c * 2 for c in m.group(1))
    m = re.match(r"^#([0-9a-f]{6})([0-9a-f]{2})?$", s)
    if m:
        return "#" + m.group(1)
    m = re.match(r"^rgba?\(\s*(\d+)[,\s]+(\d+)[,\s]+(\d+)", s)
    if m:
        r, g, b = (min(255, int(v)) for v in m.groups())
        return f"#{r:02x}{g:02x}{b:02x}"
    return None


def _rgb(hexc: str) -> tuple[int, int, int]:
    return int(hexc[1:3], 16), int(hexc[3:5], 16), int(hexc[5:7], 16)


def _hsl(hexc: str) -> tuple[float, float, float]:
    r, g, b = (v / 255 for v in _rgb(hexc))
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    return h, s, l


def _is_brandable(hexc: str) -> bool:
    """Filter near-white/near-black/gray colors."""
    _, s, l = _hsl(hexc)
    return s >= 0.25 and 0.08 <= l <= 0.85


def _dist(a: str, b: str) -> float:
    ra, ga, ba = _rgb(a)
    rb, gb, bb = _rgb(b)
    return ((ra - rb) ** 2 + (ga - gb) ** 2 + (ba - bb) ** 2) ** 0.5


def _darken(hexc: str, pct: float = 0.12) -> str:
    r, g, b = (max(0, int(round(v * (1 - pct)))) for v in _rgb(hexc))
    return f"#{r:02x}{g:02x}{b:02x}"


def _luminance(hexc: str) -> float:
    def chan(v: float) -> float:
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = _rgb(hexc)
    return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b)


def _contrast(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _ink_for(accent: str) -> str:
    """W3C contrast: near-white ink on dark accents, near-black on light."""
    white, ink = "#f7f9fc", "#16181d"
    return white if _contrast(white, accent) >= _contrast(ink, accent) else ink


# ————— HTML/CSS harvesting —————

_ATTR = r"""[^>]*?"""

def _attr(tag: str, name: str) -> str | None:
    m = re.search(rf"""{name}\s*=\s*["']([^"']*)["']""", tag, re.I)
    return m.group(1) if m else None


def _find_tags(html: str, tag: str) -> list[str]:
    return re.findall(rf"<{tag}\b[^>]*>", html, re.I)


CONTEXT_KEYWORDS = ("header", "nav", "button", "btn", "hero", "brand", "primary", "accent", "link")


def _harvest_css_colors(css: str, counts: dict[str, int], contexts: dict[str, set]) -> None:
    for m in re.finditer(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]{1,40}\)", css):
        hexc = _norm_hex(m.group(0))
        if not hexc:
            continue
        counts[hexc] = counts.get(hexc, 0) + 1
        # cheap context hint: keywords in the preceding ~160 chars (selector/props)
        window = css[max(0, m.start() - 160):m.start()].lower()
        for kw in CONTEXT_KEYWORDS:
            if kw in window:
                contexts.setdefault(hexc, set()).add(kw)


def _harvest_fonts(css: str, fonts: list[str]) -> None:
    generics = {"sans-serif", "serif", "monospace", "system-ui", "inherit", "initial",
                "cursive", "fantasy", "ui-sans-serif", "ui-serif", "ui-monospace", "var",
                "-apple-system", "blinkmacsystemfont", "segoe ui", "arial", "helvetica",
                "helvetica neue", "roboto", "none", "unset"}
    for m in re.finditer(r"font-family\s*:\s*([^;}]+)", css, re.I):
        for fam in m.group(1).split(","):
            fam = fam.strip().strip("'\"").strip()
            if fam and fam.lower() not in generics and not fam.startswith("var(") and len(fam) < 40:
                if fam not in fonts:
                    fonts.append(fam)


def _collapse_colors(counts: dict[str, int], contexts: dict[str, set]) -> list[dict]:
    """Filter to brandable colors, collapse near-duplicates, rank by frequency."""
    ranked = sorted(((c, n) for c, n in counts.items() if _is_brandable(c)),
                    key=lambda x: -x[1])
    out: list[dict] = []
    for c, n in ranked:
        merged = False
        for o in out:
            if _dist(c, o["hex"]) < 32:
                o["count"] += n
                o["hints"] |= contexts.get(c, set())
                merged = True
                break
        if not merged:
            out.append({"hex": c, "count": n, "hints": set(contexts.get(c, set()))})
    out.sort(key=lambda o: -o["count"])
    return out[:12]


_FONT_CDN_HOSTS = ("fonts.googleapis.com", "fonts.bunny.net")


def _normalize_font_url(u: str) -> str:
    """https-normalize a font-CDN stylesheet href; ensure display=swap."""
    p = urllib.parse.urlparse(u)
    if "display=" not in p.query:
        q = (p.query + "&" if p.query else "") + "display=swap"
        p = p._replace(query=q)
    return urllib.parse.urlunparse(p._replace(scheme="https"))


def _pick_icon_link(html: str, base_url: str) -> tuple[str | None, list[str]]:
    """Best <link rel=icon|apple-touch-icon> href, largest declared size wins."""
    best, best_size, warns = None, -1, []
    for tag in _find_tags(html, "link"):
        rel = (_attr(tag, "rel") or "").lower()
        if "icon" not in rel or "mask" in rel:
            continue
        href = _attr(tag, "href")
        if not href or href.startswith("data:"):
            continue
        sizes = _attr(tag, "sizes") or ""
        m = re.match(r"(\d+)", sizes)
        size = int(m.group(1)) if m else (180 if "apple" in rel else 32)
        href_l = href.lower()
        if href_l.endswith(".svg"):
            size += 1000  # prefer svg
        elif href_l.endswith(".ico"):
            size -= 10
        if size > best_size:
            best, best_size = urllib.parse.urljoin(base_url, href), size
    return best, warns


def _icon_from_manifest(manifest: dict, base_url: str) -> str | None:
    best, best_size = None, -1
    for icon in manifest.get("icons") or []:
        src = icon.get("src")
        if not src:
            continue
        m = re.match(r"(\d+)", str(icon.get("sizes") or ""))
        size = int(m.group(1)) if m else 0
        if size <= 512 and size > best_size:
            best, best_size = urllib.parse.urljoin(base_url, src), size
    return best


_MIME_OK = {"image/png", "image/jpeg", "image/svg+xml", "image/x-icon",
            "image/vnd.microsoft.icon", "image/webp", "image/gif"}


def _fetch_icon_data_uri(url: str, warnings: list[str]) -> str | None:
    res = _fetch(url, ICON_CAP, timeout=8.0)
    if isinstance(res, str):
        warnings.append(f"icon fetch failed ({res})")
        return None
    body, ctype, _ = res
    mime = ctype.split(";")[0].strip().lower()
    if mime not in _MIME_OK:
        # sniff magic bytes
        if body[:8] == b"\x89PNG\r\n\x1a\n":
            mime = "image/png"
        elif body[:3] == b"\xff\xd8\xff":
            mime = "image/jpeg"
        elif body[:4] == b"\x00\x00\x01\x00":
            mime = "image/x-icon"
        elif b"<svg" in body[:512].lower():
            mime = "image/svg+xml"
        else:
            warnings.append(f"icon has unsupported type '{mime or 'unknown'}'")
            return None
    return f"data:{mime};base64,{base64.b64encode(body).decode()}"


# ————— LLM pick + deterministic fallback —————

def _llm_pick(evidence: dict, colors: list[dict]) -> dict | None:
    palette = [{"hex": c["hex"], "count": c["count"], "usedIn": sorted(c["hints"])[:4]}
               for c in colors]
    prompt = (
        "You are choosing a brand theme for a company from evidence scraped off its homepage.\n"
        f"Site title: {evidence.get('title') or 'unknown'}\n"
        f"Declared theme-color: {evidence.get('themeColor') or 'none'}\n"
        f"Colors found in its CSS (hex, frequency, where used): {json.dumps(palette)}\n"
        f"Fonts found: {json.dumps(evidence.get('fonts') or [])}\n\n"
        "Pick the brand accent color (the color the company clearly uses for its identity — "
        "buttons, links, header — NOT a random decorative color), a slightly darker variant "
        "of it for accentDeep, and accentInk as a text color with strong contrast on the "
        "accent (near-white like #f4f8ff for dark accents, near-black like #16181d for light "
        "accents). If the palette clearly contains a distinct green, blue, or gold brand "
        "color, map those too, else null. Pick displayFont from the fonts list (the primary "
        "brand/heading font), or null if the list is empty.\n\n"
        "Also pick bodyFont — the family used for running text (may equal displayFont), "
        "verbatim from the fonts list, or null.\n"
        'Return JSON exactly: {"accent": "#rrggbb", "accentDeep": "#rrggbb", '
        '"accentInk": "#rrggbb", "green": "#rrggbb"|null, "blue": "#rrggbb"|null, '
        '"gold": "#rrggbb"|null, "displayFont": "Name"|null, "bodyFont": "Name"|null}'
    )
    out = llm.generate_json(prompt, system="You are a meticulous brand designer. JSON only.")
    if not isinstance(out, dict):
        return None
    picked: dict = {}
    for k in ("accent", "accentDeep", "accentInk", "green", "blue", "gold"):
        v = out.get(k)
        if isinstance(v, str) and HEX_RE.match(v.strip()):
            picked[k] = v.strip().lower()
        else:
            picked[k] = None
    if not picked["accent"]:
        return None
    if not picked["accentDeep"]:
        picked["accentDeep"] = _darken(picked["accent"])
    # never trust the LLM on contrast — recompute if its ink fails WCAG AA-large
    if not picked["accentInk"] or _contrast(picked["accentInk"], picked["accent"]) < 3.0:
        picked["accentInk"] = _ink_for(picked["accent"])
    allowed_fonts = evidence.get("fonts") or []
    for k in ("displayFont", "bodyFont"):
        v = out.get(k)
        picked[k] = v if isinstance(v, str) and v in allowed_fonts else None
    return picked


def _deterministic_pick(evidence: dict, colors: list[dict]) -> dict:
    theme = evidence.get("themeColor")
    accent = theme if theme and _is_brandable(theme) else (colors[0]["hex"] if colors else theme)
    picked = {"accent": accent, "accentDeep": None, "accentInk": None,
              "green": None, "blue": None, "gold": None,
              "displayFont": (evidence.get("fonts") or [None])[0],
              "bodyFont": (evidence.get("fonts") or [None, None])[1] if len(evidence.get("fonts") or []) > 1 else None}
    if accent:
        picked["accentDeep"] = _darken(accent)
        picked["accentInk"] = _ink_for(accent)
    return picked


# ————— main entry —————

def import_brand(url: str) -> dict:
    """Harvest brand identity from a homepage URL. Never raises."""
    warnings: list[str] = []
    url = url.strip()
    if url and "://" not in url:
        url = "https://" + url
    err = _check_url(url) if url else "empty URL"
    if err:
        return {"error": err, "warnings": warnings}

    res = _fetch(url, PAGE_CAP)
    if isinstance(res, str):
        return {"error": res, "warnings": warnings}
    body, ctype, final_url = res
    charset = "utf-8"
    m = re.search(r"charset=([\w-]+)", ctype)
    if m:
        charset = m.group(1)
    html = body.decode(charset, errors="replace")

    evidence: dict = {"themeColor": None, "cssColors": [], "iconUrl": None,
                      "fonts": [], "title": None}

    # <title> / og:site_name → logoAlt
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    if m:
        evidence["title"] = re.sub(r"\s+", " ", m.group(1)).strip()[:160]
    logo_alt = None
    for tag in _find_tags(html, "meta"):
        prop = (_attr(tag, "property") or _attr(tag, "name") or "").lower()
        if prop == "og:site_name" and _attr(tag, "content"):
            logo_alt = _attr(tag, "content").strip()
        if prop == "theme-color" and _attr(tag, "content"):
            evidence["themeColor"] = _norm_hex(_attr(tag, "content")) or evidence["themeColor"]
    if not logo_alt and evidence["title"]:
        logo_alt = re.split(r"\s+[—|–·:-]\s+", evidence["title"])[0].strip() or None

    counts: dict[str, int] = {}
    contexts: dict[str, set] = {}
    fonts: list[str] = []

    # inline <style> blocks + style="" attributes
    for block in re.findall(r"<style[^>]*>(.*?)</style>", html, re.I | re.S):
        _harvest_css_colors(block, counts, contexts)
        _harvest_fonts(block, fonts)
    for style in re.findall(r"""style\s*=\s*["']([^"']+)["']""", html, re.I):
        _harvest_css_colors(style, counts, contexts)

    # linked stylesheets (up to 5) + font-CDN (Google/Bunny) family params
    sheet_urls: list[str] = []
    font_url: str | None = None
    saw_font_face = "@font-face" in html.lower()
    for tag in _find_tags(html, "link"):
        rel = (_attr(tag, "rel") or "").lower()
        href = _attr(tag, "href")
        if not href:
            continue
        full = urllib.parse.urljoin(final_url, href)
        if "stylesheet" in rel:
            host = (urllib.parse.urlparse(full).hostname or "").lower()
            if host in _FONT_CDN_HOSTS:
                for fam in re.findall(r"family=([^&]+)", full):
                    name = urllib.parse.unquote_plus(fam).split(":")[0].strip()
                    if name and name not in fonts:
                        fonts.append(name)
                if not font_url:
                    font_url = _normalize_font_url(full)
            elif len(sheet_urls) < 5:
                sheet_urls.append(full)
    for su in sheet_urls:
        cres = _fetch(su, CSS_CAP, timeout=8.0)
        if isinstance(cres, str):
            warnings.append(f"stylesheet skipped ({cres})")
            continue
        css = cres[0].decode("utf-8", errors="replace")
        saw_font_face = saw_font_face or "@font-face" in css.lower()
        _harvest_css_colors(css, counts, contexts)
        _harvest_fonts(css, fonts)

    # manifest.json: theme_color + icons
    manifest_icon = None
    mtag = next((t for t in _find_tags(html, "link")
                 if "manifest" in (_attr(t, "rel") or "").lower() and _attr(t, "href")), None)
    if mtag:
        mres = _fetch(urllib.parse.urljoin(final_url, _attr(mtag, "href")), 100 * 1024, timeout=8.0)
        if isinstance(mres, str):
            warnings.append(f"manifest fetch failed ({mres})")
        else:
            try:
                manifest = json.loads(mres[0])
                tc = _norm_hex(str(manifest.get("theme_color") or ""))
                if tc and not evidence["themeColor"]:
                    evidence["themeColor"] = tc
                manifest_icon = _icon_from_manifest(manifest, mres[2])
                if not logo_alt and manifest.get("name"):
                    logo_alt = str(manifest["name"]).strip()
            except (json.JSONDecodeError, AttributeError, TypeError):
                warnings.append("manifest unparseable")
    else:
        warnings.append("no manifest found")

    # icon → data URI (manifest icon wins, then <link rel=icon>, then /favicon.ico)
    icon_link, _ = _pick_icon_link(html, final_url)
    logo = None
    for cand in (manifest_icon, icon_link,
                 urllib.parse.urljoin(final_url, "/favicon.ico")):
        if not cand:
            continue
        logo = _fetch_icon_data_uri(cand, warnings)
        if logo:
            evidence["iconUrl"] = cand
            break
    if not logo:
        warnings.append("no usable icon/logo found")

    colors = _collapse_colors(counts, contexts)
    evidence["cssColors"] = [[c["hex"], c["count"]] for c in colors]
    evidence["fonts"] = fonts[:10]
    if not colors and not evidence["themeColor"]:
        warnings.append("no saturated colors found in page CSS")

    picked = _llm_pick(evidence, colors)
    if picked:
        warnings.append("palette picked by LLM")
    else:
        warnings.append("LLM unavailable or invalid — deterministic fallback")
        picked = _deterministic_pick(evidence, colors)

    if not font_url and picked.get("displayFont") and saw_font_face:
        warnings.append("brand font is self-hosted — not importable")

    return {
        "accent": picked.get("accent"),
        "accentDeep": picked.get("accentDeep"),
        "accentInk": picked.get("accentInk"),
        "green": picked.get("green"),
        "blue": picked.get("blue"),
        "gold": picked.get("gold"),
        "logo": logo,
        "logoAlt": logo_alt,
        "displayFont": picked.get("displayFont"),
        "bodyFont": picked.get("bodyFont"),
        "fontUrl": font_url,
        "evidence": evidence,
        "warnings": warnings,
    }
