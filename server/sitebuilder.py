"""Mari Cloud — static doc-site builder (DESIGN.md §16).

Builds a real static website from documents in Postgres, themed by the site's
config, with an injected customizer widget. Output: server/builds/site_<id>/.
Preview served by FastAPI at /sites/site_<id>/. Deploy uploads the build to S3
when credentials are configured; otherwise the release is an honest local build.
"""

from __future__ import annotations

import base64
import html as html_mod
import json
import os
import pathlib
import posixpath
import re
import shutil
import subprocess
import time

import markdown
import nh3

BUILDS = pathlib.Path(os.environ.get("MARI_BUILDS_DIR", pathlib.Path(__file__).parent / "builds"))

FONTS = ("https://fonts.googleapis.com/css2?family=Playfair+Display:wght@500;600&family=Lora:ital@0;1"
         "&family=Source+Sans+3:wght@400;600&family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap")

# The mari.guru node-graph "M" mark — identical file (byte for byte, confirmed
# by fetching both) at mari.guru/assets/mari-mark.svg and mari.guru/docs's own
# favicon, so it's the one shared brand mark, not a theme-specific asset.
# Used two ways below: MARK_SVG is inlined next to the site name with fixed
# colors swapped for currentColor/var(--accent) so it follows the active
# theme; FAVICON_SVG keeps the original hardcoded ink-on-white and is only
# ever base64'd into a <link rel="icon"> data URI, so it reads the same in
# every browser tab regardless of which site theme is active.
MARK_SVG = """<svg viewBox="0 0 32 32" width="22" height="22" aria-hidden="true" class="mari-mark">
<rect x="1" y="1" width="30" height="30" fill="none" stroke="currentColor" stroke-width="2"/>
<path d="M7 25V9l9 9 9-9v16" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linejoin="miter"/>
<rect x="5" y="7" width="4" height="4" fill="currentColor"/><rect x="23" y="7" width="4" height="4" fill="currentColor"/>
<rect x="5" y="23" width="4" height="4" fill="currentColor"/><rect x="23" y="23" width="4" height="4" fill="currentColor"/>
<rect x="14" y="16" width="4" height="4" fill="var(--accent)"/></svg>"""

FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="32" height="32">
<rect x="1" y="1" width="30" height="30" fill="#FFFFFF" stroke="#10263B" stroke-width="2"/>
<path d="M7 25V9l9 9 9-9v16" fill="none" stroke="#10263B" stroke-width="2.4" stroke-linejoin="miter"/>
<rect x="5" y="7" width="4" height="4" fill="#10263B"/><rect x="23" y="7" width="4" height="4" fill="#10263B"/>
<rect x="5" y="23" width="4" height="4" fill="#10263B"/><rect x="23" y="23" width="4" height="4" fill="#10263B"/>
<rect x="14" y="16" width="4" height="4" fill="#1C3F60"/></svg>"""
FAVICON_HREF = "data:image/svg+xml;base64," + base64.b64encode(FAVICON_SVG.encode()).decode()

# Night-mode toggle icon: a sun in light mode (what's active), a moon in dark
# mode — swapped by NIGHTMODE_JS. Lucide-style line icons (stroke, not fill)
# so no icon library needs to ship with a static build.
SUN_ICON = ('<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/>'
            '<path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41'
            'M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/></svg>')
MOON_ICON = ('<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
             'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
             '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79Z"/></svg>')

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
    # mari.guru/docs's own "Brutalist Blueprint" skin — see the matching seed
    # row in init.sql for where these values come from. Its dark mode is its
    # own named "navy" palette (mari-cli/theme/mari.css's `html.navy`), not
    # Starlight's black — hence the "dark" override other presets don't have.
    "Mari Blueprint": {"accent": "#1e6fa8", "bg": "#ffffff", "card": "#f7f8fa", "ink": "#10263b", "line": "#d4d5d8",
                       "display": "'Inter', ui-sans-serif, system-ui, sans-serif",
                       "serif": "'Inter', ui-sans-serif, system-ui, sans-serif",
                       "dark": {"bg": "#0e2032", "card": "#0a1926", "ink": "#eaf0f5", "line": "#2d4356"}},
}

# The switches this generator honours, and what they do when nothing overrides
# them. site_feature_defs carries the same keys with the labels the console
# shows; this dict is the fallback for a build with no database, and the list
# of keys is what makes each toggle on the Publish page mean something.
FEATURE_DEFAULTS = {"sidebar": True, "search": True, "customizer": True,
                    "provenance": True, "source_path": False}


# ——— theme values are attacker-reachable; nothing here reaches a template raw ———
#
# sites.theme is written by updateSiteTheme (a user-supplied JSON blob) and by
# aiCustomizeSite (LLM output). Built sites are served from /sites on the same
# origin as /graphql, so a value that escapes a CSS declaration or an HTML
# attribute here reaches the console session cookie (AUTH-12). mutations_publish
# validates on the way in; these validate on the way out, because the row may
# predate that check, may come from a preset row an operator edited, and because
# a generator should not depend on its callers to be safe.

DENSITIES = {"comfortable", "compact", "dense"}
MODES = {"light", "dark"}
# Mari's own brand, and the fallback for any site that has not chosen. It is
# deliberately NOT "Mari Editorial": that preset is rust on cream, which Mari
# Cloud does not use. Editorial stays available for customers who want it, but
# nothing Mari publishes should land on it by omission.
DEFAULT_PRESET = "Mari Blueprint"
DEFAULT_ACCENT = "#1e6fa8"

# #rgb / #rgba / #rrggbb / #rrggbbaa, the functional forms with numeric-only
# arguments, and bare CSS colour keywords. No url(), no var(), no ; or }.
_COLOR_RE = re.compile(
    r"^(?:#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})"
    r"|(?:rgb|rgba|hsl|hsla)\(\s*[0-9.,%\s/deg-]+\)"
    r"|[a-zA-Z]{3,20})$")

# A font stack: names, quotes, commas, spaces. Nothing that can end a
# declaration or open a url().
_FONT_RE = re.compile(r"^[-\w \t,'\"]{1,120}$")


def css_color(value, fallback: str = DEFAULT_ACCENT) -> str:
    """A colour safe to interpolate into a CSS declaration, or the fallback."""
    text = str(value or "").strip()
    return text if _COLOR_RE.match(text) else fallback


def css_font(value, fallback: str) -> str:
    """A font-family stack safe to interpolate, or the fallback."""
    text = str(value or "").strip()
    return text if _FONT_RE.match(text) else fallback


def _token(value, allowed: set[str], fallback: str) -> str:
    text = str(value or "").strip()
    return text if text in allowed else fallback


def _safe_preset(preset: dict) -> dict:
    """A preset with every interpolated value validated. Preset rows live in
    site_theme_presets, which an operator edits — same treatment."""
    base = THEME_PRESETS[DEFAULT_PRESET]
    out = {k: css_color(preset.get(k), base[k]) for k in ("accent", "bg", "card", "ink", "line")}
    out["display"] = css_font(preset.get("display"), base["display"])
    out["serif"] = css_font(preset.get("serif"), base["serif"])
    return out


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
    out = {}
    for r in rows:
        preset = {"accent": r["accent"], "bg": r["bg"], "card": r["card"], "ink": r["ink"],
                  "line": r["line"], "display": r["display_font"], "serif": r["serif_font"]}
        # A preset's own dark-mode colours, if it has one (site_theme_presets.
        # dark_*) — bg and ink are the two that actually distinguish a real
        # palette from an unset one, so both must be present to opt in.
        if r.get("dark_bg") and r.get("dark_ink"):
            preset["dark"] = {"bg": r["dark_bg"], "card": r.get("dark_card") or r["dark_bg"],
                              "ink": r["dark_ink"], "line": r.get("dark_line") or r["dark_ink"]}
        out[r["key"]] = preset
    return out


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
  accent.value = readVar('--accent') || '#1e6fa8';
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

NIGHTMODE_JS = """
(function () {
  var KEY = 'mari-mode';
  var btn = document.querySelector('.mari-nightmode');
  if (!btn) return;
  // Icon markup lives here too (not just server-rendered), so the toggle
  // still works correctly if a cached page's initial icon predates this file.
  var SUN = '""" + SUN_ICON + """';
  var MOON = '""" + MOON_ICON + """';
  function apply(mode) {
    document.body.classList.toggle('dark', mode === 'dark');
    btn.innerHTML = mode === 'dark' ? MOON : SUN;
    btn.setAttribute('aria-pressed', mode === 'dark' ? 'true' : 'false');
    btn.setAttribute('aria-label', mode === 'dark' ? 'Switch to day mode' : 'Switch to night mode');
  }
  var saved = null;
  try { saved = localStorage.getItem(KEY); } catch (e) { /* private mode etc. */ }
  // A visitor's own choice (once made) overrides the site's built-in default
  // mode; until then the page shows whatever the site owner built it with.
  apply(saved === 'dark' || saved === 'light' ? saved : (document.body.classList.contains('dark') ? 'dark' : 'light'));
  btn.addEventListener('click', function () {
    var next = document.body.classList.contains('dark') ? 'light' : 'dark';
    apply(next);
    try { localStorage.setItem(KEY, next); } catch (e) { /* private mode etc. */ }
  });
})();
"""

SEARCH_JS = """
(function () {
  var box = document.querySelector('.mari-search');
  if (!box) return;
  // Real search over every page's text (search-index.json, written at build
  // time next to this file), not just the nav's own link labels — the old
  // version filtered the sidebar by page TITLE only, so it found nothing a
  // page's body talked about unless the title happened to say it too.
  var block = box.closest('.mari-search-block') || box.parentNode;
  var results = document.createElement('div');
  results.className = 'mari-search-results';
  block.appendChild(results);
  var base = location.pathname.replace(/[^/]*$/, '');
  var index = null;
  function ensureIndex() {
    if (index) return Promise.resolve(index);
    return fetch(base + 'search-index.json').then(function (r) { return r.json(); })
      .then(function (j) { index = j; return index; })
      .catch(function () { index = []; return index; });
  }
  function esc(s) {
    var d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }
  function snippet(text, q) {
    var i = text.toLowerCase().indexOf(q);
    if (i < 0) return esc(text.slice(0, 120));
    var start = Math.max(0, i - 40);
    var pre = (start > 0 ? '\\u2026' : '') + text.slice(start, i);
    var match = text.slice(i, i + q.length);
    var post = text.slice(i + q.length, i + q.length + 80) + '\\u2026';
    return esc(pre) + '<mark>' + esc(match) + '</mark>' + esc(post);
  }
  function render(qs) {
    var q = qs.toLowerCase();
    var hits = index.filter(function (p) {
      return p.title.toLowerCase().indexOf(q) !== -1 || p.text.toLowerCase().indexOf(q) !== -1;
    }).sort(function (a, b) {
      var at = a.title.toLowerCase().indexOf(q) === -1 ? 1 : 0;
      var bt = b.title.toLowerCase().indexOf(q) === -1 ? 1 : 0;
      return at - bt;
    }).slice(0, 8);
    if (!hits.length) {
      results.innerHTML = '<div class="msr-empty">No pages match \\u201c' + esc(qs) + '\\u201d</div>';
    } else {
      results.innerHTML = hits.map(function (p) {
        return '<a href="' + p.slug + '.html"><span class="msr-title">' + esc(p.title) + '</span>' +
          '<span class="msr-snippet">' + snippet(p.text, q) + '</span></a>';
      }).join('');
    }
    results.classList.add('open');
  }
  box.addEventListener('input', function () {
    var qs = box.value.trim();
    if (!qs) { results.classList.remove('open'); results.innerHTML = ''; return; }
    ensureIndex().then(function () { render(qs); });
  });
  document.addEventListener('click', function (e) {
    if (e.target !== box && !results.contains(e.target)) results.classList.remove('open');
  });
})();
"""

CUSTOMIZER_CSS = """
#mari-customize-btn { position: fixed; right: 18px; bottom: 18px; z-index: 999;
  background: var(--accent); color: #fff; border: none; border-radius: 999px;
  padding: 10px 16px; font: 600 13px var(--serif); cursor: pointer;
  box-shadow: 2px 3px 0 rgba(0,0,0,0.18); }
