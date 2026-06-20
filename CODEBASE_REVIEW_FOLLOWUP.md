# hpm Codebase Review — Follow-Up

Second review (kimi-k2.7-code). Finds the first review missed.  
See **CODEBASE_REVIEW.md** for the original findings.

---

## 🔴 Critical (4)

### 1. Sidecar global cursor causes permanent cross-session message loss

**File:** `sidecar.py:68–78, 237–239`

The cursor stores `_global = max(m["id"] for m in messages)` — the single highest message ID across **all** sessions. The next poll filters with `WHERE m.id > ?` using that global max. If Session A races ahead (IDs 500–600) while Session B has IDs 100–200, Session B's messages are **permanently skipped** because 100–200 < 600. The per-session cursor infrastructure exists (`cursor[session_id]`) in `build_turns` but is never read.

**Fix:** Use `cursor.get(session_id, 0)` per session in `get_latest_messages`, not a single global max.

### 2. `reinforce()` is never called — decay is one-way only

**File:** `db.py:531–541`

`reinforce()` resets `decay_score = 1.0` and updates `last_accessed` when an entry is retrieved. But **no query path calls it** — neither `query_vector`, `query_keyword`, nor `query_hybrid`. README advertises "reinforced on every retrieval" but the code never does it. Scores only go down and never recover.

**Fix:** Call `reinforce(conn, row["id"])` for each result in all three query paths.

### 3. New entries never decay because `last_accessed` starts NULL

**File:** `db.py:553–554`

`compute_decay_score` returns `decay_score` unchanged when `last_accessed` is `None`. New entries inserted via `_insert_new` or `_merge_existing` never set `last_accessed` — it defaults to NULL. Combined with issue #2 (reinforce never called), **new entries effectively never decay**. They sit at `decay_score=1.0` permanently.

**Fix:** Set `last_accessed = _now()` on insert, or treat NULL as "just created" with no special exemption.

### 4. `conn.commit()` never wrapped in `with_retry` across all write paths

**File:** `db.py:250, 283, 541, 593`

`_merge_existing`, `_insert_new`, `reinforce`, and `run_decay` all call `conn.commit()` **outside** the `with_retry` wrapper. If SQLite returns `SQLITE_BUSY` on commit, the transaction silently rolls back and the caller receives no error.

**Fix:** Wrap `conn.commit()` in `with_retry()` on all four code paths.

---

## 🟡 Should Fix (8)

### 5. `with_retry` is architecturally redundant with SQLite's `busy_timeout`

**File:** `db.py:29–33, 149–169`

SQLite is configured with `PRAGMA busy_timeout=5000` — it retries internally for 5s. The `with_retry` wrapper adds 350ms of Python-level retry (50+100+200ms) on top. If SQLite couldn't acquire the lock in 5s, 3 more attempts at those intervals are almost certain to fail too. The wrapper provides a false sense of safety.

**Fix:** Either reduce `busy_timeout` to ~100ms and let `with_retry` handle retries, or remove `with_retry` and rely on `busy_timeout`.

### 6. FTS5 error on empty query — `MATCH ''` is invalid syntax

**File:** `db.py:348–368, 462–472`

`_to_fts5_query("")` returns `""`. `query_keyword` then executes `WHERE fts.content MATCH ''` which is invalid FTS5 syntax and raises `sqlite3.OperationalError`.

**Fix:** Add `if not fts_query: return []` at the top of `query_keyword`.

### 7. Double-quote characters in query break FTS5 syntax

**File:** `db.py:462–472`

A query like `test"` produces `"test""` — the embedded double-quote creates a dangling quote. Since the code wraps every term in quotes, any `"` inside the term breaks the quoted phrase.

**Fix:** Strip or escape `"` characters from input terms.

### 8. `rerank` module forces sentence-transformers load even with `--no-rerank`

**File:** `rerank.py:12`, `cli.py:161`

`from sentence_transformers import CrossEncoder` at module level executes at import time. When `hpm answer --no-rerank` is called, the module is still imported, triggering the full PyTorch load even though it's never used.

**Fix:** Make the `CrossEncoder` import lazy inside `_get_reranker()`.

### 9. MCP server `from hpm import embed` at module body between definitions

**File:** `hpm_mcp_server.py:134`

The import is placed at the top level of the module body **after** `TOOL_DEFINITIONS` and **between** function definitions. Fragile and against PEP 8.

**Fix:** Move to top of imports (line 36 area) or make it lazy inside handlers.

### 10. Sidecar `get_latest_messages` doesn't check session active status

**File:** `sidecar.py:76`

The query filters `AND m.active = 1` but doesn't check `s.active` for sessions. Orphaned messages from deleted sessions could still be returned.

**Fix:** Add `AND s.active = 1` to the WHERE clause.

### 11. `merge_or_insert` silently drops `session_id` during merge

**File:** `db.py:210–251`

`_merge_existing` gets no `session_id` or `access_scope` parameters. If session B references the same fact as session A, the merged entry only shows session A's ID.

**Fix:** Pass and update `session_id` and `access_scope` in `_merge_existing`.

### 12. `test_hybrid_search_vector_weight` test doesn't actually test weight

**File:** `test_db.py:197–200`

The `vector_weight=0.0` test checks `any("Python" in c for c in kw_contents)` — but "Python" appears in the first entry regardless of ordering. The test would pass even if `vector_weight` were completely ignored.

**Fix:** Check that the first result is the keyword-preferred entry.

---

## 🟢 Nice to Have (8)

### 13. `get_superseded_entries` is dead code — never called

### 14. No test for empty query in keyword search path

### 15. `serialize_vector` does redundant `bytes()` call — no-op overhead

### 16. Embedder dimension sampling embeds empty string on every init — use `model.output_dim` instead

### 17. Dashboard browser open uses unexpanded `~` path — browser gets literal `~` in file URL

### 18. `test_rerank.py` can't import without sentence-transformers installed — tests un-runnable in minimal install

### 19. `Embedder.__init__` has no friendly error if fastembed is not installed

### 20. MCP server has no graceful shutdown — connections leak on restart

---

## Combined Summary

| Source | Critical | Should Fix | Nice to Have | Total |
|--------|----------|------------|--------------|-------|
| First review (deepseek-v4-flash) | 5 | 4 | 4 | 13 |
| Second review (kimi-k2.7-code) | 4 | 8 | 7 | 19 |
| **Combined** | **9** | **12** | **11** | **32** |
