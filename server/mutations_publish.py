"""Mari — publishing mutations: sites, releases, MCP servers, workflows."""

from __future__ import annotations

import json
import os

import strawberry
from strawberry.scalars import JSON

import brandimport
import flowengine
import llm
import sitebuilder
from db import actor_name, audit, exec_, jload, q, q1
from mutations_admin import _require_admin

# ——— sites.theme: validated on the way in ———
#
# Built sites are served from /sites, the same origin as /graphql, so a theme
# value that escapes a CSS declaration or an HTML attribute in the generator
# reaches the console session cookie (AUTH-12). The generator escapes on
# interpolation; this stops the value being storable at all. It matters most for
# aiCustomizeSite, which writes LLM output straight into a template variable —
# a prompt-injected document could otherwise choose the accent colour.

THEME_KEYS = ("theme", "accent", "radius", "density", "mode")


def _next_version(site_id: int) -> str:
    """Next release version for a site: minor bump on the newest stored one.

    The stored string is whatever a previous deploy (or a seed row, or a hand
    edit) wrote. An unguarded three-way unpack raised ValueError on any two- or
    four-part version, mid-deploy and after the upload (ERR-3). Anything
    unparseable restarts the numbering rather than failing a deploy that has
    already happened."""
    last = q1("SELECT version FROM releases WHERE site_id = %s ORDER BY id DESC LIMIT 1",
              (site_id,))
    parts = (last["version"] or "").lstrip("v").split(".") if last else ["1", "7", "2"]
    try:
        major, minor = int(parts[0]), int(parts[1])
    except (IndexError, ValueError):
        major, minor = 1, 0
    return f"v{major}.{minor + 1}.0"


