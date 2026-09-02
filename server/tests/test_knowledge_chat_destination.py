from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from mari_server import settings
from mari_server.destinations import chat as chat_api, graphql as graphql_destinations
from mari_server.conversations import chat as conversation_chat
from mari_server.identity import routes as auth
from mari_server.persistence.postgres import chat as chat_store
from mari_components.destinations import knowledge_chat


def info(project_id: int = 7):
    return SimpleNamespace(context={"access": SimpleNamespace(project_id=project_id), "user": {"role": "admin"}})


def request(ip: str = "203.0.113.9"):
    return SimpleNamespace(client=SimpleNamespace(host=ip), headers={})


ROW = {"id": 2, "project_id": 7, "project_slug": "acme", "project_name": "Acme",
       "name": "Company KB", "slug": "company-kb", "title": "Ask Acme", "welcome": "Welcome",
       "tools": ["search"]}


class KnowledgeChatDestinationTests(unittest.TestCase):
    def test_terse_entity_prompt_is_not_rewritten_for_retrieval_or_generation(self):
        project = SimpleNamespace(project_id=7, user_id=0)
        document = {"id": 4, "title": "Mari README", "source": "github",
                    "body": "Mari manages product knowledge.", "snippet": "Mari manages"}
        with patch.object(conversation_chat.chat_store, "create_session", return_value=9), \
             patch.object(conversation_chat.chat_store, "add_message"), \
             patch.object(conversation_chat, "select_workflow", return_value=None), \
             patch.object(conversation_chat.chat_store, "messages", return_value=[
                 {"role": "user", "content": "mari"},
             ]), patch.object(conversation_chat, "hybrid_search", return_value=[document]) as search:
            context = conversation_chat.ports(project, "test", frozenset({"search"})).prepare(
                None, "mari",
            )
        search.assert_called_once_with("mari", 8)
        self.assertIn("Question: mari", context.messages[-1]["content"])

    def test_create_validates_slug_and_calls_application_port(self):
        ports = knowledge_chat.KnowledgeChatPorts(
            create=lambda project, name, slug, title, welcome, tools: 12,
            update=lambda *_args: False,
            deploy=lambda *_args: None,
            audit=lambda *_args: None,
        )
        with patch.object(graphql_destinations, "_require_admin"), \
             patch.object(graphql_destinations.knowledge_chat_repository, "ports", return_value=ports):
            mut = graphql_destinations.DestinationMutations()
            self.assertEqual(
                mut.create_knowledge_chat_destination(
                    info(), "Company KB", "company-kb", "Ask Acme", ["search"], "Welcome",
                ),
                12,
            )
            with self.assertRaisesRegex(ValueError, "URL slug"):
                mut.create_knowledge_chat_destination(
                    info(), "Company KB", "Bad Slug", "Ask Acme", ["search"], "",
                )

    def test_update_and_deploy_cannot_cross_projects(self):
        ports = knowledge_chat.KnowledgeChatPorts(
            create=lambda *_args: 1,
            update=lambda *_args: False,
            deploy=lambda *_args: None,
            audit=lambda *_args: None,
        )
        with patch.object(graphql_destinations, "_require_admin"), \
             patch.object(graphql_destinations.knowledge_chat_repository, "ports", return_value=ports):
            mut = graphql_destinations.DestinationMutations()
            self.assertFalse(mut.update_knowledge_chat_destination(info(7), 99, "Name", "Title", "Welcome", ["search"]))
            with self.assertRaisesRegex(ValueError, "not found"):
                mut.deploy_knowledge_chat_destination(info(7), 99)

    def test_deploy_returns_real_destination_url_and_audits(self):
        audits = []
        ports = knowledge_chat.KnowledgeChatPorts(
            create=lambda *_args: 1,
            update=lambda *_args: False,
            deploy=lambda project, destination: ("Company KB", "/knowledge-chat/acme/company-kb"),
            audit=lambda verb, target: audits.append((verb, target)),
        )
        with patch.object(graphql_destinations, "_require_admin"), \
             patch.object(graphql_destinations.knowledge_chat_repository, "ports", return_value=ports):
            result = graphql_destinations.DestinationMutations().deploy_knowledge_chat_destination(info(), 12)
        self.assertEqual(result, "/knowledge-chat/acme/company-kb")
        self.assertEqual(audits, [("deployed knowledge chat", "Company KB")])

    def test_live_config_is_public_but_only_resolves_live_destination(self):
        row = {"id": 2, "project_id": 7, "project_slug": "acme", "project_name": "Acme",
               "name": "Company KB", "slug": "company-kb", "title": "Ask Acme", "welcome": "Welcome",
               "tools": ["search"]}
        with patch.object(chat_api, "live_destination", return_value=row):
            self.assertEqual(chat_api.destination("acme", "company-kb")["title"], "Ask Acme")
        with patch.object(chat_api, "live_destination", return_value=None):
            with self.assertRaises(HTTPException) as caught:
                chat_api.destination("acme", "company-kb")
            self.assertEqual(caught.exception.status_code, 404)

    def test_public_chat_uses_destination_scoped_read_access(self):
        sentinel = object()
        with patch.object(chat_api, "live_destination", return_value=ROW), \
             patch.object(chat_api, "_throttle"), \
             patch.object(chat_api, "_sse", return_value=sentinel) as answer:
            result = chat_api.public_chat("acme", "company-kb", chat_api.ChatIn(message="policy"), request())
        self.assertIs(result, sentinel)
        destination_access = answer.call_args.args[0]
        self.assertEqual(destination_access.project_id, 7)
        self.assertEqual(destination_access.principal_type, "knowledge_chat")
        self.assertEqual(destination_access.capabilities, frozenset({"knowledge.read"}))
        self.assertEqual(answer.call_args.args[2], "knowledge_chat:2")
        self.assertEqual(answer.call_args.args[3], frozenset({"search"}))


