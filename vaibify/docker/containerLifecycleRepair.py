"""The one transaction any container repair runs inside.

A container restart is not a narrow operation, and treating it as one
is how a "quick fix" costs a researcher their session. Restarting
re-runs the entrypoint, which reconfigures git, migrates workspace
ownership, syncs repositories and updates agents -- and it kills every
live shell, every running agent turn and every pipeline step in the
container. A repair that does that without saying so first is worse
than the fault it fixes.

So every repair here obeys the same five rules, whichever lane it
arrives on:

1. **A live hub owning the container is the authority**, not this
   process. The CLI routes the request to that hub over its
   peer-authenticated host control socket rather than reaching around
   it, exactly as ``vaibify reconcile`` does.
2. **Anything live refuses the repair, by name.** A run, a terminal, a
   council turn, a pipeline action -- the researcher is told WHAT is
   busy, because "try again later" is unactionable.
3. **The established lock is acquired**, never a new one invented for
   this feature: the container flock on the direct lane, the hub's own
   container-mutation lock on the hub lane.
4. **The operation is journaled** before the effect exists, so a crash
   mid-repair reconciles like every other interrupted operation.
5. **An image IDENTITY is pinned, never a tag.** A recreation resolves
   the running container's image ID and refuses if it cannot;
   ``<project>:latest`` may have moved since the container was built,
   and a repair that silently swapped the environment would invalidate
   the digest an envelope pins.
"""

from vaibify.config import containerLock, operationJournal
from vaibify.docker import containerManager


__all__ = [
    "RepairRefusedError", "fsDescribeRestartConsequences",
    "fsResolveRunningImageIdentity", "fdictRestartUnderJournal",
    "fdictRecreateUnderJournal", "flistNameLiveWork",
]


_S_JOURNAL_KIND = "helper"


class RepairRefusedError(RuntimeError):
    """A repair refused before changing anything."""


def fsDescribeRestartConsequences(sContainerName):
    """Return what a restart of this container will actually do.

    Printed BEFORE the repair, on every lane. The list is not
    defensive padding: each item has surprised somebody. The
    entrypoint's work is invisible from outside, and the killed shells
    include the terminal the researcher may be reading this in.
    """
    return (
        f"Restarting '{sContainerName}' will:\n"
        "  - re-run the container entrypoint, which reconfigures git, "
        "migrates workspace ownership, syncs repositories and updates "
        "installed agents;\n"
        "  - kill every shell, agent and pipeline step running inside "
        "the container, including any terminal you have open;\n"
        "  - keep the workspace volume and everything in it."
    )


def fsResolveRunningImageIdentity(sContainerName):
    """Return the container's image ID, or '' when it cannot be resolved.

    The ID, never the tag. Returning ``""`` is a refusal signal the
    caller must honour: recreating from ``<project>:latest`` when the
    identity is unknown is the substitution this rule exists to
    prevent.
    """
    jsonInspect = containerManager.fjsonInspectContainer(sContainerName)
    return str(jsonInspect.get("Image") or "")


def flistNameLiveWork(sContainerName, connectionDocker=None):
    """Return a description of everything live in this container.

    Empty means nothing is live. The journal is the authority: a BUSY
    resolution means a live holder, and the display records say which
    operations they are.
    """
    dictResolution = operationJournal.fdictResolveContainerJournal(
        sContainerName, connectionDocker, bPersistResolution=False,
    )
    if dictResolution["sResolution"] == operationJournal.S_RESOLUTION_BUSY:
        return [
            f"operation {sOperationId} is still in flight"
            for sOperationId in dictResolution["listBusyOperationIds"]
        ] or ["an operation is still in flight"]
    if dictResolution["sResolution"] == (
        operationJournal.S_RESOLUTION_QUARANTINED
    ):
        raise RepairRefusedError(
            f"container '{sContainerName}' is quarantined: "
            + str(dictResolution.get("sQuarantineReason") or "")
            + f". Run `vaibify reconcile {sContainerName}` first."
        )
    return []


def _fsPrepareJournalRecord(sContainerName, sOperation):
    """Journal the repair before its effect exists; return the record id."""
    import os
    sOperationId = operationJournal.fsPrepareOperation(
        sContainerName, _S_JOURNAL_KIND, f"repair-{sOperation}",
    )
    operationJournal.fnPromoteOperationToInFlight(
        sContainerName, sOperationId,
        {
            "iHolderPid": os.getpid(),
            "iHolderProcessGroup": os.getpgrp(),
        },
    )
    return sOperationId


def _fnRefuseWhileLiveWork(sContainerName, connectionDocker):
    """Raise naming what is busy, or return having proven nothing is."""
    listLive = flistNameLiveWork(sContainerName, connectionDocker)
    if not listLive:
        return
    raise RepairRefusedError(
        f"container '{sContainerName}' is busy and a repair would "
        "destroy work in progress: " + "; ".join(listLive)
    )