#mari-customize-panel { position: fixed; right: 18px; bottom: 66px; z-index: 999;
  width: 250px; background: var(--card); color: var(--ink); border: 1.5px solid var(--line);
  border-radius: 12px 14px 11px 13px; padding: 14px; display: none;
  box-shadow: 3px 4px 0 rgba(0,0,0,0.12); font: 13px var(--serif); }
#mari-customize-panel.open { display: block; }
#mari-customize-panel h3 { margin: 0 0 10px; font: 600 15px var(--display); }
#mari-customize-panel label { display: flex; justify-content: space-between; align-items: center;
  gap: 8px; margin: 8px 0; }
#mari-customize-panel .mc-row { justify-content: flex-start; }
#mari-customize-panel .mc-actions { display: flex; gap: 8px; align-items: center; margin-top: 10px; }
#mari-customize-panel .mc-actions button { background: var(--accent); color: #fff; border: none;
  border-radius: 8px; padding: 6px 12px; font: 600 12.5px var(--serif); cursor: pointer; }
#mari-customize-panel .mc-note { color: color-mix(in srgb, var(--ink) 55%, transparent);
  font-size: 11px; margin: 8px 0 0; }
#mc-status { font-size: 11.5px; }
"""


def _site_css(theme: dict) -> str:
    presets = theme_presets()
    fallback = presets.get(DEFAULT_PRESET) or next(iter(presets.values()))
    preset_raw = presets.get(theme.get("theme", DEFAULT_PRESET), fallback)
    preset = _safe_preset(preset_raw)
    # A site that has not picked an accent ships its preset's own — which is
    # the swatch the Publish page shows for that preset.
    accent = css_color(theme.get("accent") or preset["accent"], preset["accent"])
    try:
        radius = min(max(int(theme.get("radius", 10)), 0), 64)
    except (TypeError, ValueError):
        radius = 10
    density = _token(theme.get("density"), DENSITIES, "comfortable")
    pad = {"comfortable": 28, "compact": 20, "dense": 14}[density]
    starlight = _safe_preset(presets.get("Starlight", THEME_PRESETS["Starlight"]))
    # Most presets have no opinion about dark mode, so they all share
    # Starlight's — but a preset that DOES ship its own (Mari Blueprint's navy,
    # matching mari-cli/theme/mari.css's `html.navy`) uses that instead of
    # being forced into Starlight's black.
    own_dark = preset_raw.get("dark") if isinstance(preset_raw.get("dark"), dict) else None
    dark = ({k: css_color(own_dark.get(k), starlight[k]) for k in ("bg", "card", "ink", "line")}
            if own_dark else starlight)
    return f"""
:root {{ --accent: {accent}; --radius: {radius}px; --bg: {preset['bg']}; --card: {preset['card']};
  --ink: {preset['ink']}; --line: {preset['line']}; --display: {preset['display']}; --serif: {preset['serif']};
  --mono: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace; }}
