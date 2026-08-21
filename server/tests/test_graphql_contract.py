from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mari_server.scripts import export_graphql_schema


class GraphQLContractTests(unittest.TestCase):
    def test_committed_schema_matches_runtime_schema(self) -> None:
        self.assertTrue(export_graphql_schema.check())

    def test_drift_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stale = Path(directory) / "schema.graphql"
            stale.write_text("type Query { stale: Boolean! }\n")
            self.assertFalse(export_graphql_schema.check(stale))


if __name__ == "__main__":
    unittest.main()
