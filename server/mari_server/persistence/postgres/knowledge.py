"""PostgreSQL persistence for knowledge lifecycle operations."""

from __future__ import annotations

import json
import time

from mari_server.persistence.postgres import connection as db
from mari_server.identity import context as access
from mari_server.providers import models as llm


_SCAN_COLUMNS = {"facts": "facts_scanned_at", "decisions": "decisions_scanned_at"}


def scan_documents(kind: str, document_ids: list[int] | None, limit: int) -> list[dict]:
    column = _SCAN_COLUMNS[kind]
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        if document_ids:
            rows = conn.execute(
                f"""SELECT id, title, snippet, body, source, updated_src FROM documents
                     WHERE project_id = %s AND id = ANY(%s)
                     ORDER BY {column} NULLS FIRST, updated_src DESC NULLS LAST, id""",
                (project_id, list(document_ids)),
            ).fetchall()
            return rows[:limit] if limit else rows
        return conn.execute(
            f"""SELECT id, title, snippet, body, source, updated_src FROM documents
                 WHERE project_id = %s
                 ORDER BY {column} NULLS FIRST, updated_src DESC NULLS LAST, id
                 LIMIT %s""", (project_id, limit),
        ).fetchall()


def mark_scanned(kind: str, document_ids: list[int]) -> None:
    if not document_ids:
        return
    column = _SCAN_COLUMNS[kind]
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        conn.execute(
            f"UPDATE documents SET {column} = now() WHERE project_id = %s AND id = ANY(%s)",
            (project_id, list(document_ids)),
        )


def decision_statements() -> set[str]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT statement FROM decisions WHERE project_id = %s", (project_id,),
        ).fetchall()
    return {str(row["statement"]).lower() for row in rows}


def add_decision(statement: str, context: str, source: str, owner: str) -> bool:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        row = conn.execute(
            """INSERT INTO decisions
               (project_id, statement, context, status, source_label, owners)
               VALUES (%s, %s, %s, 'proposed', %s, %s)
               ON CONFLICT (project_id, statement) DO NOTHING RETURNING id""",
            (project_id, statement, context, source, [owner]),
        ).fetchone()
    return bool(row)


def fact_claims(*, verified_only: bool = False, original_case: bool = False) -> set[str]:
    project_id = access.require_current_access().project_id
    sql = "SELECT claim FROM facts WHERE project_id = %s"
    if verified_only:
        sql += " AND status = 'Verified'"
    with db.connect() as conn:
        rows = conn.execute(sql, (project_id,)).fetchall()
    return {str(row["claim"]) if original_case else str(row["claim"]).lower() for row in rows}


def add_fact(claim: str, source: str, owner: str, document_id: int | None) -> bool:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        row = conn.execute(
            """INSERT INTO facts
               (project_id, claim, source, owner_name, owner_tint, status, verified, document_id)
               VALUES (%s, %s, %s, %s, 1, 'Needs review', '—', %s)
               ON CONFLICT (project_id, claim) DO NOTHING RETURNING id""",
            (project_id, claim, source, owner, document_id),
        ).fetchone()
    return bool(row)


def stage_fact_candidates(run_id: int, candidates: list[dict]) -> int:
    """Persist model output for review without touching the fact ledger."""
    project_id = access.require_current_access().project_id
    if not candidates:
        return 0
    added = 0
    with db.connect() as conn, conn.transaction():
        for candidate in candidates:
            row = conn.execute(
                """INSERT INTO fact_extraction_candidates
                     (project_id, run_id, document_id, claim, source_label, evidence, confidence)
                   SELECT %s, r.id, %s, %s, %s, %s, %s
                     FROM workflow_runs r
                    WHERE r.project_id = %s AND r.id = %s
                   ON CONFLICT (run_id, claim) DO NOTHING RETURNING id""",
                (project_id, candidate.get("document_id"), candidate["claim"],
                 candidate.get("source") or "", candidate.get("evidence") or "",
                 float(candidate.get("confidence") or 0), project_id, run_id),
            ).fetchone()
            added += bool(row)
    return added


def fact_candidates(run_id: int) -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute(
            """SELECT c.*, d.title AS document_title
                 FROM fact_extraction_candidates c
                 LEFT JOIN documents d ON d.project_id = c.project_id AND d.id = c.document_id
                WHERE c.project_id = %s AND c.run_id = %s ORDER BY c.id""",
            (project_id, run_id),
        ).fetchall()


def fact_embedding_subjects(run_id: int) -> list[dict]:
    """Current claims that need to share one comparable embedding space."""
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        candidates = conn.execute(
            """SELECT id, 'candidate' AS subject_type, claim
                 FROM fact_extraction_candidates
                WHERE project_id = %s AND run_id = %s ORDER BY id""",
            (project_id, run_id),
        ).fetchall()
        facts = conn.execute(
            """SELECT id, 'fact' AS subject_type, claim FROM facts
                WHERE project_id = %s AND status NOT IN ('Retired', 'Invalidated') ORDER BY id""",
            (project_id,),
        ).fetchall()
    return [*facts, *candidates]


