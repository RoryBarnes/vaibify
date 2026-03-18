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
