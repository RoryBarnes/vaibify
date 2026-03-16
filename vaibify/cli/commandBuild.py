"""CLI subcommand: vaibify build."""

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
    fnPrepareContainerConf(config, sDockerDir)
    fnBuildImage(config, sDockerDir, bNoCache=bNoCache)


def fnPrepareContainerConf(config, sDockerDir):
    """Generate container.conf in the Docker build context."""
    from vaibify.config.containerConfig import (
        fnGenerateContainerConf,
    )
    import os
    sConfPath = os.path.join(sDockerDir, "container.conf")
    fnGenerateContainerConf(config, sConfPath)


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