def upsert_fact_embedding(subject_type: str, subject_id: int, *, profile: str,
                          provider: str, model: str, content_hash: str,
                          embedding: list[float]) -> None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        conn.execute(
            """INSERT INTO fact_embeddings
                 (project_id, subject_type, subject_id, embedding_profile, provider,
                  model, dimensions, content_hash, embedding)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector)
               ON CONFLICT (project_id, subject_type, subject_id, embedding_profile, purpose)
               DO UPDATE SET provider = EXCLUDED.provider, model = EXCLUDED.model,
                 dimensions = EXCLUDED.dimensions, content_hash = EXCLUDED.content_hash,
                 embedding = EXCLUDED.embedding, updated_at = now()""",
            (project_id, subject_type, subject_id, profile, provider, model,
             len(embedding), content_hash, str(embedding)),
        )


def current_fact_embedding_hashes(profile: str) -> dict[tuple[str, int], str]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT subject_type, subject_id, content_hash FROM fact_embeddings
                WHERE project_id = %s AND embedding_profile = %s AND purpose = 'fact'""",
            (project_id, profile),
        ).fetchall()
    return {(str(row["subject_type"]), int(row["subject_id"])): str(row["content_hash"])
            for row in rows}


def candidate_fact_neighbors(candidate_id: int, profile: str, *, limit: int = 8,
                             minimum_similarity: float = .72) -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute(
            """SELECT f.id, f.claim AS label, f.status, f.document_id,
                      d.updated_src AS target_updated_at, d.content_hash AS target_content_hash,
                      1 - (target.embedding <=> query.embedding) AS similarity
                 FROM fact_embeddings query
                 JOIN fact_embeddings target ON target.project_id = query.project_id
                   AND target.embedding_profile = query.embedding_profile
                   AND target.purpose = query.purpose AND target.subject_type = 'fact'
                 JOIN facts f ON f.project_id = target.project_id AND f.id = target.subject_id
                 LEFT JOIN documents d ON d.project_id = f.project_id AND d.id = f.document_id
                WHERE query.project_id = %s AND query.subject_type = 'candidate'
                  AND query.subject_id = %s AND query.embedding_profile = %s
                  AND f.status NOT IN ('Retired', 'Invalidated')
                  AND 1 - (target.embedding <=> query.embedding) >= %s
                ORDER BY target.embedding <=> query.embedding, f.id LIMIT %s""",
            (project_id, candidate_id, profile, minimum_similarity, limit),
        ).fetchall()


def candidate_document_neighbors(candidate_id: int, profile: str, *, source_document_id: int | None,
                                 limit: int = 6, minimum_similarity: float = .68) -> list[dict]:
    """MaxSim over chunk vectors: each document may contribute its best passage."""
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute(
            """SELECT d.id, d.title AS label, d.updated_src AS target_updated_at,
                      d.content_hash AS target_content_hash,
                      max(1 - (chunks.embedding <=> query.embedding)) AS similarity
                 FROM fact_embeddings query
                 JOIN chunk_embeddings chunks ON chunks.project_id = query.project_id
                   AND chunks.embedding_profile = query.embedding_profile
                   AND chunks.purpose = 'document'
                 JOIN documents d ON d.project_id = chunks.project_id AND d.id = chunks.document_id
                 JOIN chunks c ON c.project_id = chunks.project_id AND c.id = chunks.chunk_id
                    AND c.content_hash = chunks.content_hash
                WHERE query.project_id = %s AND query.subject_type = 'candidate'
                  AND query.subject_id = %s AND query.embedding_profile = %s
                  AND (%s::integer IS NULL OR d.id <> %s::integer)
                GROUP BY d.id, d.title, d.updated_src, d.content_hash, query.embedding
               HAVING max(1 - (chunks.embedding <=> query.embedding)) >= %s
                ORDER BY similarity DESC, d.id LIMIT %s""",
            (project_id, candidate_id, profile, source_document_id, source_document_id,
             minimum_similarity, limit),
        ).fetchall()


def replace_candidate_semantic_links(candidate_id: int, profile: str, links: list[dict],
                                     *, impact_score: int, high_impact: bool) -> None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        conn.execute(
            """UPDATE fact_semantic_links SET active = false
                WHERE project_id = %s AND subject_type = 'candidate' AND subject_id = %s
                  AND embedding_profile = %s""",
            (project_id, candidate_id, profile),
        )
        for link in links:
            conn.execute(
                """INSERT INTO fact_semantic_links
                     (project_id, subject_type, subject_id, target_type, target_id,
                      embedding_profile, similarity, relation, target_label,
                      target_updated_at, target_content_hash, active)
                   VALUES (%s, 'candidate', %s, %s, %s, %s, %s, %s, %s, %s, %s, true)
                   ON CONFLICT (project_id, subject_type, subject_id, target_type, target_id, embedding_profile)
                   DO UPDATE SET similarity = EXCLUDED.similarity, relation = EXCLUDED.relation,
                     target_label = EXCLUDED.target_label,
                     target_updated_at = EXCLUDED.target_updated_at,
                     target_content_hash = EXCLUDED.target_content_hash,
                     observed_at = now(), active = true""",
                (project_id, candidate_id, link["target_type"], link["target_id"], profile,
                 float(link["similarity"]), link["relation"], link["target_label"][:500],
                 link.get("target_updated_at"), link.get("target_content_hash") or ""),
            )
        conn.execute(
            """UPDATE fact_extraction_candidates SET impact_score = %s, high_impact = %s
                WHERE project_id = %s AND id = %s""",
            (impact_score, high_impact, project_id, candidate_id),
        )


def semantic_links(subject_type: str, subject_id: int) -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute(
            """SELECT target_type, target_id, relation, similarity, target_label,
                      target_updated_at, target_content_hash, observed_at
                 FROM fact_semantic_links
                WHERE project_id = %s AND subject_type = %s AND subject_id = %s AND active
                ORDER BY CASE relation WHEN 'contradicts' THEN 0 WHEN 'supports' THEN 1 ELSE 2 END,
                         similarity DESC, id""",
            (project_id, subject_type, subject_id),
        ).fetchall()


def review_fact_candidate(candidate_id: int, *, accepted: bool, reviewer: str,
                          reason: str = "", kind: str = "human") -> bool:
    project_id = access.require_current_access().project_id
    status = "accepted" if accepted else "rejected"
    with db.connect() as conn, conn.transaction():
        return bool(conn.execute(
            """UPDATE fact_extraction_candidates
                  SET review_status = %s, review_kind = %s, review_reason = %s,
                      reviewer = %s, reviewed_at = now()
                WHERE project_id = %s AND id = %s AND published_fact_id IS NULL
                RETURNING id""",
            (status, kind[:20], reason[:1000], reviewer[:120], project_id, candidate_id),
        ).fetchone())


def fact_candidate_counts(run_id: int) -> dict[str, int]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT review_status, count(*) AS n FROM fact_extraction_candidates
                WHERE project_id = %s AND run_id = %s GROUP BY review_status""",
            (project_id, run_id),
        ).fetchall()
    counts = {"pending": 0, "accepted": 0, "rejected": 0}
    counts.update({str(row["review_status"]): int(row["n"]) for row in rows})
    return counts


