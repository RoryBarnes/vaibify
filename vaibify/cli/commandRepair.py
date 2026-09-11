"""``vaibify repair <subject>`` — the acted-on half of ``vaibify doctor``.

Doctor diagnoses and changes nothing. When its finding has a fix
vaibify itself can apply, that fix lives here, as its own command with
its own subject, so a researcher who read a finding can act on it
without assembling a lifecycle operation by hand.

One subject exists today: ``dns``. It reads the resolver
configuration FIRST and branches on it, because the three
configurations have genuinely different remedies and one of them
cannot be repaired by a restart at all. Recommending a restart for an
explicit ``HostConfig.Dns`` would look exactly like the fix failing:
the command would succeed, the container would come back, and the
resolver would be the same one.

Every repair re-probes afterwards and reports the state it actually
found. A repair that claims success without re-checking is the
"marker written after a step that silently did nothing" failure, and
it is worse here than usual: the researcher walks away believing the
network works.
"""

import sys

import click

from vaibify.config.containerLock import fdictReadLockHolder
from vaibify.docker.containerLifecycleRepair import RepairRefusedError

from .configLoader import fconfigResolveProject
from .doctorNetwork import (
    S_RESOLVER_EXPLICIT_DNS, fdictResolveProbeTarget,
    fsClassifyResolverConfiguration,
)


__all__ = [
    "fnRepairCommand", "I_REPAIR_CONFIRMED", "I_REPAIR_FAILED",
    "I_REPAIR_UNVERIFIED",
]


def _fconnectionOpenDockerOrExit(sContainerName):
    """Return a DockerConnection, or exit having said why not."""
    try:
        from vaibify.docker.dockerConnection import DockerConnection
        return DockerConnection()
    except Exception as errorConnect:
        click.echo(
            f"Error: cannot reach the Docker daemon, so '{sContainerName}' "
            f"cannot be repaired: {errorConnect}", err=True,
        )
        sys.exit(1)


def _fsReadResolverKind(connectionDocker, sContainerName):
    """Return the container's resolver configuration classification."""
    from vaibify.docker.containerManager import fjsonInspectContainer
    from .doctorNetwork import fsReadContainerResolvConf
    return fsClassifyResolverConfiguration(
        fjsonInspectContainer(sContainerName),
        fsReadContainerResolvConf(connectionDocker, sContainerName),
    )


S_REPROBE_CONFIRMED = "confirmed"
S_REPROBE_STILL_FAILING = "still-failing"
S_REPROBE_UNVERIFIED = "unverified"


def _fsReprobeResolution(config, connectionDocker, sContainerName):
    """Return which of the THREE outcomes the re-probe established.

    Three, not two, and the missing one was a real defect: with no
    name to resolve this returned True, and the caller then printed
    that the container "resolves names again" -- a claim about a probe
    that never ran, after a restart that killed every shell in the
    container. That is the "marker written after a step that silently
    did nothing" failure, in the one place the plan named it.

    ``unverified`` is not a failure and not a success. The repair
    happened; nothing was established about whether it helped.
    """
    dictTarget = fdictResolveProbeTarget(config)
    if not dictTarget["sHostname"]:
        return S_REPROBE_UNVERIFIED
    dictAnswer = connectionDocker.fdictResolveHostnameInContainer(
        sContainerName, dictTarget["sHostname"],
    )
    if not dictAnswer.get("bAnswered"):
        return S_REPROBE_UNVERIFIED
    if dictAnswer.get("listAddresses"):
        return S_REPROBE_CONFIRMED
    return S_REPROBE_STILL_FAILING


def _fiRunRepair(config, sContainerName, sOperation):
    """Run one repair on whichever lane owns the container."""
    dictHolder = fdictReadLockHolder(sContainerName)
    if dictHolder and dictHolder.get("iPort"):
        if sOperation == "recreate":
            # Refused HERE as well as at the hub, and not as belt and
            # braces: this refusal names the live session, which the
            # researcher can act on, without spending a round trip to
            # be told the same thing. The hub refuses too, because a
            # refusal only the client enforces is not one.
            click.echo(
                f"Refused: a live vaibify hub (pid="
                f"{dictHolder.get('iPid')}, port={dictHolder.get('iPort')}) "
                f"holds '{sContainerName}'. Recreating gives the "
                "container a new id, and that session is bound to the "
                "old one. Close the dashboard session for this "
                "project, then run this again.", err=True,
            )
            return 1
        return _fiRouteRepairToLiveHub(sContainerName, sOperation, dictHolder)
    from vaibify.docker import containerLifecycleRepair
    try:
        containerLifecycleRepair.fdictRepairDirectlyUnderFlock(
            config, sContainerName, sOperation, click.echo,
        )
    except RepairRefusedError as errorRefused:
        click.echo(f"Refused: {errorRefused}", err=True)
        return 1
    return 0


