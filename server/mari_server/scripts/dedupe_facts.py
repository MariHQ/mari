"""Preview or merge deterministic fact duplicates without deleting history.

Usage:
    python -m mari_server.scripts.dedupe_facts --project 1
    python -m mari_server.scripts.dedupe_facts --project 1 --apply

Only normalized case/punctuation/spacing equivalents are automatic. Semantic
paraphrases remain in the review workflow because merging them requires human
judgment about scope and effective time.
"""

from __future__ import annotations

import argparse
import json

from mari_server.persistence.postgres import connection as db


def duplicate_groups(project_id: int) -> list[dict]:
    with db.connect() as conn:
        return conn.execute(
            """SELECT normalized_key, array_agg(id ORDER BY
                         CASE status WHEN 'Verified' THEN 0 ELSE 1 END, id) AS fact_ids,
                      array_agg(claim ORDER BY
                         CASE status WHEN 'Verified' THEN 0 ELSE 1 END, id) AS claims,
                      count(DISTINCT document_id) AS document_count
                 FROM facts
                WHERE project_id = %s AND merged_into_fact_id IS NULL
                  AND normalized_key <> ''
                GROUP BY normalized_key HAVING count(*) > 1
                ORDER BY count(*) DESC, min(id)""",
            (project_id,),
        ).fetchall()


def merge_group(project_id: int, fact_ids: list[int]) -> int:
    keeper, duplicates = int(fact_ids[0]), [int(value) for value in fact_ids[1:]]
    if not duplicates:
        return 0
    with db.connect() as conn, conn.transaction():
        locked = conn.execute(
            """SELECT id FROM facts WHERE project_id = %s AND id = ANY(%s)
                 AND merged_into_fact_id IS NULL ORDER BY id FOR UPDATE""",
            (project_id, [keeper, *duplicates]),
        ).fetchall()
        live = {int(row["id"]) for row in locked}
        if keeper not in live:
            return 0
        duplicates = [value for value in duplicates if value in live]
        if not duplicates:
            return 0
        conn.execute(
            """UPDATE facts SET merged_into_fact_id = %s, status = 'Retired',
                      invalidated_at = COALESCE(invalidated_at, now()),
                      invalidation_reason = %s
                WHERE project_id = %s AND id = ANY(%s)""",
            (keeper, f"Merged into fact #{keeper}: normalized duplicate", project_id, duplicates),
        )
        conn.execute(
            """UPDATE fact_assertions SET status = 'superseded',
                      recorded_to = COALESCE(recorded_to, now())
                WHERE project_id = %s AND fact_id = ANY(%s) AND status = 'active'""",
            (project_id, duplicates),
        )
        conn.execute(
            """UPDATE fact_extraction_candidates SET published_fact_id = %s
                WHERE project_id = %s AND published_fact_id = ANY(%s)""",
            (keeper, project_id, duplicates),
        )
    return len(duplicates)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preview deterministic fact deduplication")
    parser.add_argument("--project", required=True, type=int)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    groups = duplicate_groups(args.project)
    merged = 0
    if args.apply:
        for group in groups:
            merged += merge_group(args.project, list(group["fact_ids"]))
    print(json.dumps({
        "project_id": args.project,
        "mode": "apply" if args.apply else "preview",
        "groups": len(groups),
        "duplicate_rows": sum(len(row["fact_ids"]) - 1 for row in groups),
        "merged_rows": merged,
        "items": [{
            "keeper": int(row["fact_ids"][0]),
            "duplicates": [int(value) for value in row["fact_ids"][1:]],
            "claim": str(row["claims"][0]),
            "document_count": int(row["document_count"]),
        } for row in groups],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
