# LLM-Based Conflict Detector

> **Status: Implemented** — Phase 4 is complete. See `src/hpm/conflict.py` and
> `tests/test_conflict.py` for the current implementation.

A module to detect contradictory memory entries using LLM judgment and mark
the older one as outdated.

## Motivation

The current dedup system merges near-duplicates (cosine > 0.85) and inserts everything else as new. But it can't distinguish a refinement ("we chose Paddle") from a contradiction ("we chose Stripe"). Both just get separate rows, and recall returns both without flagging the conflict.

An LLM-based pass can identify true contradictions and mark the stale entry so recalled answers include conflict notes.

## How It Would Work

```
Periodic cron pass (alongside the spot-check):
  │
  ├─ 1. Find candidate pairs:
  │      Entries with high tag/keyword overlap but low vector similarity
  │      that were created at different times (newer may supersede older)
  │
  ├─ 2. Ask the LLM:
  │      "Do these two entries contradict each other?
  │       Entry A: ...
  │       Entry B: ...
  │       Answer: CONTRADICTION | REFINEMENT | UNRELATED"
  │
  ├─ 3. On CONTRADICTION:
  │      Set superseded_by on the older entry pointing to the newer one
  │      Add a conflict note visible in cited-answer synthesis
  │
  └─ 4. On REFINEMENT:
      Merge or leave as-is (the newer entry is more accurate)
```

## Schema

The `superseded_by TEXT` column exists in the schema (added by ``migrate_v2``
in ``db.py``). It's managed by the conflict detector module and checked by
``answer.py`` during cited-answer synthesis.

## Cited-Answer Integration

The existing `answer.py` synthesis would need updating to check for superseded entries and add conflict notes:

```python
# In synthesize_answer, when building the memory context:
if entry.get("superseded_by"):
    notes.append(f"Note: this entry was superseded on {entry['timestamp']}")
```

## Risks

| Risk | Mitigation |
|---|---|
| LLM hallucinates contradictions | Use low temperature (0.1), require high-confidence flag |
| False positives mark correct entries | Mark entries as `superseded_confirmed: bool` so the field can be reviewed and reverted |
| Latency of LLM calls on each pass | Batch 10+ pairs per call, run as part of daily cron (not on every read) |
| Catching every contradiction | The pass only finds *candidate* pairs via tag/keyword overlap — contradictions on different topics are unlikely to be found. Acceptable: the most impactful contradictions are on the same topic. |

## Migration Path

Since `superseded_by` was recently removed from the schema, re-adding it is straightforward:

```python
# In migrate_v1() or a new migrate_v2():
conn.execute("ALTER TABLE memories ADD COLUMN superseded_by TEXT")
```

The column is nullable, so existing entries are unchanged.
