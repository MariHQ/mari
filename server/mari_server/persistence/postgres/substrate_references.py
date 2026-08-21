"""Postgres projections for external substrate identities and metadata."""

from __future__ import annotations

import json

from mari_components.substrates import SearchHit, Source
from mari_server.persistence.postgres import connection as db


def record_hits(project_id: int, substrate: str, hits: list[SearchHit]) -> list[dict]:
    rows: list[dict] = []
    with db.connect() as conn:
        for hit in hits:
            rows.append(conn.execute(
                """INSERT INTO substrate_documents
                     (project_id, substrate, external_id, title, excerpt, source, url,
                      updated_at, metadata, observed_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                   ON CONFLICT (project_id, substrate, external_id) DO UPDATE SET
                     title=EXCLUDED.title, excerpt=EXCLUDED.excerpt, source=EXCLUDED.source,
                     url=EXCLUDED.url, updated_at=EXCLUDED.updated_at,
                     metadata=EXCLUDED.metadata, observed_at=now()
                   RETURNING *""",
                (project_id, substrate, hit.document_id, hit.title, hit.content,
                 hit.source, hit.url, hit.updated_at, json.dumps(dict(hit.metadata))),
            ).fetchone())
    return rows


def get_document(project_id: int, document_id: int) -> dict | None:
    with db.connect() as conn:
        return conn.execute(
            "SELECT * FROM substrate_documents WHERE project_id=%s AND id=%s",
            (project_id, document_id),
        ).fetchone()


