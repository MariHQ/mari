from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from sitefiles import PublishedSiteFiles


def _scope() -> dict:
    return {
        "type": "http", "method": "GET", "path": "/sites", "headers": [],
        "query_string": b"", "scheme": "http", "server": ("test", 80),
        "client": ("test", 1), "root_path": "",
    }


class PublishedSiteFilesTests(unittest.TestCase):
    def test_only_explicit_site_directories_are_servable(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "audit" / "private").mkdir(parents=True)
            (root / "audit" / "private" / "secret.txt").write_text("secret")
            (root / "_work_site_1").mkdir()
            (root / "_work_site_1" / "source.md").write_text("private")
            files = PublishedSiteFiles(
                directory=raw, lookup=lambda _: {"status": "live"}, authenticated=lambda *_: False,
            )
            for path in ("audit/private/secret.txt", "_work_site_1/source.md", "site_1/../audit/private/secret.txt"):
                response = asyncio.run(files.get_response(path, _scope()))
                self.assertEqual(response.status_code, 404, path)

    def test_live_is_public_but_draft_requires_authentication(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "site_1").mkdir()
            (root / "site_1" / "index.html").write_text("published")
            status = {1: {"status": "draft"}}
            files = PublishedSiteFiles(
                directory=raw, lookup=status.get, authenticated=lambda scope, _: bool(scope.get("user")),
            )
            self.assertEqual(asyncio.run(files.get_response("site_1/", _scope())).status_code, 404)
            authed = _scope()
            authed["user"] = True
            self.assertEqual(asyncio.run(files.get_response("site_1/index.html", authed)).status_code, 200)
            status[1] = {"status": "live"}
            self.assertEqual(asyncio.run(files.get_response("site_1/index.html", _scope())).status_code, 200)


if __name__ == "__main__":
    unittest.main()