body.dark {{ --bg: {dark['bg']}; --card: {dark['card']}; --ink: {dark['ink']}; --line: {dark['line']}; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--ink); font-family: var(--serif); line-height: 1.6; }}
.wrap {{ display: grid; grid-template-columns: 250px 1fr; min-height: 100vh; }}
header {{ grid-column: 1 / -1; display: flex; align-items: center; gap: 18px;
  padding: 14px {pad}px; border-bottom: 1.5px solid var(--line); background: var(--card); }}
header .logo {{ display: inline-flex; align-items: center; gap: 8px; font: 600 17px var(--display); letter-spacing: 0.06em; }}
.mari-mark {{ color: var(--ink); flex: none; }}
header nav {{ margin-left: auto; display: flex; align-items: center; gap: 16px; font: 13.5px var(--serif); }}
header nav a {{ color: inherit; text-decoration: none; opacity: 0.75; }}
header nav a:hover {{ opacity: 1; color: var(--accent); }}
.mari-nightmode {{ display: inline-flex; align-items: center; justify-content: center;
  width: 28px; height: 28px; border: 1.5px solid var(--line); background: var(--card); color: inherit;
  border-radius: var(--radius); cursor: pointer; }}
.mari-nightmode:hover {{ border-color: var(--accent); color: var(--accent); }}
.mari-nightmode svg {{ display: block; }}
aside {{ border-right: 1.5px solid var(--line); padding: {pad}px 18px; background: var(--card); }}
aside a {{ display: block; padding: 6px 10px; border-radius: var(--radius); color: inherit;
  text-decoration: none; font: 14px var(--serif); opacity: 0.8; }}
aside a:hover {{ background: color-mix(in srgb, var(--accent) 9%, transparent); opacity: 1; }}
aside a.active {{ background: color-mix(in srgb, var(--accent) 14%, transparent); color: var(--accent);
  font-weight: 600; opacity: 1; }}
.mari-nav-section {{ margin: 16px 0 4px; padding: 0 10px; font: 600 11px var(--mono);
  text-transform: uppercase; letter-spacing: 0.08em; opacity: 0.55; }}
.mari-nav-section:first-child {{ margin-top: 0; }}
main {{ padding: {pad + 8}px {pad + 14}px; max-width: 760px; }}
main h1 {{ font: 600 34px var(--display); margin: 0 0 14px; }}
main h2 {{ font: 500 23px var(--display); margin: 28px 0 8px; border-bottom: 1px solid var(--line); padding-bottom: 6px; }}
main h3 {{ font: 600 17px var(--display); margin: 20px 0 6px; }}
main a {{ color: var(--accent); }}
main code {{ background: color-mix(in srgb, var(--ink) 8%, transparent); padding: 1.5px 6px;
  border-radius: 6px; font-size: 0.9em; font-family: var(--mono); }}
main pre {{ background: #26231c; color: #f3ecd9; padding: 16px 18px; border-radius: var(--radius);
  overflow-x: auto; }}
main pre code {{ background: none; padding: 0; color: inherit; font-family: var(--mono); }}
main table {{ border-collapse: collapse; width: 100%; font-size: 14.5px; }}
main th, main td {{ border: 1px solid var(--line); padding: 8px 12px; text-align: left; }}
main th {{ font-family: var(--serif); background: color-mix(in srgb, var(--ink) 4%, transparent); }}
footer {{ grid-column: 1 / -1; padding: 18px {pad}px; border-top: 1.5px solid var(--line);
  font: 12px var(--serif); opacity: 0.65; }}
/* feature switches (site_feature_defs) */
body.no-sidebar .wrap {{ grid-template-columns: 1fr; }}
.mari-search {{ width: 100%; margin-bottom: 10px; padding: 6px 10px; border-radius: var(--radius);
  border: 1.5px solid var(--line); background: var(--bg); color: inherit;
  font: 13.5px var(--serif); }}
.mari-search-block {{ position: relative; margin-bottom: 18px; max-width: 320px; }}
.mari-editions {{ display: flex; margin-bottom: 10px; max-width: 320px;
  border: 1.5px solid var(--line); border-radius: var(--radius); overflow: hidden; }}
.mari-edition {{ flex: 1 1 0; padding: 7px 10px; text-align: center; color: var(--ink);
  opacity: 0.65; text-decoration: none; white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis; font: 600 12px var(--serif);
  transition: background-color 90ms linear, color 90ms linear, opacity 90ms linear; }}
.mari-edition + .mari-edition {{ border-left: 1.5px solid var(--line); }}
.mari-edition:hover {{ background: var(--card); opacity: 1; }}
.mari-edition[aria-current="page"] {{ background: var(--accent); color: var(--bg); opacity: 1; }}
.mari-edition[aria-current="page"]:hover {{ background: var(--accent); color: var(--bg); }}
.mari-search-results {{ display: none; position: absolute; z-index: 20; top: calc(100% + 4px); left: 0; right: 0;
  max-height: 340px; overflow-y: auto; background: var(--card); border: 1.5px solid var(--line);
  border-radius: var(--radius); box-shadow: 0 8px 24px -8px color-mix(in srgb, var(--ink) 25%, transparent); }}
