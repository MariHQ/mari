from pathlib import Path
import tempfile
import unittest

from mari_server.persistence.postgres.schema import Migration, discover, pending


class SchemaMigrationTests(unittest.TestCase):
    def test_discovers_baseline_and_numbered_files_in_order(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "migrations").mkdir()
            (root / "init.sql").write_text("SELECT 1;", encoding="utf-8")
            (root / "migrations" / "0003_third.sql").write_text("SELECT 3;", encoding="utf-8")
            (root / "migrations" / "0002_second.sql").write_text("SELECT 2;", encoding="utf-8")
            self.assertEqual(
                [item.version for item in discover(root)],
                ["0001_baseline", "0002_second", "0003_third"],
            )

    def test_rejects_invalid_or_reserved_names(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "migrations").mkdir()
            (root / "init.sql").write_text("SELECT 1;", encoding="utf-8")
            (root / "migrations" / "next.sql").write_text("SELECT 2;", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "Invalid migration filename"):
                discover(root)

    def test_checksum_mismatch_and_unknown_database_version_fail_closed(self):
        migration = Migration("0001_baseline", Path("init.sql"), "SELECT 1", "expected")
        with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
            pending([migration], {"0001_baseline": "tampered"})
        with self.assertRaisesRegex(RuntimeError, "absent from this release"):
            pending([migration], {"9999_future": "value"})

    def test_pending_returns_only_unapplied_versions(self):
        first = Migration("0001_baseline", Path("init.sql"), "one", "a")
        second = Migration("0002_next", Path("next.sql"), "two", "b")
        self.assertEqual(pending([first, second], {"0001_baseline": "a"}), [second])

    def test_release_ships_the_heartbeat_and_full_sync_columns_as_one_migration(self):
        # The ledger is immutable once applied, so the two runtime columns
        # travel in a numbered file, not in init.sql (the baseline has not
        # mirrored a numbered migration since the ledger release).
        migrations = {item.version: item for item in discover()}
        migration = migrations["0035_run_heartbeat_and_full_sync"]
        self.assertIn("ALTER TABLE workflow_runs ADD COLUMN heartbeat_at timestamptz NOT NULL DEFAULT now()",
                      migration.sql)
        self.assertIn("ALTER TABLE sources ADD COLUMN last_full_sync_at timestamptz", migration.sql)
        baseline = migrations["0001_baseline"].sql
        self.assertNotIn("heartbeat_at", baseline)
        self.assertNotIn("last_full_sync_at", baseline)

    def test_release_hashes_legacy_mcp_tokens_before_authenticate_stops_reading_them(self):
        # authenticate no longer matches the plaintext column, so the ledger
        # has to carry every legacy bearer into token_hash first, with the
        # digest the runtime compares (sha256 hex of the UTF-8 bytes), and
        # leave nothing replayable behind.
        migrations = {item.version: item for item in discover()}
        migration = migrations["0036_mcp_legacy_token_hash"]
        self.assertIn("SET token_hash = encode(sha256(convert_to(token, 'UTF8')), 'hex')", migration.sql)
        self.assertIn("token_hash = ''", migration.sql)
        self.assertIn("UPDATE mcp_servers SET token = '' WHERE token IS NOT NULL AND token <> ''",
                      migration.sql)
        self.assertNotIn("DROP COLUMN", migration.sql)


if __name__ == "__main__":
    unittest.main()
