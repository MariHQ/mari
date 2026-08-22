"""HTTP search destination for external knowledge consumers."""

from __future__ import annotations

import hashlib

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from mari_components.knowledge.excerpt import excerpt
from mari_server.identity import access
from mari_server.persistence.postgres import identity
from mari_server.search.service import hybrid_count, hybrid_search


router = APIRouter()


class ApiSearchIn(BaseModel):
    query: str
    limit: int = 10


class ApiSearchResult(BaseModel):
    id: int
    title: str
    source: str
    snippet: str
    score: float


class ApiSearchOut(BaseModel):
    results: list[ApiSearchResult]
    total: int


# hybrid_search's raw score is (keyword_score * 2 + semantic_lift * 3) * boost
# (search/service.py). keyword_score and semantic_lift each sit in [0, 1], so
# 2 + 3 = 5 is the ceiling before any connector boost multiplier. Dividing by
# that ceiling and clamping to [0, 1] turns the raw weight into a stable
# relevance score API consumers can compare across queries.
_SCORE_CEILING = 5.0


def _normalize_score(raw: float) -> float:
    return max(0.0, min(1.0, float(raw) / _SCORE_CEILING))


@router.post("/api/search", response_model=ApiSearchOut, include_in_schema=True)
def api_search(body: ApiSearchIn, authorization: str = Header(default="")) -> ApiSearchOut:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "Bearer API key required.")
    row = identity.authenticate_api_key(hashlib.sha256(token.encode()).hexdigest())
    if not row:
        raise HTTPException(401, "Invalid or revoked API key.")
    scopes = {value.strip() for value in str(row["scopes"] or "").split(",")}
    if not ({"read", "search"} & scopes):
        raise HTTPException(403, "This API key does not allow search.")
    context = access.external_access(
        row["project_id"], row["slug"], row["project_name"], "api_key", str(row["id"]),
        frozenset({"knowledge.read"}),
    )
    query = body.query.strip()
    limit = max(1, min(body.limit, 50))
    with access.use_access(context):
        rows = hybrid_search(query, limit)
        total = hybrid_count(query)
    identity.touch_api_key(row["id"])
    return ApiSearchOut(
        results=[
            ApiSearchResult(
                id=item["id"], title=item["title"], source=item["source"],
                # Same cleanup the console's document card uses (product/queries.py
                # _doc): a computed excerpt when the row carries a body, the
                # stored snippet for older rows ingested before excerpt existed.
                snippet=(excerpt(item.get("body"), item["title"])
                         if item.get("body") else item["snippet"]),
                score=_normalize_score(item.get("score", 0)),
            )
            for item in rows
        ],
        total=total,
    )
