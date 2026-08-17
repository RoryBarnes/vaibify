"""``vaibify reconcile`` — the proving exit from a journal quarantine.

The destructive exits — break-glass, force-abandon,
abandon-host-journal — are trust judgements about possibly-corrupt
state, so they ride ONLY this host lane (design §8) and never the
browser or agent lanes. The non-destructive PROVE is additionally
reachable from the dashboard's quarantine modal (2026-08-17), which
runs the same shared transaction and can therefore never clear what
this command would refuse; the agent lane stays refused for all of it.
Discovery decides the path: when a LIVE hub
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
    """Explain a non-valid journal and name the destructive exit.

    WHICH exit depends on the mode, and the difference is not cosmetic.
    A container's break-glass stops the container first, so clearing
    the marker afterwards rests on something proven. A host project has
    no container to stop and therefore no proof to offer, so its exit
    is named for what it actually does. Printing the container recipe
    to a host researcher would send them to a command that refuses.
    """
    from vaibify.config.operationJournal import fsComputeJournalFileSha256
    from vaibify.config.registryManager import fbIsHostProject
    click.echo(f"Error: {error}", err=True)
    if getattr(error, "sReadState", "") == "requiresUpgrade":
        click.echo(
            "Upgrade vaibify to a build that understands this journal; "
            "it is never cleared blind.", err=True,
        )
        return 1
    sMarkerSha256 = fsComputeJournalFileSha256(sContainerName)
    if fbIsHostProject(sContainerName):
        click.echo(
            "This project runs on this machine, so there is no container "
            "to stop and nothing vaibify can prove about what the marker "
            "describes. If you have inspected it and are willing to "
            "ASSERT that nothing it describes survives — an assertion "
            "that will be recorded against your account, this project "
            "and these exact marker bytes — re-run with:\n"
            f"  vaibify reconcile {sContainerName} "
            f"--abandon-host-journal {sMarkerSha256}",
            err=True,
        )
        return 1
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


def fiTerminateRecordedHostProcesses(sContainerName):
    """Signal a host project's journaled groups; return the exit code.

    The lever a quarantined host project has and a container does not:
    there is no container to stop, but every process vaibify started
    was journaled with a recycle-proof identity, so the ones still
    wearing that identity can be signalled.

    Crash-time only, and that is a design position rather than a gap. A
    live hub holding this project's flock OWNS these records; killing
    them behind its back would leave it streaming from pipes whose
    writers vanished, and the lever for a wedged worker on a live hub
    is ``--force-abandon``.

    What it cannot do is stated where the researcher reads it: an
    unprovable identity is reported, never signalled, and a process
    that detached into its own session is outside all of this.
    """
    from vaibify.config.registryManager import fbIsHostProject
    from vaibify.host import hostCancellation
    if not fbIsHostProject(sContainerName):
        click.echo(
            "Error: --terminate-recorded is for host projects. A "
            f"containerized project like '{sContainerName}' is settled "
            "by stopping its container, which reconciliation already "
            "does.", err=True,
        )
        return 2
    try:
        dictCancelled = hostCancellation.fdictCancelJournaledHostRun(
            sContainerName,
        )
    except Exception as error:
        click.echo(f"Error: could not read the records: {error}", err=True)
        return 1
    _fnReportTerminationOutcome(dictCancelled)
    return 0


def _fnReportTerminationOutcome(dictCancelled):
    """Print what was signalled, what had ended, and what was refused."""
    click.echo(
        f"Terminated {dictCancelled['iGroupsTerminated']} recorded "
        f"process group(s); "
        f"{len(dictCancelled['listAlreadyExited'])} had already ended."
    )
    for dictRefused in dictCancelled["listRefused"]:
        click.echo(
            f"  NOT signalled: {dictRefused['sOperationLabel']} "
            f"(pid {dictRefused['iHolderPid']}, group "
            f"{dictRefused['iHolderProcessGroup']}) — "
            f"{dictRefused['sReason']}", err=True,
        )
    click.echo(
        "Vaibify cannot detect processes that detached into a new "
        "session; what it proves is that every process it recorded has "
        "exited."
    )


def fiRunCrashTimeBreakGlass(sContainerName, sMarkerSha256):
    """Run the crash-time break-glass; return the process exit code."""
    try:
        reconciliation.fdictExecuteBreakGlass(
            sContainerName, sMarkerSha256,
            fnStopContainerByName=_fbStopContainerByName,
        )
    except reconciliation.ReconciliationRefusedError as error:
        click.echo(f"Break-glass refused: {error}", err=True)
        return 1
    click.echo(
        f"Break-glass cleared the malformed marker for container "
        f"'{sContainerName}'."
    )
    return 0


def fbConfirmAbandoningTheProof(sContainerName, bAssumeYes):
    """Ask, in the words of what is being given up, before abandoning.

    The hash argument already makes this deliberate; the prompt exists
    because "clear the quarantine" and "declare that a proof cannot be
    made and that you are proceeding anyway" are different acts, and
    only one of them is what is about to happen.
    """
    if bAssumeYes:
        return True
    click.echo(
        f"Host project '{sContainerName}' has a journal marker vaibify "
        "cannot read, and no container to stop. Abandoning it does not "
        "prove that the work it describes has ended — it records that "
        "you asserted so.\n"
        "Anything the marker describes may still be running on this "
        "machine right now."
    )
    return click.confirm(
        "Abandon the proof for this project and clear the marker?"
    )


def fiRunCrashTimeAbandonHostJournal(sContainerName, sMarkerSha256):
    """Abandon a host project's marker; return the process exit code."""
    try:
        reconciliation.fdictAbandonHostJournal(
            sContainerName, sMarkerSha256,
        )
    except reconciliation.ReconciliationRefusedError as error:
        click.echo(f"Abandonment refused: {error}", err=True)
        return 1
    click.echo(
        f"Abandoned the journal marker for host project "
        f"'{sContainerName}'. Nothing was proven; the assertion is "
        "recorded beside the journal."
    )
    return 0


