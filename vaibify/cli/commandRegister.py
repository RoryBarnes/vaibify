"""CLI subcommand: vaibify register."""

import pathlib
import sys

import click

from .configLoader import fsConfigPath

from vaibify.config.registryManager import fnAddProject


@click.command("register")
@click.argument(
    "sDirectory",
    default=".",
    type=click.Path(exists=True, file_okay=False),
)
def register(sDirectory):
    """Register an existing project directory.

    Adds the project to the global registry so it can be
    targeted with --project/-p from any directory. The
    directory must contain a vaibify.yml file. Unlike
    ``vaibify init``, this does not create or overwrite any
    configuration files.
    """
    sAbsDirectory = str(pathlib.Path(sDirectory).resolve())
    sConfigPath = str(pathlib.Path(sAbsDirectory) / "vaibify.yml")
    if not pathlib.Path(sConfigPath).is_file():
        click.echo(
            f"Error: No vaibify.yml found in {sAbsDirectory}"
        )
        sys.exit(1)
    try:
        fnAddProject(sAbsDirectory)
    except ValueError as error:
        click.echo(f"Already registered: {error}")
        return
    click.echo(f"Registered project at {sAbsDirectory}")
