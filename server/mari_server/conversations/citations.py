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

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from mari_components.knowledge.excerpt import excerpt

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
