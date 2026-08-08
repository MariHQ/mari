"""Mari — authentication (DESIGN.md §3).

Email/password (scrypt, stdlib), GitHub/Google OAuth (when configured in
mari.toml), cookie sessions, and first-run setup: when no account can log in,
a one-time admin setup token is printed to the server logs; POST /auth/setup
redeems it to create the admin.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import os
import secrets
import threading
import time
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

# How long a magic sign-in link stays valid.
MAGIC_LINK_TTL_MIN = 15

# How long the first-run admin setup token stays redeemable. `first_run_check`
# re-mints it on every startup while setup is still pending, so an expired
# token is fixed by restarting the server — which is also where it is printed.
SETUP_TOKEN_TTL_MIN = 24 * 60

# A bypass session is a demo convenience, not a workday. It gets its own short
# lifetime regardless of auth.session_days.
BYPASS_SESSION_HOURS = 12


# ————— who is making this request —————
#
# The audit log, notifications and watches all need the caller's identity, and
# they are written from code that has no Request in hand (GraphQL resolvers,
# the connector REST surface). `current_user` is the one place every request
# resolves its session, so it publishes the answer here and db.actor_name()
# reads it back. Background threads (ingest, flowengine, the poller) never go
# through `current_user`, so they keep the default: SERVICE_ACTOR, which says
# "no human asked for this" instead of naming one.
SERVICE_ACTOR = "Mari"

CALLER: contextvars.ContextVar[dict | None] = contextvars.ContextVar("mari_caller", default=None)


def set_caller(user: dict | None) -> None:
    CALLER.set(user)


def caller() -> dict | None:
    return CALLER.get()


def actor_name() -> str:
    """The name to record as the actor of a write. SERVICE_ACTOR when the write
    has no human behind it — never a hardcoded person."""
    user = CALLER.get()
    name = (user or {}).get("name") if isinstance(user, dict) else None
    return str(name) if name else SERVICE_ACTOR


def _conn():
    return psycopg.connect(DB_URL_REF["url"], row_factory=dict_row)


# ————— rate limiting —————
#
# In-process sliding window. This is one API process per container, so it is a
# per-instance brake, not a cluster-wide quota — enough to make credential
# stuffing and setup-token guessing expensive, and it never fails a request for
# a reason it will not name.
_ATTEMPTS: dict[str, list[float]] = {}
_ATTEMPTS_LOCK = threading.Lock()


def _client_ip(request: Request | None) -> str:
    if request is None:
        return "unknown"
    forwarded = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    return forwarded or (request.client.host if request.client else "unknown")


def _rate_limit(bucket: str, key: str, limit: int, window_s: int) -> None:
    ident = f"{bucket}:{key}"
    now = time.time()
    with _ATTEMPTS_LOCK:
        if len(_ATTEMPTS) > 8192:  # bounded: a flood must not grow the process
            _ATTEMPTS.clear()
        hits = [t for t in _ATTEMPTS.get(ident, ()) if now - t < window_s]
        if len(hits) >= limit:
            retry = max(1, int(window_s - (now - hits[0])) + 1)
            _ATTEMPTS[ident] = hits
            raise HTTPException(
                429, f"Too many attempts. Try again in {retry} seconds.",
                headers={"Retry-After": str(retry)})
        hits.append(now)
        _ATTEMPTS[ident] = hits


def _rate_clear(bucket: str, key: str) -> None:
    """Called on success: a person who got in should not be throttled by their
    own earlier typos."""
    with _ATTEMPTS_LOCK:
        _ATTEMPTS.pop(f"{bucket}:{key}", None)


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
        # AUTH-2: marks a row as an admin-issued invitation, claimable once at
        # POST /auth/register. See init.sql for why credential-less is not
        # sufficient on its own.
        conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS invited_by text NOT NULL DEFAULT ''")
        conn.execute("""CREATE TABLE IF NOT EXISTS sessions (
            token text PRIMARY KEY, user_id int NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at timestamptz NOT NULL DEFAULT now())""")
        # AUTH-3: sessions used to live forever server-side; the cookie's
        # max_age was a client hint a stolen token could simply ignore.
        # Existing rows get an expiry derived from when they were created, so
        # the migration does not silently extend anything.
        conn.execute("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS expires_at timestamptz")
        conn.execute("""UPDATE sessions SET expires_at = created_at + (%s || ' days')::interval
                        WHERE expires_at IS NULL""", (int(config.get("auth", "session_days", 14)),))
        conn.execute("ALTER TABLE sessions ALTER COLUMN expires_at SET NOT NULL")
        conn.execute("CREATE INDEX IF NOT EXISTS sessions_expires_idx ON sessions (expires_at)")
        conn.execute("DELETE FROM sessions WHERE expires_at <= now()")


def first_run_check() -> None:
    """If nobody can log in yet, mint a setup token and print it to the logs."""
    with _conn() as conn:
        can_login = conn.execute(
            "SELECT count(*) AS n FROM users WHERE password_hash <> '' OR github_id <> '' OR google_id <> ''"
        ).fetchone()["n"]
        done = conn.execute("SELECT 1 FROM settings WHERE key = 'setup_complete'").fetchone()
        if can_login or done:
            _warn_if_bypass_enabled()
            return
        # The desktop supervisor passes a fresh token directly to the bundled
        # local web client. Server deployments keep the log-only token flow.
        token = os.environ.get("MARI_DESKTOP_SETUP_TOKEN") or secrets.token_urlsafe(24)
        conn.execute("""INSERT INTO settings (key, value) VALUES ('setup_token', %s)
                        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""",
                     (json.dumps({"hash": hashlib.sha256(token.encode()).hexdigest(),
                                  "minted_at": time.time()}),))
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
    _warn_if_bypass_enabled()


def _warn_if_bypass_enabled() -> None:
    """The demo bypass is a real feature and a real hole: one unauthenticated
    POST becomes a workspace-admin session. If it is on, say so at startup
    rather than letting a deployment inherit it quietly."""
    if not config.get("auth", "bypass_enabled", False):
        return
    message = (
        "auth.bypass_enabled is ON: POST /auth/bypass signs anyone in as the "
        "workspace admin, with no credential. Intended for demos only — set "
        "MARI_AUTH_BYPASS=false (or auth.bypass_enabled = false in mari.toml) "
        "on any deployment that holds real data."
    )
    log.warning(message)
    print(f"\n!!  {message}\n", flush=True)


def _is_https(request: Request | None) -> bool:
    """HTTPS detection (direct or behind a proxy) so cookies get secure=True
    in production while localhost HTTP dev keeps working."""
    if request is None:
        return False
    proto = request.headers.get("X-Forwarded-Proto", request.url.scheme)
    return proto.split(",")[0].strip().lower() == "https"


def _set_session_cookie(response: Response, token: str, request: Request | None,
                        max_age: int | None = None) -> None:
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax",
                        secure=_is_https(request),
                        max_age=max_age if max_age is not None
                        else int(config.get("auth", "session_days", 14)) * 86400)


def _clear_session_cookie(response: Response, request: Request | None) -> None:
    """Delete the session cookie, with the same attributes it was set with —
    a browser only drops a cookie when path/secure/samesite line up.

    This exists because a rejected credential used to live forever: the
    hardening pass stopped honouring the static "mari-bypass" token, and every
    returning visitor kept presenting it on every request because nothing ever
    told the browser to throw it away."""
    response.delete_cookie(COOKIE, httponly=True, samesite="lax", secure=_is_https(request))


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


def _create_session(user_id: int, response: Response, request: Request | None = None,
                    ttl_seconds: int | None = None, verb: str = "signed in") -> str:
    """Mint one real session row. EVERY authenticated path goes through here —
    there is no token the server accepts that it did not generate and store."""
    ttl = ttl_seconds if ttl_seconds is not None else int(config.get("auth", "session_days", 14)) * 86400
    token = secrets.token_urlsafe(32)
    with _conn() as conn:
        conn.execute("""INSERT INTO sessions (token, user_id, expires_at)
                        VALUES (%s, %s, now() + (%s || ' seconds')::interval)""",
                     (token, user_id, ttl))
        # Cheap opportunistic GC: expired rows are unusable, so they are litter.
        conn.execute("DELETE FROM sessions WHERE expires_at <= now()")
        # Every sign-in path funnels through here, so the access log records
        # them all — with the client detail the expanded row shows.
        conn.execute("""INSERT INTO events (actor, verb, target, detail)
                        SELECT name, %s, 'Mari', %s FROM users WHERE id = %s""",
                     (verb, json.dumps(_client_detail(request)), user_id))
    _set_session_cookie(response, token, request, max_age=ttl)
    return token


def current_user(request: Request) -> dict | None:
    """Resolve the session cookie to a user. A cookie value is only ever a row
    in `sessions` that has not expired — there is no static token, and no
    configuration setting that makes a guessable string authenticate.

    The answer is memoised on the ASGI scope, so the middleware below, a route
    dependency and the GraphQL context getter cost one query between them."""
    scope = getattr(request, "scope", None)
    if isinstance(scope, dict) and "mari_user" in scope:
        user = scope["mari_user"]
        set_caller(user)
        return user
    token = request.cookies.get(COOKIE)
    user = None
    if token:
        with _conn() as conn:
            user = conn.execute("""SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
                                   WHERE s.token = %s AND s.expires_at > now()""", (token,)).fetchone()
    # A server with the bypass ON has declared that it does not require
    # authentication: POST /auth/bypass already hands a workspace-admin session
    # to anyone who asks, so resolving an unauthenticated caller to the same
    # admin grants nothing that was not already free. It is off by default, so
    # this branch never runs on a server that has not opted in.
    #
    # This is not the old static token coming back. There is still no string a
    # caller can present to authenticate — the difference is that a server which
    # says it wants no auth is believed, rather than being handed a session row
    # it may not be able to read back.
    #
    # Which is the real reason this exists: deploy/lambda runs an embedded
    # Postgres per execution environment, so under any concurrency a session
    # minted by one instance does not exist for the next. Requiring the row made
    # the demo 401 at random. The row is still required, and still the only way
    # in, on every server that has not turned auth off.
    if user is None and config.get("auth", "bypass_enabled", False):
        with _conn() as conn:
            user = _bypass_target(conn)
    if isinstance(scope, dict):
        scope["mari_user"] = user
    set_caller(user)
    return user


class CallerMiddleware:
    """Publish the caller for the whole request, before anything else runs.

    A pure ASGI middleware rather than a `@app.middleware("http")` one on
    purpose: BaseHTTPMiddleware runs the rest of the app in a spawned task, and
    a contextvar set there does not reliably reach the endpoint. This runs in
    the request's own task, so the value is visible to route dependencies, to
    GraphQL resolvers, and to sync endpoints (Starlette copies the current
    context into the worker thread). Background threads never pass through
    here, which is why db.actor_name() falls back to SERVICE_ACTOR."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        try:
            current_user(Request(scope, receive))
        except Exception:  # noqa: BLE001 — identifying the caller must never
            set_caller(None)  # fail the request; the route's own guard decides
        return await self.app(scope, receive, send)


