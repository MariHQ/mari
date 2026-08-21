"""WorkflowView hierarchy matching over the configured HTTP embedding port."""

from __future__ import annotations

import json

from mari_components.trajectories import match_hierarchy
from mari_server.persistence.postgres import trajectories as store
from mari_server.providers import models as llm


def _json(value, default):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return value if isinstance(value, type(default)) else default


def _phase_for(ordinal: int, phases: list[dict]) -> int:
    for index, phase in enumerate(phases):
        if int(phase.get("start") or 0) <= ordinal <= int(phase.get("end") or 0):
            return index
    return 0


def _texts(row: dict) -> tuple[list[str], list[dict], list[dict]]:
    phases = _json(row.get("phases"), [])
    steps = _json(row.get("steps"), [])
    texts = [f"{row.get('name', '')}. {row.get('description', '')}"]
    texts.extend(
        f"{phase.get('name', '')}. {phase.get('family', '')}. {phase.get('substate', '')}"
        for phase in phases
    )
    texts.extend(
        f"{step.get('tool', '')}. {step.get('summary', '')}. "
        f"{json.dumps(step.get('arguments') or {}, sort_keys=True)}"
        for step in steps
    )
    return texts, phases, steps


def _ensure_indexes(rows: list[dict]) -> list[dict]:
    profile = llm.embedding_profile()
    pending: list[tuple[dict, list[str], list[dict], list[dict]]] = []
    all_texts: list[str] = []
    for row in rows:
        cached = _json(row.get("match_index"), {})
        if row.get("embedding_profile") == profile and cached.get("embedding"):
            row["_index"] = cached
            continue
        texts, phases, steps = _texts(row)
        pending.append((row, texts, phases, steps))
        all_texts.extend(texts)
    vectors = iter(llm.embed_many(all_texts)) if all_texts else iter(())
    for row, texts, phases, steps in pending:
        embedded = [next(vectors, None) for _ in texts]
        if any(vector is None for vector in embedded):
            continue
        phase_vectors = embedded[1:1 + len(phases)]
        step_vectors = embedded[1 + len(phases):]
        index = {
            "embedding": embedded[0],
            "phases": [{**phase, "embedding": vector}
                       for phase, vector in zip(phases, phase_vectors)],
            "steps": [{**step, "phase_index": _phase_for(int(step.get("ordinal") or 0), phases),
                       "embedding": vector}
                      for step, vector in zip(steps, step_vectors)],
        }
        store.save_match_index(int(row["id"]), profile, index)
        row["_index"] = index
    return [row for row in rows if row.get("_index")]


def select(query: str, available_tools: set[str] | None = None) -> dict | None:
    """Match intent → phase → step using cached provider embeddings."""
    query_vector = llm.embed(query)
    if not query_vector:
        return None
    rows = _ensure_indexes(store.active_workflows(50))
    candidates = []
    by_id = {}
    for row in rows:
        index = row["_index"]
        steps = [{**step, "_source_index": position}
                 for position, step in enumerate(index.get("steps") or [])]
        if available_tools is not None:
            steps = [step for step in steps if step.get("tool") in available_tools]
        if not steps:
            continue
        candidate = {"id": row["id"], "embedding": index["embedding"],
                     "phases": index.get("phases") or [], "steps": steps}
        candidates.append(candidate)
        by_id[int(row["id"])] = row
    match = match_hierarchy(query_vector, candidates, minimum_score=0.0)
    if match is None:
        return None
    row = by_id[match.workflow_id]
    if match.workflow_score < float(row.get("match_threshold") or 0.55):
        return None
    matched_candidate = next(row for row in candidates if int(row["id"]) == match.workflow_id)
    source_step_index = int(matched_candidate["steps"][match.step_index]["_source_index"])
    steps = list(row["_index"].get("steps") or [])
    chosen = [step for index, step in enumerate(steps) if index >= source_step_index]
    if available_tools is not None:
        chosen = [step for step in chosen if step.get("tool") in available_tools]
    return {**row, "steps": chosen, "match": {
        "workflow_score": match.workflow_score, "phase_index": match.phase_index,
        "phase_score": match.phase_score, "step_index": source_step_index,
        "step_score": match.step_score,
    }}


def guidance(selected: dict | None) -> str:
    if not selected:
        return ""
    match = selected["match"]
    return (
        "\n\nHUMAN-CODIFIED WORKFLOW SELECTED:\n"
        f"{selected['name']} (workflow={match['workflow_score']:.3f}, "
        f"phase={match['phase_index']}, step={match['step_index']}). "
        "Continue from the reviewed calls and enforce permissions."
    )


def retrieval_query(query: str, selected: dict | None = None) -> str:
    selected = selected if selected is not None else select(query, {"search"})
    if not selected:
        return query
    for step in selected["steps"]:
        arguments = step.get("arguments")
        if step.get("tool") == "search" and isinstance(arguments, dict):
            return str(arguments.get("query") or query)
    return query
