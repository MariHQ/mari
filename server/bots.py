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
import json
import urllib.error
import urllib.request
import uuid

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

import auth
import access
import config
from mari_server.infrastructure import connector_provider as component_connectors
import llm
from mari_components.connectors import SlackConfig, fetch_slack_thread_by_id
from mari_components.connectors.events import (
    verify_hmac_sha256 as component_verify_hmac_sha256,
    verify_slack_signature as component_verify_slack_signature,
)
from mari_components import KnowledgeDocument
from mari_components.knowledge import answer_question as component_answer_question
from event_inbox import DEFAULT_INBOX, EventDispatcher
from db import exec_, pq, pq1, q, q1
from queries import hybrid_search

router = APIRouter()

SLACK_API = "https://slack.com/api"
SLACK_WORKERS = max(1, int(config.get("bots", "slack_workers", 4)))
_EVENT_INBOX = DEFAULT_INBOX
_EVENT_DISPATCHER: EventDispatcher | None = None


class SlackSetupIn(BaseModel):
    bot_token: str
    signing_secret: str


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


def answer_question(question: str, supplemental_context: str = "") -> str:
    """Hybrid search + strict, evidence-preserving component answer recipe."""
    caller_access = access.require_current_access()
    # Curated answers beat generation (same canon as /chat).
    qvec = llm.embed(question)
    if qvec:
        approved = q1(
            """SELECT question, answer, 1 - (embedding <=> %s::vector) AS sim
               FROM approved_answers
               WHERE project_id = %s AND status = 'approved' AND embedding IS NOT NULL
               ORDER BY embedding <=> %s::vector LIMIT 1""",
            (str(qvec), caller_access.project_id, str(qvec)),
        )
        if approved and approved["sim"] >= 0.62:
            return f"{approved['answer']}\n\n_Approved answer · served verbatim_"

    docs = hybrid_search(question, 4)
    knowledge = [
        KnowledgeDocument(
            f"document:{d.get('id') or d.get('external_id') or index}",
            d["title"], d["body"] or d["snippet"],
            revision=str(d.get("updated") or ""),
            metadata={"source": d["source"]},
        )
        for index, d in enumerate(docs, 1)
    ]
    facts = pq("SELECT claim FROM facts WHERE project_id = %s AND status = 'Verified' LIMIT 8")
    if facts:
        knowledge.append(KnowledgeDocument(
            "verified-facts", "Verified facts", "\n".join(f"- {f['claim']}" for f in facts),
            revision="verified",
        ))

    if supplemental_context:
        knowledge.append(KnowledgeDocument(
            "slack-conversation", "Slack conversation so far", supplemental_context,
            revision="current-thread",
        ))
    result = component_answer_question(
        question,
        knowledge,
        generate_json=lambda prompt, _version: llm.generate_json(prompt, system=BOT_SYSTEM),
    )
    by_id = {document.external_id: document for document in knowledge}
    cited = []
    for evidence in result.evidence:
        title = by_id[evidence.document_id].title
        if title not in cited:
            cited.append(title)
    suffix = "\n\nSources: " + " · ".join(
        f"[{index + 1}] {title}" for index, title in enumerate(cited)) if cited else ""
    return result.answer + suffix


# ————————————————— GET /bots/slack/manifest —————————————————

