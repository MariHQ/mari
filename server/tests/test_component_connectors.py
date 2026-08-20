from __future__ import annotations

import unittest
from unittest.mock import patch

import component_connectors
import connectors
from nethttp import Response


class ComponentConnectorAdapterTests(unittest.TestCase):
    def test_registry_uses_components_for_supported_providers_only(self):
        connectors.REGISTRY.refresh()
        self.assertEqual(connectors.REGISTRY["confluence"]["list_items"].__module__, "component_connectors")
        self.assertEqual(connectors.REGISTRY["website"]["list_items"].__module__, "connectors.website")

    def test_confluence_component_result_maps_to_legacy_worker_contract(self):
        responses = [
            Response(200, b'{"results":[]}', {}, "https://example.atlassian.net", False),
            Response(200, b'{"size":0,"results":[]}', {}, "https://example.atlassian.net", False),
        ]
        with patch.object(component_connectors._net, "fetch", side_effect=responses):
            validate, poll = component_connectors.functions("confluence", lambda _cfg: "legacy", lambda *_: None)
            cfg = {"site_url": "https://example.atlassian.net", "email": "me@example.com", "api_token": "token"}
            self.assertIsNone(validate(cfg))
            result = poll(cfg, None)
        self.assertTrue(result.snapshot_complete)
        self.assertEqual(result.items, [])

    def test_incomplete_checkpoint_preserves_original_cursor(self):
        class Page:
            upserts = ()
            tombstones = ()
            next_cursor = "too-new"
            next_checkpoint = "page:2"
            snapshot_complete = False

        result = component_connectors._collect(iter([Page()]), "old")
        self.assertEqual(result.cursor, "old")
        self.assertTrue(result.checkpoint.startswith("mari-components:"))
        cursor, checkpoint = component_connectors._cursor(result.checkpoint)
        self.assertEqual((cursor, checkpoint), ("old", "page:2"))


if __name__ == "__main__":
    unittest.main()
