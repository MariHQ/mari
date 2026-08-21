"""Audit log read models."""

from mari_server.persistence.postgres import connection as db
from mari_server.identity import context as access


def _filter(query: str, date_from: str | None, date_to: str | None) -> tuple[str, tuple]:
    clauses: list[str] = []
    args: list[str] = []
    if query.strip():
        clauses.append("(actor ILIKE %s OR verb ILIKE %s OR target ILIKE %s)")
        args.extend([f"%{query.strip()}%"] * 3)
    if date_from:
        clauses.append("occurred_at >= %s")
        args.append(date_from)
    if date_to:
        clauses.append("occurred_at < (%s::date + 1)")
        args.append(date_to)
    return " AND ".join(clauses) if clauses else "true", tuple(args)


def events(query: str, date_from: str | None, date_to: str | None, limit: int) -> list[dict]:
    project_id = access.require_current_access().project_id
    where, args = _filter(query, date_from, date_to)
    with db.connect() as conn:
        return conn.execute(
            f"SELECT * FROM events WHERE project_id = %s AND {where} ORDER BY occurred_at DESC, id DESC LIMIT %s",
            (project_id,) + args + (limit,),
        ).fetchall()


def event_count(query: str, date_from: str | None, date_to: str | None) -> int:
    project_id = access.require_current_access().project_id
    where, args = _filter(query, date_from, date_to)
    with db.connect() as conn:
        row = conn.execute(f"SELECT count(*) AS n FROM events WHERE project_id = %s AND {where}",
                           (project_id,) + args).fetchone()
    return int(row["n"])


def activity(limit: int) -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute("""SELECT id, actor, verb, target, to_char(occurred_at, 'HH24:MI') AS at,
          greatest(0, extract(epoch FROM now() - occurred_at))::int AS seconds_ago,
          CASE WHEN verb LIKE '%%run%%' OR verb LIKE 'started%%' THEN 'run'
          WHEN verb LIKE 'deploy%%' OR verb LIKE 'rolled%%' THEN 'deploy'
          WHEN verb LIKE '%%fact%%' OR verb LIKE '%%verif%%' THEN 'fact'
          WHEN verb LIKE '%%task%%' OR verb IN ('completed','reopened') THEN 'task'
          WHEN verb LIKE '%%sync%%' OR verb LIKE '%%connect%%' THEN 'sync'
          WHEN verb LIKE '%%link%%' OR verb LIKE 'derived%%' THEN 'link' ELSE 'edit' END AS kind
          FROM events WHERE project_id = %s ORDER BY occurred_at DESC, id DESC LIMIT %s""",
          (project_id, limit)).fetchall()


def repository_runs(limit: int = 10) -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute(
            "SELECT * FROM audit_runs WHERE project_id = %s ORDER BY id DESC LIMIT %s",
            (project_id, limit),
        ).fetchall()


def repository_findings(run_id: int | None = None) -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        if run_id is not None:
            return conn.execute("""SELECT * FROM audit_findings
              WHERE project_id = %s AND run_id = %s ORDER BY kind, id""",
              (project_id, run_id)).fetchall()
        return conn.execute("""SELECT * FROM audit_findings WHERE project_id = %s
          AND run_id = (SELECT max(id) FROM audit_runs WHERE project_id = %s)
          ORDER BY kind, id""", (project_id, project_id)).fetchall()
