# hpm — Hermes Pi Memory

Shared local vector memory for [Hermes Agent](https://hermes-agent.nousresearch.com) and Pi Coding Agent. Every conversation turn is automatically captured, summarized, embedded, and stored for semantic recall — all on-device, zero cloud costs.

## Quick Start

```bash
# Install
pip install -e .

# Save a fact
hpm save "The sky is blue" --tags topic:weather

# Capture a conversation turn (requires OPENCODE_GO_API_KEY)
hpm capture "User: what's the capital of France? Assistant: Paris" --tags topic:geography

# Query memory
hpm query "weather fact"
hpm query "capital city" --mode vector

# Run auto-capture daemon
hpm sidecar
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

### Multi-Tier Recall

| Tier | What | When |
|------|------|------|
| **0** | Injected context (frozen memory snapshot) | In prompt — fastest |
| **1** | Hybrid vector + FTS5 BM25 search | Semantic recall |
| **2** | Cross-encoder reranker | Reorder candidates (Phase 2) |
| **3** | Cited-answer synthesis via LLM | Final answer with sources (Phase 2) |

### Auto-Capture (Sidecar)

The sidecar (`hpm sidecar`) polls `~/.hermes/state.db` — the SQLite session store Hermes uses internally. It detects new user→assistant message pairs, summarizes them, embeds them, and stores them. A JSON cursor file tracks position so it survives restarts.

## CLI Commands

| Command | Description |
|---------|-------------|
| `hpm save <fact>` | Save an explicit fact (no summarization) |
| `hpm capture <text>` | Capture a turn: summarize, embed, store |
| `hpm query <query>` | Search memory (vector, keyword, or hybrid) |
| `hpm sidecar` | Run the auto-capture daemon |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENCODE_GO_API_KEY` | — | **Required** for summarization |
| `OPENCODE_GO_BASE_URL` | `https://opencode.ai/zen/go/v1` | API base URL |
| `HPM_SUMMARIZATION_MODEL` | `minimax-m2.5` | Model for summarization |
| `HPM_EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` | Model for embeddings |
| `HPM_DB_PATH` | `~/.hermes/memories/memories.db` | Vector database path |

## Project Structure

```
src/hpm/
  __main__.py     CLI entry point
  cli.py          Command implementations
  config.py       Paths and environment defaults
  db.py           sqlite-vec schema, WAL mode, write retry, queries
  daily.py        Daily markdown audit trail
  embed.py        BGE-small embedding (lazy-loaded singleton)
  sidecar.py      Hermes state.db poller daemon
  summarize.py    OpenCode Go API client
tests/            pytest suite (27 tests)
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

| Phase | What |
|-------|------|
| **1** ✅ | Foundation: sqlite-vec + CLI + Hermes sidecar |
| **2** | Enhancement: reranker + cited answers + /memory-find |
| **3** | Pi extension: TypeScript extension for Pi |
| **4** | Multi-agent coherence: dedup + conflict resolution |
| **5** | Observability: decay evaluator + dashboard |
