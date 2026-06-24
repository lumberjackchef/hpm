# hpm — Product Spec & Architecture

> **Version:** 0.1.0 (pre-release)
> **Description:** Hybrid Persistent Memory — a shared, single-user, local memory
> system for AI agents.
> **Repository:** `github.com/jarvis-travel/hpm`

## Vision

hpm is a local, on-device memory system for AI agents. It automatically captures
conversation turns, embeds them into a vector store, and surfaces structured,
cited answers via a multi-tier recall pipeline — all running locally with
sqlite-vec and no network dependency.

Unlike cloud-based memory (Mem0, RAG on Pinecone, etc.), hpm is designed for
**privacy, zero API cost per query, and sub-100ms recall** on a single-user
workstation. It's the memory layer an agent reaches for before reaching for
a cloud API.

---

## Core Concepts

### 1. Memory Entry

The fundamental unit of storage. Each entry represents a single captured
conversation turn or an explicitly saved fact.

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID v4 | Unique identifier |
| `content` | text | Summarized memory entry (2–4 bullets) or raw fact |
| `embedding` | vector(384) | BGE-small-en-v1.5 vector embedding |
| `source` | text[] | Agent sources, e.g. `["hermes"]` or `["hermes","pi"]` |
| `session_id` | text | Source session ID for traceability |
| `timestamp` | datetime | When captured (ISO 8601 UTC) |
| `tags` | text[] | Auto-tagged or user-assigned: project, topic, client |
| `decay_score` | float | 0.0–1.0, computed by cron evaluator |
| `access_scope` | text | `"all"` or `"private"` (for future team mode) |
| `last_accessed` | datetime | Updated on every query hit |

### 2. Three Memory Jobs

The system is organized around three distinct responsibilities:

- **Storage** — Persist, index, and retrieve memory entries. Handled by
  sqlite-vec (local) with an abstracted backend interface (future: Turso).
  Supports WAL mode, write retry with exponential backoff, and FTS5 keyword
  search.
- **Injection** — Get relevant memories into the agent's context window at
  session start. Currently a manual agent call to `memory-find`; future work
  includes automatic context injection from the wiki index.
- **Recall** — Find and synthesize relevant memories from a user query.
  Implemented as a multi-tier pipeline (see Architecture).

### 3. Multi-Tier Recall Pipeline

| Tier | What | When | Cost |
|------|------|------|------|
| **Tier 0** | Wiki page lookup | Planned | O(1) file read |
| **Tier 1** | Hybrid vector + BM25 search | Always | ~20ms (embedding) + ~5ms (sqlite) |
| **Tier 2** | Cross-encoder reranker | Optional (`--no-rerank` to skip) | ~200ms transient load, then ~50ms per batch |
| **Tier 3** | Cited-answer LLM synthesis | `hpm answer` or `memory-find` | One LLM call (~256 tokens) |

Tier 0 short-circuits the pipeline when the wiki already has a compiled page
on the topic. Tiers 1–3 run sequentially on every `hpm answer` call.

### 4. Source Agents

hpm is agent-agnostic at the storage layer. Every memory entry is tagged
with its source agent (`hermes`, `pi`, `cli`). Multiple agents write to
the same store and recall from it. The CLI bridge pattern keeps agent-specific
code out of the storage layer — Hermes has a sidecar, Pi will have a
TypeScript extension, both call the same `hpm` CLI.

### 5. Tags

Free-form string tags (e.g. `project:jarvis`, `topic:payments`, `client:acme`).
Stored as a JSON array, filterable in queries. Used for:
- Scoping recall to a project or domain
- Clustering memories for wiki compilation
- Cross-referencing with the wiki page taxonomy

### 6. Decay Score

A float [0.0, 1.0] indicating how current a memory is. Computed by the cron
evaluator using an exponential decay formula based on age. The LLM spot-check
pass can adjust scores up (VALID → +0.05) or down (STALE → -0.3). Entries
below the eviction threshold are candidates for archival.

### 7. Daily Log

A secondary plain-text append-only log at `~/.hpm/daily/YYYY-MM-DD.md`.
Every capture and save also appends to today's log. This is an **audit trail
and text backup** only — never the recall source. The primary recall source
is the vector store.

