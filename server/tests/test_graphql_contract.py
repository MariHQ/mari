from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import psycopg
from graphql import parse, validate
from strawberry.extensions.query_depth_limiter import create_validator

from mari_server import app as application
from mari_server.product import queries
from mari_server.scripts import export_graphql_schema


class GraphQLContractTests(unittest.TestCase):
    def test_committed_schema_matches_runtime_schema(self) -> None:
        self.assertTrue(export_graphql_schema.check())

    def test_drift_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stale = Path(directory) / "schema.graphql"
            stale.write_text("type Query { stale: Boolean! }\n")
            self.assertFalse(export_graphql_schema.check(stale))


def messages(result) -> list[str]:
    return [error.message for error in (result.errors or [])]


class GraphQLShapeLimitTests(unittest.TestCase):
    """The console's deepest query nests three selection sets and its largest
    lexes to 130 tokens; the limits refuse only the document built to
    exhaust the server."""

    def test_the_deepest_console_query_passes_the_depth_rule(self) -> None:
        # The deepest chain in web/src/data/actions/facts.ts, validated
        # against the real schema with the same rule the extension installs,
        # without executing it (that would need a database).
        document = parse("query { factRunIntelligence(runId: 1) { evidenceGroups { spans { documentTitle } } } }")
        rule = create_validator(application.GRAPHQL_MAX_DEPTH, None)
        self.assertEqual(validate(application.schema._schema, document, [rule]), [])
        self.assertTrue(validate(application.schema._schema, document, [create_validator(2, None)]))

    def test_pathological_depth_is_refused(self) -> None:
        deep = "{ a " * (application.GRAPHQL_MAX_DEPTH + 2) + "}" * (application.GRAPHQL_MAX_DEPTH + 2)
        errors = messages(application.schema.execute_sync("query " + deep))
        self.assertTrue(any("exceeds maximum operation depth" in e for e in errors), errors)

    def test_alias_and_token_floods_are_refused(self) -> None:
        aliases = " ".join(f"a{i}: __typename" for i in range(application.GRAPHQL_MAX_ALIASES + 1))
        errors = messages(application.schema.execute_sync("query { " + aliases + " }"))
        self.assertTrue(any("aliases" in e for e in errors), errors)
        flood = "query { " + "__typename " * (application.GRAPHQL_MAX_TOKENS + 1) + "}"
        errors = messages(application.schema.execute_sync(flood))
        self.assertTrue(any("tokens" in e for e in errors), errors)


class GraphQLErrorMaskingTests(unittest.TestCase):
    QUERY = "query { auditLog(limit: 5) { id } }"

    def test_database_errors_are_logged_and_masked(self) -> None:
        leak = psycopg.OperationalError("SELECT * FROM events WHERE project_id = 7 failed")
        with patch.object(queries.audit_store, "events", side_effect=leak), \
             self.assertLogs("strawberry.execution", level="ERROR") as logged:
            result = application.schema.execute_sync(self.QUERY)
        self.assertEqual(messages(result), [application.GRAPHQL_MASKED_MESSAGE])
        self.assertIn("SELECT * FROM events", "\n".join(logged.output))

    def test_errors_raised_for_the_console_stay_readable(self) -> None:
        with patch.object(queries.audit_store, "events", side_effect=ValueError("dateFrom must be a date")), \
             self.assertLogs("strawberry.execution", level="ERROR"):
            result = application.schema.execute_sync(self.QUERY)
        self.assertEqual(messages(result), ["dateFrom must be a date"])
        with patch.object(queries.audit_store, "events", side_effect=PermissionError("Admins only.")), \
             self.assertLogs("strawberry.execution", level="ERROR"):
            result = application.schema.execute_sync(self.QUERY)
        self.assertEqual(messages(result), ["Admins only."])

    def test_query_errors_keep_graphql_wording(self) -> None:
        errors = messages(application.schema.execute_sync("query { nope }"))
        self.assertTrue(errors and "nope" in errors[0], errors)


class AuditLogLimitTests(unittest.TestCase):
    def test_limit_is_clamped_before_it_reaches_the_store(self) -> None:
        row = {"id": 1, "actor": "a", "verb": "v", "target": "t",
               "occurred_at": dt.datetime(2026, 9, 2, tzinfo=dt.timezone.utc), "detail": []}
        with patch.object(queries.audit_store, "events", return_value=[row]) as events:
            self.assertEqual(len(queries.Query().audit_log(limit=100000)), 1)
            self.assertEqual(events.call_args.args[-1], 500)
            queries.Query().audit_log(limit=0)
            self.assertEqual(events.call_args.args[-1], 1)
            queries.Query().audit_log(limit=200)
            self.assertEqual(events.call_args.args[-1], 200)


if __name__ == "__main__":
    unittest.main()
