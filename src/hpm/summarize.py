"""OpenCode Go summarization client.

Calls the same endpoint Hermes uses for conversation summarization.
"""

from __future__ import annotations

import logging

import httpx

from . import config

logger = logging.getLogger(__name__)

SUMMARIZE_SYSTEM_PROMPT = (
    "You are a precise memory summarizer. Summarize the new information, decisions, "
    "facts, and preferences discussed in the following exchange. Be concise. "
    "Output 2–4 bullet points. Use plain text, no markdown formatting."
)


def summarize_turn(
    turn_text: str,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> str:
    """Send *turn_text* to the OpenCode Go API for summarization.

    Returns the condensed summary string (2–4 bullet points).

    Raises
    ------
    httpx.HTTPError
        If the API call fails.
    ValueError
        If the API key is not configured.
    """
    key = api_key or config.OPENGINE_API_KEY
    if not key:
        raise ValueError(
            "OPENCODE_GO_API_KEY is not set. "
            "Set it in your environment or pass it explicitly."
        )

    url = (base_url or config.OPENGINE_BASE_URL).rstrip("/") + "/chat/completions"
    resolved_model = model or config.SUMMARIZATION_MODEL

    body = {
        "model": resolved_model,
        "messages": [
            {"role": "system", "content": SUMMARIZE_SYSTEM_PROMPT},
            {"role": "user", "content": turn_text},
        ],
        "max_tokens": 256,
        "temperature": 0.3,
    }

    logger.info("summarizing turn with model=%s len=%d", resolved_model, len(turn_text))
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

    choice = data["choices"][0]
    summary: str = choice["message"]["content"].strip()
    logger.info("summary received (%d chars)", len(summary))
    return summary