def _fbStopContainerByName(sContainerName):
    """Stop the possibly-relevant container and PROVE it settled.

    Returns True only when the container was stopped or the daemon
    positively answered that it does not exist; the break-glass refuses
    on anything else rather than deleting a marker whose writer may
    still be running.
    """
    from vaibify.docker.containerManager import fbStopContainerProvenSettled
    return fbStopContainerProvenSettled(sContainerName)


def _fiRouteToLiveHub(
    sContainerName, dictHolder, bAssumeYes, dictDestructive,
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
        sContainerName, bAssumeYes, dictDestructive,
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


def _fdictBuildHubRequest(sContainerName, bAssumeYes, dictDestructive):
    """Build the socket request, showing and confirming the records."""
    if dictDestructive["sForceAbandonOperationId"]:
        return {
            "sOperation": "force-abandon",
            "sContainerName": sContainerName,
            "sExpectedOperationId": (
                dictDestructive["sForceAbandonOperationId"]
            ),
        }
    if dictDestructive["sBreakGlassSha256"]:
        return {
            "sOperation": "break-glass",
            "sContainerName": sContainerName,
            "sMarkerSha256": dictDestructive["sBreakGlassSha256"],
        }
    if dictDestructive["sAbandonHostJournalSha256"]:
        if not fbConfirmAbandoningTheProof(sContainerName, bAssumeYes):
            click.echo("Abandonment cancelled; the quarantine stands.")
            return None
        return {
            "sOperation": "abandon-host-journal",
            "sContainerName": sContainerName,
            "sMarkerSha256": (
                dictDestructive["sAbandonHostJournalSha256"]
            ),
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
@click.option(
    "--terminate-recorded", "bTerminateRecorded", is_flag=True,
    help="For a HOST project only: signal the process groups vaibify "
         "journaled for it, then retry the proof. A record whose "
         "identity cannot be proven is reported, never signalled.",
)
@click.option(
    "--abandon-host-journal", "sAbandonHostJournalSha256", default="",
    help="For a HOST project only: give up on proving a MALFORMED "
         "marker whose raw bytes hash to this sha256, recording the "
         "abandonment beside the journal. Proves nothing.",
)
def fnReconcileCommand(container, bAssumeYes, sBreakGlassSha256,
                       sForceAbandonOperationId, bTerminateRecorded,
                       sAbandonHostJournalSha256):
    """Prove a quarantined container's past operations settled."""
    sys.exit(fiRunReconcileCommand(
        container, bAssumeYes, sBreakGlassSha256,
        sForceAbandonOperationId, sAbandonHostJournalSha256,
        bTerminateRecorded,
    ))


def fiRunReconcileCommand(
    sContainerName, bAssumeYes, sBreakGlassSha256="",
    sForceAbandonOperationId="", sAbandonHostJournalSha256="",
    bTerminateRecorded=False,
):
    """The reconcile entry: discovery picks the crash or live-hub path."""
    if not fbIsValidProjectName(sContainerName):
        click.echo(
            f"Error: invalid container name {sContainerName!r}.", err=True,
        )
        return 2
    dictDestructive = {
        "sBreakGlassSha256": sBreakGlassSha256,
        "sForceAbandonOperationId": sForceAbandonOperationId,
        "sAbandonHostJournalSha256": sAbandonHostJournalSha256,
    }
    dictHolder = fdictReadLockHolder(sContainerName)
    if dictHolder:
        if bTerminateRecorded:
            click.echo(
                "Error: --terminate-recorded acts on records a live hub "
                f"no longer owns, and pid={dictHolder.get('iPid')} still "
                f"holds '{sContainerName}'. Stop that hub first, or use "
                "--force-abandon for a wedged worker inside it.", err=True,
            )
            return 2
        return _fiRouteToLiveHub(
            sContainerName, dictHolder, bAssumeYes, dictDestructive,
        )
    if bTerminateRecorded:
        iOutcome = fiTerminateRecordedHostProcesses(sContainerName)
        if iOutcome != 0:
            return iOutcome
        return fiRunCrashTimeReconcile(sContainerName, bAssumeYes)
    if sForceAbandonOperationId:
        click.echo(
            "Error: --force-abandon targets a wedged worker on a LIVE "
            "hub, and no live vaibify process holds this container. "
            "Run a plain 'vaibify reconcile' instead.", err=True,
        )
        return 2
    if sBreakGlassSha256:
        return fiRunCrashTimeBreakGlass(sContainerName, sBreakGlassSha256)
    if sAbandonHostJournalSha256:
        if not fbConfirmAbandoningTheProof(sContainerName, bAssumeYes):
            click.echo("Abandonment cancelled; the quarantine stands.")
            return 1
        return fiRunCrashTimeAbandonHostJournal(
            sContainerName, sAbandonHostJournalSha256,
        )
    return fiRunCrashTimeReconcile(sContainerName, bAssumeYes)
