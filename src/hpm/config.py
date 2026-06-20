"""Configuration paths and defaults for hpm."""

import os
from pathlib import Path

HPM_DIR = Path.home() / ".hermes" / "memories"
DEFAULT_DB_PATH = HPM_DIR / "memories.db"
DAILY_LOG_DIR = HPM_DIR / "daily"

# OpenCode Go summarization API
OPENGINE_BASE_URL = os.environ.get(
    "OPENCODE_GO_BASE_URL",
    "https://opencode.ai/zen/go/v1",
)
OPENGINE_API_KEY = os.environ.get("OPENCODE_GO_API_KEY", "")

# Default models
SUMMARIZATION_MODEL = os.environ.get("HPM_SUMMARIZATION_MODEL", "minimax-m2.5")
EMBEDDING_MODEL = os.environ.get("HPM_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
EMBEDDING_DIM = int(os.environ.get("HPM_EMBEDDING_DIM", "384"))

# Capture
DAILY_LOG = os.environ.get("HPM_DAILY_LOG", str(DAILY_LOG_DIR))

# Source identifier
SOURCE = "hermes"
