from __future__ import annotations

import datetime as dt
import tempfile
import unittest

from tests.iceberg_fixture import temporary_warehouse
from mari_server.domain.documents import DocumentVersion
from mari_server.infrastructure.document_store import IcebergDocumentStore


class KnowledgeLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = IcebergDocumentStore(temporary_warehouse(self.tmp.name))
        self.when = dt.datetime(2026, 8, 19, tzinfo=dt.timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def version(self, **changes):
        values = dict(project_id=7, source_id="confluence:1", external_id="page-9",
                      revision="1", title="Retention", body="Thirty days",
                      source_url="https://wiki/page-9", recorded_at=self.when)
        values.update(changes)
        return DocumentVersion(**values)

    def test_versions_are_idempotent_and_acl_changes_are_versions(self):
        first = self.store.append(self.version())
        replay = self.store.append(self.version(version_id="replay"))
        self.assertEqual(replay["version_id"], first["version_id"])
        self.store.append(self.version(version_id="acl", acl={"visibility": "restricted", "principals": ["team:legal"]}))
        self.assertEqual(len(self.store.history(7, "confluence:1", "page-9")), 2)

    def test_archive_delete_restore_are_non_destructive(self):
        self.store.append(self.version())
        self.store.transition(project_id=7, source_id="confluence:1", external_id="page-9",
                              status="archived", reason="source archived", actor="connector")
        self.assertEqual(self.store.current(7), [])
        self.assertEqual(len(self.store.current(7, include_archived=True)), 1)
        self.store.transition(project_id=7, source_id="confluence:1", external_id="page-9",
                              status="deleted", reason="source tombstone", actor="connector")
        self.assertIsNone(self.store.get(7, "confluence:1", "page-9"))
        self.assertEqual(len(self.store.history(7, "confluence:1", "page-9")), 3)
        self.store.append(self.version(revision="2", body="Seven days", version_id="restored",
                                       recorded_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=1)))
        self.assertEqual(self.store.get(7, "confluence:1", "page-9")["body"], "Seven days")

    def test_project_boundary_is_part_of_document_identity(self):
        self.store.append(self.version())
        self.store.append(self.version(project_id=9, body="Different project", version_id="p9"))
        self.assertEqual([row["body"] for row in self.store.current(7)], ["Thirty days"])
        self.assertEqual([row["body"] for row in self.store.current(9)], ["Different project"])


if __name__ == "__main__":
    unittest.main()