.mari-search-results.open {{ display: block; }}
.mari-search-results a {{ display: block; padding: 8px 12px; text-decoration: none; color: inherit;
  border-bottom: 1px solid var(--line); }}
.mari-search-results a:last-child {{ border-bottom: none; }}
.mari-search-results a:hover, .mari-search-results a.active {{
  background: color-mix(in srgb, var(--accent) 9%, transparent); }}
.mari-search-results .msr-title {{ font: 600 13.5px var(--serif); }}
.mari-search-results .msr-snippet {{ display: block; margin-top: 2px; font: 12px var(--serif); opacity: 0.7; }}
.mari-search-results .msr-snippet mark {{ background: color-mix(in srgb, var(--accent) 30%, transparent);
  color: inherit; border-radius: 3px; }}
.mari-search-results .msr-empty {{ padding: 10px 12px; font: 13px var(--serif); opacity: 0.65; }}
.mari-source {{ margin: -6px 0 18px; font: 12px var(--serif); opacity: 0.6; }}
{CUSTOMIZER_CSS}
{_blueprint_css() if str(theme.get("theme", DEFAULT_PRESET)) == "Mari Blueprint" else ""}
"""


def _blueprint_css() -> str:
    """The one flourish the generic accent/bg/card/ink/line preset shape can't
    express: mari.guru/docs's real theme (mari-cli/theme/mari.css) puts a hard
    3px block shadow under code blocks and pins code/table/blockquote corners
    to near-zero regardless of the site's own radius slider — brutalist
    details, not just a different palette. Scoped to the 'Mari Blueprint'
    preset only; every other preset keeps following the radius slider as-is."""
    return """
main pre { border: 1px solid var(--line); border-radius: 2px; box-shadow: 3px 3px 0 color-mix(in srgb, var(--ink) 16%, transparent); }
main code { border: 1px solid var(--line); border-radius: 2px; }
main pre code { border: none; border-radius: 0; }
main blockquote { border-inline-start: 3px solid var(--accent); border-radius: 0; padding-left: 14px; margin-left: 0; }
main table, main th, main td { border-radius: 2px; }
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


_TAG_RE = re.compile(r"<[^>]+>")
_MAX_INDEX_CHARS = 4000


def _html_to_text(html: str) -> str:
    """Sanitized page HTML -> plain text for the search index. Same content
    the page renders, just without markup — no separate extraction pass to
    keep in sync with what a reader (or the detector) actually sees."""
    return " ".join(html_mod.unescape(_TAG_RE.sub(" ", html)).split())[:_MAX_INDEX_CHARS]


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "page"


def _slugify_pages(docs: list[dict]) -> list[dict]:
    """One slug per document, deduped when two titles collide — both
    generators publish one file per document with no subfolders, so two docs
    slugging to the same name would otherwise silently overwrite each other
    (the same failure mode as onboard.py's basename collision, one stage
    later: at build time instead of ingest time)."""
    seen: set[str] = set()
    pages = []
    for pos, d in enumerate(docs, start=1):
        slug = _slug(d["title"])
        if slug in seen:
            slug = f"{slug}-{pos}"
        seen.add(slug)
        pages.append({"slug": slug, "title": d["title"], "body": d["body"] or d.get("snippet") or "",
                      "source_path": d.get("source_path") or "", "id": d.get("id")})
    return pages


def _apply_nav(pages: list[dict], nav) -> list[dict]:
    """Reorder/group `pages` by the site's curated nav, if it has one.

    `nav` is `[{"label": str | None, "docs": [document id, ...]}, ...]` —
    sections in the order they should render, each listing its documents in
    the order they should render. It's a curation layer ON TOP of which
    documents got published (that's still the site's tag scope): a document
    the nav doesn't mention isn't dropped, it lands in a trailing unlabeled
    group, so a page always has SOME way to reach it even if nav curation
    hasn't caught up with what's tagged. Sets `section` on each page dict —
    None for an unlabeled group, which the caller renders with no header.
    Falls back to the original (id) order, all in one unlabeled group, if nav
    is empty or malformed — the shape every caller had before nav existed."""
    sections = nav if isinstance(nav, list) else []
    by_id = {p["id"]: p for p in pages if p.get("id") is not None}
    placed: set[int] = set()
    ordered: list[dict] = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        label = section.get("label")
        label = str(label).strip() if label else None
        for doc_id in section.get("docs") or []:
            page = by_id.get(doc_id)
            if page is None or doc_id in placed:
                continue
            placed.add(doc_id)
            ordered.append(dict(page, section=label))
    for p in pages:
        if p.get("id") not in placed:
            ordered.append(dict(p, section=None))
    return ordered


# Relative paths and https only. An edition href is rendered into every page of
# a published site, and those sites are served from the same origin as /graphql,
# so a `javascript:` or `data:` URL here would be a stored XSS with a session
# cookie behind it (AUTH-12). Scheme-relative `//host` is refused too: it is not
# obviously a different origin to a reviewer reading the config.
_EDITION_HREF_RE = re.compile(
    r"^(?:"
    r"https://[\w.-]+(?::\d+)?(?:/[^\s\"'<>]*)?"   # absolute, https only
    r"|/(?!/)[^\s\"'<>]*"                          # root-relative, but not //host
    r"|[\w.-]+\.html(?:#[\w-]+)?"                  # a sibling page
    r")$")


