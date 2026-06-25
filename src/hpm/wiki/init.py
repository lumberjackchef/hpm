"""``hpm wiki init`` — create the wiki directory structure."""

from __future__ import annotations

import datetime
from pathlib import Path

import click

from .. import config
from . import types as wiki_types


def _ensure_wiki_dir() -> Path:
    """Create ``~/.hpm/wiki/`` and subdirectories if they don't exist."""
    root = config.WIKI_DIR
    root.mkdir(parents=True, exist_ok=True)

    for _, subdir_fn in wiki_types.SUBDIRS.items():
        subdir_fn().mkdir(parents=True, exist_ok=True)

    return root


def _write_if_missing(path: Path, content: str) -> bool:
    """Write *content* to *path* only if the file doesn't exist yet.

    Returns ``True`` if the file was created, ``False`` if it already existed.
    """
    if path.exists():
        return False
    path.write_text(content)
    return True


def cmd_init() -> None:
    """Initialize the wiki directory with SCHEMA.md, index.md, log.md."""
    root = _ensure_wiki_dir()
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    created_schema = _write_if_missing(wiki_types.schema_path(), wiki_types.generate_schema())
    created_index = _write_if_missing(
        wiki_types.index_path(),
        "# Wiki Index\n\n_Empty.  Run `hpm wiki compile` to create pages._\n",
    )
    created_log = _write_if_missing(
        wiki_types.log_path(),
        f"# Wiki Action Log\n\n## {now[:10]}\n\n- Initialized wiki at `{root}`\n",
    )

    click.echo(f"Wiki initialized at {root}")
    if created_schema:
        click.echo(f"  Created {wiki_types.schema_path().name}")
    else:
        click.echo(f"  Already exists: {wiki_types.schema_path().name}")
    if created_index:
        click.echo(f"  Created {wiki_types.index_path().name}")
    else:
        click.echo(f"  Already exists: {wiki_types.index_path().name}")
    if created_log:
        click.echo(f"  Created {wiki_types.log_path().name}")
    else:
        click.echo(f"  Already exists: {wiki_types.log_path().name}")

    click.echo(f"  Entities:     {wiki_types.entities_dir()}")
    click.echo(f"  Concepts:     {wiki_types.concepts_dir()}")
    click.echo(f"  Comparisons:  {wiki_types.comparisons_dir()}")
    click.echo(f"  Queries:      {wiki_types.queries_dir()}")

    # Append to log
    with wiki_types.log_path().open("a") as f:
        f.write(f"- {now[:19]} — ``hpm wiki init`` (all directories)\n")


@click.command(name="init")
def init_cli() -> None:
    """Initialize the wiki directory structure and schema."""
    cmd_init()
