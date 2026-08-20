"""Onboarding upload and glossary source persistence."""

from __future__ import annotations

from mari_server import db
from mari_server.domain import access
from mari_server.integrations.document_index import content_hash, title_of
from mari_server.services.excerpt import excerpt


def connection():
    return db.connect()


def upload_source(conn) -> int:
    project_id = access.require_current_access().project_id
    row = conn.execute("""INSERT INTO sources
      (project_id, provider, display_name, kind, stat_unit, status, health)
      VALUES (%s, 'upload', 'Uploads', 'upload', 'docs', 'active', 'Healthy')
      ON CONFLICT (project_id, provider) DO UPDATE SET kind = 'upload', display_name = 'Uploads'
      RETURNING id""", (project_id,)).fetchone()
    conn.commit()
    return int(row["id"])


def upsert_upload_document(conn, source_id: int, filename: str, text: str) -> int:
    project_id = access.require_current_access().project_id
    title = title_of(text, filename)
    row = conn.execute("""INSERT INTO documents
      (project_id, source, external_id, title, snippet, body, author, author_initials,
       kind, updated_src, created_src, content_hash, source_path, source_id)
      VALUES (%s, 'upload', %s, %s, %s, %s, 'Upload', 'UP', 'page', CURRENT_DATE,
       CURRENT_DATE, %s, %s, %s) ON CONFLICT (project_id, source, external_id) DO UPDATE SET
       title = EXCLUDED.title, snippet = EXCLUDED.snippet, body = EXCLUDED.body,
       updated_src = CURRENT_DATE, content_hash = EXCLUDED.content_hash,
       source_path = EXCLUDED.source_path, source_id = EXCLUDED.source_id RETURNING id""",
      (project_id, f"upload:{filename}", title, excerpt(text, title), text,
       content_hash(text), f"upload/{filename}", source_id)).fetchone()
    conn.commit()
    return int(row["id"])


def finish_upload(conn, source_id: int, requested: int, results: list[dict]) -> None:
    project_id = access.require_current_access().project_id
    ok = [row for row in results if row.get("docId")]
    count = conn.execute("SELECT count(*) AS n FROM documents WHERE project_id = %s AND source_id = %s",
                         (project_id, source_id)).fetchone()["n"]
    conn.execute("""UPDATE sources SET last_sync_at = now(), docs_count = %s,
      stat_num = %s, stat_unit = 'docs' WHERE project_id = %s AND id = %s""",
      (count, str(count), project_id, source_id))
    detail = (f"{len(ok)} file(s) ingested · {sum(r['chunks'] for r in ok)} chunks · "
              f"{sum(r['embedded'] for r in ok)} embedded · "
              f"{sum(1 for r in ok if r['embedded'] == 0)} unchanged (hash skip)")
    conn.execute("""INSERT INTO sync_events (project_id, provider, event, detail, at_label)
      VALUES (%s, 'upload', %s, %s,
      to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'))""",
      (project_id, f"upload: {requested} file(s)", detail))
    conn.execute("INSERT INTO events (project_id, actor, verb, target) VALUES (%s, 'Upload', %s, 'Uploads')",
                 (project_id, f"uploaded {len(ok)} file(s)"))
    conn.commit()


def harvest_documents(source_id: int | None, limit: int) -> list[dict]:
    project_id = access.require_current_access().project_id
    where = "d.project_id = %s AND d.kind = 'page' AND d.body <> ''"
    args: list = [project_id]
    if source_id is not None:
        where += " AND d.source_id = %s"
        args.append(source_id)
    args.append(limit)
    with db.connect() as conn:
        return conn.execute(f"""WITH degree AS (
          SELECT doc, count(*) AS n FROM (SELECT from_doc AS doc FROM edges WHERE project_id = %s
          UNION ALL SELECT to_doc AS doc FROM edges WHERE project_id = %s) x GROUP BY doc)
          SELECT d.id, d.title, d.body, coalesce(g.n, 0) AS degree FROM documents d
          LEFT JOIN degree g ON g.doc = d.id WHERE {where}
          ORDER BY degree DESC, d.updated_src DESC NULLS LAST, d.id DESC LIMIT %s""",
          (project_id, project_id, *args)).fetchall()


def existing_terms() -> set[str]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        rows = conn.execute("SELECT term, variants FROM glossary WHERE project_id = %s", (project_id,)).fetchall()
    terms: set[str] = set()
    for row in rows:
        terms.add(str(row["term"]).strip().lower())
        terms.update(value.strip().lower() for value in str(row["variants"] or "").split(",") if value.strip())
    return terms