def require_user(request: Request) -> dict:
    """FastAPI dependency: any authenticated user."""
    user = current_user(request)
    if not user:
        raise HTTPException(401, "Authentication required.")
    return user


def require_role(request: Request, *roles: str) -> dict:
    user = require_user(request)
    if user.get("role") not in roles:
        raise HTTPException(403, f"This action needs the {' or '.join(roles)} role.")
    return user


def require_admin(request: Request) -> dict:
    return require_role(request, "admin")


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


def _bypass_target(conn) -> dict | None:
    return conn.execute("""SELECT * FROM users
                           ORDER BY (role = 'admin') DESC, id ASC
                           LIMIT 1""").fetchone()


@router.get("/me")
def me(request: Request, response: Response):
    u = current_user(request)
    # A session cookie that resolves to nothing is a credential this server
    # will never accept again, and the browser will keep sending it on every
    # request until something clears it. /auth/me is where the app asks "who
    # am I", so it is where the answer "nobody, and that cookie is dead" gets
    # acted on. Only when a cookie was actually presented: a visitor with no
    # cookie gets no Set-Cookie header at all.
    if u is None and request.cookies.get(COOKIE):
        _clear_session_cookie(response, request)
    with _conn() as conn:
        needs_setup = not conn.execute("SELECT 1 FROM settings WHERE key = 'setup_complete'").fetchone()
        ws = conn.execute("SELECT value FROM settings WHERE key = 'workspace'").fetchone()
        # Only advertise the bypass when it would actually work. Hiding the
        # flag while the endpoint still answers would be theatre — an attacker
        # posts to /auth/bypass regardless — and it would hide the button from
        # the demo it exists for. The honest fix is the default (off), not the
        # secrecy.
        bypass = bool(config.get("auth", "bypass_enabled", False)) and _bypass_target(conn) is not None
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
            "registrationEnabled": bool(config.get("auth", "registration_enabled", False)),
            "bypassEnabled": bypass}


