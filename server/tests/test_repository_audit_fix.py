from __future__ import annotations

import unittest
from unittest.mock import patch

from mari_server.identity import context as access
from mari_server.persistence.postgres import repository_audit


class Result:
    def __init__(self, one=None, many=None):
        self.one, self.many = one, many or []

    def fetchone(self):
        return self.one

    def fetchall(self):
        return list(self.many)


class FakeConn:
    def __init__(self, handler):
        self.handler, self.calls = handler, []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, args=()):
        normalized = " ".join(sql.split())
        self.calls.append((normalized, args))
        return self.handler(normalized, args)


FINDING = {"id": 11, "project_id": 7, "run_id": 2, "status": "open", "title": "Untagged doc",
           "fix_action": "apply_tag", "fix_payload": {"doc_id": 42, "tag": "canonical"}}


def context(project_id: int) -> access.AccessContext:
    return access.AccessContext(
        user_id=1, project_id=project_id, project_slug="acme", project_name="Acme",
        role="admin", capabilities=access.CAPABILITIES)


class ApplyTagFixTests(unittest.TestCase):
    def tearDown(self):
        access.set_access(None)

    def _run(self, document_in_project: bool):
        def handler(sql, args):
            if sql.startswith("SELECT * FROM audit_findings"): return Result(FINDING)
            if sql.startswith("SELECT id FROM documents"):
                self.assertEqual(args, (42, 7))
                return Result({"id": 42}) if document_in_project else Result()
            return Result()
        conn = FakeConn(handler)
        with access.use_access(context(7)), patch.object(repository_audit, "_conn", return_value=conn):
            summary = repository_audit.fix_finding(11, "Dana")
        return summary, conn.calls

    def test_tag_is_written_into_the_project_the_finding_belongs_to(self):
        summary, calls = self._run(document_in_project=True)
        self.assertEqual(summary, "tagged 'canonical'")
        tag_sql, tag_args = next((sql, args) for sql, args in calls if sql.startswith("INSERT INTO tags"))
        self.assertIn("(project_id, document_id, tag)", tag_sql)
        self.assertEqual(tag_args, (7, 42, "canonical"))
        self.assertTrue(any(sql.startswith("UPDATE audit_findings SET status = 'fixed'") for sql, _ in calls))

    def test_document_outside_the_project_is_neither_tagged_nor_marked_fixed(self):
        summary, calls = self._run(document_in_project=False)
        self.assertEqual(summary, "that document is not in this project")
        self.assertFalse(any("INSERT INTO tags" in sql for sql, _ in calls))
        self.assertFalse(any("SET status = 'fixed'" in sql for sql, _ in calls))


if __name__ == "__main__":
    unittest.main()
