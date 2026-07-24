"""Mari Cloud — static doc-site builder (DESIGN.md §16).

Builds a real static website from documents in Postgres, themed by the site's
config, with an injected customizer widget. Output: server/builds/site_<id>/.
Preview served by FastAPI at /sites/site_<id>/. Deploy uploads the build to S3
when credentials are configured; otherwise the release is an honest local build.
"""

from __future__ import annotations

import html as html_mod
import json
import os
import pathlib
import re
import shutil
import subprocess
import time

import markdown
import nh3

BUILDS = pathlib.Path(os.environ.get("MARI_BUILDS_DIR", pathlib.Path(__file__).parent / "builds"))

FONTS = "https://fonts.googleapis.com/css2?family=Playfair+Display:wght@500;600&family=Lora:ital@0;1&family=Source+Sans+3:wght@400;600&display=swap"

# Fallback copy of the presets seeded into site_theme_presets. The table is the
# source of truth (the Publish page reads it, and an operator can edit a row);
# this dict keeps a build working when the table is missing or empty, e.g. in a
# unit test with no database.
THEME_PRESETS = {
    "Mari Editorial": {"accent": "#b04e2c", "bg": "#f6f0e3", "card": "#fcf9f1", "ink": "#2d2a22", "line": "#e2d8c2",
                       "display": "'Playfair Display', Georgia, serif", "serif": "'Lora', Georgia, serif"},
    "Minimal": {"accent": "#1f6feb", "bg": "#ffffff", "card": "#fafafa", "ink": "#1a1a1a", "line": "#e5e5e5",
                "display": "'Source Sans 3', system-ui, sans-serif", "serif": "'Source Sans 3', system-ui, sans-serif"},
    "Material": {"accent": "#1a73e8", "bg": "#f5f5f6", "card": "#ffffff", "ink": "#202124", "line": "#dadce0",
                 "display": "'Source Sans 3', Roboto, sans-serif", "serif": "'Source Sans 3', Roboto, sans-serif"},
    "Starlight": {"accent": "#7c9cff", "bg": "#17181c", "card": "#1f2127", "ink": "#e7e9ee", "line": "#33363f",
                  "display": "'Source Sans 3', system-ui, sans-serif", "serif": "'Source Sans 3', system-ui, sans-serif"},
}

# The switches this generator honours, and what they do when nothing overrides
# them. site_feature_defs carries the same keys with the labels the console
# shows; this dict is the fallback for a build with no database, and the list
# of keys is what makes each toggle on the Publish page mean something.
FEATURE_DEFAULTS = {"sidebar": True, "search": True, "customizer": True,
                    "provenance": True, "source_path": False}


def _rows(sql: str) -> list[dict]:
    """Read config rows, tolerating a build that has no database at all."""
    try:
        from db import q
        return q(sql)
    except Exception:
        return []


def theme_presets() -> dict:
    """Presets keyed by the name stored in sites.theme->>'theme'."""
    rows = _rows("SELECT * FROM site_theme_presets ORDER BY sort, key")
    if not rows:
        return THEME_PRESETS
    return {r["key"]: {"accent": r["accent"], "bg": r["bg"], "card": r["card"], "ink": r["ink"],
                       "line": r["line"], "display": r["display_font"], "serif": r["serif_font"]}
            for r in rows}


def site_features(site: dict) -> dict[str, bool]:
    """Which switches are on for this site: the shipped default for each key,
    overlaid with what the site stored. Unknown stored keys are ignored — the
    generator only honours the keys it implements."""
    stored = site.get("features")
    if isinstance(stored, str):
        stored = json.loads(stored or "{}")
    if not isinstance(stored, dict):
        stored = {}
    rows = _rows("SELECT key, default_on FROM site_feature_defs")
    defaults = {r["key"]: bool(r["default_on"]) for r in rows} if rows else dict(FEATURE_DEFAULTS)
    return {k: bool(stored.get(k, v)) for k, v in defaults.items()}