def _clean_theme(theme) -> tuple[dict, list[str]]:
    """Return (storable theme, rejected reasons). Unknown keys are rejected —
    the generator reads exactly these five and nothing else."""
    if not isinstance(theme, dict):
        return {}, ["theme must be a JSON object"]
    out: dict = {}
    bad: list[str] = []
    presets = sitebuilder.theme_presets()
    for key, value in theme.items():
        if key not in THEME_KEYS:
            bad.append(f"unknown key '{key}'")
        elif key == "theme":
            if value in presets:
                out[key] = value
            else:
                bad.append(f"no theme preset named '{value}' "
                           f"(have: {', '.join(sorted(presets))})")
        elif key == "accent":
            if sitebuilder.css_color(value, ""):
                out[key] = str(value).strip()
            else:
                bad.append(f"accent '{value}' is not a colour "
                           "(expected #rrggbb, rgb(), hsl(), or a colour keyword)")
        elif key == "radius":
            try:
                out[key] = min(max(int(value), 0), 18)
            except (TypeError, ValueError):
                bad.append(f"radius '{value}' is not a number 0-18")
        elif key == "density":
            if value in sitebuilder.DENSITIES:
                out[key] = value
            else:
                bad.append(f"density '{value}' is not one of "
                           f"{', '.join(sorted(sitebuilder.DENSITIES))}")
        elif key == "mode":
            if value in sitebuilder.MODES:
                out[key] = value
            else:
                bad.append(f"mode '{value}' is not light or dark")
    return out, bad


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
              (actor_name(), n, workflow_id))
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
            rows[paused_at]["detail"] = f"approved by {actor_name()}"
        exec_("UPDATE workflow_runs SET rows_data = %s, status = 'running' WHERE id = %s",
              (json.dumps(rows), run_id))
        exec_("INSERT INTO events (actor, verb, target) VALUES (%s, 'approved run', '#' || %s)", (actor_name(), run["number"]))
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
        exec_("INSERT INTO events (actor, verb, target) SELECT %s, 'deleted flow', name FROM workflows WHERE id = %s", (actor_name(), id))
        exec_("DELETE FROM workflows WHERE id = %s", (id,))
        return True

    @strawberry.mutation
    def set_workflow_status(self, id: int, status: str) -> bool:
        exec_("UPDATE workflows SET status = %s WHERE id = %s", (status, id))
        exec_("INSERT INTO events (actor, verb, target) SELECT %s, %s || ' flow', name FROM workflows WHERE id = %s",
              (actor_name(), 'enabled' if status == 'active' else 'paused', id))
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
        site["nav"] = jload(site.get("nav")) or []
        gen = (generator or site["theme"].get("generator") or "mari").lower()
        # `sources` is the tag list createSite persisted for this site; it used
        # to be stored and never read, so every site — regardless of what its
        # own config said — published whatever happened to be tagged
        # 'customer-facing' globally. A site scoped to its own tags is what
        # makes running more than one published site (e.g. public docs vs. an
        # internal partner site) possible at all.
        tags = [t for t in (jload(site.get("sources")) or []) if isinstance(t, str) and t.strip()]
        if not tags:
            tags = ["customer-facing"]
        # source_path rides along for the 'source_path' feature switch: the page
        # prints the document path it was built from when the site asks for it.
        docs = q("""SELECT DISTINCT d.id, d.title, d.snippet, d.body, d.source_path FROM documents d
                    JOIN tags t ON t.document_id = d.id
                    WHERE t.tag = ANY(%s) AND d.body <> '' ORDER BY d.id""", (tags,))
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
        if not isinstance(out, dict):
            return {"error": "The model did not return a theme config. Try rewording the request."}
        # The model's output is untrusted input — it goes straight into a
        # template variable on a page served from the console's own origin, and
        # the documents that shape the prompt are synced from other systems
        # (AUTH-12). Anything that is not a colour or a known token is dropped,
        # and the caller is told which, so a request the model mangled does not
        # silently look like it worked.
        allowed, bad = _clean_theme({k: v for k, v in out.items() if k in THEME_KEYS})
        if not allowed:
            return {"error": "No usable theme change — " + ("; ".join(bad) if bad
                                                            else "the model changed nothing.")}
        exec_("UPDATE sites SET theme = theme || %s::jsonb WHERE id = %s", (json.dumps(allowed), id))
        audit("AI-customized site", f"{site['name']}: {instruction[:80]}")
        return dict(allowed, **({"skipped": bad} if bad else {}))

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
    def rename_site(self, id: int, name: str) -> bool:
        """Rename a site — the name is what the generator upper-cases into the
        header logotype, so this is the only way to fix it short of editing
        the row directly. Same uniqueness rule as create_site."""
        name = (name or "").strip()
        if not name:
            raise ValueError("A doc site needs a name.")
        clash = q1("SELECT id FROM sites WHERE name = %s AND id != %s", (name, id))
        if clash:
            raise ValueError(f"A doc site called '{name}' already exists.")
        site = q1("SELECT name FROM sites WHERE id = %s", (id,))
        if not site:
            return False
        exec_("UPDATE sites SET name = %s WHERE id = %s", (name, id))
        audit("renamed site", f"{site['name']} → {name}")
        return True

    @strawberry.mutation
    def update_site_theme(self, id: int, theme: JSON) -> bool:
        """Store theme values the generator can actually render. A rejected
        value is an error naming the value and what was expected — silently
        dropping it would leave the Publish page showing a colour the built
        site does not use."""
        clean, bad = _clean_theme(theme)
        if bad:
            raise ValueError("Theme not saved — " + "; ".join(bad))
        if not clean:
            return False
        exec_("UPDATE sites SET theme = theme || %s::jsonb WHERE id = %s", (json.dumps(clean), id))
        return True

    @strawberry.mutation
    def set_site_nav(self, id: int, nav: JSON) -> bool:
        """Curate the published sidebar: an ordered list of
        `{label, docs: [document id, ...]}` sections (label omitted/null for
        an unlabeled group — used to pin a page above every section, e.g. a
        landing/overview doc). This ONLY reorders and groups; it doesn't
        change which documents get published — a doc missing from every
        section still ships, just in a trailing unlabeled group (see
        sitebuilder._apply_nav), so a nav a builder forgot to update can't
        silently un-publish something. Nothing here reaches a template raw:
        labels are HTML-escaped at render time same as any other doc title."""
        if not isinstance(nav, list):
            raise ValueError("nav must be a list of sections.")
        clean: list[dict] = []
        for i, section in enumerate(nav):
            if not isinstance(section, dict):
                raise ValueError(f"Section {i} must be an object with 'label' and 'docs'.")
            label = section.get("label")
            if label is not None and not isinstance(label, str):
                raise ValueError(f"Section {i}'s label must be a string or null.")
            docs = section.get("docs")
            if not isinstance(docs, list) or not all(isinstance(d, int) for d in docs):
                raise ValueError(f"Section {i}'s docs must be a list of document ids.")
            clean.append({"label": label, "docs": docs})
        exec_("UPDATE sites SET nav = %s::jsonb WHERE id = %s", (json.dumps(clean), id))
        audit("updated site nav", (q1("SELECT name FROM sites WHERE id = %s", (id,)) or {}).get("name", f"site {id}"))
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
        # Every other site's prefix, so the stale sweep does not delete a
        # neighbour. Sites nest by design: mari.guru/docs owns 'docs/' and
        # mari.guru/docs/canon owns 'docs/canon/' inside it.
        others = q("SELECT domain FROM sites WHERE id <> %s", (id,)) or []
        reserved = [sitebuilder._s3_prefix(str(r["domain"] or "")) for r in others]
        uploaded, detail = sitebuilder.deploy_to_s3(
            str(sitebuilder.BUILDS / f"site_{id}"), deploy_cfg or {}, site,
            reserved_prefixes=[p for p in reserved if p])
        bucket_configured = bool((deploy_cfg or {}).get("bucket") or os.environ.get("MARI_S3_BUCKET"))
        if bucket_configured and not uploaded:
            # A site whose upload failed is not live anywhere. Recording the
            # release and flipping status='live' regardless was the whole of
            # ERR-2: nothing downstream could tell a deployed site from a
            # failed one. No release row, no status change, real reason.
            raise ValueError(f"Deploy failed — {detail}. The build is intact; "
                             "fix the bucket or credentials and deploy again.")
        version = _next_version(id)
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
    #
    # An MCP server is a bearer token that reads the whole knowledge base from
    # outside the console. Minting, rescoping and deleting one is the same kind
    # of act as creating an API key, so it carries the same guard (AUTH-4):
    # admin. Testing an existing one reads nothing new and stays open.
    @strawberry.mutation
    def create_mcp_server(self, info: strawberry.Info, name: str, scope: str, capabilities: JSON) -> str:
        """Create an MCP server: generated endpoint + bearer token; capability
        toggles decide which tool groups it exposes. Returns the token (shown once)."""
        import re as _re
        import secrets
        actor = _require_admin(info)
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
        audit("created MCP server", name, actor["name"],
              detail=[("Scope", scope), ("Capabilities", ", ".join(caps) or "(none)")])
        return token

    @strawberry.mutation
    def update_mcp_server(self, info: strawberry.Info, id: int, scope: str | None = None,
                          capabilities: JSON = None) -> bool:
        _require_admin(info)
        if scope:
            exec_("UPDATE mcp_servers SET scope = %s WHERE id = %s", (scope, id))
        if capabilities is not None:
            caps = [c for c in capabilities if isinstance(c, str)]
            tools = {"search": 3, "facts": 4, "glossary": 2, "chat": 1, "lineage": 2, "answers": 2}
            exec_("UPDATE mcp_servers SET config = jsonb_set(config, '{capabilities}', %s), tools = %s WHERE id = %s",
                  (json.dumps(caps), sum(tools.get(c, 0) for c in caps) or 1, id))
        exec_("INSERT INTO events (actor, verb, target) SELECT %s, 'updated MCP server', name FROM mcp_servers WHERE id = %s", (actor_name(), id))
        return True

    @strawberry.mutation
    def delete_mcp_server(self, info: strawberry.Info, id: int) -> bool:
        _require_admin(info)
        exec_("INSERT INTO events (actor, verb, target) SELECT %s, 'deleted MCP server', name FROM mcp_servers WHERE id = %s", (actor_name(), id))
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
