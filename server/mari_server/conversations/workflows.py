"""Human-codified assistant behavior shared by every conversation surface."""

from __future__ import annotations

import json
import re

from mari_server.persistence.postgres import trajectories as store


def _words(value: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", value.lower()) if len(word) > 2}


def select(query: str, available_tools: set[str] | None = None) -> dict | None:
    """Select the single best enabled workflow with an observable intent match."""
    rows = store.active_workflows(50)
    if not rows:
        return None
    query_words = _words(query)
    ranked: list[tuple[int, dict]] = []
    for row in rows:
        haystack = f"{row.get('name', '')} {row.get('description', '')}"
        overlap = len(query_words & _words(haystack))
        if overlap <= 0:
            continue
        steps = _steps(row)
        if available_tools is not None:
            steps = [step for step in steps if step["tool"] in available_tools]
        if not steps:
            continue
        row = {**row, "steps": steps}
        ranked.append((overlap, row))
    return max(ranked, key=lambda item: item[0])[1] if ranked else None


def _steps(row: dict) -> list[dict]:
    raw_steps = row.get("steps") or []
    if isinstance(raw_steps, str):
        try:
            raw_steps = json.loads(raw_steps)
        except json.JSONDecodeError:
            raw_steps = []
    return [raw for raw in raw_steps[:12] if isinstance(raw, dict)
            and str(raw.get("tool") or "").strip()]


def guidance(query: str, available_tools: set[str] | None = None) -> str:
    """Describe the deterministically selected workflow to the planner."""
    selected = select(query, available_tools)
    if not selected:
        return ""
    blocks: list[str] = []
    for row in [selected]:
        steps = []
        for raw in _steps(row):
            tool = str(raw.get("tool") or "").strip()
            if not tool:
                continue
            arguments = raw.get("arguments") if isinstance(raw.get("arguments"), dict) else {}
            steps.append(f"{tool}({json.dumps(arguments, sort_keys=True)[:500]})")
        sequence = " -> ".join(steps) or "Answer using the reviewed behavior."
        blocks.append(
            f"- {str(row.get('name') or 'Workflow')[:160]}: "
            f"{str(row.get('description') or '')[:400]}\n  {sequence}"
        )
    return (
        "\n\nHUMAN-CODIFIED WORKFLOWS:\n"
        "This workflow was selected deterministically. Its reviewed calls run before planning; "
        "continue from their results and still enforce permissions.\n" + "\n".join(blocks)
    )


def retrieval_query(query: str) -> str:
    """Use a selected workflow's reviewed search arguments for RAG surfaces."""
    selected = select(query, {"search"})
    if not selected:
        return query
    for step in _steps(selected):
        arguments = step.get("arguments")
        if step.get("tool") == "search" and isinstance(arguments, dict):
            reviewed = str(arguments.get("query") or "").strip()
            if reviewed:
                return reviewed
    return query
