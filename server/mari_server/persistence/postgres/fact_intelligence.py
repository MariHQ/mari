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


def replace_embedding_evidence(assertion_id: int, neighbors: list[dict], *,
                               retrieval_profile: str) -> list[int]:
    """Persist retrieved context as an explicitly insufficient evidence group."""
    project_id = access.require_current_access().project_id
    span_ids = [upsert_evidence_span({
        **row,
        "acl": row.get("acl") or {},
        "revised_at": row.get("updated_src"),
    }) for row in neighbors]
    context_hash = hashlib.sha256(
        ":".join(str(value) for value in span_ids).encode()
    ).hexdigest()
    with db.connect() as conn, conn.transaction():
        conn.execute(
            """UPDATE fact_evidence_groups SET active = false
                WHERE project_id = %s AND assertion_id = %s
                  AND decision_kind = 'embedding' AND retrieval_profile = %s""",
            (project_id, assertion_id, retrieval_profile),
        )
        if not span_ids:
            return []
        group = conn.execute(
            """INSERT INTO fact_evidence_groups (
                 project_id, assertion_id, verdict, sufficient, confidence,
                 rationale, decision_kind, context_hash, retrieval_profile
               ) VALUES (%s, %s, 'insufficient', false, 0,
                         'Embedding candidates awaiting adjudication', 'embedding', %s, %s)
               RETURNING id""",
            (project_id, assertion_id, context_hash, retrieval_profile),
        ).fetchone()
        group_id = int(group["id"])
        by_id = {span_id: row for span_id, row in zip(span_ids, neighbors, strict=True)}
        for ordinal, span_id in enumerate(span_ids):
            conn.execute(
                """INSERT INTO fact_evidence_group_members
                     (group_id, evidence_span_id, role, ordinal, similarity)
                   VALUES (%s, %s, 'context', %s, %s)""",
                (group_id, span_id, ordinal, float(by_id[span_id]["similarity"])),
            )
    return span_ids


def adjudication_packet(assertion_id: int) -> dict | None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        assertion = conn.execute(
            """SELECT a.id, a.fact_id, a.candidate_id, a.claim, a.structured_claim,
                      a.valid_from, a.valid_to, a.recorded_from, a.confidence,
                      f.criticality, f.owner_name
                 FROM fact_assertions a
                 LEFT JOIN facts f ON f.project_id = a.project_id AND f.id = a.fact_id
                WHERE a.project_id = %s AND a.id = %s""",
            (project_id, assertion_id),
        ).fetchone()
        if not assertion:
            return None
        relations = conn.execute(
            """SELECT r.target_assertion_id, r.relation, r.exact_score, r.confidence,
                      target.claim, target.structured_claim, target.valid_from, target.valid_to,
                      target.recorded_from, target.fact_id, f.criticality, f.owner_name
                 FROM fact_relations r
                 JOIN fact_assertions target ON target.project_id = r.project_id
                  AND target.id = r.target_assertion_id
                 LEFT JOIN facts f ON f.project_id = target.project_id AND f.id = target.fact_id
                WHERE r.project_id = %s AND r.source_assertion_id = %s AND r.active
                ORDER BY r.exact_score DESC NULLS LAST, r.id LIMIT 20""",
            (project_id, assertion_id),
        ).fetchall()
        evidence = conn.execute(
            """SELECT span.id AS span_id, span.document_id, span.quote, span.content_hash,
                      span.source_authority, span.published_at, span.effective_from,
                      span.effective_to, span.revised_at, span.ingested_at,
                      member.similarity, d.title, d.source
                 FROM fact_evidence_groups g
                 JOIN fact_evidence_group_members member ON member.group_id = g.id
                 JOIN evidence_spans span ON span.project_id = g.project_id
                  AND span.id = member.evidence_span_id
                 JOIN documents d ON d.project_id = span.project_id AND d.id = span.document_id
                WHERE g.project_id = %s AND g.assertion_id = %s AND g.active
                  AND g.decision_kind = 'embedding'
                ORDER BY member.similarity DESC NULLS LAST, member.ordinal LIMIT 30""",
            (project_id, assertion_id),
        ).fetchall()
    return {"assertion": {**assertion, "structured_claim": _json(assertion.get("structured_claim"))},
            "relations": [{**row, "structured_claim": _json(row.get("structured_claim"))}
                          for row in relations],
            "evidence": evidence}


