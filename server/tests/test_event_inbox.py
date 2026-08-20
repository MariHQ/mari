from __future__ import annotations

import unittest

from mari_server.persistence.postgres.event_inbox import EventDispatcher


class MemoryInbox:
    def __init__(self, rows):
        self.rows = rows
        self.completed = []
        self.retried = []
    def claim(self):
        for row in self.rows:
            if row["status"] == "pending":
                row["status"] = "processing"
                row["attempts"] += 1
                return row
        return None
    def complete(self, row_id):
        self.completed.append(row_id)
        next(row for row in self.rows if row["id"] == row_id)["status"] = "completed"
    def retry(self, row_id, error, attempts):
        self.retried.append((row_id, error, attempts))
        next(row for row in self.rows if row["id"] == row_id)["status"] = "pending"


class EventDispatcherTests(unittest.TestCase):
    def test_crash_retries_and_restart_finishes_same_delivery(self):
        rows = [{"id": 1, "provider": "slack", "project_id": 2,
                 "delivery_id": "Ev-1", "payload": {}, "attempts": 0,
                 "status": "pending"}]
        inbox = MemoryInbox(rows)
        calls = []
        def crashes_once(row):
            calls.append(row["delivery_id"])
            if len(calls) == 1:
                raise RuntimeError("worker crashed")
        self.assertTrue(EventDispatcher(inbox, {"slack": crashes_once}).drain_once())
        self.assertEqual(rows[0]["status"], "pending")
        # A new dispatcher represents a restarted API process.
        self.assertTrue(EventDispatcher(inbox, {"slack": crashes_once}).drain_once())
        self.assertEqual(inbox.completed, [1])
        self.assertEqual(calls, ["Ev-1", "Ev-1"])

    def test_unknown_provider_is_retained_for_retry(self):
        rows = [{"id": 9, "provider": "future", "project_id": 2,
                 "delivery_id": "D", "payload": {}, "attempts": 0,
                 "status": "pending"}]
        inbox = MemoryInbox(rows)
        EventDispatcher(inbox, {}).drain_once()
        self.assertEqual(rows[0]["status"], "pending")
        self.assertIn("no event handler", inbox.retried[0][1])


if __name__ == "__main__":
    unittest.main()