class PublicChatThrottleTests(unittest.TestCase):
    """The published chat answers without a sign-in, so it is the one surface
    that needs brakes of its own: per-IP and per-destination windows, plus a
    daily budget read back from usage_log."""

    def setUp(self) -> None:
        auth._ATTEMPTS.clear()
        self.limits = patch.dict(settings.CONFIG["knowledge_chat"],
                                 {"ip_per_minute": 2, "destination_per_minute": 3, "daily_budget": 10})
        self.limits.start()

    def tearDown(self) -> None:
        self.limits.stop()
        auth._ATTEMPTS.clear()

    def call(self, ip: str = "203.0.113.9"):
        with patch.object(chat_api, "live_destination", return_value=ROW), \
             patch.object(chat_api, "answers_since", return_value=0), \
             patch.object(chat_api, "_sse", return_value="answered"):
            return chat_api.public_chat("acme", "company-kb", chat_api.ChatIn(message="policy"), request(ip))

    def test_one_ip_is_throttled_after_its_window_fills(self):
        self.assertEqual(self.call(), "answered")
        self.assertEqual(self.call(), "answered")
        with self.assertRaises(HTTPException) as caught:
            self.call()
        self.assertEqual(caught.exception.status_code, 429)
        self.assertIn("Retry-After", caught.exception.headers)

    def test_destination_window_holds_across_ips(self):
        for ip in ("203.0.113.1", "203.0.113.2", "203.0.113.3"):
            self.assertEqual(self.call(ip), "answered")
        with self.assertRaises(HTTPException) as caught:
            self.call("203.0.113.4")
        self.assertEqual(caught.exception.status_code, 429)

    def test_daily_budget_counts_the_usage_log_and_stops_at_the_budget(self):
        with patch.object(chat_api, "live_destination", return_value=ROW), \
             patch.object(chat_api, "answers_since", return_value=10) as counted, \
             patch.object(chat_api, "_sse", return_value="answered") as answer:
            with self.assertRaises(HTTPException) as caught:
                chat_api.public_chat("acme", "company-kb", chat_api.ChatIn(message="policy"), request())
        self.assertEqual(caught.exception.status_code, 429)
        self.assertIn("daily limit", caught.exception.detail)
        counted.assert_called_once_with(7, "knowledge_chat:2", 24)
        answer.assert_not_called()

    def test_zero_switches_a_limit_off(self):
        with patch.dict(settings.CONFIG["knowledge_chat"],
                        {"ip_per_minute": 0, "destination_per_minute": 0, "daily_budget": 0}), \
             patch.object(chat_api, "answers_since") as counted:
            for _ in range(5):
                self.assertEqual(self.call(), "answered")
        counted.assert_not_called()


