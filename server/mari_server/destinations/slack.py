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
import time
import urllib.error
import urllib.request
import uuid

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from mari_server.identity import routes as auth
from mari_server.persistence.postgres import document_index
from mari_server.identity import access
from mari_server import settings as config
from mari_server.providers import connectors as component_connectors
from mari_server.providers import models as llm
from mari_server.search.service import hybrid_search, invalidate_search
from mari_components.connectors import SlackConfig, fetch_slack_thread_by_id
from mari_components.connectors.events import (
    verify_hmac_sha256 as component_verify_hmac_sha256,
    verify_slack_signature as component_verify_slack_signature,
)
from mari_components import KnowledgeDocument
from mari_components.destinations.chat import answer_search_query
from mari_components.knowledge import answer_question as component_answer_question
from mari_server.persistence.postgres.event_inbox import DEFAULT_INBOX, EventDispatcher
from mari_server.persistence.postgres import bots as bot_store

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
    return bot_store.setting(key)


def merge_setting(key: str, patch: dict) -> None:
    """Shallow-merge patch into settings[key], creating the row if absent."""
    bot_store.merge_setting(key, patch)


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _log_usage(kind: str, detail: str = "") -> None:
    """Honest-telemetry hook (contract §A). Tolerates db.log_usage not existing yet."""
    try:
        bot_store.log_usage(kind, detail)
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


def _bot_system(selected_workflow: dict | None) -> str:
    from mari_server.conversations.workflows import guidance
    return BOT_SYSTEM + guidance(selected_workflow)