---

## Architecture

### High-Level Design

```
┌────────────────────────────────────────────────────────────┐
│                     Agent Layer                              │
│  Hermes (sidecar)    Pi (TS extension)    CLI / MCP tools    │
└─────────┬────────────────────┬────────────────────┬─────────┘
          │                    │                    │
          │      hpm CLI / MCP Server               │
          │  (capture / query / save / answer)       │
          └──────────┬──────────────────────────────┘
                     │
          ┌──────────▼──────────────────────────────┐
          │            Recall Pipeline                │
          │                                           │
          │  Tier 0: Wiki (planned)                   │
          │    ~/.hpm/wiki/{entities,concepts,...}/   │
          │                                           │
          │  Tier 1: Hybrid Search                    │
          │    sqlite-vec (ANN) + FTS5 (BM25)         │
          │                                           │
          │  Tier 2: Cross-encoder Reranker           │
          │    sentence-transformers (transient)       │
          │                                           │
          │  Tier 3: Cited-Answer Synthesis            │
          │    LLM (opencode/anthropic/openai/...)     │
          └──────────┬──────────────────────────────┘
                     │
          ┌──────────▼──────────────────────────────┐
          │           Storage Layer                   │
          │                                           │
          │  Local (default):                         │
          │    sqlite-vec → ~/.hpm/memories.db        │
          │    + WAL mode + busy_timeout + retry       │
          │                                           │
          │  Future: Turso/libSQL                      │
          │    (team mode, cloud-optional)             │
          │                                           │
          │  Secondary: Daily Log                      │
          │    ~/.hpm/daily/YYYY-MM-DD.md              │
          └───────────────────────────────────────────┘
```

### LLM Client

A multi-provider wrapper supporting:
- **OpenAI-compatible**: OpenCode Go, OpenAI, OpenRouter
- **Anthropic Messages API**: Claude

Configured via `HPM_LLM_PROVIDER` and the corresponding `*_API_KEY` /
`*_BASE_URL` env vars. Used for summarization, answer synthesis, wiki
compilation, and decay spot-checks.

### Embedding Engine

- **Model:** `BAAI/bge-small-en-v1.5` (384-dimensional)
- **Runtime:** fastembed (ONNX, CPU only)
- **Cost:** ~3ms per embedding, zero API calls
- **Lifecycle:** Loaded on demand, cached in process

### Dedup & Conflict Resolution

On every `capture` or `save`, checks for a near neighbor (cosine > 0.85)
before inserting:

1. **No match** → insert new entry
2. **Semantic match** → merge (update timestamp, append source, combine tags)
3. **Content differs** despite match → insert as new entry (future: flag
   contradiction for LLM review)

### Write Retry

SQLite `busy_timeout=100ms` (fail fast), Python retry with exponential
backoff: 100ms → 200ms → 400ms → 800ms → 1600ms (5 attempts). This
prevents write contention between concurrent agents (Hermes sidecar,
CLI, future Pi extension) while keeping latency predictable.

---

## Technical Stack

| Concern | Choice |
|---------|--------|
| Language | Python ≥3.11 |
| CLI framework | Click |
| Vector store | sqlite-vec (sqlite3 + vec0 virtual table) |
| Embedding | fastembed (BGE-small-en-v1.5, ONNX, CPU) |
| Reranker | sentence-transformers (cross-encoder/ms-marco-MiniLM-L-6-v2, optional dep) |
| Keyword search | FTS5 (built into sqlite) |
| LLM client | httpx, multi-provider (OpenAI-compatible + Anthropic) |
| Testing | pytest, pytest-cov |
| Linting | ruff |
| Typing | mypy (strict) |

### Why This Stack

Every choice is driven by the **single-user, local-only** constraint:
- sqlite-vec vs pgvector — zero daemons, zero network, zero ops. One file.
- fastembed ONNX — CPU-only inference, no CUDA dependency, ~3ms per embedding.
- sentence-transformers as optional dep — heavy (200MB+ RAM spike), loaded only
  on query and unloaded after. Keeps the baseline dependency light.
