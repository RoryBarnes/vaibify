"""CLI subcommand: vaibify build."""

import os
import sys

import click

from .configLoader import fconfigLoad, fsDockerDir


def fnBuildFromConfig(config, sDockerDir, bNoCache):
    """Invoke the Docker image builder with the loaded configuration.

    Uses lazy import so the CLI remains usable without Docker installed.
    """
    try:
        from vaibify.docker.imageBuilder import fnBuildImage
    except ImportError:
        click.echo(
            "Error: Docker support is not installed. "
            "Install with: pip install vaibify[docker]"
        )
        sys.exit(1)
    fnPrepareBuildContext(config, sDockerDir)
    fnBuildImage(config, sDockerDir, bNoCache=bNoCache)


def fnPrepareBuildContext(config, sDockerDir):
    """Generate all config-derived files in the Docker build context."""
    from vaibify.config.containerConfig import (
        fnGenerateContainerConf,
    )
    fnGenerateContainerConf(
        config, os.path.join(sDockerDir, "container.conf")
    )
    fnWriteSystemPackages(config, sDockerDir)
    fnWritePythonPackages(config, sDockerDir)
    fnWritePipInstallFlags(config, sDockerDir)
    fnWriteBinariesEnv(config, sDockerDir)


def fnWriteSystemPackages(config, sDockerDir):
    """Write listSystemPackages to system-packages.txt."""
    sPath = os.path.join(sDockerDir, "system-packages.txt")
    sContent = "\n".join(config.listSystemPackages) + "\n"
    _fnWriteFile(sPath, sContent)


def fnWritePythonPackages(config, sDockerDir):
    """Write listPythonPackages to requirements.txt."""
    sPath = os.path.join(sDockerDir, "requirements.txt")
    sContent = "\n".join(config.listPythonPackages) + "\n"
    _fnWriteFile(sPath, sContent)


def fnWritePipInstallFlags(config, sDockerDir):
    """Write sPipInstallFlags to pip-flags.txt."""
    sPath = os.path.join(sDockerDir, "pip-flags.txt")
    _fnWriteFile(sPath, config.sPipInstallFlags.strip() + "\n")


def fnWriteBinariesEnv(config, sDockerDir):
    """Write listBinaries to binaries.env as KEY=VALUE lines."""
    sPath = os.path.join(sDockerDir, "binaries.env")
    listLines = []
    for dictBinary in config.listBinaries:
        sName = dictBinary.get("name", "")
        sBinPath = dictBinary.get("path", "")
        if sName and sBinPath:
            listLines.append(f"{sName}={sBinPath}")
    sContent = "\n".join(listLines) + "\n"
    _fnWriteFile(sPath, sContent)


def _fnWriteFile(sPath, sContent):
    """Write string content to a file."""
    with open(sPath, "w") as fileHandle:
        fileHandle.write(sContent)


@click.command("build")
@click.option(
    "--no-cache",
    "bNoCache",
    is_flag=True,
    default=False,
    help="Build the image without using Docker cache.",
)
def build(bNoCache):
    """Build the Vaibify Docker image from vaibify.yml."""
    config = fconfigLoad()
    sDockerDir = fsDockerDir()
    click.echo(
        f"Building image {config.sProjectName}:latest ..."
    )
    fnBuildFromConfig(config, sDockerDir, bNoCache)
    click.echo("Build complete.")
