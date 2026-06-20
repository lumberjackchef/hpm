# Team Mode — Shared Cloud Memory Store

A plan for extending hpm with an optional cloud-hosted memory store so teams can share a single memory database while keeping the default experience local-only.

## Design Principles

1. **Local-first, cloud-optional** — hpm works fully offline with no config changes. Team mode is an explicit, intentional opt-in.
2. **Same CLI commands** — `hpm save`, `hpm query`, `hpm answer`, `hpm capture` all work identically whether pointed at local sqlite-vec or a remote Turso database.
3. **Transparent switching** — A single config toggle (`HPM_BACKEND`) switches between `local` (default) and `remote`. No code changes in tools or integrations.
4. **Row-level security** — Every entry is scoped to a team. Users only see memories their team owns.
5. **Managed infrastructure** — Turso handles hosting, replication, and auth. No server to deploy or maintain.

## Why Turso Over a Custom API Server

| Factor | FastAPI + PostgreSQL + pgvector | Turso / libSQL |
|--------|-------------------------------|----------------|
| **Infrastructure** | Need to deploy, maintain, and scale a server + DB | Managed — sign up, create a DB, get a URL |
| **Schema** | Different from local (pgvector syntax, team_id) | Almost identical to local (libSQL is a SQLite fork) |
| **Embedding** | Server-side (send raw text, get back vector) | Client-side (same fastembed, same BGE-small) |
| **Replication** | Manual with pglogical or Patroni | Built-in edge replicas, 30+ regions |
| **Auth** | Need to build API key management | Built-in — Turso tokens with per-DB scoping |
| **Vector search** | pgvector distance operators | Native `libsql_vector_idx` — built into the engine |
| **Cost** | Droplet (~$12–48/mo) + DB (~$15–30/mo) | Free tier (500MB, 30M row reads/mo) / Scale plan ($29/mo) |
| **LLM calls** | Go through server (extra hop) | Go direct from client (no intermediary) |

**Verdict:** Turso eliminates the need for a custom server. hpm's client just opens a libSQL connection instead of a local SQLite file — same code, same embedding, same data model adapted to libSQL's native vector syntax.

## Architecture

```
┌─────────────────────────────────────────────────┐
│                  hpm CLI / MCP                    │
│                                                   │
│  local mode (default, HPM_BACKEND=local):         │
│    sqlite-vec → ~/.hpm/memories.db                │
│                                                   │
│  team mode (opt-in, HPM_BACKEND=turso):           │
│    libsql-client → turso://team-name.turso.io     │
│     (same fastembed, same LLM provider config)    │
│                                                   │
│    Config: HPM_BACKEND=turso                       │
│            HPM_TURSO_URL=libsql://team...          │
│            HPM_TURSO_TOKEN=...                     │
└───────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│              Turso / libSQL Server                │
│                                                   │
│  Built-in: vector search (libsql_vector_idx),     │
│  edge replicas, TLS, token auth                   │
│                                                   │
│  Schema (adapted from local):                     │
│    memories table with libsql_vector_idx on        │
│    embedding column, team_id for RLS              │
└─────────────────────────────────────────────────┘
```

## Schema Differences

The data model stays the same; the vector index syntax changes:

| Component | Local (sqlite-vec) | Team (libSQL) |
|---|---|---|
| **Vector index** | `CREATE VIRTUAL TABLE memories_vec USING vec0(embedding float[384])` | `CREATE INDEX idx_embedding ON memories(libsql_vector_idx(embedding, 'metric=cosine'))` |
| **ANN search** | `SELECT ... FROM vec0 WHERE embedding MATCH ? AND k = ?` | `SELECT ... FROM vector_top_k('idx_embedding', 5, <vec>)` |
| **Storage** | 3NF: separate `memories` + `memories_vec` tables | Single `memories` table with inline embedding column |
| **RLS** | None (single-user) | `WHERE team_id = ?` on every query |
| **Tags** | JSON string `'["tag1","tag2"]'` | Same JSON string |
| **Source** | JSON string `'["hermes"]'` | Same JSON string |

The `embedding` column in libSQL is stored inline in the main table (as a `FLOAT32 BLOB` or similar), and the vector index is a secondary index on that column. This avoids the JOIN that sqlite-vec requires.

## Phases

### Phase 1 — Turso Backend Implementation

**Deliverable:** A `TursoBackend` class alongside the existing `sqlite-vec` local path, swappable via config.

| Component | What |
|---|---|
| **Dependency** | Add `libsql-client` (or `libsql-experimental`) Python package as optional dependency — `pip install hpm[turso]` |
| **Backend class** | New `TursoBackend` in `src/hpm/backend.py` that implements the same `Backend` protocol as the local SQLite path, but connects to a Turso DB via `libsql://` URL using token auth |
| **Vector search** | Uses libSQL's native `vector_top_k()` function instead of sqlite-vec's `vec0` virtual table. Same distance metric (cosine), same vector format (float32[384]). |
| **Schema** | Single `memories` table with inline `embedding` column + `libsql_vector_idx`. `team_id TEXT NOT NULL` on every row for RLS. |
| **Auth** | Turso token passed via `HPM_TURSO_TOKEN` env var. Token scoped to a specific database in Turso's console. |
| **Backend abstraction** | The `db.py` module delegates to whichever backend is active. `cli.py` and the MCP server don't know or care. |

