"""Privacy-bounded LLM trajectory harvesting and hierarchical workflow mining.

The progressive abstraction mirrors rt-intent's WorkflowView work:
chronological tool telemetry -> grounded detailed workflow -> succinct inferred
activity -> discovered/assigned taxonomy. A deterministic coarse-to-fine phase
tree remains available when Ollama is unavailable and gives every LLM summary
auditable step boundaries.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import llm
from db import exec_, q, q1

_WORKERS = ThreadPoolExecutor(max_workers=2, thread_name_prefix="trajectory-harvest")

FAMILY = {
    "search": "discover", "read_document": "inspect", "list_sources": "inspect",
    "list_flows": "inspect", "list_tasks": "inspect", "list_answers": "inspect",
    "tag_document": "change", "untag_document": "change",
    "create_task": "change", "approve_answer": "approve", "sync_source": "execute",
    "run_flow": "execute", "navigate": "navigate",
}


def _safe_args(args: object) -> dict:
    """Keep routing/provenance hints; discard bodies, secrets, and nested payloads."""
    if not isinstance(args, dict):
        return {}
    out = {}
    for key, value in args.items():
        clean_key = re.sub(r"[^a-zA-Z0-9_-]", "", str(key))[:40]
        if not clean_key or any(word in clean_key.lower() for word in
                                ("body", "content", "token", "secret", "password", "key")):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            out[clean_key] = str(value)[:160] if isinstance(value, str) else value
    return out


def normalize_steps(trace: list[dict]) -> list[dict]:
    output = []
    for ordinal, event in enumerate(trace):
        tool = re.sub(r"[^a-z0-9_-]", "", str(event.get("name") or "unknown").lower())[:60]
        output.append({
            "ordinal": ordinal,
            "tool": tool or "unknown",
            "action_family": FAMILY.get(tool, "other"),
            "args": _safe_args(event.get("args")),
            "summary": str(event.get("summary") or "")[:300],
            "ok": bool(event.get("ok")),
        })
    return output


def segment_phases(steps: list[dict]) -> list[dict]:
    """Coarse-to-fine phases from action-family changes and failure recovery.

    Short product traces do not support statistically honest KMeans. This
    online equivalent uses the hierarchy's observable signals: family shifts
    define phase boundaries and failure -> later success marks a recovery
    sub-state. Adjacent one-step phases of the same family are always merged.
    """
    if not steps:
        return []
    phases: list[dict] = []
    start = 0
    for index in range(1, len(steps) + 1):
        boundary = index == len(steps)
        if not boundary:
            before, after = steps[index - 1], steps[index]
            boundary = before["action_family"] != after["action_family"] or (
                not before["ok"] and after["ok"])
        if not boundary:
            continue
        chunk = steps[start:index]
        family = Counter(s["action_family"] for s in chunk).most_common(1)[0][0]
        failures = sum(not s["ok"] for s in chunk)
        phases.append({
            "id": len(phases), "name": family.capitalize(), "family": family,
            "start": start, "end": index - 1, "steps": len(chunk),
            "substate": "Recovery" if failures and chunk[-1]["ok"] else (
                "Blocked" if failures else "Progress"),
            "failures": failures,
        })
        start = index
    return phases


def rework_count(steps: list[dict]) -> int:
    """Repeated action signatures and change -> inspect -> change loops."""
    signatures = Counter((s["tool"], json.dumps(s["args"], sort_keys=True)) for s in steps)
    repeated = sum(max(0, count - 1) for count in signatures.values())
    families = [s["action_family"] for s in steps]
    loops = sum(families[i:i + 3] == ["change", "inspect", "change"]
                for i in range(max(0, len(families) - 2)))
    return repeated + loops


def _telemetry_text(steps: list[dict]) -> str:
    return "\n".join(
        f"{s['ordinal'] + 1}. {s['tool']} [{s['action_family']}] "
        f"{'ok' if s['ok'] else 'failed'}: {s['summary']} args={json.dumps(s['args'], sort_keys=True)}"
        for s in steps
    )


def _json(prompt: str) -> dict:
    value = llm.generate_json(prompt)
    return value if isinstance(value, dict) else {}


def analyze(trajectory_id: int, prompt: str, steps: list[dict]) -> None:
    phases = segment_phases(steps)
    telemetry = _telemetry_text(steps)
    fallback1 = "; ".join(f"{p['name']} ({p['steps']} steps)" for p in phases) or "No tool actions"
    try:
        layer1_data = _json(
            "Describe this agent workflow using only the chronological tool telemetry below. "
            "Do not infer hidden reasoning, task text, or tool results. Mention failures and recovery. "
            'Return JSON {"workflow": "2-5 grounded sentences"}.\n\n' + telemetry)
        layer1 = str(layer1_data.get("workflow") or fallback1)[:3000]
        layer2_data = _json(
            "Compress the grounded workflow into one succinct developer activity. Do not add facts. "
            'Return JSON {"activity": "one sentence"}.\n\nWorkflow:\n' + layer1)
        layer2 = str(layer2_data.get("activity") or fallback1)[:600]
        existing = [r["category"] for r in q(
            "SELECT category FROM trajectories WHERE category <> 'Unclassified' GROUP BY category ORDER BY count(*) DESC LIMIT 20")]
        tax = _json(
            "Assign this activity to a stable workflow taxonomy. Prefer an existing category when it fits. "
            "Otherwise create a short category of at most five words. "
            f'Existing: {json.dumps(existing)}. Return JSON {{"category":"..."}}.\nActivity: {layer2}')
        category = str(tax.get("category") or phases[0]["name"] if phases else "Unclassified")[:100]
        macro = _json(
            "Name the user's macro intent in at most six words. Return JSON "
            f'{{"intent":"..."}}.\nUser request:\n{prompt[:1200]}')
        macro_intent = str(macro.get("intent") or category)[:120]
        exec_("""UPDATE trajectories SET status = 'ready', layer1 = %s, layer2 = %s,
                    category = %s, macro_intent = %s, phases = %s, completed_at = now()
                  WHERE id = %s""",
              (layer1, layer2, category, macro_intent, json.dumps(phases), trajectory_id))
    except Exception as error:  # noqa: BLE001 -- keep grounded fallback available
        exec_("""UPDATE trajectories SET status = 'fallback', layer1 = %s, layer2 = %s,
                    category = %s, macro_intent = %s, phases = %s, completed_at = now()
                  WHERE id = %s""",
              (fallback1, fallback1, phases[0]["name"] if phases else "Unclassified",
               "Unavailable", json.dumps(phases), trajectory_id))


def harvest(session_id: int, prompt: str, trace: list[dict], model: str) -> int:
    steps = normalize_steps(trace)
    row = q1("""INSERT INTO trajectories
                  (session_id, prompt, status, model, step_count, failure_count, rework_count, phases)
                VALUES (%s, %s, 'processing', %s, %s, %s, %s, %s) RETURNING id""",
             (session_id, prompt[:8000], model[:100], len(steps),
              sum(not s["ok"] for s in steps), rework_count(steps), json.dumps(segment_phases(steps))))
    trajectory_id = int(row["id"])
    for step in steps:
        exec_("""INSERT INTO trajectory_steps
                   (trajectory_id, ordinal, tool, action_family, args, summary, ok)
                 VALUES (%s, %s, %s, %s, %s, %s, %s)""",
              (trajectory_id, step["ordinal"], step["tool"], step["action_family"],
               json.dumps(step["args"]), step["summary"], step["ok"]))
    _WORKERS.submit(analyze, trajectory_id, prompt, steps)
    return trajectory_id
