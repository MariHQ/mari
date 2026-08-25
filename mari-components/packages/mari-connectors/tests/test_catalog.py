from __future__ import annotations

import unittest

from mari_components.connectors import CONNECTOR_CATALOG, connector_definitions


class CatalogTests(unittest.TestCase):
    def test_catalog_is_complete_unique_and_ordered(self):
        expected = {
            "airtable", "asana", "confluence", "dropbox", "gdrive", "github",
            "jira", "linear", "notion", "slack", "trello", "zendesk",
        }
        self.assertEqual(set(CONNECTOR_CATALOG), expected)
        ordered = connector_definitions()
        self.assertEqual([item.key for item in ordered[:6]], [
            "github", "slack", "gdrive", "confluence", "notion", "jira",
        ])
        self.assertTrue(all(definition.fields for definition in ordered))

    def test_jira_catalog_explains_current_token_and_filter_setup(self):
        jira = CONNECTOR_CATALOG["jira"]
        fields = {field.key: field for field in jira.fields}

        self.assertIn("unscoped Atlassian API token", jira.description)
        self.assertIn("manage-api-tokens", jira.documentation_url)
        self.assertIn("not an API token with scopes", fields["api_token"].help)
        self.assertIn("Browse Projects", fields["email"].help)
        self.assertEqual(fields["project_key"].placeholder, "MARI")
        self.assertIn("do not include ORDER BY", fields["jql"].help)
        self.assertIn("last 365 days", fields["jql"].help)


if __name__ == "__main__":
    unittest.main()