CUSTOMIZER_JS = """
(function () {
  var API = location.origin + '/graphql';
  var SITE_ID = window.__MARI_SITE_ID__;
  var btn = document.createElement('button');
  btn.id = 'mari-customize-btn';
  btn.textContent = '\\u270e Customize';
  document.body.appendChild(btn);
  var panel = document.createElement('div');
  panel.id = 'mari-customize-panel';
  panel.innerHTML = '<h3>Customize this site</h3>' +
    '<label>Accent <input type="color" id="mc-accent"></label>' +
    '<label>Corner radius <input type="range" id="mc-radius" min="0" max="18" step="1"></label>' +
    '<label>Density <select id="mc-density"><option>comfortable</option><option>compact</option><option>dense</option></select></label>' +
    '<label class="mc-row"><input type="checkbox" id="mc-dark"> Dark mode</label>' +
    '<div class="mc-actions"><button id="mc-save">Save to Mari</button><span id="mc-status"></span></div>' +
    '<p class="mc-note">Changes preview instantly. Save writes the theme back to Mari Cloud; the next deploy ships it.</p>';
  document.body.appendChild(panel);
  var root = document.documentElement;
  function readVar(n) { return getComputedStyle(root).getPropertyValue(n).trim(); }
  var accent = document.getElementById('mc-accent');
  var radius = document.getElementById('mc-radius');
  var density = document.getElementById('mc-density');
  var dark = document.getElementById('mc-dark');
  accent.value = readVar('--accent') || '#b04e2c';
  radius.value = parseInt(readVar('--radius')) || 10;
  density.value = document.body.dataset.density || 'comfortable';
  dark.checked = document.body.classList.contains('dark');
  accent.oninput = function () { root.style.setProperty('--accent', accent.value); };
  radius.oninput = function () { root.style.setProperty('--radius', radius.value + 'px'); };
  density.onchange = function () { document.body.dataset.density = density.value; };
  dark.onchange = function () { document.body.classList.toggle('dark', dark.checked); };
  btn.onclick = function () { panel.classList.toggle('open'); };
  document.getElementById('mc-save').onclick = function () {
    var status = document.getElementById('mc-status');
    status.textContent = 'Saving\\u2026';
    fetch(API, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        query: 'mutation($id: Int!, $theme: JSON!) { updateSiteTheme(id: $id, theme: $theme) }',
        variables: { id: SITE_ID, theme: {
          accent: accent.value, radius: parseInt(radius.value),
          density: density.value, mode: dark.checked ? 'dark' : 'light' } }
      })
    }).then(function (r) { return r.json(); }).then(function () {
      status.textContent = 'Saved \\u2713 (redeploy to publish)';
    }).catch(function () { status.textContent = 'Mari API unreachable'; });
  };
})();
"""

SEARCH_JS = """
(function () {
  var box = document.querySelector('.mari-search');
  if (!box) return;
  var scope = box.closest('aside') || box.closest('.mari-pages');
  var links = scope ? Array.prototype.slice.call(scope.querySelectorAll('a')) : [];
  box.addEventListener('input', function () {
    var qs = box.value.trim().toLowerCase();
    links.forEach(function (a) {
      a.style.display = !qs || a.textContent.toLowerCase().indexOf(qs) !== -1 ? '' : 'none';
    });
  });
})();
"""

