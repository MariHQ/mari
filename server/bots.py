"""Mari bots — Slack bot + GitHub webhook self-serve (BOTS-CONTRACT.md §B).

Routes (APIRouter, included from app.py):
  GET  /bots/slack/manifest   Slack app manifest YAML with this deployment's URLs
  POST /webhooks/slack        Slack Events API: challenge echo, v0 signature check,
                              answer app_mentions / DMs on a background thread
  GET  /bots/status           real observed state for the self-serve UI
  POST /bots/slack/test       auth.test with the stored bot token (never logged)

Settings keys (existing settings CRUD; jsonb values):
  slack_bot:  {bot_token, signing_secret, team_name, connected_at,
               last_event_at, last_error}
  github_bot: {webhook_secret, repo_events, last_delivery_at}
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import threading
import time
import urllib.error
import urllib.request

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import PlainTextResponse

import auth
import config
import llm
from db import exec_, q, q1
from queries import hybrid_search

router = APIRouter()

SLACK_API = "https://slack.com/api"


# ————————————————— settings helpers —————————————————


def get_setting(key: str) -> dict:
    row = q1("SELECT value FROM settings WHERE key = %s", (key,))
    if not row:
        return {}
    val = row["value"]
    if isinstance(val, str):
        try:
            val = json.loads(val)
        except json.JSONDecodeError:
            return {}
    return val if isinstance(val, dict) else {}


def merge_setting(key: str, patch: dict) -> None:
    """Shallow-merge patch into settings[key], creating the row if absent."""
    exec_(
        """INSERT INTO settings (key, value) VALUES (%s, %s)
           ON CONFLICT (key) DO UPDATE SET value = settings.value || EXCLUDED.value""",
        (key, json.dumps(patch)),
    )


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _log_usage(kind: str, detail: str = "") -> None:
    """Honest-telemetry hook (contract §A). Tolerates db.log_usage not existing yet."""
    try:
        import db as _db

        if hasattr(_db, "log_usage"):
            _db.log_usage(kind, detail)
        else:
            exec_("INSERT INTO usage_log (kind, detail) VALUES (%s, %s)", (kind, detail))
    except Exception:
        pass


# ————————————————— Slack API (urllib; tokens never logged) —————————————————


def slack_call(method: str, token: str, payload: dict | None = None) -> dict:
    req = urllib.request.Request(
        f"{SLACK_API}/{method}",
        data=json.dumps(payload or {}).encode(),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20.0) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        return {"ok": False, "error": f"request failed: {getattr(e, 'reason', e)}"}


# ————————————————— answer pipeline (same retrieval as /chat, non-streaming) —————————————————

BOT_SYSTEM = (
    "You are Mari, the team's knowledge assistant, answering in Slack. "
    "Answer from the provided context. Be concise (2-4 sentences), cite sources as [1], [2]. "
    "If the context doesn't cover it, say so."
)


def answer_question(question: str) -> str:
    """Hybrid search + LLM answer with the same deterministic fallback as /chat."""
    # Curated answers beat generation (same canon as /chat).
    qvec = llm.embed(question)
    if qvec:
        approved = q1(
            """SELECT question, answer, 1 - (embedding <=> %s::vector) AS sim
               FROM approved_answers
               WHERE status = 'approved' AND embedding IS NOT NULL
               ORDER BY embedding <=> %s::vector LIMIT 1""",
            (str(qvec), str(qvec)),
        )
        if approved and approved["sim"] >= 0.62:
            return f"{approved['answer']}\n\n_Approved answer · served verbatim_"

    docs = hybrid_search(question, 4)
    context = "\n\n".join(
        f"[{i + 1}] {d['title']} ({d['source']})\n{d['body'] or d['snippet']}"
        for i, d in enumerate(docs)
    )
    facts = q("SELECT claim FROM facts WHERE status = 'Verified' LIMIT 8")
    if facts:
        context += "\n\nVerified facts:\n" + "\n".join(f"- {f['claim']}" for f in facts)

    answer = llm.generate(f"Context:\n{context}\n\nQuestion: {question}", BOT_SYSTEM)
    if not answer:
        if docs:
            answer = (
                "I couldn't reach the local model, but hybrid search found: "
                + "; ".join(d["title"] for d in docs) + "."
            )
        else:
            answer = "I couldn't reach the local model and found no matching documents."
    if docs:
        answer += "\n\nSources: " + " · ".join(
            f"[{i + 1}] {d['title']}" for i, d in enumerate(docs)
        )
    return answer


# ————————————————— GET /bots/slack/manifest —————————————————

MANIFEST_TEMPLATE = """display_information:
  name: Mari
  description: Team knowledge assistant — @mention or DM it a question.
  background_color: "#1a1d21"
features:
  bot_user:
    display_name: Mari
    always_online: true
oauth_config:
  scopes:
    bot:
      - app_mentions:read
      - chat:write
      - im:history
      - im:read
      - im:write
settings:
  event_subscriptions:
    request_url: {base}/webhooks/slack
    bot_events:
      - app_mention
      - message.im
  interactivity:
    is_enabled: false
  org_deploy_enabled: false
  socket_mode_enabled: false
  token_rotation_enabled: false
