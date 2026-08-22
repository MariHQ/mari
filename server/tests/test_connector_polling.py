from __future__ import annotations

import unittest
from unittest.mock import patch

from mari_server.persistence.postgres import connector_sync as connect_sync
from mari_server.sources import routes as connectors_api
from mari_components.connectors import CONNECTOR_CATALOG
from mari_components.types import KnowledgeDocument


class ConnectorContractTests(unittest.TestCase):
    def test_catalog_hides_upload_and_website(self) -> None:
        with patch.object(connectors_api, "_connected_map", return_value={}):
            keys = [item["key"] for item in connectors_api.catalog()]
        self.assertIn("github", keys)
        self.assertNotIn("upload", keys)
        self.assertNotIn("website", keys)
        self.assertEqual(keys[:4], ["github", "slack", "gdrive", "confluence"])

    def test_requested_poll_connectors_are_discoverable(self) -> None:
        for key in ("confluence", "slack", "gdrive"):
            definition = CONNECTOR_CATALOG[key]
            self.assertTrue(callable(definition.validate))
            self.assertTrue(callable(definition.poll))

    def test_connector_config_masks_credentials_and_drops_hash_checkpoint_map(self) -> None:
        cfg = {"provider_key": "gdrive", "access_token": "ya29.secret", "folder_id": "abc",
               "item_hashes": {"doc": "hash"}, "cursor": "2026-08-19"}
        safe = connect_sync.masked_config("gdrive:abc", cfg)
        self.assertEqual(safe["access_token"], connect_sync.MASK)
        self.assertEqual(safe["folder_id"], "abc")
        self.assertNotIn("item_hashes", safe)

    def test_document_author_reads_the_connector_supplied_person(self) -> None:
        document = KnowledgeDocument("123", "Title", "Body", metadata={"author": "Ana Ruiz"})
        self.assertEqual(connect_sync.document_author(document), "Ana Ruiz")

    def test_document_author_is_empty_rather_than_the_source_name(self) -> None:
        # No connector fields expose a real person here; the source's own
        # display name (e.g. "Confluence", "Jira") must never leak in as
        # the document's owner.
        document = KnowledgeDocument("123", "Title", "Body", metadata={"status": "Backlog"})
        self.assertEqual(connect_sync.document_author(document), "")

if __name__ == "__main__":
    unittest.main()
