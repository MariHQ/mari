from __future__ import annotations

import datetime as dt
import tempfile
import unittest

import pyarrow as pa

from iceberg import IcebergWarehouse, restore_args


class IcebergWarehouseTests(unittest.TestCase):
    def test_journal_round_trips_order_types_and_transaction_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            store = IcebergWarehouse(directory)
            store.append_transaction("tx-2", [
                ("UPDATE tasks SET done = ? WHERE id = ?", (True, 7)),
                ("INSERT INTO events VALUES (?, ?)", (dt.date(2026, 8, 19), b"ok")),
            ], dt.datetime(2026, 8, 19, 12, tzinfo=dt.timezone.utc))
            rows = store.transactions()
            self.assertEqual([row["ordinal"] for row in rows], [0, 1])
            self.assertEqual(restore_args(rows[0]["args_json"]), [True, 7])
            self.assertEqual(restore_args(rows[1]["args_json"]), [dt.date(2026, 8, 19), b"ok"])
            table = store.catalog.load_table("mari.mutation_journal")
            self.assertEqual(table.current_snapshot().summary.get("mari.transaction-id"), "tx-2")

    def test_typed_snapshot_overwrites_and_preserves_time_travel_history(self):
        with tempfile.TemporaryDirectory() as directory:
            store = IcebergWarehouse(directory)
            schema = pa.schema([pa.field("id", pa.int64(), nullable=False), pa.field("title", pa.string())])
            store.snapshot("documents", pa.Table.from_pylist([{"id": 1, "title": "Old"}], schema=schema),
                           transaction_id="tx-1")
            table = store.catalog.load_table("mari.documents")
            first_snapshot = table.current_snapshot().snapshot_id
            store.snapshot("documents", pa.Table.from_pylist([{"id": 1, "title": "New"}], schema=schema),
                           transaction_id="tx-2")
            self.assertEqual(store.read_snapshot("documents").to_pylist(), [{"id": 1, "title": "New"}])
            table = store.catalog.load_table("mari.documents")
            old = table.scan(snapshot_id=first_snapshot).to_arrow().to_pylist()
            self.assertEqual(old, [{"id": 1, "title": "Old"}])
            self.assertIn("documents", store.table_names())


if __name__ == "__main__":
    unittest.main()
