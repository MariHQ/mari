from __future__ import annotations

import unittest
from unittest.mock import patch

import connectors
from nethttp import Response
from mari_server.infrastructure import connector_provider as component_connectors


class ComponentConnectorAdapterTests(unittest.TestCase):
    def test_registry_uses_components_for_supported_providers_only(self):
        connectors.REGISTRY.refresh()
        self.assertEqual(
            connectors.REGISTRY["confluence"]["list_items"].__module__,
            "mari_server.infrastructure.connector_provider",
        )
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

    def test_native_pages_are_not_buffered_and_carry_resumable_checkpoint(self):
        class Definition:
            def poll(self, _cfg, request, *, http):
                self.request = request
                yield component_connectors.ComponentPollPage(
                    next_cursor="too-new", next_checkpoint="page:2", snapshot_complete=False,
                )

        definition = Definition()
        with patch.object(component_connectors, "connector_definition", return_value=definition):
            pages = component_connectors.poll_pages("example", {}, "old", full=True)
            page = next(pages)
        self.assertEqual(definition.request.mode.value, "full")
        self.assertEqual(page.next_cursor, "old")
        cursor, checkpoint = component_connectors._cursor(page.next_checkpoint)
        self.assertEqual((cursor, checkpoint), ("old", "page:2"))


if __name__ == "__main__":
    unittest.main()
