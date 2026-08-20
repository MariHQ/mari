from __future__ import annotations

import hashlib
import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException, Response
from starlette.requests import Request

import auth


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


class PasswordAndMagicTests(unittest.TestCase):
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

    def test_magic_link_is_consumed_by_one_conditional_update(self):
        conn = FakeConn(lambda sql, _args: Result({"user_id": 9})
                        if sql.startswith("UPDATE magic_links") else Result())
        with patch.object(auth, "_conn", return_value=conn), patch.object(auth, "_create_session"):
            response = auth.magic_consume("once", request())
        self.assertEqual(response.status_code, 303)
        statements = [sql for sql, _ in conn.calls]
        self.assertEqual(len(statements), 1)
        self.assertIn("used_at IS NULL", statements[0])
        self.assertIn("RETURNING user_id", statements[0])


class LegacyOauthTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
