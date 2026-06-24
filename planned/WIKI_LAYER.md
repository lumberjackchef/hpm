# Wiki Layer — Long-Term Semantic Memory for hpm

A structured markdown wiki (`~/.hpm/wiki/`) that sits **above** the vector
store in the recall pipeline. Compiles knowledge from captured memories into
durable, cross-referenced, contradiction-aware pages.

## Motivation

hpm's current recall pipeline is good at finding *relevant passages* from
conversation history, but every answer is synthesized from scratch:
Tier 1 (vector + BM25) → Tier 2 (reranker) → Tier 3 (LLM synthesis).
The synthesis is ephemeral — repeated queries on the same topic pay the full
pipeline cost every time, and the agent never accumulates a persistent
understanding.

The wiki adds a **Tier 0**: before hitting the vector pipeline, check whether
a curated wiki page already exists on this topic. Reading a markdown file is
O(1) — no embedding, no reranker, no LLM synthesis for known topics.

| Dimension | hpm vector store (episodic) | Wiki (semantic) |
|-----------|------------------------------|-----------------|
| What | Conversation turn summaries | Compiled knowledge pages |
| Granularity | 2–4 bullet points | Full document per topic |
| Persistence | Rows in sqlite-vec | Markdown files in `~/.hpm/wiki/` |
| Query cost | Vector → reranker → LLM | O(1) file read |
| Cross-refs | None | [[wikilinks]] |
| Contradictions | Implicit (merge_or_insert) | Explicit frontmatter (`contested`) |
| Confidence | Implicit (distance score) | Explicit (`high/medium/low`) |
| Human-editable | sqlite-vec (not easily) | Markdown (any editor, Obsidian) |

## Architecture

```
Recall pipeline (before this change):
  Tier 1 (hybrid vec+BM25) → Tier 2 (reranker) → Tier 3 (LLM synthesis)

Recall pipeline (after):
  Tier 0 (wiki check) → Tier 1 (hybrid) → Tier 2 (reranker) → Tier 3 (LLM synthesis)
```

Tier 0 short-circuits: if an agent asks for something the wiki covers, the
answer comes from the wiki page at zero pipeline cost. If the wiki doesn't
cover it, fall through to the existing recall pipeline — and optionally file
the result back to the wiki so the next query hits Tier 0.

## Wiki Directory Structure

```
~/.hpm/wiki/
├── SCHEMA.md           # Conventions, tag taxonomy, page thresholds
├── index.md            # Sectioned content catalog with one-line summaries
├── log.md              # Append-only action log (compilation, updates, lint)
├── entities/           # Entity pages (people, products, projects, orgs)
├── concepts/           # Concept/topic pages (architectures, techniques, domains)
├── comparisons/        # Side-by-side analyses (trade-off tables)
└── queries/            # Filed query results worth keeping
```

### Frontmatter (every page)

```yaml
---
title: Page Title
created: 2026-06-24
updated: 2026-06-24
type: entity | concept | comparison | query
tags: [project:jarvis, topic:payments]
sources: [memory:abc123-def, memory:456-789]
confidence: high | medium | low
contested: false        # set when contradicted by another page
contradictions: []      # list of page slugs this one conflicts with
---
```

### SCHEMA.md

Generated on `hpm wiki init`. Defines the tag taxonomy, page creation
thresholds, and naming conventions. Every new page must conform.

### index.md

Auto-generated catalog of all wiki pages, grouped by type. Regenerated
by `hpm wiki lint --fix` or `hpm wiki sync`.

## CLI Commands

### `hpm wiki init`

Create `~/.hpm/wiki/` with SCHEMA.md, index.md, log.md and empty subdirs.

### `hpm wiki compile [--topic QUERY] [--tags TAGS]`

The core compilation command.

1. Runs the full Tier 1–3 pipeline on the topic
2. Passes the synthesized answer + source memories to the LLM
3. The LLM writes a structured wiki page (frontmatter + markdown body)
4. Saves to `concepts/{slug}.md` or `entities/{slug}.md`
5. Updates `index.md` and appends to `log.md`

If a page already exists on the topic, the LLM merges or flags contradiction:

- **Refinement** — new info adds to existing page, bumps `updated` date
- **Contradiction** — new info conflicts. Both positions noted in the page
  body with dates and sources. Frontmatter gets `contested: true`. The
  conflict detector plan (`CONFLICT_DETECTOR.md`) can handle this at scale.

### `hpm wiki find <query>`

Read `index.md` → scan for matching topic. If found, read the page and
return its content. Falls through to `hpm answer` (Tier 1–3) if no match.

```python
# Pseudocode
wiki_pages = search_index(query)  # simple keyword match against index.md
if wiki_pages:
    return read_pages(wiki_pages)
return answer(query)  # fall through to vector pipeline
```

### `hpm wiki sync`

