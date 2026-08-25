"""Workspace analytics read models."""

from mari_server.persistence.postgres import connection as db
from mari_server.identity import context as access


def overview(since: str | None) -> dict:
    project_id = access.require_current_access().project_id
    with db.request_connection() as conn:
        changes = conn.execute(
            "SELECT count(*) AS n FROM changes WHERE project_id = %s AND created_at >= %s" if since else
            "SELECT count(*) AS n FROM changes WHERE project_id = %s AND created_at >= now() - interval '7 days'",
            (project_id, since) if since else (project_id,),
        ).fetchone()["n"]
        facts = conn.execute("SELECT count(*) AS n FROM facts WHERE project_id = %s AND status <> 'Verified'",
                             (project_id,)).fetchone()["n"]
        running = conn.execute("""SELECT count(*) AS n FROM workflow_runs
          WHERE project_id = %s AND status IN ('running','waiting')""", (project_id,)).fetchone()["n"]
        flows = conn.execute("SELECT count(*) AS n FROM workflows WHERE project_id = %s AND status = 'active'",
                             (project_id,)).fetchone()["n"]
        workflows_active = conn.execute(
            "SELECT count(*) AS n FROM assistant_workflows WHERE project_id = %s AND status = 'active'",
            (project_id,)).fetchone()["n"]
    # flowsRunning/flowsActive are unused now that the Flows tab is gone
    # (the product concept is assistant workflows); kept in the dict for one
    # release so a console still on the old build does not lose a tile.
    return {"changes": int(changes), "factsReview": int(facts),
            "flowsRunning": int(running), "flowsActive": int(flows),
            "workflowsActive": int(workflows_active)}


def insight_stats(since: str | None, until: str | None) -> tuple[dict, int, int]:
    project_id = access.require_current_access().project_id
    clauses: list[str] = ["project_id = %s"]
    args: list = [project_id]
    if since:
        clauses.append("at >= %s")
        args.append(since)
    if until:
        clauses.append("at < (%s::date + 1)")
        args.append(until)
    usage_where = " AND ".join(clauses)
    temporal = ""
    temporal_args: list = [project_id]
    if since:
        temporal += " AND created_at >= %s"
        temporal_args.append(since)
    if until:
        temporal += " AND created_at < (%s::date + 1)"
        temporal_args.append(until)
    with db.connect() as conn:
        counts = conn.execute(f"""SELECT count(*) FILTER (WHERE kind = 'search') AS searches,
          count(*) FILTER (WHERE kind = 'chat_answer') AS served, min(at) AS since
          FROM usage_log WHERE {usage_where}""", tuple(args)).fetchone()
        drift = conn.execute(f"""SELECT count(*) AS n FROM findings
          WHERE project_id = %s AND kind IN ('fact','freshness'){temporal}""",
          tuple(temporal_args)).fetchone()["n"]
        fixed = conn.execute(f"""SELECT count(*) AS n FROM changes
          WHERE project_id = %s AND status = 'accepted'{temporal}""",
          tuple(temporal_args)).fetchone()["n"]
    return counts, int(drift), int(fixed)
