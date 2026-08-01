"""``vaibify reconcile`` — the proving exit from a journal quarantine.

Host-lane only (design §8): reconciliation is a trust judgement about
possibly-corrupt state, so it never rides the browser or agent lanes
and needs no capability. Discovery decides the path: when a LIVE hub
holds the container's flock, the request is routed to that hub over its
peer-authenticated host control socket; when no live hub holds it, the
crash-time transaction runs directly under a freshly-taken
reconciliation flock. Either way the transaction shows WHICH operation
was mid-write, WHEN it was prepared and went in flight, and WHICH
container, proves the recorded writer dead and the operation settled,
and clears the durable marker LAST — or refuses and leaves the
container quarantined.
"""

import sys

import click

from vaibify.config import reconciliation
from vaibify.config.containerLock import (
    fbIsValidProjectName,
    fdictReadLockHolder,
)


def _fconnectionCreateDockerQuietly():
    """Return a DockerConnection, or None when the daemon is unreachable.

    Reconciliation without Docker still proves process-holder records;
    Docker-identified records then refuse honestly ("cannot be proven
    settled") instead of the command failing outright.
    """
    try:
        from vaibify.docker.dockerConnection import DockerConnection
        return DockerConnection()
    except Exception:
        return None


def _fnPrintJournalRecords(sContainerName, listRecords):
    """Print the allowlisted display fields of every journal record."""
    click.echo(f"Container '{sContainerName}' has "
               f"{len(listRecords)} unsettled journal record(s):")
    for dictRecord in listRecords:
        click.echo(f"  operation {dictRecord['sOperationId']}:")
        click.echo(f"    state:     {dictRecord['sState']}")
        click.echo(f"    kind:      {dictRecord['sKind']}")
        click.echo(f"    target:    {dictRecord['sTarget']}")
        click.echo(f"    prepared:  {dictRecord['sPreparedIso']}")
        if dictRecord["sInFlightIso"]:
            click.echo(f"    in flight: {dictRecord['sInFlightIso']}")
        if dictRecord["sNote"]:
            click.echo(f"    note:      {dictRecord['sNote']}")


def _fiReportMalformedJournal(sContainerName, error):
    """Explain a non-valid journal and name the break-glass path."""
    from vaibify.config.operationJournal import fsComputeJournalFileSha256
    click.echo(f"Error: {error}", err=True)
    if getattr(error, "sReadState", "") == "requiresUpgrade":
        click.echo(
            "Upgrade vaibify to a build that understands this journal; "
            "it is never cleared blind.", err=True,
        )
        return 1
    sMarkerSha256 = fsComputeJournalFileSha256(sContainerName)
    click.echo(
        "If you have inspected the marker and accept destroying it, "
        "re-run with:\n"
        f"  vaibify reconcile {sContainerName} "
        f"--break-glass {sMarkerSha256}",
        err=True,
    )
    return 1


def fiRunCrashTimeReconcile(sContainerName, bAssumeYes):
    """Run the crash-time transaction; return the process exit code."""
    try:
        listRecords = reconciliation.flistDescribeJournalRecords(
            sContainerName,
        )
    except reconciliation.ReconciliationRefusedError as error:
        return _fiReportMalformedJournal(sContainerName, error)
    if not listRecords:
        click.echo(
            f"Container '{sContainerName}' has no journal marker; "
            "nothing to reconcile."
        )
        return 0
    _fnPrintJournalRecords(sContainerName, listRecords)
    if not bAssumeYes and not click.confirm(
        "Prove these operations settled and clear the quarantine?"
    ):
        click.echo("Reconciliation cancelled; the quarantine stands.")
        return 1
    setExpectedIds = {
        dictRecord["sOperationId"] for dictRecord in listRecords
    }
    try:
        dictProven = reconciliation.fdictReconcileCrashTimeJournal(
            sContainerName, _fconnectionCreateDockerQuietly(),
            setExpectedIds,
        )
    except reconciliation.ReconciliationRefusedError as error:
        click.echo(f"Reconciliation refused: {error}", err=True)
        return 1
    for sNote in dictProven["listRecordNotes"]:
        click.echo(f"  proven: {sNote}")
    click.echo(
        f"Reconciled container '{sContainerName}'; it is claimable again."
    )
    return 0


def fiRunCrashTimeBreakGlass(sContainerName, sMarkerSha256):
    """Run the crash-time break-glass; return the process exit code."""
    try:
        reconciliation.fdictExecuteBreakGlass(
            sContainerName, sMarkerSha256,
            fnStopContainerByName=_fnStopContainerByName,
        )
    except reconciliation.ReconciliationRefusedError as error:
        click.echo(f"Break-glass refused: {error}", err=True)
        return 1
    click.echo(
        f"Break-glass cleared the malformed marker for container "
        f"'{sContainerName}'."
    )
    return 0


def _fnStopContainerByName(sContainerName):
    """Stop the possibly-relevant container before a break-glass."""
    from vaibify.docker.containerManager import fnStopContainer
    fnStopContainer(sContainerName)


