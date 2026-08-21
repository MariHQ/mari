from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from mari_server.persistence.postgres import lineage as links


class LinkProjectScopeTests(unittest.TestCase):
    def test_edge_insert_persists_project_id(self):
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [{"id": 1}]

        created = links._insert_edges(conn, 7, [(11, 12, "links_to", {"href": "README.md"})])

        self.assertEqual(created, 1)
        sql, args = conn.execute.call_args.args
        self.assertIn("INSERT INTO edges (project_id, from_doc, to_doc", sql)
        self.assertEqual(args[:4], [7, 11, 12, "links_to"])

    def test_extract_rejects_a_source_from_another_project(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {"project_id": 9}
        manager = MagicMock()
        manager.__enter__.return_value = conn
        manager.__exit__.return_value = False

        with patch.object(links, "_conn", return_value=manager):
            with self.assertRaisesRegex(PermissionError, "active project"):
                links.extract(2, project_id=7)

    def test_extract_all_lists_only_the_active_projects_sources(self):
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [{"source_id": 2}]
        manager = MagicMock()
        manager.__enter__.return_value = conn
        manager.__exit__.return_value = False

        with patch.object(links, "_conn", return_value=manager), \
             patch.object(links, "extract", return_value={"references": 2, "links_to": 1,
                                                           "similar": 0}) as extract:
            self.assertEqual(links.extract_all(7), 3)

        self.assertIn("project_id = %s", conn.execute.call_args.args[0])
        self.assertEqual(conn.execute.call_args.args[1], (7,))
        extract.assert_called_once_with(2, project_id=7)


if __name__ == "__main__":
    unittest.main()
