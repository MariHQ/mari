"""A legacy sources row (kind "" from the retired connectSource mutation, or
any kind the connector worker does not own) can only be removed. Every other
door is shut honestly: its config is masked like a connector's, and the sync
and config writes refuse with the words the card shows."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from mari_server.identity import access
from mari_server.identity import graphql as mutations_admin
from mari_server.persistence.postgres import connector_sync
from mari_server.product import queries
from mari_server.sources import sync as ingest


def _context() -> access.AccessContext:
    return access.AccessContext(
        user_id=1, project_id=7, project_slug="acme", project_name="Acme",
        role="admin", capabilities=access.CAPABILITIES)


class _Info:
    context = {"user": {"id": 1, "name": "Admin", "role": "admin"}, "access": _context()}


class SourcePulseConfigMaskTests(unittest.TestCase):
    def test_a_legacy_row_config_is_masked_like_a_connectors(self) -> None:
        # The old mutation stored the token it was handed; sourcePulse is read
        # by every member, so the kind must not decide whether it is masked.
        rows = [{
            "id": 4, "provider": "confluence", "display_name": "Confluence (old)",
            "status": "active", "stat_num": "0", "stat_unit": "docs", "docs_count": 0,
            "health": "Never synced", "kind": "",
            "config": json.dumps({"site_url": "https://acme.atlassian.test",
                                  "api_token": "atl-secret", "shas": {"a": "b"}}),
            "last_sync_at": None,
        }]
        with patch.object(queries.source_store, "pulse_inputs", return_value=([], [], rows)):
            pulse = queries.Query().source_pulse()
        self.assertEqual(len(pulse), 1)
        self.assertEqual(pulse[0].kind, "")
        self.assertEqual(pulse[0].config["api_token"], connector_sync.MASK)
        self.assertEqual(pulse[0].config["site_url"], "https://acme.atlassian.test")
        self.assertNotIn("shas", pulse[0].config)
        self.assertNotIn("atl-secret", json.dumps(pulse[0].config))


class LegacySourceRefusalTests(unittest.TestCase):
    def tearDown(self) -> None:
        access.set_access(None)
        with ingest._LOCK:
            ingest._RUNNING.discard(4)

    def test_start_sync_refuses_a_row_no_connector_owns(self) -> None:
        # Refused before a run is accepted: no True that turns into
        # "source is not a connector" in the worker, and no slot held.
        with access.use_access(_context()), \
             patch.object(ingest.source_store, "source_kind", return_value=""):
            with self.assertRaisesRegex(ValueError, "no connector behind it"):
                ingest.start_sync(4)
        self.assertFalse(ingest.is_running(4))

    def test_sync_and_resync_mutations_surface_the_refusal(self) -> None:
        with access.use_access(_context()), \
             patch.object(ingest.source_store, "source_kind", return_value=""):
            with self.assertRaisesRegex(ValueError, "Remove it and connect again"):
                mutations_admin.MutAdmin().sync_source(_Info(), 4)
            with self.assertRaisesRegex(ValueError, "Remove it and connect again"):
                mutations_admin.MutAdmin().resync_source(_Info(), 4)

    def test_sync_now_refuses_a_legacy_provider(self) -> None:
        with patch.object(mutations_admin.admin_store, "source",
                          return_value={"id": 4, "kind": "", "config": {}}), \
             patch.object(mutations_admin.ingest, "start_sync") as start:
            with self.assertRaisesRegex(ValueError, "no connector behind it"):
                mutations_admin.MutAdmin().sync_now(_Info(), "confluence")
        start.assert_not_called()

    def test_update_source_config_refuses_to_store_a_credential_in_a_dead_row(self) -> None:
        with patch.object(mutations_admin.admin_store, "source",
                          return_value={"id": 4, "kind": "", "config": {}}), \
             patch.object(mutations_admin.admin_store, "update_source_config") as write, \
             patch.object(mutations_admin, "audit"):
            with self.assertRaisesRegex(ValueError, "no connector behind it"):
                mutations_admin.MutAdmin().update_source_config(
                    _Info(), "confluence", {"api_token": "new-secret"})
        write.assert_not_called()

    def test_pause_source_answers_false_for_a_row_no_connector_owns(self) -> None:
        # The console turns this False into a thrown error rather than
        # drawing the orphan as paused.
        with patch.object(mutations_admin.source_store, "connector_sources", return_value=[]), \
             patch.object(mutations_admin.admin_store, "pause_source") as pause:
            self.assertFalse(mutations_admin.MutAdmin().pause_source(_Info(), 4))
        pause.assert_not_called()


if __name__ == "__main__":
    unittest.main()
