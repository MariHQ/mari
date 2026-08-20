"""Minimal Iceberg catalog adapter for canonical document tables."""

from __future__ import annotations

import os
import pathlib
import threading

from pyiceberg.catalog import Catalog, load_catalog
from pyiceberg.catalog.sql import SqlCatalog


NAMESPACE = "mari"


def _default_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[3] / "var" / "mari" / "iceberg"


class IcebergWarehouse:
    """Resolve one catalog; document stores own their tables and schemas."""

    def __init__(self, warehouse: str | None = None, catalog: Catalog | None = None):
        configured = warehouse or os.environ.get("MARI_ICEBERG_WAREHOUSE")
        self._lock = threading.RLock()
        if catalog is not None:
            self.catalog = catalog
            self.warehouse = configured or ""
        elif os.environ.get("MARI_ICEBERG_CATALOG"):
            self.catalog = load_catalog(os.environ["MARI_ICEBERG_CATALOG"])
            self.warehouse = configured or ""
        else:
            root = pathlib.Path(configured or _default_root()).expanduser().resolve()
            root.mkdir(parents=True, exist_ok=True)
            warehouse_path = root / "warehouse"
            warehouse_path.mkdir(parents=True, exist_ok=True)
            self.warehouse = warehouse_path.as_uri()
            catalog_uri = os.environ.get("MARI_ICEBERG_CATALOG_URI", "").strip()
            if not catalog_uri:
                from mari_server.infrastructure.postgres import database_url
                database_uri = database_url()
                catalog_uri = database_uri.replace("postgresql://", "postgresql+psycopg://", 1)
            self.catalog = SqlCatalog("mari", uri=catalog_uri, warehouse=self.warehouse)
        self.catalog.create_namespace_if_not_exists(NAMESPACE)

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
