# DOX framework — hpm (Hermes Pi Memory)

DOX is a performant AGENTS.md hierarchy. This file is the root contract for the
hpm repository. Every AGENTS.md in the tree is a binding work contract for its
subtree.

## Core Contract

- AGENTS.md files are binding work contracts for their subtrees
- Work products, source materials, instructions, records, assets, and durable
  docs must stay understandable from the nearest applicable AGENTS.md plus every
  parent AGENTS.md above it

## Read Before Editing

1. Read AGENTS.md files from root to target path before editing
2. Use the nearest AGENTS.md as the local contract
3. If docs conflict, the closer doc controls

## Purpose

A shared, single-user, local memory system for Hermes Agent and Pi Coding Agent.
Provides automatic capture of conversation turns, immediate local embedding,
hybrid semantic + keyword recall, and cited-answer retrieval — all running
entirely on-device with sqlite-vec.

## Ownership

| Area | Owner |
|------|-------|
| Design plan | `hermes-pi-memory-design.html` (root) |
| External review | `deepseek-v4-pro-review.md` (root) |
| CLI tool (`hpm`) | `src/hpm/` |
| Hermes auto-capture sidecar | `src/hpm/sidecar.py` |
| Pi extension | `src/pi-extension/` (not yet created) |
| Cron evaluator | `src/evaluator/` (not yet created) |
| Dashboard | `src/dashboard/` (not yet created) |

## Local Contracts

### Architecture (immutable)

1. **Three memory jobs** — Storage, Injection, Recall. Each job is a separate concern.
2. **Multi-tier recall** — Tier 0 (injected context) → Tier 1 (hybrid vector + BM25 search) → Tier 2 (cross-encoder reranker) → Tier 3 (cited-answer synthesis via LLM).
3. **Immediate embedding** — Every captured turn is vector-embedded immediately (~20ms with BGE-small). No batch deferral. Memories must be queryable within seconds of capture.
4. **Shared CLI bridge** — Both Hermes and Pi communicate with the vector store through the `hpm` CLI. No agent-specific code in the storage layer.
5. **sqite-vec with WAL mode** — `PRAGMA journal_mode=WAL;`, `PRAGMA busy_timeout=5000;`, write retry with exponential backoff (3 attempts, 50ms base). Required from day one for concurrent access (Hermes sidecar, Pi extension, cron evaluator).
6. **Local embeddings** — BGE-small-en-v1.5 (384d) via `sentence-transformers`. On-device CPU, zero API cost.
7. **Cross-encoder reranker** — `cross-encoder/ms-marco-MiniLM-L-6-v2` for Tier 2. Loaded transiently on query (~200 MB spike, unloads after).
8. **Summarization via OpenCode Go** — `POST https://opencode.ai/zen/go/v1/chat/completions` with `OPENCODE_GO_API_KEY`. Configurable model (recommended: `minimax-m2.5` for speed/cost, `deepseek-v4-flash` for quality).
9. **Daily log as audit trail** — Captures also append to `~/.hermes/memories/daily/YYYY-MM-DD.md` as a plain-text backup, but the vector store is the primary recall source.
10. **Structured answer with citations** — Recall returns a written answer citing specific source files and timestamps. If nothing relevant is found, says so explicitly (GBrain pattern).

### Data model

| Field | Type | Description |
|-------|------|-------------|
| `id` | `text` | UUID v4 |
| `content` | `text` | Summarized memory entry (2-4 bullets) |
| `embedding` | `vector(384)` | BGE-small embedding |
| `source` | `text` | `hermes` or `pi` |
| `session_id` | `text` | Source session ID |
| `timestamp` | `datetime` | When captured |
| `tags` | `text[]` | Auto-tagged: project, topic, client |
| `decay_score` | `float` | 0.0–1.0, computed by cron evaluator |

### Build & run

