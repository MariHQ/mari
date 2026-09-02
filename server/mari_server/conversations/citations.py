"""The citation payload every Mari chat surface emits.

A citation used to be four fields wide (`n`, `source`, `title`, `meta`), which
is enough to print "[1] Runbook" and nothing else: no date, no author, no way
to open the thing where it actually lives. The console now renders a real
source card, so one function builds the whole payload and both the dock and the
public chat get identical fields.

`meta` survives as an alias of `snippet` for one release. Old clients read it
by name and dropping it would blank their cards on deploy, not on upgrade.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
import re
from typing import Any

from mari_components.knowledge.excerpt import excerpt
from mari_server.conversations.prompts import NOT_FOUND

# Wider than the old 110-character `meta` slice, still short enough that the
# card stays two or three lines at the console's column width.
SNIPPET_LIMIT = 160

_URL_SCHEMES = ("http://", "https://")


def dedupe(rows: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Retrieval rows with one row per document, first occurrence winning.

    Ranking already keys by document id, but a caller that concatenates two
    result sets (a search plus a pinned document, say) would otherwise cite the
    same page as [2] and [4] and invite the model to cite both.
    """
    seen: set[Any] = set()
    unique: list[Mapping[str, Any]] = []
    for row in rows:
        key = row.get("id", row.get("document_id"))
        if key is not None and key in seen:
            continue
        if key is not None:
            seen.add(key)
        unique.append(row)
    return unique


def _iso(value: Any) -> str:
    if value is None or value == "":
        return ""
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else str(value)


def _tags(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(tag) for tag in value if tag]
    return []


def _normalized_scores(rows: Sequence[Mapping[str, Any]]) -> list[float]:
    """Raw hybrid scores mapped onto 0..1, relative to the best row here.

    `search/service.py` composes keyword and semantic scores on an open-ended
    scale, so the absolute number means nothing to a client. Scaling by the top
    hit gives the reader the one comparison that does mean something: how far
    the fourth source sits below the first.
    """
    raw = [max(float(row.get("score") or 0.0), 0.0) for row in rows]
    top = max(raw, default=0.0)
    if top <= 0.0:
        return [0.0 for _ in raw]
    return [round(value / top, 3) for value in raw]


def source_url_of(value: Any) -> str | None:
    """A source URL a client can open, or None.

    The canonical `source_url` is projected into `documents.source_path`, which
    connectors also fill with repository-relative paths ("docs/runbook.md").
    Those are not links, so they come back as null rather than as an href that
    404s.
    """
    text = str(value or "").strip()
    return text if text.startswith(_URL_SCHEMES) else None


def source_payload(rows: Sequence[Mapping[str, Any]], *,
                   source_urls: Mapping[int, str] | None = None) -> list[dict[str, Any]]:
    """The `sources` array for one answer, numbered from 1 in retrieval order.

    `rows` are retrieval rows (`persistence/postgres/search.py`). `source_urls`
    maps document id to the canonical source URL; anything missing from it is
    null, which is what a document with no upstream link genuinely is.
    """
    unique = dedupe(rows)
    scores = _normalized_scores(unique)
    urls = source_urls or {}
    sources = []
    for index, row in enumerate(unique):
        document_id = row.get("id", row.get("document_id"))
        title = str(row.get("title") or "")
        # The same cleaner the knowledge cards use (product/queries._doc), so a
        # citation and a card show the same sentence for the same document.
        snippet = excerpt(row.get("body") or row.get("snippet") or "", title, SNIPPET_LIMIT)
        sources.append({
            "n": index + 1,
            "source": str(row.get("source") or ""),
            "kind": str(row.get("kind") or "page"),
            "title": title,
            "snippet": snippet,
            # Alias kept for one release; old clients read `meta`.
            "meta": snippet,
            "author": str(row.get("author") or ""),
            "updated": _iso(row.get("updated_src") or row.get("updated")),
            "tags": _tags(row.get("tags")),
            "document_id": document_id,
            "href": f"/knowledge/doc?id={document_id}" if document_id is not None else "",
            "source_url": source_url_of(urls.get(document_id) if document_id is not None
                                        else None) or source_url_of(row.get("source_path")),
            "score": scores[index],
        })
    return sources


# ————— what the answer actually cites —————
#
# Retrieval hands the model up to four documents and every chat surface used
# to show all four under the answer, whatever the answer said. Under "I could
# not find this in the connected sources" that rail was four pages the reader
# had just been told were no help. The functions below read the finished
# answer and keep only the rows it cites.

