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

_See also: `planned/PI_EXTENSION.md` (Phase 3), this file's sibling in the planned/ directory._
