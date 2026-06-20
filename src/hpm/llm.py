"""Multi-provider LLM client for summarization, answer synthesis, and spot-check.

Supports OpenAI-compatible endpoints (OpenCode Go, OpenAI, OpenRouter)
and Anthropic's native Messages API.

Configured via the ``HPM_LLM_PROVIDER`` environment variable.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from . import config

logger = logging.getLogger(__name__)


# ── Provider resolution ──────────────────────────────────────────────────


def _provider_config() -> dict[str, str]:
    """Return ``(api_key, base_url, default_model)`` for the active provider."""
    provider = config.LLM_PROVIDER

    if provider == "opencode":
        return {
            "api_key": config.OPENGINE_API_KEY,
            "base_url": config.OPENGINE_BASE_URL,
            "default_model": config.DEFAULT_MODELS["opencode"],
            "api_type": "openai",
        }
    elif provider == "openai":
        return {
            "api_key": config.OPENAI_API_KEY,
            "base_url": config.OPENAI_BASE_URL,
            "default_model": config.DEFAULT_MODELS["openai"],
            "api_type": "openai",
        }
    elif provider == "openrouter":
        return {
            "api_key": config.OPENROUTER_API_KEY,
            "base_url": config.OPENROUTER_BASE_URL,
            "default_model": config.DEFAULT_MODELS["openrouter"],
            "api_type": "openai",
        }
    elif provider == "anthropic":
        return {
            "api_key": config.ANTHROPIC_API_KEY,
            "base_url": config.ANTHROPIC_BASE_URL,
            "default_model": config.DEFAULT_MODELS["anthropic"],
            "api_type": "anthropic",
        }
    else:
        raise ValueError(
            f"Unknown HPM_LLM_PROVIDER: {provider!r}. "
            f"Expected one of: opencode, openai, openrouter, anthropic"
        )


# ── Core completion function ─────────────────────────────────────────────


def complete(
    messages: list[dict[str, str]],
    system: str | None = None,
    model: str | None = None,
    max_tokens: int = 256,
    temperature: float = 0.3,
) -> str:
    """Send a chat completion request to the configured LLM provider.

    Args:
        messages: List of ``{"role": ..., "content": ...}`` dicts.
        system: Optional system prompt (used differently per provider).
        model: Model name override (defaults to provider default).
        max_tokens: Maximum tokens in the response.
        temperature: Sampling temperature.

    Returns:
        The response text (stripped).

    Raises:
        ValueError: If the API key is not set.
        httpx.HTTPError: If the API call fails.
    """
    cfg = _provider_config()
    api_key = cfg["api_key"]

    if not api_key:
        raise ValueError(
            f"No API key configured for provider {config.LLM_PROVIDER!r}. "
            f"Set the corresponding environment variable."
        )

    resolved_model = model or config.SUMMARIZATION_MODEL or cfg["default_model"]

    if cfg["api_type"] == "anthropic":
        return _call_anthropic(
            api_key, cfg["base_url"], resolved_model, messages,
            system, max_tokens, temperature,
        )
    else:
        return _call_openai(
            api_key, cfg["base_url"], resolved_model, messages,
            system, max_tokens, temperature,
        )


# ── OpenAI-compatible call ───────────────────────────────────────────────


def _call_openai(
    api_key: str,
    base_url: str,
    model: str,
    messages: list[dict[str, str]],
    system: str | None,
    max_tokens: int,
    temperature: float,
) -> str:
    """Call an OpenAI-compatible chat completions endpoint."""
    url = base_url.rstrip("/") + "/chat/completions"
    body: dict[str, Any] = {
        "model": model,
        "messages": list(messages),
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    # Insert system prompt as first message if provided
    if system:
        body["messages"].insert(0, {"role": "system", "content": system})

    logger.debug("openai-compatible call to %s model=%s", url, model)
    resp = httpx.post(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()
    content: str = data["choices"][0]["message"]["content"].strip()
    return content


# ── Anthropic call ───────────────────────────────────────────────────────


def _call_anthropic(
    api_key: str,
    base_url: str,
    model: str,
    messages: list[dict[str, str]],
    system: str | None,
    max_tokens: int,
    temperature: float,
) -> str:
    """Call the Anthropic Messages API."""
    url = base_url.rstrip("/") + "/messages"

    # Build messages — Anthropic requires alternating user/assistant, no system role
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [m for m in messages if m["role"] in ("user", "assistant")],
    }

    if system:
        body["system"] = system

    logger.debug("anthropic call to %s model=%s", url, model)
    resp = httpx.post(
        url,
        headers={
            "x-api-key": api_key,
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        },
        json=body,
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()

    # Collect all text content blocks
    parts = []
    for block in data.get("content", []):
        if block.get("type") == "text":
            parts.append(block["text"])
    return "\n".join(parts).strip()