CUSTOMIZER_CSS = """
#mari-customize-btn { position: fixed; right: 18px; bottom: 18px; z-index: 999;
  background: var(--accent); color: #fff; border: none; border-radius: 999px;
  padding: 10px 16px; font: 600 13px 'Source Sans 3', sans-serif; cursor: pointer;
  box-shadow: 2px 3px 0 rgba(0,0,0,0.18); }
#mari-customize-panel { position: fixed; right: 18px; bottom: 66px; z-index: 999;
  width: 250px; background: var(--card); color: var(--ink); border: 1.5px solid var(--line);
  border-radius: 12px 14px 11px 13px; padding: 14px; display: none;
  box-shadow: 3px 4px 0 rgba(0,0,0,0.12); font: 13px 'Source Sans 3', sans-serif; }
#mari-customize-panel.open { display: block; }
#mari-customize-panel h3 { margin: 0 0 10px; font: 600 15px var(--display); }
#mari-customize-panel label { display: flex; justify-content: space-between; align-items: center;
  gap: 8px; margin: 8px 0; }
#mari-customize-panel .mc-row { justify-content: flex-start; }
#mari-customize-panel .mc-actions { display: flex; gap: 8px; align-items: center; margin-top: 10px; }
#mari-customize-panel .mc-actions button { background: var(--accent); color: #fff; border: none;
  border-radius: 8px; padding: 6px 12px; font: 600 12.5px 'Source Sans 3', sans-serif; cursor: pointer; }
#mari-customize-panel .mc-note { color: color-mix(in srgb, var(--ink) 55%, transparent);
  font-size: 11px; margin: 8px 0 0; }
#mc-status { font-size: 11.5px; }
"""


def _site_css(theme: dict) -> str:
    presets = theme_presets()
    fallback = presets.get("Mari Editorial") or next(iter(presets.values()))
    preset = presets.get(theme.get("theme", "Mari Editorial"), fallback)
    # A site that has not picked an accent ships its preset's own — which is
    # the swatch the Publish page shows for that preset.
    accent = theme.get("accent") or preset.get("accent") or "#b04e2c"
    radius = int(theme.get("radius", 10))
    density = theme.get("density", "comfortable")
    pad = {"comfortable": 28, "compact": 20, "dense": 14}.get(density, 28)
    dark = presets.get("Starlight", THEME_PRESETS["Starlight"])
    return f"""
:root {{ --accent: {accent}; --radius: {radius}px; --bg: {preset['bg']}; --card: {preset['card']};
  --ink: {preset['ink']}; --line: {preset['line']}; --display: {preset['display']}; --serif: {preset['serif']}; }}
body.dark {{ --bg: {dark['bg']}; --card: {dark['card']}; --ink: {dark['ink']}; --line: {dark['line']}; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--ink); font-family: var(--serif); line-height: 1.6; }}
.wrap {{ display: grid; grid-template-columns: 250px 1fr; min-height: 100vh; }}
header {{ grid-column: 1 / -1; display: flex; align-items: center; gap: 18px;
  padding: 14px {pad}px; border-bottom: 1.5px solid var(--line); background: var(--card); }}
header .logo {{ font: 600 17px var(--display); letter-spacing: 0.06em; }}
header nav {{ margin-left: auto; display: flex; gap: 16px; font: 13.5px 'Source Sans 3', sans-serif; }}
header nav a {{ color: inherit; text-decoration: none; opacity: 0.75; }}
header nav a:hover {{ opacity: 1; color: var(--accent); }}
aside {{ border-right: 1.5px solid var(--line); padding: {pad}px 18px; background: var(--card); }}
aside a {{ display: block; padding: 6px 10px; border-radius: var(--radius); color: inherit;
  text-decoration: none; font: 14px 'Source Sans 3', sans-serif; opacity: 0.8; }}
aside a:hover {{ background: color-mix(in srgb, var(--accent) 9%, transparent); opacity: 1; }}
aside a.active {{ background: color-mix(in srgb, var(--accent) 14%, transparent); color: var(--accent);
  font-weight: 600; opacity: 1; }}
main {{ padding: {pad + 8}px {pad + 14}px; max-width: 760px; }}
main h1 {{ font: 600 34px var(--display); margin: 0 0 14px; }}
main h2 {{ font: 500 23px var(--display); margin: 28px 0 8px; border-bottom: 1px solid var(--line); padding-bottom: 6px; }}
main h3 {{ font: 600 17px var(--display); margin: 20px 0 6px; }}
main a {{ color: var(--accent); }}
main code {{ background: color-mix(in srgb, var(--ink) 8%, transparent); padding: 1.5px 6px;
  border-radius: 6px; font-size: 0.9em; }}
main pre {{ background: #26231c; color: #f3ecd9; padding: 16px 18px; border-radius: var(--radius);
  overflow-x: auto; }}
main pre code {{ background: none; padding: 0; color: inherit; }}
main table {{ border-collapse: collapse; width: 100%; font-size: 14.5px; }}
main th, main td {{ border: 1px solid var(--line); padding: 8px 12px; text-align: left; }}
main th {{ font-family: 'Source Sans 3', sans-serif; background: color-mix(in srgb, var(--ink) 4%, transparent); }}
footer {{ grid-column: 1 / -1; padding: 18px {pad}px; border-top: 1.5px solid var(--line);
  font: 12px 'Source Sans 3', sans-serif; opacity: 0.65; }}
/* feature switches (site_feature_defs) */
body.no-sidebar .wrap {{ grid-template-columns: 1fr; }}
.mari-search {{ width: 100%; margin-bottom: 10px; padding: 6px 10px; border-radius: var(--radius);
  border: 1.5px solid var(--line); background: var(--bg); color: inherit;
  font: 13.5px 'Source Sans 3', sans-serif; }}
.mari-search-block {{ margin-bottom: 18px; max-width: 320px; }}
.mari-source {{ margin: -6px 0 18px; font: 12px 'Source Sans 3', sans-serif; opacity: 0.6; }}
{CUSTOMIZER_CSS}
"""


