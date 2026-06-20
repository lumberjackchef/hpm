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

### 2. Configure the LLM Provider

hpm supports multiple LLM providers for summarization and answer synthesis. The easiest way to configure it is:

```bash
hpm setup
```

This walks you through provider selection, API key entry, and model configuration — all saved to `~/.hpm/.env`.

Or manually via environment variables:

| Provider | `HPM_LLM_PROVIDER` | API Key | Default Model |
|---|---|---|---|
| OpenCode Go | `opencode` (default) | `OPENCODE_GO_API_KEY` | `minimax-m2.5` |
| Anthropic | `anthropic` | `ANTHROPIC_API_KEY` | `claude-sonnet-4-20250514` |
| OpenAI | `openai` | `OPENAI_API_KEY` | `gpt-4o-mini` |
| OpenRouter | `openrouter` | `OPENROUTER_API_KEY` | `anthropic/claude-sonnet-4` |

If you have an Anthropic key but no OpenCode account, set:

```bash
export HPM_LLM_PROVIDER=anthropic
export ANTHROPIC_API_KEY=sk-ant-***

# Or persist in ~/.hpm/.env:
mkdir -p ~/.hpm
echo 'HPM_LLM_PROVIDER=anthropic' >> ~/.hpm/.env
echo 'ANTHROPIC_API_KEY=sk-ant-***' >> ~/.hpm/.env
```

If you do have an OpenCode key, the default works as-is:

```bash
echo 'OPENCODE_GO_API_KEY=***' >> ~/.hpm/.env
```

To override the model for any provider:

```bash
export HPM_LLM_MODEL=claude-sonnet-4-20250514
```

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

This project uses hpm (Hybrid Persistent Memory) for persistent cross-session memory.
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
    → sqlite-vec vector store (~/.hpm/memories.db)
    → BGE-small embedding (fastembed/ONNX, ~3ms)
    → Configured LLM provider (HPM_LLM_PROVIDER) for summarization and answer synthesis
```

All data is local — same store, same models, shared between Hermes and Claude Code.