Batch compilation from recent memories.

1. Scan memories from the last N hours (default 24)
2. Cluster by tag/keyword overlap
3. For each cluster: if no wiki page exists → run `compile`
4. For existing pages with new related memories → run `compile` in merge mode
5. Update index, log

Designed to run alongside the decay cron pass (Phase 5).

### `hpm wiki lint`

Karpathy-style health check:

- **Orphan pages** — pages with zero inbound [[wikilinks]]
- **Broken links** — [[wikilinks]] pointing to non-existent pages
- **Index completeness** — every wiki page in index.md, no stale index entries
- **Frontmatter validation** — required fields, valid tags from taxonomy
- **Stale content** — pages where `updated` >90 days from newest related memory
- **Contradictions** — pages with `contested: true` or `contradictions:` set
- **Low confidence** — pages with `confidence: low` flagged for review
- **Large pages** — >200 lines, candidates for splitting
- **Empty subdirs** — entities/, concepts/, comparisons/ directories with files
  not represented in index

Outputs severity-grouped report.

## MCP Tools

New tool for the existing `hpm_mcp_server.py`:

### `memory-wiki-find`

```json
{
  "name": "memory-wiki-find",
  "description": "Look up a topic in the compiled knowledge wiki. Faster than memory-find for known topics. Falls back to memory-find if the wiki doesn't cover it.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "query": { "type": "string" }
    },
    "required": ["query"]
  }
}
```

Agents call `memory-wiki-find` first. If it returns a wiki page, done. If
the result says "not found in wiki, fell back to memory-find" with a
synthesis result, they get an answer regardless.

### Agent prompt integration

When injecting context at session start, the sidecar or agent system can
read `~/.hpm/wiki/index.md` and inject a summary of what the wiki covers
into the system prompt. This gives the agent awareness of what knowledge
exists without reading every page.

## Implementation Phases

### Phase A — Foundation (1–2 sessions)

- `hpm wiki init` — create directory structure, SCHEMA.md, empty index.md
- `hpm wiki compile` — one-shot: run Tier 3 pipeline, write page
- `hpm wiki find` — index scan → file read → return content
- MCP `memory-wiki-find` tool
- `config.py` — add `WIKI_DIR = HPM_DIR / "wiki"`

**Files to create:**
- `src/hpm/wiki/__init__.py`
- `src/hpm/wiki/init.py` — `cmd_init()` creates dirs + SCHEMA.md
- `src/hpm/wiki/compile.py` — `cmd_compile()` orchestrates LLM → write
- `src/hpm/wiki/find.py` — `cmd_find()` scans index, reads files
- `src/hpm/wiki/types.py` — frontmatter parser, slug generation, helpers

### Phase B — Sync & Lint (1 session)

- `hpm wiki sync` — batch compilation from recent captures
- `hpm wiki lint` — orphan/broken-link/contradiction/staleness checks
- `hpm wiki lint --fix` — auto-fix index, regenerate index.md
- Update `cli.py` with the wiki command group

### Phase C — Integration (1 session)

- Agent prompt injection: sidecar reads wiki/index.md into session context
- Cron integration: `hpm wiki sync` in the decay cron pass
- Contradiction awareness in `answer.py` synthesis when `contested` is set
- Update AGENTS.md with wiki ownership

## Risks

| Risk | Mitigation |
|------|------------|
| Wiki drifts from current knowledge | `hpm wiki lint` surfaces stale pages; `hpm wiki sync` refreshes them on cron |
| Pages accumulate, index grows unmanageable | Split into sub-sections at 50 entries per section; archive to `_archive/` |
| LLM writes inconsistent pages | SCHEMA.md defines strict frontmatter + tag taxonomy; lint validates |
| False confidence — wiki says something confidently that's outdated | `confidence` + `updated` frontmatter fields let readers judge recency |
| Compilation costs (LLM calls per topic) | Only pays once per topic, unlike the per-query Tier 3 synthesis it replaces |
| Duplicate pages on same topic | `hpm wiki compile` always checks existing pages first; `--force` to override |

## Relation to Existing Plans

- **CONFLICT_DETECTOR.md** — Wiki contradictions use the same superseded-by
  concept. The conflict detector handles the vector store; the wiki handles
  the markdown layer. They share the LLM judgment pass.
- **PI_EXTENSION.md** — Pi writes to the same wiki directory. Cross-agent
  contradictions are surfaced during `hpm wiki lint`.
- **TEAM_MODE.md** — Shared wiki is the natural knowledge base for a team of
  agents. Each agent compiles and reads from the same `~/.hpm/wiki/`.

## Verification

Before each commit:
- `python3 -c "import ast; ast.parse(open(f).read())"` for new Python files
- `hpm wiki init` followed by `hpm wiki lint` produces no errors
- MCP server starts and `memory-wiki-find` responds to requests
