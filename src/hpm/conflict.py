"""LLM-based conflict detector for Phase 4 cross-agent coherence.

Finds candidate memory pairs sharing tags but with different timestamps,
asks the LLM to judge whether they contradict, and marks the older entry
with a ``superseded_by`` pointer if they do.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Any

from . import llm

logger = logging.getLogger(__name__)

# ── Candidate finding ─────────────────────────────────────────────────────


def find_candidates(
    conn: Any,
    max_pairs: int = 10,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Find memory pairs that share tags and may conflict.

    Returns ``[(newer_entry, older_entry), ...]`` sorted so the newer
    entry comes first. Skips entries that already have a ``superseded_by``
    pointer set.  Limited to *max_pairs* candidates per call.
    """
    rows = conn.execute(
        "SELECT id, content, timestamp, tags, superseded_by FROM memories "
        "WHERE superseded_by IS NULL "
        "ORDER BY timestamp DESC"
    ).fetchall()

    if not rows:
        return []

    entries = [dict(r) for r in rows]

    # Build tag → entries index
    tag_entries: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in entries:
        for tag in _iter_tags(e.get("tags", [])):
            tag_entries[tag].append(e)

    # Form pairs from entries sharing tags
    seen: set[tuple[str, str]] = set()
    candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []

    for tag, group in tag_entries.items():
        if len(group) < 2:
            continue
        # Already sorted by timestamp DESC from SQL
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                newer, older = group[i], group[j]
                pair_id = (newer["id"], older["id"])
                if pair_id in seen:
                    continue
                seen.add(pair_id)
                candidates.append((newer, older))
                if len(candidates) >= max_pairs:
                    return candidates

    return candidates


def _iter_tags(tags_val: list[str] | str) -> list[str]:
    """Normalise and return tags from a JSON array or already-parsed list."""
    if isinstance(tags_val, list):
        return tags_val
    try:
        parsed = json.loads(tags_val)
        return parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


# ── LLM judgment ─────────────────────────────────────────────────────────


_JUDGE_SYSTEM_PROMPT = (
    "You are a precise memory auditor. You will be given two memory entries "
    "from an AI agent's persistent store — a NEWER one and an OLDER one.\n\n"
    "Decide whether they contradict each other:\n"
    "- CONTRADICTION: The entries make conflicting claims about the same "
    "topic and cannot both be true simultaneously. The newer entry should "
    "supersede the older one.\n"
    "- REFINEMENT: The newer entry updates or refines the older one without "
    "contradicting it (e.g. adding more detail, correcting a number while "
    "keeping the same direction).\n"
    "- UNRELATED: The entries discuss different topics or are not comparable.\n\n"
    "Reply with exactly ONE word: CONTRADICTION, REFINEMENT, or UNRELATED."
)


def judge_pair(
    newer: dict[str, Any],
    older: dict[str, Any],
    model: str | None = None,
) -> str:
    """Ask the LLM whether *newer* and *older* contradict.

    Returns ``CONTRADICTION``, ``REFINEMENT``, or ``UNRELATED``.
    """
    prompt = (
        f"Entry A (NEWER — {newer['timestamp']}):\n{newer['content']}\n\n"
        f"Entry B (OLDER — {older['timestamp']}):\n{older['content']}"
    )

    try:
        response = llm.complete(
            messages=[{"role": "user", "content": prompt}],
            system=_JUDGE_SYSTEM_PROMPT,
            model=model,
            max_tokens=32,
            temperature=0.1,
        )
    except (ValueError, Exception) as exc:
        logger.warning("LLM call failed for pair %s / %s: %s", newer["id"], older["id"], exc)
        return "UNRELATED"  # safe fallback — don't falsely flag

    # Extract the judgment keyword from the response
    for word in ("CONTRADICTION", "REFINEMENT", "UNRELATED"):
        if word in response.upper():
            return word

    logger.warning("Unexpected LLM response for pair: %r", response[:100])
    return "UNRELATED"


# ── Full detection pass ──────────────────────────────────────────────────


def run_conflict_detection(
    conn: Any,
    max_pairs: int = 10,
    model: str | None = None,
) -> dict[str, int]:
    """Run a full conflict detection pass.

    Finds candidate pairs, judges them via LLM, and marks contradictions
    with ``superseded_by``.

    Returns a summary dict with ``checked``, ``contradictions``, and
    ``errors`` counts.
    """
    candidates = find_candidates(conn, max_pairs=max_pairs)

    if not candidates:
        logger.info("no candidate pairs found for conflict detection")
        return {"checked": 0, "contradictions": 0, "errors": 0}

    contradictions = 0
    errors = 0
    checked = 0

    for newer, older in candidates:
        verdict = judge_pair(newer, older, model=model)
        checked += 1
        if verdict == "CONTRADICTION":
            conn.execute(
                "UPDATE memories SET superseded_by = ? WHERE id = ?",
                (newer["id"], older["id"]),
            )
            contradictions += 1
            logger.info(
                "contradiction: %s -> superseded_by %s",
                older["id"][:8], newer["id"][:8],
            )
        elif verdict == "UNRELATED":
            errors += 1  # mark as an error since tag overlap should imply relation
            logger.debug("unrelated pair: %s / %s", newer["id"][:8], older["id"][:8])

    conn.commit()

    logger.info(
        "conflict detection: %d checked, %d contradictions, %d errors",
        checked, contradictions, errors,
    )
    return {
        "checked": checked,
        "contradictions": contradictions,
        "errors": errors,
    }