# Doc bodies are untrusted (crawled/uploaded/connector content) and published
# sites are served same-origin at /sites — sanitize the rendered HTML.
_ALLOWED_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "ul", "ol", "li",
                 "table", "thead", "tbody", "tr", "th", "td",
                 "code", "pre", "blockquote", "a", "img", "strong", "em",
                 "hr", "br"}
_ALLOWED_ATTRS = {"a": {"href", "title"}, "img": {"src", "alt", "title"},
                  "code": {"class"}, "th": {"align"}, "td": {"align"}}


def _sanitize_html(html: str) -> str:
    return nh3.clean(html, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRS)


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "page"


class SiteBuildError(Exception):
    """A site build failed; the message is safe to surface in GraphQL results."""


GENERATORS = ("mari", "docusaurus")


def build_site(site: dict, docs: list[dict], generator: str = "mari") -> str:
    """Build the static site with the chosen generator; returns the build dir.

    generator: "mari" (handcrafted static HTML, the default/original path) or
    "docusaurus" (real Docusaurus 3 build from a cached npm template).
    Raises SiteBuildError on generator failure — callers surface it, never 500.
    """
    if generator == "docusaurus":
        return build_docusaurus_site(site, docs)
    if generator not in GENERATORS:
        raise SiteBuildError(f"unknown generator '{generator}' (available: {', '.join(GENERATORS)})")
    return build_mari_site(site, docs)


