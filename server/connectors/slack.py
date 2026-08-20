"""Mari connector — Slack channel-history import (stdlib urllib, no deps).

Bot-token import of public-channel history. Messages are grouped into one Item
per channel per day: path "slack/<channel>/<YYYY-MM-DD>", body lines
"HH:MM @user: text". Bot/system subtypes are skipped. Cursor = latest message
ts seen (Slack ts string). Re-syncs fetch from the START of the cursor's day —
not the cursor itself — so a day document is always rebuilt complete; only
days that actually contain a message newer than the cursor are re-emitted.

Rate limits: Slack answers 429 + Retry-After; _api sleeps and retries once.
All HTTP goes through _api so tests can monkeypatch it.
"""

from __future__ import annotations

import datetime
import json
import re
import time
import urllib.parse

from . import _net
from ._protocol import ACLMetadata, PollResult

PROVIDER = {
    "key": "slack",
    "name": "Slack",
    "blurb": "Channel history from the public channels your bot is in.",
    "fields": [
        {"key": "bot_token", "label": "Bot token", "secret": True,
         "placeholder": "xoxb-…",
         "help": "Slack app → OAuth & Permissions → Bot User OAuth Token "
                 "(scopes: channels:read, channels:history, users:read)."},
        {"key": "channels", "label": "Channels", "secret": False,
         "placeholder": "general, engineering (blank = all public channels the bot is in)",
         "help": "Comma-separated channel names; leave blank for every public channel "
                 "the bot is a member of."},
    ],
    "docs_url": "https://api.slack.com/tutorials/tracks/getting-a-token",
}

API = "https://slack.com/api"
UA = "mari-cloud-sync"
HISTORY_PAGE = 200
MAX_HISTORY_PAGES = 10   # per channel per sync
MAX_LIST_PAGES = 10
EDIT_LOOKBACK_DAYS = 7


class _PagedList(list):
    """List-compatible page result carrying whether a provider cap was hit."""

    def __init__(self, values=(), *, complete: bool = True):
        super().__init__(values)
        self.complete = complete


class SlackError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


def _api(method: str, token: str, params: dict | None = None) -> dict:
    """POST a Slack Web API method (form-encoded). One retry on 429."""
    data = urllib.parse.urlencode(params or {}).encode()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": UA,
    }
    for attempt in (0, 1):
        # Through the shared SSRF guard (AUTH-11), like every connector.
        try:
            resp = _net.fetch(f"{API}/{method}", method="POST", data=data,
                              headers=headers, timeout=30.0)
        except _net.Blocked as e:
            raise SlackError(f"Slack: refused to fetch {API}/{method} — {e}", 0) from None
        except _net.NetworkError as e:
            raise SlackError(f"Slack unreachable: {e}", 0) from None
        if resp.status == 429 and attempt == 0:
            try:
                wait = min(float(_net.header(resp.headers, "Retry-After") or 1), 30.0)
            except ValueError:
                wait = 1.0
            time.sleep(wait)
            continue
        if resp.status >= 400:
            raise SlackError(f"Slack API HTTP {resp.status} on {method}", resp.status)
        try:
            return json.loads(resp.body)
        except json.JSONDecodeError:
            raise SlackError(f"Slack: non-JSON response on {method}", resp.status) from None
    raise SlackError(f"Slack rate limited on {method} (retried once)", 429)


def _call(method: str, token: str, params: dict | None = None) -> dict:
    """_api + Slack's ok/error envelope. Rate-limit errors sleep-and-retry once."""
    data = _api(method, token, params)
    if not data.get("ok"):
        err = data.get("error", "unknown_error")
        if err == "ratelimited":
            time.sleep(2.0)
            data = _api(method, token, params)
            if data.get("ok"):
                return data
            err = data.get("error", "unknown_error")
        raise SlackError(f"Slack API error on {method}: {err}")
    return data


# ————— contract surface —————

def validate(config: dict) -> str | None:
    token = (config.get("bot_token") or "").strip()
    if not token:
        return "bot token is required"
    try:
        data = _api("auth.test", token)
    except SlackError as e:
        return str(e)
    if not data.get("ok"):
        return f"Slack rejected the token: {data.get('error', 'unknown_error')}"
    return None


def _user_map(token: str) -> dict[str, str]:
    users: dict[str, str] = {}
    cursor = ""
    for _ in range(MAX_LIST_PAGES):
        params: dict = {"limit": 200}
        if cursor:
            params["cursor"] = cursor
        try:
            data = _call("users.list", token, params)
        except SlackError:
            return users  # names degrade to raw ids; not fatal
        for u in data.get("members", []):
            prof = u.get("profile") or {}
            name = (prof.get("display_name") or prof.get("real_name")
                    or u.get("real_name") or u.get("name") or u.get("id", ""))
            if u.get("id"):
                users[u["id"]] = name
        cursor = (data.get("response_metadata") or {}).get("next_cursor") or ""
        if not cursor:
            break
    return users