def save_adjudication(assertion_id: int, result: dict, *, model: str, recipe: str,
                      context_hash: str, retrieval_profile: str) -> None:
    """Persist one bounded LLM proposal; publication remains a separate gate."""
    project_id = access.require_current_access().project_id
    relation = str(result.get("relation") or "insufficient")
    allowed = {"supports", "contradicts", "supersedes", "qualifies",
               "duplicate", "related", "insufficient"}
    if relation not in allowed:
        relation = "insufficient"
    confidence = max(0.0, min(1.0, float(result.get("confidence") or 0)))
    target_id = int(result.get("target_assertion_id") or 0)
    valid_from = result.get("valid_from")
    valid_to = result.get("valid_to")
    evidence_groups = result.get("evidence_groups")
    if not isinstance(evidence_groups, list):
        evidence_groups = []
    with db.connect() as conn, conn.transaction():
        conn.execute(
            """UPDATE fact_evidence_groups SET active = false
                WHERE project_id = %s AND assertion_id = %s AND decision_kind = 'llm'""",
            (project_id, assertion_id),
        )
        conn.execute(
            """UPDATE fact_relations SET active = false
                WHERE project_id = %s AND source_assertion_id = %s AND decision_kind = 'llm'""",
            (project_id, assertion_id),
        )
        conn.execute(
            """UPDATE fact_assertions SET adjudication = %s,
                      confidence = greatest(confidence, %s),
                      confidence_reason = %s
                WHERE project_id = %s AND id = %s
                  AND status IN ('proposed', 'pending_review')""",
            (json.dumps(result, default=str), confidence, str(result.get("reason") or "")[:2000],
             project_id, assertion_id),
        )
        if valid_from is not None or valid_to is not None:
            conn.execute(
                """UPDATE fact_assertions SET valid_from = COALESCE(%s, valid_from),
                          valid_to = COALESCE(%s, valid_to), confidence = %s,
                          confidence_reason = %s
                    WHERE project_id = %s AND id = %s
                      AND status IN ('proposed', 'pending_review')""",
                (valid_from, valid_to, confidence, str(result.get("reason") or "")[:2000],
                 project_id, assertion_id),
            )
        if target_id and target_id != assertion_id:
            target = conn.execute(
                "SELECT 1 FROM fact_assertions WHERE project_id = %s AND id = %s",
                (project_id, target_id),
            ).fetchone()
            if target:
                conn.execute(
                    """INSERT INTO fact_relations (
                         project_id, source_assertion_id, target_assertion_id, relation,
                         decision_kind, decision_model, decision_recipe, confidence,
                         rationale, retrieval_profile, active
                       ) VALUES (%s, %s, %s, %s, 'llm', %s, %s, %s, %s, %s, true)
                       ON CONFLICT (project_id, source_assertion_id, target_assertion_id,
                                    relation, retrieval_profile)
                       DO UPDATE SET decision_kind = 'llm', decision_model = EXCLUDED.decision_model,
                         decision_recipe = EXCLUDED.decision_recipe,
                         confidence = EXCLUDED.confidence, rationale = EXCLUDED.rationale,
                         observed_at = now(), active = true""",
                    (project_id, assertion_id, target_id, relation, model, recipe, confidence,
                     str(result.get("reason") or "")[:4000], retrieval_profile),
                )
        for raw_group in evidence_groups[:10]:
            if not isinstance(raw_group, dict):
                continue
            verdict = str(raw_group.get("verdict") or relation)
            if verdict not in {"supports", "contradicts", "qualifies", "insufficient"}:
                verdict = "insufficient"
            span_ids = sorted({int(value) for value in raw_group.get("span_ids") or ()
                               if str(value).isdigit()})[:20]
            if not span_ids:
                continue
            available = conn.execute(
                "SELECT id FROM evidence_spans WHERE project_id = %s AND id = ANY(%s)",
                (project_id, span_ids),
            ).fetchall()
            available_ids = {int(row["id"]) for row in available}
            if not available_ids:
                continue
            group = conn.execute(
                """INSERT INTO fact_evidence_groups (
                     project_id, assertion_id, verdict, sufficient, confidence,
                     rationale, decision_kind, decision_model, decision_recipe,
                     context_hash, retrieval_profile, reviewer, reviewed_at
                   ) VALUES (%s, %s, %s, %s, %s, %s, 'llm', %s, %s, %s, %s, %s, now())
                   RETURNING id""",
                (project_id, assertion_id, verdict, bool(raw_group.get("sufficient")),
                 max(0.0, min(1.0, float(raw_group.get("confidence") or confidence))),
                 str(raw_group.get("explanation") or "")[:4000], model, recipe,
                 context_hash, retrieval_profile, model),
            ).fetchone()
            group_id = int(group["id"])
            role = {"supports": "support", "contradicts": "contradiction",
                    "qualifies": "qualification"}.get(verdict, "context")
            for ordinal, span_id in enumerate(span_ids):
                if span_id in available_ids:
                    conn.execute(
                        """INSERT INTO fact_evidence_group_members
                             (group_id, evidence_span_id, role, ordinal)
                           VALUES (%s, %s, %s, %s)""",
                        (group_id, span_id, role, ordinal),
                    )


