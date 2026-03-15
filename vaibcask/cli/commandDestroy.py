"""CLI subcommand: vaibcask destroy."""

import sys

import click

from .configLoader import fbDockerAvailable, fconfigLoad


def fnRemoveVolume(sVolumeName):
    """Remove a Docker volume by name."""
    import docker
    dockerClient = docker.from_env()
    try:
        volume = dockerClient.volumes.get(sVolumeName)
        volume.remove(force=True)
        click.echo(f"Removed volume: {sVolumeName}")
    except docker.errors.NotFound:
        click.echo(f"Volume '{sVolumeName}' does not exist.")
    except docker.errors.APIError as error:
        click.echo(f"Error removing volume: {error}")
        sys.exit(1)


def fnRemoveImage(sFullName):
    """Remove a Docker image by full name."""
    import docker
    dockerClient = docker.from_env()
    try:
        dockerClient.images.remove(sFullName, force=True)
        click.echo(f"Removed image: {sFullName}")
    except docker.errors.ImageNotFound:
        click.echo(f"Image '{sFullName}' does not exist.")
    except docker.errors.APIError as error:
        click.echo(f"Error removing image: {error}")
        sys.exit(1)


def fnRequireDocker():
    """Exit with an error if the Docker Python SDK is not installed."""
    if not fbDockerAvailable():
        click.echo(
            "Error: Docker support is not installed. "
            "Install with: pip install vaibcask[docker]"
        )
        sys.exit(1)


@click.command("destroy")
def destroy():
    """Remove the VaibCask workspace volume and optionally the image."""
    fnRequireDocker()
    config = fconfigLoad()
    sVolumeName = f"{config.sProjectName}-workspace"
    sFullName = f"{config.sProjectName}:latest"
    if not click.confirm(
        f"This will remove the workspace volume "
        f"'{sVolumeName}'. Continue?"
    ):
        click.echo("Aborted.")
        return
    fnRemoveVolume(sVolumeName)
    if click.confirm(
        "Also remove the Docker image?", default=False
    ):
        fnRemoveImage(sFullName)
    click.echo("Destroy complete.")
