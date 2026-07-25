"""Path-resilient handle on the shared SSRF guard (server/nethttp.py).

Connector modules are meant to be importable on their own, and the server's
modules are flat top-level imports (`import auth`, `import connectors`). When a
connector is imported without the server directory on sys.path — a standalone
`import connectors.jira` from the repo root, say — plain `import nethttp` fails.
This adds the server directory once and re-exports the guard, so every provider
module can just `from ._net import fetch, Blocked, NetworkError`.

Underscore-prefixed, so the provider registry skips it.
"""

from __future__ import annotations

try:  # normal case: the server directory is already the import root
    import nethttp
except ImportError:  # pragma: no cover — standalone import
    import pathlib
    import sys

    _SERVER_DIR = str(pathlib.Path(__file__).resolve().parents[1])
    if _SERVER_DIR not in sys.path:
        sys.path.append(_SERVER_DIR)
    import nethttp

Blocked = nethttp.Blocked
NetworkError = nethttp.NetworkError
Response = nethttp.Response
check_url = nethttp.check_url
fetch = nethttp.fetch
header = nethttp.rheaders_get

__all__ = ["Blocked", "NetworkError", "Response", "check_url", "fetch", "header", "nethttp"]
