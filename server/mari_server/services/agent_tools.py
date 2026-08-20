"""Database-backed implementations of the agent's read-only product tools."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from mari_components.agents.runtime import ToolBinding, ToolOutcome
from mari_server.domain.navigation import PRODUCT_SURFACES, valid_navigation


Query = Callable[[str, tuple[Any, ...]], Sequence[Mapping[str, Any]]]
QueryOne = Callable[[str, tuple[Any, ...]], Mapping[str, Any] | None]


@dataclass(frozen=True, slots=True)
class ToolDependencies:
    project_id: int
    query: Query
    query_one: QueryOne
    search: Callable[[str, int], Sequence[Mapping[str, Any]]]
    record_search: Callable[[str], None]
    review_items: Callable[[], Sequence[Any]]
    connector_definitions: Callable[[], Sequence[Any]]


UNTRUSTED_OPEN = "<<<UNTRUSTED_DOCUMENT_CONTENT>>>"
UNTRUSTED_CLOSE = "<<<END_UNTRUSTED_DOCUMENT_CONTENT>>>"


def safe_document_body(value: str) -> str:
    return value.replace(UNTRUSTED_OPEN, "[document delimiter removed]") \
                .replace(UNTRUSTED_CLOSE, "[document delimiter removed]")


def build_tool_bindings(deps: ToolDependencies) -> dict[str, ToolBinding]:
    """Build one project's tool registry from explicit infrastructure ports."""

    def search(arguments: Mapping[str, Any]) -> ToolOutcome:
        text = str(arguments.get("query") or "").strip()
        if not text:
            return ToolOutcome(False, "search needs a query", "error: missing query")
        rows = deps.search(text, 8)
        deps.record_search(text)
        hits = [{
            "id": row["id"], "title": row["title"],
            "snippet": str(row.get("snippet") or "")[:160],
        } for row in rows]
        return ToolOutcome(True, f'{len(hits)} hits for "{text[:60]}"', hits)

    def read_document(arguments: Mapping[str, Any]) -> ToolOutcome:
        try:
            document_id = int(arguments.get("id"))
        except (TypeError, ValueError):
            return ToolOutcome(False, "read_document needs a valid id", "error: invalid id")
        document = deps.query_one(
            """SELECT id, title, body, snippet, source, author, updated_src
                 FROM documents WHERE project_id = %s AND id = %s""",
            (deps.project_id, document_id),
        )
        if not document:
            return ToolOutcome(False, f"document {document_id} not found", "error: no document")
        tags = deps.query(
            """SELECT tag FROM tags WHERE project_id = %s AND document_id = %s ORDER BY tag""",
            (deps.project_id, document_id),
        )
        names = [str(row["tag"]) for row in tags]
        raw_body = str(document.get("body") or document.get("snippet") or "")
        updated = document.get("updated_src")
        detail = {
            "id": document_id, "title": document["title"], "source": document["source"],
            "author": document["author"], "tags": names,
            "updated": updated.isoformat() if hasattr(updated, "isoformat") else str(updated or ""),
            "body": f"{UNTRUSTED_OPEN}\n{safe_document_body(raw_body[:4000])}\n{UNTRUSTED_CLOSE}",
        }
        summary = (f'read "{document["title"]}" ({len(raw_body)} chars, '
                   f'tags: {", ".join(names) or "none"})')
        return ToolOutcome(True, summary, detail)

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
        rows = deps.query(
            """SELECT id, display_name, provider, kind, status, health, docs_count
                 FROM sources WHERE project_id = %s ORDER BY id""", (deps.project_id,),
        )
        detail = [{
            "id": row["id"], "name": row["display_name"], "provider": row["provider"],
            "status": row["status"], "health": row["health"], "docs": row["docs_count"],
        } for row in rows]
        return ToolOutcome(True, f"{len(detail)} sources", detail)

    def list_flows(_arguments: Mapping[str, Any]) -> ToolOutcome:
        rows = deps.query(
            """SELECT id, name, status, description FROM workflows
                 WHERE project_id = %s ORDER BY id""", (deps.project_id,),
        )
        detail = [{
            "id": row["id"], "name": row["name"], "status": row["status"],
            "description": str(row.get("description") or "")[:100],
        } for row in rows]
        return ToolOutcome(True, f"{len(detail)} flows", detail)

    def inspect_flow(arguments: Mapping[str, Any]) -> ToolOutcome:
        try:
            workflow_id = int(arguments.get("id"))
        except (TypeError, ValueError):
            return ToolOutcome(False, "inspect_flow needs a workflow id", "error: invalid id")
        row = deps.query_one(
            """SELECT id, name, description, status, nodes, trigger
                 FROM workflows WHERE project_id = %s AND id = %s""",
            (deps.project_id, workflow_id),
        )
        if not row:
            return ToolOutcome(False, f"workflow {workflow_id} not found", "error: no workflow")
        runs = deps.query(
            """SELECT id, number, status, progress, stats, rows_data, triggered_by
                 FROM workflow_runs WHERE project_id = %s AND workflow_id = %s
                ORDER BY id DESC LIMIT 10""", (deps.project_id, workflow_id),
        )
        return ToolOutcome(
            True, f'inspected "{row["name"]}" and {len(runs)} recent runs',
            {"workflow": dict(row), "runs": [dict(run) for run in runs]},
        )

    def list_workflow_observations(arguments: Mapping[str, Any]) -> ToolOutcome:
        rows = deps.query(
            """SELECT id, prompt, status, layer2, category, macro_intent,
                      step_count, failure_count, rework_count, started_at
                 FROM trajectories WHERE project_id = %s
                ORDER BY started_at DESC, id DESC LIMIT 50""", (deps.project_id,),
        )
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
        row = deps.query_one(
            """SELECT id, prompt, status, layer1, layer2, category, macro_intent,
                      phases, step_count, failure_count, rework_count
                 FROM trajectories WHERE project_id = %s AND id = %s""",
            (deps.project_id, trajectory_id),
        )
        if not row:
            return ToolOutcome(False, f"workflow observation {trajectory_id} not found",
                               "error: no observation")
        steps = deps.query(
            """SELECT ordinal, tool, action_family, summary, ok
                 FROM trajectory_steps WHERE project_id = %s AND trajectory_id = %s
                ORDER BY ordinal""", (deps.project_id, trajectory_id),
        )
        return ToolOutcome(True, f"inspected observed workflow {trajectory_id}",
                           {**dict(row), "steps": [dict(step) for step in steps]})

    def list_tasks(_arguments: Mapping[str, Any]) -> ToolOutcome:
        rows = deps.review_items()
        detail = [{
            "id": row.id, "title": row.title, "kind": row.kind, "status": row.status,
            "done": row.status in {"done", "approved", "rejected"},
        } for row in rows]
        open_count = sum(not row["done"] for row in detail)
        return ToolOutcome(True, f"{len(detail)} review items ({open_count} open)", detail)

    def list_answers(_arguments: Mapping[str, Any]) -> ToolOutcome:
        rows = deps.query(
            """SELECT id, question, status, served FROM approved_answers
                 WHERE project_id = %s ORDER BY id""", (deps.project_id,),
        )
        detail = [dict(row) for row in rows]
        return ToolOutcome(True, f"{len(detail)} answers", detail)

    def navigate(arguments: Mapping[str, Any]) -> ToolOutcome:
        path = str(arguments.get("path") or "")
        if not valid_navigation(path):
            return ToolOutcome(False, f"path not allowed: {path[:80]}", {"path": path})
        return ToolOutcome(True, f"→ {path}", {"path": path}, path)

    return {
        "search": ToolBinding("search(query) — hybrid knowledge search with real document ids", search),
        "read_document": ToolBinding("read_document(id) — one document with provenance and tags", read_document),
        "list_product_surfaces": ToolBinding("list_product_surfaces() — shipped surfaces and paths", list_product_surfaces),
        "list_connector_types": ToolBinding("list_connector_types() — supported connector contracts", list_connector_types),
        "list_sources": ToolBinding("list_sources() — connected sources and health", list_sources),
        "list_flows": ToolBinding("list_flows() — configured automations", list_flows),
        "inspect_flow": ToolBinding("inspect_flow(id) — definition and recent run evidence", inspect_flow),
        "list_workflow_observations": ToolBinding("list_workflow_observations(query?) — mined behavior and rework", list_workflow_observations),
        "inspect_workflow_observation": ToolBinding("inspect_workflow_observation(id) — phases and tool outcomes", inspect_workflow_observation),
        "list_tasks": ToolBinding("list_tasks() — unified Review items", list_tasks),
        "list_answers": ToolBinding("list_answers() — approved-answer library", list_answers),
        "navigate": ToolBinding("navigate(path) — open a path returned by list_product_surfaces", navigate),
    }
