from __future__ import annotations

import tempfile
import unittest

from audit_log import AuditEvent, IcebergAuditTrail
from tests.iceberg_fixture import temporary_warehouse


class AuditTrailTests(unittest.TestCase):
    def test_chain_is_append_only_project_filterable_and_redacted(self):
        with tempfile.TemporaryDirectory() as directory:
            trail = IcebergAuditTrail(temporary_warehouse(directory))
            trail.append(AuditEvent(
                project_id=7, actor_type="user", actor_id="2", actor_name="Dana",
                action="fact.approve", resource_type="fact", resource_id="8",
                request_id="req-1", correlation_id="run-4",
                reason="trusted source", detail={"before": "draft", "api_token": "secret"},
            ))
            trail.append(AuditEvent(
                project_id=9, actor_type="service", actor_id="policy", actor_name="Mari",
                action="fact.escalate", resource_type="fact", resource_id="9",
                outcome="manual", reason="insufficient evidence",
            ))
            rows = trail.rows()
            self.assertTrue(trail.verify(rows))
            self.assertEqual(len(trail.rows(project_id=7)), 1)
            self.assertNotIn("secret", rows[0]["detail_json"])
            self.assertIn("[REDACTED]", rows[0]["detail_json"])

            tampered = [dict(row) for row in rows]
            tampered[0]["reason"] = "rewritten"
            self.assertFalse(trail.verify(tampered))

    def test_event_validation_is_fail_closed(self):
        with self.assertRaises(ValueError):
            AuditEvent(project_id=1, actor_type="user", actor_id="1", actor_name="A",
                       action="", resource_type="fact", resource_id="1")
        with self.assertRaises(ValueError):
            AuditEvent(project_id=1, actor_type="user", actor_id="1", actor_name="A",
                       action="read", resource_type="fact", resource_id="1", outcome="maybe")


if __name__ == "__main__":
    unittest.main()
