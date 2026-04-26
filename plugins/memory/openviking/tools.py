"""OpenViking tool schemas and tool implementation functions.

Tool implementations are free functions taking `provider` as the first
argument to avoid circular imports — `OpenVikingMemoryProvider` is only
referenced under TYPE_CHECKING (no runtime import from __init__).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Dict

from tools.registry import tool_error  # noqa: F401 — re-exported for convenience

if TYPE_CHECKING:
    from . import OpenVikingMemoryProvider  # only for type hints, no runtime import


def tool_ok(data: Any) -> str:
    """Serialize a successful tool result to JSON."""
    return json.dumps(data, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

SEARCH_SCHEMA = {
    "name": "viking_search",
    "description": (
        "Semantic search over the OpenViking knowledge base. "
        "Returns ranked results with viking:// URIs for deeper reading. "
        "Use mode='deep' for complex queries that need reasoning across "
        "multiple sources, 'fast' for simple lookups."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query."},
            "mode": {
                "type": "string", "enum": ["auto", "fast", "deep"],
                "description": "Search depth (default: auto).",
            },
            "scope": {
                "type": "string",
                "description": "Viking URI prefix to scope search (e.g. 'viking://resources/docs/').",
            },
            "limit": {"type": "integer", "description": "Max results (default: 10)."},
        },
        "required": ["query"],
    },
}

READ_SCHEMA = {
    "name": "viking_read",
    "description": (
        "Read content at a viking:// URI. Three detail levels:\n"
        "  abstract — ~100 token summary (L0)\n"
        "  overview — ~2k token key points (L1)\n"
        "  full — complete content (L2)\n"
        "Start with abstract/overview, only use full when you need details."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "uri": {"type": "string", "description": "viking:// URI to read."},
            "level": {
                "type": "string", "enum": ["abstract", "overview", "full"],
                "description": "Detail level (default: overview).",
            },
        },
        "required": ["uri"],
    },
}

BROWSE_SCHEMA = {
    "name": "viking_browse",
    "description": (
        "Browse the OpenViking knowledge store like a filesystem.\n"
        "  list — show directory contents\n"
        "  tree — show hierarchy\n"
        "  stat — show metadata for a URI"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string", "enum": ["tree", "list", "stat"],
                "description": "Browse action.",
            },
            "path": {
                "type": "string",
                "description": "Viking URI path (default: viking://). Examples: 'viking://resources/', 'viking://user/memories/'.",
            },
        },
        "required": ["action"],
    },
}

REMEMBER_SCHEMA = {
    "name": "viking_remember",
    "description": (
        "Explicitly store a fact or memory in the OpenViking knowledge base. "
        "Use for important information the agent should remember long-term. "
        "The system automatically categorizes and indexes the memory."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "The information to remember."},
            "category": {
                "type": "string",
                "enum": ["preference", "entity", "event", "case", "pattern"],
                "description": "Memory category (default: auto-detected).",
            },
        },
        "required": ["content"],
    },
}

ADD_RESOURCE_SCHEMA = {
    "name": "viking_add_resource",
    "description": (
        "Add a URL or document to the OpenViking knowledge base. "
        "Supports web pages, GitHub repos, PDFs, markdown, code files. "
        "The system automatically parses, indexes, and generates summaries."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL or path of the resource to add."},
            "reason": {
                "type": "string",
                "description": "Why this resource is relevant (improves search).",
            },
        },
        "required": ["url"],
    },
}


# ---------------------------------------------------------------------------
# Tool implementations (free functions — provider passed as first argument)
# ---------------------------------------------------------------------------

def _tool_search_impl(provider: "OpenVikingMemoryProvider", args: dict) -> str:
    query = args.get("query", "")
    if not query:
        return tool_error("query is required")

    payload: Dict[str, Any] = {"query": query}
    mode = args.get("mode", "auto")
    if mode != "auto":
        payload["mode"] = mode
    if args.get("scope"):
        payload["target_uri"] = args["scope"]
    if args.get("limit"):
        payload["top_k"] = args["limit"]

    resp = provider._client.post("/api/v1/search/find", payload)
    result = resp.get("result", {})

    # Format results for the model — keep it concise
    scored_entries = []
    for ctx_type in ("memories", "resources", "skills"):
        items = result.get(ctx_type, [])
        for item in items:
            raw_score = item.get("score")
            sort_score = raw_score if raw_score is not None else 0.0
            entry = {
                "uri": item.get("uri", ""),
                "type": ctx_type.rstrip("s"),
                "score": round(raw_score, 3) if raw_score is not None else 0.0,
                "abstract": item.get("abstract", ""),
            }
            if item.get("relations"):
                entry["related"] = [r.get("uri") for r in item["relations"][:3]]
            scored_entries.append((sort_score, entry))

    scored_entries.sort(key=lambda x: x[0], reverse=True)
    formatted = [entry for _, entry in scored_entries]

    return json.dumps({
        "results": formatted,
        "total": result.get("total", len(formatted)),
    }, ensure_ascii=False)


def _tool_read_impl(provider: "OpenVikingMemoryProvider", args: dict) -> str:
    uri = args.get("uri", "")
    if not uri:
        return tool_error("uri is required")

    level = args.get("level", "overview")
    # Map our level names to OpenViking GET endpoints
    if level == "abstract":
        resp = provider._client.get("/api/v1/content/abstract", params={"uri": uri})
    elif level == "full":
        resp = provider._client.get("/api/v1/content/read", params={"uri": uri})
    else:  # overview
        resp = provider._client.get("/api/v1/content/overview", params={"uri": uri})

    result = resp.get("result", "")
    # result is a plain string from the content endpoints
    content = result if isinstance(result, str) else result.get("content", "")

    # Truncate very long content to avoid flooding the context
    if len(content) > 8000:
        content = content[:8000] + "\n\n[... truncated, use a more specific URI or abstract level]"

    return json.dumps({
        "uri": uri,
        "level": level,
        "content": content,
    }, ensure_ascii=False)


def _tool_browse_impl(provider: "OpenVikingMemoryProvider", args: dict) -> str:
    action = args.get("action", "list")
    path = args.get("path", "viking://")

    # Map action to the correct fs endpoint (all GET with uri= param)
    endpoint_map = {"tree": "/api/v1/fs/tree", "list": "/api/v1/fs/ls", "stat": "/api/v1/fs/stat"}
    endpoint = endpoint_map.get(action, "/api/v1/fs/ls")
    resp = provider._client.get(endpoint, params={"uri": path})
    result = resp.get("result", {})

    # Format list/tree results for readability
    if action in ("list", "tree") and isinstance(result, list):
        entries = []
        for e in result[:50]:  # cap at 50 entries
            entries.append({
                "name": e.get("rel_path", e.get("name", "")),
                "uri": e.get("uri", ""),
                "type": "dir" if e.get("isDir") else "file",
                "abstract": e.get("abstract", ""),
            })
        return json.dumps({"path": path, "entries": entries}, ensure_ascii=False)

    return json.dumps(result, ensure_ascii=False)


def _tool_remember_impl(provider: "OpenVikingMemoryProvider", args: dict) -> str:
    content = args.get("content", "")
    if not content:
        return tool_error("content is required")

    # Store as a session message that will be extracted during commit.
    # The category hint helps OpenViking's extraction classify correctly.
    category = args.get("category", "")
    text = f"[Remember] {content}"
    if category:
        text = f"[Remember — {category}] {content}"

    provider._client.post(f"/api/v1/sessions/{provider._session_id}/messages", {
        "role": "user",
        "parts": [
            {"type": "text", "text": text},
        ],
    })

    return json.dumps({
        "status": "stored",
        "message": "Memory recorded. Will be extracted and indexed on session commit.",
    })


def _tool_add_resource_impl(provider: "OpenVikingMemoryProvider", args: dict) -> str:
    url = args.get("url", "")
    if not url:
        return tool_error("url is required")

    payload: Dict[str, Any] = {"path": url}
    if args.get("reason"):
        payload["reason"] = args["reason"]

    resp = provider._client.post("/api/v1/resources", payload)
    result = resp.get("result", {})

    return json.dumps({
        "status": "added",
        "root_uri": result.get("root_uri", ""),
        "message": "Resource queued for processing. Use viking_search after a moment to find it.",
    }, ensure_ascii=False)
