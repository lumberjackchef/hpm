#!/usr/bin/env python3
"""MCP stdio server exposing hpm memory tools to Hermes Agent and Claude Code.

Register with Hermes::

    hermes mcp add hpm --command python3 --args /path/to/hpm_mcp_server.py

Or with Claude Code via `.mcp.json` at the repo root::

    {
      "mcpServers": {
        "hpm": {
          "command": "/path/to/python3",
          "args": ["/path/to/hpm_mcp_server.py"]
        }
      }
    }

Then in any agent session, the ``memory-find``, ``memory-save``, and
``memory-capture`` tools are available for the agent to call automatically.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

# Add src/ to path so hpm can be imported when run standalone
_src = Path(__file__).resolve().parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from hpm import (  # noqa: E402, I001
    answer,
    config,
    daily,
    db as db_module,
    embed,  # lazy — fastembed is heavy, only imported when first handler runs
    summarize,
)
from hpm.wiki import find as wiki_find  # noqa: E402

logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
logger = logging.getLogger("hpm-mcp")


# ── MCP Protocol ─────────────────────────────────────────────────────────

def respond(req: dict[str, Any], result: Any = None, error: dict | None = None) -> None:
    """Send a JSON-RPC response to stdout."""
    resp: dict[str, Any] = {"jsonrpc": "2.0", "id": req.get("id")}
    if error:
        resp["error"] = error
    else:
        resp["result"] = result
    sys.stdout.write(json.dumps(resp) + "\n")
    sys.stdout.flush()


def send_notification(method: str, params: dict[str, Any]) -> None:
    """Send a JSON-RPC notification (no id)."""
    msg = {"jsonrpc": "2.0", "method": method, "params": params}
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


# ── Tool Handlers ─────────────────────────────────────────────────────────

TOOL_DEFINITIONS = [
    {
        "name": "memory-find",
        "description": (
            "Search memory with hybrid semantic + keyword retrieval and return "
            "a structured cited answer. Uses a cross-encoder reranker for precision. "
            "Pass the user's question as the query."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The question or search query",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to return (default 5)",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "memory-save",
        "description": "Save an explicit fact to memory without summarization.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "fact": {
                    "type": "string",
                    "description": "The fact to remember",
                },
                "tags": {
                    "type": "string",
                    "description": "Comma-separated tags (e.g. project:jarvis,topic:payments)",
                },
            },
            "required": ["fact"],
        },
    },
    {
        "name": "memory-capture",
        "description": "Capture and summarize a conversation turn, then store to memory.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The conversation turn text (user + assistant)",
                },
                "tags": {
                    "type": "string",
                    "description": "Comma-separated tags",
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "memory-wiki-find",
        "description": (
            "Look up a topic in the compiled knowledge wiki. "
            "Faster than memory-find for well-known topics. "
            "Falls back to memory-find if the wiki doesn't cover the topic."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The topic or question to look up",
                },
            },
            "required": ["query"],
        },
    },
]


def handle_memory_find(query: str, limit: int = 5) -> str:
    """Full recall pipeline: hybrid → reranker → cited answer."""
    from hpm import rerank  # lazy: sentence-transformers is heavy
    conn = db_module.get_connection()
    try:
        db_module.init_db(conn)
        query_vec = embed.embed_text(query)
        results = db_module.query_hybrid(conn, query, query_vec, limit=rerank.RERANK_CANDIDATES)
        if not results:
            return "I don't know based on available memories."
        results = rerank.rerank(query, results, keep=limit)
        rerank.unload()
        return answer.synthesize_answer(query, results)
    finally:
        conn.close()


def handle_memory_save(fact: str, tags: str | None = None) -> str:
    from hpm import embed
    """Save a fact to memory."""
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    conn = db_module.get_connection()
    try:
        db_module.init_db(conn)
        vector = embed.embed_text(fact)
        mem_id = db_module.insert_memory(
            conn, content=fact, embedding=vector, source=config.SOURCE, tags=tag_list or None,
        )
        daily.append_to_daily_log(
            content=fact, source=config.SOURCE, tags=tag_list or None,
        )
        return f"saved: {mem_id}"
    finally:
        conn.close()


def handle_memory_wiki_find(query: str) -> str:
    """Look up a topic in the wiki; fall back to memory-find."""
    result = wiki_find.cmd_find(query)
    if result.startswith("__WIKI_FALLTHROUGH__:"):
        actual_query = result[len("__WIKI_FALLTHROUGH__:"):]
        return handle_memory_find(actual_query)
    return result


def handle_memory_capture(text: str, tags: str | None = None) -> str:
    from hpm import embed
    """Capture a conversation turn."""
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    conn = db_module.get_connection()
    try:
        db_module.init_db(conn)
        summary = summarize.summarize_turn(text)
        vector = embed.embed_text(summary)
        mem_id = db_module.insert_memory(
            conn, content=summary, embedding=vector, source=config.SOURCE, tags=tag_list or None,
        )
        daily.append_to_daily_log(
            content=summary, source=config.SOURCE, tags=tag_list or None,
        )
        return f"captured: {mem_id}"
    finally:
        conn.close()


# ── Main Loop ─────────────────────────────────────────────────────────────

TOOL_HANDLERS = {
    "memory-find": handle_memory_find,
    "memory-save": handle_memory_save,
    "memory-capture": handle_memory_capture,
    "memory-wiki-find": handle_memory_wiki_find,
}


def main() -> None:
    """Read JSON-RPC requests from stdin, dispatch, write responses to stdout."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = req.get("method", "")
        params = req.get("params", {}) or {}

        try:
            if method == "initialize":
                respond(req, {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "hpm-mcp", "version": "0.1.0"},
                })
            elif method == "tools/list":
                respond(req, {"tools": TOOL_DEFINITIONS})
            elif method == "tools/call":
                name = params.get("name", "")
                args = params.get("arguments", {}) or {}
                handler = TOOL_HANDLERS.get(name)
                if not handler:
                    respond(req, error={"code": -32601, "message": f"Unknown tool: {name}"})
                    continue

                result = handler(**args)
                respond(req, {"content": [{"type": "text", "text": str(result)}]})
            elif method == "notifications/initialized":
                pass  # Ack
            else:
                respond(req, error={"code": -32601, "message": f"Unknown method: {method}"})
        except Exception as exc:
            logger.exception("error handling %s", method)
            respond(req, error={"code": -32603, "message": "Internal error"})


if __name__ == "__main__":
    main()
