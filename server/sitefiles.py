"""Narrow static serving for locally built documentation sites.

The build root also contains compiler work directories and may coexist with
private repository audit checkouts.  A raw ``StaticFiles`` mount therefore
turns implementation artifacts into public HTTP content.  This wrapper only
serves the explicit ``site_<integer>`` publication namespace and only exposes
draft previews to an authenticated caller.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from starlette.responses import Response
from starlette.staticfiles import StaticFiles


_SITE_PATH = re.compile(r"^site_([1-9][0-9]*)(?:/|$)")


class PublishedSiteFiles(StaticFiles):
    def __init__(
        self,
        *,
        directory: str,
        lookup: Callable[[int], dict[str, Any] | None],
        authenticated: Callable[[dict[str, Any], dict[str, Any]], bool],
    ) -> None:
        super().__init__(directory=directory, html=True, follow_symlink=False)
        self._lookup = lookup
        self._authenticated = authenticated

    async def get_response(self, path: str, scope: dict[str, Any]) -> Response:
        segments = path.replace("\\", "/").split("/")
        if any(segment in (".", "..") for segment in segments):
            return self.not_found_response()
        match = _SITE_PATH.match(path)
        if not match:
            return self.not_found_response()
        site = self._lookup(int(match.group(1)))
        if not site:
            return self.not_found_response()
        if str(site.get("status") or "") != "live" and not self._authenticated(scope, site):
            # Do not reveal whether a draft exists to an anonymous caller.
            return self.not_found_response()
        return await super().get_response(path, scope)

    @staticmethod
    def not_found_response() -> Response:
        return Response("Not Found", status_code=404)
