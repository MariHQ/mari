"""Hybrid search keeps a ranking in memory, not the corpus, and selects
keyword candidates through the tsvector index."""

from __future__ import annotations

import datetime as dt
import unittest
from unittest.mock import patch

from mari_server.identity import access
from mari_server.persistence.postgres import search as search_repository
from mari_server.search import service as search_service


def context(project_id: int = 7) -> access.AccessContext:
    return access.AccessContext(
        user_id=1, project_id=project_id, project_slug="acme", project_name="Acme",
        role="admin", capabilities=access.CAPABILITIES)


def row(doc_id: int, title: str, body: str = "", updated: dt.date | None = None) -> dict:
    return {"id": doc_id, "source": "docs", "title": title, "snippet": "deploy", "body": body,
            "author": "", "author_initials": "", "updated_src": updated, "kind": "page",
            "tags": [], "boost": 1, "acl_visibility": "project", "acl_principals": []}


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


class RankCacheTests(unittest.TestCase):
    def tearDown(self):
        access.set_access(None)
        search_service._vec_cache.clear()
        search_service._rank_cache.clear()

    def _patches(self, rows, reread=None):
        reread = reread if reread is not None else rows
        return (
            patch.object(search_service.llm, "embed", return_value=None),
            patch.object(search_service.search_store, "keyword_candidates",
                         side_effect=lambda *_args: [dict(r) for r in rows]),
            patch.object(search_service.search_store, "documents_by_id",
                         side_effect=lambda _project_id, ids: [dict(r) for r in reread if r["id"] in ids]),
        )

    def test_cache_holds_ranking_entries_and_a_hit_rereads_the_page_by_id(self):
        rows = [row(1, "Deploy runbook", "a long body " * 50), row(2, "Deploy notes")]
        embed, candidates, by_id = self._patches(rows)
        with access.use_access(context()), embed, candidates as ranked, by_id as reread:
            first = search_service.hybrid_search("deploy")
            reread.assert_not_called()
            second = search_service.hybrid_search("deploy")
        # the ranking pass ran once; the second page came from the store by id
        self.assertEqual(ranked.call_count, 1)
        reread.assert_called_once_with(7, [r["id"] for r in first])
        self.assertEqual([r["title"] for r in first], [r["title"] for r in second])
        self.assertTrue(all("score" in r and "boost" not in r for r in second))
        cached = [entry for _at, entries in search_service._rank_cache.values() for entry in entries]
        self.assertEqual(len(cached), 2)
        self.assertEqual({key for entry in cached for key in entry}, {"id", "score", "updated_src"})

    def test_count_and_page_describe_the_same_ranking_and_pass_the_query_to_the_store(self):
        rows = [row(1, "Deploy runbook"), row(2, "Deploy notes")]
        embed, candidates, by_id = self._patches(rows)
        with access.use_access(context()), embed, candidates as ranked, by_id, \
             patch.object(search_service.search_store, "document_count", return_value=1) as count:
            self.assertEqual(search_service.hybrid_count("deploy"), 2)
            self.assertEqual(len(search_service.hybrid_search("deploy")), 2)
        count.assert_called_once_with(7, "deploy")
        self.assertEqual(ranked.call_args.args, (7, "deploy", search_service.MAX_K * 2))

    def test_freshness_window_filters_the_cached_ranking(self):
        today = dt.date.today()
        rows = [row(1, "Fresh deploy", updated=today), row(2, "Old deploy", updated=today - dt.timedelta(days=40))]
        embed, candidates, by_id = self._patches(rows)
        with access.use_access(context()), embed, candidates, by_id:
            self.assertEqual([r["title"] for r in search_service.hybrid_search("deploy", days=7)], ["Fresh deploy"])
            self.assertEqual(search_service.hybrid_count("deploy", days=7), 1)
            self.assertEqual(search_service.hybrid_count("deploy", days=90), 2)

    def test_document_gone_since_ranking_is_skipped_not_served_as_a_hole(self):
        rows = [row(1, "Deploy runbook"), row(2, "Deleted since")]
        embed, candidates, by_id = self._patches(rows, reread=rows[:1])
        with access.use_access(context()), embed, candidates, by_id:
            search_service.hybrid_search("deploy")
            page = search_service.hybrid_search("deploy")
        self.assertEqual([r["title"] for r in page], ["Deploy runbook"])


