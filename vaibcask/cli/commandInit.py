"""CLI subcommand: vaibcask init."""

import pathlib
import shutil
import sys

import click

from .configLoader import fsConfigPath

_sTemplatesDir = "templates"


def flistAvailableTemplates():
    """Return a list of template directory names shipped with the package."""
    sPackageRoot = str(pathlib.Path(__file__).resolve().parents[2])
    sTemplatesPath = pathlib.Path(sPackageRoot) / _sTemplatesDir
    if not sTemplatesPath.is_dir():
        return []
    return sorted(
        d.name for d in sTemplatesPath.iterdir() if d.is_dir()
    )


def fsTemplatePath(sTemplateName):
    """Return the absolute path to a named template directory."""
    sPackageRoot = str(pathlib.Path(__file__).resolve().parents[2])
    return str(pathlib.Path(sPackageRoot) / _sTemplatesDir / sTemplateName)


def fnPrintAvailableTemplates():
    """Print available template names to stdout."""
    listTemplates = flistAvailableTemplates()
    if not listTemplates:
        click.echo("No templates found.")
        return
    click.echo("Available templates:")
    for sName in listTemplates:
        click.echo(f"  - {sName}")


def fbConfigExists():
    """Return True if vaibcask.yml exists in the current directory."""
    return pathlib.Path(fsConfigPath()).is_file()


def fnCopyTemplate(sTemplateName):
    """Copy template files into the current directory."""
    sSourcePath = fsTemplatePath(sTemplateName)
    if not pathlib.Path(sSourcePath).is_dir():
        click.echo(f"Error: Template '{sTemplateName}' not found.")
        sys.exit(1)
    fnCopyDirectoryContents(sSourcePath, str(pathlib.Path.cwd()))


def fnCopyDirectoryContents(sSourceDir, sDestDir):
    """Copy all files from sSourceDir into sDestDir."""
    sSource = pathlib.Path(sSourceDir)
    for sItem in sSource.iterdir():
        sDest = pathlib.Path(sDestDir) / sItem.name
        if sItem.is_dir():
            shutil.copytree(str(sItem), str(sDest), dirs_exist_ok=True)
        else:
            shutil.copy2(str(sItem), str(sDest))


def fnWriteDefaultConfig(sTemplateName):
    """Write a minimal vaibcask.yml using the ProjectConfig defaults."""
    from vaibcask.config.projectConfig import (
        ProjectConfig,
        fnSaveToFile,
    )
    sConfigPath = fsConfigPath()
    config = ProjectConfig(sProjectName=sTemplateName)
    fnSaveToFile(config, sConfigPath)
    click.echo(f"Created {sConfigPath}")


@click.command("init")
@click.option(
    "--template",
    "sTemplateName",
    default=None,
    help="Name of the project template to use.",
)
@click.option(
    "--force",
    "bForce",
    is_flag=True,
    default=False,
    help="Overwrite existing vaibcask.yml.",
)
def init(sTemplateName, bForce):
    """Initialize a new VaibCask project in the current directory."""
    if sTemplateName is None:
        fnPrintAvailableTemplates()
        return
    if fbConfigExists() and not bForce:
        click.echo(
            "Error: vaibcask.yml already exists. "
            "Use --force to overwrite."
        )
        sys.exit(1)
    fnCopyTemplate(sTemplateName)
    fnWriteDefaultConfig(sTemplateName)
    click.echo(f"Initialized VaibCask project with '{sTemplateName}'.")
