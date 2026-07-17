"""Mari Cloud — admin mutations: members, API keys, settings, sources, sync, repo audit."""

from __future__ import annotations

import json

import strawberry
from strawberry.scalars import JSON

import flowengine
import github
import ingest
import links
import repoaudit
from db import ME, audit, exec_, q, q1


def _require_admin(info: strawberry.Info) -> dict:
    """Admin-only guard: the GraphQL context getter (app.py) already rejected
    unauthenticated requests, so context['user'] is the real caller."""
    ctx = info.context if isinstance(info.context, dict) else {}
    user = ctx.get("user")
    if not user or user.get("role") != "admin":
        raise PermissionError("Admin role required for this operation")
    return user


@strawberry.type
class MutAdmin:
    @strawberry.mutation
    def connect_source(self, provider: str, display_name: str, config: JSON) -> bool:
        """Connector setup wizard completion: create the source and log the
        connection — no fabricated backfill progress, sparkline, or checkpoint
        theater. Real connectors plug in behind this seam (DESIGN.md §7)."""
        exec_("""INSERT INTO sources (provider, display_name, status, stat_num, stat_unit, bars, config, docs_count, health)
                 VALUES (%s, %s, 'active', '0', 'items', '{}', %s, 0, 'Never synced')
                 ON CONFLICT (provider) DO UPDATE SET config = sources.config || EXCLUDED.config, status = 'active'""",
              (provider, display_name, json.dumps(config)))
        exec_("""INSERT INTO sync_events (provider, event, detail, at_label)
                 VALUES (%s, %s, '', to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'))""",
              (provider, f"connected: {display_name}"))
        audit("connected source", display_name)
        return True

    @strawberry.mutation
    def disconnect_source(self, provider: str) -> bool:
        exec_("UPDATE sources SET status = 'paused', health = 'Paused' WHERE provider = %s", (provider,))
        exec_("UPDATE ingest_checkpoints SET status = 'paused' WHERE provider = %s AND status = 'running'", (provider,))
        exec_("""INSERT INTO sync_events (provider, event, detail, at_label)
                 VALUES (%s, 'disconnected', 'Paused by admin', to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'))""",
              (provider,))
        audit("disconnected source", provider)
        return True

    # ——— members (admin-only; audit the real caller, not ME) ———
    @strawberry.mutation
    def invite_member(self, info: strawberry.Info, name: str, email: str, role: str = "user") -> bool:
        actor = _require_admin(info)
        initials = "".join(w[0].upper() for w in name.split()[:2]) or "??"
        exec_("""INSERT INTO users (name, initials, tint, email, role, provider, joined)
                 VALUES (%s, %s, %s, %s, %s, 'manual', now()) ON CONFLICT (name) DO NOTHING""",
              (name, initials, (hash(name) % 4) + 1, email, role))
        audit("invited member", f"{name} ({role})", actor["name"])
        return True

    @strawberry.mutation
    def set_member_role(self, info: strawberry.Info, id: int, role: str) -> bool:
        actor = _require_admin(info)
        exec_("UPDATE users SET role = %s WHERE id = %s", (role, id))
        exec_("INSERT INTO events (actor, verb, target) SELECT %s, 'changed role to ' || %s, name FROM users WHERE id = %s",
              (actor["name"], role, id))
        return True

    @strawberry.mutation
    def remove_member(self, info: strawberry.Info, id: int) -> bool:
        actor = _require_admin(info)
        exec_("INSERT INTO events (actor, verb, target) SELECT %s, 'removed member', name FROM users WHERE id = %s",
              (actor["name"], id))
        exec_("DELETE FROM users WHERE id = %s", (id,))
        return True

    # ——— api keys ———
    @strawberry.mutation
    def create_api_key(self, info: strawberry.Info, name: str, scopes: str = "read") -> str:
        import secrets
        actor = _require_admin(info)
        token = "mari_sk_" + secrets.token_hex(16)
        exec_("""INSERT INTO api_keys (name, prefix, scopes, created_at, last_used)
                 VALUES (%s, %s, %s, now(), 'never') ON CONFLICT (name) DO NOTHING""",
              (name, token[:12] + "…", scopes))
        audit("created API key", name, actor["name"])
        return token

    @strawberry.mutation
    def revoke_api_key(self, id: int) -> bool:
        exec_("UPDATE api_keys SET revoked = true WHERE id = %s", (id,))
        exec_("INSERT INTO events (actor, verb, target) SELECT %s, 'revoked API key', name FROM api_keys WHERE id = %s", (ME, id))
        return True

    # ——— settings ———
    @strawberry.mutation
    def update_setting(self, info: strawberry.Info, key: str, value: JSON) -> bool:
        actor = _require_admin(info)
        exec_("""INSERT INTO settings (key, value) VALUES (%s, %s)
                 ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""", (key, json.dumps(value)))
        audit("updated setting", key, actor["name"])
        return True

    # ——— GitHub ingestion (real — GITHUB-SYNC-CONTRACT.md) ———
    @strawberry.mutation
    def connect_github_repo(self, repo: str, paths: str | None = None) -> int:
        """Create a real GitHub source and start the initial sync in the background."""
        if not github.token():
            raise ValueError("No GitHub token configured (github.token / MARI_GITHUB_TOKEN)")
        if q1("SELECT id FROM sources WHERE kind = 'github' AND config->>'repo' = %s", (repo,)):
            raise ValueError(f"Repository {repo} is already connected")
        branch = github.default_branch(repo)  # also validates the repo is reachable
        cfg = {"repo": repo, "branch": branch, "paths": paths or "",
               "cursor": "", "last_sync_at": "", "last_error": "", "shas": {}}
        exec_("""INSERT INTO sources (provider, display_name, kind, status, stat_num, stat_unit, bars,
                                      config, docs_count, health)
                 VALUES (%s, %s, 'github', 'active', '0', 'docs', '{}', %s, 0, 'Syncing')""",
              (f"github:{repo}", repo, json.dumps(cfg)))
        source_id = q1("SELECT id FROM sources WHERE provider = %s", (f"github:{repo}",))["id"]
        audit("connected GitHub repo", repo)
        # every github source gets a scheduled sync flow (Flows UI owns cadence)
        flowengine.ensure_sync_flow(source_id, repo)
        ingest.start_sync(source_id)
        return source_id

    @strawberry.mutation
    def sync_source(self, source_id: int) -> bool:
        """Diff-based incremental sync; returns immediately, progress via syncStatus."""
        return ingest.start_sync(source_id)

    @strawberry.mutation
    def resync_source(self, source_id: int) -> bool:
        """Full rebuild escape hatch: drops this source's chunks/hashes, then syncs."""
        return ingest.start_sync(source_id, full=True)

    @strawberry.mutation
    def extract_links(self, source_id: int | None = None) -> int:
        """Backfill link extraction (LINEAGE-ROLLUP-CONTRACT.md §2) for one
        source, or every source when sourceId is null. Returns edges created."""
        if source_id is None:
            created = links.extract_all()
        else:
            created = sum(links.extract(source_id).values())
        audit("extracted links", f"source {source_id if source_id is not None else 'all'} · {created} edges")
        return created

    # ——— sources / ingestion ———
    @strawberry.mutation
    def sync_now(self, provider: str) -> bool:
        # Real github/connector sources delegate to the real sync engines
        # (ingest.start_sync dispatches by kind); anything else has no sync
        # implementation, so we say so instead of faking progress.
        src = q1("SELECT id, kind FROM sources WHERE provider = %s", (provider,))
        if src and src.get("kind") in ("github", "connector"):
            ingest.start_sync(src["id"])
            audit("triggered sync", provider)
            return True
        return False

    @strawberry.mutation
    def update_source_config(self, provider: str, config: JSON) -> bool:
        exec_("UPDATE sources SET config = config || %s::jsonb WHERE provider = %s", (json.dumps(config), provider))
        audit("updated source config", provider)
        return True

    # ——— repository audit (onboarding + re-audit) ———
    @strawberry.mutation
    def run_repo_audit(self, provider: str = "github") -> int:
        return repoaudit.run_audit(provider)

    @strawberry.mutation
    def fix_audit_finding(self, id: int, member_name: str = "") -> str:
        return repoaudit.fix_finding(id, ME, member_name)

    @strawberry.mutation
    def fix_all_audit_findings(self, run_id: int, kind: str) -> int:
        rows = q("SELECT id FROM audit_findings WHERE run_id = %s AND kind = %s AND status = 'open'", (run_id, kind))
        for r in rows:
            repoaudit.fix_finding(r["id"], ME)
        return len(rows)

    @strawberry.mutation
    def dismiss_audit_finding(self, id: int) -> bool:
        exec_("UPDATE audit_findings SET status = 'dismissed' WHERE id = %s", (id,))
        return True