def tags(project_id: int, document_id: int) -> list[str]:
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT tag FROM substrate_document_tags
                 WHERE project_id=%s AND document_id=%s ORDER BY tag""",
            (project_id, document_id),
        ).fetchall()
    return [str(row["tag"]) for row in rows]


def set_tag(project_id: int, document_id: int, tag: str, present: bool) -> tuple[str | None, list[str]]:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT title FROM substrate_documents WHERE project_id=%s AND id=%s",
            (project_id, document_id),
        ).fetchone()
        if not row:
            return None, []
        if present:
            conn.execute(
                """INSERT INTO substrate_document_tags(project_id, document_id, tag)
                     VALUES (%s,%s,%s) ON CONFLICT DO NOTHING""",
                (project_id, document_id, tag),
            )
        else:
            conn.execute(
                """DELETE FROM substrate_document_tags
                     WHERE project_id=%s AND document_id=%s AND tag=%s""",
                (project_id, document_id, tag),
            )
    return str(row["title"]), tags(project_id, document_id)


def toggle_watch(project_id: int, document_id: int, user_name: str) -> bool:
    with db.connect() as conn:
        removed = conn.execute(
            """DELETE FROM substrate_document_watches
                 WHERE project_id=%s AND document_id=%s AND user_name=%s RETURNING document_id""",
            (project_id, document_id, user_name),
        ).fetchone()
        if removed:
            return False
        conn.execute(
            """INSERT INTO substrate_document_watches(project_id, document_id, user_name)
                 VALUES (%s,%s,%s) ON CONFLICT DO NOTHING""",
            (project_id, document_id, user_name),
        )
    return True


def is_watched(project_id: int, document_id: int, user_name: str) -> bool:
    with db.connect() as conn:
        return bool(conn.execute(
            """SELECT 1 FROM substrate_document_watches
                 WHERE project_id=%s AND document_id=%s AND user_name=%s""",
            (project_id, document_id, user_name),
        ).fetchone())


def add_finding(project_id: int, document_id: int, text: str, note: str) -> bool:
    with db.connect() as conn:
        return bool(conn.execute(
            """INSERT INTO substrate_findings(project_id,document_id,text,note)
                 VALUES (%s,%s,%s,%s) RETURNING id""",
            (project_id, document_id, text, note),
        ).fetchone())


def findings(project_id: int, document_id: int) -> list[dict]:
    with db.connect() as conn:
        return conn.execute(
            """SELECT id,kind,severity,text,note FROM substrate_findings
                 WHERE project_id=%s AND document_id=%s ORDER BY id""",
            (project_id, document_id),
        ).fetchall()


def set_position(project_id: int, document_id: int,
                 position: tuple[float, float] | None) -> str | None:
    with db.connect() as conn:
        row = conn.execute(
            """UPDATE substrate_documents SET graph_x=%s, graph_y=%s
                 WHERE project_id=%s AND id=%s RETURNING title""",
            (position[0] if position else None, position[1] if position else None,
             project_id, document_id),
        ).fetchone()
    return str(row["title"]) if row else None


def recent_documents(project_id: int, limit: int, offset: int) -> list[dict]:
    with db.connect() as conn:
        return conn.execute(
            """SELECT * FROM substrate_documents WHERE project_id=%s
                 ORDER BY observed_at DESC, id DESC LIMIT %s OFFSET %s""",
            (project_id, limit, offset),
        ).fetchall()


def document_count(project_id: int) -> int:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT count(*) AS n FROM substrate_documents WHERE project_id=%s",
            (project_id,),
        ).fetchone()
    return int(row["n"])


def scan_documents(project_id: int, kind: str, document_ids: list[int] | None,
                   limit: int) -> list[dict]:
    column = {"facts": "facts_scanned_at", "decisions": "decisions_scanned_at"}[kind]
    with db.connect() as conn:
        if document_ids:
            rows = conn.execute(
                f"""SELECT * FROM substrate_documents
                      WHERE project_id=%s AND id=ANY(%s)
                      ORDER BY array_position(%s::bigint[], id)""",
                (project_id, document_ids, document_ids),
            ).fetchall()
        else:
            rows = conn.execute(
                f"""SELECT * FROM substrate_documents WHERE project_id=%s
                      ORDER BY {column} ASC NULLS FIRST, observed_at DESC LIMIT %s""",
                (project_id, limit),
            ).fetchall()
    return [_analysis_row(row) for row in rows]


def mark_scanned(project_id: int, kind: str, document_ids: list[int]) -> None:
    if not document_ids:
        return
    column = {"facts": "facts_scanned_at", "decisions": "decisions_scanned_at"}[kind]
    with db.connect() as conn:
        conn.execute(
            f"UPDATE substrate_documents SET {column}=now() WHERE project_id=%s AND id=ANY(%s)",
            (project_id, document_ids),
        )


def _analysis_row(row: dict) -> dict:
    return {
        "id": row["id"], "title": row["title"], "body": row["excerpt"],
        "snippet": row["excerpt"], "source": row["source"],
        "updated_src": row["updated_at"] or row["observed_at"],
    }


def documents_for_analysis(project_id: int, limit: int | None = None,
                           sources: list[str] | None = None) -> list[dict]:
    with db.connect() as conn:
        sql = "SELECT * FROM substrate_documents WHERE project_id=%s"
        args: list = [project_id]
        if sources:
            sql += " AND source=ANY(%s)"
            args.append(sources)
        sql += " ORDER BY observed_at DESC, id DESC LIMIT %s"
        args.append(limit or 2000)
        rows = conn.execute(sql, tuple(args)).fetchall()
    return [_analysis_row(row) for row in rows]


def save_readability(project_id: int, scores: list[tuple[int, str]]) -> None:
    with db.connect() as conn:
        for document_id, score in scores:
            conn.execute(
                "UPDATE substrate_documents SET readability=%s WHERE project_id=%s AND id=%s",
                (score, project_id, document_id),
            )


def record_sources(project_id: int, substrate: str, sources: list[Source]) -> list[dict]:
    rows: list[dict] = []
    with db.connect() as conn:
        seen: list[str] = []
        for source in sources:
            seen.append(source.source_id)
            rows.append(conn.execute(
                """INSERT INTO substrate_sources
                     (project_id, substrate, external_id, name, kind, status, credential_id,
                      document_count, last_run_at, error, configuration, observed_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
                   ON CONFLICT (project_id, substrate, external_id) DO UPDATE SET
                     name=EXCLUDED.name, kind=EXCLUDED.kind, status=EXCLUDED.status,
                     credential_id=EXCLUDED.credential_id,
                     document_count=EXCLUDED.document_count, last_run_at=EXCLUDED.last_run_at,
                     error=EXCLUDED.error, configuration=EXCLUDED.configuration, observed_at=now()
                   RETURNING *""",
                (project_id, substrate, source.source_id, source.name, source.kind, source.status,
                 source.credential_id, source.document_count, source.last_run_at, source.error,
                 json.dumps(dict(source.configuration))),
            ).fetchone())
        if seen:
            conn.execute(
                "DELETE FROM substrate_sources WHERE project_id=%s AND substrate=%s "
                "AND NOT (external_id = ANY(%s))",
                (project_id, substrate, seen),
            )
        else:
            conn.execute(
                "DELETE FROM substrate_sources WHERE project_id=%s AND substrate=%s",
                (project_id, substrate),
            )
    return rows


def get_source(project_id: int, source_id: int) -> dict | None:
    with db.connect() as conn:
        return conn.execute(
            "SELECT * FROM substrate_sources WHERE project_id=%s AND id=%s",
            (project_id, source_id),
        ).fetchone()