def answer_question(question: str, supplemental_context: str = "") -> str:
    """Hybrid search + strict, evidence-preserving component answer recipe."""
    caller_access = access.require_current_access()
    # Curated answers beat generation (same canon as /chat).
    qvec = llm.embed(question)
    if qvec:
        approved = bot_store.approved_answer(qvec)
        if approved and approved["sim"] >= 0.62:
            return f"{approved['answer']}\n\n_Approved answer · served verbatim_"

    from mari_server.conversations.workflows import retrieval_query, select
    selected_workflow = select(question, {"search"})
    docs = hybrid_search(retrieval_query(answer_search_query(question), selected_workflow), 8)[:4]
    knowledge = [
        KnowledgeDocument(
            f"document:{d.get('id') or d.get('external_id') or index}",
            d["title"], d["body"] or d["snippet"],
            revision=str(d.get("updated") or ""),
            metadata={"source": d["source"]},
        )
        for index, d in enumerate(docs, 1)
    ]
    facts = bot_store.verified_facts()
    if facts:
        knowledge.append(KnowledgeDocument(
            "verified-facts", "Verified facts", "\n".join(f"- {claim}" for claim in facts),
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
        generate_json=lambda prompt, _version: llm.generate_json(prompt, system=_bot_system(selected_workflow)),
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


def stream_answer_question(question: str, supplemental_context: str = ""):
    """Yield a grounded Slack answer as the model produces it."""
    qvec = llm.embed(question)
    if qvec:
        approved = bot_store.approved_answer(qvec)
        if approved and approved["sim"] >= 0.62:
            yield f"{approved['answer']}\n\n_Approved answer · served verbatim_"
            return

    from mari_server.conversations.workflows import retrieval_query, select
    selected_workflow = select(question, {"search"})
    docs = hybrid_search(retrieval_query(answer_search_query(question), selected_workflow), 8)[:4]
    facts = bot_store.verified_facts()
    if not docs and not facts:
        yield "I couldn't find enough product knowledge to answer that yet."
        return

    sections = [
        f"[{index}] {row['title']} ({row['source']})\n{row['body'] or row['snippet']}"
        for index, row in enumerate(docs, 1)
    ]
    if facts:
        sections.append("Verified facts:\n" + "\n".join(f"- {claim}" for claim in facts))
    if supplemental_context:
        sections.append("Slack conversation so far:\n" + supplemental_context)
    prompt = "Context:\n" + "\n\n".join(sections) + f"\n\nQuestion: {question}"

    emitted = False
    for token in llm.chat_stream([{"role": "user", "content": prompt}], _bot_system(selected_workflow)):
        if token:
            emitted = True
            yield token
    if not emitted:
        yield "The language model is temporarily unavailable. Please try again shortly."
        return
    if docs:
        yield "\n\nSources: " + " · ".join(
            f"[{index}] {row['title']}" for index, row in enumerate(docs, 1)
        )


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
      - channels:read
      - channels:history
      - chat:write
      - groups:history
      - groups:read
      - im:history
      - im:read
      - im:write
      - mpim:history
      - mpim:read
      - users:read
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
        bot_store.touch_installation(installation_id)
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
    from mari_server.sources import sync as ingest

    sources = bot_store.slack_sources(project_id)
    if not sources:
        return
    document, complete = fetch_slack_thread_by_id(
        SlackConfig(token), channel, thread_ts, http=component_connectors.http_transport,
    )
    if not complete:
        raise RuntimeError("Slack thread response was incomplete")
    if document is None:
        return
    max_tokens, overlap = document_index.chunk_settings()
    for source in sources:
        with document_index.connection() as conn:
            path = document.external_id
            content_hash = document.revision or document_index.content_hash(
                f"{document.title}\n\n{document.body}"
            )
            doc_id, _inserted = document_index.upsert_document(
                conn, source["id"], f"slack:{source['id']}:{path}", document.title,
                document.body, f"slack/{path}", "page", content_hash, "Slack",
                source="slack", initials="SL", acl_visibility="restricted",
                acl_principals=(f"channel:{channel}",),
                source_updated_at=document.updated_at,
            )
            document_index.sync_chunks(conn, doc_id, document.title, document.body,
                                max_tokens, overlap)
            cfg = source["config"] if isinstance(source["config"], dict) else json.loads(source["config"] or "{}")
            hashes = dict(cfg.get("item_hashes") or {})
            hashes[path] = content_hash
            cfg["item_hashes"] = hashes
            conn.commit()
            bot_store.save_source_config(source["id"], cfg)
    invalidate_search(project_id)


def _process_slack_delivery(row: dict) -> None:
    envelope = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
    installation_id = int(envelope["installation_id"])
    installation = bot_store.installation(installation_id, row["project_id"])
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
    client_msg_id = str(uuid.uuid5(uuid.NAMESPACE_URL,
                                   f"mari:slack:{row['project_id']}:{row['delivery_id']}"))
    pending = {"channel": channel, "text": "Searching product knowledge…",
               "client_msg_id": client_msg_id}
    if thread_ts:
        pending["thread_ts"] = root_ts
    posted = slack_call("chat.postMessage", token, pending)
    if not posted.get("ok"):
        raise RuntimeError(f"chat.postMessage: {posted.get('error', 'unknown error')}")
    bot_message_ts = str(posted.get("ts") or "")
    if not bot_message_ts:
        raise RuntimeError("chat.postMessage returned no message timestamp")

    turns: list[dict[str, str]] = []
    if thread_ts:
        thread = bot_store.thread(installation_id, installation["project_id"], channel, thread_ts) or {}
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
    parts: list[str] = []
    visible = ""
    last_update = time.monotonic()
    with access.use_access(project_access):
        for piece in stream_answer_question(question, context):
            parts.append(str(piece))
            answer_so_far = "".join(parts)[:4000]
            now = time.monotonic()
            if len(answer_so_far) - len(visible) >= 160 or now - last_update >= 0.75:
                updated = slack_call("chat.update", token, {
                    "channel": channel, "ts": bot_message_ts, "text": answer_so_far,
                })
                if not updated.get("ok"):
                    raise RuntimeError(f"chat.update: {updated.get('error', 'unknown error')}")
                visible = answer_so_far
                last_update = now
    answer = "".join(parts)[:4000]
    if answer != visible:
        updated = slack_call("chat.update", token, {
            "channel": channel, "ts": bot_message_ts, "text": answer,
        })
        if not updated.get("ok"):
            raise RuntimeError(f"chat.update: {updated.get('error', 'unknown error')}")
    _log_usage("chat_answer", "slack")
    participation_ts = thread_ts or bot_message_ts
    turns = _with_turn(turns, "assistant", answer, bot_message_ts)
    bot_store.save_thread(installation_id, installation["project_id"], channel,
                          participation_ts, bot_message_ts, turns)
    bot_store.touch_installation(installation_id, {"last_event_at": _now_iso(), "last_error": ""})
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
        from mari_server.sources.gdrive_events import process_gdrive_delivery
        from mari_server.sources import provider_events
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
    installation = bot_store.installation_by_team(team_id)
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
            bot_store.thread_exists(installation["id"], installation["project_id"],
                                    event.get("channel"), event.get("thread_ts"))
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
    slack, gh_installation, repos = bot_store.status(project_id)
    gh = {**bot_store.setting("github_bot"), **gh_installation}
    env_secret = (config.get("github", "webhook_secret") or "").strip()
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
        installation_id = bot_store.configure_slack(project_id, team_id, config_patch)
    except ValueError as error:
        raise HTTPException(409, str(error)) from None
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, "That Slack workspace was connected concurrently; retry setup.") from None
    return {"ok": True, "team": team_name, "teamId": team_id,
            "botUser": bot_user, "installationId": installation_id}


# ————————————————— POST /bots/slack/test —————————————————


@router.post("/bots/slack/test")
def slack_test(
    current: access.AccessContext = Depends(auth.require_capability("destination.manage")),
) -> dict:
    project_id = current.project_id
    row = bot_store.project_slack(project_id)
    cfg = (row or {}).get("config") or {}
    token = (cfg.get("bot_token") or "").strip()
    if not token:
        return {"ok": False, "error": "no bot token configured"}
    out = slack_call("auth.test", token)
    if not out.get("ok"):
        return {"ok": False, "error": out.get("error", "unknown error")}
    bot_store.touch_installation(row["id"], {"team_name": out.get("team", ""), "connected_at": _now_iso()})
    return {"ok": True, "team": out.get("team", ""), "botUser": out.get("user", "")}
