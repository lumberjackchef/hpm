"""hpm CLI entry point."""

import click

from . import cli as cli_module


@click.group()
@click.version_option(version="0.1.0", prog_name="hpm")
def cli() -> None:
    """Hermes Pi Memory — shared local vector memory for AI agents."""


cli.add_command(cli_module.capture)
cli.add_command(cli_module.query)
cli.add_command(cli_module.save)
cli.add_command(cli_module.sidecar)


if __name__ == "__main__":
    cli()
