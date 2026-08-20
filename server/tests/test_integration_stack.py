"""Credential-free production-stack probe; enabled only in compose integration CI."""

from __future__ import annotations

import os
import unittest
from concurrent.futures import ThreadPoolExecutor


@unittest.skipUnless(os.environ.get("MARI_INTEGRATION_STACK") == "1", "integration stack only")
class IntegrationStackTests(unittest.TestCase):
    def test_postgres_iceberg_object_storage_and_ollama(self):
        import boto3
        import numpy as np

        from mari_server.identity import access
        from mari_server.providers import models as llm
        from mari_server.providers import vectors as retrieval
        from mari_server.persistence.postgres import schema
        from mari_components.documents import DocumentVersion
        from mari_server.persistence.iceberg.documents import IcebergDocumentStore
        from mari_server.persistence.iceberg.warehouse import warehouse
        from mari_server.persistence.postgres.database import q1

        context = access.AccessContext(
            user_id=1, project_id=1, project_slug="default",
            project_name="Integration Workspace", role="owner",
            principal_type="user", principal_id="1",
            capabilities=access.capabilities_for_role("owner"),
        )
        with access.use_access(context):
            document = q1(
                "SELECT id, title FROM documents WHERE project_id = %s AND external_id = %s",
                (1, "retention-runbook"),
            )
            self.assertEqual(document["title"], "Retention runbook")

            vector = llm.embed("retention policy")
            self.assertIsNotNone(vector, llm.last_error())
            self.assertEqual(len(vector), 768)

            generated = llm.generate("Reply with exactly: stack-ok", timeout=60)
            self.assertTrue(generated, llm.last_error())

            matrix = np.asarray([vector], dtype=np.float32)
            retrieval.index_for(1).build(
                {int(document["id"]): matrix}, {int(document["id"]): "ci"},
            )
            IcebergDocumentStore().append(DocumentVersion(
                project_id=1, source_id="integration", external_id="retention-runbook",
                revision="ci-1", title="Retention runbook", body="Retention policy",
            ))

        self.assertEqual(warehouse().table_names(), ["knowledge_versions"])
        self.assertEqual(schema.migrate(), [])
        migration = q1("SELECT version, checksum FROM schema_migrations WHERE version = %s", ("0001_baseline",))
        self.assertEqual(migration["version"], "0001_baseline")
        self.assertEqual(len(migration["checksum"]), 64)
        client = boto3.client("s3", endpoint_url=os.environ["MARI_S3_ENDPOINT_URL"])
        keys = [
            row["Key"]
            for row in client.list_objects_v2(
                Bucket="mari-ci", Prefix="vectors/projects/1/",
            ).get("Contents", [])
        ]
        self.assertTrue(any(key.endswith("current.json") for key in keys), keys)

    def test_postgres_sessions_and_webhook_deduplication_survive_reconnect(self):
        from mari_server.persistence.postgres import control_store
        from mari_server.persistence.postgres.event_inbox import EventInbox

        control_store.put_session("integration-session", 1, 60)
        self.assertEqual(control_store.session("integration-session")["user_id"], 1)
        self.assertEqual(control_store.health()["backend"], "postgresql")

        first, restarted = EventInbox(), EventInbox()
        row_id, inserted = first.enqueue("slack", 1, "integration-event", {})
        self.assertTrue(inserted)
        duplicate_id, inserted = restarted.enqueue("slack", 1, "integration-event", {})
        self.assertFalse(inserted)
        self.assertEqual(duplicate_id, row_id)

        control_store.revoke_session("integration-session")

    def test_migrations_and_event_claims_are_safe_under_concurrency(self):
        from mari_server.persistence.postgres import schema
        from mari_server.persistence.postgres.event_inbox import EventInbox

        with ThreadPoolExecutor(max_workers=8) as workers:
            migration_results = list(workers.map(lambda _index: schema.migrate(), range(16)))
        self.assertEqual(migration_results, [[] for _index in range(16)])

        with ThreadPoolExecutor(max_workers=16) as workers:
            inserts = list(workers.map(
                lambda _index: EventInbox().enqueue(
                    "slack", 1, "concurrent-integration-event", {},
                )[1],
                range(32),
            ))
        self.assertEqual(inserts.count(True), 1)


if __name__ == "__main__":
    unittest.main()
