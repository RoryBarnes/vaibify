"""CLI subcommand: vaibify destroy."""

import sys

import click

from .configLoader import fbDockerAvailable, fconfigResolveProject


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


def flistRemoveProjectImages(sProjectName):
    """Remove every tag in the project's repository; return what went.

    A build does not leave one image behind. It tags ``:base``, one tag
    per overlay, and finally ``:latest`` -- so this used to remove
    ``:latest`` alone and tell the researcher the environment was gone
    while most of its bytes stayed on the daemon. Measured on one
    researcher's machine: destroying a six-tag project would have
    untagged 4.18 GB of references and stranded ``:base``, ``:claude``,
    ``:codex``, ``:antigravity`` and ``:opencode`` behind it
    (2026-09-21).

    THE SET IS NOT DERIVED HERE. ``imageBuilder`` already answers
    "which images are this project's", and the dashboard's Delete has
    always asked it; this command asking a second way is how the two
    came to disagree in the first place. One authority, two callers.
    """
    from vaibify.docker import imageBuilder
    try:
        listReferences = imageBuilder.flistProjectImageReferences(
            sProjectName,
        )
    except RuntimeError as error:
        click.echo(f"Error listing images: {error}")
        sys.exit(1)
    if not listReferences:
        click.echo(f"No images found for '{sProjectName}'.")
        return []
    listRemoved = []
    for sReference in listReferences:
        if imageBuilder.fbRemoveImage(sReference):
            click.echo(f"Removed image: {sReference}")
            listRemoved.append(sReference)
        else:
            click.echo(f"Could not remove the image {sReference}.")
    return listRemoved


def fnRequireDocker():
    """Exit with an error if the Docker Python SDK is not installed."""
    if not fbDockerAvailable():
        click.echo(
            "Error: the docker Python package is missing. It installs "
            "with vaibify itself, so this vaibify installation is "
            "broken. Repair it with: pip install --force-reinstall vaibify"
        )
        sys.exit(1)


@click.command("destroy")
@click.option(
    "--project", "-p", "sProjectName", default=None,
    help="Project name (omit if in a project directory "
    "or only one project exists).",
)
def fnDestroyCommand(sProjectName):
    """Remove the Vaibify workspace volume and optionally the image."""
    fnRequireDocker()
    config = fconfigResolveProject(sProjectName)
    sVolumeName = f"{config.sProjectName}-workspace"
    if not click.confirm(
        f"This will remove the workspace volume "
        f"'{sVolumeName}'. Continue?"
    ):
        click.echo("Aborted.")
        return
    fnRemoveVolume(sVolumeName)
    if click.confirm(
        "Also remove the Docker images (the base layer, every overlay, "
        "and latest)?", default=False
    ):
        flistRemoveProjectImages(config.sProjectName)
    click.echo("Destroy complete.")
