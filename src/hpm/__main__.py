"""hpm CLI entry point."""

import click

from . import cli as cli_module
from .wiki import compile as wiki_compile
from .wiki import find as wiki_find
from .wiki import init as wiki_init
from .wiki import lint as wiki_lint
from .wiki import sync as wiki_sync


@click.group()
@click.version_option(version="0.1.0", prog_name="hpm")
def cli() -> None:
    """Hermes Pi Memory — shared local vector memory for AI agents."""


@click.group(name="wiki")
def wiki() -> None:
    """Compiled knowledge wiki (Tier 0 recall)."""


wiki.add_command(wiki_init.init_cli)
wiki.add_command(wiki_compile.compile_cli)
wiki.add_command(wiki_find.find_cli)
wiki.add_command(wiki_sync.sync_cli)
wiki.add_command(wiki_lint.lint_cli)

cli.add_command(cli_module.capture)
cli.add_command(cli_module.query)
cli.add_command(cli_module.save)
cli.add_command(cli_module.sidecar)
cli.add_command(cli_module.answer)
cli.add_command(cli_module.decay)
cli.add_command(cli_module.status)
cli.add_command(cli_module.dashboard)
cli.add_command(cli_module.setup)
cli.add_command(wiki)


if __name__ == "__main__":
    cli()
