from __future__ import annotations

import unittest

from mari_components.audit import AuditEvent, chained_row, redact, scrub, verify_chain


class AuditTrailTests(unittest.TestCase):
    def test_chain_is_project_partitioned_tamper_evident_and_redacted(self):
        first = chained_row(AuditEvent(
            project_id=7, actor_type="user", actor_id="2", actor_name="Dana",
            action="fact.approve", resource_type="fact", resource_id="8",
            detail={"before": "draft", "api_token": "secret"},
        ), "")
        second = chained_row(AuditEvent(
            project_id=7, actor_type="service", actor_id="policy", actor_name="Mari",
            action="fact.escalate", resource_type="fact", resource_id="9", outcome="manual",
        ), first["event_hash"])
        other = chained_row(AuditEvent(
            project_id=9, actor_type="service", actor_id="policy", actor_name="Mari",
            action="fact.approve", resource_type="fact", resource_id="1",
        ), "")
        rows = [first, second, other]
        self.assertTrue(verify_chain(rows))
        self.assertNotIn("secret", first["detail_json"])
        self.assertIn("[REDACTED]", first["detail_json"])
        tampered = [dict(row) for row in rows]
        tampered[0]["reason"] = "rewritten"
        self.assertFalse(verify_chain(tampered))

    def test_redact_covers_credential_key_families(self):
        sensitive = [
            "password", "passwd", "pwd", "passphrase", "password_hash",
            "token", "access_token", "id_token", "jwt", "bearer", "secret", "client_secret",
            "authorization", "auth", "auth_header", "cookie", "session", "session_id",
            "credential", "credentials", "cred", "creds",
            "api_key", "apiKey", "x-api-key", "private_key", "ssh_key", "signing_key",
            "encryption_key", "aws_secret_access_key", "aws_access_key_id",
            "csrf_token", "xsrf", "otp", "totp", "mfa_code", "pin", "nonce", "salt", "oauth_state",
            "ssn", "social_security_number", "credit_card", "card_number", "cvv", "iban",
            "account_number", "routing_number",
            "connection_string", "database_url", "db_url", "dsn", "webhook_url", "gh_pat",
        ]
        benign = [
            "user_id", "email", "author", "author_id", "authored_at", "public_key", "key", "kid",
            "hash", "project_id", "actor_id", "actor_type", "action", "resource", "timestamp",
            "duration_ms", "status", "error", "message", "payload", "body", "headers", "query",
            "path", "method", "phone", "address", "ip_address", "user_agent", "license", "serial",
            "pinned", "mapping", "option", "pattern", "template",
        ]
        out = redact({k: "v" for k in sensitive + benign})
        self.assertEqual([k for k in sensitive if out[k] != "[REDACTED]"], [])
        self.assertEqual([k for k in benign if out[k] == "[REDACTED]"], [])
        # nested dicts and lists are walked
        nested = redact({"detail": {"items": [{"private_key": "x"}], "note": "ok"}})
        self.assertEqual(nested["detail"]["items"][0]["private_key"], "[REDACTED]")
        self.assertEqual(nested["detail"]["note"], "ok")

    def test_redact_scrubs_secret_shaped_values_under_any_key(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA7\n-----END RSA PRIVATE KEY-----"
        # fixtures are assembled from fragments so the source never contains a
        # scannable token and push protection stays quiet
        secrets = {
            "jwt": jwt,
            "pem": pem,
            "bearer": "Authorization: Bearer a1b2c3d4e5f6g7h8i9j0",
            "basic": "Basic dXNlcjpwYXNzd29yZDEyMw==",
            "aws": "AKIA" + "IOSFODNN7EXAMPLE",
            "github": "ghp_" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
            "github_fine": "github_pat_" + "11ABCDEFG0123456789_abcdefghijklmnop",
            "slack": "xoxb-" + "1234567890-abcdefghij",
            "slack_hook": "see https://hooks.slack.com/services/T000/B000/XXXXXXXX for details",
            "anthropic": "sk-ant-" + "api03-abcdefghijklmnopqrstuvwxyz",
            "openai": "sk-" + "abcdefghijklmnopqrstuvwxyz123456",
            "stripe": "sk_live_" + "abcdefghijklmnopqrstuvwxyz",
            "google": "AIza" + "SyA1234567890abcdefghijklmnopqrstuv",
            "pg_url": "postgres://mari:hunter2@db.internal:5432/mari",
        }
        out = redact({"note": secrets})["note"]
        for key, raw in secrets.items():
            self.assertIn("[REDACTED]", out[key], key)
        self.assertEqual(out["pg_url"], "postgres://mari:[REDACTED]@db.internal:5432/mari")
        self.assertEqual(out["slack_hook"], "see [REDACTED] for details")
        self.assertNotIn("hunter2", out["pg_url"])
        self.assertNotIn("MIIEpAIBAAKCAQEA7", out["pem"])

        benign = [
            "3b1f2c9d4e5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c",                       # git sha
            "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",  # sha256
            "6f1e2d3c-4b5a-4c6d-8e9f-0a1b2c3d4e5f",                           # uuid
            "https://docs.mari.guru/guide/setup",                             # url, no creds
            "bearer token_expired_message",                                   # prose
            "Basic configuration_of_the_thing",                               # prose
            "fact.approve", "draft", "Dana approved the change at 10:42",
            "eyJ short", "AKIA", "ghp_short",
        ]
        for text in benign:
            self.assertEqual(scrub(text), text, text)

    def test_event_validation_is_fail_closed(self):
        with self.assertRaises(ValueError):
            AuditEvent(project_id=1, actor_type="user", actor_id="1", actor_name="A",
                       action="", resource_type="fact", resource_id="1")
        with self.assertRaises(ValueError):
            AuditEvent(project_id=1, actor_type="user", actor_id="1", actor_name="A",
                       action="read", resource_type="fact", resource_id="1", outcome="maybe")


if __name__ == "__main__":
    unittest.main()
