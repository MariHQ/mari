"""Mari — the one SSRF-guarded outbound HTTP client.

Every request this server makes to a host a *user* named — connector site URLs,
Zendesk subdomains, brand-import homepages, crawled websites — goes through
`fetch()` here. Nothing else in the codebase should call `urllib.request.urlopen`
on a user-supplied URL.

The guard (extracted from duplicated connector HTTP clients, which had two
near-identical copies of it):

* **Scheme allowlist** — http/https only, so `file://`, `gopher://`, `ftp://`
  and friends cannot be reached at all.
* **Address check** — the hostname is resolved and EVERY returned address is
  checked against private / loopback / link-local / reserved / multicast /
  unspecified ranges. That covers cloud instance metadata (169.254.169.254),
  localhost, and RFC1918.
* **Every redirect hop re-validated** — redirects are followed manually, one
  hop at a time, and each new URL runs the full check. A public host cannot
  302 the server into the metadata service.
* **DNS-rebinding guard** — the check above validates one resolution, but the
  socket re-resolves. So the ACTUAL connected peer (`getpeername`) is checked
  after connect and before any request bytes are sent.
* **Credentials are not carried across origins** — on a redirect to a different
  scheme/host/port, `Authorization`/`Cookie`/`Proxy-Authorization` are dropped.
  A connector sends its API token to the site URL the user configured; a
  redirect must not hand that token to a third host.

`fetch()` raises `Blocked` when the guard refuses (the message says which rule
and why — it is meant to be shown to the user) and `NetworkError` when the
transport fails. Any HTTP status, including 4xx/5xx, comes back as a `Response`
so callers can read the vendor's own error body.

Dev override: `allow_loopback=True` permits loopback ONLY (so a local dev site
can be crawled). Link-local — cloud metadata — and private ranges are ALWAYS
refused, override or not.
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import NamedTuple

UA = "Mozilla/5.0 (compatible; MariCloud/1.0)"
MAX_REDIRECTS = 5
DEFAULT_TIMEOUT = 30.0
DEFAULT_CAP = 25 * 1024 * 1024  # bytes read per response

REDIRECT_CODES = (301, 302, 303, 307, 308)
_CREDENTIAL_HEADERS = ("authorization", "cookie", "proxy-authorization")


class Blocked(ValueError):
    """The URL must not be fetched. The message is safe to show a user."""


class NetworkError(OSError):
    """The request could not be completed (DNS, TLS, timeout, reset)."""


class Response(NamedTuple):
    status: int
    body: bytes
    headers: dict
    url: str            # final URL after redirects
    truncated: bool     # True if the body hit `cap`


# ————— address / URL checks —————

def ip_error(ip_str: str, *, allow_loopback: bool = False) -> str | None:
    """Error string if this IP must not be contacted, else None."""
    try:
        ip = ipaddress.ip_address(ip_str.split("%")[0])  # strip IPv6 scope id
    except ValueError:
        return f"unparseable address {ip_str!r}"
    if ip.is_loopback and allow_loopback:
        return None  # dev override — loopback ONLY; everything below still applies
    if (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
        return "private/loopback address — refused"
    return None


def check_url(url: str, *, allow_loopback: bool = False) -> str | None:
    """Return an error string if the URL must not be fetched, else None."""
    try:
        p = urllib.parse.urlparse(url)
    except ValueError:
        return "unparseable URL"
    if p.scheme not in ("http", "https"):
        return f"scheme '{p.scheme}' not allowed (http/https only)"
    try:
        host = p.hostname
        port = p.port or (443 if p.scheme == "https" else 80)
    except ValueError:
        return "unparseable URL (bad port)"
    if not host:
        return "no hostname"
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return f"cannot resolve host '{host}'"
    for info in infos:
        if ip_error(info[4][0], allow_loopback=allow_loopback):
            return f"host '{host}' resolves to a private/loopback address — refused"
    return None


def require_ok(url: str, *, allow_loopback: bool = False) -> None:
    """check_url, raising Blocked instead of returning a string."""
    err = check_url(url, allow_loopback=allow_loopback)
    if err:
        raise Blocked(err)


# ————— DNS-rebinding guard —————
#
# check_url validates one resolution, but the connection re-resolves — so
# re-check the ACTUAL connected peer (getpeername) before any request bytes are
# sent. Certificate validation is untouched (the standard connect path does
# resolution + TLS with server_hostname as usual).

def _verify_peer(sock: socket.socket, allow_loopback: bool) -> None:
    peer = sock.getpeername()[0]
    err = ip_error(peer, allow_loopback=allow_loopback)
    if err:
        sock.close()
        raise OSError(f"connected peer {peer} is a {err}")


def _pinned_handlers(allow_loopback: bool) -> list:
    class _PinnedHTTPConnection(http.client.HTTPConnection):
        def connect(self):  # noqa: D102
            super().connect()
            _verify_peer(self.sock, allow_loopback)

    class _PinnedHTTPSConnection(http.client.HTTPSConnection):
        def connect(self):  # noqa: D102
            super().connect()
            _verify_peer(self.sock, allow_loopback)

    class _PinnedHTTPHandler(urllib.request.HTTPHandler):
        def http_open(self, req):  # noqa: D102
            return self.do_open(_PinnedHTTPConnection, req)

    class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self, req):  # noqa: D102
            return self.do_open(_PinnedHTTPSConnection, req, context=self._context)

    return [_NoRedirect(), _PinnedHTTPHandler(), _PinnedHTTPSHandler()]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Redirects are followed by hand in fetch(), so every hop is re-checked."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


# ————— the fetch —————

def _same_origin(a: str, b: str) -> bool:
    pa, pb = urllib.parse.urlparse(a), urllib.parse.urlparse(b)
    try:
        return (pa.scheme, pa.hostname, pa.port or (443 if pa.scheme == "https" else 80)) == \
               (pb.scheme, pb.hostname, pb.port or (443 if pb.scheme == "https" else 80))
    except ValueError:
        return False


def _strip_credentials(headers: dict) -> dict:
    return {k: v for k, v in headers.items() if k.lower() not in _CREDENTIAL_HEADERS}


def fetch(url: str, *, method: str = "GET", headers: dict | None = None,
          data: bytes | None = None, timeout: float = DEFAULT_TIMEOUT,
          cap: int = DEFAULT_CAP, allow_loopback: bool = False,
          max_redirects: int = MAX_REDIRECTS, user_agent: str = UA) -> Response:
    """SSRF-guarded HTTP request with manual, re-checked redirects.

    Returns a Response for every HTTP status (4xx/5xx included — callers want
    the vendor's error body). Raises Blocked if the guard refuses the URL or any
    redirect target, and NetworkError if the transport fails.
    """
    hdrs = {"User-Agent": user_agent}
    hdrs.update(headers or {})
    body = data
    verb = method.upper()

    for _ in range(max_redirects + 1):
        require_ok(url, allow_loopback=allow_loopback)
        req = urllib.request.Request(url, data=body, headers=hdrs, method=verb)
        opener = urllib.request.build_opener(*_pinned_handlers(allow_loopback))
        try:
            with opener.open(req, timeout=timeout) as resp:
                status, rheaders = resp.status, dict(resp.headers)
                if status in REDIRECT_CODES:
                    loc = rheaders_get(rheaders, "Location")
                    if not loc:
                        raise NetworkError(f"HTTP {status} redirect without a Location header")
                    url, hdrs, verb, body = _next_hop(url, loc, hdrs, verb, body, status)
                    continue
                raw = resp.read(cap + 1)
                truncated = len(raw) > cap
                return Response(status, raw[:cap] if truncated else raw,
                                rheaders, url, truncated)
        except urllib.error.HTTPError as e:
            rheaders = dict(e.headers or {})
            if e.code in REDIRECT_CODES and rheaders_get(rheaders, "Location"):
                url, hdrs, verb, body = _next_hop(
                    url, rheaders_get(rheaders, "Location"), hdrs, verb, body, e.code)
                continue
            raw = e.read(cap + 1)
            truncated = len(raw) > cap
            return Response(e.code, raw[:cap] if truncated else raw,
                            rheaders, url, truncated)
        except (Blocked, NetworkError):
            raise
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError, ValueError) as e:
            raise NetworkError(str(getattr(e, "reason", e)) or e.__class__.__name__) from None
    raise NetworkError(f"too many redirects (over {max_redirects})")


def _next_hop(url: str, location: str, hdrs: dict, verb: str,
              body: bytes | None, status: int) -> tuple[str, dict, str, bytes | None]:
    """Resolve one redirect: new URL, and the headers/method/body to carry."""
    new_url = urllib.parse.urljoin(url, location)
    if not _same_origin(url, new_url):
        hdrs = _strip_credentials(hdrs)  # never hand a token to another origin
    if status in (301, 302, 303) and verb not in ("GET", "HEAD"):
        verb, body = "GET", None
        hdrs = {k: v for k, v in hdrs.items()
                if k.lower() not in ("content-type", "content-length")}
    return new_url, hdrs, verb, body


def rheaders_get(headers: dict, name: str) -> str | None:
    """Case-insensitive header lookup (servers vary: 'ETag' vs 'etag')."""
    low = name.lower()
    for k, v in headers.items():
        if k.lower() == low:
            return v
    return None