def _editions_html(site: dict) -> str:
    """The edition toggle, rendered above the sidebar search.

    Two or more sibling doc sets, one marked current. One entry is not a
    choice, so it renders nothing rather than a toggle that cannot toggle.
    """
    raw = site.get("editions") or []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return ""
    if not isinstance(raw, list) or len(raw) < 2:
        return ""

    items = []
    for e in raw:
        if not isinstance(e, dict):
            continue
        label = str(e.get("label") or "").strip()
        href = str(e.get("href") or "").strip()
        if not label or len(label) > 40 or not _EDITION_HREF_RE.match(href):
            continue
        current = bool(e.get("current"))
        attrs = ' aria-current="page"' if current else ""
        items.append(
            f'<a class="mari-edition" href="{html_mod.escape(href, quote=True)}"{attrs}>'
            f'{html_mod.escape(label)}</a>'
        )

    if len(items) < 2:
        return ""
    return ('<nav class="mari-editions" aria-label="Documentation edition">'
            + "".join(items) + "</nav>")


def _nav_html(pages: list[dict], active: str) -> str:
    """The sidebar/page-list markup: a section header wherever `section`
    changes from the page before it (nothing for an unlabeled run), then the
    page links themselves — in the order `_apply_nav` already put `pages` in."""
    out = []
    seen_section = False
    last_section = None
    for p in pages:
        section = p.get("section")
        if not seen_section or section != last_section:
            seen_section, last_section = True, section
            if section:
                out.append(f'<div class="mari-nav-section">{html_mod.escape(section)}</div>')
        cls = "active" if p["slug"] == active else ""
        out.append(f'<a href="{p["slug"]}.html" class="{cls}">{html_mod.escape(p["title"])}</a>')
    return "\n".join(out)


_MD_LINK_RE = re.compile(r'(\]\()([^)\s]+)((?:\s+"[^"]*")?\))')


def _link_index(pages: list[dict]) -> dict[str, str]:
    """Map every path a document's own relative links might target — its
    source_path, each of that path's suffixes, and its bare basename when
    that basename is unique across the build — to the slug the build gave it.

    Every generator flattens the doc tree into one page per document with no
    subfolders, so a source link like `../reference/cli.md` (correct relative
    to the *original* file tree) no longer points anywhere in the built site.
    Without this, virtually every cross-document link in a real docs tree
    (built from nested folders, exactly what mari-cli's own docs look like)
    404s in the published site."""
    index: dict[str, str] = {}
    basename_hits: dict[str, int] = {}
    basename_slug: dict[str, str] = {}
    for p in pages:
        sp = (p["source_path"] or "").lstrip("/")
        if not sp:
            continue
        index[sp] = p["slug"]
        parts = sp.split("/")
        for i in range(1, len(parts)):
            index.setdefault("/".join(parts[i:]), p["slug"])
        base = parts[-1]
        basename_hits[base] = basename_hits.get(base, 0) + 1
        basename_slug[base] = p["slug"]
    for base, n in basename_hits.items():
        if n == 1:
            index.setdefault(base, basename_slug[base])
    return index


def _resolve_doc_link(target: str, source_path: str, index: dict[str, str]) -> tuple[str, str] | None:
    """Resolve one `](target)` from a document at source_path to (slug,
    fragment), or None if it isn't an internal doc link this build can
    place (external URL, anchor-only, or a target this build didn't
    publish — left untouched rather than guessed at)."""
    if not target or "://" in target or target.startswith(("#", "mailto:")):
        return None
    path, _, fragment = target.partition("#")
    if not path:
        return None
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if ext not in ("md", "mdx", "markdown"):
        return None
    if path.startswith("/"):
        resolved = path.lstrip("/")
    else:
        base_dir = posixpath.dirname((source_path or "").lstrip("/"))
        resolved = posixpath.normpath(posixpath.join(base_dir, path))
    slug = index.get(resolved) or index.get(posixpath.basename(resolved))
    return (slug, fragment) if slug else None


