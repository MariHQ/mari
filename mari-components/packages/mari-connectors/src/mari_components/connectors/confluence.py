"""Confluence Cloud validation, canonical page fetch, and ordered polling."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import datetime as dt
import html
from html.parser import HTMLParser
import json
from typing import Any, Iterator, Mapping
import urllib.parse

from mari_components.connectors._shared import json_response
from mari_components.connectors.protocol import ValidationResult, classify_error
from mari_components.errors import AuthenticationFailure, PermanentFailure
from mari_components.http import HttpRequest, HttpTransport
from mari_components.types import DocumentACL, KnowledgeDocument, PollPage, PollRequest


@dataclass(frozen=True, slots=True)
class ConfluenceConfig:
    site_url: str
    email: str
    api_token: str
    space_key: str = ""

    def __post_init__(self) -> None:
        if not self.site_url.strip() or not self.email.strip() or not self.api_token.strip():
            raise ValueError("Confluence site URL, email, and API token are required")


class _StorageText(HTMLParser):
    _BLOCK_END = {"p", "div", "ul", "ol", "table", "tr", "blockquote",
                  "ac:adf-node", "ac:adf-content", "ac:adf-extension"}
    # New-editor markup arrives inside <ac:adf-extension>. Its attribute-style
    # children are configuration, not prose: a panel's
    # <ac:adf-attribute key="panel-type">note</ac:adf-attribute> and its
    # <ac:adf-attribute key="local-id">f19de4a5-…</ac:adf-attribute> were being
    # glued onto the panel text ("notef19de4a5-…As of November 2024"). The
    # words live in <ac:adf-content>, which is kept. The one attribute a reader
    # sees is an expand's key="title", the heading of the collapsed section;
    # it is handled like a macro's title parameter in handle_starttag.
    _ADF_CONFIG = {"ac:adf-parameter"}
    # ac:placeholder is the editor's grey hint ("Type / to insert"), never
    # something a reader of the page sees.
    _SKIPPED = _ADF_CONFIG | {"ac:task-status", "ac:placeholder"}
    _ADF_NODES = {"ac:adf-node", "ac:adf-extension"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.output: list[str] = []
        self.list_depth = 0
        self.skip_depth = 0
        self.code_opener: str | None = None
        self.macro_names: list[str] = []
        self.param_keeps: list[bool] = []
        # One flag per open <ac:adf-extension>: did its <ac:adf-content> emit
        # any text? Decides whether the trailing <ac:adf-fallback> is a
        # duplicate or the only rendering there is.
        self.adf_content_seen: list[bool] = []
        self.adf_content_depth = 0
        self.fallback_skips: list[bool] = []
        # One flag per open <ac:adf-attribute>, and where each open ADF node's
        # attributes start in that stack. An attribute the page never closed
        # would otherwise skip everything to the end of the page; the node
        # that owns it unwinds it instead.
        self.adf_attribute_keeps: list[bool] = []
        self.adf_node_marks: list[int] = []

    def _words(self, text: str) -> None:
        """Visible words, wherever they come from: text, CDATA, an image's alt,
        a link's page title. Inside <ac:adf-content> they also mark the node
        as rendered, so its fallback is not read as well."""
        if self.adf_content_depth and self.adf_content_seen and text.strip():
            self.adf_content_seen[-1] = True
        self.output.append(text)

    def _unwind_attributes(self) -> None:
        mark = self.adf_node_marks.pop() if self.adf_node_marks else 0
        while len(self.adf_attribute_keeps) > mark:
            if not self.adf_attribute_keeps.pop():
                self.skip_depth = max(0, self.skip_depth - 1)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        mapping = dict(attrs)
        if tag in self._ADF_NODES:
            self.adf_node_marks.append(len(self.adf_attribute_keeps))
        if tag == "ac:parameter":
            # Macro parameters are configuration, not content, and glued
            # straight into the body they read as garbage ("Team directorytrue").
            # The one exception is the title parameter, which is the visible
            # text of panels and status lozenges.
            keep = str(mapping.get("ac:name") or "") == "title"
            self.param_keeps.append(keep)
            if not keep:
                self.skip_depth += 1
            return
        if tag == "ac:adf-attribute":
            # Same rule for the new editor's attributes: only a title is
            # something the reader sees.
            keep = str(mapping.get("key") or "") == "title"
            self.adf_attribute_keeps.append(keep)
            if not keep:
                self.skip_depth += 1
            return
        if tag in self._SKIPPED:
            self.skip_depth += 1
            return
        if tag == "ac:adf-fallback":
            # The legacy rendering of the node before it, for editors that
            # cannot draw ADF: the same words again. Indexing both put every
            # panel in the body twice, so the fallback is read only when the
            # node carried no text of its own.
            duplicate = bool(self.adf_content_seen and self.adf_content_seen[-1])
            self.fallback_skips.append(duplicate)
            if duplicate:
                self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag == "ac:adf-extension":
            self.adf_content_seen.append(False)
        elif tag == "ac:adf-content":
            self.adf_content_depth += 1
        elif tag == "ac:structured-macro":
            name = str(mapping.get("ac:name") or "")
            self.macro_names.append(name)
            if name == "code" and self.code_opener is None:
                self.code_opener = tag
                self.output.append("\n```\n")
        elif tag == "ac:image":
            alt = str(mapping.get("ac:alt") or "")
            if alt:
                self._words(alt + " ")
        elif tag == "ri:page":
            title = str(mapping.get("ri:content-title") or "")
            if title:
                self._words(title + " ")
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.output.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag in {"ul", "ol"}:
            self.list_depth += 1
        elif tag == "li":
            self.output.append("\n" + "  " * max(self.list_depth - 1, 0) + "- ")
        elif tag == "pre":
            if self.code_opener is None:
                self.code_opener = tag
                self.output.append("\n```\n")
        elif tag == "code":
            if self.code_opener is None:
                self.code_opener = tag
                self.output.append("`")
        elif tag == "br":
            self.output.append("\n")
        elif tag in {"td", "th"}:
            self.output.append(" | ")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._ADF_NODES:
            self._unwind_attributes()
        if tag == "ac:parameter":
            keep = self.param_keeps.pop() if self.param_keeps else False
            if keep:
                self.output.append("\n")
            else:
                self.skip_depth = max(0, self.skip_depth - 1)
            return
        if tag == "ac:adf-attribute":
            keep = self.adf_attribute_keeps.pop() if self.adf_attribute_keeps else False
            if keep:
                self.output.append("\n")
            else:
                self.skip_depth = max(0, self.skip_depth - 1)
            return
        if tag in self._SKIPPED:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if tag == "ac:adf-fallback":
            if self.fallback_skips and self.fallback_skips.pop():
                self.skip_depth = max(0, self.skip_depth - 1)
            return
        if self.skip_depth:
            return
        if tag == "ac:adf-extension":
            seen = self.adf_content_seen.pop() if self.adf_content_seen else False
            # A nested extension that rendered words rendered them inside its
            # parent's content, so the parent's fallback is a duplicate too.
            if seen and self.adf_content_depth and self.adf_content_seen:
                self.adf_content_seen[-1] = True
            self.output.append("\n")
        elif tag == "ac:adf-content":
            self.adf_content_depth = max(0, self.adf_content_depth - 1)
            self.output.append("\n")
        elif tag == "ac:structured-macro":
            # Only the macro that opened the fence may close it; an info panel
            # inside inline code must not emit a stray fence.
            name = self.macro_names.pop() if self.macro_names else ""
            if name == "code" and self.code_opener == tag:
                self.code_opener = None
                self.output.append("\n```\n")
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.output.append("\n")
        elif tag in {"ul", "ol"}:
            self.list_depth = max(0, self.list_depth - 1)
        elif tag == "pre":
            if self.code_opener == tag:
                self.code_opener = None
                self.output.append("\n```\n")
        elif tag == "code":
            if self.code_opener == tag:
                self.code_opener = None
                self.output.append("`")
        elif tag in self._BLOCK_END:
            self.output.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        self._words(data)

    def unknown_decl(self, data: str) -> None:
        # CDATA sections hold code samples verbatim; stripping the guards and
        # parsing the payload as HTML ate the markup a knowledge base exists
        # to index.
        if not self.skip_depth and data.startswith("CDATA["):
            self._words(data[len("CDATA["):])

    def text(self) -> str:
        lines = [line.rstrip() for line in "".join(self.output).split("\n")]
        cleaned: list[str] = []
        blank = False
        for line in lines:
            if not line:
                if blank:
                    continue
                blank = True
            else:
                blank = False
            cleaned.append(line)
        return "\n".join(cleaned).strip()


def storage_to_text(xhtml: str) -> str:
    if not xhtml:
        return ""
    parser = _StorageText()
    try:
        parser.feed(xhtml)
        parser.close()
        return parser.text()
    except Exception:
        import re

        return html.unescape(re.sub(r"<[^>]+>", " ", xhtml)).strip()


def _when(value: str) -> dt.datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def _order_key(time_str: str, id_str: str) -> tuple:
    """A total order over (updated_at, page id) cursor pairs.

    Timestamps compare as instants (string compare misorders "…07Z" against
    "…07.500000Z"), and numeric page ids compare as numbers (a new page with
    id 1000 must not sort below a stored id of 999)."""
    when = _when(time_str)
    time_part = (1, when, "") if when is not None else (0, dt.datetime.min.replace(tzinfo=dt.timezone.utc), time_str)
    id_text = str(id_str or "")
    id_part = (0, int(id_text), "") if id_text.isdigit() else (1, 0, id_text)
    return (time_part, id_part)


def _site(config: ConfluenceConfig) -> str:
    site = config.site_url.strip().rstrip("/")
    # Users commonly paste the /wiki URL they browse to; the REST API lives
    # at the bare site root, so strip a trailing /wiki or it doubles up
    # (.../wiki/wiki/rest/api).
    if site.lower().endswith("/wiki"):
        site = site[: -len("/wiki")]
    return site if site.startswith(("http://", "https://")) else f"https://{site}"


def _get(
    config: ConfluenceConfig,
    path: str,
    params: Mapping[str, Any] | None,
    *,
    http: HttpTransport,
) -> dict[str, Any]:
    encoded = base64.b64encode(f"{config.email.strip()}:{config.api_token.strip()}".encode()).decode()
    query = "?" + urllib.parse.urlencode(params) if params else ""
    value = json_response(
        http,
        HttpRequest(
            "GET",
            _site(config) + path + query,
            {"Authorization": f"Basic {encoded}", "Accept": "application/json"},
        ),
    )
    if not isinstance(value, dict):
        raise PermanentFailure("Confluence returned a non-object response")
    return value


def validate_confluence(config: ConfluenceConfig, *, http: HttpTransport) -> ValidationResult:
    try:
        data = _get(config, "/wiki/rest/api/space", {"limit": 1}, http=http)
    except AuthenticationFailure as error:
        return ValidationResult(False, str(error), kind=classify_error(error).value)
    except Exception as error:
        return ValidationResult(False, str(error), kind=classify_error(error).value)
    if "results" not in data:
        return ValidationResult(False, "Confluence space API returned an unexpected response")
    return ValidationResult(True, identity=config.email.strip())


def _document(page: Mapping[str, Any], site: str) -> KnowledgeDocument:
    page_id = str(page.get("id") or "")
    if not page_id:
        raise PermanentFailure("Confluence page is missing an id")
    body = (((page.get("body") or {}).get("storage") or {}).get("value")) or ""
    version = str((page.get("version") or {}).get("number") or "")
    updated = (((page.get("history") or {}).get("lastUpdated") or {}).get("when")) or (
        (page.get("version") or {}).get("when") or ""
    )
    # The last editor is the more useful owner; a page nobody has edited since
    # creation falls back to who created it. Never the connector's own name.
    author = str(((page.get("version") or {}).get("by") or {}).get("displayName") or "") or str(
        ((page.get("history") or {}).get("createdBy") or {}).get("displayName") or ""
    )
    links = page.get("_links") or {}
    webui = str(links.get("webui") or "")
    return KnowledgeDocument(
        page_id,
        str(page.get("title") or f"Page {page_id}"),
        storage_to_text(str(body)),
        revision=version,
        updated_at=str(updated),
        # The web UI lives under /wiki on Cloud; _site() strips it for REST
        # calls, so it must come back here or every citation link 404s.
        source_url=site + "/wiki" + webui if webui.startswith("/") else webui,
        acl=DocumentACL("connector_scope"),
        metadata={"space_key": str((page.get("space") or {}).get("key") or ""), "author": author},
    )


def fetch_confluence_page(
    config: ConfluenceConfig, page_id: str, *, http: HttpTransport
) -> KnowledgeDocument | None:
    if not page_id.strip():
        raise ValueError("page_id is required")
    try:
        page = _get(
            config,
            f"/wiki/rest/api/content/{urllib.parse.quote(page_id, safe='')}",
            {"expand": "body.storage,version,history.lastUpdated,history.createdBy,space"},
            http=http,
        )
    except PermanentFailure as error:
        if "HTTP 404" in str(error):
            return None
        raise
    if str(page.get("type") or "page") != "page":
        return None
    return _document(page, _site(config))


def poll_confluence(
    config: ConfluenceConfig, request: PollRequest, *, http: HttpTransport
) -> Iterator[PollPage]:
    start = 0
    cursor_time, _, cursor_id = str(request.cursor or "").partition("|")
    # The caller's cursor is the change filter for the whole sweep; the
    # checkpoint's cursor_time/cursor_id is only the high-water mark of the
    # batches already seen. Reusing the high-water mark as the filter on
    # resume dropped every document in later windows (the sweep is unordered,
    # so a later window can hold keys below the mark).
    filter_key = (cursor_time, cursor_id)
    last_key = filter_key
    if request.checkpoint:
        try:
            checkpoint = json.loads(request.checkpoint)
            start = max(0, int(checkpoint.get("start", 0)))
            last_key = (str(checkpoint.get("cursor_time") or cursor_time),
                        str(checkpoint.get("cursor_id") or cursor_id))
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("invalid Confluence checkpoint") from error
    for page_number in range(request.page_limit):
        params: dict[str, Any] = {
            "type": "page",
            "expand": "body.storage,version,history.lastUpdated,history.createdBy,space,_links",
            "limit": request.page_size,
            "start": start,
            # No "orderby": live Confluence sites now reject it on this
            # endpoint (400 "Unsupported orderBy field") regardless of value.
            # It was never load-bearing for correctness: this sweep already
            # walks every page (start += size until size < page_size) and
            # sorts/filters each page client-side by (updated_at, id) below,
            # so completeness and cursor ordering hold with the server's
            # native (undefined) order too.
        }
        if config.space_key.strip():
            params["spaceKey"] = config.space_key.strip()
        data = _get(config, "/wiki/rest/api/content", params, http=http)
        documents = sorted(
            (_document(page, _site(config)) for page in data.get("results") or []),
            key=lambda document: _order_key(document.updated_at, document.external_id),
        )
        emitted: list[KnowledgeDocument] = []
        for document in documents:
            key = (document.updated_at, document.external_id)
            if request.cursor and _order_key(*key) <= _order_key(*filter_key):
                continue
            emitted.append(document)
            if _order_key(*key) > _order_key(*last_key):
                last_key = key
        size = int(data.get("size", len(documents)) or 0)
        # Confluence applies `limit` before permission filtering and silently
        # caps it at a server maximum, so a short window proves nothing. When
        # the response carries a _links object, its `next` link is the only
        # honest signal that the sweep is done; the size heuristic stays as a
        # fallback for feeds that do not send _links at all.
        links = data.get("_links")
        if isinstance(links, dict):
            next_link = str(links.get("next") or "")
            terminal = not next_link
        else:
            next_link = ""
            terminal = not documents or size < request.page_size
        applied_limit = int(data.get("limit", request.page_size) or 0) or request.page_size
        next_start = None
        if next_link:
            with_query = urllib.parse.parse_qs(urllib.parse.urlsplit(next_link).query)
            if with_query.get("start"):
                try:
                    next_start = int(with_query["start"][0])
                except ValueError:
                    next_start = None
        # Advance by the window the server actually applied, not by the
        # (possibly filtered) result count, or a short window re-reads rows.
        start = next_start if next_start is not None else start + max(size, applied_limit)
        next_cursor = "|".join(last_key) if terminal and last_key[0] else request.cursor
        checkpoint = None if terminal else json.dumps(
            {"start": start, "cursor_time": last_key[0], "cursor_id": last_key[1]},
            sort_keys=True,
            separators=(",", ":"),
        )
        yield PollPage(
            tuple(emitted),
            next_cursor=next_cursor,
            next_checkpoint=checkpoint,
            snapshot_complete=terminal,
        )
        if terminal:
            return
    yield PollPage(
        next_cursor=request.cursor,
        next_checkpoint=json.dumps(
            {"start": start, "cursor_time": last_key[0], "cursor_id": last_key[1]},
            sort_keys=True,
            separators=(",", ":"),
        ),
        snapshot_complete=False,
        provider_metadata={"reason": "page_limit"},
    )
