"""Shared Docker utilities for CLI commands."""

import json
import sys

import click


def fconnectionRequireDocker():
    """Create and return a DockerConnection, exiting if unavailable."""
    try:
        from vaibify.docker.dockerConnection import DockerConnection
        return DockerConnection()
    except (ImportError, Exception) as error:
        click.echo(f"Error: Docker is not available: {error}")
        sys.exit(2)


def fsRequireRunningContainer(configProject):
    """Verify the project container is running and return its name."""
    sContainerName = configProject.sProjectName
    connectionDocker = fconnectionRequireDocker()
    listRunning = connectionDocker.flistGetRunningContainers()
    listMatching = [
        dictContainer for dictContainer in listRunning
        if dictContainer["sName"] == sContainerName
    ]
    if not listMatching:
        click.echo(
            f"Error: Container '{sContainerName}' is not running. "
            f"Start it with: vaibify start"
        )
        sys.exit(2)
    return sContainerName


def fdictRequireWorkflow(connectionDocker, sContainerName):
    """Load workflow JSON from the container, exiting if not found."""
    from vaibify.gui.workflowManager import (
        fdictLoadWorkflowFromContainer,
        flistFindWorkflowsInContainer,
    )
    listWorkflows = flistFindWorkflowsInContainer(
        connectionDocker, sContainerName
    )
    if not listWorkflows:
        click.echo("Error: No workflow found in container.")
        sys.exit(2)
    sWorkflowPath = listWorkflows[0]["sPath"]
    dictWorkflow = fdictLoadWorkflowFromContainer(
        connectionDocker, sContainerName, sWorkflowPath
    )
    return {"dictWorkflow": dictWorkflow, "sWorkflowPath": sWorkflowPath}


def fnPrintJson(dictData):
    """Print indented JSON to stdout."""
    click.echo(json.dumps(dictData, indent=2))


def fbShouldOutputJson(bJsonFlag):
    """Return True if JSON output is requested or stdout is not a TTY."""
    if bJsonFlag:
        return True
    return not sys.stdout.isatty()