def _rewrite_doc_links(body: str, source_path: str, index: dict[str, str], href) -> str:
    """Rewrite internal `](./other.md)`-style links in `body` to the slug
    `href(slug, fragment)` builds; anything unresolved is left as-is."""
    def sub(m: re.Match) -> str:
        hit = _resolve_doc_link(m.group(2), source_path, index)
        if hit is None:
            return m.group(0)
        slug, fragment = hit
        return m.group(1) + href(slug, fragment) + m.group(3)
    return _MD_LINK_RE.sub(sub, body)


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

    pages = _slugify_pages(docs)
    nav_cfg = site.get("nav") if isinstance(site.get("nav"), list) else []
    pages = _apply_nav(pages, nav_cfg)
    link_index = _link_index(pages)

    # Which switches this site has on. Every one of them changes the output
    # below — nothing here is decorative.
    feat = site_features(site)

    (out / "style.css").write_text(_site_css(theme))
    (out / "nightmode.js").write_text(NIGHTMODE_JS)
    if feat["customizer"]:
        (out / "customize.js").write_text(CUSTOMIZER_JS)
    if feat["search"]:
        (out / "search.js").write_text(SEARCH_JS)
        # One entry per page, body text only (markup stripped) — real search
        # over what a page actually says, not just its title. Static and
        # fetched client-side, so it works the same in the FastAPI preview and
        # after an S3/CloudFront deploy: no server-side search endpoint to run.
        index_entries = [
            {"slug": p["slug"], "title": p["title"],
             "text": _html_to_text(_sanitize_html(markdown.markdown(
                 _rewrite_doc_links(p["body"], p["source_path"], link_index,
                                    lambda slug, frag: f"{slug}.html" + (f"#{frag}" if frag else "")),
                 extensions=["tables", "fenced_code"])))}
            for p in pages
        ]
        (out / "search-index.json").write_text(json.dumps(index_entries))

    # Theme values reach an HTML attribute; a token allowlist is the check, and
    # escaping on interpolation below is the belt (AUTH-12).
    mode = _token(theme.get("mode"), MODES, "light")
    density = _token(theme.get("density"), DENSITIES, "comfortable")
    name_esc = html_mod.escape(str(site["name"]))
    search_html = ('<div class="mari-search-block"><input class="mari-search" type="search" '
                   'placeholder="Search docs…" aria-label="Search docs"></div>') if feat["search"] else ""
    editions_html = _editions_html(site)

    # Header links point at real pages, computed from what actually got
    # published rather than hardcoded — "API reference" used to be
    # `pages[0]`, which meant it silently pointed at whatever document
    # happened to sort first (the site's own intro page, once nav pinned it
    # to the top) instead of anything resembling a reference. "Changelog" was
    # a bare `#` — a link that goes nowhere is worse than no link.
    first_slug = pages[0]["slug"] if pages else "index"
    guides_slug = next((p["slug"] for p in pages if p.get("section")), first_slug)
    api_ref_slug = next((p["slug"] for p in pages if p["title"] == "CLI reference"),
                        next((p["slug"] for p in pages if p.get("section") == "Reference"), first_slug))
    changelog_slug = next((p["slug"] for p in pages if p["title"] == "Changelog"), None)
    nightmode_btn = (f'<button type="button" class="mari-nightmode" aria-pressed="{"true" if mode == "dark" else "false"}" '
                     f'aria-label="Switch to {"day" if mode == "dark" else "night"} mode">'
                     f'{MOON_ICON if mode == "dark" else SUN_ICON}</button>')
    header_nav = (nightmode_btn +
                  f'<a href="{guides_slug}.html">Guides</a><a href="{api_ref_slug}.html">API reference</a>')
    if changelog_slug:
        header_nav += f'<a href="{changelog_slug}.html">Changelog</a>'

    def render(page: dict, active: str) -> str:
        body_md = _rewrite_doc_links(page["body"], page.get("source_path") or "", link_index,
                                      lambda slug, frag: f"{slug}.html" + (f"#{frag}" if frag else ""))
        body_html = _sanitize_html(markdown.markdown(body_md, extensions=["tables", "fenced_code"]))
        nav = _nav_html(pages, active)
        title_esc = html_mod.escape(page["title"])
        body_class = " ".join(c for c in (("dark" if mode == "dark" else ""),
                                          ("" if feat["sidebar"] else "no-sidebar")) if c)
        # The sidebar carries the page list, so the filter box goes with it;
        # with the sidebar off it filters the page list rendered in main.
        # Editions sit above the search box, so the reader picks which product
        # they are searching before they type into it.
        aside = (f"<aside>{editions_html}{search_html}{nav}</aside>"
                 if feat["sidebar"] else "")
        main_nav = ("" if feat["sidebar"]
                    else f'<div class="mari-pages">{editions_html}{search_html}{nav}</div>')
        source = (f'<p class="mari-source">Source: {html_mod.escape(page["source_path"])}</p>'
                  if feat["source_path"] and page.get("source_path") else "")
        footer = (f"<footer>Published with Mari Cloud · {html_mod.escape(site['domain'])} · "
                  "every fact on this page traces to a verified source</footer>") if feat["provenance"] else ""
        scripts = '<script src="nightmode.js"></script>' + \
                  ('<script src="search.js"></script>' if feat["search"] else "") + \
                  ('<script src="customize.js"></script>' if feat["customizer"] else "")
        site_id_js = (f"<script>window.__MARI_SITE_ID__ = {int(site['id'])};</script>"
                      if feat["customizer"] else "")
        return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title_esc} · {name_esc}</title>
