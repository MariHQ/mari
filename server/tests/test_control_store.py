from __future__ import annotations

import datetime as dt
import unittest
from unittest.mock import MagicMock, patch

from mari_server.persistence.postgres import control_store


class ControlStoreTests(unittest.TestCase):
    def connection(self):
        connection = MagicMock()
        manager = MagicMock()
        manager.__enter__.return_value = connection
        manager.__exit__.return_value = False
        return connection, manager

    def test_session_write_uses_postgres_and_absolute_expiry(self) -> None:
        connection, manager = self.connection()
        with patch.object(control_store, "_connect", return_value=manager):
            control_store.put_session(
                "opaque", 42, 60, now=1_000,
                client_ip="x" * 300, user_agent="y" * 700,
            )
        insert = connection.execute.call_args_list[0]
        self.assertIn("INSERT INTO sessions", insert.args[0])
        self.assertEqual(insert.args[1][2], dt.datetime.fromtimestamp(1_000, dt.timezone.utc))
        self.assertEqual(insert.args[1][3], dt.datetime.fromtimestamp(1_060, dt.timezone.utc))
        self.assertEqual(len(insert.args[1][4]), 200)
        self.assertEqual(len(insert.args[1][5]), 500)

    def test_session_lookup_and_expired_cleanup(self) -> None:
        connection, manager = self.connection()
        connection.execute.return_value.fetchone.return_value = {
            "token": "opaque", "user_id": 42,
            "created_at": dt.datetime.now(dt.timezone.utc),
            "expires_at": dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=1),
            "client_ip": "", "user_agent": "",
        }
        with patch.object(control_store, "_connect", return_value=manager):
            self.assertEqual(control_store.session("opaque")["user_id"], 42)
        self.assertIn("expires_at >", connection.execute.call_args.args[0])

        missing, missing_manager = self.connection()
        missing.execute.return_value.fetchone.return_value = None
        with patch.object(control_store, "_connect", return_value=missing_manager):
            self.assertIsNone(control_store.session("expired", now=1_000))
        self.assertIn("DELETE FROM sessions", missing.execute.call_args_list[-1].args[0])

    def test_revocation_reports_affected_rows(self) -> None:
        connection, manager = self.connection()
        connection.execute.return_value.rowcount = 2
        with patch.object(control_store, "_connect", return_value=manager):
            self.assertTrue(control_store.revoke_session("one"))
            self.assertEqual(control_store.revoke_user_sessions(7), 2)
            self.assertEqual(control_store.revoke_other_user_sessions(7, "current"), 2)

    def test_rejects_invalid_session_material(self) -> None:
        for args in (("", 1, 10), ("token", 0, 10), ("token", 1, 0)):
            with self.assertRaises(ValueError):
                control_store.put_session(*args)

    def test_health_identifies_postgres_backend(self) -> None:
        connection, manager = self.connection()
        connection.execute.return_value.fetchone.return_value = {"n": 3}
        with patch.object(control_store, "_connect", return_value=manager):
            self.assertEqual(
                control_store.health(),
                {"ok": True, "backend": "postgresql", "active_sessions": 3},
            )


if __name__ == "__main__":
    unittest.main()
