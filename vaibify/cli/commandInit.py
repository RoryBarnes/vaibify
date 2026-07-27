"""CLI subcommand: vaibify init."""

import pathlib
import shutil
import sys

import click

from .configLoader import fsConfigPath

from vaibify.config.registryManager import fnAddProject

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
    """Return True if vaibify.yml exists in the current directory."""
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


def fnWriteDefaultConfig(sProjectName, bMinimal=False):
    """Write a vaibify.yml for sProjectName using the ProjectConfig defaults.

    ``bMinimal`` strips the configuration to the smallest thing that
    still builds — no optional features, no extra system or Python
    packages — which is what a scripted environment (CI, a fresh clone,
    a reproduction attempt) wants when it needs a container and nothing
    else. It is the one statement of "minimal config", so a workflow
    file never has to hand-write another.
    """
    from vaibify.config.projectConfig import (
        ProjectConfig,
        fnSaveToFile,
    )
    sConfigPath = fsConfigPath()
    config = ProjectConfig(sProjectName=sProjectName)
    if bMinimal:
        config.listSystemPackages = []
        config.listPythonPackages = []
        config.features.bLatex = False
        config.features.bJupyter = False
    else:
        _fnApplyInstallerAgentDefaults(config)
    fnSaveToFile(config, sConfigPath)
    click.echo(f"Created {sConfigPath}")


def _fnApplyInstallerAgentDefaults(config):
    """Enable safe CLI defaults selected by the host installer."""
    pathDefaults = pathlib.Path.home() / ".vaibify" / "agent-defaults"
    if not pathDefaults.is_file():
        return
    dictFields = {
        "claude": "bClaude",
        "codex": "bCodex",
        "gemini": "bGemini",
        "opencode": "bOpenCode",
        "cline": "bCline",
        "openhands": "bOpenHands",
        "pi": "bPi",
    }
    for sAgent in pathDefaults.read_text(encoding="utf-8").splitlines():
        sField = dictFields.get(sAgent.strip().lower())
        if sField:
            setattr(config.features, sField, True)


def fnRegisterProject():
    """Register the current directory in the global project registry."""
    try:
        fnAddProject(str(pathlib.Path.cwd()))
    except (ValueError, FileNotFoundError):
        pass


@click.command("init")
@click.option(
    "--template",
    "sTemplateName",
    default=None,
    help="Name of the project template to use.",
)
@click.option(
    "--name",
    "sProjectName",
    default=None,
    help="Project name; scaffolds without a template when given alone.",
)
@click.option(
    "--minimal",
    "bMinimal",
    is_flag=True,
    default=False,
    help="Smallest config that still builds: no features, no packages.",
)
@click.option(
    "--force",
    "bForce",
    is_flag=True,
    default=False,
    help="Overwrite existing vaibify.yml.",
)
def init(sTemplateName, sProjectName, bMinimal, bForce):
    """Initialize a new Vaibify project in the current directory."""
    if sTemplateName is None and sProjectName is None:
        fnPrintAvailableTemplates()
        return
    if fbConfigExists() and not bForce:
        click.echo(
            "Error: vaibify.yml already exists. "
            "Use --force to overwrite."
        )
        sys.exit(1)
    if sTemplateName is not None:
        fnCopyTemplate(sTemplateName)
    fnWriteDefaultConfig(sProjectName or sTemplateName, bMinimal)
    fnRegisterProject()
    click.echo(
        f"Initialized Vaibify project "
        f"'{sProjectName or sTemplateName}'."
    )
