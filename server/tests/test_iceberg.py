from __future__ import annotations

import tempfile
import unittest
from unittest.mock import Mock, patch

from mari_components.documents import DocumentVersion
from mari_server.persistence.iceberg.documents import IcebergDocumentStore
from mari_server.persistence.iceberg.warehouse import IcebergWarehouse
from tests.iceberg_fixture import temporary_warehouse


class IcebergWarehouseTests(unittest.TestCase):
    def test_object_store_uri_is_passed_to_catalog_without_local_path_conversion(self):
        catalog = Mock()
        with (
            patch.dict("os.environ", {}, clear=True),
            patch(
                "mari_server.persistence.iceberg.warehouse.SqlCatalog",
                return_value=catalog,
            ) as sql_catalog,
            patch(
                "mari_server.persistence.postgres.connection.database_url",
                return_value="postgresql://mari:secret@db/mari",
            ),
            patch("pathlib.Path.mkdir") as mkdir,
        ):
            warehouse = IcebergWarehouse("s3://mari-production/iceberg/")

        self.assertEqual(warehouse.warehouse, "s3://mari-production/iceberg")
        sql_catalog.assert_called_once_with(
            "mari",
            uri="postgresql+psycopg://mari:secret@db/mari",
            warehouse="s3://mari-production/iceberg",
        )
        catalog.create_namespace_if_not_exists.assert_called_once_with("mari")
        mkdir.assert_not_called()

    def test_catalog_desync_propagates_instead_of_masquerading_as_a_create(self):
        # A catalog whose rows point at files this process cannot see is
        # desync, not absence. The bare except used to answer it with a
        # create that failed on the catalog's primary key as "already
        # exists" — the wrong error, pointing away from the real one (the
        # four-day container/host split of 2026-09-01).
        catalog = Mock()
        catalog.load_table.side_effect = FileNotFoundError(
            "/Users/somebody/else/warehouse/mari/knowledge_versions/metadata/x.json")
        store = Mock(spec=IcebergWarehouse)
        store.catalog = catalog
        with self.assertRaises(FileNotFoundError):
            IcebergDocumentStore(store)
        catalog.create_table.assert_not_called()
        catalog.create_table_if_not_exists.assert_not_called()

    def test_a_genuinely_missing_table_is_created_race_safely(self):
        from pyiceberg.exceptions import NoSuchTableError

        catalog = Mock()
        table = Mock()
        table.schema.return_value.column_names = ["source_updated_at"]
        catalog.load_table.side_effect = NoSuchTableError("mari.knowledge_versions")
        catalog.create_table_if_not_exists.return_value = table
        store = Mock(spec=IcebergWarehouse)
        store.catalog = catalog
        IcebergDocumentStore(store)
        catalog.create_table_if_not_exists.assert_called_once()

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
