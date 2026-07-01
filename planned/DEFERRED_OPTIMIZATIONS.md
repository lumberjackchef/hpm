# Deferred Optimizations

A runway for performance, UX, and polish improvements that aren't urgent
enough to block a release but should be tracked for periodic grooming.

Each entry includes the trigger condition — the threshold at which it
becomes worth implementing.

---

## Current Backlog

| # | Area | Item | Trigger |
|---|------|------|---------|
| D1 | DB | **Missing indexes** — no index on `superseded_by` (WHERE filter) or `timestamp` (ORDER BY). Every conflict detection run forces a full table scan + sort. | When `memories` exceeds 10K rows or `hpm conflict --run` latency exceeds 1s |
| D2 | Conflict | **Explicit transaction** — the `find_candidates` read and subsequent `UPDATE` writes aren't wrapped in a single transaction. With `with_retry` guarding each write, data isn't at risk, but a concurrent writer between the read and the first UPDATE wastes LLM calls on stale candidates. | When concurrent write load (sidecar + Pi + manual) is consistently high |
| D3 | CLI | **No `--model` option for `hpm conflict`** — `run_conflict_detection` accepts a `model` parameter but the CLI doesn't expose it, forcing the conflict judge to use the same model configured for answer synthesis (likely more expensive than needed for a simple three-class judgment). | When there's a clear cost difference between the answer model and a cheaper judge model |
| D4 | Conflict | **O(n²) pair generation** — the inner loop in `find_candidates` generates all tag-sharing pairs until `max_pairs` is reached. For a tag with 100 entries this is ~5K iterations. The early-return caps output, but the loop doesn't short-circuit per tag group. | When any tag group exceeds 1K entries |
| D5 | DB | **FTS5 trigger re-indexes on every `superseded_by` UPDATE** — the `memories_au` trigger fires delete+reinsert on every `UPDATE memories`, even when only `superseded_by` changes. Content is unchanged so the index is identical, but `2N` unnecessary FTS writes occur per run. | When conflict detection runs on a store with 50K+ entries |

## Future Additions

As new features land or the store grows, add entries here instead of
blocking delivery. Groom periodically (e.g. quarterly or before a tagged
release) to decide which items to implement vs. discard vs. fold into a
larger refactor.

### Grooming checklist

- [ ] Is this still relevant? (usage patterns may have changed)
- [ ] Is the trigger condition met? (store size, latency, cost)
- [ ] Is the fix still the right approach? (architecture may have evolved)
- [ ] Schedule or discard.

---

## Phase 6 (Proposed): Agent Usage Validation & Observability

hpm captures and retrieves memory, but we have zero visibility into *whether
the agent actually uses it well*. The proposals below close that gap.

Each item includes an evaluation note — assess before implementing.

|| # | Area | Item | Trigger | Evaluation |
|---|------|------|---------|------------|
|| V1 | MCP Telemetry | **Agent usage log** — instrument the MCP server's `main()` dispatch to log every tool call (query, result count, latency, wiki fallback) to a new `usage_log` table or rotating ring buffer. Expose via `hpm usage` CLI and a dashboard panel. | When you can't diagnose an agent-behavior complaint (re-asked questions, stale answers) without per-call data | *Evaluate.* A week of manual `tail -f` on the sidecar's stderr logs may tell you enough. Build this only when the lack of visibility is concretely causing problems. |
|| V2 | Dashboard | **Health section** — add to `hpm dashboard`: capture gap (minutes since last entry vs expected interval), query-to-result ratio, wiki hit rate, decay trend, age histogram. | When you find yourself repeatedly running `hpm status` + mental math to assess whether the system is working | *Evaluate.* The current dashboard already shows score distribution and recent entries. Start with a one-liner in `hpm status` instead of a full panel. |
|| V3 | Cron | **Health heartbeat** — cron job that checks sidecar liveness (captured in last 10 min?) and flags memories below eviction threshold that seem to be good knowledge. Alerts via configured channel. | When the sidecar is deployed as an unattended background service you don't manually supervise | *Evaluate.* If you check in daily, a startup health check (`hpm status` after launch) is sufficient. A liveness miss during unmonitored hours could mean days of lost captures. |
|| V4 | Sidecar | **Capture instrumentation** — log per-capture latency (poll → embed → store) and success/failure rate in the sidecar's existing poll cycle. Track leading-edge gap (how far behind real-time the cursor is). | When you're tuning poll intervals or suspect the sidecar is falling behind during heavy use | *Evaluate.* The simplest form: one line per cycle showing count captured + duration. Currently the sidecar logs exceptions but not per-capture timing. |
|| V5 | Tests | **Integration tests with real DB** — end-to-end tests that run `hpm sidecar --once` against a synthetic state.db, then verify captured entries are queryable via `memory-find`. | When you change the capture pipeline (swapping embedders, changing summarization) more than once a quarter | *Evaluate.* Existing unit tests cover components well. An integration test catches schema drift or pipeline breaks — worth building only if the pipeline is in active flux. |
|| V6 | Wiki | **Wiki hit/miss tracking** — log whether `memory-wiki-find` resolved from wiki content vs fell through to the vector pipeline. Expose as a ratio. | During initial wiki build-out to gauge whether compilation effort is paying off | *Evaluate.* Once the wiki is stable and covers core topics, the ratio converges and marginal value drops. Build during Phase 6 setup, discard after ~100 wiki queries. |

### Evaluation process for V1–V6

Before implementing any V-item, answer:

1. **Is the problem real?** Can you cite a specific instance where not having this caused a concrete issue (wasted time, bad answer, lost memory)?
2. **What's the simplest version?** Could a grep/oneliner/shell alias do it instead of code?
3. **What's the maintenance cost?** New tables, endpoints, and dashboard panels need upkeep. Will you remember to maintain this in 6 months?
4. **Ship or skip.** If the problem isn't biting you, defer to next grooming cycle.

---

_See also: `planned/PI_EXTENSION.md` (Phase 3), `AGENTS.md` (Phase 6 not yet listed), this file's sibling in the planned/ directory._
