"""Mari Cloud — authentication (DESIGN.md §3).

Email/password (scrypt, stdlib), GitHub/Google OAuth (when configured in
mari.toml), cookie sessions, and first-run setup: when no account can log in,
a one-time admin setup token is printed to the server logs; POST /auth/setup
redeems it to create the admin.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import typing as t
import urllib.error
import urllib.parse
import urllib.request

import psycopg
from fastapi import APIRouter, HTTPException, Request, Response
from psycopg.rows import dict_row
from pydantic import BaseModel

import config

log = logging.getLogger("mari.auth")
router = APIRouter(prefix="/auth")

DB_URL_REF: dict = {"url": config.get("database", "url")}
COOKIE = "mari_session"
BYPASS_TOKEN = "mari-bypass"


def _conn():
    return psycopg.connect(DB_URL_REF["url"], row_factory=dict_row)


def _hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return salt.hex() + ":" + digest.hex()


def _verify(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split(":", 1)
        expected = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1)
        return secrets.compare_digest(expected.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def ensure_schema() -> None:
    with _conn() as conn:
        conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash text NOT NULL DEFAULT ''")
        conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS github_id text NOT NULL DEFAULT ''")
        conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS google_id text NOT NULL DEFAULT ''")
        conn.execute("""CREATE TABLE IF NOT EXISTS sessions (
            token text PRIMARY KEY, user_id int NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at timestamptz NOT NULL DEFAULT now())""")


def first_run_check() -> None:
    """If nobody can log in yet, mint a setup token and print it to the logs."""
    with _conn() as conn:
        can_login = conn.execute(
            "SELECT count(*) AS n FROM users WHERE password_hash <> '' OR github_id <> '' OR google_id <> ''"
        ).fetchone()["n"]
        done = conn.execute("SELECT 1 FROM settings WHERE key = 'setup_complete'").fetchone()
        if can_login or done:
            return
        token = secrets.token_urlsafe(24)
        conn.execute("""INSERT INTO settings (key, value) VALUES ('setup_token', %s)
                        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""",
                     (json.dumps({"hash": hashlib.sha256(token.encode()).hexdigest()}),))
    banner = "\n".join([
        "", "=" * 68,
        "  MARI CLOUD — FIRST-TIME SETUP",
        "  No admin account exists yet. Open the app and finish setup with",
        "  this one-time admin token (it will not be shown again):",
        "", f"      {token}", "",
        "  Setup URL: http://localhost:5173/setup", "=" * 68, "",
    ])
    log.warning(banner)
    print(banner, flush=True)


def _is_https(request: Request | None) -> bool:
    """HTTPS detection (direct or behind a proxy) so cookies get secure=True
    in production while localhost HTTP dev keeps working."""
    if request is None:
        return False
    proto = request.headers.get("X-Forwarded-Proto", request.url.scheme)
    return proto.split(",")[0].strip().lower() == "https"


def _set_session_cookie(response: Response, token: str, request: Request | None) -> None:
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax",
                        secure=_is_https(request),
                        max_age=int(config.get("auth", "session_days", 14)) * 86400)


def _client_detail(request: Request | None) -> list[dict]:
    """The access log's per-event detail for a session: where the request came
    from and what made it. Only what the request actually carried — a missing
    header is recorded as unknown, never guessed."""
    if request is None:
        return []
    forwarded = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    ip = forwarded or (request.client.host if request.client else "")
    return [{"label": "IP address", "value": ip or "unknown"},
            {"label": "User agent", "value": (request.headers.get("User-Agent") or "unknown")[:200]}]


def _create_session(user_id: int, response: Response, request: Request | None = None) -> str:
    token = secrets.token_urlsafe(32)
    with _conn() as conn:
        conn.execute("INSERT INTO sessions (token, user_id) VALUES (%s, %s)", (token, user_id))
        # Every sign-in path funnels through here, so the access log records
        # them all — with the client detail the expanded row shows.
        conn.execute("""INSERT INTO events (actor, verb, target, detail)
                        SELECT name, 'signed in', 'Mari Cloud', %s FROM users WHERE id = %s""",
                     (json.dumps(_client_detail(request)), user_id))
    _set_session_cookie(response, token, request)
    return token


def current_user(request: Request) -> dict | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    with _conn() as conn:
        if token == BYPASS_TOKEN and config.get("auth", "bypass_enabled", False):
            return conn.execute("""SELECT * FROM users
                                   ORDER BY (role = 'admin') DESC, id ASC
                                   LIMIT 1""").fetchone()
        return conn.execute("""SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
                               WHERE s.token = %s""", (token,)).fetchone()


def require_user(request: Request) -> dict:
    """FastAPI dependency: any authenticated user (the bypass cookie resolves
    to the workspace admin, so it satisfies this too)."""
    user = current_user(request)
    if not user:
        raise HTTPException(401, "Authentication required.")
    return user


class Credentials(BaseModel):
    email: str
    password: str
    name: str | None = None


class SetupIn(BaseModel):
    token: str
    name: str
    email: str
    password: str
    workspace: str | None = None


def _user_out(u: dict) -> dict:
    return {"id": u["id"], "name": u["name"], "email": u["email"], "role": u["role"],
            "initials": u["initials"], "tint": u["tint"], "provider": u["provider"]}


@router.get("/me")
def me(request: Request):
    u = current_user(request)
    with _conn() as conn:
        needs_setup = not conn.execute("SELECT 1 FROM settings WHERE key = 'setup_complete'").fetchone()
        ws = conn.execute("SELECT value FROM settings WHERE key = 'workspace'").fetchone()
    oauth = {"github": bool(config.get("auth", "github_client_id")),
             "google": bool(config.get("auth", "google_client_id"))}
    # The sign-in screen names the workspace before anyone is authenticated, so
    # the name (and only the name) rides along here. "" until setup or an admin
    # supplies one — the screen then renders unbranded rather than guessing.
    value = ws["value"] if ws else {}
    if not isinstance(value, dict):
        value = json.loads(value or "{}")
    return {"user": _user_out(u) if u else None, "needsSetup": needs_setup, "oauth": oauth,
            "workspace": {"name": str(value.get("name") or "")},
            "bypassEnabled": bool(config.get("auth", "bypass_enabled", False))}


@router.post("/bypass")
def bypass(request: Request, response: Response):
    """Create a session for the workspace admin when the explicit escape hatch is enabled."""
    if not config.get("auth", "bypass_enabled", False):
        raise HTTPException(404, "Login bypass is not enabled.")
    with _conn() as conn:
        user = conn.execute("""SELECT * FROM users
                               ORDER BY (role = 'admin') DESC, id ASC
                               LIMIT 1""").fetchone()
        if not user:
            raise HTTPException(503, "No workspace user is available for login bypass.")
        conn.execute("""INSERT INTO events (actor, verb, target, detail)
                        VALUES (%s, 'used login bypass', 'Mari Cloud', %s)""",
                     (user["name"], json.dumps(_client_detail(request))))
    _set_session_cookie(response, BYPASS_TOKEN, request)
    return {"user": _user_out(user)}


@router.post("/setup")
def setup(body: SetupIn, request: Request, response: Response):
    with _conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = 'setup_token'").fetchone()
        if not row:
            raise HTTPException(400, "Setup is not pending.")
        stored = row["value"] if isinstance(row["value"], dict) else json.loads(row["value"])
        if hashlib.sha256(body.token.encode()).hexdigest() != stored.get("hash"):
            raise HTTPException(403, "Invalid setup token — check the server logs.")
        # Never overwrite an existing row's credentials — a name collision must
        # not become an account takeover (pick a different name/email instead).
        taken = conn.execute("SELECT 1 FROM users WHERE email = %s OR name = %s",
                             (body.email, body.name)).fetchone()
        if taken:
            raise HTTPException(409, "A user with that name or email already exists.")
        initials = "".join(w[0].upper() for w in body.name.split()[:2]) or "AD"
        conn.execute("""INSERT INTO users (name, initials, tint, email, role, provider, password_hash)
                        VALUES (%s, %s, 1, %s, 'admin', 'manual', %s)""",
                     (body.name, initials, body.email, _hash(body.password)))
        if body.workspace:
            conn.execute("""INSERT INTO settings (key, value) VALUES ('workspace', %s)
                            ON CONFLICT (key) DO UPDATE SET value = settings.value || EXCLUDED.value""",
                         (json.dumps({"name": body.workspace}),))
        conn.execute("INSERT INTO settings (key, value) VALUES ('setup_complete', 'true') ON CONFLICT DO NOTHING")
        conn.execute("DELETE FROM settings WHERE key = 'setup_token'")
        user = conn.execute("SELECT * FROM users WHERE email = %s", (body.email,)).fetchone()
        conn.execute("INSERT INTO events (actor, verb, target) VALUES (%s, 'completed first-run setup', %s)",
                     (body.name, body.workspace or "workspace"))
    _create_session(user["id"], response, request)
    return {"user": _user_out(user)}


@router.post("/register")
def register(body: Credentials, request: Request, response: Response):
    if not body.name or len(body.password) < 8:
        raise HTTPException(400, "Name required; password must be 8+ characters.")
    initials = "".join(w[0].upper() for w in body.name.split()[:2]) or "??"
    with _conn() as conn:
        # Explicit conflict check on BOTH email and name: registering with an
        # existing user's display name must never overwrite their credentials.
        exists = conn.execute("SELECT 1 FROM users WHERE email = %s OR name = %s",
                              (body.email, body.name)).fetchone()
        if exists:
            raise HTTPException(409, "An account with that email or name already exists.")
        conn.execute("""INSERT INTO users (name, initials, tint, email, role, provider, password_hash)
                        VALUES (%s, %s, %s, %s, 'user', 'manual', %s)""",
                     (body.name, initials, (hash(body.name) % 4) + 1, body.email, _hash(body.password)))
        user = conn.execute("SELECT * FROM users WHERE email = %s", (body.email,)).fetchone()
    _create_session(user["id"], response, request)
    return {"user": _user_out(user)}


@router.post("/login")
def login(body: Credentials, request: Request, response: Response):
    with _conn() as conn:
        user = conn.execute("SELECT * FROM users WHERE email = %s AND password_hash <> ''", (body.email,)).fetchone()
    if not user or not _verify(body.password, user["password_hash"]):
        raise HTTPException(401, "Wrong email or password.")
    _create_session(user["id"], response, request)
    return {"user": _user_out(user)}


@router.post("/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE)
    if token:
        with _conn() as conn:
            conn.execute("DELETE FROM sessions WHERE token = %s", (token,))
    response.delete_cookie(COOKIE)
    return {"ok": True}


# ————— OAuth (GitHub / Google) — enabled when configured in mari.toml —————

OAUTH = {
    "github": {
        "authorize": "https://github.com/login/oauth/authorize",
        "token": "https://github.com/login/oauth/access_token",
        "user": "https://api.github.com/user",
        "scope": "read:user user:email",
    },
    "google": {
        "authorize": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "user": "https://www.googleapis.com/oauth2/v2/userinfo",
        "scope": "openid email profile",
    },
}


def _oauth_creds(provider: str) -> tuple[str, str]:
    cid = config.get("auth", f"{provider}_client_id", "")
    secret = config.get("auth", f"{provider}_client_secret", "")
    if not cid:
        raise HTTPException(400, f"{provider} OAuth is not configured — set auth.{provider}_client_id in mari.toml.")
    return cid, secret


# Literal column map — never derive SQL identifiers from the URL path.
OAUTH_ID_COLUMN = {"github": "github_id", "google": "google_id"}
STATE_COOKIE = "mari_oauth_state"


def _link_or_create_oauth_user(provider: str, ext_id: str, name: str, email: str) -> dict:
    """Resolve an OAuth identity to a user row. Match ONLY by the provider id
    column or by verified email — never by display name: a profile named like
    an existing member must not inherit that member's row (or role). On a pure
    name collision we create a fresh 'user'-role row with a deduplicated name."""
    column = OAUTH_ID_COLUMN[provider]
    with _conn() as conn:
        user = conn.execute(f"SELECT * FROM users WHERE {column} = %s", (ext_id,)).fetchone()
        if user:
            return user
        if email:
            existing = conn.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone()
            if existing:
                conn.execute(f"UPDATE users SET {column} = %s WHERE id = %s", (ext_id, existing["id"]))
                return conn.execute("SELECT * FROM users WHERE id = %s", (existing["id"],)).fetchone()
        initials = "".join(w[0].upper() for w in name.split()[:2]) or "??"
        candidate = name
        for n in range(2, 50):
            if not conn.execute("SELECT 1 FROM users WHERE name = %s", (candidate,)).fetchone():
                break
            candidate = f"{name} ({n})"
        row = conn.execute(f"""INSERT INTO users (name, initials, tint, email, role, provider, {column})
                               VALUES (%s, %s, %s, %s, 'user', %s, %s)
                               ON CONFLICT (name) DO NOTHING RETURNING id""",
                           (candidate, initials, (hash(name) % 4) + 1, email, provider, ext_id)).fetchone()
        if not row:  # lost a concurrent race on the deduped name — very unlikely
            raise HTTPException(409, "Could not create an account for this OAuth identity — try again.")
        return conn.execute("SELECT * FROM users WHERE id = %s", (row["id"],)).fetchone()


@router.get("/oauth/{provider}")
def oauth_start(provider: str, request: Request):
    if provider not in OAUTH:
        raise HTTPException(404, "Unknown provider")
    cid, _ = _oauth_creds(provider)
    redirect = f"{config.get('auth', 'oauth_redirect_base')}/auth/callback/{provider}"
    state = secrets.token_urlsafe(16)
    params = {"client_id": cid, "redirect_uri": redirect, "scope": OAUTH[provider]["scope"],
              "state": state, "response_type": "code"}
    from fastapi.responses import RedirectResponse
    resp = RedirectResponse(OAUTH[provider]["authorize"] + "?" + urllib.parse.urlencode(params))
    # Short-lived state cookie: the callback must echo this exact value (CSRF).
    resp.set_cookie(STATE_COOKIE, state, httponly=True, samesite="lax",
                    secure=_is_https(request), max_age=600)
    return resp


@router.get("/callback/{provider}")
def oauth_callback(provider: str, code: str, request: Request, state: str = ""):
    if provider not in OAUTH:
        raise HTTPException(404, "Unknown provider")
    expected_state = request.cookies.get(STATE_COOKIE, "")
    if not state or not expected_state or not secrets.compare_digest(state, expected_state):
        raise HTTPException(400, "OAuth state mismatch — restart the sign-in flow.")
    cid, secret = _oauth_creds(provider)
    redirect = f"{config.get('auth', 'oauth_redirect_base')}/auth/callback/{provider}"
    body = urllib.parse.urlencode({
        "client_id": cid, "client_secret": secret, "code": code,
        "redirect_uri": redirect, "grant_type": "authorization_code"}).encode()
    req = urllib.request.Request(OAUTH[provider]["token"], data=body,
                                 headers={"Accept": "application/json",
                                          "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            token_data = json.loads(r.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError):
        raise HTTPException(400, "OAuth token exchange failed — try signing in again.")
    access = token_data.get("access_token")
    if not access:
        raise HTTPException(401, "OAuth exchange failed.")
    req = urllib.request.Request(OAUTH[provider]["user"], headers={"Authorization": f"Bearer {access}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            profile = json.loads(r.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError):
        raise HTTPException(400, "OAuth profile fetch failed — try signing in again.")
    ext_id = str(profile.get("id", "")).strip()
    if not ext_id:
        raise HTTPException(400, "OAuth provider returned no account id.")
    name = profile.get("name") or profile.get("login") or profile.get("email", "user").split("@")[0]
    email = profile.get("email") or ""
    user = _link_or_create_oauth_user(provider, ext_id, name, email)
    from fastapi.responses import RedirectResponse
    # Land back on the web app: configurable for deployments, dev default.
    resp = RedirectResponse(config.get("auth", "app_url", "http://localhost:5173/"))
    token = secrets.token_urlsafe(32)
    with _conn() as conn:
        conn.execute("INSERT INTO sessions (token, user_id) VALUES (%s, %s)", (token, user["id"]))
    _set_session_cookie(resp, token, request)
    resp.delete_cookie(STATE_COOKIE)
    return resp