MANIFEST_TEMPLATE = """display_information:
  name: Mari
  description: Team knowledge assistant — @mention or DM it a question.
  background_color: "#1a1d21"
features:
  app_home:
    home_tab_enabled: false
    messages_tab_enabled: true
    messages_tab_read_only_enabled: false
  bot_user:
    display_name: Mari
    always_online: true
oauth_config:
  scopes:
    bot:
      - app_mentions:read
      - channels:history
      - chat:write
      - im:history
      - im:read
      - im:write
settings:
  event_subscriptions:
    request_url: {base}/webhooks/slack
    bot_events:
      - app_mention
      - message.channels
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
    try:
        component_verify_slack_signature(raw, timestamp, signature, secret)
        return True
    except Exception:
        return False


def verify_github_signature(raw: bytes, signature: str, secrets: list[str]) -> bool:
    """Accept a GitHub sha256 delivery signed by any configured webhook secret."""
    for secret in secrets:
        try:
            component_verify_hmac_sha256(raw, signature, secret)
            return True
        except Exception:
            continue
    return False


def _strip_mentions(text: str) -> str:
    while "<@" in text and ">" in text:
        start = text.find("<@")
        end = text.find(">", start)
        if end < 0:
            break
        text = (text[:start] + text[end + 1:]).strip()
    return text.strip()


def _handle_slack_event(event: dict, token: str, project_access=None,
                        installation_id: int | None = None,
                        event_id: str = "") -> None:
    """Compatibility entry point for direct/internal callers.

    Provider webhooks never call this function; they always use the durable
    inbox. Keeping the narrow helper avoids breaking local diagnostics.
    """
    question = _strip_mentions((event.get("text") or "").strip()) or "What can you help with?"
    if project_access is None:
        answer = answer_question(question)
    else:
        with access.use_access(project_access):
            answer = answer_question(question)
    out = slack_call("chat.postMessage", token, {
        "channel": event.get("channel"), "text": answer,
        "thread_ts": event.get("thread_ts") or None,
    })
    if not out.get("ok"):
        raise RuntimeError(f"chat.postMessage: {out.get('error', 'unknown error')}")
    if installation_id is not None:
        exec_("UPDATE bot_installations SET updated_at=now() WHERE id=%s", (installation_id,))
    else:
        merge_setting("slack_bot", {"last_event_at": _now_iso(), "last_error": ""})


def _slack_root_message(token: str, channel: str, thread_ts: str) -> str:
    """Read the root message with the bot-token-compatible history API."""
    out = slack_call("conversations.history", token, {
        "channel": channel, "oldest": thread_ts, "latest": thread_ts,
        "inclusive": True, "limit": 15,
    })
    if not out.get("ok"):
        raise RuntimeError(f"conversations.history: {out.get('error', 'unknown error')}")
    messages = out.get("messages") or []
    return str(messages[0].get("text") or "").strip() if messages else ""


def _conversation(value) -> list[dict[str, str]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if not isinstance(value, list):
        return []
    return [turn for turn in value if isinstance(turn, dict)
            and turn.get("role") in {"user", "assistant"} and turn.get("text")]


def _with_turn(turns: list[dict[str, str]], role: str, text: str, ts: str) -> list[dict[str, str]]:
    kept = [dict(turn) for turn in turns if str(turn.get("ts") or "") != ts]
    kept.append({"role": role, "text": text.strip(), "ts": ts})
    return kept[-40:]


def _conversation_context(turns: list[dict[str, str]]) -> str:
    names = {"user": "User", "assistant": "Mari"}
    return "\n".join(f"{names[turn['role']]}: {turn['text']}" for turn in turns)


def _refresh_slack_aggregate(project_id: int, token: str, channel: str,
                             thread_ts: str) -> None:
    """Refetch the canonical thread and update every matching Slack source."""
    import ingest

    sources = q(
        """SELECT id, config FROM sources
             WHERE project_id=%s AND kind='connector'
               AND split_part(provider, ':', 1)='slack' AND status='active'""",
        (project_id,),
    )
    if not sources:
        return
    document, complete = fetch_slack_thread_by_id(
        SlackConfig(token), channel, thread_ts, http=component_connectors._http,
    )
    if not complete:
        raise RuntimeError("Slack thread response was incomplete")
    if document is None:
        return
    max_tokens, overlap = ingest._chunk_settings()
    for source in sources:
        with ingest._conn() as conn:
            path = document.external_id
            content_hash = document.revision or ingest._sha(
                f"{document.title}\n\n{document.body}"
            )
            doc_id, _inserted = ingest._upsert_document(
                conn, source["id"], f"slack:{source['id']}:{path}", document.title,
                document.body, f"slack/{path}", "page", content_hash, "Slack",
                source="slack", initials="SL", acl_visibility="restricted",
                acl_principals=(f"channel:{channel}",),
            )
            ingest._sync_chunks(conn, doc_id, document.title, document.body,
                                max_tokens, overlap)
            cfg = source["config"] if isinstance(source["config"], dict) else json.loads(source["config"] or "{}")
            hashes = dict(cfg.get("item_hashes") or {})
            hashes[path] = content_hash
            cfg["item_hashes"] = hashes
            conn.execute("UPDATE sources SET config=%s, last_sync_at=now() WHERE id=%s",
                         (json.dumps(cfg), source["id"]))
            conn.commit()


def _process_slack_delivery(row: dict) -> None:
    envelope = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
    installation_id = int(envelope["installation_id"])
    installation = q1(
        """SELECT b.*, p.slug AS project_slug, p.name AS project_name
             FROM bot_installations b JOIN projects p ON p.id=b.project_id
            WHERE b.id=%s AND b.project_id=%s AND b.provider='slack'
              AND b.status='connected' AND p.status='active'""",
        (installation_id, row["project_id"]),
    )
    if not installation:
        raise RuntimeError("Slack installation is no longer active")
    cfg = installation["config"] if isinstance(installation["config"], dict) else json.loads(installation["config"])
    token = (cfg.get("bot_token") or "").strip()
    event = envelope["event"]
    channel = str(event.get("channel") or "")
    event_ts = str(event.get("ts") or "")
    thread_ts = str(event.get("thread_ts") or "")
    root_ts = thread_ts or event_ts
    if not token or not channel or not root_ts:
        raise RuntimeError("Slack delivery is missing token, channel, or timestamp")

    project_access = access.external_access(
        installation["project_id"], installation["project_slug"], installation["project_name"],
        "slack", str(installation_id), frozenset({"knowledge.read"}),
        frozenset({f"channel:{channel}"}),
    )
    question = _strip_mentions((event.get("text") or "").strip()) or "What can you help with?"
    turns: list[dict[str, str]] = []
    if thread_ts:
        thread = q1(
            """SELECT conversation FROM slack_bot_threads
                WHERE installation_id=%s AND project_id=%s
                  AND channel_id=%s AND thread_ts=%s""",
            (installation_id, installation["project_id"], channel, thread_ts),
        ) or {}
        turns = _conversation(thread.get("conversation"))
        # Rows created before durable conversation storage still have their
        # visible Mari root in Slack. Bootstrap it once via conversations.history,
        # which bot tokens may use for public channels.
        if not turns:
            root_text = _slack_root_message(token, channel, thread_ts)
            if root_text:
                turns = _with_turn(turns, "assistant", root_text, thread_ts)
    turns = _with_turn(turns, "user", question, event_ts)
    context = _conversation_context(turns)
    with access.use_access(project_access):
        answer = answer_question(question, context)
    _log_usage("chat_answer", "slack")
    client_msg_id = str(uuid.uuid5(uuid.NAMESPACE_URL,
                                   f"mari:slack:{row['project_id']}:{row['delivery_id']}"))
    post = {"channel": channel, "text": answer, "client_msg_id": client_msg_id}
    # Root mentions and DMs get a normal visible reply. If a user starts a
    # thread from Mari's response, subsequent turns stay in that thread.
    if thread_ts:
        post["thread_ts"] = root_ts
    out = slack_call("chat.postMessage", token, post)
    if not out.get("ok"):
        raise RuntimeError(f"chat.postMessage: {out.get('error', 'unknown error')}")
    bot_message_ts = str(out.get("ts") or "")
    participation_ts = thread_ts or bot_message_ts
    turns = _with_turn(turns, "assistant", answer, bot_message_ts)
    exec_(
        """INSERT INTO slack_bot_threads
               (installation_id, project_id, channel_id, thread_ts, bot_message_ts, conversation)
             VALUES (%s, %s, %s, %s, %s, %s)
             ON CONFLICT (installation_id, channel_id, thread_ts) DO UPDATE
               SET bot_message_ts=EXCLUDED.bot_message_ts,
                   conversation=EXCLUDED.conversation, last_event_at=now()""",
        (installation_id, installation["project_id"], channel, participation_ts,
         bot_message_ts, json.dumps(turns)),
    )
    exec_("""UPDATE bot_installations SET config=config || %s, updated_at=now() WHERE id=%s""",
          (json.dumps({"last_event_at": _now_iso(), "last_error": ""}), installation_id))
    # Event-driven ingestion is a repair/latency optimization, never a gate on
    # answering the user. Once a real Slack thread exists, refresh it after the
    # response has been posted and let scheduled polling repair any API error.
    if thread_ts:
        try:
            _refresh_slack_aggregate(installation["project_id"], token, channel, root_ts)
        except Exception:
            pass


def start_event_dispatcher() -> None:
    global _EVENT_DISPATCHER
    if _EVENT_DISPATCHER is None:
        from gdrive_events import process_gdrive_delivery
        import provider_events
        _EVENT_DISPATCHER = EventDispatcher(
            _EVENT_INBOX,
            {"slack": _process_slack_delivery, "gdrive": process_gdrive_delivery,
             **provider_events.HANDLERS},
            workers=SLACK_WORKERS,
        )
    _EVENT_DISPATCHER.start()


def stop_event_dispatcher() -> None:
    global _EVENT_DISPATCHER
    if _EVENT_DISPATCHER is not None:
        _EVENT_DISPATCHER.stop()
        _EVENT_DISPATCHER = None


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

    team_id = str(payload.get("team_id") or (payload.get("team") or {}).get("id") or "")
    installation = q1("""SELECT b.*, p.slug AS project_slug, p.name AS project_name
                           FROM bot_installations b JOIN projects p ON p.id = b.project_id
                          WHERE b.provider = 'slack' AND b.external_team_id = %s
                            AND b.status = 'connected' AND p.status = 'active'""", (team_id,))
    if not installation:
        return Response(status_code=401, content="unknown Slack team")
    cfg = installation.get("config") or {}
    if isinstance(cfg, str):
        cfg = json.loads(cfg)
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
        is_thread_reply = bool(
            etype == "message" and event.get("thread_ts") and
            q1("""SELECT 1 FROM slack_bot_threads
                   WHERE installation_id=%s AND project_id=%s
                     AND channel_id=%s AND thread_ts=%s""",
               (installation["id"], installation["project_id"], event.get("channel"),
                event.get("thread_ts")))
        )
        # Skip our own echoes and edits/joins (message_changed etc.).
        if (is_mention or is_dm or is_thread_reply) and not event.get("bot_id") and not event.get("subtype"):
            event_id = str(payload.get("event_id") or "").strip()
            if not event_id:
                # Slack normally provides event_id.  The deterministic fallback
                # keeps older/test payloads idempotent without trusting text.
                identity = json.dumps({"team": team_id, "event": event},
                                      sort_keys=True, separators=(",", ":"))
                event_id = "derived:" + hashlib.sha256(identity.encode()).hexdigest()
            root_ts = str(event.get("thread_ts") or event.get("ts") or "")
            try:
                _row_id, inserted = _EVENT_INBOX.enqueue(
                    "slack", installation["project_id"], event_id,
                    {"installation_id": installation["id"], "event": event},
                    coalesce_key=f"{installation['id']}:{event.get('channel', '')}:{root_ts}",
                )
            except Exception:
                # No ACK when durability is unavailable: Slack will retry.
                return Response(status_code=503, content="Slack delivery could not be persisted")
            if not inserted:
                return {"ok": True, "duplicate": True}

    return {"ok": True}  # ack within 3s; work continues on the thread


# ————————————————— GET /bots/status —————————————————


@router.get("/bots/status")
def bots_status(current: access.AccessContext = Depends(auth.require_project)) -> dict:
    project_id = current.project_id
    slack_row = q1("""SELECT config FROM bot_installations
                       WHERE project_id = %s AND provider = 'slack' AND status = 'connected'
                       ORDER BY id LIMIT 1""", (project_id,))
    gh_row = q1("""SELECT config FROM bot_installations
                    WHERE project_id = %s AND provider = 'github' AND status = 'connected'
                    ORDER BY id LIMIT 1""", (project_id,))
    slack = (slack_row or {}).get("config") or {}
    gh = (gh_row or {}).get("config") or {}
    env_secret = (config.get("github", "webhook_secret") or "").strip()
    repos = q(
        "SELECT id, config->>'repo' AS repo FROM sources "
        """WHERE project_id = %s AND kind = 'connector'
             AND split_part(provider, ':', 1) = 'github'
             AND config->>'repo' IS NOT NULL ORDER BY id""",
        (project_id,),
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


# ————————————————— POST /bots/slack/setup —————————————————


@router.post("/bots/slack/setup")
def slack_setup(
    body: SlackSetupIn,
    current: access.AccessContext = Depends(auth.require_capability("destination.manage")),
) -> dict:
    """Verify credentials and persist the installation webhook routing uses.

    `auth.test` is the identity proof: callers do not get to choose a team id,
    and failed credentials never replace a working installation.
    """
    token = body.bot_token.strip()
    signing_secret = body.signing_secret.strip()
    if not token.startswith("xoxb-") or len(token) > 500:
        raise HTTPException(400, "A Slack bot token beginning with xoxb- is required.")
    if not signing_secret or len(signing_secret) > 500:
        raise HTTPException(400, "A Slack signing secret is required.")
    verified = slack_call("auth.test", token)
    if not verified.get("ok"):
        raise HTTPException(400, f"Slack rejected the bot token: {verified.get('error', 'invalid_auth')}")
    team_id = str(verified.get("team_id") or "").strip()
    if not team_id:
        raise HTTPException(502, "Slack auth.test returned no team id.")
    team_name = str(verified.get("team") or "").strip()[:200]
    bot_user = str(verified.get("user") or "").strip()[:200]
    project_id = current.project_id
    config_patch = {
        "bot_token": token,
        "signing_secret": signing_secret,
        "team_name": team_name,
        "bot_user": bot_user,
        "connected_at": _now_iso(),
        "last_error": "",
    }
    try:
        with auth._conn() as conn:
            current = conn.execute(
                """SELECT id, external_team_id FROM bot_installations
                    WHERE project_id = %s AND provider = 'slack'
                    ORDER BY id LIMIT 1 FOR UPDATE""", (project_id,)).fetchone()
            owner = conn.execute(
                """SELECT id, project_id FROM bot_installations
                    WHERE provider = 'slack' AND external_team_id = %s
                      AND external_installation_id = '' FOR UPDATE""", (team_id,)).fetchone()
            if owner and owner["project_id"] != project_id:
                raise HTTPException(409, "That Slack workspace is already connected to another project.")
            if current:
                row = conn.execute(
                    """UPDATE bot_installations
                          SET external_team_id = %s, external_installation_id = '',
                              config = config || %s, status = 'connected', updated_at = now()
                        WHERE id = %s AND project_id = %s RETURNING id""",
                    (team_id, json.dumps(config_patch), current["id"], project_id)).fetchone()
            else:
                row = conn.execute(
                    """INSERT INTO bot_installations
                         (project_id, provider, external_team_id, external_installation_id, config, status)
                       VALUES (%s, 'slack', %s, '', %s, 'connected') RETURNING id""",
                    (project_id, team_id, json.dumps(config_patch))).fetchone()
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, "That Slack workspace was connected concurrently; retry setup.") from None
    return {"ok": True, "team": team_name, "teamId": team_id,
            "botUser": bot_user, "installationId": row["id"]}


# ————————————————— POST /bots/slack/test —————————————————


@router.post("/bots/slack/test")
def slack_test(
    current: access.AccessContext = Depends(auth.require_capability("destination.manage")),
) -> dict:
    project_id = current.project_id
    row = q1("""SELECT id, config FROM bot_installations
                 WHERE project_id = %s AND provider = 'slack' AND status = 'connected'
                 ORDER BY id LIMIT 1""", (project_id,))
    cfg = (row or {}).get("config") or {}
    token = (cfg.get("bot_token") or "").strip()
    if not token:
        return {"ok": False, "error": "no bot token configured"}
    out = slack_call("auth.test", token)
    if not out.get("ok"):
        return {"ok": False, "error": out.get("error", "unknown error")}
    exec_("""UPDATE bot_installations SET config = config || %s, updated_at = now()
             WHERE id = %s AND project_id = %s""",
          (json.dumps({"team_name": out.get("team", ""), "connected_at": _now_iso()}),
           row["id"], project_id))
    return {"ok": True, "team": out.get("team", ""), "botUser": out.get("user", "")}