def _channels(token: str) -> list[dict]:
    out: list[dict] = []
    cursor = ""
    for _ in range(MAX_LIST_PAGES):
        params: dict = {"types": "public_channel", "exclude_archived": "true", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        data = _call("conversations.list", token, params)
        out.extend(data.get("channels", []))
        cursor = (data.get("response_metadata") or {}).get("next_cursor") or ""
        if not cursor:
            break
    return _PagedList(out, complete=not bool(cursor))


_MENTION_RE = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]*)?>")
_LINK_RE = re.compile(r"<(https?://[^>|]+)(?:\|([^>]*))?>")


def _clean_text(text: str, users: dict[str, str]) -> str:
    text = _MENTION_RE.sub(lambda m: "@" + users.get(m.group(1), m.group(1)), text)
    text = _LINK_RE.sub(lambda m: m.group(2) or m.group(1), text)
    text = text.replace("<!channel>", "@channel").replace("<!here>", "@here")
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").strip()


def _channel_messages(token: str, channel_id: str, oldest: str | None) -> list[dict]:
    msgs: list[dict] = []
    cursor = ""
    complete = False
    for _ in range(MAX_HISTORY_PAGES):
        params: dict = {"channel": channel_id, "limit": HISTORY_PAGE}
        if oldest:
            params["oldest"] = oldest
        if cursor:
            params["cursor"] = cursor
        data = _call("conversations.history", token, params)
        msgs.extend(data.get("messages", []))
        cursor = (data.get("response_metadata") or {}).get("next_cursor") or ""
        if not data.get("has_more") or not cursor:
            complete = not data.get("has_more")
            break
    return _PagedList(msgs, complete=complete)


def _thread_replies(token: str, channel_id: str, thread_ts: str) -> _PagedList:
    replies: list[dict] = []
    cursor = ""
    complete = False
    for _ in range(MAX_HISTORY_PAGES):
        params = {"channel": channel_id, "ts": thread_ts, "limit": HISTORY_PAGE}
        if cursor:
            params["cursor"] = cursor
        data = _call("conversations.replies", token, params)
        rows = data.get("messages") or []
        replies.extend(rows[1:] if not cursor else rows)  # omit repeated root
        cursor = (data.get("response_metadata") or {}).get("next_cursor") or ""
        if not data.get("has_more") or not cursor:
            complete = not data.get("has_more")
            break
    return _PagedList(replies, complete=complete)


def _effective_ts(message: dict) -> float:
    values = [message.get("ts"), (message.get("edited") or {}).get("ts"), message.get("latest_reply")]
    parsed = []
    for value in values:
        try:
            parsed.append(float(value))
        except (TypeError, ValueError):
            pass
    return max(parsed) if parsed else 0.0


def thread_item(token: str, channel_id: str, thread_ts: str) -> dict:
    """Refetch one canonical thread aggregate for event-driven refreshes.

    Polling remains the repair path. Webhook workers use this narrow function
    as a dirty hint so retrieval sees a new reply immediately instead of
    waiting for the next scheduled poll.
    """
    data = _call("conversations.replies", token, {
        "channel": channel_id, "ts": thread_ts, "limit": HISTORY_PAGE,
    })
    messages = list(data.get("messages") or [])
    if data.get("has_more") or (data.get("response_metadata") or {}).get("next_cursor"):
        replies = _thread_replies(token, channel_id, thread_ts)
        root = messages[:1]
        messages = root + list(replies)
        if not replies.complete:
            raise SlackError("Slack thread exceeded the retrieval safety cap")
    users = _user_map(token)
    kept: list[tuple[float, dict]] = []
    for message in messages:
        if message.get("subtype") or not (message.get("text") or "").strip():
            continue
        try:
            ts = float(message["ts"])
        except (KeyError, TypeError, ValueError):
            continue
        kept.append((ts, message))
    if not kept:
        raise SlackError("Slack returned no readable messages for the thread")
    kept.sort(key=lambda pair: pair[0])
    lines = []
    for ts, message in kept:
        who = users.get(message.get("user", ""), message.get("user") or
                        ("Mari" if message.get("bot_id") else "unknown"))
        timestamp = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
        lines.append(f"{timestamp.strftime('%Y-%m-%d %H:%M')} @{who}: "
                     f"{_clean_text(message.get('text', ''), users)}")
    latest = max(_effective_ts(message) for _, message in kept)
    root_text = _clean_text(kept[0][1].get("text", ""), users)
    return {
        "path": f"thread/{channel_id}/{thread_ts}",
        "title": (root_text[:120] or f"Slack thread {thread_ts}"),
        "body": "\n".join(lines),
        "updated_at": datetime.datetime.fromtimestamp(
            latest, tz=datetime.timezone.utc).isoformat(),
        "hash_hint": f"{latest:.6f}",
        "acl": ACLMetadata(visibility="restricted", principals=(f"channel:{channel_id}",)),
    }


