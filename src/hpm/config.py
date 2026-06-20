"""Configuration paths, provider selection, and defaults for hpm."""

import os
from pathlib import Path

# ── Env file loader ──────────────────────────────────────────────────────
# Load ~/.hpm/.env before reading any env vars so all os.environ.get() calls
# below pick up values from it. Silently no-ops if the file doesn't exist.

_HPM_ENV_DIR = Path.home() / ".hpm"
_HPM_ENV_FILE = _HPM_ENV_DIR / ".env"


def _load_dotenv(path: Path) -> None:
    """Load a simple KEY=VALUE env file into the process environment.

    Only sets variables not already set in the environment (no overwrite).
    Supports quoted values and comments (lines starting with #).
    """
    if not path.exists():
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip("\"'").strip()
            if key and key not in os.environ:
                os.environ[key] = val


_load_dotenv(_HPM_ENV_FILE)

# Also check ~/.hermes/.env for backward compatibility (legacy location)
_load_dotenv(Path.home() / ".hermes" / ".env")

# ── Auto-create ~/.hpm/ directory and .env template ─────────────────────
# On first install, create the config directory and a commented-out
# template so users know what env vars are available.

_HPM_ENV_STUB = """# hpm configuration
# Uncomment and set the variables you need.
# This file is loaded automatically by hpm on every command.
#
# LLM provider: opencode (default), anthropic, openai, openrouter
#HPM_LLM_PROVIDER=opencode
#
# Provider-specific API keys (set the one matching your provider):
#OPENCODE_GO_API_KEY=
#ANTHROPIC_API_KEY=
#OPENAI_API_KEY=
#OPENROUTER_API_KEY=
#
# Model override (defaults per provider):
#HPM_LLM_MODEL=
#
# Database path (default: ~/.hpm/memories.db):
#HPM_DB_PATH=
"""


def _ensure_hpm_dir() -> None:
    """Create ``~/.hpm/`` and a commented ``.env`` template if they don't exist."""
    if _HPM_ENV_DIR.exists():
        return
    _HPM_ENV_DIR.mkdir(parents=True, exist_ok=True)
    if not _HPM_ENV_FILE.exists():
        _HPM_ENV_FILE.write_text(_HPM_ENV_STUB.strip() + "\n")


_ensure_hpm_dir()

# ── Data directory ───────────────────────────────────────────────────────
# The canonical location is ~/.hpm/, but ~/.hermes/memories/ is checked as
# a fallback for users with existing data from before the migration.

HPM_DIR = _HPM_ENV_DIR  # ~/.hpm
_LEGACY_HPM_DIR = Path.home() / ".hermes" / "memories"

DEFAULT_DB_PATH = HPM_DIR / "memories.db"
DAILY_LOG_DIR = HPM_DIR / "daily"

# ── LLM Provider ─────────────────────────────────────────────────────────
# Set HPM_LLM_PROVIDER to one of:
#   "opencode"   — OpenCode Go (OpenAI-compatible) [default]
#   "anthropic"  — Anthropic Messages API
#   "openai"     — OpenAI API / any OpenAI-compatible endpoint
#   "openrouter" — OpenRouter (OpenAI-compatible, multi-model)

LLM_PROVIDER = os.environ.get("HPM_LLM_PROVIDER", "opencode").lower()

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
