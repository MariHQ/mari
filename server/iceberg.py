"""Apache Iceberg warehouse for Mari's durable state and analytical snapshots.

Request-time SQL is materialized in embedded DuckDB, while every committed
transaction is first appended to the Iceberg mutation journal. Canonical table
snapshots are periodically overwritten as typed Iceberg tables for analytical
queries, time travel, compaction, and cross-instance bootstrap. Embeddings are
excluded: ``retrieval.py`` owns those rebuildable filesystem/S3 artifacts.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import threading
import typing as t

import pyarrow as pa
from pyiceberg.catalog import Catalog, load_catalog
from pyiceberg.catalog.sql import SqlCatalog

NAMESPACE = "mari"
JOURNAL = f"{NAMESPACE}.mutation_journal"

JOURNAL_SCHEMA = pa.schema([
    pa.field("transaction_id", pa.string(), nullable=False),
    pa.field("ordinal", pa.int64(), nullable=False),
    pa.field("sql", pa.string(), nullable=False),
    pa.field("args_json", pa.string(), nullable=False),
    pa.field("committed_at", pa.timestamp("us", tz="UTC"), nullable=False),
])


def _default_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent.parent / ".mari" / "iceberg"


def _json_default(value: t.Any) -> t.Any:
    if isinstance(value, (dt.datetime, dt.date)):
        return {"__mari_type__": "datetime" if isinstance(value, dt.datetime) else "date",
                "value": value.isoformat()}
    if isinstance(value, bytes):
        return {"__mari_type__": "bytes", "value": value.hex()}
    raise TypeError(f"cannot journal {type(value).__name__}")


def journal_args(args: t.Any) -> str:
    return json.dumps(args, default=_json_default, separators=(",", ":"), sort_keys=True)


def restore_args(value: str) -> t.Any:
    def hook(row: dict) -> t.Any:
        marker = row.get("__mari_type__")
        if marker == "date":
            return dt.date.fromisoformat(row["value"])
        if marker == "datetime":
            return dt.datetime.fromisoformat(row["value"])
        if marker == "bytes":
            return bytes.fromhex(row["value"])
        return row
    return json.loads(value, object_hook=hook)


class IcebergWarehouse:
    """Small catalog facade; all PyIceberg writes are serialized per process."""

    def __init__(self, warehouse: str | None = None, catalog: Catalog | None = None):
        configured = warehouse or os.environ.get("MARI_ICEBERG_WAREHOUSE")
        self.warehouse = configured or str(_default_root() / "warehouse")
        self._lock = threading.RLock()
        if catalog is not None:
            self.catalog = catalog
        elif os.environ.get("MARI_ICEBERG_CATALOG"):
            # REST/Glue/Hive catalogs use the ordinary PyIceberg environment
            # configuration. The named catalog can point at S3 and is the
            # production multi-writer path.
            self.catalog = load_catalog(os.environ["MARI_ICEBERG_CATALOG"])
        else:
            root = pathlib.Path(configured or _default_root()).expanduser().resolve()
            root.mkdir(parents=True, exist_ok=True)
            warehouse_path = root / "warehouse" if configured is None or not str(configured).startswith("s3://") else configured
            warehouse_uri = str(warehouse_path)
            if not warehouse_uri.startswith(("s3://", "file://")):
                pathlib.Path(warehouse_uri).mkdir(parents=True, exist_ok=True)
                warehouse_uri = pathlib.Path(warehouse_uri).resolve().as_uri()
            self.warehouse = warehouse_uri
            catalog_uri = os.environ.get("MARI_ICEBERG_CATALOG_URI", "").strip()
            if not catalog_uri:
                database_uri = os.environ.get("MARI_DB", "postgresql://localhost/mari_cloud")
                catalog_uri = database_uri.replace("postgresql://", "postgresql+psycopg://", 1)
            self.catalog = SqlCatalog("mari", uri=catalog_uri, warehouse=warehouse_uri)
        self.catalog.create_namespace_if_not_exists(NAMESPACE)
        self._ensure_journal()

    def _ensure_journal(self):
        try:
            return self.catalog.load_table(JOURNAL)
        except Exception:  # table-not-found types vary between catalog plugins
            return self.catalog.create_table(JOURNAL, schema=JOURNAL_SCHEMA)

    def append_transaction(self, transaction_id: str,
                           statements: list[tuple[str, t.Any]],
                           committed_at: dt.datetime | None = None) -> None:
        if not statements:
            return
        when = committed_at or dt.datetime.now(dt.timezone.utc)
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt.timezone.utc)
        rows = [{
            "transaction_id": transaction_id,
            "ordinal": ordinal,
            "sql": sql,
            "args_json": journal_args(args),
            "committed_at": when,
        } for ordinal, (sql, args) in enumerate(statements)]
        with self._lock:
            table = self.catalog.load_table(JOURNAL)
            table.append(pa.Table.from_pylist(rows, schema=JOURNAL_SCHEMA),
                         snapshot_properties={"mari.transaction-id": transaction_id})

    def transactions(self, after: set[str] | None = None) -> list[dict]:
        rows = self.catalog.load_table(JOURNAL).scan().to_arrow().to_pylist()
        if after:
            rows = [row for row in rows if row["transaction_id"] not in after]
        rows.sort(key=lambda row: (row["committed_at"], row["transaction_id"], row["ordinal"]))
        return rows

    def snapshot(self, name: str, arrow: pa.Table, *, transaction_id: str = "") -> None:
        """Replace one typed analytical table, producing an Iceberg snapshot."""
        identifier = f"{NAMESPACE}.{name}"
        with self._lock:
            try:
                table = self.catalog.load_table(identifier)
                # Request materializations normalize types before this call;
                # an incompatible schema must fail loudly instead of silently
                # reinterpreting old snapshots.
                table.overwrite(arrow, snapshot_properties={
                    "mari.transaction-id": transaction_id,
                    "mari.snapshot-kind": "materialized-table",
                })
            except Exception as error:
                try:
                    table = self.catalog.create_table(identifier, schema=arrow.schema)
                except Exception:
                    raise error
                if len(arrow):
                    table.append(arrow, snapshot_properties={
                        "mari.transaction-id": transaction_id,
                        "mari.snapshot-kind": "materialized-table",
                    })

    def read_snapshot(self, name: str) -> pa.Table:
        return self.catalog.load_table(f"{NAMESPACE}.{name}").scan().to_arrow()

    def table_names(self) -> list[str]:
        return sorted(identifier[-1] for identifier in self.catalog.list_tables(NAMESPACE))


_WAREHOUSE: IcebergWarehouse | None = None
_WAREHOUSE_LOCK = threading.Lock()


def warehouse() -> IcebergWarehouse:
    global _WAREHOUSE
    with _WAREHOUSE_LOCK:
        if _WAREHOUSE is None:
            _WAREHOUSE = IcebergWarehouse()
        return _WAREHOUSE