def publish_fact_candidates(run_id: int, owner: str, *, verified: bool = False) -> int:
    """Idempotently promote accepted candidates to the durable fact ledger."""
    project_id = access.require_current_access().project_id
    status = "Verified" if verified else "Needs review"
    published = 0
    with db.connect() as conn, conn.transaction():
        rows = conn.execute(
            """SELECT * FROM fact_extraction_candidates
                WHERE project_id = %s AND run_id = %s
                  AND review_status = 'accepted' AND published_fact_id IS NULL
                ORDER BY id FOR UPDATE""",
            (project_id, run_id),
        ).fetchall()
        for candidate in rows:
            fact = conn.execute(
                """INSERT INTO facts
                     (project_id, claim, source, owner_name, owner_tint, status,
                      verified, verified_at, document_id, valid_from)
                   VALUES (%s, %s, %s, %s, 1, %s, %s,
                           CASE WHEN %s THEN current_date ELSE NULL END, %s,
                           COALESCE((SELECT updated_src FROM documents
                                      WHERE project_id = %s AND id = %s), now()))
                   ON CONFLICT (project_id, claim) DO UPDATE SET claim = EXCLUDED.claim
                   RETURNING id""",
                (project_id, candidate["claim"], candidate["source_label"], owner,
                 status, time.strftime("%b %d, %Y") if verified else "—", verified,
                 candidate["document_id"], project_id, candidate["document_id"]),
            ).fetchone()
            if fact:
                conn.execute(
                    """INSERT INTO fact_embeddings
                         (project_id, subject_type, subject_id, embedding_profile, provider,
                          model, purpose, representation, distance_metric, normalized,
                          dimensions, content_hash, embedding)
                       SELECT project_id, 'fact', %s, embedding_profile, provider, model,
                              purpose, representation, distance_metric, normalized,
                              dimensions, content_hash, embedding
                         FROM fact_embeddings
                        WHERE project_id = %s AND subject_type = 'candidate' AND subject_id = %s
                       ON CONFLICT (project_id, subject_type, subject_id, embedding_profile, purpose)
                       DO UPDATE SET content_hash = EXCLUDED.content_hash,
                         embedding = EXCLUDED.embedding, updated_at = now()""",
                    (fact["id"], project_id, candidate["id"]),
                )
                conn.execute(
                    """INSERT INTO fact_semantic_links
                         (project_id, subject_type, subject_id, target_type, target_id,
                          embedding_profile, similarity, relation, target_label,
                          target_updated_at, target_content_hash, observed_at, active)
                       SELECT project_id, 'fact', %s, target_type, target_id,
                              embedding_profile, similarity, relation, target_label,
                              target_updated_at, target_content_hash, observed_at, active
                         FROM fact_semantic_links
                        WHERE project_id = %s AND subject_type = 'candidate' AND subject_id = %s
                       ON CONFLICT (project_id, subject_type, subject_id, target_type, target_id, embedding_profile)
                       DO UPDATE SET similarity = EXCLUDED.similarity, relation = EXCLUDED.relation,
                         target_label = EXCLUDED.target_label,
                         target_updated_at = EXCLUDED.target_updated_at,
                         target_content_hash = EXCLUDED.target_content_hash,
                         observed_at = EXCLUDED.observed_at, active = EXCLUDED.active""",
                    (fact["id"], project_id, candidate["id"]),
                )
                conn.execute(
                    """UPDATE fact_extraction_candidates SET published_fact_id = %s
                        WHERE project_id = %s AND id = %s""",
                    (fact["id"], project_id, candidate["id"]),
                )
                published += 1
    return published


