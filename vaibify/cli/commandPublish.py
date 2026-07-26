"""CLI subcommand group: vaibify publish (stub, not registered).

Deliberately absent from ``main``'s command list: both subcommands
only print "Not yet implemented.", and a published CLI that advertises
a command which does nothing is a promise the tool cannot keep. Wire
it back into ``vaibify/cli/main.py`` in the same commit that gives the
subcommands a real implementation.
"""

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