class PublicSessionTests(unittest.TestCase):
    """A public session id is sequential, so the id alone must not continue a
    conversation. The token rides inside the session_id the widget already
    echoes, which keeps a deployed widget working without a change."""

    def setUp(self) -> None:
        self.visitor = SimpleNamespace(project_id=7, user_id=0)
        self.member = SimpleNamespace(project_id=7, user_id=3)

    def test_a_new_visitor_gets_a_tokened_reference_and_a_tokened_row(self):
        with patch.object(chat_store, "create_session", return_value=9) as create, \
             patch.object(chat_store, "public_session_exists") as exists:
            row, ref = conversation_chat.resolve_session(self.visitor, None, "hello")
        self.assertEqual(row, 9)
        token = create.call_args.kwargs["public_token"]
        self.assertGreaterEqual(len(token), 32)
        self.assertEqual(ref, f"9.{token}")
        self.assertEqual(create.call_args.args, (7, None, "hello"))
        exists.assert_not_called()

    def test_a_visitor_continues_only_with_the_matching_token(self):
        with patch.object(chat_store, "public_session_exists", return_value=True) as exists, \
             patch.object(chat_store, "create_session") as create:
            self.assertEqual(conversation_chat.resolve_session(self.visitor, "9.tok", "hi"), (9, "9.tok"))
        exists.assert_called_once_with(7, 9, "tok")
        create.assert_not_called()
        with patch.object(chat_store, "public_session_exists", return_value=False), \
             patch.object(chat_store, "create_session") as create:
            with self.assertRaises(LookupError):
                conversation_chat.resolve_session(self.visitor, "9.wrong", "hi")
        create.assert_not_called()

    def test_a_bare_id_from_a_visitor_starts_a_fresh_session_not_someone_elses(self):
        with patch.object(chat_store, "create_session", return_value=12) as create, \
             patch.object(chat_store, "session_exists") as owned, \
             patch.object(chat_store, "public_session_exists") as public:
            row, ref = conversation_chat.resolve_session(self.visitor, 9, "hi")
        self.assertEqual(row, 12)
        self.assertTrue(ref.startswith("12."))
        owned.assert_not_called()
        public.assert_not_called()
        create.assert_called_once()

    def test_garbage_references_are_a_missing_session(self):
        for value in ("abc", "-1", "9.tok.extra"[::-1], ""):
            with self.assertRaises(LookupError):
                conversation_chat.resolve_session(self.visitor, value, "hi")

    def test_a_member_keeps_plain_ids_and_never_takes_a_tokened_reference(self):
        with patch.object(chat_store, "session_exists", return_value=True) as owned:
            self.assertEqual(conversation_chat.resolve_session(self.member, 9, "hi"), (9, 9))
        owned.assert_called_once_with(7, 3, 9)
        with patch.object(chat_store, "session_exists", return_value=False):
            with self.assertRaises(LookupError):
                conversation_chat.resolve_session(self.member, 9, "hi")
        with patch.object(chat_store, "public_session_exists") as public:
            with self.assertRaises(LookupError):
                conversation_chat.resolve_session(self.member, "9.tok", "hi")
        public.assert_not_called()
        with patch.object(chat_store, "create_session", return_value=4) as create:
            self.assertEqual(conversation_chat.resolve_session(self.member, None, "hi"), (4, 4))
        self.assertEqual(create.call_args.args, (7, 3, "hi"))

    def test_the_answer_is_persisted_against_the_row_not_the_reference(self):
        with patch.object(chat_store, "add_message") as add, \
             patch.object(conversation_chat.trajectory_store, "harvest") as harvest, \
             patch.object(conversation_chat, "answer_system", return_value=""), \
             patch.object(conversation_chat, "workspace_style_text", return_value=""):
            ports = conversation_chat.ports(self.visitor, "test", frozenset())
            ports.persist("9.tok", "answer", [])
            ports.observe("9.tok", "question", [], False)
        self.assertEqual(add.call_args.args[:3], (7, 9, "assistant"))
        self.assertEqual(harvest.call_args.args[0], 9)

    def test_store_queries_never_attach_a_member_to_an_ownerless_row(self):
        class Result:
            def fetchone(self):
                return None

        class Connection:
            calls: list = []

            def execute(self, sql, args):
                self.calls.append((" ".join(sql.split()), args))
                return Result()

        connection = Connection()
        with patch.object(chat_store.db, "connect", return_value=nullcontext(connection)):
            self.assertFalse(chat_store.session_exists(7, 3, 9))
            self.assertFalse(chat_store.public_session_exists(7, 9, "tok"))
        owned, public = connection.calls
        self.assertNotIn("IS NULL", owned[0])
        self.assertIn("owner_user_id = %s", owned[0])
        self.assertIn("owner_user_id IS NULL AND public_token IS NOT NULL AND public_token = %s", public[0])
        self.assertEqual(public[1], (9, 7, "tok"))


if __name__ == "__main__":
    unittest.main()