def document(document_id: int) -> dict | None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute(
            "SELECT * FROM documents WHERE project_id = %s AND id = %s",
            (project_id, document_id),
        ).fetchone()


def add_finding(document_id: int, text: str, note: str) -> bool:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        row = conn.execute(
            """INSERT INTO findings
               (project_id, document_id, kind, severity, text, note)
               VALUES (%s, %s, 'fact', 'error', %s, %s)
               ON CONFLICT (document_id, text) DO NOTHING RETURNING id""",
            (project_id, document_id, text, note),
        ).fetchone()
    return bool(row)


def recent_documents(limit: int) -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute(
            """SELECT id, external_id, title, body, snippet, source, updated_src
                 FROM documents WHERE project_id = %s
                 ORDER BY updated_src DESC LIMIT %s""", (project_id, limit),
        ).fetchall()


def replace_digest(topics: list[tuple[str, str, str, str]]) -> None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        conn.execute("DELETE FROM digest_topics WHERE project_id = %s", (project_id,))
        for title, summary, wheres, impact in topics:
            conn.execute(
                """INSERT INTO digest_topics
                   (project_id, title, summary, wheres, impact)
                   VALUES (%s, %s, %s, %s, %s)""",
                (project_id, title, summary, wheres, impact),
            )


def set_task_done(task_id: int, done: bool) -> str | None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        row = conn.execute(
            "SELECT title FROM tasks WHERE project_id = %s AND id = %s",
            (project_id, task_id),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE tasks SET done = %s WHERE project_id = %s AND id = %s",
                (done, project_id, task_id),
            )
    return str(row["title"]) if row else None


def clear_done_tasks() -> int:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        rows = conn.execute(
            "DELETE FROM tasks WHERE project_id = %s AND done RETURNING id", (project_id,),
        ).fetchall()
    return len(rows)


def verify_fact(fact_id: int) -> str | None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        row = conn.execute(
            """UPDATE facts SET status = 'Verified', verified_at = current_date
                 WHERE project_id = %s AND id = %s RETURNING claim""",
            (project_id, fact_id),
        ).fetchone()
    return str(row["claim"]) if row else None


def create_task(*, title: str, assignee: str, initials: str, kind: str,
                kind_label: str, due_date: str | None, subject: tuple[str, ...]) -> None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        conn.execute(
            """INSERT INTO tasks
               (project_id, title, assignee, assignee_initials, assignee_tint, kind,
                kind_label, due_date, subject_type, subject_id, subject_title, subject_href)
               VALUES (%s, %s, %s, %s, 1, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (project_id, title) DO NOTHING""",
            (project_id, title, assignee, initials, kind, kind_label, due_date, *subject),
        )


def set_task_due(task_id: int, due_date: str | None) -> dict | None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        row = conn.execute(
            "SELECT title, due_date FROM tasks WHERE project_id = %s AND id = %s",
            (project_id, task_id),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE tasks SET due_date = %s WHERE project_id = %s AND id = %s",
                (due_date, project_id, task_id),
            )
    return row


def document_exists(document_id: int) -> bool:
    return document(document_id) is not None


def upsert_glossary(*, term_id: int | None, term: str, definition: str,
                    owner: str, evidence: str, document_id: int | None) -> None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        if term_id:
            conn.execute(
                """UPDATE glossary SET term = %s, definition = %s, updated = now(),
                       evidence = CASE WHEN %s <> '' THEN %s ELSE evidence END,
                       evidence_doc_id = coalesce(%s, evidence_doc_id)
                     WHERE project_id = %s AND id = %s""",
                (term, definition, evidence, evidence, document_id, project_id, term_id),
            )
        else:
            conn.execute(
                """INSERT INTO glossary
                   (project_id, term, definition, owner_name, updated, evidence, evidence_doc_id)
                   VALUES (%s, %s, %s, %s, now(), %s, %s)
                   ON CONFLICT (project_id, term) DO UPDATE SET
                     definition = EXCLUDED.definition, updated = now(),
                     evidence = CASE WHEN EXCLUDED.evidence <> ''
                                     THEN EXCLUDED.evidence ELSE glossary.evidence END,
                     evidence_doc_id = coalesce(EXCLUDED.evidence_doc_id, glossary.evidence_doc_id)""",
                (project_id, term, definition, owner, evidence, document_id),
            )


def delete_glossary(term_id: int) -> bool:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        row = conn.execute(
            "DELETE FROM glossary WHERE project_id = %s AND id = %s RETURNING id",
            (project_id, term_id),
        ).fetchone()
    return bool(row)


def graph_views() -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute(
            "SELECT id, name, state FROM graph_views WHERE project_id = %s ORDER BY id",
            (project_id,),
        ).fetchall()


def facts(document_id: int | None = None) -> list[dict]:
    project_id = access.require_current_access().project_id
    clause = " AND document_id = %s" if document_id is not None else ""
    args = (project_id, document_id) if document_id is not None else (project_id,)
    with db.connect() as conn:
        return conn.execute(
            f"SELECT * FROM facts WHERE project_id = %s{clause} ORDER BY id", args,
        ).fetchall()


def tasks() -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute(
            """SELECT *, (due_date IS NOT NULL AND NOT done AND due_date < current_date) AS overdue
                 FROM tasks WHERE project_id = %s ORDER BY id""", (project_id,),
        ).fetchall()


