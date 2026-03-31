"""Shared utility for loading vaibify.yml from the working directory."""

import pathlib
import sys

import click
import yaml

_sConfigFileName = "vaibify.yml"
_sConfigOverride = None


def fnSetConfigPath(sPath):
    """Override the default config file path."""
    global _sConfigOverride
    _sConfigOverride = sPath


def fsConfigPath():
    """Return the absolute path to the config file."""
    if _sConfigOverride:
        return str(pathlib.Path(_sConfigOverride).resolve())
    return str(pathlib.Path.cwd() / _sConfigFileName)


def fconfigResolveProject(sProjectName=None):
    """Resolve the target project config.

    Resolution order:
    1. If sProjectName is provided, look it up in the registry
    2. If cwd contains a vaibify.yml, use it (current behavior)
    3. If the registry has exactly one project, use it
    4. If the registry has multiple projects, list them and exit
    5. If no project found anywhere, exit with helpful message
    """
    if sProjectName:
        return _fconfigLoadFromRegistry(sProjectName)
    sLocalPath = str(pathlib.Path.cwd() / _sConfigFileName)
    if pathlib.Path(sLocalPath).is_file():
        return _fconfigParse(sLocalPath)
    return _fconfigResolveFromRegistry()


def _fconfigLoadFromRegistry(sProjectName):
    """Load a project config by name from the global registry."""
    from vaibify.config.registryManager import fdictLoadRegistry
    dictRegistry = fdictLoadRegistry()
    listProjects = dictRegistry["listProjects"]
    for dictProject in listProjects:
        if dictProject["sName"] == sProjectName:
            return _fconfigParse(dictProject["sConfigPath"])
    _fnPrintAvailableAndExit(listProjects, sProjectName)


def _fconfigResolveFromRegistry():
    """Resolve a project from the registry when no name given."""
    from vaibify.config.registryManager import fdictLoadRegistry
    dictRegistry = fdictLoadRegistry()
    listProjects = dictRegistry["listProjects"]
    if len(listProjects) == 1:
        return _fconfigParse(listProjects[0]["sConfigPath"])
    if len(listProjects) == 0:
        click.echo(
            "No vaibify projects found. "
            "Run 'vaibify init' in a project directory."
        )
        sys.exit(1)
    _fnPrintMultipleAndExit(listProjects)


def _fnPrintAvailableAndExit(listProjects, sRequestedName):
    """Print available projects and exit after a failed lookup."""
    click.echo(
        f"Error: Project '{sRequestedName}' not found in registry."
    )
    if listProjects:
        click.echo("Available projects:")
        for dictProject in listProjects:
            click.echo(f"  {dictProject['sName']}")
    sys.exit(1)


def _fnPrintMultipleAndExit(listProjects):
    """Print project list and exit when multiple are registered."""
    click.echo(
        "Multiple projects found. "
        "Specify one with --project/-p:"
    )
    for dictProject in listProjects:
        click.echo(f"  {dictProject['sName']}")
    sys.exit(1)


def fconfigLoad():
    """Load vaibify.yml and return a validated ProjectConfig.

    Returns
    -------
    ProjectConfig
        Validated configuration dataclass instance.

    Raises
    ------
    SystemExit
        If the config file does not exist or fails validation.
    """
    sPath = fsConfigPath()
    if not pathlib.Path(sPath).is_file():
        click.echo(f"Error: Config file not found: {sPath}")
        click.echo("Run 'vaibify init' to create one.")
        sys.exit(1)
    return _fconfigParse(sPath)


def _fconfigParse(sPath):
    """Parse the YAML config file and return a ProjectConfig."""
    try:
        from vaibify.config.projectConfig import (
            fconfigLoadFromFile,
        )
        return fconfigLoadFromFile(sPath)
    except (ValueError, FileNotFoundError, TypeError,
            yaml.YAMLError) as error:
        click.echo(f"Error: Failed to load {sPath}: {error}")
        sys.exit(1)


def fconfigLoadFromPath(sPath):
    """Load a vaibify.yml from an explicit path.

    Unlike ``fconfigLoad``, this raises on error instead of
    calling ``sys.exit``, making it safe for server processes.

    Parameters
    ----------
    sPath : str
        Absolute path to the YAML configuration file.

    Returns
    -------
    ProjectConfig
        Validated configuration dataclass instance.

    Raises
    ------
    ValueError
        If the file is missing or fails validation.
    """
    if not pathlib.Path(sPath).is_file():
        raise ValueError(f"Config file not found: {sPath}")
    from vaibify.config.projectConfig import fconfigLoadFromFile
    return fconfigLoadFromFile(sPath)


def fsDockerDir():
    """Return the path to the docker/ directory in the package root."""
    sPackageRoot = str(pathlib.Path(__file__).resolve().parents[2])
    return str(pathlib.Path(sPackageRoot) / "docker")


def fbDockerAvailable():
    """Return True if the Docker Python SDK is importable."""
    try:
        import docker  # noqa: F401
        return True
    except ImportError:
        return False
