"""Canonical temporal fact representations, evidence, and bounded AI work."""

from __future__ import annotations

import hashlib
import json
import typing as t

from mari_server.identity import context as access
from mari_server.persistence.postgres import connection as db


def _json(value: t.Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def ensure_run_assertions(run_id: int, *, model: str = "") -> list[dict]:
    """Create one immutable proposed assertion for each staged candidate."""
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        conn.execute(
            """INSERT INTO fact_assertions (
                 project_id, candidate_id, run_id, source_document_id, claim,
                 structured_claim, extraction_schema, status, confidence,
                 extraction_model, extraction_recipe, content_hash, recorded_from
               )
               SELECT c.project_id, c.id, c.run_id, c.document_id, c.claim,
                      c.structured_claim, 'fact-assertion-v1',
                      CASE c.review_status WHEN 'rejected' THEN 'rejected'
                                           WHEN 'accepted' THEN 'pending_review'
                                           ELSE 'proposed' END,
                      greatest(0, least(1, c.confidence)), %s, c.extraction_recipe,
                      encode(sha256(convert_to(c.claim || '|' || c.structured_claim::text, 'UTF8')), 'hex'),
                      c.created_at
                 FROM fact_extraction_candidates c
                WHERE c.project_id = %s AND c.run_id = %s
               ON CONFLICT (project_id, candidate_id) WHERE candidate_id IS NOT NULL
               DO UPDATE SET structured_claim = EXCLUDED.structured_claim,
                 extraction_model = EXCLUDED.extraction_model,
                 extraction_recipe = EXCLUDED.extraction_recipe,
                 content_hash = EXCLUDED.content_hash""",
            (model, project_id, run_id),
        )
        return conn.execute(
            """SELECT a.*, c.source_label, c.evidence, c.document_id
                 FROM fact_assertions a
                 JOIN fact_extraction_candidates c ON c.project_id = a.project_id
                  AND c.id = a.candidate_id
                WHERE a.project_id = %s AND a.run_id = %s
                ORDER BY a.id""",
            (project_id, run_id),
        ).fetchall()


def representation_subjects(run_id: int) -> list[dict]:
    """Assertions participating in the active fact embedding space."""
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT a.id AS assertion_id, a.fact_id, a.candidate_id, a.claim,
                      a.structured_claim, a.content_hash, a.status
                 FROM fact_assertions a
                WHERE a.project_id = %s
                  AND (a.run_id = %s OR (a.fact_id IS NOT NULL AND a.status = 'active'))
                ORDER BY a.fact_id NULLS LAST, a.candidate_id NULLS LAST, a.id""",
            (project_id, run_id),
        ).fetchall()
    return [{**row, "structured_claim": _json(row.get("structured_claim"))} for row in rows]


def update_assertion_structure(assertion_id: int, structured_claim: dict, *,
                               valid_from: t.Any = None, valid_to: t.Any = None) -> None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        conn.execute(
            """UPDATE fact_assertions
                  SET structured_claim = %s, valid_from = %s, valid_to = %s,
                      content_hash = encode(sha256(convert_to(claim || '|' || %s::jsonb::text,
                                                              'UTF8')), 'hex')
                WHERE project_id = %s AND id = %s AND status IN ('proposed', 'pending_review')""",
            (json.dumps(structured_claim), valid_from, valid_to, json.dumps(structured_claim),
             project_id, assertion_id),
        )


def component_hashes(embedding_profile: str, representation_profile: str) -> dict[int, str]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT assertion_id,
                      string_agg(content_hash, ':' ORDER BY ordinal) AS component_hash
                 FROM fact_representation_components
                WHERE project_id = %s AND embedding_profile = %s
                  AND representation_profile = %s
                GROUP BY assertion_id""",
            (project_id, embedding_profile, representation_profile),
        ).fetchall()
    return {int(row["assertion_id"]): str(row["component_hash"]) for row in rows}


def replace_components(assertion_id: int, *, embedding_profile: str,
                       representation_profile: str, provider: str, model: str,
                       components: list[dict]) -> None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        owned = conn.execute(
            "SELECT 1 FROM fact_assertions WHERE project_id = %s AND id = %s",
            (project_id, assertion_id),
        ).fetchone()
        if not owned:
            raise ValueError("Fact assertion not found")
        conn.execute(
            """DELETE FROM fact_representation_components
                WHERE project_id = %s AND assertion_id = %s
                  AND embedding_profile = %s AND representation_profile = %s""",
            (project_id, assertion_id, embedding_profile, representation_profile),
        )
        for ordinal, component in enumerate(components):
            vector = list(component["embedding"])
            text = str(component["text"])
            conn.execute(
                """INSERT INTO fact_representation_components (
                     project_id, assertion_id, embedding_profile, representation_profile,
                     ordinal, component_role, rendered_text, content_hash, provider,
                     model, dimensions, embedding
                   ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector)""",
                (project_id, assertion_id, embedding_profile, representation_profile,
                 ordinal, str(component["role"]), text,
                 hashlib.sha256(text.encode()).hexdigest(), provider, model,
                 len(vector), str(vector)),
            )


