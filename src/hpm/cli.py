"""CLI subcommands for hpm."""

import click


@click.command()
@click.argument("text")
@click.option("--tags", "-t", multiple=True, help="Tags to attach (e.g. project:jarvis)")
def capture(text: str, tags: tuple[str, ...]) -> None:
    """Capture a conversation turn: summarize, embed, and store."""
    click.echo(f"capture not yet implemented: {text[:60]}...")


@click.command()
@click.argument("query")
@click.option("--limit", "-l", default=10, show_default=True, help="Max results")
@click.option("--tags", "-t", multiple=True, help="Filter by tags")
def query(query: str, limit: int, tags: tuple[str, ...]) -> None:
    """Search memory with hybrid semantic + keyword retrieval."""
    click.echo(f"query not yet implemented: {query}")


@click.command()
@click.argument("fact")
@click.option("--tags", "-t", multiple=True, help="Tags to attach")
def save(fact: str, tags: tuple[str, ...]) -> None:
    """Save an explicit fact to memory (skips summarization)."""
    click.echo(f"save not yet implemented: {fact[:60]}...")
