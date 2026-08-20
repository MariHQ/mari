"""Minimal MCP Streamable HTTP endpoint for published Mari knowledge tools.

Each server created in Publish gets a bearer token and a capability allowlist.
The endpoint implements the non-streaming JSON-RPC portion of MCP: initialize,
ping, tools/list and tools/call. SSE is deliberately unnecessary for these
bounded read tools.
"""

from __future__ import annotations

import json
import hashlib
import logging
import re
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, Response

import bots
import access
from db import pq, q1
from queries import hybrid_search

router = APIRouter(prefix="/mcp")
PROTOCOL_VERSION = "2025-06-18"

CAPABILITY_TOOLS: dict[str, list[dict[str, Any]]] = {
    "search": [{"name": "search_documents", "description": "Search the Mari knowledge base.",
                "inputSchema": {"type": "object", "properties": {
                    "query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 20}},
                    "required": ["query"]}}],
    "facts": [{"name": "list_facts", "description": "List curated facts.",
               "inputSchema": {"type": "object", "properties": {
                   "status": {"type": "string", "enum": ["Verified", "Needs review"]}}}}],
    "glossary": [{"name": "list_glossary", "description": "List approved glossary terms.",
                  "inputSchema": {"type": "object", "properties": {}}}],
    "answers": [{"name": "list_answers", "description": "List approved canonical answers.",
                 "inputSchema": {"type": "object", "properties": {}}}],
    "lineage": [{"name": "document_lineage", "description": "List direct lineage edges for a document.",
                 "inputSchema": {"type": "object", "properties": {
                     "document_id": {"type": "integer"}}, "required": ["document_id"]}}],
    "chat": [{"name": "ask_knowledge", "description": "Answer a question from Mari knowledge.",
              "inputSchema": {"type": "object", "properties": {
                  "question": {"type": "string"}}, "required": ["question"]}}],
}


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "server"


def _caps(server: dict) -> list[str]:
    cfg = server.get("config") or {}
    if isinstance(cfg, str):
        try:
            cfg = json.loads(cfg)
        except json.JSONDecodeError:
            cfg = {}
    return [c for c in cfg.get("capabilities", ["search"]) if c in CAPABILITY_TOOLS]


def _tool_names(server: dict) -> set[str]:
    return {tool["name"] for cap in _caps(server) for tool in CAPABILITY_TOOLS[cap]}


def _result(value: Any) -> dict:
    return {"content": [{"type": "text", "text": value if isinstance(value, str) else json.dumps(value, default=str)}]}


def call_tool(server: dict, name: str, args: dict) -> dict:
    """Execute one capability-scoped read. Raises ValueError for bad input."""
    if name not in _tool_names(server):
        raise ValueError(f"tool {name!r} is not enabled for this server")
    if name == "search_documents":
        query = str(args.get("query") or "").strip()
        if not query:
            raise ValueError("query is required")
        limit = min(max(int(args.get("limit", 8)), 1), 20)
        rows = hybrid_search(query, limit)
        return _result([{"id": r.get("id"), "title": r.get("title"), "source": r.get("source"),
                         "snippet": r.get("snippet") or (r.get("body") or "")[:500]} for r in rows])
    if name == "list_facts":
        status = args.get("status")
        if status and status not in ("Verified", "Needs review"):
            raise ValueError("status must be Verified or Needs review")
        sql = "SELECT id, claim, source, status FROM facts"
        rows = pq(sql + (" WHERE project_id = %s AND status = %s" if status
                         else " WHERE project_id = %s") + " ORDER BY id LIMIT 200",
                  (status,) if status else ())
        return _result(rows)
    if name == "list_glossary":
        return _result(pq("""SELECT id, term, definition FROM glossary
                             WHERE project_id = %s AND NOT candidate ORDER BY term LIMIT 200"""))
    if name == "list_answers":
        return _result(pq("""SELECT id, question, answer FROM approved_answers
                             WHERE project_id = %s AND status = 'approved' ORDER BY id LIMIT 200"""))
    if name == "document_lineage":
        doc_id = int(args.get("document_id") or 0)
        if doc_id < 1:
            raise ValueError("document_id is required")
        return _result(pq("""SELECT e.id, e.rel, f.id AS from_id, f.title AS from_title,
                                   t.id AS to_id, t.title AS to_title
                            FROM edges e JOIN documents f ON f.id = e.from_doc
                            JOIN documents t ON t.id = e.to_doc
                            WHERE e.project_id = %s AND f.project_id = e.project_id
                              AND t.project_id = e.project_id
                              AND (e.from_doc = %s OR e.to_doc = %s)
                            ORDER BY e.id LIMIT 200""",
                         (doc_id, doc_id)))
    if name == "ask_knowledge":
        question = str(args.get("question") or "").strip()
        if not question:
            raise ValueError("question is required")
        return _result(bots.answer_question(question))
    raise ValueError(f"unknown tool {name!r}")


def dispatch(server: dict, message: dict) -> dict | None:
    """Dispatch one JSON-RPC message; notifications intentionally return None."""
    request_id = message.get("id")
    method = message.get("method")
    if request_id is None:
        return None
    try:
        if method == "initialize":
            result = {"protocolVersion": PROTOCOL_VERSION,
                      "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": "mari", "version": "0.2.0"}}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": [tool for cap in _caps(server) for tool in CAPABILITY_TOOLS[cap]]}
        elif method == "tools/call":
            params = message.get("params") or {}
            result = call_tool(server, str(params.get("name") or ""), params.get("arguments") or {})
        else:
            return {"jsonrpc": "2.0", "id": request_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"}}
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    except (TypeError, ValueError) as exc:
        return {"jsonrpc": "2.0", "id": request_id,
                "error": {"code": -32602, "message": str(exc)}}
    except Exception:  # noqa: BLE001 — transport must preserve JSON-RPC framing
        logging.getLogger("mari.mcp").exception("MCP tool execution failed")
        return {"jsonrpc": "2.0", "id": request_id,
                "error": {"code": -32603, "message": "Internal tool error"}}


@router.post("/{slug}", response_model=None)
async def mcp_endpoint(slug: str, request: Request,
                       authorization: str = Header(default="")) -> Any:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "Bearer token required")
    # Token lookup is the bootstrap boundary: after it succeeds every tool
    # executes with the server's immutable project context.
    server = q1("""SELECT m.id, m.name, m.config, m.project_id,
                          p.slug AS project_slug, p.name AS project_name
                     FROM mcp_servers m JOIN projects p ON p.id = m.project_id
                    WHERE (m.token_hash = %s OR (m.token <> '' AND m.token = %s))
                      AND m.status = 'connected'
                      AND p.status = 'active'""", (hashlib.sha256(token.encode()).hexdigest(), token))
    if not server or _slug(server["name"]) != slug:
        raise HTTPException(401, "Invalid MCP server or token")
    try:
        message = json.loads(await request.body())
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(400, "Invalid JSON-RPC payload") from None
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        raise HTTPException(400, "Expected a JSON-RPC 2.0 object")
    context = access.external_access(
        server["project_id"], server["project_slug"], server["project_name"],
        "mcp", str(server["id"]), frozenset({"knowledge.read"}))
    with access.use_access(context):
        response = dispatch(server, message)
    return Response(status_code=202) if response is None else response