def assertion_components(assertion_id: int, embedding_profile: str,
                         representation_profile: str) -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute(
            """SELECT ordinal, component_role, rendered_text, content_hash,
                      provider, model, dimensions, embedding
                 FROM fact_representation_components
                WHERE project_id = %s AND assertion_id = %s
                  AND embedding_profile = %s AND representation_profile = %s
                ORDER BY ordinal""",
            (project_id, assertion_id, embedding_profile, representation_profile),
        ).fetchall()


def assertion_neighbors(assertion_id: int, embedding_profile: str,
                        representation_profile: str, *, limit: int,
                        minimum_similarity: float) -> list[dict]:
    """Asymmetric mean MaxSim over fact component sets in PostgreSQL."""
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute(
            """WITH query_components AS (
                 SELECT id, embedding
                   FROM fact_representation_components
                  WHERE project_id = %s AND assertion_id = %s
                    AND embedding_profile = %s AND representation_profile = %s
               ), per_component AS (
                 SELECT target.assertion_id, query.id AS query_component_id,
                        max(1 - (target.embedding <=> query.embedding)) AS similarity
                   FROM query_components query
                   JOIN fact_representation_components target
                     ON target.project_id = %s
                    AND target.embedding_profile = %s
                    AND target.representation_profile = %s
                    AND target.assertion_id <> %s
                   JOIN fact_assertions assertion ON assertion.project_id = target.project_id
                    AND assertion.id = target.assertion_id
                    AND assertion.status = 'active'
                  GROUP BY target.assertion_id, query.id
               ), scored AS (
                 SELECT assertion_id, avg(similarity) AS similarity
                   FROM per_component GROUP BY assertion_id
               )
               SELECT a.id AS assertion_id, a.fact_id, a.claim, a.valid_from, a.valid_to,
                      a.recorded_from, scored.similarity, f.status AS fact_status,
                      f.criticality, f.owner_name
                 FROM scored
                 JOIN fact_assertions a ON a.project_id = %s AND a.id = scored.assertion_id
                 JOIN facts f ON f.project_id = a.project_id AND f.id = a.fact_id
                WHERE scored.similarity >= %s
                ORDER BY scored.similarity DESC, a.id LIMIT %s""",
            (project_id, assertion_id, embedding_profile, representation_profile,
             project_id, embedding_profile, representation_profile, assertion_id,
             project_id, minimum_similarity, limit),
        ).fetchall()


def evidence_neighbors(assertion_id: int, embedding_profile: str,
                       representation_profile: str, *, limit: int,
                       minimum_similarity: float,
                       exclude_document_id: int | None = None) -> list[dict]:
    """Embedding-first evidence spans, ranked by mean component similarity."""
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute(
            """WITH query_components AS (
                 SELECT id, embedding
                   FROM fact_representation_components
                  WHERE project_id = %s AND assertion_id = %s
                    AND embedding_profile = %s AND representation_profile = %s
               ), scored AS (
                 SELECT ce.chunk_id, ce.document_id,
                        avg(1 - (ce.embedding <=> query.embedding)) AS similarity
                   FROM query_components query
                   JOIN chunk_embeddings ce ON ce.project_id = %s
                    AND ce.embedding_profile = %s AND ce.purpose = 'document'
                   JOIN chunks c ON c.project_id = ce.project_id AND c.id = ce.chunk_id
                    AND c.content_hash = ce.content_hash
                  WHERE (%s::integer IS NULL OR ce.document_id <> %s::integer)
                  GROUP BY ce.chunk_id, ce.document_id
               )
               SELECT scored.chunk_id, scored.document_id, scored.similarity,
                      c.content AS quote, c.content_hash, d.title, d.updated_src,
                      d.source, jsonb_build_object(
                        'visibility', d.acl_visibility,
                        'principals', d.acl_principals
                      ) AS acl
                 FROM scored
                 JOIN chunks c ON c.project_id = %s AND c.id = scored.chunk_id
                 JOIN documents d ON d.project_id = c.project_id AND d.id = scored.document_id
                WHERE scored.similarity >= %s
                ORDER BY scored.similarity DESC, scored.chunk_id LIMIT %s""",
            (project_id, assertion_id, embedding_profile, representation_profile,
             project_id, embedding_profile, exclude_document_id, exclude_document_id,
             project_id, minimum_similarity, limit),
        ).fetchall()


def upsert_evidence_span(row: dict) -> int:
    project_id = access.require_current_access().project_id
    quote = str(row.get("quote") or "")
    content_hash = str(row.get("content_hash") or hashlib.sha256(quote.encode()).hexdigest())
    with db.connect() as conn, conn.transaction():
        result = conn.execute(
            """INSERT INTO evidence_spans (
                 project_id, document_id, chunk_id, start_offset, end_offset,
                 quote, content_hash, acl,
                 source_authority, revised_at
               ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (project_id, document_id, content_hash, start_offset, end_offset)
               DO UPDATE SET quote = EXCLUDED.quote, acl = EXCLUDED.acl,
                 source_authority = EXCLUDED.source_authority, revised_at = EXCLUDED.revised_at
               RETURNING id""",
            (project_id, int(row["document_id"]), row.get("chunk_id"), 0, max(1, len(quote)),
             quote, content_hash, json.dumps(row.get("acl") or {}),
             str(row.get("source_authority") or "unrated"), row.get("revised_at")),
        ).fetchone()
    return int(result["id"])