**Backend abstraction sketch:**

```python
# src/hpm/backend.py
from abc import ABC, abstractmethod

class Backend(ABC):
    @abstractmethod
    def connect(self) -> None: ...
    @abstractmethod
    def insert(self, content, embedding, source, session_id, tags) -> str: ...
    @abstractmethod
    def search(self, query_text, query_vec, limit) -> list[dict]: ...
    @abstractmethod
    def status(self) -> dict: ...

class LocalBackend(Backend):
    """Existing sqlite-vec path — unmodified."""
    ...

class TursoBackend(Backend):
    """Remote libSQL connection to Turso."""
    def __init__(self):
        self.url = config.HPM_TURSO_URL
        self.token = config.HPM_TURSO_TOKEN
        self.conn = None
    def connect(self):
        import libsql_client
        self.conn = libsql_client.create_client_sync(self.url, auth_token=self.token)
    def search(self, query_text, query_vec, limit):
        vec_blob = struct.pack(f"{384}f", *query_vec)
        rows = self.conn.execute_sql(
            "SELECT id, content, source, timestamp, tags, "
            "vector_distance_cos(embedding, ?) AS distance "
            "FROM memories WHERE team_id = ? "
            "ORDER BY vector_top_k('idx_embedding', ?, ?)",
            (vec_blob, team_id, limit, vec_blob)
        )
        ...
```

### Phase 2 — Team Setup & Config

**Deliverable:** `hpm setup --team` walkthrough + documentation.

| Component | What |
|---|---|
| **CLI setup** | `hpm setup --team` prompts for Turso DB URL and token, writes `HPM_BACKEND=turso`, `HPM_TURSO_URL`, `HPM_TURSO_TOKEN` to `~/.hpm/.env` |
| **Auth** | Turso's own token system. Create a DB in Turso console, generate a token with read+write scope, share with team members. |
| **Schema init** | First connection auto-creates the `memories` table and vector index if they don't exist (same pattern as `init_db()` for local) |
| **Registration** | If the team has access to a `turso` CLI, they can create a new team DB: `turso db create hpm-team --enable-vector` |
| **MCP server** | Inherits `HPM_BACKEND` config. If set to `turso`, the MCP tools hit the Turso DB directly. Same tools, same interface. |

### Phase 3 — Team Safety & UX

| Component | What |
|---|---|
| **Graceful fallback** | If Turso is unreachable, the CLI caches the last query results and queues writes locally for retry. Users don't lose data during brief outages. |
| **Private scope** | Entries can have `scope: team` or `scope: private`. Private entries are filtered by a per-user identifier embedded in the Turso token or a sub-field. |
| **Shared models** | The team can configure a single set of LLM provider env vars on their server or agree on a shared `HPM_LLM_PROVIDER` in their docs. Each member still runs embedding locally. |
| **Pricing awareness** | Turso free tier: 500MB storage, 30M row reads/month. Scale plan: $29/mo for 8GB, 1B row reads. Vector index counts toward storage. |

## What Doesn't Change

| Area | Stays the same |
|---|---|
| **Default behavior** | `hpm save`, `hpm query`, `hpm capture` all hit local sqlite-vec. Zero config changes. |
| **CLI interface** | All existing commands keep their exact signatures. |
| **MCP tools** | `memory-find`, `memory-save`, `memory-capture` work identically in both modes. |
| **Embedding model** | BGE-small via fastembed, client-side, always the same. |
| **LLM config** | `HPM_LLM_PROVIDER` and API keys stay in `~/.hpm/.env`. Team mode doesn't change how LLM calls work. |
| **Data model fields** | Same `id`, `content`, `source`, `session_id`, `timestamp`, `tags`, `decay_score`, `superseded_by`, `access_scope` — same semantics. |

## Backward Compatibility

- Existing `~/.hpm/memories.db` files continue to work unmodified.
- Setting `HPM_BACKEND=turso` without `HPM_TURSO_URL` produces a clear error.
- Unsetting `HPM_BACKEND` or setting it to `local` restores local-only behavior immediately.
- Both backends can coexist — query `local` for personal memories, `turso` for team memories.

## Risks & Mitigation

| Risk | Impact | Mitigation |
|---|---|---|
| libSQL vector search quality vs sqlite-vec | Different recall results for team | Both use cosine distance on the same BGE-small vectors. Run a comparison benchmark before shipping. |
| Turso free tier limits | Team hits 500MB or 30M reads/month | Optimize query frequency with client-side caching. Upgrade to Scale plan ($29/mo) as needed. |
| Token management friction | Each team member needs a Turso token | Document one-time setup. The team admin creates the DB and shares the connection details. |
| Network latency on queries | Team members see slow recall | Embedding is local (~3ms). Only the vector search round-trips to Turso (~30-100ms). Acceptable for infrequent recall. |
| Team member leaves | Access to team memories persists | Turso supports token revocation. The team admin regenerates the DB token and shares the new one. |
| SQL syntax differences | Feature drift between local and team backends | The `Backend` abstraction encapsulates all differences. New features (decay, spot-check) hit both schemas. |
