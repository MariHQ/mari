from __future__ import annotations

import hashlib
import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException, Response
from starlette.requests import Request

from mari_server.identity import routes as auth


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


def request(*, query: bytes = b"", headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    return Request({"type": "http", "method": "POST", "path": "/auth/register",
                    "headers": headers or [], "query_string": query, "scheme": "https",
                    "server": ("test", 443), "client": ("127.0.0.1", 1)})


USER = {"id": 7, "name": "Invited", "email": "person@example.test", "role": "user",
        "initials": "I", "tint": 1, "provider": "manual", "invited_by": "Admin",
        "password_hash": "", "github_id": "", "google_id": "", "status": "active"}


class InvitationTests(unittest.TestCase):
    def setUp(self):
        auth._ATTEMPTS.clear()

    def test_issue_stores_only_hash_and_normalizes_email(self):
        def handler(sql, _args):
            if sql.startswith("SELECT * FROM users WHERE lower(email)"): return Result()
            if sql.startswith("SELECT * FROM users WHERE name"): return Result()
            if "INSERT INTO users" in sql: return Result({"id": 7})
            return Result()
        conn = FakeConn(handler)
        with patch.object(auth, "_conn", return_value=conn), \
             patch.object(auth.secrets, "token_urlsafe", return_value="invite-secret"), \
             patch.object(auth.config, "get", return_value="https://mari.example.test"), \
             patch("builtins.print"):
            token = auth.issue_invitation("Invited", " Person@Example.Test ", "user", "Admin")
        self.assertEqual(token, "invite-secret")
        insert = next((args for sql, args in conn.calls if "INSERT INTO invitation_tokens" in sql), None)
        self.assertIsNotNone(insert)
        self.assertEqual(insert[0], hashlib.sha256(b"invite-secret").hexdigest())
        self.assertEqual(insert[2], "person@example.test")
        self.assertNotIn("invite-secret", json.dumps(conn.calls))

    def test_existing_account_cannot_be_turned_into_an_invitation(self):
        active = {**USER, "password_hash": "already-set"}
        conn = FakeConn(lambda sql, _args: Result(active) if "lower(email)" in sql else Result())
        with patch.object(auth, "_conn", return_value=conn), self.assertRaisesRegex(ValueError, "already exists"):
            auth.issue_invitation("Invited", USER["email"], "admin", "Admin")
        self.assertFalse(any("invitation_tokens" in sql for sql, _ in conn.calls))

    def test_register_claims_only_matching_atomic_token(self):
        def handler(sql, _args):
            if "setup_complete" in sql: return Result({"exists": 1})
            if sql.startswith("UPDATE invitation_tokens"):
                return Result({"user_id": 7, "email_normalized": USER["email"]})
            if sql.startswith("SELECT * FROM users WHERE id"): return Result(USER)
            if sql.startswith("SELECT * FROM users WHERE id = %s"): return Result(USER)
            if sql.startswith("SELECT id FROM projects"): return Result(many=[])
            return Result()
        conn = FakeConn(handler)
        body = auth.Credentials(name="Claimant", email="Person@Example.Test",
                                password="correct horse", invite_token="invite-secret")
        with patch.object(auth, "_conn", return_value=conn), \
             patch.object(auth, "_hash", return_value="new-hash"), \
             patch.object(auth, "_create_session"):
            result = auth.register(body, request(), Response())
        self.assertEqual(result["user"]["id"], 7)
        claim_sql, claim_args = next((sql, args) for sql, args in conn.calls
                                     if sql.startswith("UPDATE invitation_tokens"))
        self.assertIn("used_at IS NULL", claim_sql)
        self.assertIn("expires_at > now()", claim_sql)
        self.assertEqual(claim_args[0], hashlib.sha256(b"invite-secret").hexdigest())

    def test_claiming_an_invitation_joins_with_the_invited_tier(self):
        # users.role is only a label; the project membership is what grants
        # anything, so the claim has to carry the invited tier across.
        for invited_role, membership_role in [("admin", "admin"), ("manager", "manager"), ("user", "member")]:
            with self.subTest(invited_role=invited_role):
                invited = {**USER, "role": invited_role}
                def handler(sql, _args):
                    if "setup_complete" in sql: return Result({"exists": 1})
                    if sql.startswith("UPDATE invitation_tokens"):
                        return Result({"user_id": 7, "email_normalized": USER["email"]})
                    if sql.startswith("SELECT * FROM users WHERE id"): return Result(invited)
                    if sql.startswith("SELECT id FROM projects"): return Result(many=[{"id": 3}])
                    return Result()
                conn = FakeConn(handler)
                body = auth.Credentials(name="Claimant", email=USER["email"],
                                        password="correct horse", invite_token="invite-secret")
                with patch.object(auth, "_conn", return_value=conn), \
                     patch.object(auth, "_hash", return_value="new-hash"), \
                     patch.object(auth, "_create_session"):
                    auth.register(body, request(), Response())
                join_sql, join_args = next((sql, args) for sql, args in conn.calls
                                           if sql.startswith("INSERT INTO project_members"))
                self.assertEqual(join_args, (3, 7, membership_role))
                self.assertIn("ON CONFLICT (project_id, user_id) DO NOTHING", join_sql)

    def test_open_registration_joins_as_a_member_whatever_the_row_says(self):
        def handler(sql, _args):
            if "setup_complete" in sql: return Result({"exists": 1})
            if sql.startswith("SELECT 1 FROM users"): return Result()
            if sql.startswith("SELECT * FROM users WHERE lower(email)"):
                return Result({**USER, "role": "admin", "invited_by": "", "password_hash": "new-hash"})
            if sql.startswith("SELECT id FROM projects"): return Result(many=[{"id": 3}])
            return Result()
        conn = FakeConn(handler)
        body = auth.Credentials(name="Newcomer", email="new@example.test", password="correct horse")
        with patch.object(auth, "_conn", return_value=conn), \
             patch.object(auth, "_hash", return_value="new-hash"), \
             patch.object(auth, "_create_session"), \
             patch.object(auth.config, "get",
                          side_effect=lambda *k, **kw: k[:2] == ("auth", "registration_enabled")):
            auth.register(body, request(), Response())
        join_args = next(args for sql, args in conn.calls if sql.startswith("INSERT INTO project_members"))
        self.assertEqual(join_args[2], "member")

    def test_missing_replayed_or_expired_invite_fails_closed(self):
        def no_claim(sql, _args):
            if "setup_complete" in sql: return Result({"exists": 1})
            if sql.startswith("UPDATE invitation_tokens"): return Result()
            return Result()
        body = auth.Credentials(name="Claimant", email=USER["email"],
                                password="correct horse", invite_token="spent")
        with patch.object(auth, "_conn", return_value=FakeConn(no_claim)), \
             self.assertRaises(HTTPException) as error:
            auth.register(body, request(), Response())
        self.assertEqual(error.exception.status_code, 403)

    def test_pending_legacy_invite_without_token_fails_closed(self):
        def handler(sql, _args):
            if "setup_complete" in sql: return Result({"exists": 1})
            if sql.startswith("SELECT 1 FROM users WHERE lower(email)"): return Result({"exists": 1})
            return Result()
        body = auth.Credentials(name="Claimant", email=USER["email"], password="correct horse")
        with patch.object(auth, "_conn", return_value=FakeConn(handler)), \
             self.assertRaisesRegex(HTTPException, "one-time link"):
            auth.register(body, request(), Response())


class PasswordTests(unittest.TestCase):
    def setUp(self):
        auth._ATTEMPTS.clear()

    def test_unknown_login_still_runs_password_verifier(self):
        conn = FakeConn(lambda _sql, _args: Result())
        body = auth.Credentials(email="missing@example.test", password="wrong")
        with patch.object(auth, "_conn", return_value=conn), \
             patch.object(auth, "_verify", return_value=False) as verify, \
             self.assertRaises(HTTPException):
            auth.login(body, request(), Response())
        verify.assert_called_once_with("wrong", auth._DUMMY_PASSWORD_HASH)

    def test_unimplemented_email_delivery_routes_are_absent(self):
        self.assertFalse(hasattr(auth, "magic_link"))
        self.assertFalse(hasattr(auth, "magic_consume"))

    def test_first_run_check_does_not_mint_or_log_a_credential(self):
        with patch.object(auth, "_warn_if_bypass_enabled") as warn, \
             patch.object(auth, "_conn") as connect:
            auth.first_run_check()
        warn.assert_called_once_with()
        connect.assert_not_called()

    def test_self_service_setup_serializes_the_first_owner_claim(self):
        owner = {**USER, "name": "Dana", "email": "dana@example.test", "role": "admin"}
        def handler(sql, _args):
            if sql.startswith("SELECT 1 FROM settings WHERE key = 'setup_complete'"):
                return Result()
            if sql.startswith("SELECT 1 FROM users WHERE lower(email)"):
                return Result()
            if sql.startswith("SELECT id FROM projects WHERE status = 'active'"):
                return Result({"id": 1})
            if sql.startswith("SELECT * FROM users WHERE lower(email)"):
                return Result(owner)
            return Result()
        conn = FakeConn(handler)
        body = auth.SetupIn(name="Dana", email="dana@example.test",
                            password="correct horse battery staple", workspace="Acme")
        with patch.object(auth, "_conn", return_value=conn), \
             patch.object(auth, "_hash", return_value="password-hash"), \
             patch.object(auth, "_join_single_project") as join, \
             patch.object(auth, "_create_session"):
            result = auth.setup(body, request(), Response())
        self.assertEqual(result["user"]["email"], "dana@example.test")
        statements = [sql for sql, _ in conn.calls]
        lock_at = next(i for i, sql in enumerate(statements) if "pg_advisory_xact_lock" in sql)
        check_at = next(i for i, sql in enumerate(statements) if "setup_complete" in sql and sql.startswith("SELECT"))
        self.assertLess(lock_at, check_at)
        self.assertFalse(any("setup_token" in sql for sql in statements))
        join.assert_called_once_with(conn, owner["id"], "owner")


class LegacyOauthTests(unittest.TestCase):
    def test_github_start_uses_normalized_public_callback(self):
        values = {"github_client_id": "client-id", "github_client_secret": "secret",
                  "oauth_redirect_base": "https://mari.example.test/"}
        with patch.object(auth.config, "get", side_effect=lambda _section, key, default=None: values.get(key, default)), \
             patch.object(auth.secrets, "token_urlsafe", return_value="state-token"):
            response = auth.oauth_start("github", request())
        self.assertIn("redirect_uri=https%3A%2F%2Fmari.example.test%2Fauth%2Fcallback%2Fgithub",
                      response.headers["location"])
        self.assertNotIn("localhost", response.headers["location"])

    def test_github_callback_lands_on_configured_public_app_url(self):
        class JsonResponse:
            def __init__(self, value): self.value = value
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self): return json.dumps(self.value).encode()

        values = {"github_client_id": "client-id", "github_client_secret": "secret",
                  "oauth_redirect_base": "https://api.mari.example.test/",
                  "app_url": "https://mari.example.test/"}
        callback_request = request(headers=[(b"cookie", b"mari_oauth_state=state-token")])
        with patch.object(auth.config, "get", side_effect=lambda _section, key, default=None: values.get(key, default)), \
             patch.object(auth.urllib.request, "urlopen", side_effect=[
                 JsonResponse({"access_token": "provider-token"}),
                 JsonResponse({"id": 42, "login": "person"}),
             ]), patch.object(auth, "_verified_oauth_email", return_value=("person@example.test", True)), \
             patch.object(auth, "_link_or_create_oauth_user", return_value=USER), \
             patch.object(auth, "_create_session"):
            response = auth.oauth_callback("github", "code", callback_request, "state-token")
        self.assertEqual(response.headers["location"], "https://mari.example.test/")

    def test_first_link_requires_provider_verified_email(self):
        conn = FakeConn(lambda _sql, _args: Result())
        with patch.object(auth, "_conn", return_value=conn), \
             self.assertRaisesRegex(HTTPException, "provider-verified email"):
            auth._link_or_create_oauth_user("google", "new-sub", "Person",
                                            "person@example.test", False)

    def test_provider_subject_is_immutable(self):
        existing = {**USER, "google_id": "", "github_id": ""}
        def handler(sql, _args):
            if "WHERE ei.provider" in sql: return Result()
            if "WHERE google_id" in sql: return Result()
            if "lower(email)" in sql: return Result(existing)
            if "SELECT subject FROM external_identities" in sql: return Result({"subject": "original"})
            return Result()
        with patch.object(auth, "_conn", return_value=FakeConn(handler)), \
             self.assertRaisesRegex(HTTPException, "already linked"):
            auth._link_or_create_oauth_user("google", "attacker", "Person",
                                            "PERSON@example.test", True)

    def test_verified_email_cannot_reactivate_a_disabled_account(self):
        disabled = {**USER, "status": "disabled", "google_id": "", "github_id": ""}
        def handler(sql, _args):
            if "WHERE ei.provider" in sql or "WHERE google_id" in sql: return Result()
            if "lower(email)" in sql: return Result(disabled)
            return Result()
        with patch.object(auth, "_conn", return_value=FakeConn(handler)), \
             self.assertRaisesRegex(HTTPException, "deactivated"):
            auth._link_or_create_oauth_user("google", "new-sub", "Person",
                                            "person@example.test", True)

    def _oauth_handler(self, by_email, ext_id="new-sub"):
        def handler(sql, _args):
            if "WHERE ei.provider" in sql or "WHERE google_id" in sql: return Result()
            if "SELECT * FROM users WHERE lower(email)" in sql: return Result(by_email)
            if "SELECT subject FROM external_identities" in sql: return Result()
            if "SELECT user_id, subject FROM external_identities" in sql:
                return Result({"user_id": 7, "subject": ext_id})
            if "INSERT INTO users" in sql: return Result({**USER, "role": "user", "invited_by": ""})
            if sql.startswith("SELECT id FROM projects"): return Result(many=[{"id": 3}])
            return Result()
        return handler

    def test_oauth_claim_of_a_pending_invitation_joins_with_the_invited_tier(self):
        for invited_role, membership_role in [("admin", "admin"), ("manager", "manager"), ("user", "member")]:
            with self.subTest(invited_role=invited_role):
                pending = {**USER, "role": invited_role, "google_id": "", "github_id": ""}
                conn = FakeConn(self._oauth_handler(pending))
                with patch.object(auth, "_conn", return_value=conn):
                    auth._link_or_create_oauth_user("google", "new-sub", "Person",
                                                    "person@example.test", True)
                join_args = next(args for sql, args in conn.calls
                                 if sql.startswith("INSERT INTO project_members"))
                self.assertEqual(join_args, (3, 7, membership_role))

    def test_oauth_link_to_a_claimed_account_or_fresh_signup_joins_as_a_member(self):
        # A row that already holds a credential is not an invitation, whatever
        # users.role says; nor is a brand-new sign-up.
        claimed = {**USER, "role": "admin", "password_hash": "set", "google_id": "", "github_id": ""}
        for by_email in (claimed, None):
            with self.subTest(existing=bool(by_email)):
                conn = FakeConn(self._oauth_handler(by_email))
                with patch.object(auth, "_conn", return_value=conn):
                    auth._link_or_create_oauth_user("google", "new-sub", "Person",
                                                    "person@example.test", True)
                join_args = next(args for sql, args in conn.calls
                                 if sql.startswith("INSERT INTO project_members"))
                self.assertEqual(join_args[2], "member")

    def test_google_requires_strict_verified_flag(self):
        self.assertEqual(auth._verified_oauth_email(
            "google", {"email": " Person@Example.Test ", "verified_email": True}, "token"),
            ("person@example.test", True))
        self.assertEqual(auth._verified_oauth_email(
            "google", {"email": "person@example.test", "verified_email": "true"}, "token"),
            ("person@example.test", False))

    def test_github_uses_only_verified_email_endpoint_records(self):
        class Context:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self):
                return json.dumps([
                    {"email": "unverified@example.test", "primary": True, "verified": False},
                    {"email": "Verified@Example.Test", "primary": False, "verified": True},
                ]).encode()
        with patch.object(auth.urllib.request, "urlopen", return_value=Context()):
            self.assertEqual(auth._verified_oauth_email("github", {}, "token"),
                             ("verified@example.test", True))


