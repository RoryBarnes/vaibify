"""CLI subcommand: vaibify status."""

import sys

import click

from .configLoader import fbDockerAvailable, fconfigLoad


def fnShowDaemonStatus(dockerClient):
    """Print whether the Docker daemon is reachable."""
    try:
        dockerClient.ping()
        click.echo("Docker daemon: running")
    except Exception as error:
        click.echo(f"Docker daemon: unavailable ({error})")


def fnShowImageStatus(dockerClient, config):
    """Print the build status of the configured image."""
    sFullName = f"{config.sProjectName}:latest"
    try:
        image = dockerClient.images.get(sFullName)
        sCreated = image.attrs.get("Created", "unknown")
        click.echo(f"Image: {sFullName} (built {sCreated})")
    except Exception:
        click.echo(f"Image: {sFullName} (not found)")


def fnShowVolumeStatus(dockerClient, config):
    """Print the status of the workspace volume."""
    sVolumeName = f"{config.sProjectName}-workspace"
    try:
        dockerClient.volumes.get(sVolumeName)
        click.echo(f"Volume: {sVolumeName} (exists)")
    except Exception:
        click.echo(f"Volume: {sVolumeName} (not found)")


def fnShowContainerStatus(dockerClient, config):
    """Print whether a Vaibify container is running or stopped."""
    sProjectName = config.sProjectName
    try:
        listContainers = dockerClient.containers.list(
            all=True, filters={"name": sProjectName}
        )
        if not listContainers:
            click.echo("Container: none")
            return
        for dcContainer in listContainers:
            click.echo(
                f"Container: {dcContainer.name} "
                f"({dcContainer.status})"
            )
    except Exception:
        click.echo("Container: unable to query")


@click.command("status")
def status():
    """Show the status of the Vaibify environment."""
    if not fbDockerAvailable():
        click.echo(
            "Error: Docker support is not installed. "
            "Install with: pip install vaibify[docker]"
        )
        sys.exit(1)
    import docker
    dockerClient = docker.from_env()
    config = fconfigLoad()
    fnShowDaemonStatus(dockerClient)
    fnShowImageStatus(dockerClient, config)
    fnShowVolumeStatus(dockerClient, config)
    fnShowContainerStatus(dockerClient, config)
