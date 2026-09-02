"""Slack channel/DM history and canonical thread document ingestion."""

from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
import json
import re
from typing import Any, Callable, Iterator, Mapping
import urllib.parse

from mari_components.connectors._shared import json_response
from mari_components.connectors.protocol import ValidationResult, classify_error
from mari_components.errors import AuthenticationFailure, PermanentFailure
from mari_components.http import HttpRequest, HttpTransport
from mari_components.types import DocumentACL, KnowledgeDocument, PollPage, PollRequest, Principal


API = "https://slack.com/api"
# "Test connection" lists channels at Slack's page maximum so the check is one
# request for almost every workspace, and a bounded handful for the largest.
VALIDATE_CHANNEL_PAGES = 5
VALIDATE_CHANNEL_PAGE_SIZE = 1000
# Slack channel IDs are an uppercase C (public) or G (private) followed by
# uppercase alphanumerics. Channel names are always lowercase, so "general"
# never matches while C0123ABCD does.
CHANNEL_ID_RE = re.compile(r"^[CG][A-Z0-9]+$")
MENTION_RE = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]*)?>")
LINK_RE = re.compile(r"<(https?://[^>|]+)(?:\|([^>]*))?>")


@dataclass(frozen=True, slots=True)
class SlackConfig:
    bot_token: str
    channels: tuple[str, ...] = ()
    history_token: str = ""

    def __post_init__(self) -> None:
        if not self.bot_token.strip():
            raise ValueError("Slack bot token is required")