@router.post("/bypass")
def bypass(request: Request, response: Response):
    """Demo escape hatch (README): sign in as the workspace admin with no
    credential. It mints an ordinary session row like every other sign-in, so
    it can be revoked, expires, and shows up in the access log as what it is."""
    if not config.get("auth", "bypass_enabled", False):
        raise HTTPException(404, "Login bypass is not enabled.")
    _rate_limit("bypass", _client_ip(request), 20, 300)
    with _conn() as conn:
        user = _bypass_target(conn)
    if not user:
        raise HTTPException(503, "No workspace user is available for login bypass.")
    _create_session(user["id"], response, request,
                    ttl_seconds=BYPASS_SESSION_HOURS * 3600, verb="used login bypass")
    return {"user": _user_out(user)}


def _read_setup_token(conn) -> dict:
    """The stored setup token, or a loud reason there isn't a usable one."""
    row = conn.execute("SELECT value FROM settings WHERE key = 'setup_token'").fetchone()
    if not row:
        raise HTTPException(400, "Setup is not pending.")
    stored = row["value"] if isinstance(row["value"], dict) else json.loads(row["value"])
    minted = stored.get("minted_at")
    # Tokens minted before this column existed have no timestamp; they are
    # treated as still valid rather than locking an in-progress install out.
    if minted is not None and time.time() - float(minted) > SETUP_TOKEN_TTL_MIN * 60:
        raise HTTPException(403, "That setup token has expired — restart the server for a new one.")
    return stored


