"""``hpm wiki git`` — pass-through git commands for the wiki repo.

Allows agents and users to push, pull, branch, log, status, etc.
directly on the wiki's git repo without needing to ``cd ~/.hpm/wiki``.

Usage::

    hpm wiki git status
    hpm wiki git log --oneline -10
    hpm wiki git push origin main
    hpm wiki git pull --rebase
    hpm wiki git remote add origin <url>
    hpm wiki git diff --stat
"""

from __future__ import annotations

import click

from . import git as wiki_git


@click.command(name="git")
@click.argument("args", nargs=-1, required=True)
def git_cli(args: tuple[str, ...]) -> None:
    """Run git commands inside the wiki repo.

    Passes all arguments through to ``git`` inside ``~/.hpm/wiki/``.

    Examples:

        hpm wiki git status

        hpm wiki git log --oneline -5

        hpm wiki git remote add origin <url>

        hpm wiki git push origin main

        hpm wiki git pull --rebase
    """
    result = wiki_git.pass_through(list(args))
    click.echo(result)
