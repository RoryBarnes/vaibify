"""Shared utility for loading vaibcask.yml from the working directory."""

import pathlib
import sys

import click
import yaml

_sConfigFileName = "vaibcask.yml"


def fsConfigPath():
    """Return the absolute path to vaibcask.yml in the current directory."""
    return str(pathlib.Path.cwd() / _sConfigFileName)


def fconfigLoad():
    """Load vaibcask.yml and return a validated ProjectConfig.

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
        click.echo(
            f"Error: {_sConfigFileName} not found in the "
            "current directory."
        )
        click.echo("Run 'vaibcask init' to create one.")
        sys.exit(1)
    return _fconfigParse(sPath)


def _fconfigParse(sPath):
    """Parse the YAML config file and return a ProjectConfig."""
    try:
        from vaibcask.config.projectConfig import (
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
