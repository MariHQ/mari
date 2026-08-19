"""Synced documents cannot be rewritten through GraphQL or the agent."""

from __future__ import annotations

import unittest

import agentchat
from app import schema


class ReadOnlyDocumentTests(unittest.TestCase):
    def test_document_edit_mutations_are_not_in_schema(self):
        fields = schema.as_str()
        for name in ("updateDocument", "setChangeStatus", "acceptAllChanges", "runRefinement"):
            self.assertNotIn(name, fields)

    def test_agent_has_no_document_replacement_tool(self):
        self.assertNotIn("edit_document", agentchat.TOOLS)


if __name__ == "__main__":
    unittest.main()
