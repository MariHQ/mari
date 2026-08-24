"""Database-backed implementations of the agent's read-only product tools."""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from mari_components.agents.runtime import ToolBinding, ToolOutcome
from mari_components.agents.content import UNTRUSTED_CLOSE, UNTRUSTED_OPEN, untrusted_document
from mari_server.product.navigation import PRODUCT_SURFACES, valid_navigation


class AgentToolReadStore(Protocol):
    def document(self, document_id: int) -> Mapping[str, Any] | None: ...
    def document_tags(self, document_id: int) -> Sequence[Mapping[str, Any]]: ...
    def sources(self) -> Sequence[Mapping[str, Any]]: ...
    def trajectories(self) -> Sequence[Mapping[str, Any]]: ...
    def trajectory(self, trajectory_id: int) -> Mapping[str, Any] | None: ...
    def trajectory_steps(self, trajectory_id: int) -> Sequence[Mapping[str, Any]]: ...
    def answers(self) -> Sequence[Mapping[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class ToolDependencies:
    store: AgentToolReadStore
    search: Callable[[str, int], Sequence[Mapping[str, Any]]]
    record_search: Callable[[str], None]
    connector_definitions: Callable[[], Sequence[Any]]


# The workspace tag vocabulary (mari tag): trust states first, lifecycle
# states after, so "canonical" outranks "deprecated" on a doc carrying both.
REVIEW_STATES = ("canonical", "canon", "verified", "needs-review", "stale", "deprecated", "draft", "internal")


def _review_status(tags: Any) -> str:
    """The record's verification state, from the review tags the workspace
    actually uses. "unreviewed" is the honest default, not a judgment."""
    names = {str(tag).lower() for tag in (tags or [])}
    for state in REVIEW_STATES:
        if state in names:
            return state
    return "unreviewed"


def _age_days(updated: Any) -> int | None:
    if updated is None:
        return None
    if isinstance(updated, dt.datetime):
        updated = updated.date()
    if isinstance(updated, dt.date):
        return max(0, (dt.date.today() - updated).days)
    try:
        return max(0, (dt.date.today() - dt.date.fromisoformat(str(updated)[:10])).days)
    except ValueError:
        return None


def _passage(body: str, query: str, radius: int = 400) -> str:
    """The stretch of the document around the query's first matching term, or
    the opening if nothing matches. Whitespace-collapsed so the excerpt spends
    its budget on words."""
    text = " ".join(str(body or "").split())
    if not text:
        return ""
    lower = text.lower()
    hit = -1
    for term in re.findall(r"[a-z0-9][a-z0-9_-]+", query.lower()):
        hit = lower.find(term)
        if hit >= 0:
            break
    if hit < 0:
        return text[: radius * 2]
    start = max(0, hit - radius)
    end = min(len(text), hit + radius)
    return ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")


def build_tool_bindings(deps: ToolDependencies) -> dict[str, ToolBinding]:
    """Build one project's tool registry from explicit infrastructure ports."""

    def search(arguments: Mapping[str, Any]) -> ToolOutcome:
        text = str(arguments.get("query") or "").strip()
        if not text:
            return ToolOutcome(False, "search needs a query", "error: missing query")
        rows = deps.search(text, 8)
        deps.record_search(text)
        # The top hits carry a real passage and the record's trust metadata,
        # not a 160-character teaser. An answer written from teasers could
        # never say more than the result list showed, and an answer that
        # cannot see verification states cannot prefer a verified fact over a
        # stale one — which is the whole difference between this assistant
        # and a search box.
        hits = []
        for index, row in enumerate(rows):
            hit: dict[str, Any] = {
                "id": row["id"], "title": row["title"],
                "status": _review_status(row.get("tags")),
                "owner": str(row.get("author") or "") or None,
                "age_days": _age_days(row.get("updated_src")),
            }
            if index < 3:
                passage = _passage(str(row.get("body") or row.get("snippet") or ""), text)
                if passage:
                    hit["passage"] = untrusted_document(passage)
            else:
                hit["snippet"] = str(row.get("snippet") or "")[:160]
            hits.append(hit)
        evidence = tuple({
            "document_id": int(row["id"]), "title": str(row["title"]),
            "reason": "retrieved by knowledge search", "rank": index + 1,
        } for index, row in enumerate(rows))
        return ToolOutcome(True, f'{len(hits)} hits for "{text[:60]}"', hits,
                           evidence=evidence)

    def read_document(arguments: Mapping[str, Any]) -> ToolOutcome:
        try:
            document_id = int(arguments.get("id"))
        except (TypeError, ValueError):
            return ToolOutcome(False, "read_document needs a valid id", "error: invalid id")
        document = deps.store.document(document_id)
        if not document:
            return ToolOutcome(False, f"document {document_id} not found", "error: no document")
        tags = deps.store.document_tags(document_id)
        names = [str(row["tag"]) for row in tags]
        raw_body = str(document.get("body") or document.get("snippet") or "")
        updated = document.get("updated_src")
        detail = {
            "id": document_id, "title": document["title"], "source": document["source"],
            "author": document["author"], "tags": names,
            "updated": updated.isoformat() if hasattr(updated, "isoformat") else str(updated or ""),
            "body": untrusted_document(raw_body[:4000]),
        }
        summary = (f'read "{document["title"]}" ({len(raw_body)} chars, '
                   f'tags: {", ".join(names) or "none"})')
        return ToolOutcome(True, summary, detail, evidence=({
            "document_id": document_id, "title": str(document["title"]),
            "reason": "read to answer the user", "rank": 1,
        },))

    def list_product_surfaces(_arguments: Mapping[str, Any]) -> ToolOutcome:
        detail = [{"path": surface.path, "label": surface.label} for surface in PRODUCT_SURFACES]
        return ToolOutcome(True, f"{len(detail)} product surfaces", detail)

    def list_connector_types(_arguments: Mapping[str, Any]) -> ToolOutcome:
        detail = [{
            "key": definition.key, "name": definition.name,
            "description": definition.description,
            "fields": [{
                "key": field.key, "label": field.label, "required": field.required,
                "secret": field.secret, "help": field.help,
            } for field in definition.fields],
            "documentation_url": definition.documentation_url,
        } for definition in deps.connector_definitions()]
        return ToolOutcome(True, f"{len(detail)} connector types", detail)

    def list_sources(_arguments: Mapping[str, Any]) -> ToolOutcome:
        rows = deps.store.sources()
        detail = [{
            "id": row["id"], "name": row["display_name"], "provider": row["provider"],
            "status": row["status"], "health": row["health"], "docs": row["docs_count"],
        } for row in rows]
        return ToolOutcome(True, f"{len(detail)} sources", detail)

    def list_workflow_observations(arguments: Mapping[str, Any]) -> ToolOutcome:
        rows = deps.store.trajectories()
        wanted = str(arguments.get("query") or "").strip().casefold()
        if wanted:
            rows = [row for row in rows if wanted in " ".join(
                str(row.get(key) or "")
                for key in ("prompt", "layer2", "category", "macro_intent")
            ).casefold()]
        detail = [{
            "id": row["id"], "status": row["status"], "activity": row["layer2"],
            "category": row["category"], "intent": row["macro_intent"],
            "steps": row["step_count"], "failures": row["failure_count"],
            "rework": row["rework_count"],
        } for row in rows[:20]]
        return ToolOutcome(True, f"{len(detail)} observed workflows", detail)

    def inspect_workflow_observation(arguments: Mapping[str, Any]) -> ToolOutcome:
        try:
            trajectory_id = int(arguments.get("id"))
        except (TypeError, ValueError):
            return ToolOutcome(False, "workflow observation needs an id", "error: invalid id")
        row = deps.store.trajectory(trajectory_id)
        if not row:
            return ToolOutcome(False, f"workflow observation {trajectory_id} not found",
                               "error: no observation")
        steps = deps.store.trajectory_steps(trajectory_id)
        return ToolOutcome(True, f"inspected observed workflow {trajectory_id}",
                           {**dict(row), "steps": [dict(step) for step in steps]})

    def list_answers(_arguments: Mapping[str, Any]) -> ToolOutcome:
        rows = deps.store.answers()
        detail = [dict(row) for row in rows]
        return ToolOutcome(True, f"{len(detail)} answers", detail)

    def navigate(arguments: Mapping[str, Any]) -> ToolOutcome:
        path = str(arguments.get("path") or "")
        if not valid_navigation(path):
            return ToolOutcome(False, f"path not allowed: {path[:80]}", {"path": path})
        return ToolOutcome(True, f"→ {path}", {"path": path}, path)

    return {
        "search": ToolBinding(
            "search(query) - hybrid knowledge search with real document ids. The agent cannot "
            "answer a knowledge question without calling this: list_sources only reports connector "
            "health, not document content, and is not a substitute.",
            search,
        ),
        "read_document": ToolBinding("read_document(id) — one document with provenance and tags", read_document),
        "list_product_surfaces": ToolBinding("list_product_surfaces() — shipped surfaces and paths", list_product_surfaces),
        "list_connector_types": ToolBinding("list_connector_types() — supported connector contracts", list_connector_types),
        "list_sources": ToolBinding("list_sources() — connected sources and health", list_sources),
        "list_workflow_observations": ToolBinding("list_workflow_observations(query?) — mined behavior and rework", list_workflow_observations),
        "inspect_workflow_observation": ToolBinding("inspect_workflow_observation(id) — phases and tool outcomes", inspect_workflow_observation),
        "list_answers": ToolBinding("list_answers() — approved-answer library", list_answers),
        "navigate": ToolBinding("navigate(path) — open a path returned by list_product_surfaces", navigate),
    }