_CITE = re.compile(r"\[(\d{1,3})\](?!\()")
_FENCE_OPEN = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
# A fenced block, or an inline code span, that opens the answer. The text
# after it is whatever the model went on to say, usually nothing.
_LEADING_FENCED = re.compile(r"^(`{3,}|~{3,})[^\n]*\n(?P<inner>[\s\S]*?)\n[ \t]*\1[ \t]*(?=\n|$)")
_LEADING_INLINE = re.compile(r"^`(?P<inner>[^`\n]+)`")
_NOT_FOUND_KEY = " ".join(re.sub(r"[^a-z ]+", " ", NOT_FOUND.lower()).split())
# The refusal, paraphrased. The style rules ask for one sentence, but a model
# that rewords it ("I couldn't find anything about that in the connected
# sources") is still refusing, and the rail under it is still four pages the
# reader was just told were no help.
_REFUSAL = re.compile(
    r"\b(?:could ?not|couldn.?t|can ?not|can.?t) find\b"
    r"|\bno information\b|\bnothing about\b|\bnot covered\b",
    re.IGNORECASE,
)
_REFUSAL_SCOPE = re.compile(r"\b(?:sources?|context|knowledge base)\b", re.IGNORECASE)
# A refusal that names no scope still counts when it is this short: a real
# answer of a sentence or two does not dwell on what it failed to find.
REFUSAL_LIMIT = 200


def _outside_fences(text: str) -> Iterator[str]:
    """The chunks of an answer that are not inside a fenced code block, so a
    shell snippet's `arr[3]` is never read as a citation. Mirrors the library's
    outsideFences in ChatMessage.tsx."""
    fence = ""
    chunk: list[str] = []
    for line in text.split("\n"):
        opened = _FENCE_OPEN.match(line)
        if fence:
            if opened and line.strip().startswith(fence):
                fence = ""
        elif opened:
            if chunk:
                yield "\n".join(chunk)
                chunk = []
            fence = opened.group(1)[:3]
        else:
            chunk.append(line)
    if chunk:
        yield "\n".join(chunk)


def cite_numbers(answer: str) -> set[int]:
    """Every [n] marker in an answer, read the way the console links them:
    outside fenced code, outside inline code, and never a real link `[3](…)`."""
    numbers: set[int] = set()
    for chunk in _outside_fences(answer or ""):
        for index, part in enumerate(re.split(r"(`[^`]*`)", chunk)):
            if index % 2:
                continue
            numbers.update(int(match.group(1)) for match in _CITE.finditer(part))
    return numbers


def is_not_found(answer: str) -> bool:
    """Whether the answer is a refusal.

    The style rules' sentence is the canonical case, read loosely: case,
    punctuation and any wrapping the model added do not count. A paraphrase
    counts too: "could not find", "no information", "nothing about" or "not
    covered", said of the sources, the context or the knowledge base, or said
    of nothing in particular in an answer under `REFUSAL_LIMIT` characters.
    """
    text = (answer or "").strip()
    words = " ".join(re.sub(r"[^a-z ]+", " ", text.lower()).split())
    if _NOT_FOUND_KEY in words:
        return True
    if not _REFUSAL.search(text):
        return False
    return bool(_REFUSAL_SCOPE.search(text)) or len(text) < REFUSAL_LIMIT


def clean_answer(answer: str) -> str:
    """The model's answer as it should reach a renderer.

    Leading whitespace goes: a reply never opens with an indented code block,
    but the console's Markdown parser reads four leading spaces as one, which
    is how a plain sentence came out in monospace. A not-found sentence the
    model wrapped in a fence or backticks is unwrapped for the same reason,
    and whatever follows the wrapper is kept as the model wrote it, so the
    stream and the stored transcript agree. Anything else is passed through
    untouched, code fences included.
    """
    text = (answer or "").lstrip()
    wrapped = _LEADING_FENCED.match(text) or _LEADING_INLINE.match(text)
    if wrapped and is_not_found(wrapped.group("inner")):
        return wrapped.group("inner").strip() + text[wrapped.end():]
    return text


def cited(answer: str, sources: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The sources to show under `answer`, keeping their numbers.

    Cited rows only, when the answer cites a row that exists. An answer that
    says it could not find anything gets none. An answer with no markers that
    is not a refusal keeps every candidate: the model drew on the context
    without saying where, and hiding provenance would be worse than
    over-showing it (this is also what keeps an approved answer's card, which
    nothing cites by number).
    """
    rows = [dict(source) for source in sources]
    available = {_number(row) for row in rows} - {None}
    # A marker with no row behind it ([7] over four candidates) is a slip,
    # not a citation, so an answer whose only markers are slips is read like
    # an answer with none.
    numbers = {number for number in cite_numbers(answer) if number in available}
    if numbers:
        return [row for row in rows if _number(row) in numbers]
    if is_not_found(answer):
        return []
    return rows


def _number(row: Mapping[str, Any]) -> int | None:
    try:
        return int(row.get("n"))
    except (TypeError, ValueError):
        return None
