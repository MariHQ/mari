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
from db import audit, exec_, q, q1

# ————— the authorization rule —————
#
# Roles are admin | manager | user (init.sql). Three tiers, applied by what an
# action can cost you rather than by which file it happens to live in:
#
#   admin    — anything that changes who can get in, or what the workspace is
#              connected to: members, roles, API keys, settings, workspace
#              identity, GitHub team provisioning, and connecting /
#              disconnecting / reconfiguring a source. Source config holds
#              stored credentials and the repo slug a sync reads, so writing it
#              is a credential operation, not a maintenance one.
#   manager  — operating what is already connected, with no credential in
#              reach: running a sync or a re-sync, extracting links, running
#              the repo audit and resolving its findings. These cost CPU and
#              write documents; they cannot exfiltrate a token.
#   user     — the knowledge and editorial surface (mutations_knowledge /
#              mutations_publish), unchanged.
#
# Anything unlisted stays at "any authenticated user", which is what it was.
ROLES = ("admin", "manager", "user")

# Keys in sources.config that identify WHICH thing a source is: repointing them
# is a new connection, not an edit.
CONFIG_IDENTITY_KEYS = ("repo", "provider_key")


def _actor(info: strawberry.Info) -> dict:
    """The caller. The GraphQL context getter (app.py) already rejected
    unauthenticated requests, so context['user'] is the real caller."""
    ctx = info.context if isinstance(info.context, dict) else {}
    user = ctx.get("user")
    if not user:
        raise PermissionError("Authentication required for this operation")
    return user


def _require_admin(info: strawberry.Info) -> dict:
    user = _actor(info)
    if user.get("role") != "admin":
        raise PermissionError("Admin role required for this operation")
    return user


def _require_manager(info: strawberry.Info) -> dict:
    user = _actor(info)
    if user.get("role") not in ("admin", "manager"):
        raise PermissionError("Manager or admin role required for this operation")
    return user


