"""HTTP search destination for external knowledge consumers."""

from __future__ import annotations

import hashlib
import typing as t

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from mari_server.identity import access
from mari_server.persistence.postgres import identity
from mari_server.substrates.errors import SubstrateConfigurationError, SubstrateRequestError
from mari_server.substrates.service import configured_substrate
from mari_components.substrates import SearchRequest


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
        try:
            rows = configured_substrate().search(SearchRequest(
                body.query.strip(), max(1, min(body.limit, 50)),
            ))
        except SubstrateConfigurationError as error:
            raise HTTPException(409, str(error)) from None
        except SubstrateRequestError as error:
            status = error.status if error.status in {400, 401, 403, 404, 409, 422, 429, 503} else 502
            raise HTTPException(status, str(error)) from None
    identity.touch_api_key(row["id"])
    return {"results": [
        {"id": item.document_id, "title": item.title, "source": item.source,
         "snippet": item.content, "url": item.url, "score": item.score}
        for item in rows
    ]}