class KeywordPredicateTests(unittest.TestCase):
    @staticmethod
    def conn(usable: bool) -> FakeConn:
        def handler(sql, _args):
            if sql.startswith("SELECT numnode("):
                return Result({"usable": usable})
            return Result({"n": 0}, [])
        return FakeConn(handler)

    def test_search_text_ors_the_scoring_vocabulary_as_prefixes(self):
        # Each term carries the prefix operator so "auth" still admits
        # "authentication" and "k8s" still admits "k8s-migration", the
        # substring hits the ILIKE needles used to give.
        self.assertEqual(search_repository.search_text("How long are customer records retained?"),
                         "long:* | customer:* | records:* | retained:*")
        self.assertEqual(search_repository.search_text("AI"), "AI:*")

    def test_prefix_tsquery_strips_what_to_tsquery_would_choke_on(self):
        build = search_repository.prefix_tsquery
        # Hyphens and underscores are parser-safe (checked on Postgres 16).
        self.assertEqual(build(["k8s-migration"]), "k8s-migration:*")
        self.assertEqual(build(["snake_case"]), "snake_case:*")
        # Quotes, operators, parentheses and colons are dropped, not escaped.
        self.assertEqual(build(["foo'bar"]), "foobar:*")
        self.assertEqual(build(["a & b", "(c)", "d:*", "e|f"]), "ab:* | c:* | d:* | ef:*")
        # A term with nothing left after cleaning disappears; so do repeats
        # and a leading hyphen that would put punctuation first.
        self.assertEqual(build(["!!", "''", "--foo", "foo"]), "foo:*")
        self.assertEqual(build([]), "")

    def test_search_text_with_no_usable_token_is_empty_and_skips_the_parser(self):
        self.assertEqual(search_repository.search_text("!!! ???"), "")
        # One-letter tokens never become prefixes: "a:*" would admit most of
        # the corpus. Such a query keeps the literal-substring path.
        self.assertEqual(search_repository.search_text("a & b"), "")
        conn = self.conn(usable=True)
        with patch.object(search_repository.db, "connect", return_value=conn):
            search_repository.keyword_candidates(7, "!!! ???", 10)
        self.assertEqual(len(conn.calls), 1)
        sql, args = conn.calls[-1]
        self.assertIn("d.body ILIKE needle", sql)
        self.assertEqual(args, (7, ["%!!! ???%"], 10))

    def test_candidates_come_from_the_tsvector_index(self):
        conn = self.conn(usable=True)
        with patch.object(search_repository.db, "connect", return_value=conn):
            search_repository.keyword_candidates(7, "How long are customer records retained?", 10)
        sql, args = conn.calls[-1]
        self.assertIn("d.search_vec @@ to_tsquery('english', %s)", sql)
        self.assertNotIn("websearch_to_tsquery", sql)
        self.assertNotIn("ILIKE", sql)
        self.assertEqual(args, (7, "long:* | customer:* | records:* | retained:*", 10))

    def test_text_the_parser_reduces_to_nothing_falls_back_to_ilike(self):
        conn = self.conn(usable=False)
        with patch.object(search_repository.db, "connect", return_value=conn):
            search_repository.keyword_candidates(7, "about", 10)
        sql, args = conn.calls[-1]
        self.assertIn("d.body ILIKE needle", sql)
        self.assertNotIn("search_vec", sql)
        self.assertEqual(args, (7, ["%about%"], 10))

    def test_count_shares_the_candidate_predicate(self):
        conn = self.conn(usable=True)
        with patch.object(search_repository.db, "connect", return_value=conn):
            search_repository.document_count(7, "deploy")
            search_repository.document_count(7, None)
        filtered, unfiltered = conn.calls[1], conn.calls[2]
        self.assertIn("d.search_vec @@ to_tsquery('english', %s)", filtered[0])
        self.assertEqual(filtered[1], (7, "deploy:*"))
        self.assertNotIn("search_vec", unfiltered[0])
        self.assertEqual(unfiltered[1], (7,))
        self.assertEqual(len(conn.calls), 3)


if __name__ == "__main__":
    unittest.main()
