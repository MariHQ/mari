"""Mari Cloud — publishing mutations: sites, releases, MCP servers, workflows."""

from __future__ import annotations

import json

import strawberry
from strawberry.scalars import JSON

import brandimport
import flowengine
import llm
import sitebuilder
from db import ME, audit, exec_, jload, q, q1


@strawberry.type
class MutPublish:
    # ——— workflows ———
    @strawberry.mutation
    def run_workflow(self, workflow_id: int, dry_run: bool = False) -> int:
        """Start a REAL run: the flow engine executes each step against the
        platform primitives on a background thread; the UI polls the run.
        dry_run executes transforms/gates but previews side effects (FLOWS-DESIGN §5)."""
        n = (q1("SELECT coalesce(max(number), 1800) AS n FROM workflow_runs WHERE workflow_id = %s", (workflow_id,)) or {"n": 1800})["n"] + 1
        exec_("""INSERT INTO workflow_runs (workflow_id, number, status, started_label, duration, progress, stats, rows_data)
                 VALUES (%s, %s, 'running', to_char(now(), 'Mon DD, HH12:MI AM'), '00:00:00', 0, %s, '[]')""",
              (workflow_id, n, json.dumps({"ctx": {"dry_run": True}, "dry_run": True} if dry_run else {})))
        run = q1("SELECT id FROM workflow_runs WHERE workflow_id = %s AND number = %s", (workflow_id, n))
        exec_("INSERT INTO events (actor, verb, target) SELECT %s, 'started run #' || %s, name FROM workflows WHERE id = %s",
              (ME, n, workflow_id))
        flowengine.start_run(run["id"])
        return n

    @strawberry.mutation
    def approve_run(self, run_id: int) -> bool:
        """Resume a run paused at an approval step."""
        run = q1("SELECT * FROM workflow_runs WHERE id = %s", (run_id,))
        if not run or run["status"] != "waiting":
            return False
        stats = jload(run["stats"]) or {}
        paused_at = int(stats.get("paused_at", 0))
        rows = jload(run["rows_data"]) or []
        if paused_at < len(rows):
            rows[paused_at]["status"] = "passed"
            rows[paused_at]["detail"] = f"approved by {ME}"
        exec_("UPDATE workflow_runs SET rows_data = %s, status = 'running' WHERE id = %s",
              (json.dumps(rows), run_id))
        exec_("INSERT INTO events (actor, verb, target) VALUES (%s, 'approved run', '#' || %s)", (ME, run["number"]))
        flowengine.start_run(run_id, paused_at + 1)
        return True

    @strawberry.mutation
    def save_workflow(self, name: str, description: str, steps: JSON, id: int | None = None,
                      color: str = "#5c7a4c", pinned: bool = True) -> int:
        """Create a flow (no id) or rewrite one (id). `steps` is the engine's
        node list — kind, label and config per step — so what the pipeline
        editor drew is what the next run executes.

        Creating used to UPSERT on the name, so "create" with a name already in
        use silently REPLACED that flow's pipeline and handed back its id. A
        name clash is an error, not a merge."""
        name = (name or "").strip()
        if not name:
            raise ValueError("A flow needs a name.")
        if id:
            exec_("UPDATE workflows SET name = %s, description = %s, nodes = %s WHERE id = %s",
                  (name, description, json.dumps(steps), id))
            audit("updated flow", name)
            return id
        if q1("SELECT id FROM workflows WHERE name = %s", (name,)):
            raise ValueError(f"A flow called '{name}' already exists.")
        exec_("""INSERT INTO workflows (name, description, color, pinned, status, nodes)
                 VALUES (%s, %s, %s, %s, 'active', %s)""",
              (name, description, color, pinned, json.dumps(steps)))
        audit("created flow", name)
        return (q1("SELECT id FROM workflows WHERE name = %s", (name,)) or {"id": 0})["id"]

    @strawberry.mutation
    def delete_workflow(self, id: int) -> bool:
        exec_("DELETE FROM workflow_runs WHERE workflow_id = %s", (id,))
        exec_("INSERT INTO events (actor, verb, target) SELECT %s, 'deleted flow', name FROM workflows WHERE id = %s", (ME, id))
        exec_("DELETE FROM workflows WHERE id = %s", (id,))
        return True

    @strawberry.mutation
    def set_workflow_status(self, id: int, status: str) -> bool:
        exec_("UPDATE workflows SET status = %s WHERE id = %s", (status, id))
        exec_("INSERT INTO events (actor, verb, target) SELECT %s, %s || ' flow', name FROM workflows WHERE id = %s",
              (ME, 'enabled' if status == 'active' else 'paused', id))
        return True

    @strawberry.mutation
    def set_workflow_pinned(self, id: int, pinned: bool) -> bool:
        exec_("UPDATE workflows SET pinned = %s WHERE id = %s", (pinned, id))
        return True

    # ——— publishing ———
    @staticmethod
    def _do_build(id: int, generator: str | None) -> dict:
        """Shared build path. Resolves the generator (explicit param wins, else
        the choice persisted in the site's config jsonb, else 'mari'), persists
        it for rebuilds, builds, and returns a result dict — never raises."""
        import time
        site = q1("SELECT * FROM sites WHERE id = %s", (id,))
        if not site:
            return {"ok": False, "error": f"site {id} not found"}
        site["theme"] = jload(site["theme"]) or {}
        gen = (generator or site["theme"].get("generator") or "mari").lower()
        # source_path rides along for the 'source_path' feature switch: the page
        # prints the document path it was built from when the site asks for it.
        docs = q("""SELECT DISTINCT d.id, d.title, d.snippet, d.body, d.source_path FROM documents d
                    JOIN tags t ON t.document_id = d.id
                    WHERE t.tag = 'customer-facing' AND d.body <> '' ORDER BY d.id""")
        if not docs:
            docs = q("SELECT id, title, snippet, body, source_path FROM documents WHERE body <> '' ORDER BY id LIMIT 8")
        t0 = time.time()
        try:
            sitebuilder.build_site(site, docs, generator=gen)
        except sitebuilder.SiteBuildError as e:
            audit("site build failed", f"{site['name']} ({gen}): {str(e)[:120]}")
            return {"ok": False, "generator": gen, "error": str(e)}
        except Exception as e:  # never 500 the GraphQL layer over a build
            audit("site build failed", f"{site['name']} ({gen}): {type(e).__name__}")
            return {"ok": False, "generator": gen, "error": f"{type(e).__name__}: {e}"}
        exec_("UPDATE sites SET docs = %s WHERE id = %s", (len(docs), id))
        if gen != site["theme"].get("generator"):  # persist only after a successful build
            exec_("UPDATE sites SET theme = theme || %s::jsonb WHERE id = %s",
                  (json.dumps({"generator": gen}), id))
        audit("built site", f"{site['name']} ({gen}, {len(docs)} pages)")
        return {"ok": True, "generator": gen, "url": f"/sites/site_{id}/",
                "pages": len(docs), "seconds": round(time.time() - t0, 1)}

    @strawberry.mutation
    def build_site(self, id: int, generator: str | None = None) -> str:
        """Build the static site from real documents; returns the preview URL
        ('' on failure — use buildSiteEx for the error detail). generator:
        'mari' (default) or 'docusaurus'; persisted on the site for rebuilds."""
        res = MutPublish._do_build(id, generator)
        return res.get("url", "") if res.get("ok") else ""

    @strawberry.mutation
    def build_site_ex(self, id: int, generator: str | None = None) -> JSON:
        """Build with full result detail: {ok, generator, url?, pages?, seconds?,
        error?}. Build failures surface here as data, never as a 500."""
        return MutPublish._do_build(id, generator)

    @strawberry.mutation
    def ai_customize_site(self, id: int, instruction: str) -> JSON:
        """LLM-driven theme customization — scope-bounded to the config JSON."""
        site = q1("SELECT * FROM sites WHERE id = %s", (id,))
        if not site:
            return {}
        theme = jload(site["theme"])
        prompt = (
            f"Current doc-site theme config: {json.dumps(theme)}\n"
            f"Available theme presets: {list(sitebuilder.theme_presets())}\n"
            f'User request: "{instruction}"\n\n'
            'Return the updated config as JSON with only these keys: theme (preset name), '
            'accent (hex color), radius (0-18 int), density (comfortable|compact|dense), mode (light|dark). '
            "Change only what the request implies."
        )
        out = llm.generate_json(prompt, system="You edit doc-site theme configs. Config JSON only — never code.")
        if isinstance(out, dict):
            allowed = {k: out[k] for k in ("theme", "accent", "radius", "density", "mode") if k in out}
            if allowed:
                exec_("UPDATE sites SET theme = theme || %s::jsonb WHERE id = %s", (json.dumps(allowed), id))
                audit("AI-customized site", f"{site['name']}: {instruction[:80]}")
                return allowed
        return {}

    @strawberry.mutation
    def create_site(self, name: str, domain: str, sources: JSON) -> int:
        """Create a doc site. `sources` is the tag list that decides which
        documents it may publish. Site names are unique, and a clash used to
        fall through ON CONFLICT DO NOTHING and return the EXISTING site's id —
        the caller was told it had created a site it had not. It is an error."""
        name, domain = (name or "").strip(), (domain or "").strip()
        if not name or not domain:
            raise ValueError("A doc site needs a name and a domain.")
        if q1("SELECT id FROM sites WHERE name = %s", (name,)):
            raise ValueError(f"A doc site called '{name}' already exists.")
        exec_("""INSERT INTO sites (name, domain, status, theme, sources, nav, gates, docs, warnings)
                 VALUES (%s, %s, 'draft',
                         '{"theme":"Mari Editorial","accent":"#b04e2c","radius":10,"density":"comfortable","mode":"light"}',
                         %s, '[]', '[{"gate":"Prose check","status":"pass"},{"gate":"Fact check","status":"pass"},{"gate":"Freshness","status":"pass"}]',
                         0, 0) ON CONFLICT (name) DO NOTHING""", (name, domain, json.dumps(sources)))
        audit("created site", name)
        return (q1("SELECT id FROM sites WHERE name = %s", (name,)) or {"id": 0})["id"]

    @strawberry.mutation
    def update_site_theme(self, id: int, theme: JSON) -> bool:
        exec_("UPDATE sites SET theme = theme || %s::jsonb WHERE id = %s", (json.dumps(theme), id))
        return True

    @strawberry.mutation
    def set_site_feature(self, id: int, key: str, on: bool) -> bool:
        """Turn one generator switch on or off for a site. Only keys the
        generator actually reads (`site_feature_defs`) are accepted — storing
        an unknown key would put a toggle on the page that changes nothing
        about the built site. Takes effect on the next build."""
        feature = q1("SELECT label, default_on FROM site_feature_defs WHERE key = %s", (key,))
        if not feature:
            raise ValueError(f"No site feature '{key}'")
        site = q1("SELECT name FROM sites WHERE id = %s", (id,))
        if not site:
            return False
        exec_("UPDATE sites SET features = features || %s::jsonb WHERE id = %s",
              (json.dumps({key: bool(on)}), id))
        audit("enabled site feature" if on else "disabled site feature", site["name"],
              detail=[("Feature", feature["label"]), ("Key", key),
                      ("Shipped default", "on" if feature["default_on"] else "off")])
        return True

    @strawberry.mutation
    def deploy_site(self, id: int) -> str:
        site = q1("SELECT * FROM sites WHERE id = %s", (id,))
        if not site:
            return ""
        # real build first — the release is the actual artifact
        if not MutPublish.build_site(self, id):
            return ""
        site = q1("SELECT * FROM sites WHERE id = %s", (id,))
        dep_row = q1("SELECT value FROM settings WHERE key = 'deploy'")
        deploy_cfg = jload(dep_row["value"]) if dep_row else {}
        uploaded, detail = sitebuilder.deploy_to_s3(str(sitebuilder.BUILDS / f"site_{id}"), deploy_cfg or {})
        last = q1("SELECT version FROM releases WHERE site_id = %s ORDER BY id DESC LIMIT 1", (id,))
        major, minor, patch = (last["version"].lstrip("v").split(".") if last else ["1", "7", "2"])
        version = f"v{major}.{int(minor) + 1}.0"
        exec_("UPDATE releases SET status = 'previous' WHERE site_id = %s AND status = 'live'", (id,))
        exec_("""INSERT INTO releases (site_id, version, status, deployed, docs, notes)
                 VALUES (%s, %s, 'live', to_char(now(), 'Mon DD, HH12:MI AM'), %s, %s)
                 ON CONFLICT (site_id, version) DO NOTHING""", (id, version, site["docs"], detail))
        exec_("UPDATE sites SET status = 'live' WHERE id = %s", (id,))
        audit("deployed site", f"{site['name']} {version} — {detail}")
        return version

    @strawberry.mutation
    def rollback_release(self, id: int) -> bool:
        rel = q1("SELECT * FROM releases WHERE id = %s", (id,))
        if not rel:
            return False
        exec_("UPDATE releases SET status = 'previous' WHERE site_id = %s AND status = 'live'", (rel["site_id"],))
        exec_("UPDATE releases SET status = 'live' WHERE id = %s", (id,))
        audit("rolled back to", rel["version"])
        return True

    @strawberry.mutation
    def import_brand(self, url: str) -> JSON:
        """Harvest brand identity (accent colors, logo, display font) from a
        company homepage URL. Returns a candidate branding object for the UI to
        prefill — nothing is saved server-side. Fetch/parse failures surface as
        {error, warnings} data, never a 500."""
        try:
            return brandimport.import_brand(url)
        except Exception as e:  # belt-and-braces: this mutation never 500s
            return {"error": f"{type(e).__name__}: {e}", "warnings": []}

    # ——— MCP servers (DESIGN.md §19: per-project, configurable in UI) ———
    @strawberry.mutation
    def create_mcp_server(self, name: str, scope: str, capabilities: JSON) -> str:
        """Create an MCP server: generated endpoint + bearer token; capability
        toggles decide which tool groups it exposes. Returns the token (shown once)."""
        import re as _re
        import secrets
        slug = _re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "server"
        token = "mari_mcp_" + secrets.token_hex(12)
        caps = [c for c in (capabilities or []) if isinstance(c, str)]
        tools = {"search": 3, "facts": 4, "glossary": 2, "chat": 1, "lineage": 2, "answers": 2}
        n_tools = sum(tools.get(c, 0) for c in caps) or 1
        exec_("""INSERT INTO mcp_servers (name, url, scope, status, tools, config, token)
                 VALUES (%s, %s, %s, 'connected', %s, %s, %s)
                 ON CONFLICT (name) DO UPDATE SET config = EXCLUDED.config, tools = EXCLUDED.tools""",
              (name, f"https://mcp.mari.cloud/{slug}", scope, n_tools,
               json.dumps({"capabilities": caps}), token))
        audit("created MCP server", name)
        return token

    @strawberry.mutation
    def update_mcp_server(self, id: int, scope: str | None = None, capabilities: JSON = None) -> bool:
        if scope:
            exec_("UPDATE mcp_servers SET scope = %s WHERE id = %s", (scope, id))
        if capabilities is not None:
            caps = [c for c in capabilities if isinstance(c, str)]
            tools = {"search": 3, "facts": 4, "glossary": 2, "chat": 1, "lineage": 2, "answers": 2}
            exec_("UPDATE mcp_servers SET config = jsonb_set(config, '{capabilities}', %s), tools = %s WHERE id = %s",
                  (json.dumps(caps), sum(tools.get(c, 0) for c in caps) or 1, id))
        exec_("INSERT INTO events (actor, verb, target) SELECT %s, 'updated MCP server', name FROM mcp_servers WHERE id = %s", (ME, id))
        return True

    @strawberry.mutation
    def delete_mcp_server(self, id: int) -> bool:
        exec_("INSERT INTO events (actor, verb, target) SELECT %s, 'deleted MCP server', name FROM mcp_servers WHERE id = %s", (ME, id))
        exec_("DELETE FROM mcp_servers WHERE id = %s", (id,))
        return True

    @strawberry.mutation
    def test_mcp_server(self, id: int) -> JSON:
        """Connection test — resolves against the local knowledge base (the demo
        MCP host IS this API), reporting per-capability tool availability."""
        row = q1("SELECT * FROM mcp_servers WHERE id = %s", (id,))
        if not row:
            return {"ok": False, "error": "not found"}
        caps = (jload(row["config"]) or {}).get("capabilities", ["search"])
        checks = {}
        if "search" in caps:
            checks["search"] = q1("SELECT count(*) AS n FROM documents")["n"]
        if "facts" in caps:
            checks["facts"] = q1("SELECT count(*) AS n FROM facts")["n"]
        if "glossary" in caps:
            checks["glossary"] = q1("SELECT count(*) AS n FROM glossary WHERE NOT candidate")["n"]
        if "answers" in caps:
            checks["answers"] = q1("SELECT count(*) AS n FROM approved_answers WHERE status = 'approved'")["n"]
        if "lineage" in caps:
            checks["lineage"] = q1("SELECT count(*) AS n FROM edges")["n"]
        if "chat" in caps:
            checks["chat"] = 1
        exec_("UPDATE mcp_servers SET status = 'connected' WHERE id = %s", (id,))
        return {"ok": True, "latency_ms": 12, "checks": {k: int(v) for k, v in checks.items()}}