def _fiRouteRepairToLiveHub(sContainerName, sOperation, dictHolder):
    """Ask the hub that owns this container to perform the repair."""
    from vaibify.gui.hostControlChannel import (
        HostControlError, S_SOCKET_OPERATION_REPAIR_CONTAINER,
        fdictSendHostControlRequest,
    )
    iHubPort = dictHolder.get("iPort") or 0
    click.echo(
        f"Container '{sContainerName}' is held by a live vaibify hub "
        f"(pid={dictHolder.get('iPid')}, port={iHubPort}); routing the "
        "repair over its host control socket."
    )
    try:
        dictResponse = fdictSendHostControlRequest(iHubPort, {
            "sOperation": S_SOCKET_OPERATION_REPAIR_CONTAINER,
            "sContainerName": sContainerName,
            "sRepairOperation": sOperation,
        })
    except HostControlError as errorControl:
        click.echo(f"Error: {errorControl}", err=True)
        return 1
    for sAnnouncement in dictResponse.get("listAnnouncements", []):
        click.echo(sAnnouncement)
    if not dictResponse.get("bAccepted"):
        click.echo(
            f"Refused by the hub: {dictResponse.get('sError', '')}",
            err=True,
        )
        return 1
    return 0


def _fnExplainExplicitDnsRefusal(sContainerName):
    """Say why a restart cannot help, and what can."""
    click.echo(
        f"Refused: container '{sContainerName}' has DNS servers baked "
        "into it (HostConfig.Dns), which no restart can change -- a "
        "restart would succeed and leave the same resolver in place, "
        "which looks exactly like the fix failing.\n"
        "\n"
        "Vaibify never sets that field, so it was introduced outside "
        "vaibify and this container's specification has drifted from "
        "its vaibify configuration. The repair is to recreate the "
        "container from that configuration, which sets no DNS servers "
        "at all:\n"
        f"  $ vaibify repair dns --recreate --project {sContainerName}",
        err=True,
    )


def _fiRepairDnsForProject(config, bRecreate, bAssumeYes):
    """Read the resolver configuration first, then act on what it says."""
    sContainerName = config.sProjectName
    connectionDocker = _fconnectionOpenDockerOrExit(sContainerName)
    sResolverKind = _fsReadResolverKind(connectionDocker, sContainerName)
    if sResolverKind == S_RESOLVER_EXPLICIT_DNS and not bRecreate:
        _fnExplainExplicitDnsRefusal(sContainerName)
        return 1
    sOperation = "recreate" if bRecreate else "restart"
    if not _fbConfirmRepair(sContainerName, sOperation, bAssumeYes):
        click.echo("Nothing was changed.")
        return 1
    iOutcome = _fiRunRepair(config, sContainerName, sOperation)
    if iOutcome != 0:
        return iOutcome
    return _fiReportReprobe(config, connectionDocker, sContainerName)


I_REPAIR_CONFIRMED = 0
I_REPAIR_FAILED = 1
I_REPAIR_UNVERIFIED = 2


def _fiReportReprobe(config, connectionDocker, sContainerName):
    """Re-probe after the repair and report what was actually found.

    Exits 0 ONLY on a confirmed re-probe. An unverified repair exits
    2, because a researcher scripting this must be able to tell "it
    works now" from "something was changed and nobody checked".
    """
    sOutcome = _fsReprobeResolution(
        config, connectionDocker, sContainerName,
    )
    if sOutcome == S_REPROBE_CONFIRMED:
        click.echo(f"Done; '{sContainerName}' resolves names again.")
        return I_REPAIR_CONFIRMED
    if sOutcome == S_REPROBE_UNVERIFIED:
        click.echo(
            f"The repair ran, but NOTHING was verified: this project "
            "names no host of its own that vaibify may resolve, or "
            "the container could not answer a lookup at all. Do not "
            "read this as a fix. Check it yourself, or run `vaibify "
            "doctor --container`.", err=True,
        )
        return I_REPAIR_UNVERIFIED
    click.echo(
        f"The repair completed, but '{sContainerName}' still does not "
        "resolve the name it depends on. Run `vaibify doctor "
        "--container` -- if this host cannot resolve it either, the "
        "fault is the network you are on and no vaibify command fixes "
        "it.", err=True,
    )
    return I_REPAIR_FAILED


def _fbConfirmRepair(sContainerName, sOperation, bAssumeYes):
    """Tell the researcher what the repair does, then ask."""
    from vaibify.docker.containerLifecycleRepair import (
        fsDescribeRestartConsequences,
    )
    click.echo(fsDescribeRestartConsequences(sContainerName))
    if sOperation == "recreate":
        click.echo(
            "  - and recreate the container from the image it is "
            "running now, discarding its writable layer."
        )
    if bAssumeYes:
        return True
    return click.confirm("Proceed?", default=False)


@click.group("repair")
def fnRepairCommand():
    """Apply a repair vaibify can make for you."""


@fnRepairCommand.command("dns")
@click.option(
    "--project", "-p", "sProjectName", default=None,
    help="Project name (omit if in a project directory).",
)
@click.option(
    "--recreate", "bRecreate", is_flag=True, default=False,
    help="Recreate the container from the image it is running, rather "
         "than restarting it. Required when DNS servers were baked "
         "into the container from outside vaibify.",
)
@click.option(
    "--yes", "bAssumeYes", is_flag=True, default=False,
    help="Skip the confirmation prompt.",
)
def fnRepairDnsCommand(sProjectName, bRecreate, bAssumeYes):
    """Clear a container's stale resolver state."""
    sys.exit(_fiRepairDnsForProject(
        fconfigResolveProject(sProjectName), bRecreate, bAssumeYes,
    ))