def fdictRestartUnderJournal(
    sContainerName, connectionDocker=None, fnAnnounce=None,
):
    """Restart one container, journaled, refusing over live work.

    The caller supplies the exclusion -- the container flock on the
    direct lane, the hub's mutation lock on the hub lane -- because
    the two lanes hold DIFFERENT locks and inventing a third here
    would arbitrate with neither.
    """
    _fnRefuseWhileLiveWork(sContainerName, connectionDocker)
    if fnAnnounce is not None:
        fnAnnounce(fsDescribeRestartConsequences(sContainerName))
    sOperationId = _fsPrepareJournalRecord(sContainerName, "restart")
    try:
        _fdictMutateThroughTheGateway(None, sContainerName, "restart")
    finally:
        operationJournal.fnSettleOperation(sContainerName, sOperationId)
    return {"bRepaired": True, "sOperation": "restart"}


def fdictRecreateUnderJournal(
    config, sContainerName, connectionDocker=None, fnAnnounce=None,
):
    """Recreate one container from its PINNED image, journaled.

    The image identity is resolved first and the whole repair refuses
    without it, before anything is stopped. Resolving it afterwards
    would mean discovering the refusal with the container already
    gone.
    """
    _fnRefuseWhileLiveWork(sContainerName, connectionDocker)
    sImageIdentity = fsResolveRunningImageIdentity(sContainerName)
    if not sImageIdentity:
        raise RepairRefusedError(
            f"the image '{sContainerName}' is running could not be "
            "resolved to an ID, so it cannot be recreated from the "
            "same image. Recreating from the project's `latest` tag "
            "could silently change the environment."
        )
    if fnAnnounce is not None:
        fnAnnounce(
            fsDescribeRestartConsequences(sContainerName)
            + "\n  - recreate the container from image "
            + sImageIdentity + ", discarding its writable layer "
            "(everything outside the workspace volume)."
        )
    sOperationId = _fsPrepareJournalRecord(sContainerName, "recreate")
    try:
        dictOutcome = _fdictMutateThroughTheGateway(
            config, sContainerName, "recreate", sImageIdentity,
        )
    finally:
        operationJournal.fnSettleOperation(sContainerName, sOperationId)
    return {
        "bRepaired": True, "sOperation": "recreate",
        "sImageIdentity": sImageIdentity,
        "sContainerId": dictOutcome["sContainerId"],
    }


def _fdictMutateThroughTheGateway(
    config, sContainerName, sOperation, sImageIdentity="",
):
    """The ONE place this module changes a container.

    Both journaled operations funnel through here so the ledger
    records a single mutation-capable call site outside the lifecycle
    gateway rather than one per operation: `vaibify repair` adds
    exactly one new way to change a container from outside that
    boundary, and the record says so on one line.

    A daemon-level failure becomes a refusal, because from the
    researcher's chair a repair that could not run and a repair that
    was declined are the same thing -- something did not happen, and
    the message has to say what.
    """
    try:
        return containerManager.fdictRepairContainerLifecycle(
            config, sContainerName, sOperation, sImageIdentity,
        )
    except (RuntimeError, ValueError) as errorRepair:
        raise RepairRefusedError(str(errorRepair))


def fdictRepairDirectlyUnderFlock(
    config, sContainerName, sOperation, fnAnnounce=None,
):
    """Run a repair on the lane where no live hub holds the container.

    Takes the established container flock -- the same one a hub takes
    when it opens a container -- so a hub starting mid-repair cannot
    interleave with it. A held flock means a live vaibify process owns
    the container and the repair belongs on that process's lane, so
    this refuses and names it.
    """
    try:
        fileHandleLock = containerLock.ffileAcquireContainerLock(
            sContainerName, 0,
        )
    except containerLock.ContainerLockedError as errorLocked:
        raise RepairRefusedError(
            f"container '{sContainerName}' is held by vaibify "
            f"pid={errorLocked.iHolderPid} on port="
            f"{errorLocked.iHolderPort}. Repair it from that session, "
            "or close it first."
        )
    except Exception as errorJournal:
        # The acquire examines the operation journal atomically with
        # the fresh flock and refuses a quarantined or busy container.
        # That refusal is this repair's refusal too, carried through
        # rather than re-derived.
        raise RepairRefusedError(str(errorJournal))
    try:
        if sOperation == "recreate":
            return fdictRecreateUnderJournal(
                config, sContainerName, None, fnAnnounce,
            )
        return fdictRestartUnderJournal(sContainerName, None, fnAnnounce)
    finally:
        containerLock.fnReleaseContainerLock(fileHandleLock)