def build_mari_site(site: dict, docs: list[dict]) -> str:
    """Build the static site; returns the build directory path."""
    theme = site["theme"] if isinstance(site["theme"], dict) else {}
    out = BUILDS / f"site_{site['id']}"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)

    pages = [{"slug": _slug(d["title"]), "title": d["title"], "body": d["body"] or d["snippet"],
              "source_path": d.get("source_path") or ""} for d in docs]

    # Which switches this site has on. Every one of them changes the output
    # below — nothing here is decorative.
    feat = site_features(site)

    (out / "style.css").write_text(_site_css(theme))
    if feat["customizer"]:
        (out / "customize.js").write_text(CUSTOMIZER_JS)
    if feat["search"]:
        (out / "search.js").write_text(SEARCH_JS)

    mode = theme.get("mode", "light")
    density = theme.get("density", "comfortable")
    search_html = ('<div class="mari-search-block"><input class="mari-search" type="search" '
                   'placeholder="Filter pages…" aria-label="Filter pages"></div>') if feat["search"] else ""

    def render(page: dict, active: str) -> str:
        body_html = _sanitize_html(markdown.markdown(page["body"], extensions=["tables", "fenced_code"]))
        nav = "\n".join(
            f'<a href="{p["slug"]}.html" class="{"active" if p["slug"] == active else ""}">{html_mod.escape(p["title"])}</a>'
            for p in pages)
        title_esc = html_mod.escape(page["title"])
        body_class = " ".join(c for c in (("dark" if mode == "dark" else ""),
                                          ("" if feat["sidebar"] else "no-sidebar")) if c)
        # The sidebar carries the page list, so the filter box goes with it;
        # with the sidebar off it filters the page list rendered in main.
        aside = f"<aside>{search_html}{nav}</aside>" if feat["sidebar"] else ""
        main_nav = "" if feat["sidebar"] else f'<div class="mari-pages">{search_html}{nav}</div>'
        source = (f'<p class="mari-source">Source: {html_mod.escape(page["source_path"])}</p>'
                  if feat["source_path"] and page.get("source_path") else "")
        footer = (f"<footer>Published with Mari Cloud · {html_mod.escape(site['domain'])} · "
                  "every fact on this page traces to a verified source</footer>") if feat["provenance"] else ""
        scripts = ('<script src="search.js"></script>' if feat["search"] else "") + \
                  ('<script src="customize.js"></script>' if feat["customizer"] else "")
        site_id_js = f"<script>window.__MARI_SITE_ID__ = {site['id']};</script>" if feat["customizer"] else ""
        return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title_esc} · {site['name']}</title>
