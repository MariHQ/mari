from __future__ import annotations

from pathlib import Path

from pyiceberg.catalog.sql import SqlCatalog

from mari_server.infrastructure.iceberg_warehouse import IcebergWarehouse


def temporary_warehouse(directory: str) -> IcebergWarehouse:
    """Isolated catalog for unit tests; production catalogs use PostgreSQL."""
    warehouse = (Path(directory) / "warehouse").resolve()
    warehouse.mkdir(parents=True, exist_ok=True)
    catalog = SqlCatalog(
        "test", uri="sqlite:///:memory:", warehouse=warehouse.as_uri(),
    )
    return IcebergWarehouse(str(warehouse), catalog=catalog)