class ForwardedHeaderTests(unittest.TestCase):
    """X-Forwarded-For and X-Forwarded-Proto are only believed from a peer in
    server.trusted_proxies. The rate limiter always checked; the cookie's
    Secure flag and the access log's IP column did not.

    Proxies APPEND (nginx's $proxy_add_x_forwarded_for, API Gateway), so the
    chain reads "whatever the client sent..., client, inner proxy" and the
    trustworthy value is found by walking from the right past the hops that
    are proxies themselves. The first version of this check took the leftmost
    value, which is the one the client wrote."""

    @staticmethod
    def forwarded(xff: bytes, proto: bytes = b"https") -> Request:
        # Peer 127.0.0.1 stands in for the nginx or Lambda Web Adapter in front.
        return Request({"type": "http", "method": "GET", "path": "/auth/me",
                        "headers": [(b"x-forwarded-for", xff), (b"x-forwarded-proto", proto)],
                        "query_string": b"", "scheme": "http", "server": ("test", 80),
                        "client": ("127.0.0.1", 1)})

    @classmethod
    def proxied(cls) -> Request:
        # Client 203.0.113.9, through proxy 10.0.0.1, through the peer.
        return cls.forwarded(b"203.0.113.9, 10.0.0.1")

    def test_forwarded_headers_are_ignored_from_an_untrusted_peer(self):
        with patch.object(auth.config, "get", return_value=[]):
            request = self.proxied()
            self.assertEqual(auth._client_ip(request), "127.0.0.1")
            self.assertFalse(auth._is_https(request))
            self.assertEqual(auth._client_detail(request)[0]["value"], "127.0.0.1")

    def test_forwarded_headers_are_honoured_from_a_configured_proxy(self):
        with patch.object(auth.config, "get", return_value=["127.0.0.1", "10.0.0.1"]):
            request = self.proxied()
            self.assertEqual(auth._client_ip(request), "203.0.113.9")
            self.assertTrue(auth._is_https(request))
            self.assertEqual(auth._client_detail(request)[0]["value"], "203.0.113.9")

    def test_an_unlisted_inner_hop_is_the_client(self):
        # Only the peer is trusted, so 10.0.0.1 is what the peer saw the
        # request come from: it IS the client as far as we can tell, and
        # 203.0.113.9 to its left is just a header value someone sent.
        with patch.object(auth.config, "get", return_value=["127.0.0.1"]):
            self.assertEqual(auth._client_ip(self.proxied()), "10.0.0.1")

    def test_a_client_supplied_leading_value_is_ignored_behind_nginx(self):
        # The client sent "X-Forwarded-For: 1.2.3.4" hoping to pick its own
        # rate-limit bucket and access-log line; nginx appended the address
        # it actually saw. The appended one wins.
        with patch.object(auth.config, "get", return_value=["127.0.0.1"]):
            request = self.forwarded(b"1.2.3.4, 203.0.113.9")
            self.assertEqual(auth._client_ip(request), "203.0.113.9")
            self.assertEqual(auth._client_detail(request)[0]["value"], "203.0.113.9")

    def test_a_single_hop_still_works(self):
        with patch.object(auth.config, "get", return_value=["127.0.0.1"]):
            self.assertEqual(auth._client_ip(self.forwarded(b"203.0.113.9")), "203.0.113.9")
            self.assertEqual(auth._client_ip(self.forwarded(b" 203.0.113.9 ")), "203.0.113.9")

    def test_wildcard_takes_the_rightmost_value(self):
        # "*" cannot tell hops apart, so it takes what the nearest proxy
        # wrote and nothing further left, however plausible it looks.
        with patch.object(auth.config, "get", return_value=["*"]):
            self.assertEqual(auth._client_ip(self.forwarded(b"1.2.3.4, 203.0.113.9")), "203.0.113.9")
            self.assertEqual(auth._client_ip(self.forwarded(b"203.0.113.9")), "203.0.113.9")
            # A rightmost value that is not an address is not replaced by the
            # client-controlled one beside it: fall back to the peer.
            self.assertEqual(auth._client_ip(self.forwarded(b"203.0.113.9, unknown")), "127.0.0.1")

    def test_malformed_entries_are_skipped(self):
        with patch.object(auth.config, "get", return_value=["127.0.0.1", "10.0.0.0/8"]):
            request = self.forwarded(b"1.2.3.4, garbage, 203.0.113.9, unknown, 10.0.0.7")
            self.assertEqual(auth._client_ip(request), "203.0.113.9")
            self.assertEqual(auth._client_ip(self.forwarded(b"nonsense, , unknown")), "127.0.0.1")
            self.assertEqual(auth._client_ip(self.forwarded(b"")), "127.0.0.1")

    def test_a_chain_of_only_proxies_falls_back_to_the_peer(self):
        with patch.object(auth.config, "get", return_value=["127.0.0.1", "10.0.0.0/8"]):
            self.assertEqual(auth._client_ip(self.forwarded(b"10.0.0.1, 10.0.0.2")), "127.0.0.1")

    def test_forwarded_proto_is_the_nearest_proxys(self):
        with patch.object(auth.config, "get", return_value=["127.0.0.1"]):
            self.assertTrue(auth._is_https(self.forwarded(b"203.0.113.9", b"http, https")))
            self.assertFalse(auth._is_https(self.forwarded(b"203.0.113.9", b"https, http")))
            self.assertTrue(auth._is_https(self.forwarded(b"203.0.113.9", b"HTTPS ")))

    def test_a_range_or_wildcard_names_a_proxy_with_a_dynamic_address(self):
        # The chart's nginx is a pod with whatever address the cluster gave
        # it, so the trust list takes a CIDR, and "*" when nothing but the
        # proxy can reach the port at all.
        with patch.object(auth.config, "get", return_value=["10.0.0.0/8"]):
            self.assertFalse(auth._is_https(self.proxied()))
        with patch.object(auth.config, "get", return_value=["127.0.0.0/8", "10.0.0.0/8"]):
            self.assertTrue(auth._is_https(self.proxied()))
            self.assertEqual(auth._client_ip(self.proxied()), "203.0.113.9")
        with patch.object(auth.config, "get", return_value=["*"]):
            self.assertTrue(auth._is_https(self.proxied()))
        with patch.object(auth.config, "get", return_value=["not-a-range/x", "10.1.2.3"]):
            self.assertFalse(auth._is_https(self.proxied()))

    def test_direct_https_needs_no_proxy(self):
        with patch.object(auth.config, "get", return_value=[]):
            self.assertTrue(auth._is_https(request()))


if __name__ == "__main__":
    unittest.main()
