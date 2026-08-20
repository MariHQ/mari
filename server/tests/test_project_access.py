"""Project selection and capability boundary tests (no database required)."""

from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

import access


PROJECTS = {
    7: {"project_id": 7, "project_slug": "acme", "project_name": "Acme", "role": "admin", "status": "active"},
    9: {"project_id": 9, "project_slug": "beta", "project_name": "Beta", "role": "viewer", "status": "active"},
}


class Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return list(self.rows)


class Connection:
    def __init__(self, state):
        self.state = state

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def execute(self, _sql, args=()):
        user_id = args[0]
        rows = [row for row in self.state.get(user_id, ()) if row["status"] == "active"]
        return Result(rows)


def factory(state):
    return lambda: Connection(state)


class ProjectAccessTests(unittest.TestCase):
    def tearDown(self):
        access.set_access(None)

    def test_role_capability_matrix_is_central_and_least_privilege(self):
        self.assertEqual(access.capabilities_for_role("owner"), access.CAPABILITIES)
        self.assertEqual(access.capabilities_for_role("admin"), access.CAPABILITIES)
        self.assertEqual(access.capabilities_for_role("manager"), frozenset({
            "knowledge.read", "knowledge.write", "review.approve",
            "automation.run", "automation.manage", "source.sync",
        }))
        self.assertEqual(access.capabilities_for_role("member"), frozenset({
            "knowledge.read", "knowledge.write", "automation.run"}))
        self.assertEqual(access.capabilities_for_role("user"), access.capabilities_for_role("member"))
        self.assertEqual(access.capabilities_for_role("viewer"), frozenset({"knowledge.read"}))
        self.assertEqual(access.capabilities_for_role("unknown"), frozenset())

    def test_access_context_is_immutable(self):
        context = access.AccessContext(1, 7, "acme", "Acme", "viewer", frozenset({"knowledge.read"}))
        with self.assertRaises(FrozenInstanceError):
            context.project_id = 9  # type: ignore[misc]

    def test_exactly_one_membership_is_inferred(self):
        context, memberships = access.resolve_access({"id": 1}, None, factory({1: [PROJECTS[7]]}))
        self.assertIsNotNone(context)
        self.assertEqual(context.project_id, 7)
        self.assertEqual(context.role, "admin")
        self.assertEqual(len(memberships), 1)

    def test_header_selects_project_by_id_or_slug(self):
        for selector in ("7", "acme"):
            with self.subTest(selector=selector):
                context, _ = access.resolve_access(
                    {"id": 1}, selector, factory({1: [PROJECTS[7], PROJECTS[9]]}))
                self.assertIsNotNone(context)
                self.assertEqual(context.project_id, 7)

    def test_two_projects_require_an_explicit_header(self):
        with self.assertRaises(HTTPException) as caught:
            access.resolve_access({"id": 1}, None, factory({1: [PROJECTS[7], PROJECTS[9]]}))
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("X-Mari-Project", caught.exception.detail)

    def test_me_mode_lists_two_projects_without_selecting_one(self):
        context, memberships = access.resolve_access(
            {"id": 1}, None, factory({1: [PROJECTS[7], PROJECTS[9]]}), required=False)
        self.assertIsNone(context)
        self.assertEqual([m.project_slug for m in memberships], ["acme", "beta"])

    def test_invalid_or_other_project_header_is_denied(self):
        for selector in ("404", "other"):
            with self.subTest(selector=selector), self.assertRaises(HTTPException) as caught:
                access.resolve_access({"id": 1}, selector, factory({1: [PROJECTS[7]]}))
            self.assertEqual(caught.exception.status_code, 403)

    def test_header_is_normalized(self):
        context, _ = access.resolve_access(
            {"id": 1}, " beta ", factory({1: [PROJECTS[7], PROJECTS[9]]}))
        self.assertEqual(context.project_id, 9)

    def test_no_active_membership_is_denied(self):
        disabled = {**PROJECTS[7], "status": "disabled"}
        with self.assertRaises(HTTPException) as caught:
            access.resolve_access({"id": 1}, None, factory({1: [disabled]}))
        self.assertEqual(caught.exception.status_code, 403)

    def test_membership_revocation_takes_effect_on_next_resolution(self):
        state = {1: [dict(PROJECTS[7])]}
        first, _ = access.resolve_access({"id": 1}, None, factory(state))
        self.assertEqual(first.project_id, 7)
        state[1][0]["status"] = "disabled"
        with self.assertRaises(HTTPException) as caught:
            access.resolve_access({"id": 1}, None, factory(state))
        self.assertEqual(caught.exception.status_code, 403)

    def test_users_can_have_different_roles_in_different_projects(self):
        context, memberships = access.resolve_access(
            {"id": 1}, "beta", factory({1: [PROJECTS[7], PROJECTS[9]]}))
        self.assertEqual(context.role, "viewer")
        self.assertEqual(context.capabilities, frozenset({"knowledge.read"}))
        self.assertEqual({m.project_slug: m.role for m in memberships},
                         {"acme": "admin", "beta": "viewer"})

    def test_unknown_capability_dependency_fails_at_startup(self):
        with self.assertRaisesRegex(ValueError, "Unknown capability"):
            access.require_capability("root.everything")

    def test_rest_project_dependency_publishes_access_for_scoped_helpers(self):
        context = access.AccessContext(
            1, 7, "acme", "Acme", "admin", access.capabilities_for_role("admin"))
        request = Request({"type": "http", "method": "POST", "path": "/chat", "headers": []})
        with patch("auth.require_user", return_value={"id": 1}), \
             patch.object(access, "resolve_access", return_value=(context, [])):
            self.assertIs(access.require_project(request), context)
        self.assertIs(access.current_access(), context)


if __name__ == "__main__":
    unittest.main()