- Click — standard Python CLI framework, zero surprises.
- httpx — lightweight HTTP client with good async support (future use).

---

## Current Capabilities (MVP)

All Phase 1, 2, 4, and 5 features are implemented:

### Phase 1 — Foundation ✓
- [x] `hpm setup` — interactive LLM provider configuration
- [x] `hpm capture` — summarize conversation turn → embed → store
- [x] `hpm query` — search with vector, keyword, or hybrid mode
- [x] `hpm save` — store an explicit fact (no summarization)
- [x] `hpm status` — display store statistics
- [x] sqlite-vec schema with WAL mode
- [x] write retry with exponential backoff
- [x] FTS5 keyword search with triggers
- [x] Daily log append as secondary audit trail

### Phase 2 — Enhancement ✓
- [x] `hpm answer` — full recall: hybrid → reranker → cited answer
- [x] Cross-encoder reranker (optional, `pip install hpm[reranker]`)
- [x] MCP server with `memory-find`, `memory-save`, `memory-capture` tools
- [x] `hpm_mcp_server.py` — stdio JSON-RPC for Hermes and Claude Code

### Phase 4 — Cross-Agent Coherence ✓
- [x] Dedup on capture: `merge_or_insert` with 0.85 cosine threshold
- [x] Source merging (JSON array of agent identifiers)
- [x] `access_scope` field for future private/team scoping

### Phase 5 — Observability ✓
- [x] `hpm decay --run` — exponential decay score computation
- [x] `hpm decay --spot-check` — LLM quality audit of lowest-scoring entries
- [x] `hpm dashboard` — self-contained HTML dashboard
- [x] Cron integration: decay evaluator + spot-check pass

---

## Planned Features

### Phase 3 — Pi Extension
Pi integration via TypeScript extension API. Two paths:
- **Preferred:** Pi's extension hooks (per-turn, documentation TBD)
- **Fallback:** Poll Pi's session file (same pattern as Hermes sidecar)

Delivers same MCP tools (`memory-find`, `memory-save`, `memory-capture`)
to Pi agents. See `planned/PI_EXTENSION.md`.

### Wiki Layer (Tier 0)
A structured markdown wiki at `~/.hpm/wiki/` that sits above the vector
pipeline. Compiles knowledge from captured memories into curated, cross-
referenced, contradiction-aware pages. An agent calls `memory-wiki-find`
first — O(1) file read if the topic is known, fall through to Tier 1–3
if not. See `planned/WIKI_LAYER.md`.

### Conflict Detector
An LLM-based cron pass that finds contradictory memory entries (same tags
and keywords, different facts, different timestamps) and marks the older
one as superseded. Building on the existing `merge_or_insert` dedup layer
with deeper semantic contradiction detection. See `planned/CONFLICT_DETECTOR.md`.

### Team Mode (Turso)
Optional cloud-backed storage via Turso/libSQL for teams that want a shared
memory store. Local-first by default — `HPM_BACKEND=turso` to opt in. Same
CLI commands, same data model, same embedding. See `planned/TEAM_MODE.md`.

### Agent Context Injection
Read relevant wiki pages or recent memories at session start and inject
them into the agent's system prompt. Currently handled manually by agents
calling `memory-find` — future: automatic pre-injection from the wiki index.

---

## ADRs — Architecture Decision Records

### ADR-001: sqlite-vec over pgvector

- **Status:** Resolved
- **Date:** Phase 1
- **Context:** Single-user local memory. No PostgreSQL, no RLS, no network.
- **Decision:** Use sqlite-vec — a SQLite extension that adds vector search
  via `vec0` virtual tables. Ships as a Python package. No daemon, no config,
  no network port. WAL mode + busy_timeout + retry handles concurrency.
- **Consequences:** +Zero ops, zero network dependency. —Different syntax
  from pgvector, must maintain a separate schema path if team mode (Turso)
  is added later.

### ADR-002: Every-Turn Capture

- **Status:** Resolved
- **Date:** Phase 1
- **Context:** After each conversation turn, not end-of-session.
- **Decision:** Capture runs after every user↔assistant exchange. Prevents
  memory gaps from long sessions where only the final summary survives.
