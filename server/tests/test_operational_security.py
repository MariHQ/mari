from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import pathlib
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

import numpy as np

import access
import bots
import trajectory
from retrieval import DerivedVectorIndex
from mari_components.retrieval import FDEConfig


class _MemoryLedger:
    """Bot orchestration seam; PostgreSQL durability is integration-tested."""
    def __init__(self):
        self.events: dict[tuple[str, str], bool] = {}

    def claim(self, provider, event_id):
        key = (provider, event_id)
        if key in self.events:
            return False
        self.events[key] = False
        return True

    def complete(self, provider, event_id):
        self.events[(provider, event_id)] = True

    def release(self, provider, event_id):
        key = (provider, event_id)
        if self.events.get(key) is False:
            del self.events[key]


class SlackExecutionTests(unittest.TestCase):
    @staticmethod
    def _request(payload: dict):
        raw = json.dumps(payload, separators=(",", ":")).encode()
        timestamp = str(int(time.time()))
        signature = "v0=" + hmac.new(
            b"secret", f"v0:{timestamp}:".encode() + raw, hashlib.sha256).hexdigest()

        class Request:
            headers = {"X-Slack-Request-Timestamp": timestamp,
                       "X-Slack-Signature": signature}

            async def body(self):
                return raw
        return Request()

    @staticmethod
    def _installation():
        return {"id": 5, "project_id": 7, "project_slug": "acme", "project_name": "Acme",
                "config": {"signing_secret": "secret", "bot_token": "xoxb"}}

    def test_duplicate_event_is_acknowledged_after_one_durable_insert(self):
        payload = {"type": "event_callback", "team_id": "T1", "event_id": "Ev1",
                   "event": {"type": "app_mention", "text": "hello", "channel": "C1", "ts": "1"}}
        class Inbox:
            calls = []
            def enqueue(self, provider, project_id, delivery_id, payload, **kwargs):
                self.calls.append((provider, project_id, delivery_id, payload, kwargs))
                return 1, len(self.calls) == 1
        inbox = Inbox()
        with patch.object(bots, "q1", return_value=self._installation()), \
             patch.object(bots, "exec_"), patch.object(bots, "_EVENT_INBOX", inbox):
            first = asyncio.run(bots.slack_webhook(self._request(payload)))
            duplicate = asyncio.run(bots.slack_webhook(self._request(payload)))
        self.assertEqual(first, {"ok": True})
        self.assertEqual(duplicate, {"ok": True, "duplicate": True})
        self.assertEqual(len(inbox.calls), 2)
        self.assertEqual(inbox.calls[0][2], "Ev1")

    def test_failed_durable_insert_requests_provider_retry(self):
        payload = {"type": "event_callback", "team_id": "T1", "event_id": "Ev-full",
                   "event": {"type": "app_mention", "text": "hello", "channel": "C1", "ts": "1"}}
        class FailedInbox:
            def enqueue(self, *_args, **_kwargs): raise OSError("database unavailable")
        with patch.object(bots, "q1", return_value=self._installation()), \
             patch.object(bots, "_EVENT_INBOX", FailedInbox()):
            response = asyncio.run(bots.slack_webhook(self._request(payload)))
        self.assertEqual(response.status_code, 503)


class TrajectoryOperationsTests(unittest.TestCase):
    def setUp(self):
        self.project = access.AccessContext(1, 7, "acme", "Acme", "admin", access.CAPABILITIES)

    def test_submit_refuses_work_when_pending_ceiling_is_full(self):
        with patch.object(trajectory._PENDING, "acquire", return_value=False), \
             patch.object(trajectory._WORKERS, "submit") as submit:
            self.assertFalse(trajectory._submit(self.project, 9, "prompt", []))
        submit.assert_not_called()

    def test_stale_rows_are_claimed_once_and_saturated_recovery_finishes_fallback(self):
        rows = [{"id": 9, "prompt": "Fix docs"}]
        steps = [{"ordinal": 0, "tool": "search", "action_family": "discover",
                  "args": {}, "summary": "found", "ok": True}]
        with access.use_access(self.project), \
             patch.object(trajectory, "q", side_effect=[rows, steps]) as query, \
             patch.object(trajectory, "_submit", return_value=False), \
             patch.object(trajectory, "_fallback") as fallback:
            self.assertEqual(trajectory.reconcile_stale_processing(), 1)
        self.assertIn("FOR UPDATE SKIP LOCKED", query.call_args_list[0].args[0])
        fallback.assert_called_once_with(9, steps, 7, "Recovered without LLM")


class RetrievalGenerationTests(unittest.TestCase):
    cfg = FDEConfig(repetitions=2, simhash_bits=2, projection_dimension=4)

    @staticmethod
    def _docs(axis: int, doc_id: int) -> dict[int, np.ndarray]:
        vector = np.zeros((1, 3), np.float32)
        vector[0, axis] = 1
        return {doc_id: vector}

    def test_reader_reloads_atomically_committed_local_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = DerivedVectorIndex(directory, self.cfg)
            writer.build(self._docs(0, 10))
            reader = DerivedVectorIndex(directory, self.cfg)
            reader._reload_seconds = 0
            self.assertEqual(reader.search(self._docs(0, 0)[0], k=1)[0]["document_id"], 10)
            DerivedVectorIndex(directory, self.cfg).build(self._docs(1, 20))
            self.assertEqual(reader.search(self._docs(1, 0)[0], k=1)[0]["document_id"], 20)

    def test_partial_s3_generation_never_replaces_last_good_pointer(self):
        with tempfile.TemporaryDirectory() as cache, tempfile.TemporaryDirectory() as remote_dir:
            with patch.dict(os.environ, {"MARI_VECTOR_CACHE": cache}):
                local = DerivedVectorIndex("s3://bucket/prefix", self.cfg)
            with patch.object(local, "_mirror_s3"):
                local.build(self._docs(0, 10))
            old_pointer = (pathlib.Path(cache) / "current.json").read_bytes()

            remote = DerivedVectorIndex(remote_dir, self.cfg)
            remote.build(self._docs(1, 20))
            remote_root = pathlib.Path(remote_dir)
            manifest = json.loads((remote_root / "current.json").read_text())
            generation = manifest["generation"]

            class Client:
                def download_file(self, _bucket, key, destination):
                    if key.endswith("current.json"):
                        source = remote_root / "current.json"
                    else:
                        name = key.rsplit("/", 1)[-1]
                        if name == "vectors.npy":
                            raise OSError("interrupted download")
                        source = remote_root / "generations" / generation / name
                    pathlib.Path(destination).write_bytes(source.read_bytes())

            fake_boto = types.SimpleNamespace(client=lambda _service: Client())
            with patch.dict(sys.modules, {"boto3": fake_boto}):
                self.assertFalse(local._pull_s3())
            self.assertEqual((pathlib.Path(cache) / "current.json").read_bytes(), old_pointer)
            self.assertEqual(local.search(self._docs(0, 0)[0], k=1)[0]["document_id"], 10)


if __name__ == "__main__":
    unittest.main()
