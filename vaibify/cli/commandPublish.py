"""CLI subcommand group: vaibify publish (stub)."""

import click


@click.group("publish")
def publish():
    """Publish reproducible archives and workflows."""
    pass


@publish.command("archive")
def publishArchive():
    """Create a reproducible archive of the current project."""
    click.echo("Not yet implemented.")


@publish.command("workflow")
def publishWorkflow():
    """Publish a workflow definition for the current project."""
    click.echo("Not yet implemented.")