- **Consequences:** +Finer-grained recall, no loss from truncated sessions.
  —Higher write volume (mitigated by WAL batch writes).

### ADR-003: Summarization Before Embedding

- **Status:** Resolved
- **Date:** Phase 1
- **Context:** Raw transcripts are verbose. Embedding 10K tokens per turn
  is wasteful and noisy.
- **Decision:** Condense each turn into 2–4 bullet points via LLM, then
  embed the summary. The daily log preserves the original for audit.
- **Consequences:** +Compact vectors (384d per turn), cheaper storage.
  —Lossy: some nuance may be lost in summarization. —Requires an LLM call
  on every capture.

### ADR-004: Daily Log as Secondary (Not Recall Source)

- **Status:** Resolved
- **Date:** Phase 1
- **Context:** Early design considered batch-embedding from daily logs.
- **Decision:** The daily log is a plain-text backup and audit trail only.
  The vector store is the sole primary recall source. Do not revert to
  batch-embedding from daily logs.
- **Consequences:** +Vector recall is always up-to-date (no batch delay).
  +Daily log remains human-readable. —Two writes per capture (vector + log).

### ADR-005: Hermes Sidecar Polls state.db

- **Status:** Resolved
- **Date:** Phase 1
- **Context:** Hermes Agent does not expose a post-turn plugin hook.
- **Decision:** The sidecar polls `~/.hermes/state.db` (Hermes' SQLite
  session store) for new completed turns. No Hermes plugin code needed.
- **Consequences:** +Works with unmodified Hermes. —Polling adds ~5s
  latency. —Fragile if Hermes changes its state.db schema.

### ADR-006: Pi Auto-Capture Via Extension API

- **Status:** Open
- **Date:** Phase 3 (planned)
- **Context:** Pi uses a TypeScript extension API. Unclear if it provides a
  post-turn hook.
- **Decision:** Use Pi's extension hooks if available. Fall back to polling
  its session file (same pattern as the Hermes sidecar).
- **Consequences:** Confirmed during Phase 3 implementation.

### ADR-007: BGE-small-en-v1.5 for Embeddings

- **Status:** Resolved
- **Date:** Phase 1
- **Context:** Need a local, CPU-only embedding model with good semantic
  retrieval quality and small footprint.
- **Decision:** Use `BAAI/bge-small-en-v1.5` (384d) via fastembed (ONNX
  runtime). ~3ms per embedding on CPU, zero GPU or API dependency.
- **Consequences:** +Fast, private, free. —384d is lower resolution than
  ada-002 (1536d) but sufficient for single-user recall. Model is
  configurable via `HPM_EMBEDDING_MODEL`.

### ADR-008: Cross-Encoder Reranker Loaded Transiently

- **Status:** Resolved
- **Date:** Phase 2
- **Context:** Cross-encoders are more accurate than bi-encoders for
  re-ranking but use ~200MB RAM and load slowly.
- **Decision:** Load the reranker (`cross-encoder/ms-marco-MiniLM-L-6-v2`)
  on demand when `hpm answer` or `memory-find` is called, then unload.
  Optional dependency: `pip install hpm[reranker]`.
- **Consequences:** +Low baseline memory (~50MB without reranker). —200ms
  transient load penalty on first answer call per process.

### ADR-009: Multi-Provider LLM Abstraction

- **Status:** Resolved
- **Date:** Phase 1
- **Context:** Different users have different LLM accounts and preferences.
  Hardcoding one provider limits adoption.
- **Decision:** Abstract LLM calls behind a `complete()` function that
  routes to OpenAI-compatible or Anthropic endpoints based on
  `HPM_LLM_PROVIDER`. New providers require adding a config entry and a
  transport function.
- **Consequences:** +Provider-agnostic by default. +Easy to add new
  providers. —Different providers have subtly different API shapes (system
  prompt placement, message ordering).

### ADR-010: WAL Mode + Exponential Backoff Write Retry

- **Status:** Resolved
- **Date:** Phase 1
- **Context:** Multiple agents (Hermes sidecar, CLI, future Pi) may write
  concurrently. SQLite default journal mode blocks readers during writes.
