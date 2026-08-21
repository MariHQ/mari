from __future__ import annotations

import tempfile
import unittest

from mari_components.documents import DocumentVersion
from mari_server.persistence.iceberg.documents import IcebergDocumentStore
from tests.iceberg_fixture import temporary_warehouse


class IcebergWarehouseTests(unittest.TestCase):
    def test_warehouse_contains_only_canonical_document_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            warehouse = temporary_warehouse(directory)
            store = IcebergDocumentStore(warehouse)
            store.append(DocumentVersion(
                project_id=1,
                source_id="github:1",
                external_id="README.md",
                revision="sha-1",
                title="README",
                body="Canonical content",
            ))

            self.assertEqual(warehouse.table_names(), ["knowledge_versions"])
            rows = warehouse.catalog.load_table("mari.knowledge_versions").scan().to_arrow().to_pylist()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["body"], "Canonical content")


if __name__ == "__main__":
    unittest.main()
