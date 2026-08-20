"""Mari — repository audit engine (DESIGN.md §20, FLOWS-DESIGN.md).

Scans connected GitHub connector sources for markdown
docs, localization variants, git authorship, and tag coverage. Findings land in
audit_findings with per-finding fix actions; re-audit any time. Clean by
design: every finding has exactly one obvious fix.
"""

from __future__ import annotations

import base64
import json
import os
import pathlib
import re
import subprocess

import config
from mari_server.infrastructure import postgres


def _conn():
    return postgres.connect()


def ensure_schema() -> None:
    with _conn() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS audit_runs (
            id serial PRIMARY KEY, provider text NOT NULL DEFAULT 'github',
            repo text NOT NULL DEFAULT '', findings int NOT NULL DEFAULT 0,
            fixed int NOT NULL DEFAULT 0, ran_at timestamptz NOT NULL DEFAULT now())""")
        conn.execute("""CREATE TABLE IF NOT EXISTS audit_findings (
            id serial PRIMARY KEY, run_id int NOT NULL REFERENCES audit_runs(id) ON DELETE CASCADE,
            kind text NOT NULL, title text NOT NULL, detail text NOT NULL DEFAULT '',
            fix_action text NOT NULL DEFAULT '', fix_payload jsonb NOT NULL DEFAULT '{}',
            status text NOT NULL DEFAULT 'open',
            UNIQUE (run_id, kind, title))""")
        # Git authors are not accounts. Scanning a repository learns that an
        # address committed to it — never that its owner is a member here — so
        # the authorship fix records the address in this table instead of
        # writing a `users` row. 'mapped' means an admin said this address is
        # an existing member; 'suggested' means the audit put it forward and
        # nobody has acted yet. Only inviteMember creates members.
        conn.execute("""CREATE TABLE IF NOT EXISTS audit_author_map (
            id serial PRIMARY KEY, email text NOT NULL UNIQUE,
            git_name text NOT NULL DEFAULT '', member_name text NOT NULL DEFAULT '',
            status text NOT NULL DEFAULT 'suggested',   -- suggested|mapped
            decided_by text NOT NULL DEFAULT '',
            decided_at timestamptz NOT NULL DEFAULT now())""")


# ——— real GitHub repository connector resolution ———

BUILDS_DIR = pathlib.Path(
    os.environ.get("MARI_REPO_AUDIT_DIR", pathlib.Path(__file__).parent / "data" / "repo-audit")
)


def _github_source() -> dict | None:
    """First connected GitHub source (lowest id) with a repo configured."""
    with _conn() as conn:
        return conn.execute(
            """SELECT id, provider, config FROM sources WHERE kind = 'connector'
                 AND split_part(provider, ':', 1) = 'github' """
            "AND coalesce(config->>'repo', '') <> '' ORDER BY id LIMIT 1").fetchone()


def _git(args: list[str], cwd: pathlib.Path | None = None, timeout: int = 120,
         token: str = "") -> str:
    """Run git, never leaking the token into exceptions or logs."""
    tok = token.strip()
    env = os.environ.copy()
    if tok:
        credential = base64.b64encode(f"x-access-token:{tok}".encode()).decode()
        # Git reads this one-shot config from the child environment. The
        # credential never appears in argv/process listings or the remote URL.
        env.update({
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.extraHeader",
            "GIT_CONFIG_VALUE_0": f"Authorization: Basic {credential}",
        })
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                       timeout=timeout, env=env)
    if r.returncode != 0:
        msg = (r.stderr or r.stdout or "").strip()
        if tok:
            msg = msg.replace(tok, "***")
        raise RuntimeError(f"git {args[0]} failed: {msg[:400]}")
    return r.stdout


def _sync_github_repo(repo_slug: str, token: str = "") -> pathlib.Path:
    """Shallow-clone (or update) owner/name into builds/audit/owner__name.

    The token is only ever passed on the command line at call time; the
    on-disk remote URL is always the plain https URL (no token persisted).
    """
    owner, _, name = repo_slug.partition("/")
    dest = BUILDS_DIR / f"{owner}__{name}"
    plain_url = f"https://github.com/{repo_slug}.git"
    if (dest / ".git").exists():
        # remote HEAD == default branch; avoids hardcoding its name
        _git(["fetch", "--depth", "50", plain_url, "HEAD"], cwd=dest, token=token)
        _git(["reset", "--hard", "FETCH_HEAD"], cwd=dest)
    else:
        BUILDS_DIR.mkdir(parents=True, exist_ok=True)
        _git(["clone", "--depth", "50", plain_url, str(dest)], timeout=300, token=token)
        _git(["remote", "set-url", "origin", plain_url], cwd=dest)
    return dest


def _clone_dir(repo_slug: str) -> pathlib.Path:
    owner, _, name = repo_slug.partition("/")
    return BUILDS_DIR / f"{owner}__{name}"


def _fix_repo_base() -> tuple[pathlib.Path, pathlib.Path]:
    """(repo root, markdown base dir) for fix actions — reuses an existing
    clone without touching the network."""
    src = _github_source()
    if src:
        cfg = src["config"] if isinstance(src["config"], dict) else json.loads(src["config"])
        dest = _clone_dir(cfg.get("repo", ""))
        if (dest / ".git").exists():
            return dest, dest
    raise RuntimeError("No connected GitHub repository clone is available.")


def _git_authors(repo: pathlib.Path) -> dict[str, str]:
    """path-less author map: email -> name from git log."""
    try:
        out = subprocess.run(["git", "log", "--format=%an|%ae"], cwd=repo,
                             capture_output=True, text=True, timeout=15).stdout
        authors = {}
        for line in out.splitlines():
            if "|" in line:
                name, email = line.split("|", 1)
                authors[email.strip()] = name.strip()
        return authors
    except (OSError, subprocess.SubprocessError):
        return {}


LOC_RE = re.compile(r"^(?P<stem>.+)\.(?P<lang>[a-z]{2})\.md$")


def run_audit(provider: str = "github") -> int:
    """Scan the repo; returns the audit run id."""
    languages = config.get("audit", "languages", ["es", "fr"])
    default_tag = config.get("audit", "default_tag", "customer-facing")

    src = _github_source()
    if not src:
        raise RuntimeError("Connect a GitHub repository before running the audit.")
    # real connected repo: clone/update and scan the whole tree
    cfg = src["config"] if isinstance(src["config"], dict) else json.loads(src["config"])
    repo_label = cfg.get("repo", "")
    repo = _sync_github_repo(
        repo_label,
        str(cfg.get("token") or config.get("github", "token") or ""),
    )
    base = repo
    md_files = sorted(p for p in repo.rglob("*.md") if ".git" not in p.parts)

    # keys are the doc path relative to `base` without the .md suffix
    base_docs: dict[str, pathlib.Path] = {}
    variants: dict[str, dict[str, pathlib.Path]] = {}
    for f in md_files:
        rel = f.relative_to(base).as_posix()
        m = LOC_RE.match(rel)
        if m:
            variants.setdefault(m.group("stem"), {})[m.group("lang")] = f
        else:
            base_docs[rel[:-3]] = f

    findings: list[dict] = []

    with _conn() as conn:
        indexed = {r["external_id"]: r for r in conn.execute(
            "SELECT d.*, array_remove(array_agg(t.tag), NULL) AS tags FROM documents d "
            "LEFT JOIN tags t ON t.document_id = d.id WHERE d.source = 'github' GROUP BY d.id").fetchall()}
        members = {r["email"]: r["name"] for r in conn.execute("SELECT name, email FROM users").fetchall()}
        author_map = {r["email"].lower(): r for r in conn.execute(
            "SELECT email, member_name, status FROM audit_author_map").fetchall()}

    # 1. coverage: repo files not indexed
    for stem, f in base_docs.items():
        ext_id = f"repo-{stem}"
        if ext_id not in indexed:
            findings.append(dict(kind="coverage", title=f"{stem}.md is not indexed",
                                 detail="Markdown file exists in the repo but not in the knowledge base.",
                                 fix_action="ingest", fix_payload={"stem": stem}))

    # 2. tags: indexed docs without the default tag set
    for ext_id, doc in indexed.items():
        if not doc["tags"]:
            findings.append(dict(kind="tags", title=f"{doc['title']} is untagged",
                                 detail=f"Suggested: {default_tag} (repo docs default).",
                                 fix_action="apply_tag", fix_payload={"doc_id": doc["id"], "tag": default_tag}))

    # 3. localization: expected languages missing per base doc; found variants unlinked
    for stem in base_docs:
        have = variants.get(stem, {})
        for lang in languages:
            if lang in have:
                findings.append(dict(kind="localization", title=f"{stem}.{lang}.md found",
                                     detail="Variant exists — link it as a translation.",
                                     fix_action="link_translation", fix_payload={"stem": stem, "lang": lang}))
            else:
                findings.append(dict(kind="localization", title=f"{stem}.md has no {lang.upper()} variant",
                                     detail=f"No {stem}.{lang}.md in the repo.",
                                     fix_action="translation_task", fix_payload={"stem": stem, "lang": lang}))

    # 4. authorship: git authors not mapped to members
    for email, name in _git_authors(repo).items():
        if email in members or name in members.values():
            continue
        prior = author_map.get(email.lower())
        if prior and prior["status"] == "mapped":
            continue  # an admin already said which member this address is
        detail = "This git author isn't a workspace member."
        if prior:
            detail += (f" Suggested as an invitation for {prior['member_name'] or name}; "
                       "still not a member — invite them from Settings → Members.")
        findings.append(dict(kind="authorship", title=f"Commits by {name} <{email}> are unmapped",
                             detail=detail,
                             fix_action="invite_member", fix_payload={"name": name, "email": email}))

    # 5. repo hygiene
    for required, why in (("README.md", "Repos should carry a README."),
                          ("LICENSE", "No license file found.")):
        if not (repo / required).exists():
            findings.append(dict(kind="hygiene", title=f"{required} missing",
                                 detail=why, fix_action="hygiene_task", fix_payload={"file": required}))

    with _conn() as conn:
        run = conn.execute("INSERT INTO audit_runs (provider, repo, findings) VALUES (%s, %s, %s) RETURNING id",
                           (provider, repo_label, len(findings))).fetchone()
        for f in findings:
            conn.execute("""INSERT INTO audit_findings (run_id, kind, title, detail, fix_action, fix_payload)
                            VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                         (run["id"], f["kind"], f["title"], f["detail"], f["fix_action"], json.dumps(f["fix_payload"])))
        conn.execute("INSERT INTO events (actor, verb, target) VALUES ('Mari', 'ran repository audit', %s)",
                     (f"{repo_label}: {len(findings)} findings",))
    return run["id"]


