"""HTTP search destination for external knowledge consumers."""

from __future__ import annotations

import hashlib
import typing as t

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from mari_server.identity import access
from mari_server.persistence.postgres import identity
from mari_server.search.service import hybrid_search


router = APIRouter()


class ApiSearchIn(BaseModel):
    query: str
    limit: int = 10


@router.post("/api/search", include_in_schema=True)
def api_search(body: ApiSearchIn, authorization: str = Header(default="")) -> dict[str, t.Any]:
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
    with access.use_access(context):
        rows = hybrid_search(body.query.strip(), max(1, min(body.limit, 50)))
    identity.touch_api_key(row["id"])
    return {"results": [
        {"id": item["id"], "title": item["title"], "source": item["source"],
         "snippet": item["snippet"], "score": item.get("score", 0)}
        for item in rows
    ]}
