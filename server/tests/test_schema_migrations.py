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


if __name__ == "__main__":
    unittest.main()