<link rel="stylesheet" href="{FONTS}"><link rel="stylesheet" href="style.css">
{site_id_js}</head>
<body class="{body_class}" data-density="{density}">
<div class="wrap">
<header><span class="logo">✳ {html_mod.escape(site['name'].upper())}</span>
<nav><a href="index.html">Guides</a><a href="{pages[0]['slug'] if pages else 'index'}.html">API reference</a><a href="#">Changelog</a></nav></header>
{aside}
<main>{main_nav}<h1>{title_esc}</h1>{source}{body_html}</main>
{footer}
</div>{scripts}</body></html>"""

    for p in pages:
        # strip the duplicate leading h1 (we render the title ourselves)
        p2 = dict(p)
        p2["body"] = re.sub(r"^#\s+.*\n", "", p["body"], count=1)
        (out / f"{p['slug']}.html").write_text(render(p2, p["slug"]))
    if pages:
        index = dict(pages[0])
        index["body"] = re.sub(r"^#\s+.*\n", "", index["body"], count=1)
        (out / "index.html").write_text(render(index, pages[0]["slug"]))
    else:
        (out / "index.html").write_text(render({"slug": "index", "title": site["name"], "body": "No documents matched this site's sources yet."}, "index"))
    return str(out)


# ——— Docusaurus generator (DESIGN: deploy different types of doc sites) ———

DOCUSAURUS_VERSION = "3.10.2"
TEMPLATE_DIR = BUILDS / "_docusaurus-template"
BUILD_TIMEOUT_S = 300

_TEMPLATE_PACKAGE_JSON = {
    "name": "mari-docusaurus-template",
    "version": "1.0.0",
    "private": True,
    "scripts": {"build": "docusaurus build"},
    "dependencies": {
        "@docusaurus/core": DOCUSAURUS_VERSION,
        "@docusaurus/preset-classic": DOCUSAURUS_VERSION,
        "@mdx-js/react": "^3.0.0",
        "clsx": "^2.0.0",
        "prism-react-renderer": "^2.3.0",
        "react": "^18.0.0",
        "react-dom": "^18.0.0",
    },
    "engines": {"node": ">=18.0"},
}

_TEMPLATE_BABEL = ("module.exports = { presets: "
                   "[require.resolve('@docusaurus/core/lib/babel/preset')] };\n")

_TEMPLATE_SIDEBARS = "module.exports = { docs: [{ type: 'autogenerated', dirName: '.' }] };\n"


def ensure_docusaurus_template() -> pathlib.Path:
    """Create the cached Docusaurus template project once (package.json pinned
    to 3.x + one-time `npm install`). Subsequent builds reuse node_modules."""
    TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    pkg = TEMPLATE_DIR / "package.json"
    if not pkg.exists():
        pkg.write_text(json.dumps(_TEMPLATE_PACKAGE_JSON, indent=2))
    (TEMPLATE_DIR / "babel.config.js").write_text(_TEMPLATE_BABEL)
    if not (TEMPLATE_DIR / "node_modules" / "@docusaurus" / "core").exists():
        if not shutil.which("npm"):
            raise SiteBuildError("npm not found on PATH — Docusaurus builds need node/npm")
        r = subprocess.run(["npm", "install", "--no-audit", "--no-fund"],
                           cwd=TEMPLATE_DIR, capture_output=True, text=True, timeout=900)
        if r.returncode != 0:
            raise SiteBuildError(f"npm install failed for Docusaurus template: {(r.stderr or r.stdout)[-800:]}")
    return TEMPLATE_DIR


_FENCE_RE = re.compile(r"^(```|~~~)")


def _mdx_sanitize(text: str) -> str:
    """Make plain markdown safe for Docusaurus's MDX parser: escape `<` and `{`
    in prose (outside code fences and inline code spans) so JSX/expression
    parsing never chokes on ordinary text like <id> or {placeholder}."""
    out, in_fence = [], False
    for line in text.split("\n"):
        if _FENCE_RE.match(line.strip()):
            in_fence = not in_fence
            out.append(line)
            continue
        if in_fence:
            out.append(line)
            continue
        # split on inline code spans; escape only the prose (even) segments
        parts = re.split(r"(`+[^`]*`+)", line)
        for i, part in enumerate(parts):
            if i % 2 == 0:
                parts[i] = part.replace("{", "\\{").replace("<", "\\<")
        out.append("".join(parts))
    return "\n".join(out)


def _write_docusaurus_project(work: pathlib.Path, site: dict, docs: list[dict]) -> None:
    theme = site["theme"] if isinstance(site["theme"], dict) else {}
    # Same accent resolution as the mari generator: the site's own, else the
    # preset's, so switching generators does not silently change the colour.
    presets = theme_presets()
    preset = presets.get(theme.get("theme", "Mari Editorial")) or {}
    accent = theme.get("accent") or preset.get("accent") or "#b04e2c"
    mode = theme.get("mode", "light")
    base_url = f"/sites/site_{site['id']}/"

    shutil.copy2(TEMPLATE_DIR / "package.json", work / "package.json")
    shutil.copy2(TEMPLATE_DIR / "babel.config.js", work / "babel.config.js")
    nm = work / "node_modules"
    if not nm.exists():
        nm.symlink_to(TEMPLATE_DIR / "node_modules")

    config = {
        "title": site["name"],
        "tagline": f"Published with Mari Cloud · {site['domain']}",
        "url": "http://localhost:8000",
        "baseUrl": base_url,
        "onBrokenLinks": "warn",
        "onBrokenMarkdownLinks": "warn",
        "onBrokenAnchors": "warn",
        "trailingSlash": True,
        "presets": [["classic", {
            "docs": {"routeBasePath": "/", "sidebarPath": "./sidebars.js"},
            "blog": False,
            "pages": False,
            "theme": {"customCss": "./src/css/custom.css"},
        }]],
        "themeConfig": {
            "navbar": {"title": site["name"],
                       "items": [{"to": "/", "label": "Docs", "position": "left"}]},
            "colorMode": {"defaultMode": mode if mode in ("light", "dark") else "light"},
            "footer": {"style": "dark",
                       "copyright": f"Published with Mari Cloud · {site['domain']} · "
                                    "every fact on this page traces to a verified source"},
        },
        "markdown": {"format": "md"},
    }
    (work / "docusaurus.config.js").write_text("module.exports = " + json.dumps(config, indent=2) + ";\n")
    (work / "sidebars.js").write_text(_TEMPLATE_SIDEBARS)

    css_dir = work / "src" / "css"
    css_dir.mkdir(parents=True, exist_ok=True)
    (css_dir / "custom.css").write_text(
        f":root {{ --ifm-color-primary: {accent}; }}\n"
        f"[data-theme='dark'] {{ --ifm-color-primary: {accent}; }}\n")
    (work / "static").mkdir(exist_ok=True)

    docs_dir = work / "docs"
    shutil.rmtree(docs_dir, ignore_errors=True)
    docs_dir.mkdir()
    pages = docs or [{"title": site["name"], "body": "No documents matched this site's sources yet.", "snippet": ""}]
    seen: set[str] = set()
    for pos, d in enumerate(pages, start=1):
        slug = _slug(d["title"])
        if slug in seen:
            slug = f"{slug}-{pos}"
        seen.add(slug)
        body = _mdx_sanitize(d.get("body") or d.get("snippet") or "")
        body = re.sub(r"^#\s+.*\n", "", body, count=1)  # title rendered from frontmatter
        title = str(d["title"]).replace('"', "'")
        fm = [f'id: {slug}', f'title: "{title}"', f"sidebar_position: {pos}"]
        if pos == 1:
            fm.append("slug: /")
        (docs_dir / f"{slug}.md").write_text("---\n" + "\n".join(fm) + "\n---\n\n" + body)


def build_docusaurus_site(site: dict, docs: list[dict]) -> str:
    """Build the site with Docusaurus 3. Copies the cached template (symlinked
    node_modules), writes docs/*.md + config, runs `docusaurus build`, then
    publishes build/ output to BUILDS/site_<id>. Raises SiteBuildError on failure."""
    ensure_docusaurus_template()
    work = BUILDS / f"_work_site_{site['id']}"
    work.mkdir(parents=True, exist_ok=True)
    _write_docusaurus_project(work, site, docs)

    t0 = time.time()
    try:
        r = subprocess.run(["npm", "run", "build"], cwd=work,
                           capture_output=True, text=True, timeout=BUILD_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise SiteBuildError(f"docusaurus build timed out after {BUILD_TIMEOUT_S}s")
    if r.returncode != 0:
        tail = (r.stderr or "").strip()[-1200:] or (r.stdout or "").strip()[-1200:]
        raise SiteBuildError(f"docusaurus build failed: {tail}")

    built = work / "build"
    if not (built / "index.html").exists():
        raise SiteBuildError("docusaurus build produced no index.html")
    out = BUILDS / f"site_{site['id']}"
    shutil.rmtree(out, ignore_errors=True)
    shutil.copytree(built, out)
    (out / ".mari-build.json").write_text(json.dumps(
        {"generator": "docusaurus", "docusaurus": DOCUSAURUS_VERSION,
         "seconds": round(time.time() - t0, 1), "pages": len(docs)}))
    return str(out)


def deploy_to_s3(build_dir: str, deploy_cfg: dict) -> tuple[bool, str]:
    """Upload the build to S3 if configured. Returns (uploaded, detail)."""
    bucket = deploy_cfg.get("bucket") or os.environ.get("MARI_S3_BUCKET", "")
    if not bucket:
        return False, "local build (no S3 bucket configured)"
    try:
        import boto3
        s3 = boto3.client("s3", region_name=deploy_cfg.get("region") or None)
        root = pathlib.Path(build_dir)
        n = 0
        for f in root.rglob("*"):
            if f.is_file():
                ctype = {"html": "text/html", "css": "text/css", "js": "application/javascript"}.get(
                    f.suffix.lstrip("."), "application/octet-stream")
                s3.upload_file(str(f), bucket, f.relative_to(root).as_posix(),
                               ExtraArgs={"ContentType": ctype})
                n += 1
        return True, f"uploaded {n} files to s3://{bucket}"
    except Exception as e:  # credentials missing, bucket denied, etc. — stay honest
        return False, f"local build (S3 upload failed: {type(e).__name__})"


