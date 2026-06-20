"""Decay evaluator and spot-check loop for memory hygiene.

Computes exponential decay scores and runs an LLM-based spot-check on the
lowest-scoring entries to catch stale information that doesn't get corrected
through normal use.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

import httpx

from . import config

logger = logging.getLogger(__name__)

SPOT_CHECK_COUNT = 5
SPOT_CHECK_SYSTEM_PROMPT = (
    "You are a memory quality auditor. Rate each memory entry below as one of:\n"
    "STALE - Information is outdated or no longer accurate\n"
    "QUESTIONABLE - Might be inaccurate, worth reviewing\n"
    "VALID - Still accurate and useful\n"
    "INSUFFICIENT - Not enough context to judge\n\n"
    "Output one rating per line in this format:\n"
    "<index>: <RATING> - brief reason"
)


def run_spot_check(
    conn: "sqlite3.Connection",
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> list[dict[str, Any]]:
    """Sample the lowest-scoring entries and rate them via LLM.

    Adjusts scores based on ratings:
      STALE       → score -= 0.3
      QUESTIONABLE → score -= 0.1
      VALID       → score += 0.05
      (clamped to [0.0, 1.0])

    Returns the list of checked entries with their ratings.
    """
    rows = conn.execute(
        "SELECT id, content, timestamp, tags, decay_score FROM memories "
        "WHERE superseded_by IS NULL ORDER BY decay_score ASC LIMIT ?",
        (SPOT_CHECK_COUNT,),
    ).fetchall()

    if not rows:
        return []

    entries = [dict(r) for r in rows]
    prompt_lines = []
    for i, e in enumerate(entries, 1):
        content = e['content']
        ts = e['timestamp']
        tags = e.get('tags', '[]')
        prompt_lines.append(
            f"[{i}] content: {content}\n    ts: {ts}\n    tags: {tags}"
        )
    prompt = "\n\n".join(prompt_lines)

    key = api_key or config.OPENGINE_API_KEY
    if not key:
        logger.warning("OPENCODE_GO_API_KEY not set — skipping spot-check")
        return entries

    url = (base_url or config.OPENGINE_BASE_URL).rstrip("/") + "/chat/completions"
    resolved_model = model or config.SUMMARIZATION_MODEL

    body = {
        "model": resolved_model,
        "messages": [
            {"role": "system", "content": SPOT_CHECK_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 512,
        "temperature": 0.1,
    }

    try:
        resp = httpx.post(
            url,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=30.0,
        )
        resp.raise_for_status()
        result = resp.json()
        feedback = result["choices"][0]["message"]["content"]
    except Exception:
        logger.exception("spot-check LLM call failed")
        return entries

    # Parse ratings from LLM output
    adjustments: dict[int, float] = {}
    for line in feedback.strip().split("\n"):
        line = line.strip()
        if ":" not in line:
            continue
        idx_str, rest = line.split(":", 1)
        try:
            idx = int(idx_str.strip())
        except ValueError:
            continue
        rating = rest.split("-")[0].strip().upper() if "-" in rest else rest.strip().upper()

        if rating == "STALE":
            adjustments[idx] = -0.3
        elif rating == "QUESTIONABLE":
            adjustments[idx] = -0.1
        elif rating == "VALID":
            adjustments[idx] = +0.05
        else:
            continue  # INSUFFICIENT or unknown — no change

    # Apply adjustments
    for i, e in enumerate(entries, 1):
        adj = adjustments.get(i, 0)
        if adj != 0:
            new_score = max(0.0, min(1.0, e["decay_score"] + adj))
            conn.execute(
                "UPDATE memories SET decay_score = ? WHERE id = ?",
                (new_score, e["id"]),
            )
            entries[i - 1]["decay_score"] = new_score
            entries[i - 1]["_rating_adjustment"] = adj

    conn.commit()
    return entries