def replace_embedding_relations(assertion_id: int, neighbors: list[dict], *,
                                retrieval_profile: str, generation: str = "postgres") -> None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        conn.execute(
            """UPDATE fact_relations SET active = false
                WHERE project_id = %s AND source_assertion_id = %s
                  AND decision_kind = 'embedding' AND retrieval_profile = %s""",
            (project_id, assertion_id, retrieval_profile),
        )
        for row in neighbors:
            score = float(row["similarity"])
            conn.execute(
                """INSERT INTO fact_relations (
                     project_id, source_assertion_id, target_assertion_id, relation,
                     approximate_score, exact_score, decision_kind, confidence,
                     retrieval_profile, index_generation, active
                   ) VALUES (%s, %s, %s, 'related', %s, %s, 'embedding', %s, %s, %s, true)
                   ON CONFLICT (project_id, source_assertion_id, target_assertion_id,
                                relation, retrieval_profile)
                   DO UPDATE SET approximate_score = EXCLUDED.approximate_score,
                     exact_score = EXCLUDED.exact_score, confidence = EXCLUDED.confidence,
                     index_generation = EXCLUDED.index_generation,
                     observed_at = now(), active = true""",
                (project_id, assertion_id, int(row["assertion_id"]), score, score,
                 max(0.0, min(1.0, score)), retrieval_profile, generation),
            )


def configure_llm_budget(run_id: int, *, stage: str, purpose: str, provider: str,
                         model: str, recipe: str, max_calls: int,
                         max_input_tokens: int, max_output_tokens: int,
                         visible_config: dict) -> dict:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        return conn.execute(
            """INSERT INTO fact_llm_invocations (
                 project_id, run_id, stage, purpose, provider, model, recipe,
                 max_calls, max_input_tokens, max_output_tokens, visible_config
               ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (project_id, run_id, stage, purpose)
               DO UPDATE SET provider = EXCLUDED.provider, model = EXCLUDED.model,
                 recipe = EXCLUDED.recipe, max_calls = EXCLUDED.max_calls,
                 max_input_tokens = EXCLUDED.max_input_tokens,
                 max_output_tokens = EXCLUDED.max_output_tokens,
                 visible_config = EXCLUDED.visible_config
               RETURNING *""",
            (project_id, run_id, stage, purpose, provider, model, recipe,
             max_calls, max_input_tokens, max_output_tokens, json.dumps(visible_config)),
        ).fetchone()


def reserve_llm_call(run_id: int, *, stage: str, purpose: str,
                     estimated_input_tokens: int, output_tokens: int) -> bool:
    """Atomically reserve one bounded, user-visible LLM invocation."""
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        row = conn.execute(
            """UPDATE fact_llm_invocations
                  SET calls_used = calls_used + 1,
                      input_tokens = input_tokens + %s,
                      output_tokens = output_tokens + %s,
                      status = 'running', started_at = COALESCE(started_at, now())
                WHERE project_id = %s AND run_id = %s AND stage = %s AND purpose = %s
                  AND calls_used < max_calls
                  AND input_tokens + %s <= max_input_tokens
                  AND output_tokens + %s <= max_output_tokens
                RETURNING id""",
            (max(0, estimated_input_tokens), max(0, output_tokens), project_id, run_id,
             stage, purpose, max(0, estimated_input_tokens), max(0, output_tokens)),
        ).fetchone()
        if row:
            return True
        conn.execute(
            """UPDATE fact_llm_invocations SET status = 'exhausted', completed_at = now()
                WHERE project_id = %s AND run_id = %s AND stage = %s AND purpose = %s
                  AND status NOT IN ('completed', 'skipped')""",
            (project_id, run_id, stage, purpose),
        )
        return False


def complete_llm_budget(run_id: int, *, stage: str, purpose: str,
                        status: str = "completed") -> None:
    if status not in {"completed", "skipped", "failed", "exhausted"}:
        raise ValueError("Invalid LLM budget status")
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        conn.execute(
            """UPDATE fact_llm_invocations SET status = %s, completed_at = now()
                WHERE project_id = %s AND run_id = %s AND stage = %s AND purpose = %s""",
            (status, project_id, run_id, stage, purpose),
        )


def llm_budgets(run_id: int) -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute(
            """SELECT stage, purpose, provider, model, recipe, max_calls,
                      max_input_tokens, max_output_tokens, calls_used,
                      input_tokens, output_tokens, cost_usd, status, visible_config
                 FROM fact_llm_invocations
                WHERE project_id = %s AND run_id = %s ORDER BY id""",
            (project_id, run_id),
        ).fetchall()