def _call(
    token: str,
    method: str,
    params: Mapping[str, Any] | None,
    *,
    http: HttpTransport,
    tolerate: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """One Slack Web API call, raising unless the error is in ``tolerate``."""
    body = urllib.parse.urlencode(params or {}).encode()
    value = json_response(
        http,
        HttpRequest(
            "POST",
            f"{API}/{method}",
            {
                "Authorization": f"Bearer {token.strip()}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            body,
        ),
    )
    if not isinstance(value, dict):
        raise PermanentFailure(f"Slack returned invalid data for {method}")
    if not value.get("ok"):
        error = str(value.get("error") or "unknown_error")
        if error in tolerate:
            return value
        if error in {"invalid_auth", "not_authed", "token_revoked", "account_inactive"}:
            raise AuthenticationFailure(f"Slack rejected credentials: {error}")
        if error == "missing_scope" and value.get("needed"):
            # Slack names the scope it wanted; "missing_scope" alone sends the
            # admin back to the app manifest to guess.
            error = f"missing_scope (the install needs {value['needed']})"
        raise PermanentFailure(f"Slack API error on {method}: {error}")
    return value


def validate_slack(config: SlackConfig, *, http: HttpTransport) -> ValidationResult:
    """auth.test, then the same channel access check the poll makes.

    A token that authenticates but cannot read the configured channels used
    to pass here, get saved, and sync nothing behind a healthy card. Checking
    membership at "Test connection" puts the remediation in front of the
    person before the source exists.
    """
    try:
        value = _call(config.bot_token, "auth.test", None, http=http)
        channels, complete = _list_channels(
            config, http=http,
            page_limit=VALIDATE_CHANNEL_PAGES, page_size=VALIDATE_CHANNEL_PAGE_SIZE,
        )
        _require_channel_access(config, channels, complete=complete, http=http)
    except Exception as error:
        return ValidationResult(False, str(error), kind=classify_error(error).value)
    return ValidationResult(
        True,
        identity=str(value.get("team") or value.get("team_id") or value.get("user") or ""),
    )


def _paginate(
    token: str,
    method: str,
    params: Mapping[str, Any],
    *,
    http: HttpTransport,
    page_limit: int,
    collection: str,
    until: Callable[[list[dict]], bool] | None = None,
) -> tuple[list[dict], bool]:
    """Rows and whether the listing was exhausted.

    ``until`` sees the rows gathered so far after each page and stops the
    walk early when the caller already has everything it wanted. That early
    stop reports the listing as complete because nothing the caller cares
    about can be left beyond it.
    """
    rows: list[dict] = []
    cursor = ""
    for _ in range(page_limit):
        value = _call(
            token,
            method,
            {**params, **({"cursor": cursor} if cursor else {})},
            http=http,
        )
        rows.extend(item for item in value.get(collection) or [] if isinstance(item, dict))
        cursor = str((value.get("response_metadata") or {}).get("next_cursor") or "")
        if not cursor or (until is not None and until(rows)):
            return rows, True
    return rows, False


def _channel_keys(channels: list[dict]) -> set[str]:
    return {
        str(channel.get(field) or "").casefold()
        for channel in channels
        for field in ("name", "id")
    }


def _list_channels(
    config: SlackConfig, *, http: HttpTransport, page_limit: int, page_size: int = 200
) -> tuple[list[dict], bool]:
    """The channels the token can list, and whether the walk was exhausted.

    A filtered source stops paging as soon as every configured name or ID
    has appeared, so it stays cheap and never runs into the page cap on
    large workspaces. Only an unfiltered source walks the whole list.
    """
    wanted = set(_configured_channels(config))
    return _paginate(
        config.bot_token,
        "conversations.list",
        # DMs are bot conversation state, not shared product knowledge. Besides
        # leaking private conversations into the corpus, polling IM/MPIM IDs can
        # make an otherwise healthy source fail when Slack retains a stale DM
        # descriptor that conversations.history answers with channel_not_found.
        # Archived channels are listed so a configured one can be named as
        # archived instead of being mistaken for a private channel or a typo.
        # _require_channel_access skips them for reading.
        {"types": "public_channel,private_channel", "exclude_archived": "false", "limit": page_size},
        http=http,
        page_limit=page_limit,
        collection="channels",
        until=(lambda rows: wanted <= _channel_keys(rows)) if wanted else None,
    )


def _configured_channels(config: SlackConfig) -> dict[str, str]:
    # Accept either the channel name people see in Slack or the stable channel
    # ID Slack shows in "About -> Copy channel ID". IDs are especially useful
    # for private channels, where renames should not silently stop ingestion.
    # The casefolded key matches; the original spelling is kept for errors,
    # so an admin who typed C0123ABCD is not asked about c0123abcd.
    return {name.lstrip("#").casefold(): name.lstrip("#") for name in config.channels}


def _channel_info(config: SlackConfig, channel_id: str, *, http: HttpTransport) -> dict | None:
    """conversations.info for one channel ID, or None when Slack cannot see it.

    The listing omits private channels the app is not in, and a large
    workspace can push a channel past the page cap. conversations.info
    answers for any channel the token can see, with is_member and
    is_archived, so an ID is settled with one call. channel_not_found means
    the channel is private and the app is not a member, or the ID is wrong.
    """
    value = _call(
        config.bot_token, "conversations.info", {"channel": channel_id},
        http=http, tolerate=frozenset({"channel_not_found"}),
    )
    channel = value.get("channel") if value.get("ok") else None
    return channel if isinstance(channel, dict) else None


def _channel_list_for_error(names: list[str], limit: int = 3) -> str:
    # Bound the channel list (three names, thirty characters each) so the
    # card's 300-character error budget never truncates the remediation off
    # the end. A message that names more than one list shows fewer per list.
    return ", ".join(name[:30] for name in names[:limit]) + (
        f" and {len(names) - limit} more" if len(names) > limit else "")


NO_CHANNELS_MESSAGE = (
    "The app is not a member of any Slack channel, so there is nothing to sync. "
    "Invite the app to each channel it should read. A private channel also needs "
    "groups:read and groups:history (plus users:read)."
)

ARCHIVED_ONLY_MESSAGE = (
    "Every Slack channel the app is a member of is archived, so there is nothing "
    "to sync. Invite the app to each channel it should read."
)


def _unscanned_message(names: list[str]) -> str:
    """The failure for configured names the listing never reached.

    Membership can only be judged for a channel the listing returned. When
    the walk hit its page cap with names still unmatched, saying "invite the
    app" would send the admin chasing a channel that may be fine, and the
    outcome would differ between "Test connection" and the sync because each
    scans a different number of pages. IDs sidestep the listing entirely.
    """
    listed = _channel_list_for_error(names)
    return (f"This workspace has more channels than were scanned, so {listed} could not "
            "be checked. Configure each channel by its ID instead.")


def _access_message(not_member: list[str], archived: list[str], unlisted: list[str]) -> str:
    """The remediation for configured channels the token cannot read.

    A public channel the app has not joined shows up in conversations.list
    with is_member false, so the fix is an invite. An archived channel is
    listed but has nothing to sync, so the fix is to drop it. A channel Slack
    does not list at all is either private (the app must be a member, and the
    install needs the groups scopes) or misnamed, and the message says both.
    Each non-empty set gets its own sentence, and the per-sentence name cap
    shrinks with the number of sentences so the worst case stays under the
    card's 300-character budget.
    """
    groups = [names for names in (not_member, archived, unlisted) if names]
    if len(groups) == 1:
        listed = _channel_list_for_error(groups[0])
        if not_member:
            return (f"Slack could not access {listed} because the app is not a member. "
                    "Invite the app to each channel.")
        if archived:
            return f"{listed} {_are(archived)} archived. {_remove(archived)}"
        return (f"Slack could not access {listed}. Invite the app to each channel. "
                "A private channel also needs groups:read and groups:history (plus users:read). "
                "Still missing? Check the name or ID.")
    limit = 2 if len(groups) == 2 else 1
    sentences: list[str] = []
    if not_member:
        sentences.append(f"Invite the app to {_channel_list_for_error(not_member, limit)}.")
    if archived:
        sentences.append(
            f"{_channel_list_for_error(archived, limit)} {_are(archived)} archived. {_remove(archived)}")
    if unlisted:
        sentences.append(
            f"{_channel_list_for_error(unlisted, limit)} {_are(unlisted)} not listed. "
            "Check the name or ID. A private channel needs groups:read and groups:history.")
    return " ".join(sentences)


def _are(names: list[str]) -> str:
    return "are" if len(names) > 1 else "is"


def _remove(names: list[str]) -> str:
    return "Remove them from the channel list." if len(names) > 1 else "Remove it from the channel list."


def _require_channel_access(
    config: SlackConfig, channels: list[dict], *, complete: bool, http: HttpTransport
) -> list[dict]:
    """The channels a poll may read, or PermanentFailure naming the fix.

    Shared by validate and poll so "Test connection" refuses exactly what a
    sync would refuse, in the same words. ``complete`` says whether the
    listing was exhausted. A configured name the walk never reached is
    reported as unchecked rather than guessed at.
    """
    configured = _configured_channels(config)
    wanted = set(configured)
    matched: set[str] = set()
    not_member: set[str] = set()
    archived: set[str] = set()
    readable: list[dict] = []
    for channel in channels:
        name = str(channel.get("name") or "")
        channel_id = str(channel.get("id") or "")
        selected = wanted.intersection({name.casefold(), channel_id.casefold()})
        if wanted and not selected:
            continue
        if channel.get("is_archived"):
            archived.update(selected)
            continue
        if not channel.get("is_member"):
            not_member.update(selected)
            continue
        matched.update(selected)
        readable.append(channel)
    if not wanted:
        # No channel filter means "everything the app can see", and an app
        # invited nowhere sees nothing. Say so instead of syncing nothing.
        if not readable:
            member_of_archived = any(
                channel.get("is_archived") and channel.get("is_member") for channel in channels)
            raise PermanentFailure(ARCHIVED_ONLY_MESSAGE if member_of_archived else NO_CHANNELS_MESSAGE)
        return readable
    # Slack omits private channels the bot is not a member of from
    # conversations.list, and a public channel it has not joined fails the
    # is_member check above, so both land here. Returning a successful
    # empty snapshot in either case made the source card look healthy
    # forever at zero documents.
    unlisted = wanted - matched - not_member - archived
    for key in sorted(unlisted):
        spelling = configured[key]
        if not CHANNEL_ID_RE.match(spelling):
            continue
        channel = _channel_info(config, spelling, http=http)
        if channel is None:
            continue
        unlisted.discard(key)
        if channel.get("is_archived"):
            archived.add(key)
        elif not channel.get("is_member"):
            not_member.add(key)
        else:
            matched.add(key)
            readable.append(channel)
    if not complete:
        # Every ID above was settled by conversations.info. A name the capped
        # walk never reached is a different problem from a name Slack does
        # not list, and gets its own answer.
        unchecked = {key for key in unlisted if not CHANNEL_ID_RE.match(configured[key])}
        if unchecked:
            raise PermanentFailure(_unscanned_message(sorted(configured[key] for key in unchecked)))
    if not_member or archived or unlisted:
        raise PermanentFailure(_access_message(
            sorted(configured[key] for key in not_member),
            sorted(configured[key] for key in archived),
            sorted(configured[key] for key in unlisted),
        ))
    return readable


def _users(config: SlackConfig, request: PollRequest, *, http: HttpTransport) -> dict[str, str]:
    rows, _ = _paginate(
        config.bot_token,
        "users.list",
        {"limit": 200},
        http=http,
        page_limit=request.page_limit,
        collection="members",
    )
    return {
        str(user["id"]): str(
            (user.get("profile") or {}).get("display_name")
            or (user.get("profile") or {}).get("real_name")
            or user.get("name")
            or user["id"]
        )
        for user in rows
        if user.get("id")
    }


def _clean(text: str, users: Mapping[str, str]) -> str:
    value = MENTION_RE.sub(lambda match: "@" + users.get(match.group(1), match.group(1)), text)
    value = LINK_RE.sub(lambda match: match.group(2) or match.group(1), value)
    return (
        value.replace("<!channel>", "@channel")
        .replace("<!here>", "@here")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&amp;", "&")
        .strip()
    )


def _message_ts(message: Mapping[str, Any]) -> float:
    values = [message.get("ts"), (message.get("edited") or {}).get("ts"), message.get("latest_reply")]
    parsed: list[float] = []
    for value in values:
        try:
            parsed.append(float(value))
        except (TypeError, ValueError):
            pass
    return max(parsed) if parsed else 0.0


def _thread_document(
    channel: Mapping[str, Any], messages: list[dict], users: Mapping[str, str]
) -> KnowledgeDocument | None:
    readable = [
        message
        for message in messages
        if message.get("type", "message") == "message"
        # Provider/system events and bot replies are not human knowledge. In
        # particular, indexing Mari's own answers creates a retrieval feedback
        # loop that can crowd the original GitHub/Confluence evidence out.
        and message.get("subtype") not in {
            "message_deleted", "tombstone", "bot_message", "channel_join",
            "channel_leave", "channel_name", "channel_purpose", "channel_topic",
        }
        and not message.get("bot_id")
        and not message.get("app_id")
        and str(message.get("text") or "").strip()
    ]
    if not readable:
        return None
    readable.sort(key=lambda message: float(message.get("ts") or 0))
    root = readable[0]
    root_ts = str(root.get("thread_ts") or root.get("ts") or "")
    channel_id = str(channel.get("id") or "")
    channel_name = str(channel.get("name") or "").strip()
    lines: list[str] = []
    for message in readable:
        timestamp = dt.datetime.fromtimestamp(float(message["ts"]), tz=dt.timezone.utc)
        author = users.get(str(message.get("user") or ""), str(message.get("user") or "unknown"))
        lines.append(f"{timestamp:%Y-%m-%d %H:%M} @{author}: {_clean(str(message.get('text') or ''), users)}")
    latest = max(_message_ts(message) for message in readable)
    title = _clean(str(root.get("text") or ""), users)[:120] or f"Slack thread {root_ts}"
    return KnowledgeDocument(
        f"thread:{channel_id}:{root_ts}",
        title,
        "\n".join(lines),
        revision=f"{latest:.6f}",
        updated_at=dt.datetime.fromtimestamp(latest, tz=dt.timezone.utc).isoformat(),
        source_url=f"https://slack.com/archives/{channel_id}/p{root_ts.replace('.', '')}",
        acl=DocumentACL("restricted", (Principal("channel", channel_id),)),
        metadata={"channel": channel_id, "channel_name": channel_name},
    )


def fetch_slack_thread(
    config: SlackConfig,
    channel: Mapping[str, Any],
    thread_timestamp: str,
    *,
    users: Mapping[str, str],
    http: HttpTransport,
    page_limit: int = 20,
) -> tuple[KnowledgeDocument | None, bool]:
    token = config.history_token.strip() or config.bot_token.strip()
    rows, complete = _paginate(
        token,
        "conversations.replies",
        {"channel": channel["id"], "ts": thread_timestamp, "limit": 200},
        http=http,
        page_limit=page_limit,
        collection="messages",
    )
    return _thread_document(channel, rows, users), complete


def fetch_slack_thread_by_id(
    config: SlackConfig,
    channel_id: str,
    thread_timestamp: str,
    *,
    http: HttpTransport,
    page_limit: int = 20,
) -> tuple[KnowledgeDocument | None, bool]:
    """Fetch one canonical thread when an event only carries provider IDs.

    Event receivers should treat Slack payloads as dirty hints and call this
    function rather than attempting to construct knowledge from the event
    body.  The complete thread is deterministic and therefore safe to replay.
    """
    if not channel_id.strip() or not thread_timestamp.strip():
        raise ValueError("Slack channel id and thread timestamp are required")
    request = PollRequest(page_limit=page_limit)
    users = _users(config, request, http=http)
    # Events carry only the opaque channel id. Resolve it before constructing
    # the document so event-driven ingestion has the same searchable channel
    # name as a scheduled poll.
    channel = _channel_info(config, channel_id.strip(), http=http) or {"id": channel_id.strip()}
    return fetch_slack_thread(
        config,
        channel,
        thread_timestamp.strip(),
        users=users,
        http=http,
        page_limit=page_limit,
    )


def poll_slack(
    config: SlackConfig, request: PollRequest, *, http: HttpTransport
) -> Iterator[PollPage]:
    users = _users(config, request, http=http)
    channels, channels_complete = _list_channels(config, http=http, page_limit=request.page_limit)
    readable = _require_channel_access(config, channels, complete=channels_complete, http=http)
    previous = float(request.cursor or 0)
    newest = previous
    documents: list[KnowledgeDocument] = []
    complete = channels_complete
    for channel in readable:
        rows, history_complete = _paginate(
            config.bot_token,
            "conversations.history",
            {
                "channel": channel["id"],
                "limit": 200,
                **({"oldest": f"{previous:.6f}"} if previous else {}),
            },
            http=http,
            page_limit=request.page_limit,
            collection="messages",
        )
        complete = complete and history_complete
        thread_roots: set[str] = set()
        for message in rows:
            newest = max(newest, _message_ts(message))
            if message.get("thread_ts") and message.get("thread_ts") != message.get("ts"):
                thread_roots.add(str(message["thread_ts"]))
                continue
            if int(message.get("reply_count") or 0):
                root_timestamp = str(message.get("ts") or "")
                if root_timestamp:
                    thread_roots.add(root_timestamp)
                continue
            document = _thread_document(channel, [message], users)
            if document is not None:
                documents.append(document)
        # conversations.history returns reply rows independently of their root.
        # Refetch each affected root so polling repairs missed provider events
        # and produces the same deterministic aggregate as event ingestion.
        for root_timestamp in sorted(thread_roots, key=float):
            document, thread_complete = fetch_slack_thread(
                config,
                channel,
                root_timestamp,
                users=users,
                http=http,
                page_limit=request.page_limit,
            )
            if document is not None:
                documents.append(document)
            complete = complete and thread_complete
    yield PollPage(
        tuple(documents),
        next_cursor=f"{newest:.6f}" if complete and newest else request.cursor,
        snapshot_complete=complete,
        provider_metadata={
            "thread_reconciliation": "complete" if complete else "incomplete",
        },
    )
