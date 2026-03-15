"""CLI subcommand group: vaibcask config."""

import pathlib
import sys

import click
import yaml

from .configLoader import fconfigLoad, fsConfigPath


def fnWriteYaml(dictData, sPath):
    """Write a dictionary to a YAML file."""
    try:
        with open(sPath, "w") as fileHandle:
            yaml.dump(dictData, fileHandle, default_flow_style=False)
    except OSError as error:
        click.echo(f"Error writing {sPath}: {error}")
        sys.exit(1)


def fdictLoadYamlFile(sPath):
    """Load and return a YAML file as a dictionary."""
    if not pathlib.Path(sPath).is_file():
        click.echo(f"Error: File '{sPath}' not found.")
        sys.exit(1)
    try:
        with open(sPath, "r") as fileHandle:
            dictData = yaml.safe_load(fileHandle)
    except yaml.YAMLError as error:
        click.echo(f"Error parsing {sPath}: {error}")
        sys.exit(1)
    if dictData is None:
        return {}
    return dictData


@click.group("config")
def config():
    """View and manage VaibCask configuration."""
    pass


@config.command("export")
@click.argument("sFilePath", metavar="FILE")
def configExport(sFilePath):
    """Export the current configuration to a YAML file."""
    from vaibcask.config.projectConfig import fnSaveToFile
    config = fconfigLoad()
    fnSaveToFile(config, sFilePath)
    click.echo(f"Configuration exported to {sFilePath}")


@config.command("import")
@click.argument("sFilePath", metavar="FILE")
def configImport(sFilePath):
    """Import configuration from a YAML file and overwrite the current config."""
    dictNewConfig = fdictLoadYamlFile(sFilePath)
    sConfigPath = fsConfigPath()
    bExists = pathlib.Path(sConfigPath).is_file()
    if bExists and not click.confirm(
        "Overwrite existing vaibcask.yml?"
    ):
        click.echo("Aborted.")
        return
    fnWriteYaml(dictNewConfig, sConfigPath)
    click.echo(f"Configuration imported from {sFilePath}")


@config.command("edit")
def configEdit():
    """Open vaibcask.yml in the default editor."""
    sConfigPath = fsConfigPath()
    if not pathlib.Path(sConfigPath).is_file():
        click.echo("Error: vaibcask.yml not found.")
        sys.exit(1)
    click.edit(filename=sConfigPath)