def task_summary() -> tuple[dict | None, list[str], list[str]]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        summary = conn.execute(
            """SELECT count(*) AS total, count(*) FILTER (WHERE NOT done) AS open,
                      count(*) FILTER (WHERE done) AS done,
                      count(*) FILTER (WHERE NOT done AND due_date < current_date) AS overdue,
                      count(*) FILTER (WHERE NOT done AND due_date >= current_date
                                       AND due_date <= current_date + 7) AS due_soon
                 FROM tasks WHERE project_id = %s""", (project_id,),
        ).fetchone()
        kinds = [row["kind_label"] for row in conn.execute(
            """SELECT DISTINCT kind_label FROM tasks
                 WHERE project_id = %s AND kind_label <> '' ORDER BY kind_label""",
            (project_id,),
        ).fetchall()]
        people = [row["assignee_initials"] for row in conn.execute(
            """SELECT DISTINCT assignee_initials FROM tasks
                 WHERE project_id = %s AND NOT done AND assignee_initials <> ''
                 ORDER BY assignee_initials""", (project_id,),
        ).fetchall()]
    return summary, kinds, people


def digest_topics() -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.request_connection() as conn:
        return conn.execute(
            "SELECT * FROM digest_topics WHERE project_id = %s ORDER BY id", (project_id,),
        ).fetchall()


def members() -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute(
            """SELECT u.id, u.name, u.initials, u.tint, u.email, pm.role,
                      u.provider, pm.status, u.joined
                 FROM project_members pm JOIN users u ON u.id = pm.user_id
                WHERE pm.project_id = %s ORDER BY u.id""", (project_id,),
        ).fetchall()


def glossary_terms() -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute(
            "SELECT * FROM glossary WHERE project_id = %s ORDER BY term", (project_id,),
        ).fetchall()


def tag_definitions() -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute(
            """SELECT d.*, (SELECT count(*) FROM tags t
                              WHERE t.project_id = %s AND t.tag = d.tag) AS usage
                 FROM tag_definitions d ORDER BY d.search_weight DESC""", (project_id,),
        ).fetchall()


def style_guides() -> tuple[list[dict], list[dict]]:
    with db.connect() as conn:
        guides = conn.execute("SELECT * FROM style_guides ORDER BY sort, key").fetchall()
        rules = conn.execute(
            "SELECT guide_key, description FROM style_rules ORDER BY guide_key, sort, id",
        ).fetchall()
    return guides, rules


def style_rules(guide_key: str | None = None) -> list[dict]:
    with db.connect() as conn:
        if guide_key:
            return conn.execute(
                "SELECT * FROM style_rules WHERE guide_key = %s ORDER BY guide_key, sort, id",
                (guide_key,),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM style_rules ORDER BY guide_key, sort, id",
        ).fetchall()


def setting_value(key: str):
    with db.connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = %s", (key,)).fetchone()
    return row["value"] if row else None


def document_templates() -> list[dict]:
    with db.connect() as conn:
        return conn.execute("SELECT * FROM document_templates ORDER BY sort, key").fetchall()


def upload_manifest() -> list[dict]:
    project_id = access.require_current_access().project_id
    profile = llm.embedding_profile()
    with db.connect() as conn:
        return conn.execute(
            """SELECT d.id, d.source_path, d.external_id, d.updated_src,
                      count(DISTINCT c.id) AS chunks, count(DISTINCT e.chunk_id) AS embedded
                 FROM documents d
                 JOIN sources s ON s.project_id = d.project_id AND s.id = d.source_id
                               AND s.provider = 'upload'
                 LEFT JOIN chunks c ON c.project_id = d.project_id AND c.document_id = d.id
                 LEFT JOIN chunk_embeddings e
                   ON e.project_id = c.project_id AND e.chunk_id = c.id
                  AND e.embedding_profile = %s AND e.purpose = 'document'
                  AND e.content_hash = c.content_hash
                WHERE d.project_id = %s GROUP BY d.id ORDER BY d.id""", (profile, project_id),
        ).fetchall()


def api_keys() -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute(
            "SELECT * FROM api_keys WHERE project_id = %s ORDER BY id", (project_id,),
        ).fetchall()


def approved_answers() -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute("""SELECT * FROM approved_answers WHERE project_id = %s
          ORDER BY (status = 'approved') DESC, served DESC""", (project_id,)).fetchall()


def answer_coverage_gaps(limit: int) -> list[str]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        rows = conn.execute("""WITH asked AS (
          SELECT lower(trim(detail)) AS question, max(at) AS last_at FROM usage_log
           WHERE project_id = %s AND kind = 'search' AND length(trim(detail)) >= 8 GROUP BY 1
          UNION ALL SELECT lower(trim(content)), max(created_at) FROM chat_messages
           WHERE project_id = %s AND role = 'user' AND length(trim(content)) BETWEEN 8 AND 200 GROUP BY 1)
          SELECT a.question, max(a.last_at) AS last_at FROM asked a
          WHERE NOT EXISTS (SELECT 1 FROM approved_answers ans WHERE ans.project_id = %s
            AND ans.status = 'approved' AND lower(ans.question) = a.question)
          GROUP BY a.question ORDER BY max(a.last_at) DESC LIMIT %s""",
          (project_id, project_id, project_id, limit)).fetchall()
    return [str(row["question"]) for row in rows]


