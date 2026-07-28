"""CLI subcommand: vaibify init."""

import pathlib
import shutil
import sys

import click

from .configLoader import fsConfigPath

from vaibify.config.registryManager import fnAddProject
from vaibify.gui.workflowManager import VAIBIFY_PROJECTS_DIR
from vaibify.resources import S_TEMPLATES_TREE, fpathPackagedTree

S_PROJECT_FILE_NAME = "project.json"


def flistAvailableTemplates():
    """Return a list of template directory names shipped with the package."""
    pathTemplates = fpathPackagedTree(S_TEMPLATES_TREE)
    if not pathTemplates.is_dir():
        return []
    return sorted(
        d.name for d in pathTemplates.iterdir() if d.is_dir()
    )


def fsTemplatePath(sTemplateName):
    """Return the absolute path to a named template directory."""
    return str(fpathPackagedTree(S_TEMPLATES_TREE) / sTemplateName)


def fnPrintAvailableTemplates():
    """Print available template names, or exit non-zero if there are none.

    An installation with no templates is broken, not empty: every
    vaibify wheel ships them. Reporting that on stdout and returning 0
    made a packaging failure look like a successful run of ``vaibify
    init``, which is how the missing package data survived a release
    workflow that only imported the module.
    """
    listTemplates = flistAvailableTemplates()
    if not listTemplates:
        click.echo(
            "Error: no project templates are installed. The vaibify "
            "package was built without its data files; reinstall from "
            "a release wheel, or from a source checkout with "
            "'pip install -e .'.",
            err=True,
        )
        sys.exit(1)
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
    pathDestination = pathlib.Path.cwd()
    fnRefuseIfProjectFileExists(pathDestination)
    fnCopyDirectoryContents(sSourcePath, str(pathDestination))
    fnMoveProjectFileWhereDiscoveryLooks(pathDestination)


def fnRefuseIfProjectFileExists(pathDestination):
    """Exit before copying anything if the Project file is already there.

    Every check that can fail runs before the first write, so a refused
    init leaves the directory exactly as it found it. Checking after the
    copy left a root project.json, container.conf and the step
    directories behind on the way out — debris the researcher then has
    to identify and remove by hand, in a directory the command has just
    said it refused to touch.
    """
    pathTarget = (
        pathDestination / VAIBIFY_PROJECTS_DIR / S_PROJECT_FILE_NAME
    )
    if not pathTarget.exists():
        return
    click.echo(
        f"Error: {pathTarget.relative_to(pathDestination)} already "
        f"exists. Move or delete it before scaffolding over it. "
        f"Nothing was written.",
        err=True,
    )
    sys.exit(1)


def fnMoveProjectFileWhereDiscoveryLooks(pathDestination):
    """Relocate a scaffolded project.json into the discovered directory.

    Templates keep their Project file at the tree root, where it is the
    first thing a reader opens. Discovery scans only
    ``.vaibify/projects`` and the legacy ``.vaibify/workflows``, so a
    Project left at the root is one the dashboard cannot list and
    ``vaibify run`` cannot resolve: ``vaibify init --template`` scaffolded
    a project that nothing could open, and said it had succeeded.
    """
    pathSource = pathDestination / S_PROJECT_FILE_NAME
    if not pathSource.is_file():
        return
    pathTarget = (
        pathDestination / VAIBIFY_PROJECTS_DIR / S_PROJECT_FILE_NAME
    )
    pathTarget.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(pathSource), str(pathTarget))


def fnCopyDirectoryContents(sSourceDir, sDestDir):
    """Copy all files from sSourceDir into sDestDir.

    ``__pycache__`` is skipped: pip byte-compiles the shipped template
    scripts at install time, so copying the tree verbatim seeds every
    new project with stale ``.pyc`` files compiled against
    site-packages paths.
    """
    sSource = pathlib.Path(sSourceDir)
    for sItem in sSource.iterdir():
        if sItem.name == "__pycache__":
            continue
        sDest = pathlib.Path(sDestDir) / sItem.name
        if sItem.is_dir():
            shutil.copytree(
                str(sItem), str(sDest), dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("__pycache__"),
            )
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