- **Decision:** WAL journal mode for concurrent reads/writes. Python-level
  retry with exponential backoff (5 attempts, 100ms base) for SQLITE_BUSY.
- **Consequences:** +Near-zero write contention. +Readers never block on
  writers. —Slightly larger journal file (WAL). Retry adds latency during
  contention spikes.

### ADR-011: Wiki as Tier 0 Above the Vector Pipeline

- **Status:** Resolved (planned)
- **Date:** WIKI_LAYER.md
- **Context:** The recall pipeline reconstructs answers from scratch every
  query, even for well-known topics.
- **Decision:** Add a markdown wiki at `~/.hpm/wiki/` as Tier 0. Agents
  check the wiki first; if a page exists, return it directly. If not, fall
  through to Tier 1–3. The wiki is compiled from memories by an LLM pass
  and curated by `hpm wiki compile` and `hpm wiki sync`.
- **Consequences:** +Common queries become O(1) (read one file). +Knowledge
  is human-readable and linkable. —Wiki pages can drift from current
  knowledge without periodic sync.

### ADR-012: LLM-Based Contradiction Detection in Cron Pass

- **Status:** Resolved (planned)
- **Date:** CONFLICT_DETECTOR.md
- **Context:** The dedup layer (cosine 0.85) can merge refinements but
  can't distinguish a refinement from a contradiction. Both end up as
  separate entries with no conflict flag.
- **Decision:** A periodic cron pass finds candidate pairs (high tag/keyword
  overlap, low vector similarity, different timestamps) and asks the LLM
  to classify them as CONTRADICTION, REFINEMENT, or UNRELATED. On
  CONTRADICTION, mark the older entry with `superseded_by`.
- **Consequences:** +Stale/overridden facts are flagged in recall output.
  —LLM calls cost but run on cron, not on every query. —False positives
  possible (mitigated by low temperature and review field).

### ADR-013: Local-First, Cloud-Optional with Turso

- **Status:** Open
- **Date:** TEAM_MODE.md (planned)
- **Context:** Teams want a shared memory store without deploying a server.
- **Decision:** Add an optional Turso/libSQL backend that mirrors the local
  schema. Backend abstraction (`Backend` protocol) in `src/hpm/backend.py`.
  Default remains local sqlite-vec. `HPM_BACKEND=turso` to opt in.
- **Consequences:** +No server to deploy (Turso is managed). +Schema is
  nearly identical to local (libSQL is a SQLite fork). —Network latency on
  queries. —Turso free tier limits (500MB, 30M reads/month).

### ADR-014: FTS5 for Keyword Search

- **Status:** Resolved
- **Date:** Phase 1
- **Context:** Need keyword-level recall alongside vector similarity.
  Some queries are better served by exact keyword matching (names, version
  numbers, specific terms).
- **Decision:** Use SQLite's built-in FTS5 extension for BM25 keyword
  search. Triggers keep the FTS index in sync with the `memories` table.
  Hybrid search fuses vector (0.7 weight) and keyword (0.3 weight) scores.
- **Consequences:** +No external search engine. +BM25 is well-understood
  and effective for keyword queries. —FTS5 query syntax is non-standard
  (requires escaping for user queries).

### ADR-015: Click Over argparse or Typer

- **Status:** Resolved
- **Date:** Phase 1
- **Context:** Need a CLI framework with subcommand support, option parsing,
  help text, and type validation.
- **Decision:** Use Click. It's the de facto standard for Python CLI tools,
  has excellent subcommand support via `@click.group()`, and integrates
  cleanly with type annotations.
- **Consequences:** +Well-documented, widely used. +Automatic help text
  generation. —Slightly more verbose than Typer for simple commands.

---

## Verification

Before each commit or release:

- [ ] Python syntax: `python3 -c "import ast; ast.parse(open(f).read())"` for
      new or changed .py files
- [ ] sqlite-vec schema is compatible with the data model in this spec
- [ ] CLI changes: update this spec and AGENTS.md
- [ ] Embedding/reranker model changes: update RAM footprint analysis
- [ ] Tests: `make test` (or `pytest`)
- [ ] Lint: `make lint` (ruff)
- [ ] Type-check: `make typecheck` (mypy --strict)
