"""CLI subcommand: vaibify status.

Reports two different things about a project, deliberately kept apart.
The default output is the ENVIRONMENT — daemon, image, volume,
container — which is what "is my container ready?" means. ``--proof``
adds the PROJECT's reproducibility level and the blockers below it,
read from the same gates the dashboard renders. ``--json`` emits both
as one machine-readable object so a script can assert on a level
without opening a browser.
"""

import json
import sys

import click

from .configLoader import fbDockerAvailable, fconfigResolveProject


def fdockerBuildClientOrNone():
    """Return a Docker SDK client, or None when one cannot be built.

    ``docker.from_env()`` raises outright when the socket it guesses is
    absent — the ordinary case on a machine whose active context points
    somewhere else. A status command that dies with a stack trace tells
    the researcher less than one that says the daemon is unreachable and
    goes on to report everything it still can.
    """
    import docker
    try:
        return docker.from_env()
    except Exception:
        return None


def fsDescribeDaemon(dockerClient):
    """Return the daemon reachability line."""
    if dockerClient is None:
        return (
            "unavailable (no Docker socket; check 'docker context show')"
        )
    try:
        dockerClient.ping()
        return "running"
    except Exception as error:
        return f"unavailable ({error})"


def fnShowDaemonStatus(dockerClient):
    """Print whether the Docker daemon is reachable."""
    click.echo(f"Docker daemon: {fsDescribeDaemon(dockerClient)}")


def fnShowDockerContext():
    """Print the active Docker context, omitting the line if unknown."""
    from vaibify.docker.dockerContext import fsActiveDockerContext
    sContext = fsActiveDockerContext()
    if not sContext:
        return
    click.echo(f"Docker context: {sContext} (active)")


def fsDescribeImage(dockerClient, config):
    """Return the image build-status line."""
    sFullName = f"{config.sProjectName}:latest"
    try:
        image = dockerClient.images.get(sFullName)
        sCreated = image.attrs.get("Created", "unknown")
        return f"{sFullName} (built {sCreated})"
    except Exception:
        return f"{sFullName} (not found)"


def fnShowImageStatus(dockerClient, config):
    """Print the build status of the configured image."""
    click.echo(f"Image: {fsDescribeImage(dockerClient, config)}")


def fsDescribeVolume(dockerClient, config):
    """Return the workspace-volume status line."""
    sVolumeName = f"{config.sProjectName}-workspace"
    try:
        dockerClient.volumes.get(sVolumeName)
        return f"{sVolumeName} (exists)"
    except Exception:
        return f"{sVolumeName} (not found)"


def fnShowVolumeStatus(dockerClient, config):
    """Print the status of the workspace volume."""
    click.echo(f"Volume: {fsDescribeVolume(dockerClient, config)}")


def flistDescribeContainers(dockerClient, config):
    """Return one status line per container matching the project name."""
    sProjectName = config.sProjectName
    try:
        listContainers = dockerClient.containers.list(
            all=True, filters={"name": sProjectName}
        )
    except Exception:
        return ["unable to query"]
    if not listContainers:
        return ["none"]
    return [
        f"{dcContainer.name} ({dcContainer.status})"
        for dcContainer in listContainers
    ]


def fnShowContainerStatus(dockerClient, config):
    """Print whether a Vaibify container is running or stopped."""
    for sLine in flistDescribeContainers(dockerClient, config):
        click.echo(f"Container: {sLine}")


def fdictBuildEnvironmentStatus(dockerClient, config):
    """Return the daemon/image/volume/container facts as one dict."""
    from vaibify.docker.dockerContext import fsActiveDockerContext
    return {
        "sProjectName": config.sProjectName,
        "sDaemon": fsDescribeDaemon(dockerClient),
        "sDockerContext": fsActiveDockerContext(),
        "sImage": fsDescribeImage(dockerClient, config),
        "sVolume": fsDescribeVolume(dockerClient, config),
        "listContainers": flistDescribeContainers(dockerClient, config),
    }


def fdictBuildLevelSection(config):
    """Return the project's PROOF report, or why it is unavailable."""
    from .commandUtilsDocker import fconnectionRequireDocker
    from .levelReport import (
        fdictBuildLevelReport, ftLoadContainerLevelInputs,
    )
    try:
        dictWorkflow, filesRepo = ftLoadContainerLevelInputs(
            fconnectionRequireDocker(), config.sProjectName,
        )
    except LookupError as error:
        return {"bAvailable": False, "sReason": str(error)}
    except Exception as error:
        return {
            "bAvailable": False,
            "sReason": f"could not read the container: {error}",
        }
    dictReport = fdictBuildLevelReport(dictWorkflow, filesRepo)
    dictReport["bAvailable"] = True
    return dictReport


def _fnEmitProofSection(config):
    """Print the PROOF section, or the honest reason it is missing."""
    from .levelReport import fnPrintLevelReport
    dictLevel = fdictBuildLevelSection(config)
    if not dictLevel["bAvailable"]:
        click.echo(f"PROOF level: unavailable ({dictLevel['sReason']})")
        return
    fnPrintLevelReport(dictLevel)


@click.command("status")
@click.option(
    "--project", "-p", "sProjectName", default=None,
    help="Project name (omit if in a project directory "
    "or only one project exists).",
)
@click.option(
    "--proof", "bProof", is_flag=True, default=False,
    help="Also report the project's PROOF level and its blockers.",
)
@click.option(
    "--json", "bJson", is_flag=True, default=False,
    help="Emit environment and PROOF status as one JSON object.",
)
def fnStatusCommand(sProjectName, bProof, bJson):
    """Show the status of the Vaibify environment."""
    if not fbDockerAvailable():
        click.echo(
            "Error: the docker Python package is missing. It installs "
            "with vaibify itself, so this vaibify installation is "
            "broken. Repair it with: pip install --force-reinstall vaibify"
        )
        sys.exit(1)
    dockerClient = fdockerBuildClientOrNone()
    config = fconfigResolveProject(sProjectName)
    if bJson:
        click.echo(json.dumps({
            "dictEnvironment": fdictBuildEnvironmentStatus(
                dockerClient, config,
            ),
            "dictProof": fdictBuildLevelSection(config),
        }, indent=2))
        return
    fnShowDaemonStatus(dockerClient)
    fnShowDockerContext()
    fnShowImageStatus(dockerClient, config)
    fnShowVolumeStatus(dockerClient, config)
    fnShowContainerStatus(dockerClient, config)
    if bProof:
        _fnEmitProofSection(config)
