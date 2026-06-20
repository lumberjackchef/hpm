"""Conversation turn summarization via the configured LLM provider."""

from __future__ import annotations

import logging

from . import llm

logger = logging.getLogger(__name__)

SUMMARIZE_SYSTEM_PROMPT = (
    "You are a precise memory summarizer. Summarize the new information, decisions, "
    "facts, and preferences discussed in the following exchange. Be concise. "
    "Output 2–4 bullet points. Use plain text, no markdown formatting."
)


def summarize_turn(turn_text: str, model: str | None = None) -> str:
    """Summarize a conversation turn using the configured LLM provider.

    Args:
        turn_text: The raw conversation text to summarize.
        model: Optional model override (defaults to provider default).

    Returns:
        Condensed summary (2–4 bullet points).
    """
    logger.info("summarizing turn (len=%d)", len(turn_text))
    return llm.complete(
        messages=[{"role": "user", "content": turn_text}],
        system=SUMMARIZE_SYSTEM_PROMPT,
        model=model,
        max_tokens=256,
        temperature=0.3,
    )
