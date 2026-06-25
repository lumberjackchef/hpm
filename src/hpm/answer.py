"""Cited-answer synthesis for Tier 3 recall.

Takes the top reranked memory results and sends them + the user's query to
the configured LLM provider for a structured answer with citations.

Follows the GBrain pattern: answer concisely, cite sources, or say "I don't know"
if nothing relevant is found.

Checks the wiki's cached contested-page index for contradiction awareness
(Phase C).  Uses the pre-computed ``contested.json`` cache so every answer
call is O(1) rather than scanning the entire wiki file tree.
"""

from __future__ import annotations

import logging
from typing import Any

from . import config, llm

logger = logging.getLogger(__name__)


def _check_wiki_contradictions(query: str) -> str:
    """Scan a cached contested-page index for a title matching *query*.

    Returns a formatted contradiction note or empty string.
    Uses the pre-built ``~/.hpm/wiki/contested.json`` cache so this is O(1)
    per answer call rather than scanning the entire wiki file tree.
    """
    try:
        from .wiki import types as wiki_types

        wiki_dir = config.WIKI_DIR
        if not wiki_dir.exists():
            return ""

        contested = wiki_types.read_contested_index()
        if not contested:
            return ""

        query_words = [w.lower() for w in query.split() if len(w) > 1]
        if not query_words:
            return ""

        for slug, info in contested.items():
            title = str(info.get("title", slug)).lower()
            # Match if any meaningful query word appears in the title
            if any(word in title for word in query_words):
                contradictions = info.get("contradictions", [])
                return (
                    "The wiki has a contested page about this topic "
                    f"(\"{info.get('title', slug)}\"). "
                    f"Conflicts: {', '.join(contradictions) if contradictions else 'unlisted'}."
                )
        return ""
    except Exception:
        logger.exception("wiki contradiction check failed")
        return ""


ANSWER_SYSTEM_PROMPT = (
    "You are a precise memory recall assistant. You have been given a user query "
    "and a set of relevant memory entries retrieved from a vector store.\n\n"
    "Your job is to answer the user's question based ONLY on the provided memory "
    "entries. Follow these rules:\n\n"
    "1. Answer concisely and directly.\n"
    "2. Cite the source of each claim by including the memory entry's `id` and "
    "`timestamp` in brackets, like this: [id:abc123, 2026-06-20T10:30:00Z]\n"
    "3. If multiple entries support the same claim, cite all of them.\n"
    "4. If an entry has a \"⚠ Note: This entry has been superseded...\" "
    "message, mention the superseded fact but note that a newer entry "
    "contradicts or replaces it. Give more weight to entries that are "
    "not superseded.\n"
    "5. If the provided memories do NOT contain enough information to answer the "
    "question, say \"I don't know based on available memories.\" Do NOT make up "
    "information.\n"
    "6. If the user's question is ambiguous, acknowledge the ambiguity and "
    "present the relevant information you do have.\n"
    "7. Output in plain text. Use bullet points for multiple facts.\n"
    "8. End with a confidence statement: \"Confidence: High / Medium / Low\" "
    "based on how well the memories support the answer."
)


def synthesize_answer(
    query: str,
    results: list[dict[str, Any]],
    model: str | None = None,
) -> str:
    """Synthesize a cited answer from reranked memory results.

    Args:
        query: The original user query.
        results: Reranked memory entries.
        model: Optional model override.

    Returns:
        A structured answer string with citations, or an "I don't know" response.
    """
    if not results:
        return "I don't know based on available memories."

    # Format the memory context for the LLM
    memory_context_lines = []
    for i, r in enumerate(results, 1):
        mem_id = r.get("id", "?")
        ts = r.get("timestamp", "?")
        content = r.get("content", "")
        score = r.get("rerank_score", r.get("_combined", "?"))
        score_str = f"{score:.4f}" if isinstance(score, float) else str(score)
        line = (
            f"[{i}] id: {mem_id} | timestamp: {ts} | relevance: {score_str}\n    {content}"
        )
        # Phase 4: flag superseded entries
        superseded_by = r.get("superseded_by")
        if superseded_by:
            line += f"\n    ⚠ Note: This entry has been superseded by {superseded_by[:8]}."
        memory_context_lines.append(line)

    memory_context = "\n\n".join(memory_context_lines)

    user_prompt = f"## User Query\n{query}\n\n## Memory Entries\n{memory_context}"

    # Phase C: Wiki contradiction check (uses pre-computed contested.json)
    wiki_note = _check_wiki_contradictions(query)
    if wiki_note:
        user_prompt += f"\n\n## Wiki Contradiction Alert\n{wiki_note}"

    logger.info("synthesizing answer for query=%r with %d entries", query[:50], len(results))
    return llm.complete(
        messages=[{"role": "user", "content": user_prompt}],
        system=ANSWER_SYSTEM_PROMPT,
        model=model,
        max_tokens=512,
        temperature=0.2,
    )