def harvest_source_counts() -> tuple[list[dict], int]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        sources = conn.execute("SELECT source, count(*) AS n FROM documents WHERE project_id = %s GROUP BY source",
                               (project_id,)).fetchall()
        chat = conn.execute("SELECT count(*) AS n FROM chat_messages WHERE project_id = %s AND role = 'user'",
                            (project_id,)).fetchone()
    return sources, int(chat["n"])


def index_stats() -> dict:
    project_id = access.require_current_access().project_id
    profile = llm.embedding_profile()
    with db.connect() as conn:
        return conn.execute("""SELECT (SELECT count(*) FROM documents WHERE project_id = %s) AS docs,
          count(DISTINCT c.id) AS chunks, count(DISTINCT e.chunk_id) AS embedded
          FROM chunks c LEFT JOIN chunk_embeddings e
            ON e.project_id = c.project_id AND e.chunk_id = c.id
           AND e.embedding_profile = %s AND e.purpose = 'document'
           AND e.content_hash = c.content_hash
          WHERE c.project_id = %s""", (project_id, profile, project_id)).fetchone()


def decisions_with_supersession() -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute("""SELECT d.*, s.statement AS sup_stmt FROM decisions d
          LEFT JOIN decisions s ON s.project_id = d.project_id AND s.id = d.superseded_by
          WHERE d.project_id = %s ORDER BY d.id DESC""", (project_id,)).fetchall()


def readability() -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute("SELECT id, title, source, readability FROM documents WHERE project_id = %s ORDER BY id",
                            (project_id,)).fetchall()


def glossary_candidates() -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute("SELECT * FROM glossary WHERE project_id = %s AND candidate ORDER BY id",
                            (project_id,)).fetchall()


def save_style_guide(key: str, name: str, description: str, tone: str) -> None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        conn.execute("""INSERT INTO style_guides (project_id, key, name, description, tone, builtin, sort)
          VALUES (%s, %s, %s, %s, %s, false, 200) ON CONFLICT (project_id, key) DO UPDATE SET
          name = EXCLUDED.name, description = EXCLUDED.description, tone = EXCLUDED.tone""",
          (project_id, key, name, description, tone))


def remove_style_guide(key: str) -> tuple[str, int] | None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        guide = conn.execute("SELECT name FROM style_guides WHERE project_id = %s AND key = %s",
                             (project_id, key)).fetchone()
        if not guide:
            return None
        count = conn.execute("SELECT count(*) AS n FROM style_rules WHERE project_id = %s AND guide_key = %s",
                             (project_id, key)).fetchone()["n"]
        conn.execute("DELETE FROM style_guides WHERE project_id = %s AND key = %s", (project_id, key))
        conn.execute("""UPDATE settings SET value = value || '{"default_pack":""}'
          WHERE project_id = %s AND key = 'style_guide' AND value->>'default_pack' = %s""", (project_id, key))
    return str(guide["name"]), int(count)


def save_style_rule(rule_id: str, guide_key: str, family: str, severity: str,
                    description: str, pack: str, suggestion: str) -> bool:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        if not conn.execute("SELECT 1 FROM style_guides WHERE project_id = %s AND key = %s",
                            (project_id, guide_key)).fetchone():
            return False
        conn.execute("""INSERT INTO style_rules
          (project_id, id, guide_key, family, severity, description, pack, suggestion, sort)
          VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 200)
          ON CONFLICT (project_id, id) DO UPDATE SET guide_key = EXCLUDED.guide_key,
          family = EXCLUDED.family, severity = EXCLUDED.severity, description = EXCLUDED.description,
          pack = EXCLUDED.pack, suggestion = EXCLUDED.suggestion""",
          (project_id, rule_id, guide_key, family, severity, description, pack, suggestion))
    return True


def remove_style_rule(rule_id: str) -> bool:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        return bool(conn.execute("DELETE FROM style_rules WHERE project_id = %s AND id = %s RETURNING id",
                                 (project_id, rule_id)).fetchone())


def set_default_style_pack(pack: str) -> str:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        if pack and not conn.execute("SELECT 1 FROM style_guides WHERE project_id = %s AND key = %s",
                                     (project_id, pack)).fetchone():
            raise ValueError(f"No style guide '{pack}' to adopt")
        previous = conn.execute("""SELECT value->>'default_pack' AS pack FROM settings
          WHERE project_id = %s AND key = 'style_guide'""", (project_id,)).fetchone()
        conn.execute("""INSERT INTO settings (project_id, key, value) VALUES (%s, 'style_guide', %s)
          ON CONFLICT (project_id, key) DO UPDATE SET value = settings.value || EXCLUDED.value""",
          (project_id, json.dumps({"default_pack": pack})))
    return str((previous or {}).get("pack") or "")


def set_voice(layer: dict) -> None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        conn.execute("""INSERT INTO settings (project_id, key, value) VALUES (%s, 'voice', %s)
          ON CONFLICT (project_id, key) DO UPDATE SET value = EXCLUDED.value""",
          (project_id, json.dumps(layer)))