class SetupCheckIn(BaseModel):
    token: str


@router.post("/setup/check")
def setup_check(body: SetupCheckIn, request: Request):
    """Pre-flight for the Setup page: is this token the one? Compares the hash
    and spends nothing — the row is only deleted by a completed POST /setup."""
    _rate_limit("setup", _client_ip(request), 10, 300)
    with _conn() as conn:
        stored = _read_setup_token(conn)
        if not secrets.compare_digest(hashlib.sha256(body.token.encode()).hexdigest(),
                                      str(stored.get("hash") or "")):
            raise HTTPException(403, "Invalid setup token — check the server logs.")
    _rate_clear("setup", _client_ip(request))
    return {"ok": True}


@router.post("/setup")
def setup(body: SetupIn, request: Request, response: Response):
    _rate_limit("setup", _client_ip(request), 10, 300)
    with _conn() as conn:
        stored = _read_setup_token(conn)
        if not secrets.compare_digest(hashlib.sha256(body.token.encode()).hexdigest(),
                                      str(stored.get("hash") or "")):
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
    _rate_clear("setup", _client_ip(request))
    _create_session(user["id"], response, request)
    return {"user": _user_out(user)}


@router.post("/register")
def register(body: Credentials, request: Request, response: Response):
    """Create an account. AUTH-2: this used to be open to anyone who could
    reach the port, and a session is all GraphQL asks for. Two ways in now:

      • Claiming an invite — an admin created the row (invite_member) and it
        has no credential yet. Registering with that email sets the password
        on the row the admin made, keeping the role they were invited with.
      • Open registration — only when auth.registration_enabled is set, for
        workspaces that genuinely want anyone to sign up.

    Both need first-run setup to be finished: before that there is no admin to
    invite anyone, and the setup token is the only intended way in."""
    email = (body.email or "").strip()
    if not body.name or not email:
        raise HTTPException(400, "Name and email are required.")
    if len(body.password) < 8:
        raise HTTPException(400, "The password must be at least 8 characters.")
    _rate_limit("register", _client_ip(request), 10, 300)
    initials = "".join(w[0].upper() for w in body.name.split()[:2]) or "??"
    with _conn() as conn:
        if not conn.execute("SELECT 1 FROM settings WHERE key = 'setup_complete'").fetchone():
            raise HTTPException(403, "This workspace has not finished first-run setup — "
                                     "redeem the admin setup token from the server logs first.")
        # An invitation is a row an admin created through inviteMember, which
        # is the only thing that writes `invited_by`. Being credential-less is
        # not enough on its own: repoaudit's member mapping also creates rows,
        # from addresses it read out of a repository, and those were never an
        # offer of an account.
        invited = conn.execute(
            """SELECT * FROM users WHERE lower(email) = lower(%s) AND invited_by <> ''
                 AND password_hash = '' AND github_id = '' AND google_id = ''""",
            (email,)).fetchone()
        if invited:
            # Claim the invite. The display name is NOT taken from the request:
            # the admin chose it, `users.name` is unique, and letting a claimer
            # rename themselves into another member's name is the collision the
            # rest of this module already refuses.
            conn.execute("UPDATE users SET password_hash = %s, provider = 'manual' WHERE id = %s",
                         (_hash(body.password), invited["id"]))
            user = conn.execute("SELECT * FROM users WHERE id = %s", (invited["id"],)).fetchone()
            conn.execute("""INSERT INTO events (actor, verb, target, detail)
                            VALUES (%s, 'claimed an invitation', 'Mari', %s)""",
                         (user["name"], json.dumps(_client_detail(request))))
        else:
            if not config.get("auth", "registration_enabled", False):
                raise HTTPException(
                    403, "This workspace is invite-only. Ask an admin to invite "
                         f"{email}, then register with that address.")
            # Explicit conflict check on BOTH email and name: registering with
            # an existing user's display name must never overwrite theirs.
            exists = conn.execute("SELECT 1 FROM users WHERE lower(email) = lower(%s) OR name = %s",
                                  (email, body.name)).fetchone()
            if exists:
                raise HTTPException(409, "An account with that email or name already exists.")
            conn.execute("""INSERT INTO users (name, initials, tint, email, role, provider, password_hash)
                            VALUES (%s, %s, %s, %s, 'user', 'manual', %s)""",
                         (body.name, initials, (hash(body.name) % 4) + 1, email, _hash(body.password)))
            user = conn.execute("SELECT * FROM users WHERE lower(email) = lower(%s)", (email,)).fetchone()
    _rate_clear("register", _client_ip(request))
    _create_session(user["id"], response, request)
    return {"user": _user_out(user)}


