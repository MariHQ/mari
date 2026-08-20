from __future__ import annotations

import unittest

from mari_components.audit import AuditEvent, chained_row, verify_chain


class AuditTrailTests(unittest.TestCase):
    def test_chain_is_project_partitioned_tamper_evident_and_redacted(self):
        first = chained_row(AuditEvent(
            project_id=7, actor_type="user", actor_id="2", actor_name="Dana",
            action="fact.approve", resource_type="fact", resource_id="8",
            detail={"before": "draft", "api_token": "secret"},
        ), "")
        second = chained_row(AuditEvent(
            project_id=7, actor_type="service", actor_id="policy", actor_name="Mari",
            action="fact.escalate", resource_type="fact", resource_id="9", outcome="manual",
        ), first["event_hash"])
        other = chained_row(AuditEvent(
            project_id=9, actor_type="service", actor_id="policy", actor_name="Mari",
            action="fact.approve", resource_type="fact", resource_id="1",
        ), "")
        rows = [first, second, other]
        self.assertTrue(verify_chain(rows))
        self.assertNotIn("secret", first["detail_json"])
        self.assertIn("[REDACTED]", first["detail_json"])
        tampered = [dict(row) for row in rows]
        tampered[0]["reason"] = "rewritten"
        self.assertFalse(verify_chain(tampered))

    def test_event_validation_is_fail_closed(self):
        with self.assertRaises(ValueError):
            AuditEvent(project_id=1, actor_type="user", actor_id="1", actor_name="A",
                       action="", resource_type="fact", resource_id="1")
        with self.assertRaises(ValueError):
            AuditEvent(project_id=1, actor_type="user", actor_id="1", actor_name="A",
                       action="read", resource_type="fact", resource_id="1", outcome="maybe")


if __name__ == "__main__":
    unittest.main()
