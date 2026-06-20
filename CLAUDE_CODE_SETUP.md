# Claude Code Integration

Claude Code (Anthropic's CLI coding agent) can use hpm's memory system through the same MCP server that Hermes uses. This gives Claude Code three memory tools: `memory-find`, `memory-save`, and `memory-capture`.

## Setup

### 1. Register the MCP Server

Claude Code supports project-level MCP server config via `.mcp.json` at the repo root. The file is already in this repo — just make sure the paths point to your local setup.

**`/Users/ryanpearson/code/hpm/.mcp.json`:**

```json
{
  "mcpServers": {
    "hpm": {
      "command": "/Users/ryanpearson/code/hpm/.venv/bin/python3",
      "args": ["/Users/ryanpearson/code/hpm/hpm_mcp_server.py"]
    }
  }
}
```

If the engineer has the repo checked out at a different path, update the `command` and `args` paths accordingly.

### 2. Ensure the API Key is Available

The MCP server needs `OPENCODE_GO_API_KEY` in its environment for summarization and answer synthesis. Add it to `~/.hermes/.env` (Hermes's env file is shared):

```bash
echo 'OPENCODE_GO_API_KEY="***"' >> ~/.hermes/.env
```

Or add it to the shell profile (`~/.zshrc`, `~/.bashrc`). When Claude Code launches the MCP server, it inherits the parent shell's environment.

### 3. Verify

Start Claude Code in the repo root and try:

```
/memory-find What decisions have been made about this project?
```

If the MCP server is connected, Claude Code will call the tool and return stored memories. You can also test directly:

```bash
hpm status
```

## Tools Available

Once registered, Claude Code automatically discovers these tools:

| Tool | What it does |
|---|---|
| `memory-find` | Search memory with hybrid search + reranker + cited answer. Ask "search memory for X" or "what do we know about Y". |
| `memory-save` | Save a fact explicitly: "remember that we chose Paddle over Stripe". |
| `memory-capture` | Summarize and store a conversation turn. |

## Auto-Capture

Claude Code doesn't have a post-turn hook, so auto-capture works through the Hermes sidecar (`hpm sidecar`). If the engineer also runs Hermes, the sidecar captures all agent conversations (both Hermes and Claude Code) into the shared store — no separate sidecar needed for Claude Code.

If they only use Claude Code, they can save facts explicitly with `memory-save` or periodically run:

```bash
hpm capture "User: ... Assistant: ..." --tags project:my-project
```

## CLAUDE.md Instructions

Add the following to your project's `CLAUDE.md` so Claude Code knows about memory:

```markdown
## Memory System

This project uses hpm (Hermes Pi Memory) for persistent cross-session memory.
MCP tools `memory-find`, `memory-save`, and `memory-capture` are available.

- **Save important decisions**: when you learn a project-specific fact,
  convention, or decision, use `memory-save` to persist it.
- **Search before asking**: if you need context the user hasn't provided,
  try `memory-find` first — the answer may already be stored.
- **Cite sources**: `memory-find` returns cited answers with entry IDs
  and timestamps.
```

## How It Works

```
Claude Code session
  → memory-find / memory-save / memory-capture (via MCP)
  → hpm_mcp_server.py (stdio subprocess)
  → hpm Python library
    → sqlite-vec vector store (~/.hermes/memories/memories.db)
    → BGE-small embedding (fastembed/ONNX, ~3ms)
    → OpenCode Go summarization (for capture)
    → OpenCode Go cited-answer synthesis (for find)
```

All data is local — same store, same models, shared between Hermes and Claude Code.