def run_assertion_ids(run_id: int) -> list[int]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id FROM fact_assertions WHERE project_id = %s AND run_id = %s ORDER BY id",
            (project_id, run_id),
        ).fetchall()
    return [int(row["id"]) for row in rows]


def cluster_graph(run_id: int, *, minimum_similarity: float) -> tuple[list[dict], list[dict]]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        nodes = conn.execute(
            """WITH member_ids AS (
                 SELECT id FROM fact_assertions
                  WHERE project_id = %s AND run_id = %s
                 UNION
                 SELECT r.target_assertion_id
                   FROM fact_relations r
                   JOIN fact_assertions seed ON seed.project_id = r.project_id
                    AND seed.id = r.source_assertion_id
                  WHERE seed.project_id = %s AND seed.run_id = %s AND r.active
                    AND COALESCE(r.exact_score, r.approximate_score, 0) >= %s
               )
               SELECT a.id, a.fact_id, a.candidate_id, a.claim, a.valid_from, a.valid_to
                 FROM member_ids ids
                 JOIN fact_assertions a ON a.project_id = %s AND a.id = ids.id
                ORDER BY a.id""",
            (project_id, run_id, project_id, run_id, minimum_similarity, project_id),
        ).fetchall()
        edges = conn.execute(
            """SELECT r.source_assertion_id AS source, r.target_assertion_id AS target,
                      COALESCE(r.exact_score, r.approximate_score, 0) AS score
                 FROM fact_relations r
                 JOIN fact_assertions seed ON seed.project_id = r.project_id
                  AND seed.id = r.source_assertion_id
                WHERE r.project_id = %s AND seed.run_id = %s AND r.active
                  AND COALESCE(r.exact_score, r.approximate_score, 0) >= %s
                ORDER BY r.source_assertion_id, r.target_assertion_id""",
            (project_id, run_id, minimum_similarity),
        ).fetchall()
    return nodes, edges


def replace_clusters(run_id: int, clusters: list[dict], *, embedding_profile: str,
                     retrieval_profile: str, generation: str) -> list[int]:
    project_id = access.require_current_access().project_id
    created: list[int] = []
    with db.connect() as conn, conn.transaction():
        for cluster in clusters:
            row = conn.execute(
                """INSERT INTO fact_clusters (
                     project_id, stable_key, label, summary, embedding_profile,
                     retrieval_profile, generation, lifecycle, previous_cluster_ids,
                     label_kind, label_model
                   ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (project_id, stable_key, generation)
                   DO UPDATE SET label = EXCLUDED.label, summary = EXCLUDED.summary,
                     lifecycle = EXCLUDED.lifecycle,
                     previous_cluster_ids = EXCLUDED.previous_cluster_ids,
                     label_kind = EXCLUDED.label_kind, label_model = EXCLUDED.label_model
                   RETURNING id""",
                (project_id, cluster["stable_key"], cluster.get("label") or "",
                 cluster.get("summary") or "", embedding_profile, retrieval_profile,
                 generation, cluster.get("lifecycle") or "created",
                 list(cluster.get("previous_cluster_ids") or []),
                 cluster.get("label_kind") or "none", cluster.get("label_model") or ""),
            ).fetchone()
            cluster_id = int(row["id"])
            created.append(cluster_id)
            conn.execute("DELETE FROM fact_cluster_memberships WHERE cluster_id = %s", (cluster_id,))
            for member in cluster.get("members") or []:
                conn.execute(
                    """INSERT INTO fact_cluster_memberships
                         (cluster_id, assertion_id, membership_score, explanation)
                       VALUES (%s, %s, %s, %s)""",
                    (cluster_id, int(member["assertion_id"]),
                     float(member.get("score") or 0), str(member.get("explanation") or "")),
                )
    return created


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


