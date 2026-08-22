"""Synced documents cannot be rewritten through GraphQL or the agent."""

from __future__ import annotations

import unittest

from mari_server.app import schema
from mari_server.conversations.tools import ToolDependencies, build_tool_bindings


class ReadOnlyDocumentTests(unittest.TestCase):
    def test_document_edit_mutations_are_not_in_schema(self):
        fields = schema.as_str()
        for name in ("updateDocument", "setChangeStatus", "acceptAllChanges", "runRefinement"):
            self.assertNotIn(name, fields)

    def test_agent_has_no_document_replacement_tool(self):
        class EmptyStore:
            def __getattr__(self, _name):
                return lambda *_args: ()

        bindings = build_tool_bindings(ToolDependencies(
            store=EmptyStore(),
            search=lambda *_args: (), record_search=lambda _text: None,
            connector_definitions=lambda: (),
        ))
        self.assertNotIn("edit_document", bindings)


if __name__ == "__main__":
    unittest.main()
