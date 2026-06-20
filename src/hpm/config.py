"""Configuration paths, provider selection, and defaults for hpm."""

import os
from pathlib import Path

HPM_DIR = Path.home() / ".hermes" / "memories"
DEFAULT_DB_PATH = HPM_DIR / "memories.db"
DAILY_LOG_DIR = HPM_DIR / "daily"

# ── LLM Provider ─────────────────────────────────────────────────────────
#
# Set HPM_LLM_PROVIDER to one of:
#   "opencode"   — OpenCode Go (OpenAI-compatible) [default]
#   "anthropic"  — Anthropic Messages API
#   "openai"     — OpenAI API / any OpenAI-compatible endpoint
#   "openrouter" — OpenRouter (OpenAI-compatible, multi-model)

LLM_PROVIDER = os.environ.get("HPM_LLM_PROVIDER", "opencode").lower()

# OpenAI-compatible providers share the same API shape.
# Provider-specific env vars:
#   opencode:  OPENCODE_GO_API_KEY,  OPENCODE_GO_BASE_URL
#   openai:    OPENAI_API_KEY,       OPENAI_BASE_URL
#   openrouter: OPENROUTER_API_KEY,  OPENROUTER_BASE_URL
#   anthropic: ANTHROPIC_API_KEY,    ANTHROPIC_BASE_URL

OPENGINE_API_KEY = os.environ.get("OPENCODE_GO_API_KEY", "")
OPENGINE_BASE_URL = os.environ.get(
    "OPENCODE_GO_BASE_URL", "https://opencode.ai/zen/go/v1"
)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_BASE_URL = os.environ.get(
    "ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1"
)

# Model overrides — each provider has a default model that can be overridden
# via the HPM_LLM_MODEL env var or the model parameter in individual calls.
SUMMARIZATION_MODEL = os.environ.get("HPM_LLM_MODEL", "")
ANSWER_MODEL = os.environ.get("HPM_ANSWER_MODEL", "")

# Fallback defaults per provider (used when HPM_LLM_MODEL is not set)
DEFAULT_MODELS = {
    "opencode": "minimax-m2.5",
    "openai": "gpt-4o-mini",
    "openrouter": "anthropic/claude-sonnet-4",
    "anthropic": "claude-sonnet-4-20250514",
}

# Embeddings
EMBEDDING_MODEL = os.environ.get("HPM_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
EMBEDDING_DIM = int(os.environ.get("HPM_EMBEDDING_DIM", "384"))

# Daily log path (directory or file)
DAILY_LOG = os.environ.get("HPM_DAILY_LOG", str(DAILY_LOG_DIR))

# Source identifier
SOURCE = "hermes"
