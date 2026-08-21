from __future__ import annotations

import json
import time
import unittest
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from jwt.algorithms import RSAAlgorithm
from starlette.requests import Request

from mari_server.identity import enterprise as ent


class Result:
    def __init__(self, one=None, many=None): self.one, self.many = one, many or []
    def fetchone(self): return self.one
    def fetchall(self): return self.many


class FakeConn:
    def __init__(self, handler): self.handler, self.calls = handler, []
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def execute(self, sql, args=()):
        self.calls.append((" ".join(sql.split()), args))
        return self.handler(sql, args)


def request_with_cookies(**cookies):
    raw = "; ".join(f"{k}={v}" for k, v in cookies.items()).encode()
    return Request({"type": "http", "method": "GET", "path": "/", "headers": [(b"cookie", raw)],
                    "query_string": b"", "scheme": "https", "server": ("test", 443),
                    "client": ("127.0.0.1", 1)})


class OidcTokenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.jwk = json.loads(RSAAlgorithm.to_jwk(cls.key.public_key())); cls.jwk["kid"] = "one"
        cls.doc = {"issuer": "https://id.example.test", "jwks_uri": "https://id.example.test/keys"}

    def token(self, **overrides):
        now = int(time.time())
        claims = {"iss": self.doc["issuer"], "aud": "mari-client", "sub": "subject-1",
                  "iat": now, "exp": now + 300, "nonce": "nonce", "email": "a@example.test",
                  "email_verified": True}
        claims.update(overrides)
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "one"})

    def verify(self, token, nonce="nonce"):
        with patch.object(ent.config, "get", return_value="mari-client"):
            return ent.verify_id_token(token, self.doc, nonce, {"keys": [self.jwk]})

    def test_valid_token_binds_signature_issuer_audience_expiry_and_nonce(self):
        self.assertEqual(self.verify(self.token())["sub"], "subject-1")

    def test_forged_signature_is_rejected(self):
        forged = jwt.encode(jwt.decode(self.token(), options={"verify_signature": False}), self.other,
                            algorithm="RS256", headers={"kid": "one"})
        with self.assertRaisesRegex(HTTPException, "token verification"):
            self.verify(forged)

    def test_expired_and_wrong_audience_are_rejected(self):
        for token in (self.token(exp=int(time.time()) - 60), self.token(aud="another-client")):
            with self.subTest():
                with self.assertRaises(HTTPException): self.verify(token)

    def test_wrong_nonce_is_rejected(self):
        with self.assertRaisesRegex(HTTPException, "nonce mismatch"):
            self.verify(self.token(), "different")

    def test_callback_rejects_state_before_exchange(self):
        request = request_with_cookies(**{ent.OIDC_STATE: "expected"})
        with self.assertRaisesRegex(HTTPException, "state mismatch"):
            ent.oidc_callback(request, "code", "forged")

    def test_start_uses_discovery_pkce_state_and_nonce(self):
        document = {**self.doc, "authorization_endpoint": "https://id.example.test/authorize",
                    "token_endpoint": "https://id.example.test/token"}
        values = {"oidc_client_id": "mari-client", "oidc_scopes": "openid email groups",
                  "oauth_redirect_base": "https://mari.example.test"}
        with patch.object(ent, "discovery", return_value=document), \
             patch.object(ent.config, "get", side_effect=lambda _s, key, default="": values.get(key, default)):
            response = ent.oidc_start(request_with_cookies())
        self.assertIn("code_challenge_method=S256", response.headers["location"])
        self.assertIn("state=", response.headers["location"])
        cookies = response.headers.getlist("set-cookie")
        self.assertTrue(any(ent.OIDC_STATE in value and "HttpOnly" in value for value in cookies))
        self.assertTrue(any(ent.OIDC_NONCE in value and "HttpOnly" in value for value in cookies))


class AccountLinkingTests(unittest.TestCase):
    def test_first_sign_in_requires_verified_email(self):
        conn = FakeConn(lambda _sql, _args: Result())
        with patch.object(ent, "_conn", return_value=conn):
            with self.assertRaisesRegex(HTTPException, "verified email"):
                ent.link_oidc_identity({"sub": "new", "email": "x@example.test",
                                        "email_verified": False}, "https://id.example.test")

    def test_existing_email_cannot_be_taken_over_by_another_subject(self):
        existing = {"id": 7, "name": "Existing", "email": "x@example.test", "status": "active"}
        def handler(sql, _args):
            if "WHERE e.provider" in sql and "e.subject" in sql: return Result()
            if "lower(email)" in sql: return Result(existing)
            if "WHERE user_id" in sql and "provider" in sql: return Result({"subject": "original"})
            return Result()
        with patch.object(ent, "_conn", return_value=FakeConn(handler)):
            with self.assertRaisesRegex(HTTPException, "already linked"):
                ent.link_oidc_identity({"sub": "attacker", "email": "x@example.test",
                                        "email_verified": True}, "https://id.example.test")

    def test_existing_immutable_subject_resolves_without_email_relink(self):
        linked = {"id": 7, "name": "Existing", "email": "x@example.test", "status": "active"}
        conn = FakeConn(lambda sql, _args: Result(linked if "JOIN users" in sql else None))
        with patch.object(ent, "_conn", return_value=conn):
            self.assertEqual(ent.link_oidc_identity({"sub": "stable"}, "https://id.example.test")["id"], 7)
        self.assertFalse(any("INSERT INTO external_identities" in sql for sql, _ in conn.calls))


