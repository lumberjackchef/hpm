"""Git integration for the compiled wiki.

Every wiki mutation (init, compile, lint --fix) is auto-committed so
changes are tracked and pushable to a remote.  The design is:

  - ``~/.hpm/wiki/`` is ``git init``'d on ``hpm wiki init``.
  - Every write through ``atomic_write``, ``rebuild_index``, and
    ``_append_log`` is followed by an auto-commit.
  - ``hpm wiki git <args>`` passes through to ``git`` inside the wiki dir.

This module provides the three helpers used by all wiki commands.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from .. import config

logger = logging.getLogger(__name__)

GITIGNORE_CONTENT = """# Regenerated on every compile / sync / lint --fix
contested.json

# Temp files from atomic_write
*.tmp
"""


# ── Helpers ─────────────────────────────────────────────────────────────


def is_git_repo(path: Path | None = None) -> bool:
    """Return ``True`` if *path* (or ``WIKI_DIR``) is a git working tree."""
    root = path or config.WIKI_DIR
    return (root / ".git").exists()


def _git(*args: str, workdir: Path | None = None) -> str:
    """Run ``git`` inside the wiki dir and return stdout."""
    cwd = workdir or config.WIKI_DIR
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        logger.debug("git not found on PATH")
        return ""
    except subprocess.TimeoutExpired:
        logger.warning("git command timed out: git %s", " ".join(args))
        return ""

    if result.returncode != 0:
        # Don't log non-zero for status checks — caller decides what to report
        logger.debug(
            "git %s → exit %d: %s",
            " ".join(args),
            result.returncode,
            result.stderr.strip(),
        )
    return result.stdout.strip()


def ensure_repo() -> bool:
    """Initialise the wiki as a git repo if it isn't already.

    Creates ``.gitignore`` and makes an initial commit with the seed files.

    Returns ``True`` if the repo was newly created, ``False`` if it already
    existed (or git is not available).
    """
    if is_git_repo():
        return False

    gitignore = config.WIKI_DIR / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(GITIGNORE_CONTENT)

    _git("init")
    # Use the user's configured git identity if available; fall back to a
    # sensible default so auto-commits don't fail.
    _git("config", "user.name", "hpm wiki")
    _git("config", "user.email", "hpm@localhost")

    _git("add", "-A")

    # Check if there's anything to commit (avoid "nothing to commit" error)
    status = _git("status", "--porcelain")
    if status:
        _git("commit", "-m", "chore: initialize wiki")
        logger.info("Wiki git repo initialised at %s", config.WIKI_DIR)

    return True


def auto_commit(message: str) -> str | None:
    """Stage all wiki changes and commit with *message*.

    This is a no-op if:
    - The wiki dir is not a git repo (the user ran `hpm wiki init` without git).
    - Git is not installed.
    - There are no changes to commit.

    Returns the abbreviated commit hash, or ``None`` if nothing was committed.
    """
    if not is_git_repo():
        return None

    _git("add", "-A")

    status = _git("status", "--porcelain")
    if not status:
        return None

    _git("commit", "-m", message)
    head = _git("rev-parse", "--short", "HEAD")
    logger.debug("Auto-commit %s: %s", head, message)
    return head or None


def pass_through(args: list[str]) -> str:
    """Run ``git <args>`` in the wiki dir and return output.

    Used by the ``hpm wiki git <args>`` subcommand.  Errors are propagated
    via the return value (prefixed with ``error: `` if needed) so the CLI
    can echo them.
    """
    if not is_git_repo():
        return "error: wiki is not a git repo — run `hpm wiki init` first"

    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(config.WIKI_DIR),
            capture_output=True,
            text=True,
            timeout=60,
        )
    except FileNotFoundError:
        return "error: git not found on PATH"
    except subprocess.TimeoutExpired:
        return "error: git command timed out"

    out = result.stdout.strip()
    err = result.stderr.strip()
    if result.returncode != 0:
        return f"error: {err or out}"

    return out or "(ok)"