<link rel="icon" type="image/svg+xml" href="{FAVICON_HREF}">
<link rel="stylesheet" href="{FONTS}"><link rel="stylesheet" href="style.css">
{site_id_js}</head>
<body class="{html_mod.escape(body_class, quote=True)}" data-density="{html_mod.escape(density, quote=True)}">
<div class="wrap">
<header><span class="logo">{MARK_SVG}{html_mod.escape(site['name'].upper())}</span>
<nav>{header_nav}</nav></header>
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
    preset = presets.get(theme.get("theme", DEFAULT_PRESET)) or {}
    # Same validation as the mari generator: this accent lands in a CSS
    # declaration, and sites.theme is user- and LLM-written (AUTH-12).
    accent = css_color(theme.get("accent") or preset.get("accent"), DEFAULT_ACCENT)
    mode = _token(theme.get("mode"), MODES, "light")
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
    raw_pages = docs or [{"title": site["name"], "body": "No documents matched this site's sources yet.", "snippet": ""}]
    pages = _slugify_pages(raw_pages)
    link_index = _link_index(pages)
    first_slug = pages[0]["slug"] if pages else None

    def _docusaurus_href(slug: str, fragment: str) -> str:
        path = "/" if slug == first_slug else f"/{slug}/"
        return path + (f"#{fragment}" if fragment else "")

    for pos, p in enumerate(pages, start=1):
        slug = p["slug"]
        body = _rewrite_doc_links(p["body"], p["source_path"], link_index, _docusaurus_href)
        body = _mdx_sanitize(body)
        body = re.sub(r"^#\s+.*\n", "", body, count=1)  # title rendered from frontmatter
        title = str(p["title"]).replace('"', "'")
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


def _s3_prefix(domain: str) -> str:
    """The S3 key prefix implied by a site's own domain. A deploy bucket is
    routinely shared — mari.guru's own hosts the marketing landing page at
    the root AND however many published doc sites at their own paths — so a
    site whose domain is 'mari.guru/docs' has to land under the 'docs/'
    prefix, not the bucket root, or its deploy silently overwrites (or
    --delete-removes) whatever else is up there. A bare domain with no path
    (e.g. a site that owns the whole bucket) still deploys to the root."""
    path = domain.split("/", 1)[1] if "/" in domain else ""
    return path.strip("/")


def deploy_to_s3(build_dir: str, deploy_cfg: dict, site: dict | None = None) -> tuple[bool, str]:
    """Upload the build to S3 if configured, scoped to the prefix the site's
    own domain implies, and invalidate CloudFront if a distribution is
    configured — otherwise a "successful" deploy still shows visitors the
    previous build until the CDN cache expires on its own. Returns
    (uploaded, detail)."""
    bucket = deploy_cfg.get("bucket") or os.environ.get("MARI_S3_BUCKET", "")
    if not bucket:
        return False, "local build (no S3 bucket configured)"
    prefix = _s3_prefix(str((site or {}).get("domain") or ""))
    key_prefix = f"{prefix}/" if prefix else ""
    try:
        import boto3
        s3 = boto3.client("s3", region_name=deploy_cfg.get("region") or None)
        root = pathlib.Path(build_dir)
        keys: set[str] = set()
        for f in root.rglob("*"):
            if f.is_file():
                ctype = {"html": "text/html", "css": "text/css", "js": "application/javascript",
                         "json": "application/json"}.get(f.suffix.lstrip("."), "application/octet-stream")
                key = key_prefix + f.relative_to(root).as_posix()
                s3.upload_file(str(f), bucket, key, ExtraArgs={"ContentType": ctype})
                keys.add(key)

        # Objects under this prefix from a previous build that the new one no
        # longer has — a page renamed or removed shouldn't keep serving stale
        # content forever just because nothing overwrote it (list_objects_v2
        # and delete_objects each cap at 1000 keys per call, hence paginate
        # and chunk rather than assume a small site).
        paginator = s3.get_paginator("list_objects_v2")
        stale = [o["Key"] for page in paginator.paginate(Bucket=bucket, Prefix=key_prefix)
                 for o in page.get("Contents", []) if o["Key"] not in keys]
        for i in range(0, len(stale), 1000):
            chunk = stale[i:i + 1000]
            s3.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": k} for k in chunk]})

        detail = f"uploaded {len(keys)} files to s3://{bucket}/{key_prefix}"
        if stale:
            detail += f", removed {len(stale)} stale"

        dist_id = deploy_cfg.get("distributionId") or os.environ.get("MARI_CLOUDFRONT_DISTRIBUTION_ID", "")
        if dist_id:
            cf = boto3.client("cloudfront")
            cf.create_invalidation(
                DistributionId=dist_id,
                InvalidationBatch={"Paths": {"Quantity": 1, "Items": [f"/{prefix}/*" if prefix else "/*"]},
                                   "CallerReference": f"mari-{int(time.time() * 1000)}"})
            detail += f", invalidated CloudFront ({dist_id})"
        return True, detail
    except Exception as e:  # credentials missing, bucket denied, etc. — stay honest
        # The class name alone made NoSuchBucket, AccessDenied and an expired
        # token indistinguishable, and every one of those is fixed differently
        # (ERR-2). botocore carries the actionable part in response['Error'];
        # anything else gets its message.
        detail = ""
        response = getattr(e, "response", None)
        if isinstance(response, dict):
            err = response.get("Error") or {}
            code, msg = err.get("Code", ""), err.get("Message", "")
            detail = " — ".join(p for p in (code, msg) if p)
        detail = detail or str(e) or e.__class__.__name__
        return False, f"S3 deploy to s3://{bucket}/{key_prefix} failed: {type(e).__name__}: {detail}"