@router.post("/login")
def login(body: Credentials, request: Request, response: Response):
    ip, email = _client_ip(request), (body.email or "").strip().lower()
    _rate_limit("login-ip", ip, 20, 300)
    _rate_limit("login-account", email, 8, 300)
    with _conn() as conn:
        user = conn.execute("SELECT * FROM users WHERE lower(email) = %s AND password_hash <> ''",
                            (email,)).fetchone()
    if not user or not _verify(body.password, user["password_hash"]):
        raise HTTPException(401, "Wrong email or password.")
    _rate_clear("login-ip", ip)
    _rate_clear("login-account", email)
    _create_session(user["id"], response, request)
    return {"user": _user_out(user)}


@router.post("/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE)
    if token:
        with _conn() as conn:
            conn.execute("DELETE FROM sessions WHERE token = %s", (token,))
    set_caller(None)
    _clear_session_cookie(response, request)
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
    _create_session(user["id"], resp, request)
    resp.delete_cookie(STATE_COOKIE)
    return resp

# ————— magic link —————
# The console has always offered "Email me a magic link instead"; there was no
# endpoint behind it, so the control did nothing. This follows the same shape
# as first-run setup: mint a single-use token, store only its hash, and print
# the link to the server log. Sending it by email needs SMTP the deployment
# does not configure yet, and printing it is honest about that — it is exactly
# how the admin setup token already reaches you.


class MagicLinkIn(BaseModel):
    email: str


@router.post("/magic-link")
def magic_link(body: MagicLinkIn, request: Request):
    """Mint a one-time sign-in link. Always reports success: telling a caller
    whether an address exists is an account-enumeration oracle."""
    email = (body.email or "").strip().lower()
    _rate_limit("magic-ip", _client_ip(request), 10, 300)
    _rate_limit("magic-account", email, 3, 300)
    with _conn() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS magic_links (
                          token_hash text PRIMARY KEY,
                          user_id    int NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                          created_at timestamptz NOT NULL DEFAULT now(),
                          used_at    timestamptz)""")
        user = conn.execute("SELECT id FROM users WHERE lower(email) = %s", (email,)).fetchone()
        if user:
            token = secrets.token_urlsafe(32)
            conn.execute("DELETE FROM magic_links WHERE user_id = %s AND used_at IS NULL", (user["id"],))
            conn.execute("INSERT INTO magic_links (token_hash, user_id) VALUES (%s, %s)",
                         (hashlib.sha256(token.encode()).hexdigest(), user["id"]))
            base = config.get("auth", "oauth_redirect_base") or "http://localhost:5173"
            print("\n" + "=" * 68, flush=True)
            print(f"  Magic sign-in link for {email} (valid once, {MAGIC_LINK_TTL_MIN} minutes):", flush=True)
            print(f"\n      {base}/auth/magic/{token}\n", flush=True)
            print("=" * 68 + "\n", flush=True)
    return {"ok": True, "sent": True}


@router.get("/magic/{token}")
def magic_consume(token: str, request: Request):
    """Spend the token, start a session, and land on the console."""
    from fastapi.responses import RedirectResponse
    digest = hashlib.sha256(token.encode()).hexdigest()
    with _conn() as conn:
        row = conn.execute(
            """SELECT user_id FROM magic_links
                WHERE token_hash = %s AND used_at IS NULL
                  AND created_at > now() - (%s || ' minutes')::interval""",
            (digest, MAGIC_LINK_TTL_MIN)).fetchone()
        if not row:
            # Expired, already spent, or never real: one message for all three.
            return RedirectResponse("/login?error=magic-link-invalid", status_code=303)
        conn.execute("UPDATE magic_links SET used_at = now() WHERE token_hash = %s", (digest,))
    resp = RedirectResponse("/", status_code=303)
    _create_session(row["user_id"], resp, request)
    return resp


# ── Preferences: the signed-in person's own account ─────────────────────────
#
# Distinct from Settings → Workspace, which is admin-owned and edits the
# workspace record. These three endpoints all act on `current_user` and never
# take a user id, so one account can never edit another's profile by guessing
# an id — the session IS the authorization.


class ProfileIn(BaseModel):
    name: str
    timezone: str = "UTC"


class PasswordIn(BaseModel):
    current: str
    next: str


class NotificationIn(BaseModel):
    key: str
    on: bool


# Per-user preferences that have no column on `users`. Kept in `settings`
# under a per-user key rather than widening the table: they are a JSON blob the
# server only ever reads back whole, and adding a column per switch would mean
# a migration every time the notification list changes.
def _prefs_key(user_id: int) -> str:
    return f"user_prefs:{user_id}"


NOTIFICATION_KEYS = {"mentions", "digest", "flowFailures"}
DEFAULT_NOTIFICATIONS = {"mentions": True, "digest": True, "flowFailures": True}


def _load_prefs(conn, user_id: int) -> dict:
    row = conn.execute("SELECT value FROM settings WHERE key = %s", (_prefs_key(user_id),)).fetchone()
    value = row["value"] if row else {}
    if not isinstance(value, dict):
        value = json.loads(value or "{}")
    notifications = {**DEFAULT_NOTIFICATIONS, **(value.get("notifications") or {})}
    return {"timezone": str(value.get("timezone") or "UTC"), "notifications": notifications}


def _save_prefs(conn, user_id: int, prefs: dict) -> None:
    conn.execute(
        """INSERT INTO settings (key, value) VALUES (%s, %s)
           ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""",
        (_prefs_key(user_id), json.dumps(prefs)))


@router.get("/preferences")
def get_preferences(request: Request):
    u = require_user(request)
    with _conn() as conn:
        prefs = _load_prefs(conn, u["id"])
    return {
        "name": u["name"],
        "email": u["email"],
        "initials": u["initials"],
        "role": u["role"],
        "joined": u["joined"].isoformat() if u["joined"] else "",
        # `provider` decides whether the page offers a password form at all.
        # A GitHub account has no password here to change, and rendering the
        # form would be rendering something that cannot work.
        "provider": "password" if u["password_hash"] else (u["provider"] or "password"),
        "timezone": prefs["timezone"],
        "notifications": prefs["notifications"],
    }


@router.post("/preferences/profile")
def save_profile(body: ProfileIn, request: Request):
    u = require_user(request)
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Display name cannot be empty.")
    with _conn() as conn:
        # `users.name` is UNIQUE, so a collision is a 400 with a real reason
        # rather than a 500 from the constraint.
        clash = conn.execute("SELECT 1 FROM users WHERE name = %s AND id <> %s", (name, u["id"])).fetchone()
        if clash:
            raise HTTPException(400, "Another member already uses that display name.")
        initials = "".join(p[0] for p in name.split()[:2]).upper() or name[:2].upper()
        conn.execute("UPDATE users SET name = %s, initials = %s WHERE id = %s", (name, initials, u["id"]))
        prefs = _load_prefs(conn, u["id"])
        prefs["timezone"] = body.timezone or "UTC"
        _save_prefs(conn, u["id"], prefs)
    return {"ok": True}


@router.post("/preferences/password")
def change_password(body: PasswordIn, request: Request):
    u = require_user(request)
    if not u["password_hash"]:
        raise HTTPException(400, "This account signs in with a provider and has no password here.")
    if not _verify(body.current, u["password_hash"]):
        raise HTTPException(400, "That is not your current password.")
    if len(body.next) < 8:
        raise HTTPException(400, "The new password must be at least 8 characters.")
    with _conn() as conn:
        conn.execute("UPDATE users SET password_hash = %s WHERE id = %s", (_hash(body.next), u["id"]))
        # Every other session was authenticated with the old credential. A
        # password change is what you do when you think someone else has it,
        # so those sessions end here; this one is kept so the page does not
        # log you out for succeeding.
        conn.execute("DELETE FROM sessions WHERE user_id = %s AND token <> %s",
                     (u["id"], request.cookies.get(COOKIE, "")))
    return {"ok": True}


@router.post("/preferences/notification")
def set_notification(body: NotificationIn, request: Request):
    u = require_user(request)
    if body.key not in NOTIFICATION_KEYS:
        raise HTTPException(400, f"Unknown notification setting: {body.key}")
    with _conn() as conn:
        prefs = _load_prefs(conn, u["id"])
        prefs["notifications"][body.key] = bool(body.on)
        _save_prefs(conn, u["id"], prefs)
    return {"ok": True}