class ScimTests(unittest.TestCase):
    def test_bearer_auth_is_required_and_constant_value_is_accepted(self):
        with patch.object(ent.config, "get", return_value="secret"):
            ent._scim_auth("Bearer secret")
            for value in (None, "Bearer forged", "Basic secret"):
                with self.assertRaises(HTTPException) as error: ent._scim_auth(value)
                self.assertEqual(error.exception.status_code, 401)

    def test_filter_accepts_only_scim_equality_for_supported_fields(self):
        self.assertEqual(ent._filter('userName eq "a@example.test"'), ("username", "a@example.test"))
        self.assertEqual(ent._filter('externalId eq "immutable-1"'), ("externalid", "immutable-1"))
        self.assertIsNone(ent._filter('userName co "example"'))
        self.assertIsNone(ent._filter('userName eq "x" or active eq true'))

    def test_existing_external_id_is_idempotently_updated(self):
        current = {"id": 9, "name": "Old", "email": "a@example.test", "status": "active",
                   "external_id": "ext-1"}
        def handler(sql, _args):
            if "SELECT u.*, e.subject" in sql or "SELECT u.*,e.subject" in sql: return Result(current)
            return Result()
        conn = FakeConn(handler)
        with patch.object(ent, "_conn", return_value=conn), patch.object(ent.control_store, "revoke_user_sessions"):
            result = ent.provision_user({"externalId": "ext-1", "userName": "a@example.test",
                                         "displayName": "Updated", "active": True})
        self.assertEqual(result["id"], "9")
        self.assertFalse(any("INSERT INTO users" in sql for sql, _ in conn.calls))
        self.assertTrue(any("UPDATE users SET name" in sql for sql, _ in conn.calls))

    def test_deprovision_disables_user_memberships_and_revokes_sessions_before_return(self):
        conn = FakeConn(lambda sql, _args: Result({"id": 9, "email": "a@example.test"})
                        if "SELECT * FROM users" in sql else Result())
        with patch.object(ent, "_conn", return_value=conn), \
             patch.object(ent.control_store, "revoke_user_sessions", return_value=2) as revoke:
            ent.deprovision_user(9)
        revoke.assert_called_once_with(9)
        sql = "\n".join(text for text, _ in conn.calls)
        self.assertIn("UPDATE users SET status='disabled'", sql)
        self.assertIn("UPDATE project_members SET status='disabled'", sql)
        self.assertTrue(any("INSERT INTO events" in text and args[1] == "deactivated user"
                            for text, args in conn.calls))

    def test_patch_deactivation_calls_immediate_deprovision(self):
        request = Request({"type": "http", "method": "PATCH", "path": "/", "headers": [],
                           "query_string": b"", "scheme": "https", "server": ("test", 443),
                           "client": ("127.0.0.1", 1)})
        async def receive():
            return {"type": "http.request", "body": json.dumps({"Operations": [
                {"op": "replace", "path": "active", "value": False}]}).encode(), "more_body": False}
        request._receive = receive
        with patch.object(ent, "_scim_auth"), patch.object(ent, "deprovision_user") as deactivate, \
             patch.object(ent, "scim_get_user", return_value={"active": False}):
            import asyncio
            result = asyncio.run(ent.scim_patch_user(9, request, "Bearer secret"))
        deactivate.assert_called_once_with(9)
        self.assertFalse(result["active"])

    def test_group_member_removal_revokes_the_managed_project_role(self):
        group = {"id": 4, "external_id": "g-4", "display_name": "Engineering",
                 "project_id": 2, "role": "member"}
        def handler(sql, _args):
            if "SELECT * FROM enterprise_groups" in sql: return Result(group)
            if "SELECT user_id FROM enterprise_group_members" in sql: return Result(many=[{"user_id": 9}])
            if "SELECT 1 FROM enterprise_managed_memberships" in sql: return Result()
            return Result()
        conn = FakeConn(handler)
        request = Request({"type": "http", "method": "PATCH", "path": "/", "headers": [],
                           "query_string": b"", "scheme": "https", "server": ("test", 443),
                           "client": ("127.0.0.1", 1)})
        async def receive():
            return {"type": "http.request", "body": json.dumps({"Operations": [
                {"op": "remove", "path": "members", "value": [{"value": "9"}]}]}).encode(),
                    "more_body": False}
        request._receive = receive
        with patch.object(ent, "_scim_auth"), patch.object(ent, "_conn", return_value=conn):
            import asyncio
            result = asyncio.run(ent.scim_patch_group(4, request, "Bearer secret"))
        self.assertEqual(result["members"], [])
        sql = "\n".join(text for text, _ in conn.calls)
        self.assertIn("DELETE FROM enterprise_managed_memberships", sql)
        self.assertIn("UPDATE project_members SET status='disabled'", sql)


if __name__ == "__main__":
    unittest.main()
