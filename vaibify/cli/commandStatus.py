"""CLI subcommand: vaibify status."""

import sys

import click

from .configLoader import fbDockerAvailable, fconfigLoad


def fnShowDaemonStatus():
    """Print whether the Docker daemon is reachable."""
    try:
        import docker
        dockerClient = docker.from_env()
        dockerClient.ping()
        click.echo("Docker daemon: running")
    except Exception as error:
        click.echo(f"Docker daemon: unavailable ({error})")


def fnShowImageStatus(config):
    """Print the build status of the configured image."""
    sFullName = f"{config.sProjectName}:latest"
    try:
        import docker
        dockerClient = docker.from_env()
        image = dockerClient.images.get(sFullName)
        sCreated = image.attrs.get("Created", "unknown")
        click.echo(f"Image: {sFullName} (built {sCreated})")
    except Exception:
        click.echo(f"Image: {sFullName} (not found)")


def fnShowVolumeStatus(config):
    """Print the status of the workspace volume."""
    sVolumeName = f"{config.sProjectName}-workspace"
    try:
        import docker
        dockerClient = docker.from_env()
        dockerClient.volumes.get(sVolumeName)
        click.echo(f"Volume: {sVolumeName} (exists)")
    except Exception:
        click.echo(f"Volume: {sVolumeName} (not found)")


def fnShowContainerStatus(config):
    """Print whether a Vaibify container is running or stopped."""
    sProjectName = config.sProjectName
    try:
        import docker
        dockerClient = docker.from_env()
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
    config = fconfigLoad()
    fnShowDaemonStatus()
    fnShowImageStatus(config)
    fnShowVolumeStatus(config)
    fnShowContainerStatus(config)
