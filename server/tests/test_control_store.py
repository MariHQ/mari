from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import control_store


class ControlStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "MARI_CONTROL_DB": str(Path(self.tmp.name) / "control.sqlite3"),
        })
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def test_session_survives_new_connections_and_expires(self) -> None:
        control_store.put_session("opaque", 42, 60, now=1_000,
                                  client_ip="127.0.0.1", user_agent="browser")
        self.assertEqual(control_store.session("opaque", now=1_030)["user_id"], 42)
        self.assertIsNone(control_store.session("opaque", now=1_060))

    def test_sessions_are_individually_and_collectively_revocable(self) -> None:
        control_store.put_session("one", 7, 60, now=1_000)
        control_store.put_session("two", 7, 60, now=1_000)
        control_store.put_session("other", 8, 60, now=1_000)

        self.assertTrue(control_store.revoke_session("one"))
        self.assertIsNone(control_store.session("one", now=1_001))
        self.assertEqual(control_store.revoke_user_sessions(7), 1)
        self.assertIsNotNone(control_store.session("other", now=1_001))

    def test_rejects_invalid_session_material(self) -> None:
        for args in (("", 1, 10), ("token", 0, 10), ("token", 1, 0)):
            with self.assertRaises(ValueError):
                control_store.put_session(*args)

    def test_password_rotation_keeps_only_the_current_session(self) -> None:
        control_store.put_session("current", 9, 60, now=100)
        control_store.put_session("other", 9, 60, now=100)
        control_store.put_session("different-user", 10, 60, now=100)

        self.assertEqual(control_store.revoke_other_user_sessions(9, "current"), 1)
        self.assertIsNotNone(control_store.session("current", now=101))
        self.assertIsNone(control_store.session("other", now=101))
        self.assertIsNotNone(control_store.session("different-user", now=101))


if __name__ == "__main__":
    unittest.main()
