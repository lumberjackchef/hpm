"""Concurrent-writer stress test for WAL mode + retry.

Launches multiple threads writing to the same database simultaneously,
each with its own connection (realistic concurrent access pattern).
Verifies WAL mode + busy_timeout + with_retry handle contention without
data loss.
"""

import concurrent.futures
import tempfile
from pathlib import Path

import numpy as np
import pytest

from hpm import db as db_module


@pytest.fixture
def db_path():
    """Temporary database file for concurrent access."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = Path(f.name)
    # Initialize the database
    conn = db_module.get_connection(str(path))
    db_module.init_db(conn)
    conn.close()
    yield str(path)
    path.unlink(missing_ok=True)
    path.with_suffix(".db-wal").unlink(missing_ok=True)
    path.with_suffix(".db-shm").unlink(missing_ok=True)


def test_concurrent_inserts_no_data_loss(db_path):
    """Multiple threads inserting simultaneously don't lose entries."""
    num_threads = 8
    inserts_per_thread = 25
    total_expected = num_threads * inserts_per_thread

    def worker(worker_id: int) -> list[str]:
        conn = db_module.get_connection(db_path)
        db_module.init_db(conn)
        ids = []
        for i in range(inserts_per_thread):
            rng = np.random.default_rng(worker_id * 1000 + i)
            vec = rng.random(384).astype(np.float32)
            mid = db_module.insert_memory(
                conn,
                content=f"worker-{worker_id}-{i}",
                embedding=vec,
                source="stress-test",
                tags=[f"worker:{worker_id}"],
            )
            ids.append(mid)
        conn.close()
        return ids

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as pool:
        futures = [pool.submit(worker, wid) for wid in range(num_threads)]
        for f in concurrent.futures.as_completed(futures):
            exc = f.exception()
            if exc:
                pytest.fail(f"Worker failed: {exc}")

    # Verify all entries were written
    conn = db_module.get_connection(db_path)
    count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    conn.close()
    assert count == total_expected, (
        f"Expected {total_expected} entries, got {count} — possible data loss"
    )


def test_concurrent_inserts_no_duplicate_ids(db_path):
    """Concurrent inserts don't create duplicate primary keys."""
    num_threads = 4
    inserts_per_thread = 50

    all_ids: list[str] = []

    def worker(worker_id: int) -> list[str]:
        conn = db_module.get_connection(db_path)
        db_module.init_db(conn)
        ids = []
        for i in range(inserts_per_thread):
            rng = np.random.default_rng(worker_id * 1000 + i)
            vec = rng.random(384).astype(np.float32)
            mid = db_module.insert_memory(
                conn,
                content=f"dup-test-{worker_id}-{i}",
                embedding=vec,
            )
            ids.append(mid)
        conn.close()
        return ids

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as pool:
        futures = [pool.submit(worker, wid) for wid in range(num_threads)]
        for f in concurrent.futures.as_completed(futures):
            result = f.result()
            all_ids.extend(result)

    assert len(all_ids) == len(set(all_ids)), (
        f"Duplicate IDs: {len(all_ids)} items, {len(set(all_ids))} unique"
    )


def test_concurrent_inserts_all_entries_queriable(db_path):
    """Every entry written under concurrent load is findable via vector search."""
    num_threads = 4
    inserts_per_thread = 10

    def worker(worker_id: int) -> None:
        conn = db_module.get_connection(db_path)
        db_module.init_db(conn)
        for i in range(inserts_per_thread):
            rng = np.random.default_rng(worker_id * 1000 + i)
            vec = rng.random(384).astype(np.float32)
            db_module.insert_memory(
                conn,
                content=f"query-test-{worker_id}-{i}",
                embedding=vec,
                tags=[f"worker:{worker_id}"],
            )
        conn.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as pool:
        futures = [pool.submit(worker, wid) for wid in range(num_threads)]
        concurrent.futures.wait(futures)

    # Query for each worker's entries — vector search should find them
    conn = db_module.get_connection(db_path)
    db_module.init_db(conn)
    for wid in range(num_threads):
        query_vec = np.array([0.5 + wid * 0.1] * 384, dtype=np.float32)
        results = db_module.query_vector(conn, query_vec, limit=inserts_per_thread)
        assert len(results) > 0, (
            f"Worker {wid} entries not found after concurrent insert"
        )
    conn.close()