def candidate_assertion_id(candidate_id: int) -> int | None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        row = conn.execute(
            "SELECT id FROM fact_assertions WHERE project_id = %s AND candidate_id = %s",
            (project_id, candidate_id),
        ).fetchone()
    return int(row["id"]) if row else None


def fact_current_assertion_id(fact_id: int) -> int | None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        row = conn.execute(
            "SELECT current_assertion_id FROM facts WHERE project_id = %s AND id = %s",
            (project_id, fact_id),
        ).fetchone()
    return int(row["current_assertion_id"]) if row and row.get("current_assertion_id") else None


def assertion_intelligence(assertion_id: int, *, embedding_profile: str = "") -> dict | None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        assertion = conn.execute(
            """SELECT a.*, f.canonical_key, f.criticality, f.owner_name
                 FROM fact_assertions a
                 LEFT JOIN facts f ON f.project_id = a.project_id AND f.id = a.fact_id
                WHERE a.project_id = %s AND a.id = %s""",
            (project_id, assertion_id),
        ).fetchone()
        if not assertion:
            return None
        profile_predicate = "AND embedding_profile = %s" if embedding_profile else ""
        component_args = (project_id, assertion_id, embedding_profile) if embedding_profile else (
            project_id, assertion_id,
        )
        components = conn.execute(
            f"""SELECT ordinal, component_role, rendered_text, content_hash,
                       embedding_profile, representation_profile, provider, model, dimensions
                  FROM fact_representation_components
                 WHERE project_id = %s AND assertion_id = %s {profile_predicate}
                 ORDER BY embedding_profile, representation_profile, ordinal""",
            component_args,
        ).fetchall()
        relations = conn.execute(
            """SELECT r.id, r.target_assertion_id, r.relation,
                      r.approximate_score, r.exact_score, r.decision_kind,
                      r.decision_model, r.confidence, r.rationale, r.observed_at,
                      target.claim AS target_claim, target.fact_id AS target_fact_id,
                      target.valid_from AS target_valid_from,
                      target.valid_to AS target_valid_to
                 FROM fact_relations r
                 JOIN fact_assertions target ON target.project_id = r.project_id
                  AND target.id = r.target_assertion_id
                WHERE r.project_id = %s AND r.source_assertion_id = %s AND r.active
                ORDER BY CASE r.decision_kind WHEN 'human' THEN 0 WHEN 'llm' THEN 1 ELSE 2 END,
                         r.exact_score DESC NULLS LAST, r.id""",
            (project_id, assertion_id),
        ).fetchall()
        group_rows = conn.execute(
            """SELECT id, verdict, sufficient, confidence, rationale, decision_kind,
                      decision_model, context_hash, retrieval_profile, reviewer,
                      reviewed_at, created_at
                 FROM fact_evidence_groups
                WHERE project_id = %s AND assertion_id = %s AND active
                ORDER BY CASE decision_kind WHEN 'human' THEN 0 WHEN 'llm' THEN 1 ELSE 2 END, id""",
            (project_id, assertion_id),
        ).fetchall()
        groups: list[dict] = []
        for group in group_rows:
            spans = conn.execute(
                """SELECT span.id, span.document_id, span.quote, span.content_hash,
                          span.source_authority, span.published_at, span.effective_from,
                          span.effective_to, span.revised_at, span.ingested_at,
                          member.role, member.ordinal, member.similarity,
                          d.title AS document_title, d.source
                     FROM fact_evidence_group_members member
                     JOIN evidence_spans span ON span.project_id = %s
                      AND span.id = member.evidence_span_id
                     JOIN documents d ON d.project_id = span.project_id AND d.id = span.document_id
                    WHERE member.group_id = %s ORDER BY member.ordinal, span.id""",
                (project_id, group["id"]),
            ).fetchall()
            groups.append({**group, "spans": spans})
        clusters = conn.execute(
            """SELECT cluster.id, cluster.stable_key, cluster.label, cluster.summary,
                      cluster.generation, cluster.lifecycle, cluster.label_kind,
                      member.membership_score, member.explanation
                 FROM fact_cluster_memberships member
                 JOIN fact_clusters cluster ON cluster.project_id = %s
                  AND cluster.id = member.cluster_id
                WHERE member.assertion_id = %s
                ORDER BY cluster.created_at DESC, cluster.id DESC LIMIT 20""",
            (project_id, assertion_id),
        ).fetchall()
    return {
        "assertion": {**assertion,
                      "structured_claim": _json(assertion.get("structured_claim")),
                      "adjudication": _json(assertion.get("adjudication"))},
        "components": components, "relations": relations,
        "evidence_groups": groups, "clusters": clusters,
    }