def save_template(key: str, name: str, category: str, description: str,
                  sections: list[str], icon: str) -> None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        conn.execute("""INSERT INTO document_templates
          (project_id, key, name, category, description, sections, icon, standard, sort)
          VALUES (%s, %s, %s, %s, %s, %s, %s, false, 200)
          ON CONFLICT (project_id, key) DO UPDATE SET name = EXCLUDED.name,
          category = EXCLUDED.category, description = EXCLUDED.description,
          sections = EXCLUDED.sections, icon = EXCLUDED.icon""",
          (project_id, key, name, category, description, json.dumps(sections), icon))


def remove_template(key: str) -> dict | None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        return conn.execute("DELETE FROM document_templates WHERE project_id = %s AND key = %s RETURNING name, standard",
                            (project_id, key)).fetchone()


def set_tag_definition(tag: str, label: str, kind: str, weight: float, behaviors: str) -> None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        conn.execute("""INSERT INTO tag_definitions
          (project_id, tag, label, kind, search_weight, is_default, behaviors)
          VALUES (%s, %s, %s, %s, %s, false, %s) ON CONFLICT (project_id, tag) DO UPDATE SET
          label = EXCLUDED.label, kind = EXCLUDED.kind, search_weight = EXCLUDED.search_weight,
          behaviors = EXCLUDED.behaviors""", (project_id, tag, label, kind, weight, behaviors))


def set_tag_weight(tag: str, weight: float) -> None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        conn.execute("UPDATE tag_definitions SET search_weight = %s WHERE project_id = %s AND tag = %s",
                     (weight, project_id, tag))


def remove_tag_definition(tag: str) -> None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        conn.execute("DELETE FROM tag_definitions WHERE project_id = %s AND tag = %s AND NOT is_default",
                     (project_id, tag))


def set_document_tag(document_id: int, tag: str, present: bool) -> tuple[str | None, list[str]]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        if present:
            conn.execute("""INSERT INTO tags (project_id, document_id, tag) VALUES (%s, %s, %s)
              ON CONFLICT DO NOTHING""", (project_id, document_id, tag))
        else:
            conn.execute("DELETE FROM tags WHERE project_id = %s AND document_id = %s AND tag = %s",
                         (project_id, document_id, tag))
        row = conn.execute("SELECT title FROM documents WHERE project_id = %s AND id = %s",
                           (project_id, document_id)).fetchone()
        tags = conn.execute("SELECT tag FROM tags WHERE project_id = %s AND document_id = %s ORDER BY tag",
                            (project_id, document_id)).fetchall()
    return (str(row["title"]) if row else None), [str(item["tag"]) for item in tags]


def set_node_position(document_id: int, position: tuple[float, float] | None) -> str | None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        row = conn.execute("SELECT title FROM documents WHERE project_id = %s AND id = %s",
                           (project_id, document_id)).fetchone()
        if not row:
            return None
        if position:
            conn.execute("UPDATE documents SET graph_x = %s, graph_y = %s WHERE project_id = %s AND id = %s",
                         (position[0], position[1], project_id, document_id))
        else:
            conn.execute("UPDATE documents SET graph_x = NULL, graph_y = NULL WHERE project_id = %s AND id = %s",
                         (project_id, document_id))
    return str(row["title"])


def save_graph_view(name: str, state: dict, creator: str) -> int:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        row = conn.execute("""INSERT INTO graph_views (project_id, name, state, created_by)
          VALUES (%s, %s, %s::jsonb, %s) ON CONFLICT (project_id, name) DO UPDATE
          SET state = EXCLUDED.state RETURNING id""", (project_id, name, json.dumps(state), creator)).fetchone()
    return int(row["id"])


def remove_graph_view(view_id: int) -> None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        conn.execute("DELETE FROM graph_views WHERE project_id = %s AND id = %s", (project_id, view_id))


def save_answer(question: str, answer: str, owner: str, answer_id: int | None = None) -> None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        if answer_id:
            conn.execute("""UPDATE approved_answers SET question = %s, answer = %s, updated = now()
              WHERE project_id = %s AND id = %s""", (question, answer, project_id, answer_id))
        else:
            conn.execute("""INSERT INTO approved_answers
              (project_id, question, answer, status, owner_name, updated)
              VALUES (%s, %s, %s, 'draft', %s, now()) ON CONFLICT (project_id, question)
              DO UPDATE SET answer = EXCLUDED.answer, updated = now()""",
              (project_id, question, answer, owner))


def answer_for_status(answer_id: int) -> dict | None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute("SELECT question, answer FROM approved_answers WHERE project_id = %s AND id = %s",
                            (project_id, answer_id)).fetchone()


def set_answer_status(answer_id: int, status: str, embedding: list[float] | None = None) -> None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        conn.execute("UPDATE approved_answers SET status = %s, updated = now() WHERE project_id = %s AND id = %s",
                     (status, project_id, answer_id))
        if embedding:
            conn.execute("UPDATE approved_answers SET embedding = %s::vector WHERE project_id = %s AND id = %s",
                         (str(embedding), project_id, answer_id))


def set_answer_channels(answer_id: int, channels: list[str]) -> str | None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        row = conn.execute("SELECT question FROM approved_answers WHERE project_id = %s AND id = %s",
                           (project_id, answer_id)).fetchone()
        if row:
            conn.execute("UPDATE approved_answers SET channels = %s WHERE project_id = %s AND id = %s",
                         (channels, project_id, answer_id))
    return str(row["question"]) if row else None