@strawberry.type
class MutAdmin:
    @strawberry.mutation
    def connect_source(self, info: strawberry.Info, provider: str, display_name: str, config: JSON) -> bool:
        """Connector setup wizard completion: create the source and log the
        connection — no fabricated backfill progress, sparkline, or checkpoint
        theater. Real connectors plug in behind this seam (DESIGN.md §7)."""
        actor = _require_admin(info)
        exec_("""INSERT INTO sources (provider, display_name, status, stat_num, stat_unit, bars, config, docs_count, health)
                 VALUES (%s, %s, 'active', '0', 'items', '{}', %s, 0, 'Never synced')
                 ON CONFLICT (provider) DO UPDATE SET config = sources.config || EXCLUDED.config, status = 'active'""",
              (provider, display_name, json.dumps(config)))
        exec_("""INSERT INTO sync_events (provider, event, detail, at_label)
                 VALUES (%s, %s, '', to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'))""",
              (provider, f"connected: {display_name}"))
        audit("connected source", display_name, actor["name"], detail=[("Provider", provider),
              ("Settings", ", ".join(sorted(config)) if isinstance(config, dict) else "")])
        return True

    @strawberry.mutation
    def disconnect_source(self, info: strawberry.Info, provider: str) -> bool:
        actor = _require_admin(info)
        exec_("UPDATE sources SET status = 'paused', health = 'Paused' WHERE provider = %s", (provider,))
        exec_("UPDATE ingest_checkpoints SET status = 'paused' WHERE provider = %s AND status = 'running'", (provider,))
        exec_("""INSERT INTO sync_events (provider, event, detail, at_label)
                 VALUES (%s, 'disconnected', 'Paused by admin', to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'))""",
              (provider,))
        audit("disconnected source", provider, actor["name"])
        return True

    # ——— members (admin-only; audit the real caller) ———
    @strawberry.mutation
    def invite_member(self, info: strawberry.Info, name: str, email: str, role: str = "user") -> bool:
        actor = _require_admin(info)
        if role not in ROLES:
            raise ValueError(f"role must be one of {', '.join(ROLES)}")
        initials = "".join(w[0].upper() for w in name.split()[:2]) or "??"
        # `invited_by` is what makes this row claimable at POST /auth/register.
        # Nothing else in the tree sets it, so a credential-less row that some
        # other path created (repoaudit's member mapping, say) is not an
        # invitation and cannot be signed into.
        exec_("""INSERT INTO users (name, initials, tint, email, role, provider, joined, invited_by)
                 VALUES (%s, %s, %s, %s, %s, 'manual', now(), %s) ON CONFLICT (name) DO NOTHING""",
              (name, initials, (hash(name) % 4) + 1, email, role, actor["name"]))
        audit("invited member", f"{name} ({role})", actor["name"],
              [("Email", email), ("Role", role), ("Provider", "manual invite")])
        return True

    @strawberry.mutation
    def set_member_role(self, info: strawberry.Info, id: int, role: str) -> bool:
        actor = _require_admin(info)
        if role not in ROLES:
            raise ValueError(f"role must be one of {', '.join(ROLES)}")
        before = q1("SELECT name, role, email FROM users WHERE id = %s", (id,))
        if not before:
            return False
        # Do not let the last admin demote themselves into a workspace nobody
        # can administer — the only way back would be the setup token, and
        # setup_complete blocks re-minting it.
        if before["role"] == "admin" and role != "admin":
            others = q1("SELECT count(*) AS n FROM users WHERE role = 'admin' AND id <> %s", (id,))["n"]
            if not others:
                raise ValueError("This is the only admin — promote someone else first.")
        exec_("UPDATE users SET role = %s WHERE id = %s", (role, id))
        audit(f"changed role to {role}", before["name"], actor["name"],
              [("Email", before["email"]), ("Previous role", before["role"]), ("New role", role)])
        return True

    @strawberry.mutation
    def remove_member(self, info: strawberry.Info, id: int) -> bool:
        actor = _require_admin(info)
        before = q1("SELECT name, role, email, provider FROM users WHERE id = %s", (id,))
        if not before:
            return False
        if before["role"] == "admin":
            others = q1("SELECT count(*) AS n FROM users WHERE role = 'admin' AND id <> %s", (id,))["n"]
            if not others:
                raise ValueError("This is the only admin — promote someone else first.")
        exec_("DELETE FROM users WHERE id = %s", (id,))
        audit("removed member", before["name"], actor["name"],
              [("Email", before["email"]), ("Role", before["role"]), ("Account source", before["provider"])])
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
        audit("created API key", name, actor["name"],
              [("Scopes", scopes), ("Prefix", token[:12] + "…")])
        return token

    @strawberry.mutation
    def revoke_api_key(self, info: strawberry.Info, id: int) -> bool:
        actor = _require_admin(info)
        key = q1("SELECT name, prefix, scopes FROM api_keys WHERE id = %s", (id,))
        if not key:
            return False
        exec_("UPDATE api_keys SET revoked = true WHERE id = %s", (id,))
        audit("revoked API key", key["name"], actor["name"],
              [("Prefix", key["prefix"]), ("Scopes", key["scopes"])])
        return True

    # ——— settings ———
    @strawberry.mutation
    def update_setting(self, info: strawberry.Info, key: str, value: JSON) -> bool:
        actor = _require_admin(info)
        exec_("""INSERT INTO settings (key, value) VALUES (%s, %s)
                 ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""", (key, json.dumps(value)))
        audit("updated setting", key, actor["name"],
              [("Setting", key), ("Fields", ", ".join(sorted(value)) if isinstance(value, dict) else "value")])
        return True

    # ——— workspace identity & member provisioning ———
    @strawberry.mutation
    def set_workspace_name(self, info: strawberry.Info, name: str) -> bool:
        """Rename the workspace. Merges into the `workspace` settings row so a
        rename never drops the timezone/language beside it."""
        actor = _require_admin(info)
        new = name.strip()
        if not new:
            raise ValueError("Workspace name cannot be empty")
        before = q1("SELECT value->>'name' AS name FROM settings WHERE key = 'workspace'")
        exec_("""INSERT INTO settings (key, value) VALUES ('workspace', %s)
                 ON CONFLICT (key) DO UPDATE SET value = settings.value || EXCLUDED.value""",
              (json.dumps({"name": new}),))
        audit("renamed workspace", new, actor["name"],
              [("Previous name", (before or {}).get("name") or "(unnamed)"), ("New name", new)])
        return True

    @strawberry.mutation
    def set_github_team(self, info: strawberry.Info, team: str) -> bool:
        """Configure (or, with an empty string, clear) the GitHub team whose
        members are provisioned into this workspace. When the server holds a
        token, the team is verified before it is saved — a slug that GitHub
        says does not exist is a typo, not a configuration."""
        actor = _require_admin(info)
        slug = team.strip().strip("/")
        if slug:
            if slug.count("/") != 1 or not all(p.strip() for p in slug.split("/")):
                raise ValueError("Team must be in org/team form, e.g. acme/docs")
            if github.token():
                org, name = slug.split("/")
                try:
                    github.team(org, name)
                except github.GithubError as e:
                    # 404 is a verdict; anything else (no org scope, rate limit,
                    # network) means we could not check, and the admin's word stands.
                    if e.status == 404:
                        raise ValueError(f"GitHub team {slug} not found, or this token cannot see it") from None
        exec_("""INSERT INTO settings (key, value) VALUES ('provisioning', %s)
                 ON CONFLICT (key) DO UPDATE SET value = settings.value || EXCLUDED.value""",
              (json.dumps({"github_team": slug}),))
        audit("configured GitHub team sync" if slug else "disabled GitHub team sync",
              slug or "GitHub team", actor["name"],
              [("Team", slug or "(none)"), ("Verified against GitHub", "yes" if slug and github.token() else "no")])
        return True

    # ——— GitHub ingestion (real — GITHUB-SYNC-CONTRACT.md) ———
    @strawberry.mutation
    def connect_github_repo(self, info: strawberry.Info, repo: str, paths: str | None = None) -> int:
        """Create a real GitHub source and start the initial sync in the background."""
        actor = _require_admin(info)
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
        audit("connected GitHub repo", repo, actor["name"],
              detail=[("Branch", branch), ("Paths", paths or "(whole repository)")])
        # every github source gets a scheduled sync flow (Flows UI owns cadence)
        flowengine.ensure_sync_flow(source_id, repo)
        ingest.start_sync(source_id)
        return source_id

    @strawberry.mutation
    def sync_source(self, info: strawberry.Info, source_id: int) -> bool:
        """Diff-based incremental sync; returns immediately, progress via syncStatus."""
        _require_manager(info)
        return ingest.start_sync(source_id)

    @strawberry.mutation
    def resync_source(self, info: strawberry.Info, source_id: int) -> bool:
        """Full rebuild escape hatch: drops this source's chunks/hashes, then syncs."""
        _require_manager(info)
        return ingest.start_sync(source_id, full=True)

    @strawberry.mutation
    def extract_links(self, info: strawberry.Info, source_id: int | None = None) -> int:
        """Backfill link extraction (LINEAGE-ROLLUP-CONTRACT.md §2) for one
        source, or every source when sourceId is null. Returns edges created."""
        _require_manager(info)
        if source_id is None:
            created = links.extract_all()
        else:
            created = sum(links.extract(source_id).values())
        audit("extracted links", f"source {source_id if source_id is not None else 'all'} · {created} edges")
        return created

    # ——— sources / ingestion ———
    @strawberry.mutation
    def sync_now(self, info: strawberry.Info, provider: str) -> bool:
        # Real github/connector sources delegate to the real sync engines
        # (ingest.start_sync dispatches by kind); anything else has no sync
        # implementation, so we say so instead of faking progress.
        _require_manager(info)
        src = q1("SELECT id, kind FROM sources WHERE provider = %s", (provider,))
        if src and src.get("kind") in ("github", "connector"):
            ingest.start_sync(src["id"])
            audit("triggered sync", provider)
            return True
        return False

    # A source's config jsonb holds its stored credentials and the identity a
    # sync reads. Merging into it is therefore a credential write: admin-only,
    # and the identity keys are not writable at all.
    @strawberry.mutation
    def update_source_config(self, info: strawberry.Info, provider: str, config: JSON) -> bool:
        actor = _require_admin(info)
        if not isinstance(config, dict):
            raise ValueError("config must be an object")
        before = q1("SELECT config FROM sources WHERE provider = %s", (provider,))
        if not before:
            raise ValueError(f"No source '{provider}' to configure")
        current = before["config"] if isinstance(before["config"], dict) else json.loads(before["config"] or "{}")
        for key in CONFIG_IDENTITY_KEYS:
            if key in config and str(config[key]) != str(current.get(key, "")):
                raise ValueError(
                    f"'{key}' identifies this source and cannot be changed here — "
                    "connect the new target as its own source instead.")
        exec_("UPDATE sources SET config = config || %s::jsonb WHERE provider = %s", (json.dumps(config), provider))
        audit("updated source config", provider, actor["name"],
              detail=[("Fields", ", ".join(sorted(config)) or "(none)")])
        return True

    # ——— repository audit (onboarding + re-audit) ———
    @strawberry.mutation
    def run_repo_audit(self, info: strawberry.Info, provider: str = "github") -> int:
        _require_manager(info)
        return repoaudit.run_audit(provider)

    @strawberry.mutation
    def fix_audit_finding(self, info: strawberry.Info, id: int, member_name: str = "") -> str:
        actor = _require_manager(info)
        return repoaudit.fix_finding(id, actor["name"], member_name)

    @strawberry.mutation
    def fix_all_audit_findings(self, info: strawberry.Info, run_id: int, kind: str) -> int:
        actor = _require_manager(info)
        rows = q("SELECT id FROM audit_findings WHERE run_id = %s AND kind = %s AND status = 'open'", (run_id, kind))
        for r in rows:
            repoaudit.fix_finding(r["id"], actor["name"])
        return len(rows)

    @strawberry.mutation
    def dismiss_audit_finding(self, info: strawberry.Info, id: int) -> bool:
        actor = _require_manager(info)
        exec_("UPDATE audit_findings SET status = 'dismissed' WHERE id = %s", (id,))
        audit("dismissed audit finding", f"#{id}", actor["name"])
        return True