"""


@router.get("/bots/slack/manifest", response_class=PlainTextResponse,
            dependencies=[Depends(auth.require_user)])
def slack_manifest() -> str:
    base = (config.get("auth", "oauth_redirect_base") or "http://localhost:8000").rstrip("/")
    return MANIFEST_TEMPLATE.format(base=base)


# ————————————————— POST /webhooks/slack —————————————————


def verify_slack_signature(raw: bytes, timestamp: str, signature: str, secret: str) -> bool:
    """Slack v0 request signing: hex hmac-sha256 of `v0:{ts}:{body}`, 5-min window."""
    if not (secret and timestamp and signature):
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs(time.time() - ts) > 300:
        return False
    base = f"v0:{timestamp}:".encode() + raw
    expect = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expect, signature)


def verify_github_signature(raw: bytes, signature: str, secrets: list[str]) -> bool:
    """Accept a GitHub sha256 delivery signed by any configured webhook secret."""
    return bool(secrets) and any(
        hmac.compare_digest(
            signature,
            "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest(),
        )
        for secret in secrets if secret
    )


def _handle_slack_event(event: dict, token: str) -> None:
    """Background worker: answer the question and post back into Slack."""
    error = ""
    try:
        text = (event.get("text") or "").strip()
        # Strip <@UBOT> mention tokens from app_mention text.
        while "<@" in text and ">" in text:
            i = text.find("<@")
            j = text.find(">", i)
            if j == -1:
                break
            text = (text[:i] + text[j + 1:]).strip()
        if not text:
            text = "What can you help with?"

        answer = answer_question(text)
        _log_usage("chat_answer", "slack")

        out = slack_call("chat.postMessage", token, {
            "channel": event.get("channel"),
            "text": answer,
            "thread_ts": event.get("thread_ts") or (
                event.get("ts") if event.get("type") == "app_mention" else None
            ),
        })
        if not out.get("ok"):
            error = f"chat.postMessage: {out.get('error', 'unknown error')}"
    except Exception as e:  # noqa: BLE001 — record, never crash the thread
        error = f"{type(e).__name__}: {e}"
    merge_setting("slack_bot", {"last_event_at": _now_iso(), "last_error": error})


@router.post("/webhooks/slack")
async def slack_webhook(request: Request):
    raw = await request.body()
    try:
        payload = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        return Response(status_code=400, content="bad payload")

    # URL verification happens before the app is fully configured; echo per Slack docs.
    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge", "")}

    cfg = get_setting("slack_bot")
    secret = (cfg.get("signing_secret") or "").strip()
    if not verify_slack_signature(
        raw,
        request.headers.get("X-Slack-Request-Timestamp", ""),
        request.headers.get("X-Slack-Signature", ""),
        secret,
    ):
        return Response(status_code=401, content="bad signature")

    if payload.get("type") == "event_callback":
        event = payload.get("event") or {}
        etype = event.get("type", "")
        is_mention = etype == "app_mention"
        is_dm = etype == "message" and event.get("channel_type") == "im"
        # Skip our own echoes and edits/joins (message_changed etc.).
        if (is_mention or is_dm) and not event.get("bot_id") and not event.get("subtype"):
            token = (cfg.get("bot_token") or "").strip()
            threading.Thread(
                target=_handle_slack_event, args=(event, token), daemon=True
            ).start()

    return {"ok": True}  # ack within 3s; work continues on the thread


# ————————————————— GET /bots/status —————————————————


@router.get("/bots/status", dependencies=[Depends(auth.require_user)])
def bots_status() -> dict:
    slack = get_setting("slack_bot")
    gh = get_setting("github_bot")
    env_secret = (config.get("github", "webhook_secret") or "").strip()
    repos = q(
        "SELECT id, config->>'repo' AS repo FROM sources "
        "WHERE kind = 'github' AND config->>'repo' IS NOT NULL ORDER BY id"
    )
    return {
        "slack": {
            "configured": bool(slack.get("bot_token") and slack.get("signing_secret")),
            "teamName": slack.get("team_name") or "",
            "lastEventAt": slack.get("last_event_at") or None,
            "lastError": slack.get("last_error") or None,
        },
        "github": {
            "webhookConfigured": bool(env_secret or (gh.get("webhook_secret") or "").strip()),
            "lastDeliveryAt": gh.get("last_delivery_at") or None,
            "sources": [{"id": r["id"], "repo": r["repo"]} for r in repos],
        },
    }


# ————————————————— POST /bots/slack/test —————————————————


@router.post("/bots/slack/test", dependencies=[Depends(auth.require_user)])
def slack_test() -> dict:
    token = (get_setting("slack_bot").get("bot_token") or "").strip()
    if not token:
        return {"ok": False, "error": "no bot token configured"}
    out = slack_call("auth.test", token)
    if not out.get("ok"):
        return {"ok": False, "error": out.get("error", "unknown error")}
    merge_setting("slack_bot", {"team_name": out.get("team", ""), "connected_at": _now_iso()})
    return {"ok": True, "team": out.get("team", ""), "botUser": out.get("user", "")}
