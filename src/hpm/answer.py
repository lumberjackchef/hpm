"""Cited-answer synthesis for Tier 3 recall.

Takes the top reranked memory results and sends them + the user's query to
the OpenCode Go API for a structured answer with citations.

Follows the GBrain pattern: answer concisely, cite sources, or say "I don't know"
if nothing relevant is found.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from . import config

logger = logging.getLogger(__name__)

ANSWER_SYSTEM_PROMPT = (
    "You are a precise memory recall assistant. You have been given a user query "
    "and a set of relevant memory entries retrieved from a vector store.\n\n"
    "Your job is to answer the user's question based ONLY on the provided memory "
    "entries. Follow these rules:\n\n"
    "1. Answer concisely and directly.\n"
    "2. Cite the source of each claim by including the memory entry's `id` and "
    "`timestamp` in brackets, like this: [id:abc123, 2026-06-20T10:30:00Z]\n"
    "3. If multiple entries support the same claim, cite all of them.\n"
    "4. If the provided memories do NOT contain enough information to answer the "
    "question, say \"I don't know based on available memories.\" Do NOT make up "
    "information.\n"
    "5. If the user's question is ambiguous, acknowledge the ambiguity and "
    "present the relevant information you do have.\n"
    "6. Output in plain text. Use bullet points for multiple facts.\n"
    "7. End with a confidence statement: \"Confidence: High / Medium / Low\" "
    "based on how well the memories support the answer."
)


def synthesize_answer(
    query: str,
    results: list[dict[str, Any]],
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> str:
    """Synthesize a cited answer from reranked memory results.

    Args:
        query: The original user query.
        results: Reranked memory entries (must have ``id``, ``timestamp``, ``content``).
        model: OpenCode Go model override.
        api_key: API key override (defaults to config).
        base_url: Base URL override (defaults to config).

    Returns:
        A structured answer string with citations, or an "I don't know" response.
    """
    if not results:
        return "I don't know based on available memories."

    key = api_key or config.OPENGINE_API_KEY
    if not key:
        raise ValueError(
            "OPENCODE_GO_API_KEY is not set. "
            "Set it in your environment or pass it explicitly."
        )

    url = (base_url or config.OPENGINE_BASE_URL).rstrip("/") + "/chat/completions"
    resolved_model = model or config.SUMMARIZATION_MODEL

    # Format the memory context for the LLM
    memory_context_lines = []
    for i, r in enumerate(results, 1):
        mem_id = r.get("id", "?")
        ts = r.get("timestamp", "?")
        content = r.get("content", "")
        score = r.get("rerank_score", r.get("_combined", "?"))
        score_str = f"{score:.4f}" if isinstance(score, float) else str(score)
        memory_context_lines.append(
            f"[{i}] id: {mem_id} | timestamp: {ts} | relevance: {score_str}\n    {content}"
        )

    memory_context = "\n\n".join(memory_context_lines)

    body = {
        "model": resolved_model,
        "messages": [
            {"role": "system", "content": ANSWER_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"## User Query\n{query}\n\n## Memory Entries\n{memory_context}"
                ),
            },
        ],
        "max_tokens": 512,
        "temperature": 0.2,
    }

    logger.info(
        "synthesizing answer for query=%r with %d memory entries",
        query[:50],
        len(results),
    )
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
    data = resp.json()

    answer: str = data["choices"][0]["message"]["content"].strip()
    logger.info("answer received (%d chars)", len(answer))
    return answer