def capture_decision(statement: str, context: str, source: str, owner: str) -> None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        conn.execute("""INSERT INTO decisions (project_id, statement, context, status, source_label, owners)
          VALUES (%s, %s, %s, 'proposed', %s, %s) ON CONFLICT (project_id, statement) DO NOTHING""",
          (project_id, statement, context, source, [owner]))


def ratify_decision(decision_id: int) -> str | None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        row = conn.execute("SELECT statement FROM decisions WHERE project_id = %s AND id = %s",
                           (project_id, decision_id)).fetchone()
        if row:
            conn.execute("UPDATE decisions SET status = 'ratified', decided_on = now() WHERE project_id = %s AND id = %s",
                         (project_id, decision_id))
    return str(row["statement"]) if row else None


def supersede_decision(decision_id: int, replacement: str, owner: str) -> str | None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        old = conn.execute("SELECT statement FROM decisions WHERE project_id = %s AND id = %s",
                           (project_id, decision_id)).fetchone()
        if not old:
            return None
        conn.execute("""INSERT INTO decisions
          (project_id, statement, status, source_label, owners, decided_on)
          VALUES (%s, %s, 'ratified', 'Supersedes an earlier decision', %s, now())
          ON CONFLICT (project_id, statement) DO NOTHING""", (project_id, replacement, [owner]))
        conn.execute("""UPDATE decisions SET status = 'superseded', superseded_by =
          (SELECT id FROM decisions WHERE project_id = %s AND statement = %s)
          WHERE project_id = %s AND id = %s""", (project_id, replacement, project_id, decision_id))
    return str(old["statement"])


def get_decision(decision_id: int) -> dict | None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute("SELECT * FROM decisions WHERE project_id = %s AND id = %s",
                            (project_id, decision_id)).fetchone()


def save_decision_impact(decision_id: int, summary: str, count: int) -> None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        conn.execute("UPDATE decisions SET impact_summary = %s, impact_count = %s WHERE project_id = %s AND id = %s",
                     (summary[:300], count, project_id, decision_id))


def documents_for_analysis(limit: int | None = None, sources: list[str] | None = None) -> list[dict]:
    project_id = access.require_current_access().project_id
    sql = "SELECT id, title, body, snippet, source, updated_src FROM documents WHERE project_id = %s"
    args: list = [project_id]
    if sources:
        sql += " AND source = ANY(%s)"
        args.append(sources)
    sql += " ORDER BY updated_src DESC NULLS LAST, id"
    if limit:
        sql += " LIMIT %s"
        args.append(limit)
    with db.connect() as conn:
        return conn.execute(sql, tuple(args)).fetchall()


def save_readability(scores: list[tuple[int, str]]) -> None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        for document_id, score in scores:
            conn.execute("UPDATE documents SET readability = %s WHERE project_id = %s AND id = %s",
                         (score, project_id, document_id))


def save_glossary_candidates(candidates: list[tuple[str, str, str, str, int]]) -> int:
    project_id = access.require_current_access().project_id
    added = 0
    with db.connect() as conn, conn.transaction():
        for term, definition, variants, evidence, document_id in candidates:
            row = conn.execute("""INSERT INTO glossary
              (project_id, term, definition, owner_name, updated, candidate, variants, evidence, evidence_doc_id)
              VALUES (%s, %s, %s, 'Mari (harvest)', now(), true, %s, %s, %s)
              ON CONFLICT (project_id, term) DO NOTHING RETURNING id""",
              (project_id, term, definition, variants, evidence, document_id)).fetchone()
            added += int(bool(row))
    return added


def decide_glossary_candidate(candidate_id: int, accept: bool) -> str | None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        row = conn.execute("SELECT term FROM glossary WHERE project_id = %s AND id = %s",
                           (project_id, candidate_id)).fetchone()
        if not row:
            return None
        if accept:
            conn.execute("UPDATE glossary SET candidate = false WHERE project_id = %s AND id = %s",
                         (project_id, candidate_id))
        else:
            conn.execute("DELETE FROM glossary WHERE project_id = %s AND id = %s AND candidate",
                         (project_id, candidate_id))
    return str(row["term"])


def answer_candidate_inputs(sources: list[str]) -> tuple[set[str], list[dict], list[str]]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        existing = {str(r["question"]).lower() for r in conn.execute(
            "SELECT question FROM approved_answers WHERE project_id = %s", (project_id,)).fetchall()}
        docs = conn.execute("""SELECT id, title, snippet, body, source, updated_src FROM documents
          WHERE project_id = %s AND source = ANY(%s) ORDER BY updated_src DESC NULLS LAST LIMIT 16""",
          (project_id, sources)).fetchall() if sources else []
        chats = [str(r["content"]) for r in conn.execute("""SELECT content FROM chat_messages
          WHERE project_id = %s AND role = 'user' ORDER BY id DESC LIMIT 10""", (project_id,)).fetchall()]
    return existing, docs, chats


def toggle_watch(document_id: int, user_name: str) -> bool:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        deleted = conn.execute("""DELETE FROM watches WHERE project_id = %s
          AND user_name = %s AND document_id = %s RETURNING document_id""",
          (project_id, user_name, document_id)).fetchone()
        if deleted:
            return False
        conn.execute("""INSERT INTO watches (project_id, user_name, document_id) VALUES (%s, %s, %s)
          ON CONFLICT DO NOTHING""", (project_id, user_name, document_id))
    return True
