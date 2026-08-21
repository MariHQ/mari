"""Human-codified assistant behavior shared by every conversation surface."""

from __future__ import annotations

import json
import re

from mari_server.persistence.postgres import trajectories as store


def _words(value: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", value.lower()) if len(word) > 2}


def guidance(query: str = "", limit: int = 8) -> str:
    """Return bounded, reviewed workflow guidance relevant to a user turn."""
    rows = store.active_workflows(max(limit * 3, limit))
    if not rows:
        return ""
    query_words = _words(query)
    ranked = []
    for row in rows:
        haystack = f"{row.get('name', '')} {row.get('description', '')}"
        overlap = len(query_words & _words(haystack)) if query_words else 0
        ranked.append((overlap, row))
    if query_words and any(score for score, _ in ranked):
        ranked = [item for item in ranked if item[0] > 0]
    selected = [row for _, row in sorted(ranked, key=lambda item: item[0], reverse=True)[:limit]]
    blocks: list[str] = []
    for row in selected:
        raw_steps = row.get("steps") or []
        if isinstance(raw_steps, str):
            try:
                raw_steps = json.loads(raw_steps)
            except json.JSONDecodeError:
                raw_steps = []
        steps = []
        for raw in raw_steps[:12] if isinstance(raw_steps, list) else []:
            if not isinstance(raw, dict):
                continue
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
        "Use a matching workflow as reviewed guidance. Never invent unavailable tools, "
        "and still enforce tool permissions and argument validation.\n" + "\n".join(blocks)
    )