def _fiRouteToLiveHub(
    sContainerName, dictHolder, bAssumeYes, sBreakGlassSha256,
    sForceAbandonOperationId,
):
    """Route the operation to the live hub over its control socket."""
    from vaibify.gui.hostControlChannel import (
        HostControlError, fdictSendHostControlRequest,
    )
    iHubPort = dictHolder.get("iPort") or 0
    if not isinstance(iHubPort, int) or iHubPort <= 0:
        click.echo(
            f"Error: a live process (pid={dictHolder.get('iPid')}) holds "
            f"container '{sContainerName}' but reports no hub port, so "
            "there is no host control socket to route to. Stop that "
            "process, then run 'vaibify reconcile' again.", err=True,
        )
        return 1
    click.echo(
        f"Container '{sContainerName}' is held by a live vaibify hub "
        f"(pid={dictHolder.get('iPid')}, port={iHubPort}); routing over "
        "its host control socket."
    )
    dictRequest = _fdictBuildHubRequest(
        sContainerName, bAssumeYes, sBreakGlassSha256,
        sForceAbandonOperationId,
    )
    if dictRequest is None:
        return 1
    try:
        dictResponse = fdictSendHostControlRequest(iHubPort, dictRequest)
    except HostControlError as error:
        click.echo(f"Error: {error}", err=True)
        return 1
    if not dictResponse.get("bAccepted"):
        click.echo(
            f"Refused by the hub: {dictResponse.get('sError', '')}",
            err=True,
        )
        return 1
    for sNote in dictResponse.get("listRecordNotes", []):
        click.echo(f"  proven: {sNote}")
    if dictResponse.get("bPoisoned"):
        click.echo(
            f"Force-abandoned; container '{sContainerName}' refuses all "
            "mutation until 'vaibify reconcile' proves the worker dead."
        )
    else:
        click.echo(f"Done; the hub reconciled '{sContainerName}'.")
    return 0


def _fdictBuildHubRequest(
    sContainerName, bAssumeYes, sBreakGlassSha256,
    sForceAbandonOperationId,
):
    """Build the socket request, showing and confirming the records."""
    if sForceAbandonOperationId:
        return {
            "sOperation": "force-abandon",
            "sContainerName": sContainerName,
            "sExpectedOperationId": sForceAbandonOperationId,
        }
    if sBreakGlassSha256:
        return {
            "sOperation": "break-glass",
            "sContainerName": sContainerName,
            "sMarkerSha256": sBreakGlassSha256,
        }
    try:
        listRecords = reconciliation.flistDescribeJournalRecords(
            sContainerName,
        )
    except reconciliation.ReconciliationRefusedError as error:
        _fiReportMalformedJournal(sContainerName, error)
        return None
    if not listRecords:
        click.echo(
            f"Container '{sContainerName}' has no journal marker; "
            "nothing to reconcile."
        )
        return None
    _fnPrintJournalRecords(sContainerName, listRecords)
    if not bAssumeYes and not click.confirm(
        "Ask the hub to prove these operations settled and clear them?"
    ):
        click.echo("Reconciliation cancelled; the quarantine stands.")
        return None
    return {
        "sOperation": "reconcile",
        "sContainerName": sContainerName,
        "listExpectedOperationIds": sorted(
            dictRecord["sOperationId"] for dictRecord in listRecords
        ),
    }


@click.command("reconcile")
@click.argument("container")
@click.option(
    "--yes", "bAssumeYes", is_flag=True,
    help="Skip the confirmation prompt.",
)
@click.option(
    "--break-glass", "sBreakGlassSha256", default="",
    help="Destructively clear a MALFORMED marker whose raw bytes hash "
         "to this sha256 (shown by a plain 'vaibify reconcile' run).",
)
@click.option(
    "--force-abandon", "sForceAbandonOperationId", default="",
    help="Poison the named wedged operation on the live hub that holds "
         "this container; mutation is refused until reconciled.",
)
def reconcile(container, bAssumeYes, sBreakGlassSha256,
              sForceAbandonOperationId):
    """Prove a quarantined container's past operations settled."""
    sys.exit(fiRunReconcileCommand(
        container, bAssumeYes, sBreakGlassSha256,
        sForceAbandonOperationId,
    ))


def fiRunReconcileCommand(
    sContainerName, bAssumeYes, sBreakGlassSha256="",
    sForceAbandonOperationId="",
):
    """The reconcile entry: discovery picks the crash or live-hub path."""
    if not fbIsValidProjectName(sContainerName):
        click.echo(
            f"Error: invalid container name {sContainerName!r}.", err=True,
        )
        return 2
    dictHolder = fdictReadLockHolder(sContainerName)
    if dictHolder:
        return _fiRouteToLiveHub(
            sContainerName, dictHolder, bAssumeYes, sBreakGlassSha256,
            sForceAbandonOperationId,
        )
    if sForceAbandonOperationId:
        click.echo(
            "Error: --force-abandon targets a wedged worker on a LIVE "
            "hub, and no live vaibify process holds this container. "
            "Run a plain 'vaibify reconcile' instead.", err=True,
        )
        return 2
    if sBreakGlassSha256:
        return fiRunCrashTimeBreakGlass(sContainerName, sBreakGlassSha256)
    return fiRunCrashTimeReconcile(sContainerName, bAssumeYes)