```bash
# CLI entry point (when built)
hpm capture <text> [--tags ...] [--session-id] [--no-summarize]
hpm query "<query>" [--limit N] [--tags ...] [--mode vector|keyword|hybrid]
hpm save "<fact>" [--tags ...]
hpm sidecar [--once] [--poll-interval N]                            # implemented
# Phase 2+
hpm embed --batch
hpm decay --run
hpm status
```

### Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENCODE_GO_API_KEY` | Yes | API key for OpenCode Go summarization endpoint |
| `OPENCODE_GO_BASE_URL` | No | Defaults to `https://opencode.ai/zen/go/v1` |

### Key design decisions (do not change without review)

- **sqlite-vec over pgvector** — Single-user local only. No PostgreSQL, no RLS, no network.
- **Every-turn capture** — After each conversation turn, not end-of-session. Prevents memory gaps.
- **Summarization before embedding** — Raw transcripts are too bulky. Condense first, then embed.
- **Daily log as secondary** — The markdown daily log is a text backup, not the recall source. Do not revert to batch-embedding from daily logs (deepseek-v4-pro identified this as a critical flaw).
- **Hermes sidecar watches state.db** — No official post-turn hook in Hermes plugin API. Sidecar polls `~/.hermes/state.db` SQLite session store. This is an acknowledged design constraint.
- **Pi auto-capture depends on extension API** — If Pi lacks a post-turn hook, fall back to polling its session file. Confirm API surface during Phase 3.

## Work Guidance

Before any code editing in this repository, load the `code-workflow` skill and follow its instructions. This skill defines the standard engineering workflow: branching conventions, pre-edit ritual, TDD (via `tdd` skill), conventional commits, quality gates, PR creation, and documentation updates.

### Build order

Follow the 5-phase plan in `hermes-pi-memory-design.html`. Phases are sequential except Phase 4 (cross-agent coherence) may run alongside Phase 3 since dedup is pure database-layer logic.

1. **Phase 1** — Foundation: sqlite-vec + hpm CLI (capture, query, save) + Hermes sidecar
2. **Phase 2** — Hermes Enhancement: reranker + cited answers + `/memory-find`
3. **Phase 3** — Pi Extension: TypeScript extension
4. **Phase 4** — Cross-Agent Coherence: dedup + conflict resolution (parallel with Phase 3)
5. **Phase 5** — Observability: decay evaluator + dashboard + cron

### Per-commit workflow (Ryan's convention)

- One logical change per git commit
- Each commit validated (type-check / build / test) before the next starts
- Destructive operations (rm, delete) reviewed before execution

### Verification

Before each commit:
- [ ] Python code is syntactically valid (`python3 -c "import ast; ast.parse(open(f).read())"`)
- [ ] sqlite-vec schema is compatible with the data model in this AGENTS.md
- [ ] If changing the CLI interface, update both this AGENTS.md and the design doc
- [ ] If changing the embedding or reranker model, update the RAM footprint analysis

## Closeout

1. Update nearest owning AGENTS.md if the change affects purpose, structure, contracts, or workflows
2. Remove stale or contradictory text
3. Run existing verification when relevant

## Child DOX Index

_No child AGENTS.md files exist yet. Created as the project grows (likely `src/AGENTS.md`, `src/hermes-sidecar/AGENTS.md`, and `src/pi-extension/AGENTS.md` once those directories are established)._

## User Preferences

- **One logical change per git commit**, each validated before the next starts
- **Destructive ops require review** — confirm before rm/delete/drop operations
- **Chunked plans with verification** between each chunk
- **Concise terminal-friendly responses** — plain text, not markdown, for delivery
- **Styled HTML for plans/docs** — dual human+agent readability with embedded CSS and JSON metadata block
- **OpenCode Go provider** for all LLM calls (summarization, spot-checks, cited-answer synthesis) — no separate API keys
- **Single-user local only** — no PostgreSQL, no team sharing, no RLS
