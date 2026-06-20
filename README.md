# hpm — Hybrid Persistent Memory

Shared local vector memory for AI agents. Every conversation turn is automatically captured, summarized, embedded, and stored for semantic recall — all on-device, zero cloud costs.

## Quick Start

```bash
# Install
pip install -e .

# Run setup to configure your LLM provider
hpm setup

# Save a fact
hpm save "The sky is blue" --tags topic:weather

# Capture a conversation turn (requires OPENCODE_GO_API_KEY)
hpm capture "User: what's the capital of France? Assistant: Paris" --tags topic:geography

# Query memory
hpm query "weather fact"
hpm query "capital city" --mode vector

# Full recall pipeline: hybrid search → reranker → cited answer
hpm answer "weather fact"

# Run auto-capture daemon
hpm sidecar

# Check store stats
hpm status

# Generate and open memory dashboard
hpm dashboard

# Run decay evaluator
hpm decay --run && hpm decay --spot-check
```

## How It Works

### Capture Pipeline

```
Conversation turn
  → OpenCode Go API (summarize to 2-4 bullets)
  → BGE-small (local embedding, ~20ms)
  → sqlite-vec vector store
  → ~/.hermes/memories/daily/YYYY-MM-DD.md (plain-text backup)
```

### Recall Pipeline (Tier 1 → 2 → 3)

```
User query
  → Tier 1: Hybrid search (vector cosine + BM25 keyword)
  → Tier 2: Cross-encoder reranker (transient load, ~200MB)
  → Tier 3: Cited-answer synthesis via OpenCode Go
```

### Auto-Capture (Sidecar)

The sidecar (`hpm sidecar`) polls `~/.hermes/state.db` — the SQLite session store Hermes uses internally. It detects new user→assistant message pairs, summarizes them, embeds them, and stores them. A JSON cursor file tracks position so it survives restarts.

### Dedup & Coherence

Every insert checks for a near neighbor (cosine > 0.85). If found, sources and tags are merged instead of creating a duplicate row. Conflicting facts are handled via timestamp-win with a `superseded_by` pointer.

### Decay & Hygiene

Memory scores decay exponentially (half-life: 1 week) and are reinforced on every retrieval. The spot-check loop samples low-scoring entries and rates them via LLM (STALE / QUESTIONABLE / VALID) to catch stale information.

## CLI Commands

| Command | Description |
|---------|-------------|
| `hpm setup` | Interactive configuration walkthrough (provider, API key, model) |
| `hpm save <fact>` | Save an explicit fact (no summarization) |
| `hpm capture <text>` | Capture a turn: summarize, embed, store |
| `hpm query <query>` | Search memory (vector, keyword, or hybrid) |
| `hpm answer <query>` | Full recall pipeline: search → rerank → cited answer |
| `hpm sidecar` | Run the Hermes state.db auto-capture daemon |
| `hpm status` | Show store statistics |
| `hpm decay --run` | Compute decay scores for all entries |
| `hpm decay --spot-check` | LLM spot-check low-scoring entries |
| `hpm dashboard` | Generate and open HTML dashboard in browser |

## MCP Server (Hermes + Claude Code)

Register with Hermes for native tool access:

```bash
hermes mcp add hpm --command /path/to/python3 --args /path/to/hpm_mcp_server.py
```

Or with Claude Code via `.mcp.json` at the repo root (already configured):

```json
{
  "mcpServers": {
    "hpm": {
      "command": "/path/to/python3",
      "args": ["/path/to/hpm_mcp_server.py"]
    }
  }
}
```

Exposes `memory-find`, `memory-save`, and `memory-capture` as tools in both agents.

See [`CLAUDE_CODE_SETUP.md`](CLAUDE_CODE_SETUP.md) for full Claude Code integration details.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENCODE_GO_API_KEY` | — | **Required** for summarization and answer synthesis |
| `OPENCODE_GO_BASE_URL` | `https://opencode.ai/zen/go/v1` | API base URL |
| `HPM_SUMMARIZATION_MODEL` | `minimax-m2.5` | Model for summarization |
| `HPM_EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` | Model for embeddings |
| `HPM_DB_PATH` | `~/.hermes/memories/memories.db` | Vector database path |

## Project Structure

```
src/hpm/
  __main__.py     CLI entry point
  answer.py       Cited-answer synthesis (Tier 3)
  cli.py          Command implementations
  config.py       Paths and environment defaults
  daily.py        Daily markdown audit trail
  dashboard.py    Self-contained HTML dashboard generator
  db.py           sqlite-vec schema, WAL mode, dedup, decay, queries
  decay.py        Decay evaluator and LLM spot-check
  embed.py        BGE-small embedding (lazy-loaded singleton)
  rerank.py       Cross-encoder reranker (Tier 2, transient load)
  sidecar.py      Hermes state.db poller daemon
  summarize.py    OpenCode Go API client
hpm_mcp_server.py  MCP server for Hermes agent integration
tests/            pytest suite (46 tests)
```

## Development

```bash
make dev       # install dev dependencies
make lint      # ruff
make typecheck # mypy
make test      # pytest
```

## Architecture (Immutable)

1. **Three memory jobs** — Storage, Injection, Recall. Separate concerns.
2. **Immediate embedding** — Every turn embedded on capture (~20ms). No batch deferral.
3. **Shared CLI bridge** — Both Hermes and Pi communicate through the `hpm` CLI.
4. **sqlite-vec with WAL** — WAL mode, busy_timeout=5000, exponential-backoff retry.
5. **Local embeddings** — Zero API cost, on-device CPU.
6. **Daily log as backup** — Not the recall source.

## Phase Plan

| Phase | What | Status |
|-------|------|--------|
| **1** | Foundation: sqlite-vec + CLI + Hermes sidecar | ✅ |
| **2** | Enhancement: hybrid search, reranker, cited answers, MCP server | ✅ |
| **3** | Pi extension: TypeScript extension for Pi | ⏸️ Deferred |
| **4** | Coherence: dedup, conflict resolution, schema migration | ✅ |
| **5** | Observability: decay evaluator, spot-check, dashboard, status | ✅ |
