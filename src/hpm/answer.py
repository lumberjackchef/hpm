"""Cited-answer synthesis for Tier 3 recall.

Takes the top reranked memory results and sends them + the user's query to
the configured LLM provider for a structured answer with citations.

Follows the GBrain pattern: answer concisely, cite sources, or say "I don't know"
if nothing relevant is found.

Checks the wiki for contested pages and injects contradiction awareness
when relevant (Phase C).
"""

from __future__ import annotations

import logging
from typing import Any

from . import config, llm

logger = logging.getLogger(__name__)


def _check_wiki_contradictions(query: str) -> str:
    """Scan wiki for a contested page relevant to *query*.

    Returns a formatted contradiction note or empty string.
    """
    try:
        from .wiki import types as wiki_types

        wiki_dir = config.WIKI_DIR
        if not wiki_dir.exists():
            return ""

        for page_type, subdir_fn in wiki_types.SUBDIRS.items():
            subdir = subdir_fn()
            if not subdir.exists():
                continue
            for fpath in subdir.iterdir():
                if fpath.suffix != ".md":
                    continue
                text = fpath.read_text()
                meta, body = wiki_types.parse_frontmatter(text)
                if meta.get("contested") not in (True, "true"):
                    continue

                # Check if query matches the page title
                title = str(meta.get("title", "")).lower()
                if any(word.lower() in title for word in query.split()):
                    contradictions = meta.get("contradictions", [])
                    return (
                        "The wiki has a contested page about this topic "
                        f"(\"{meta.get('title', '')}\"). "
                        f"Conflicts: {', '.join(contradictions) if contradictions else 'unlisted'}."
                    )
        return ""
    except Exception:
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
        memory_context_lines.append(
            f"[{i}] id: {mem_id} | timestamp: {ts} | relevance: {score_str}\n    {content}"
        )

    memory_context = "\n\n".join(memory_context_lines)

    user_prompt = f"## User Query\n{query}\n\n## Memory Entries\n{memory_context}"

    # Phase C: Wiki contradiction check
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
