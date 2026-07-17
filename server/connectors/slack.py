"""Mari Cloud connector — Slack channel-history import (stdlib urllib, no deps).

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
import urllib.error
import urllib.parse
import urllib.request

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


class SlackError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


def _api(method: str, token: str, params: dict | None = None) -> dict:
    """POST a Slack Web API method (form-encoded). One retry on 429."""
    data = urllib.parse.urlencode(params or {}).encode()
    for attempt in (0, 1):
        req = urllib.request.Request(f"{API}/{method}", data=data, headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": UA,
        })
        try:
            with urllib.request.urlopen(req, timeout=30.0) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 0:
                try:
                    wait = min(float(e.headers.get("Retry-After") or 1), 30.0)
                except ValueError:
                    wait = 1.0
                time.sleep(wait)
                continue
            raise SlackError(f"Slack API HTTP {e.code} on {method}", e.code) from None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            raise SlackError(
                f"Slack unreachable: {getattr(e, 'reason', e).__class__.__name__}", 0) from None
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
    return out


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
            break
    return msgs


def list_items(config: dict, cursor: str | None) -> tuple[list[dict], str | None]:
    token = (config.get("bot_token") or "").strip()
    if not token:
        raise SlackError("bot token is required")
    wanted = {c.strip().lstrip("#").lower()
              for c in (config.get("channels") or "").split(",") if c.strip()}

    users = _user_map(token)
    items: list[dict] = []
    cursor_ts = float(cursor) if cursor else 0.0
    max_ts = cursor_ts

    # Fetch from the start of the cursor's (UTC) day, not the cursor itself:
    # day documents are rebuilt whole, so an incremental sync never overwrites
    # a day doc with only the messages that arrived since the last sync.
    oldest = None
    if cursor:
        day_start = datetime.datetime.fromtimestamp(cursor_ts, tz=datetime.timezone.utc)\
            .replace(hour=0, minute=0, second=0, microsecond=0)
        oldest = f"{day_start.timestamp():.6f}"

    for ch in _channels(token):
        name = ch.get("name", "")
        if not ch.get("is_member"):
            continue
        if wanted and name.lower() not in wanted:
            continue
        msgs = _channel_messages(token, ch["id"], oldest)
        # keep plain user messages only (no bot/system subtypes)
        keep = []
        for m in msgs:
            if m.get("type") != "message" or m.get("subtype") or m.get("bot_id"):
                continue
            if not (m.get("text") or "").strip():
                continue
            try:
                ts = float(m["ts"])
            except (KeyError, ValueError):
                continue
            keep.append((ts, m))
        keep.sort(key=lambda p: p[0])

        by_day: dict[str, list[tuple[float, dict]]] = {}
        for ts, m in keep:
            day = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)\
                .strftime("%Y-%m-%d")
            by_day.setdefault(day, []).append((ts, m))
            max_ts = max(max_ts, ts)

        for day, pairs in sorted(by_day.items()):
            if cursor and max(ts for ts, _ in pairs) <= cursor_ts:
                continue  # day unchanged since last sync — nothing to re-emit
            lines = []
            for ts, m in pairs:
                dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
                who = users.get(m.get("user", ""), m.get("user", "unknown"))
                lines.append(f"{dt.strftime('%H:%M')} @{who}: {_clean_text(m.get('text', ''), users)}")
            day_dt = datetime.datetime.strptime(day, "%Y-%m-%d")
            latest = max(ts for ts, _ in pairs)
            items.append({
                "path": f"slack/{name}/{day}",
                "title": f"#{name} — {day_dt.strftime('%b %d')}",
                "body": "\n".join(lines),
                "updated_at": datetime.datetime.fromtimestamp(
                    latest, tz=datetime.timezone.utc).isoformat(),
                "hash_hint": f"{latest:.6f}",
            })

    new_cursor = f"{max_ts:.6f}" if max_ts else cursor
    return items, new_cursor
