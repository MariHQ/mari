"""Enterprise identity: generic OIDC and SCIM 2.0 provisioning.

The module is deliberately separate from the existing password/GitHub/Google
auth implementation. OIDC identities are keyed by immutable issuer+subject;
SCIM deactivation revokes sessions synchronously before returning.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import urllib.parse
import urllib.request
from typing import Any

import jwt
import psycopg
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from psycopg.rows import dict_row

from mari_server.identity import routes as auth
from mari_server import settings as config
from mari_server.persistence.postgres import control_store

router = APIRouter()
OIDC_STATE = "mari_oidc_state"
OIDC_NONCE = "mari_oidc_nonce"
OIDC_VERIFIER = "mari_oidc_verifier"


def _conn():
    from mari_server.persistence.postgres import connection as postgres
    return postgres.connect()


def _json_request(url: str, *, data: dict | None = None, headers: dict | None = None) -> dict:
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    request = urllib.request.Request(url, data=body, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read())
    except Exception as error:
        raise HTTPException(502, f"Identity provider request failed: {type(error).__name__}") from None


def discovery() -> dict:
    issuer = str(config.get("auth", "oidc_issuer", "")).rstrip("/")
    if not issuer.startswith("https://"):
        raise HTTPException(503, "OIDC issuer is not configured with HTTPS.")
    document = _json_request(f"{issuer}/.well-known/openid-configuration")
    if document.get("issuer", "").rstrip("/") != issuer:
        raise HTTPException(502, "OIDC discovery issuer mismatch.")
    for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not str(document.get(field, "")).startswith("https://"):
            raise HTTPException(502, f"OIDC discovery returned an invalid {field}.")
    return document


def _pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


def verify_id_token(token: str, document: dict, nonce: str, jwks: dict | None = None) -> dict:
    """Verify signature and bind issuer, audience, expiry and callback nonce."""
    client_id = str(config.get("auth", "oidc_client_id", ""))
    issuer = str(document["issuer"]).rstrip("/")
    try:
        if jwks is None:
            signing_key = jwt.PyJWKClient(document["jwks_uri"]).get_signing_key_from_jwt(token).key
        else:
            kid = jwt.get_unverified_header(token).get("kid")
            candidates = [key for key in jwks.get("keys", []) if key.get("kid") == kid]
            if len(candidates) != 1:
                raise jwt.InvalidKeyError("signing key not found")
            signing_key = jwt.PyJWK.from_dict(candidates[0]).key
        claims = jwt.decode(token, signing_key, algorithms=["RS256", "ES256"],
                            audience=client_id, issuer=issuer,
                            options={"require": ["exp", "iat", "iss", "aud", "sub", "nonce"]},
                            leeway=30)
    except jwt.PyJWTError as error:
        raise HTTPException(401, f"OIDC token verification failed: {type(error).__name__}") from None
    if not nonce or not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise HTTPException(401, "OIDC nonce mismatch.")
    return claims


def _provider(issuer: str) -> str:
    return "oidc:" + hashlib.sha256(issuer.rstrip("/").encode()).hexdigest()[:24]


def _audit(conn, actor: str, verb: str, target: str, detail: list[tuple[str, str]] = ()) -> None:
    conn.execute("INSERT INTO events (actor, verb, target, detail) VALUES (%s, %s, %s, %s)",
                 (actor, verb, target, json.dumps([{"label": k, "value": v} for k, v in detail])))


def _unique_name(conn, proposed: str) -> str:
    base = (proposed or "Enterprise user").strip()[:100]
    candidate = base
    for n in range(2, 100):
        if not conn.execute("SELECT 1 FROM users WHERE name = %s", (candidate,)).fetchone():
            return candidate
        candidate = f"{base} ({n})"
    raise HTTPException(409, "Could not allocate a unique account name.")


def link_oidc_identity(claims: dict, issuer: str) -> dict:
    """Resolve immutable issuer+sub, linking by email only when verified."""
    subject = str(claims.get("sub", "")).strip()
    email = str(claims.get("email", "")).strip().lower()
    verified = claims.get("email_verified") is True
    if not subject:
        raise HTTPException(401, "OIDC subject is missing.")
    provider = _provider(issuer)
    with _conn() as conn:
        linked = conn.execute("""SELECT u.* FROM external_identities e JOIN users u ON u.id=e.user_id
                                 WHERE e.provider=%s AND e.subject=%s""", (provider, subject)).fetchone()
        if linked:
            if linked.get("status") != "active":
                raise HTTPException(403, "Account is deactivated.")
            return linked
        if not email or not verified:
            raise HTTPException(403, "A provider-verified email is required for first sign-in.")
        user = conn.execute("SELECT * FROM users WHERE lower(email)=%s", (email,)).fetchone()
        if user:
            other = conn.execute("SELECT subject FROM external_identities WHERE user_id=%s AND provider=%s",
                                 (user["id"], provider)).fetchone()
            if other:
                raise HTTPException(409, "This account is already linked to another OIDC subject.")
        else:
            name = _unique_name(conn, str(claims.get("name") or email.split("@", 1)[0]))
            initials = "".join(x[0].upper() for x in name.split()[:2]) or "??"
            row = conn.execute("""INSERT INTO users (name, initials, email, role, provider, status)
                                  VALUES (%s,%s,%s,'user','oidc','active') RETURNING *""",
                               (name, initials, email)).fetchone()
            user = row
        try:
            conn.execute("""INSERT INTO external_identities(user_id,provider,subject,email)
                            VALUES (%s,%s,%s,%s)""", (user["id"], provider, subject, email))
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, "OIDC identity was linked concurrently; retry sign-in.") from None
        _audit(conn, user["name"], "linked enterprise identity", provider,
               [("Issuer", issuer), ("Subject hash", hashlib.sha256(subject.encode()).hexdigest()[:12])])
        return user


def _role_map() -> dict:
    value = config.get("auth", "oidc_group_role_map", {}) or {}
    return value if isinstance(value, dict) else {}


def sync_group_roles(user: dict, groups: list[str], origin: str = "oidc") -> None:
    mapping = _role_map()
    desired: dict[int, str] = {}
    with _conn() as conn:
        for group in groups:
            rule = mapping.get(group)
            if not isinstance(rule, dict):
                continue
            project = conn.execute("SELECT id FROM projects WHERE slug=%s AND status='active'",
                                   (str(rule.get("project", "")),)).fetchone()
            role = str(rule.get("role", "member"))
            if project and role in {"owner", "admin", "manager", "member", "user", "viewer"}:
                desired[project["id"]] = role
        existing = conn.execute("SELECT project_id FROM enterprise_managed_memberships WHERE user_id=%s AND origin=%s",
                                (user["id"], origin)).fetchall()
        for row in existing:
            if row["project_id"] not in desired:
                conn.execute("UPDATE project_members SET status='disabled' WHERE project_id=%s AND user_id=%s",
                             (row["project_id"], user["id"]))
                conn.execute("DELETE FROM enterprise_managed_memberships WHERE project_id=%s AND user_id=%s AND origin=%s",
                             (row["project_id"], user["id"], origin))
                _audit(conn, user["name"], "removed enterprise project role", str(row["project_id"]))
        for project_id, role in desired.items():
            conn.execute("""INSERT INTO project_members(project_id,user_id,role,status) VALUES (%s,%s,%s,'active')
                            ON CONFLICT(project_id,user_id) DO UPDATE SET role=EXCLUDED.role,status='active'""",
                         (project_id, user["id"], role))
            conn.execute("""INSERT INTO enterprise_managed_memberships(project_id,user_id,origin)
                            VALUES (%s,%s,%s) ON CONFLICT DO NOTHING""", (project_id, user["id"], origin))
            _audit(conn, user["name"], "assigned enterprise project role", str(project_id), [("Role", role)])


@router.get("/auth/oidc")
def oidc_start(request: Request):
    document = discovery()
    client_id = str(config.get("auth", "oidc_client_id", ""))
    if not client_id:
        raise HTTPException(503, "OIDC client is not configured.")
    state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    redirect_uri = f"{config.get('auth', 'oauth_redirect_base')}/auth/oidc/callback"
    params = {"client_id": client_id, "response_type": "code", "redirect_uri": redirect_uri,
              "scope": config.get("auth", "oidc_scopes", "openid email profile groups"),
              "state": state, "nonce": nonce, "code_challenge": _pkce_challenge(verifier),
              "code_challenge_method": "S256"}
    response = RedirectResponse(document["authorization_endpoint"] + "?" + urllib.parse.urlencode(params))
    for name, value in ((OIDC_STATE, state), (OIDC_NONCE, nonce), (OIDC_VERIFIER, verifier)):
        response.set_cookie(name, value, httponly=True, secure=auth._is_https(request), samesite="lax", max_age=600)
    return response


@router.get("/auth/oidc/callback")
def oidc_callback(request: Request, code: str, state: str):
    expected = request.cookies.get(OIDC_STATE, "")
    if not state or not expected or not secrets.compare_digest(state, expected):
        raise HTTPException(400, "OIDC state mismatch.")
    nonce, verifier = request.cookies.get(OIDC_NONCE, ""), request.cookies.get(OIDC_VERIFIER, "")
    if not nonce or not verifier:
        raise HTTPException(400, "OIDC transaction expired; restart sign-in.")
    document = discovery()
    redirect_uri = f"{config.get('auth', 'oauth_redirect_base')}/auth/oidc/callback"
    tokens = _json_request(document["token_endpoint"], data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
        "client_id": config.get("auth", "oidc_client_id", ""),
        "client_secret": config.get("auth", "oidc_client_secret", ""), "code_verifier": verifier})
    if not tokens.get("id_token"):
        raise HTTPException(401, "OIDC provider returned no ID token.")
    claims = verify_id_token(tokens["id_token"], document, nonce)
    user = link_oidc_identity(claims, document["issuer"])
    sync_group_roles(user, [str(x) for x in claims.get("groups", []) if isinstance(x, str)])
    response = RedirectResponse(config.get("auth", "app_url", "http://localhost:5173/"))
    auth._create_session(user["id"], response, request, verb="signed in with OIDC")
    for name in (OIDC_STATE, OIDC_NONCE, OIDC_VERIFIER): response.delete_cookie(name)
    return response


def _scim_auth(authorization: str | None) -> None:
    expected = str(config.get("auth", "scim_bearer_token", ""))
    supplied = (authorization or "").removeprefix("Bearer ")
    if not expected or not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(401, "SCIM bearer token is invalid.", headers={"WWW-Authenticate": "Bearer"})


def _scim_user(row: dict) -> dict:
    return {"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"], "id": str(row["id"]),
            "externalId": row.get("external_id") or "", "userName": row.get("email") or "",
            "displayName": row.get("name") or "", "active": row.get("status") == "active",
            "emails": [{"value": row.get("email") or "", "primary": True}]}


def _email(payload: dict) -> str:
    values = payload.get("emails") or []
    return str(payload.get("userName") or (values[0].get("value") if values else "") or "").strip().lower()


def provision_user(payload: dict) -> dict:
    external = str(payload.get("externalId") or "").strip()
    email, active = _email(payload), payload.get("active", True) is not False
    if not external or not email:
        raise HTTPException(400, "SCIM User requires immutable externalId and userName/email.")
    with _conn() as conn:
        linked = conn.execute("""SELECT u.*, e.subject AS external_id FROM external_identities e
                                 JOIN users u ON u.id=e.user_id WHERE e.provider='scim' AND e.subject=%s""",
                              (external,)).fetchone()
        if linked:
            user = linked
            conn.execute("UPDATE users SET name=%s,status=%s WHERE id=%s",
                         (str(payload.get("displayName") or user["name"]), "active" if active else "disabled", user["id"]))
        else:
            takeover = conn.execute("SELECT id FROM users WHERE lower(email)=%s", (email,)).fetchone()
            if takeover:
                raise HTTPException(409, "Email already belongs to an account not linked to this SCIM externalId.")
            name = _unique_name(conn, str(payload.get("displayName") or email.split("@", 1)[0]))
            initials = "".join(x[0].upper() for x in name.split()[:2]) or "??"
            user = conn.execute("""INSERT INTO users(name,initials,email,role,provider,status)
                                  VALUES (%s,%s,%s,'user','scim',%s) RETURNING *""",
                                (name, initials, email, "active" if active else "disabled")).fetchone()
            conn.execute("INSERT INTO external_identities(user_id,provider,subject,email) VALUES (%s,'scim',%s,%s)",
                         (user["id"], external, email))
        if not active:
            conn.execute("UPDATE project_members SET status='disabled' WHERE user_id=%s", (user["id"],))
            control_store.revoke_user_sessions(user["id"])
        _audit(conn, "SCIM", "provisioned user" if active else "deactivated user", email,
               [("External id", external)])
        result = conn.execute("""SELECT u.*, e.subject AS external_id FROM users u JOIN external_identities e
                                 ON e.user_id=u.id AND e.provider='scim' WHERE u.id=%s""", (user["id"],)).fetchone()
        return _scim_user(result)


def deprovision_user(user_id: int) -> None:
    with _conn() as conn:
        user = conn.execute("SELECT * FROM users WHERE id=%s", (user_id,)).fetchone()
        if not user: raise HTTPException(404, "SCIM User not found.")
        # Blank the password too: a disabled account must not keep a working
        # credential waiting for a membership to be re-enabled.
        conn.execute("UPDATE users SET status='disabled', password_hash='' WHERE id=%s", (user_id,))
        conn.execute("UPDATE project_members SET status='disabled' WHERE user_id=%s", (user_id,))
        control_store.revoke_user_sessions(user_id)
        _audit(conn, "SCIM", "deactivated user", user["email"])


def _filter(text: str) -> tuple[str, str] | None:
    import re
    match = re.fullmatch(r'\s*(userName|externalId)\s+eq\s+"([^"\\]+)"\s*', text or "", re.I)
    if not match: return None
    return match.group(1).lower(), match.group(2)


@router.get("/scim/v2/Users")
def scim_users(filter: str = "", authorization: str | None = Header(None)):
    _scim_auth(authorization)
    parsed = _filter(filter)
    if filter and not parsed: raise HTTPException(400, "Unsupported SCIM filter.")
    sql = """SELECT u.*, e.subject AS external_id FROM users u JOIN external_identities e
             ON e.user_id=u.id AND e.provider='scim'"""
    args: tuple = ()
    if parsed:
        field, value = parsed
        sql += " WHERE lower(u.email)=%s" if field == "username" else " WHERE e.subject=%s"
        args = (value.lower() if field == "username" else value,)
    with _conn() as conn: rows = conn.execute(sql + " ORDER BY u.id", args).fetchall()
    resources = [_scim_user(row) for row in rows]
    return {"schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
            "totalResults": len(resources), "startIndex": 1, "itemsPerPage": len(resources), "Resources": resources}


@router.post("/scim/v2/Users", status_code=201)
async def scim_create_user(request: Request, authorization: str | None = Header(None)):
    _scim_auth(authorization); return provision_user(await request.json())


@router.get("/scim/v2/Users/{user_id}")
def scim_get_user(user_id: int, authorization: str | None = Header(None)):
    _scim_auth(authorization)
    with _conn() as conn:
        row = conn.execute("""SELECT u.*,e.subject AS external_id FROM users u JOIN external_identities e
                             ON e.user_id=u.id AND e.provider='scim' WHERE u.id=%s""", (user_id,)).fetchone()
    if not row: raise HTTPException(404, "SCIM User not found.")
    return _scim_user(row)


@router.put("/scim/v2/Users/{user_id}")
async def scim_replace_user(user_id: int, request: Request, authorization: str | None = Header(None)):
    _scim_auth(authorization)
    payload = await request.json()
    with _conn() as conn:
        row = conn.execute("SELECT subject FROM external_identities WHERE provider='scim' AND user_id=%s", (user_id,)).fetchone()
    if not row: raise HTTPException(404, "SCIM User not found.")
    if payload.get("externalId") and payload["externalId"] != row["subject"]:
        raise HTTPException(409, "SCIM externalId is immutable.")
    payload["externalId"] = row["subject"]
    return provision_user(payload)


@router.patch("/scim/v2/Users/{user_id}")
async def scim_patch_user(user_id: int, request: Request, authorization: str | None = Header(None)):
    _scim_auth(authorization); payload = await request.json()
    active = None
    for op in payload.get("Operations", []):
        if str(op.get("path", "")).lower() == "active": active = op.get("value") is not False
        elif isinstance(op.get("value"), dict) and "active" in op["value"]: active = op["value"]["active"] is not False
    if active is False: deprovision_user(user_id)
    elif active is True:
        with _conn() as conn:
            conn.execute("UPDATE users SET status='active' WHERE id=%s", (user_id,))
            user = conn.execute("SELECT email FROM users WHERE id=%s", (user_id,)).fetchone()
            if not user: raise HTTPException(404, "SCIM User not found.")
            _audit(conn, "SCIM", "reactivated user", user["email"])
    return scim_get_user(user_id, f"Bearer {config.get('auth','scim_bearer_token','')}")


@router.delete("/scim/v2/Users/{user_id}", status_code=204)
def scim_delete_user(user_id: int, authorization: str | None = Header(None)):
    _scim_auth(authorization); deprovision_user(user_id); return Response(status_code=204)


def provision_group(payload: dict) -> dict:
    external, name = str(payload.get("externalId") or "").strip(), str(payload.get("displayName") or "").strip()
    if not external or not name: raise HTTPException(400, "SCIM Group requires externalId and displayName.")
    rule = _role_map().get(name, {})
    with _conn() as conn:
        project = conn.execute("SELECT id FROM projects WHERE slug=%s", (str(rule.get("project", "")),)).fetchone() if rule else None
        row = conn.execute("""INSERT INTO enterprise_groups(external_id,display_name,project_id,role)
                            VALUES (%s,%s,%s,%s) ON CONFLICT(external_id) DO UPDATE
                            SET display_name=EXCLUDED.display_name,project_id=EXCLUDED.project_id,
                                role=EXCLUDED.role,active=true,updated_at=now() RETURNING *""",
                           (external, name, project["id"] if project else None, str(rule.get("role", "member")))).fetchone()
        _audit(conn, "SCIM", "provisioned group", name)
        return {"schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"], "id": str(row["id"]),
                "externalId": external, "displayName": name, "members": []}


@router.post("/scim/v2/Groups", status_code=201)
async def scim_create_group(request: Request, authorization: str | None = Header(None)):
    _scim_auth(authorization); return provision_group(await request.json())


def _scim_group(row: dict, members: list[int]) -> dict:
    return {"schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
            "id": str(row["id"]), "externalId": row["external_id"],
            "displayName": row["display_name"],
            "members": [{"value": str(value)} for value in sorted(members)]}


@router.get("/scim/v2/Groups")
def scim_groups(filter: str = "", authorization: str | None = Header(None)):
    _scim_auth(authorization)
    import re
    parsed = re.fullmatch(r'\s*(displayName|externalId)\s+eq\s+"([^"\\]+)"\s*', filter or "", re.I)
    if filter and not parsed: raise HTTPException(400, "Unsupported SCIM group filter.")
    sql, args = "SELECT * FROM enterprise_groups WHERE active", ()
    if parsed:
        sql += " AND display_name=%s" if parsed.group(1).lower() == "displayname" else " AND external_id=%s"
        args = (parsed.group(2),)
    with _conn() as conn:
        rows = conn.execute(sql + " ORDER BY id", args).fetchall()
        resources = [_scim_group(row, [m["user_id"] for m in conn.execute(
            "SELECT user_id FROM enterprise_group_members WHERE group_id=%s", (row["id"],)).fetchall()])
                     for row in rows]
    return {"schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
            "totalResults": len(resources), "startIndex": 1, "itemsPerPage": len(resources),
            "Resources": resources}


@router.get("/scim/v2/Groups/{group_id}")
def scim_get_group(group_id: int, authorization: str | None = Header(None)):
    _scim_auth(authorization)
    with _conn() as conn:
        row = conn.execute("SELECT * FROM enterprise_groups WHERE id=%s AND active", (group_id,)).fetchone()
        if not row: raise HTTPException(404, "SCIM Group not found.")
        members = [m["user_id"] for m in conn.execute(
            "SELECT user_id FROM enterprise_group_members WHERE group_id=%s", (group_id,)).fetchall()]
    return _scim_group(row, members)


@router.patch("/scim/v2/Groups/{group_id}")
async def scim_patch_group(group_id: int, request: Request, authorization: str | None = Header(None)):
    _scim_auth(authorization); payload = await request.json()
    with _conn() as conn:
        group = conn.execute("SELECT * FROM enterprise_groups WHERE id=%s", (group_id,)).fetchone()
        if not group: raise HTTPException(404, "SCIM Group not found.")
        members = {int(row["user_id"]) for row in conn.execute("SELECT user_id FROM enterprise_group_members WHERE group_id=%s", (group_id,)).fetchall()}
        prior_members = set(members)
        for op in payload.get("Operations", []):
            values = op.get("value", []) if isinstance(op.get("value"), list) else []
            ids = {int(v["value"]) for v in values if str(v.get("value", "")).isdigit()}
            action = str(op.get("op", "")).lower()
            members = members - ids if action == "remove" else members | ids
        removed = prior_members - members
        conn.execute("DELETE FROM enterprise_group_members WHERE group_id=%s", (group_id,))
        for user_id in removed:
            if group.get("project_id"):
                conn.execute("DELETE FROM enterprise_managed_memberships WHERE project_id=%s AND user_id=%s AND origin=%s",
                             (group["project_id"], user_id, f"scim-group:{group_id}"))
                remaining = conn.execute("SELECT 1 FROM enterprise_managed_memberships WHERE project_id=%s AND user_id=%s",
                                         (group["project_id"], user_id)).fetchone()
                if not remaining:
                    conn.execute("UPDATE project_members SET status='disabled' WHERE project_id=%s AND user_id=%s",
                                 (group["project_id"], user_id))
                _audit(conn, "SCIM", "removed group project role", group["display_name"],
                       [("User id", str(user_id)), ("Project id", str(group["project_id"]))])
        for user_id in members:
            conn.execute("INSERT INTO enterprise_group_members(group_id,user_id) VALUES (%s,%s)", (group_id, user_id))
            if group.get("project_id"):
                conn.execute("""INSERT INTO project_members(project_id,user_id,role,status) VALUES (%s,%s,%s,'active')
                                ON CONFLICT(project_id,user_id) DO UPDATE SET role=EXCLUDED.role,status='active'""",
                             (group["project_id"], user_id, group["role"]))
                conn.execute("""INSERT INTO enterprise_managed_memberships(project_id,user_id,origin)
                                VALUES (%s,%s,%s) ON CONFLICT DO NOTHING""",
                             (group["project_id"], user_id, f"scim-group:{group_id}"))
        _audit(conn, "SCIM", "updated group membership", group["display_name"], [("Members", str(len(members)))])
    return {"schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"], "id": str(group_id),
            "externalId": group["external_id"], "displayName": group["display_name"],
            "members": [{"value": str(value)} for value in sorted(members)]}


@router.delete("/scim/v2/Groups/{group_id}", status_code=204)
def scim_delete_group(group_id: int, authorization: str | None = Header(None)):
    _scim_auth(authorization)
    with _conn() as conn:
        group = conn.execute("SELECT * FROM enterprise_groups WHERE id=%s AND active", (group_id,)).fetchone()
        if not group: raise HTTPException(404, "SCIM Group not found.")
        members = conn.execute("SELECT user_id FROM enterprise_group_members WHERE group_id=%s", (group_id,)).fetchall()
        for member in members:
            if group.get("project_id"):
                conn.execute("DELETE FROM enterprise_managed_memberships WHERE project_id=%s AND user_id=%s AND origin=%s",
                             (group["project_id"], member["user_id"], f"scim-group:{group_id}"))
                remaining = conn.execute("SELECT 1 FROM enterprise_managed_memberships WHERE project_id=%s AND user_id=%s",
                                         (group["project_id"], member["user_id"])).fetchone()
                if not remaining:
                    conn.execute("UPDATE project_members SET status='disabled' WHERE project_id=%s AND user_id=%s",
                                 (group["project_id"], member["user_id"]))
        conn.execute("DELETE FROM enterprise_group_members WHERE group_id=%s", (group_id,))
        conn.execute("UPDATE enterprise_groups SET active=false,updated_at=now() WHERE id=%s", (group_id,))
        _audit(conn, "SCIM", "deprovisioned group", group["display_name"],
               [("Members removed", str(len(members)))])
    return Response(status_code=204)