def run_clusters(run_id: int) -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        clusters = conn.execute(
            """SELECT DISTINCT cluster.*
                 FROM fact_clusters cluster
                 JOIN fact_cluster_memberships member ON member.cluster_id = cluster.id
                 JOIN fact_assertions assertion ON assertion.project_id = cluster.project_id
                  AND assertion.id = member.assertion_id
                WHERE cluster.project_id = %s AND assertion.run_id = %s
                ORDER BY cluster.created_at DESC, cluster.id DESC""",
            (project_id, run_id),
        ).fetchall()
        output: list[dict] = []
        for cluster in clusters:
            members = conn.execute(
                """SELECT assertion.id AS assertion_id, assertion.fact_id,
                          assertion.candidate_id, assertion.claim,
                          member.membership_score, member.explanation
                     FROM fact_cluster_memberships member
                     JOIN fact_assertions assertion ON assertion.project_id = %s
                      AND assertion.id = member.assertion_id
                    WHERE member.cluster_id = %s
                    ORDER BY member.membership_score DESC, assertion.id""",
                (project_id, cluster["id"]),
            ).fetchall()
            output.append({**cluster, "members": members})
    return output


_DEPENDENCY_SEVERITY = {
    "used_by_decision": 10,
    "used_by_workflow": 8,
    "used_by_answer": 4,
    "derived_from": 5,
    "required_by_playbook": 4,
    "cited_by": 3,
}


def record_dependency(fact_id: int, *, downstream_type: str, downstream_id: str,
                      downstream_label: str, dependency_type: str,
                      provenance: dict | None = None, created_by: str = "",
                      parent_dependency_id: int | None = None) -> int:
    if dependency_type not in _DEPENDENCY_SEVERITY:
        raise ValueError("Unknown fact dependency type")
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        fact = conn.execute(
            "SELECT current_assertion_id FROM facts WHERE project_id = %s AND id = %s",
            (project_id, fact_id),
        ).fetchone()
        if not fact or not fact.get("current_assertion_id"):
            raise ValueError("Fact has no current assertion")
        row = conn.execute(
            """INSERT INTO fact_dependencies (
                 project_id, assertion_id, downstream_type, downstream_id,
                 downstream_label, dependency_type, provenance, created_by,
                 parent_dependency_id
               ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (project_id, assertion_id, downstream_type, downstream_id,
                            dependency_type)
               DO UPDATE SET downstream_label = EXCLUDED.downstream_label,
                 provenance = EXCLUDED.provenance, active_to = NULL,
                 parent_dependency_id = EXCLUDED.parent_dependency_id
               RETURNING id""",
            (project_id, fact["current_assertion_id"], downstream_type,
             downstream_id, downstream_label[:500], dependency_type,
             json.dumps(provenance or {}), created_by[:120], parent_dependency_id),
        ).fetchone()
    return int(row["id"])


