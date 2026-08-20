"""Serialized, checksum-verified PostgreSQL schema migrations.

`init.sql` is the compatibility baseline for installs that predate a migration
ledger.  Once recorded, its checksum is immutable; subsequent changes belong
in `migrations/NNNN_description.sql` so deploys have an auditable history.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import time

import psycopg
from psycopg.rows import dict_row


DB_URL = os.environ.get("MARI_DB", "postgresql://localhost/mari_cloud")
_NAME = re.compile(r"^[0-9]{4}_[a-z0-9_]+\.sql$")
_LOCK_KEY = 6_878_277_758  # stable application-level advisory lock key


@dataclass(frozen=True, slots=True)
class Migration:
    version: str
    path: Path
    sql: str
    checksum: str


def _migration(version: str, path: Path) -> Migration:
    sql = path.read_text(encoding="utf-8")
    return Migration(version, path, sql, hashlib.sha256(sql.encode()).hexdigest())


def discover(root: Path | None = None) -> list[Migration]:
    root = root or Path(__file__).parent
    migrations = [_migration("0001_baseline", root / "init.sql")]
    directory = root / "migrations"
    for path in sorted(directory.glob("*.sql")):
        if not _NAME.fullmatch(path.name):
            raise RuntimeError(f"Invalid migration filename: {path.name}")
        if path.name.startswith("0001_"):
            raise RuntimeError("0001 is reserved for init.sql compatibility baseline")
        migrations.append(_migration(path.stem, path))
    versions = [migration.version for migration in migrations]
    if len(versions) != len(set(versions)):
        raise RuntimeError("Duplicate schema migration version")
    return migrations


def pending(migrations: list[Migration], applied: dict[str, str]) -> list[Migration]:
    known = {migration.version: migration for migration in migrations}
    unknown = sorted(set(applied) - set(known))
    if unknown:
        raise RuntimeError(f"Database has migrations absent from this release: {', '.join(unknown)}")
    for version, checksum in applied.items():
        if known[version].checksum != checksum:
            raise RuntimeError(f"Migration checksum mismatch: {version}")
    return [migration for migration in migrations if migration.version not in applied]


def migrate(db_url: str | None = None) -> list[str]:
    migrations = discover()
    applied_now: list[str] = []
    with psycopg.connect(db_url or DB_URL, row_factory=dict_row) as conn:
        # A transaction-scoped lock is automatically released on commit,
        # rollback, connection loss, or a killed deploy.
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_KEY,))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
              version text PRIMARY KEY,
              checksum text NOT NULL,
              applied_at timestamptz NOT NULL DEFAULT now(),
              execution_ms integer NOT NULL
            )
        """)
        rows = conn.execute("SELECT version, checksum FROM schema_migrations").fetchall()
        applied = {str(row["version"]): str(row["checksum"]) for row in rows}
        for migration in pending(migrations, applied):
            started = time.monotonic()
            conn.execute(migration.sql)
            elapsed_ms = max(0, round((time.monotonic() - started) * 1000))
            conn.execute(
                "INSERT INTO schema_migrations (version, checksum, execution_ms) VALUES (%s, %s, %s)",
                (migration.version, migration.checksum, elapsed_ms),
            )
            applied_now.append(migration.version)
    return applied_now


if __name__ == "__main__":
    versions = migrate()
    print("Applied: " + ", ".join(versions) if versions else "Schema is current")
