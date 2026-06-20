# Team Mode — Shared Cloud Memory Store

A plan for extending hpm with an optional cloud-hosted memory store so teams can share a single memory database while keeping the default experience local-only.

## Design Principles

1. **Local-first, cloud-optional** — hpm works fully offline with no config changes. Team mode is an explicit, intentional opt-in.
2. **Same CLI commands** — `hpm save`, `hpm query`, `hpm answer`, `hpm capture` all work identically whether pointed at local sqlite-vec or a remote server.
3. **Transparent switching** — A single config toggle (`HPM_BACKEND`) switches between `local` (default) and `remote`. No code changes in tools or integrations.
4. **Row-level security** — Every entry is scoped to a team. Users only see memories their team owns.
5. **Self-hostable** — The server component is open-source and designed to run on a single VPS (Droplet). No vendor lock-in.

## Architecture

```
┌─────────────────────────────────────────────────┐
│                  hpm CLI / MCP                    │
│                                                   │
│  local mode (default):                            │
│    sqlite-vec → ~/.hpm/memories.db                │
│                                                   │
│  remote mode (opt-in, HPM_BACKEND=remote):        │
│    hpm save/capture → POST /api/v1/memories       │
│    hpm query         → POST /api/v1/search        │
│    hpm answer        → POST /api/v1/answer        │
│                                                   │
│    Config: HPM_REMOTE_URL + HPM_API_KEY           │
└───────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│               hpm-server (FastAPI)                │
│                                                   │
│  /api/v1/memories    CRUD for memory entries      │
│  /api/v1/search      Hybrid semantic + keyword    │
│  /api/v1/answer      Full recall pipeline (server │
│                      does rerank + LLM synthesis) │
│  /api/v1/status      Store stats                  │
│                                                   │
│  Auth: API key in header (X-API-Key)              │
│  RLS:  every query scoped to team_id from key     │
└───────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│             PostgreSQL + pgvector                │
│                                                   │
│  memories table (same schema, plus team_id)       │
│  teams table (name, api_key_hash, created_at)     │
└─────────────────────────────────────────────────┘
```

## Phases

### Phase 1 — Server App

**Deliverable:** A standalone `hpm-server` FastAPI service with the full memory API.

| Component | What |
|---|---|
| **Tech stack** | Python FastAPI, PostgreSQL + pgvector, API key auth |
| **Endpoints** | `POST /api/v1/memories` (save), `POST /api/v1/capture` (summarize+store), `POST /api/v1/search` (hybrid), `POST /api/v1/answer` (full pipeline), `GET /api/v1/status` |
| **Schema** | Same as the local store but with `team_id TEXT NOT NULL` on every table. The vector index is per-team (filtered by team_id). |
| **Auth** | API keys stored as bcrypt hashes in a `teams` table. Every request authenticated via `X-API-Key` header. The team_id is derived from the key, not passed by the client. |
| **Embedding** | Server-side embedding using fastembed (same model as local). The client sends raw text; the server handles embedding. This keeps the API simple and ensures vector consistency. |
| **LLM calls** | Summarization and answer synthesis run server-side using the server's own LLM provider config. The client never needs API keys for the LLM — just its hpm-server API key. |
| **Deployment** | Docker + docker-compose (FastAPI app + PostgreSQL + pgvector). Documented deploy to a DO Droplet. |
| **CLI integration** | New env vars: `HPM_BACKEND=remote`, `HPM_REMOTE_URL=https://hpm.example.com`, `HPM_API_KEY=...`. A new `Backend` abstraction in `config.py` that routes reads/writes to either sqlite-vec or the remote API. |

**Backend abstraction** — The key architectural change on the client side:

```python
# src/hpm/backend.py (new)
class Backend(ABC):
    @abstractmethod
    def save(self, content, embedding, ...) -> str: ...
    @abstractmethod
    def search(self, query, embedding, ...) -> list[dict]: ...
    @abstractmethod
    def status(self) -> dict: ...

class LocalBackend(Backend):
    """Existing sqlite-vec path — unchanged."""
    ...

class RemoteBackend(Backend):
    """HTTP client to hpm-server."""
    def __init__(self):
        self.url = config.HPM_REMOTE_URL
        self.api_key = config.HPM_API_KEY
    ...
```

The `db.py` module becomes a thin wrapper that delegates to the active backend. `cli.py` and the MCP server don't need to know which backend is active.

### Phase 2 — Deployment & Team Setup

**Deliverable:** One-click deploy + `hpm setup --team` walkthrough.

| Component | What |
|---|---|
| **Docker Compose** | `docker compose up -d` from `server/` directory — starts FastAPI + PostgreSQL with pgvector |
| **DO Droplet guide** | DigitalOcean deployment: `doctl compute droplet create`, Docker install, DNS, SSL via Caddy |
| **Team setup command** | `hpm setup --team` prompts for server URL, creates an API key via a registration endpoint, writes `HPM_BACKEND=remote`, `HPM_REMOTE_URL`, `HPM_API_KEY` to `~/.hpm/.env` |
| **Admin endpoints** | `POST /api/v1/admin/teams` — create team + generate API key (protected by an admin-only master key) |
| **MCP server update** | The MCP server inherits the client's `HPM_BACKEND` config. If set to `remote`, it proxies through to the server. Same tools, same interface. |

### Phase 3 — Team UX & Safety

**Deliverable:** Production-grade team features.

| Component | What |
|---|---|
| **Private memories** | Entries can have `scope: team` or `scope: private`. Private entries are only visible to the author (identified by a sub-key or user_id). |
| **Audit log** | All writes logged with user_id, timestamp, and entry content hash. |
| **Rate limiting** | Per-team request caps to prevent one team's usage from affecting another's latency. |
| **Caching** | In-memory cache for frequent queries (redis optional). The server caches top-5 recent answers per team. Client-side cache for status queries. |
| **Graceful fallback** | If the remote server is unreachable, the CLI falls back to a local cache DB and queues writes for retry. Users never lose data even during an outage. |

## What Doesn't Change

| Area | Stays the same |
|---|---|
| **Default behavior** | `hpm save`, `hpm query`, `hpm capture` all hit local sqlite-vec. No config change needed. |
| **CLI interface** | All existing commands keep their exact signatures. |
| **MCP tools** | `memory-find`, `memory-save`, `memory-capture` work identically in both modes. |
| **Data model** | Same fields, same schema (plus `team_id` on the server). |
| **Embedding model** | BGE-small via fastembed, on both client and server. |
| **LLM providers** | Server-side summarization and answer synthesis use the same `HPM_LLM_PROVIDER` abstraction. |

## Backward Compatibility

- Existing `~/.hpm/memories.db` files continue to work unmodified.
- Setting `HPM_BACKEND=remote` without `HPM_REMOTE_URL` produces a clear error.
- Unsetting `HPM_BACKEND` (or setting it to `local`) restores local-only behavior immediately — no migration needed.

## Risk & Mitigation

| Risk | Impact | Mitigation |
|---|---|---|
| Network latency on every memory operation | Team users see slower saves/queries | Client-side embedding + async write queue. The server only handles DB + LLM. |
| pgvector quality vs sqlite-vec | Different recall results between local and team mode | Both use the same BGE-small model. Verify similarity scores are comparable. |
| API key leaks | Unauthorized access to team memories | Keys stored as bcrypt hashes. Transport over TLS. Rotation endpoint. |
| Server downtime during active use | Lost captures, failed queries | Client-side write queue + local cache. Writes are retried when the server comes back. |