def list_items(config: dict, cursor: str | None) -> PollResult:
    token = (config.get("bot_token") or "").strip()
    if not token:
        raise SlackError("bot token is required")
    wanted = {c.strip().lstrip("#").lower()
              for c in (config.get("channels") or "").split(",") if c.strip()}

    users = _user_map(token)
    items: list[dict] = []
    tombstones: list[str] = []
    cursor_ts = float(cursor) if cursor else 0.0
    max_ts = cursor_ts

    # Fetch from the start of the cursor's (UTC) day, not the cursor itself:
    # day documents are rebuilt whole, so an incremental sync never overwrites
    # a day doc with only the messages that arrived since the last sync.
    oldest = None
    if cursor:
        day_start = datetime.datetime.fromtimestamp(cursor_ts, tz=datetime.timezone.utc)\
            .replace(hour=0, minute=0, second=0, microsecond=0) - datetime.timedelta(days=EDIT_LOOKBACK_DAYS)
        oldest = f"{day_start.timestamp():.6f}"

    channels = _channels(token)
    snapshot_complete = getattr(channels, "complete", True)
    for ch in channels:
        name = ch.get("name", "")
        if not ch.get("is_member"):
            continue
        if wanted and name.lower() not in wanted:
            continue
        msgs = _channel_messages(token, ch["id"], oldest)
        snapshot_complete = snapshot_complete and getattr(msgs, "complete", True)
        expanded = list(msgs)
        for message in list(msgs):
            if message.get("reply_count") and message.get("ts"):
                replies = _thread_replies(token, ch["id"], message["ts"])
                snapshot_complete = snapshot_complete and replies.complete
                expanded.extend(replies)
        # keep plain user messages only (no bot/system subtypes)
        keep = []
        changed_days: set[str] = set()
        for m in expanded:
            if m.get("subtype") == "message_deleted" and m.get("deleted_ts"):
                try:
                    deleted_ts = float(m["deleted_ts"])
                    changed_days.add(datetime.datetime.fromtimestamp(
                        deleted_ts, tz=datetime.timezone.utc).strftime("%Y-%m-%d"))
                    max_ts = max(max_ts, _effective_ts(m), deleted_ts)
                except (TypeError, ValueError):
                    pass
                continue
            if m.get("type") != "message" or m.get("subtype") or m.get("bot_id"):
                continue
            if not (m.get("text") or "").strip():
                continue
            try:
                ts = float(m["ts"])
            except (KeyError, TypeError, ValueError):
                continue
            effective = _effective_ts(m) or ts
            keep.append((ts, effective, m))
        keep.sort(key=lambda p: p[0])

        by_day: dict[str, list[tuple[float, float, dict]]] = {}
        for ts, effective, m in keep:
            day = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)\
                .strftime("%Y-%m-%d")
            by_day.setdefault(day, []).append((ts, effective, m))
            max_ts = max(max_ts, effective)

        for day, pairs in sorted(by_day.items()):
            if cursor and max(effective for _, effective, _ in pairs) <= cursor_ts and day not in changed_days:
                continue  # day unchanged since last sync — nothing to re-emit
            lines = []
            for ts, _effective, m in pairs:
                dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
                who = users.get(m.get("user", ""), m.get("user", "unknown"))
                prefix = "  ↳ " if m.get("thread_ts") and m.get("thread_ts") != m.get("ts") else ""
                lines.append(f"{prefix}{dt.strftime('%H:%M')} @{who}: {_clean_text(m.get('text', ''), users)}")
            day_dt = datetime.datetime.strptime(day, "%Y-%m-%d")
            latest = max(effective for _, effective, _ in pairs)
            items.append({
                "path": f"slack/{name}/{day}",
                "title": f"#{name} — {day_dt.strftime('%b %d')}",
                "body": "\n".join(lines),
                "updated_at": datetime.datetime.fromtimestamp(
                    latest, tz=datetime.timezone.utc).isoformat(),
                "hash_hint": f"{latest:.6f}",
                "acl": ACLMetadata(visibility="restricted", principals=(f"channel:{ch['id']}",)),
            })

        for day in changed_days - set(by_day):
            # History was rebuilt from the cursor-day lookback. If the deleted
            # message was the day's last remaining message, delete that day doc.
            tombstones.append(f"slack/{name}/{day}")

    new_cursor = f"{max_ts:.6f}" if max_ts else cursor
    return PollResult(items, new_cursor if snapshot_complete else cursor,
                      snapshot_complete=snapshot_complete, tombstones=tombstones)