def _impact_rows(conn, project_id: int, assertion_id: int) -> list[dict]:
    dependencies = conn.execute(
        """WITH RECURSIVE impacted AS (
             SELECT dependency.*, 1 AS depth
               FROM fact_dependencies dependency
              WHERE dependency.project_id = %s AND dependency.assertion_id = %s
                AND dependency.parent_dependency_id IS NULL AND dependency.active_to IS NULL
             UNION ALL
             SELECT child.*, parent.depth + 1
               FROM fact_dependencies child
               JOIN impacted parent ON child.parent_dependency_id = parent.id
              WHERE child.project_id = %s AND child.active_to IS NULL AND parent.depth < 20
           )
           SELECT * FROM impacted ORDER BY depth, id""",
        (project_id, assertion_id, project_id),
    ).fetchall()
    rows = [{
        "impact_kind": "direct" if int(row["depth"]) == 1 else "transitive",
        "target_type": str(row["downstream_type"]),
        "target_id": str(row["downstream_id"]),
        "target_label": str(row.get("downstream_label") or ""),
        "severity": _DEPENDENCY_SEVERITY.get(str(row["dependency_type"]), 1),
        "dependency_type": str(row["dependency_type"]),
        "depth": int(row["depth"]),
    } for row in dependencies]
    semantic = conn.execute(
        """SELECT other.id AS assertion_id, other.fact_id, other.claim,
                  max(COALESCE(relation.exact_score, relation.approximate_score, 0)) AS similarity
             FROM fact_relations relation
             JOIN fact_assertions other ON other.project_id = relation.project_id
              AND other.id = CASE WHEN relation.source_assertion_id = %s
                                  THEN relation.target_assertion_id
                                  ELSE relation.source_assertion_id END
            WHERE relation.project_id = %s AND relation.active
              AND %s IN (relation.source_assertion_id, relation.target_assertion_id)
            GROUP BY other.id, other.fact_id, other.claim
            ORDER BY similarity DESC LIMIT 50""",
        (assertion_id, project_id, assertion_id),
    ).fetchall()
    known = {(row["target_type"], row["target_id"]) for row in rows}
    for row in semantic:
        target_id = str(row.get("fact_id") or f"assertion:{row['assertion_id']}")
        if ("fact", target_id) not in known:
            rows.append({
                "impact_kind": "possible", "target_type": "fact", "target_id": target_id,
                "target_label": str(row["claim"]), "severity": 1,
                "dependency_type": "embedding_neighbor", "depth": 0,
                "similarity": float(row["similarity"]),
            })
    return rows


def impact_preview(fact_id: int) -> dict | None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        fact = conn.execute(
            """SELECT id, claim, current_assertion_id, criticality
                 FROM facts WHERE project_id = %s AND id = %s""",
            (project_id, fact_id),
        ).fetchone()
        if not fact or not fact.get("current_assertion_id"):
            return None
        items = _impact_rows(conn, project_id, int(fact["current_assertion_id"]))
    direct_score = sum(int(row["severity"]) for row in items if row["impact_kind"] != "possible")
    possible_score = min(5, sum(1 for row in items if row["impact_kind"] == "possible"))
    criticality_bonus = {"low": 0, "normal": 0, "high": 5, "critical": 10}.get(
        str(fact.get("criticality") or "normal"), 0,
    )
    return {"fact_id": fact_id, "assertion_id": int(fact["current_assertion_id"]),
            "claim": str(fact["claim"]), "score": direct_score + possible_score + criticality_bonus,
            "items": items}


def invalidate_fact(fact_id: int, *, reason: str, actor: str,
                    effective_at: t.Any, replacement_assertion_id: int | None = None) -> dict | None:
    """Close valid/system time and materialize a replay-safe impact event."""
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        fact = conn.execute(
            """SELECT id, claim, current_assertion_id FROM facts
                WHERE project_id = %s AND id = %s AND status NOT IN ('Invalidated', 'Retired')
                FOR UPDATE""",
            (project_id, fact_id),
        ).fetchone()
        if not fact or not fact.get("current_assertion_id"):
            return None
        assertion_id = int(fact["current_assertion_id"])
        items = _impact_rows(conn, project_id, assertion_id)
        conn.execute(
            """UPDATE fact_assertions SET status = 'invalidated', valid_to = COALESCE(valid_to, %s),
                      recorded_to = COALESCE(recorded_to, now())
                WHERE project_id = %s AND id = %s AND status = 'active'""",
            (effective_at, project_id, assertion_id),
        )
        conn.execute(
            """UPDATE facts SET status = 'Invalidated', invalidated_at = %s,
                      invalidation_reason = %s
                WHERE project_id = %s AND id = %s""",
            (effective_at, reason[:1000], project_id, fact_id),
        )
        event = conn.execute(
            """INSERT INTO fact_invalidation_events (
                 project_id, assertion_id, replacement_assertion_id, reason, actor, effective_at
               ) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
            (project_id, assertion_id, replacement_assertion_id, reason[:4000], actor[:120], effective_at),
        ).fetchone()
        event_id = int(event["id"])
        for item in items:
            conn.execute(
                """INSERT INTO fact_impact_items (
                     event_id, impact_kind, target_type, target_id, target_label, severity
                   ) VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (event_id, impact_kind, target_type, target_id) DO NOTHING""",
                (event_id, item["impact_kind"], item["target_type"], item["target_id"],
                 item["target_label"][:500], int(item["severity"])),
            )
    return {"id": fact_id, "claim": str(fact["claim"]), "event_id": event_id,
            "impact_count": len(items)}