def fix_finding(finding_id: int, actor: str, member_name: str = "") -> str:
    """Apply a finding's fix. Returns a human summary of what happened."""
    with _conn() as conn:
        f = conn.execute("SELECT * FROM audit_findings WHERE id = %s", (finding_id,)).fetchone()
        if not f or f["status"] != "open":
            return "already handled"
        payload = f["fix_payload"] if isinstance(f["fix_payload"], dict) else json.loads(f["fix_payload"])
        action = f["fix_action"]
        summary = "done"

        if action == "ingest":
            stem = payload["stem"]
            _, base = _fix_repo_base()
            path = base / f"{stem}.md"
            if path.exists():  # index the file from the connected repo
                text = path.read_text(errors="replace")
                title = pathlib.PurePosixPath(stem).name.replace("-", " ").replace("_", " ").title()
                conn.execute("""INSERT INTO documents (source, external_id, title, snippet, body, author,
                                author_initials, kind, updated_src, created_src)
                                VALUES ('github', %s, %s, %s, %s, 'CI', 'CI', 'page', now(), now())
                                ON CONFLICT (source, external_id) DO UPDATE SET body = EXCLUDED.body""",
                             (f"repo-{stem}", title, text[:180].replace("\n", " "), text))
                summary = f"indexed {stem}.md"
        elif action == "apply_tag":
            conn.execute("INSERT INTO tags (document_id, tag) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                         (payload["doc_id"], payload["tag"]))
            summary = f"tagged '{payload['tag']}'"
        elif action == "link_translation":
            stem, lang = payload["stem"], payload["lang"]
            src = conn.execute("SELECT id FROM documents WHERE external_id = %s", (f"repo-{stem}",)).fetchone()
            _, base = _fix_repo_base()
            vf = base / f"{stem}.{lang}.md"
            text = vf.read_text() if vf.exists() else ""
            title = f"{stem} ({lang.upper()} translation)"
            conn.execute("""INSERT INTO documents (source, external_id, title, snippet, body, author,
                            author_initials, kind, updated_src, created_src)
                            VALUES ('github', %s, %s, %s, %s, 'CI', 'CI', 'page', now(), now())
                            ON CONFLICT (source, external_id) DO NOTHING""",
                         (f"repo-{stem}-{lang}", title, text[:180].replace("\n", " "), text))
            tgt = conn.execute("SELECT id FROM documents WHERE external_id = %s", (f"repo-{stem}-{lang}",)).fetchone()
            if src and tgt:
                conn.execute("""INSERT INTO edges (from_doc, to_doc, rel, day, curve, meta, created_at)
                                SELECT %s, %s, 'translates', 16, 10, '{"derived":"audit"}', CURRENT_DATE
                                WHERE NOT EXISTS (SELECT 1 FROM edges WHERE from_doc = %s AND to_doc = %s)""",
                             (src["id"], tgt["id"], src["id"], tgt["id"]))
            summary = f"linked {stem}.{lang}.md as a translation"
        elif action == "translation_task":
            title = f"Translate {payload['stem']}.md to {payload['lang'].upper()}"
            # Unassigned: the audit knows a translation is missing, not who
            # should write it. (The names that used to be here belonged to
            # nobody in the installing workspace.)
            conn.execute("""INSERT INTO tasks (title, assignee, assignee_initials, assignee_tint, kind, kind_label)
                            VALUES (%s, '', '', 1, 'approval', 'Translation')
                            ON CONFLICT (title) DO NOTHING""", (title,))
            summary = "translation task created"
        elif action == "invite_member":
            # This fix does not create an account. A commit address found in a
            # git history is evidence that someone committed, not that they
            # belong in this workspace, and a `users` row written here would be
            # indistinguishable in Settings → Members from a person who was
            # actually invited. Two honest outcomes instead:
            #   • member_name given → record that this address is that member.
            #   • nothing given     → record the address as a suggestion, which
            #     an admin turns into a real account via inviteMember.
            email, git_name = payload["email"], payload["name"]
            if member_name:
                known = conn.execute("SELECT name FROM users WHERE name = %s", (member_name,)).fetchone()
                if not known:
                    raise ValueError(
                        f"No workspace member named {member_name!r}. Invite them from "
                        f"Settings → Members first, then map {email} to that account.")
                conn.execute("""INSERT INTO audit_author_map
                                (email, git_name, member_name, status, decided_by)
                                VALUES (%s, %s, %s, 'mapped', %s)
                                ON CONFLICT (email) DO UPDATE SET
                                  git_name = EXCLUDED.git_name, member_name = EXCLUDED.member_name,
                                  status = 'mapped', decided_by = EXCLUDED.decided_by,
                                  decided_at = now()""",
                             (email, git_name, member_name, actor))
                summary = f"mapped {email} to member {member_name}"
            else:
                conn.execute("""INSERT INTO audit_author_map
                                (email, git_name, member_name, status, decided_by)
                                VALUES (%s, %s, %s, 'suggested', %s)
                                ON CONFLICT (email) DO UPDATE SET
                                  git_name = EXCLUDED.git_name, decided_by = EXCLUDED.decided_by,
                                  decided_at = now()
                                WHERE audit_author_map.status <> 'mapped'""",
                             (email, git_name, git_name, actor))
                summary = (f"recorded {git_name} <{email}> as a suggested invitation — "
                           "invite them from Settings → Members to create the account")
        elif action == "hygiene_task":
            title = f"Add {payload['file']} to the repo"
            conn.execute("""INSERT INTO tasks (title, assignee, assignee_initials, assignee_tint, kind, kind_label)
                            VALUES (%s, '', '', 3, 'approval', 'Repo hygiene')
                            ON CONFLICT (title) DO NOTHING""", (title,))
            summary = "hygiene task created"

        conn.execute("UPDATE audit_findings SET status = 'fixed' WHERE id = %s", (finding_id,))
        conn.execute("UPDATE audit_runs SET fixed = fixed + 1 WHERE id = %s", (f["run_id"],))
        conn.execute("INSERT INTO events (actor, verb, target) VALUES (%s, 'fixed audit finding', %s)",
                     (actor, f["title"]))
    return summary
