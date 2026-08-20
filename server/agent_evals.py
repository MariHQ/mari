"""Outcome-based evaluations for the in-product agent chat."""

from __future__ import annotations

from dataclasses import dataclass
import json
import typing as t


@dataclass(frozen=True, slots=True)
class AgentEvalCase:
    name: str
    prompt: str
    expected_path: str
    required_terms: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AgentToolEvalCase:
    name: str
    prompt: str
    expected_tool: str
    answer_terms: tuple[str, ...]


CASES = (
    AgentEvalCase(
        "home", "Show me the home dashboard", "/",
        ("digest", "activity", "source-health", "knowledge or review"),
    ),
    AgentEvalCase(
        "knowledge", "Help me browse the knowledge base", "/knowledge",
        ("search", "result type", "evidence", "provenance"),
    ),
    AgentEvalCase(
        "mcp_setup", "Help me set up MCP for my client", "/publish?tab=mcp",
        ("new server", "bearer token", "mcp url", "with test"),
    ),
    AgentEvalCase(
        "confluence_setup", "Connect our Confluence knowledge source", "/sources",
        ("add source", "validate", "incremental sync", "healthy"),
    ),
    AgentEvalCase(
        "slack_bot_setup", "Set up the Slack bot", "/publish?tab=bots",
        ("destinations", "credentials", "connection test", "sandbox event"),
    ),
    AgentEvalCase(
        "review", "Help me approve a fact in the review queue", "/tasks",
        ("filter", "evidence-linked", "verify", "policy-review"),
    ),
    AgentEvalCase(
        "facts", "Show me how to manage contradictions in facts", "/facts",
        ("search or filter", "source evidence", "contradictions", "review queue"),
    ),
    AgentEvalCase(
        "decisions", "Help me manage product decisions", "/decisions",
        ("capture", "context", "impact", "ratify"),
    ),
    AgentEvalCase(
        "answers", "Help me manage approved answers", "/answers",
        ("draft or harvest", "supporting knowledge", "delivery channels", "review"),
    ),
    AgentEvalCase(
        "lineage", "Help me inspect the dependency graph lineage", "/lineage",
        ("lens", "focal record", "neighborhood", "impact and history"),
    ),
    AgentEvalCase(
        "automations", "Help me configure an automation", "/flows",
        ("trigger", "steps", "dry-run", "run history"),
    ),
    AgentEvalCase(
        "site_publish", "Help me publish a documentation site", "/publish",
        ("content", "navigation", "preview and build", "roll back"),
    ),
    AgentEvalCase(
        "analytics", "Show me product knowledge analytics and insights", "/insights",
        ("reporting range", "evidence-backed", "affected knowledge", "review item"),
    ),
    AgentEvalCase(
        "trajectories", "Show me how to inspect agent trajectories", "/trajectories",
        ("category or status", "steps", "failures", "rework"),
    ),
    AgentEvalCase(
        "library", "Help me configure the glossary library", "/library",
        ("glossary", "search existing", "defaults or weights", "generated knowledge"),
    ),
    AgentEvalCase(
        "models", "Help me set up Ollama model settings", "/settings/models",
        ("generation and embedding", "connection settings", "connection test", "reindex"),
    ),
    AgentEvalCase(
        "members", "Help me configure SCIM user access", "/settings/members",
        ("provision", "project role", "enterprise identity", "access"),
    ),
    AgentEvalCase(
        "api_keys", "Help me create an API key", "/settings/api-keys",
        ("narrowly scoped", "shown once", "test", "revoke"),
    ),
    AgentEvalCase(
        "audit_log", "Show me who changed something in the access log", "/settings/audit",
        ("actor", "action", "correlation", "export"),
    ),
    AgentEvalCase(
        "repository_audit", "Help me review a repository audit", "/audit",
        ("findings", "evidence", "fixing or dismissing", "review"),
    ),
    AgentEvalCase(
        "branding", "Help me configure our branding and logo", "/settings/design",
        ("colors", "fonts", "warnings", "preview"),
    ),
    AgentEvalCase(
        "workspace", "Help me change the workspace timezone", "/settings/general",
        ("workspace name", "timezone", "language", "scheduled activity"),
    ),
    AgentEvalCase(
        "preferences", "Help me change password in my preferences", "/preferences",
        ("profile", "password", "notification", "success state"),
    ),
    AgentEvalCase(
        "onboarding", "Help me with initial workspace onboarding", "/welcome",
        ("connector or upload", "glossary", "back", "initial knowledge"),
    ),
)

TOOL_CASES = (
    AgentToolEvalCase("source_inventory", "What sources are connected?", "list_sources",
                      ("confluence", "healthy")),
    AgentToolEvalCase("automation_inventory", "What automations are available?", "list_flows",
                      ("fact scan", "active")),
    AgentToolEvalCase("review_inventory", "Which review tasks are open?", "list_tasks",
                      ("verify retention", "open")),
    AgentToolEvalCase("answer_inventory", "Show the approved answer library", "list_answers",
                      ("how long", "approved")),
)


def score_tool(case: AgentToolEvalCase, chunks: t.Iterable[str]) -> dict[str, t.Any]:
    events = parse_events(chunks)
    successful = [data.get("name") for event, data in events
                  if event == "tool_result" and data.get("ok")]
    answer = "".join(str(data.get("token", "")) for event, data in events if event == "token").lower()
    checks = {
        "completed": any(event == "done" for event, _data in events),
        "used_expected_tool": successful == [case.expected_tool],
        "grounded_answer": all(term in answer for term in case.answer_terms),
    }
    return {"case": case.name, "passed": all(checks.values()), "checks": checks, "answer": answer}


def parse_events(chunks: t.Iterable[str]) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for chunk in chunks:
        event = ""
        data = ""
        for line in chunk.splitlines():
            if line.startswith("event:"):
                event = line.partition(":")[2].strip()
            elif line.startswith("data:"):
                data += line.partition(":")[2].strip()
        if event and data:
            events.append((event, json.loads(data)))
    return events


def score(case: AgentEvalCase, chunks: t.Iterable[str]) -> dict[str, t.Any]:
    events = parse_events(chunks)
    paths = [data.get("path") for event, data in events if event == "navigate"]
    answer = "".join(
        str(data.get("token", "")) for event, data in events if event == "token"
    ).lower()
    failed_tools = [data for event, data in events if event == "tool_result" and not data.get("ok")]
    checks = {
        "completed": any(event == "done" for event, _data in events),
        "navigated": case.expected_path in paths,
        "actionable": all(term in answer for term in case.required_terms),
        "tools_succeeded": not failed_tools,
    }
    return {
        "case": case.name,
        "passed": all(checks.values()),
        "checks": checks,
        "answer": answer,
        "paths": paths,
    }
