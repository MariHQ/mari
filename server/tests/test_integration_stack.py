"""Credential-free production-stack probe; enabled only in compose integration CI."""

from __future__ import annotations

import os
import unittest


@unittest.skipUnless(os.environ.get("MARI_INTEGRATION_STACK") == "1", "integration stack only")
class IntegrationStackTests(unittest.TestCase):
    def test_postgres_iceberg_object_storage_and_ollama(self):
        import boto3
        import numpy as np

        import access
        import iceberg
        import llm
        import retrieval
        import schema_migrations
        from db import q1

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

        self.assertIn("mutation_journal", iceberg.warehouse().table_names())
        self.assertEqual(schema_migrations.migrate(), [])
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
        import control_store
        from event_dedupe import EventLedger

        control_store.put_session("integration-session", 1, 60)
        self.assertEqual(control_store.session("integration-session")["user_id"], 1)
        self.assertEqual(control_store.health()["backend"], "postgresql")

        first, restarted = EventLedger(), EventLedger()
        self.assertTrue(first.claim("slack", "integration-event"))
        self.assertFalse(restarted.claim("slack", "integration-event"))
        restarted.release("slack", "integration-event")
        self.assertTrue(first.claim("slack", "integration-event"))
        first.complete("slack", "integration-event")
        restarted.release("slack", "integration-event")
        self.assertFalse(restarted.claim("slack", "integration-event"))

        control_store.revoke_session("integration-session")


if __name__ == "__main__":
    unittest.main()
