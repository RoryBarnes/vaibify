"""The single Docker-SDK authority for every disposable container.

A *disposable* container is one vaibify creates for a single bounded
job, never hands to a researcher, and destroys with proof when the job
ends (see ``disposableSpecification`` for what a disposable is and why
its posture is fixed). Everything this machinery does to a daemon --
creating and starting a container, copying an archive in, executing a
bounded command, killing, probing absence, destroying with proof, and
discovering labeled survivors -- originates here. The pure half stays
in ``disposableSpecification``: it supplies the values, this module
supplies the daemon.

Three properties this module enforces, each of which the code it was
extracted from learned the hard way:

- **Opaque reservation handles.** Every created container is keyed by a
  server-minted ``secrets.token_hex(16)`` handle, and every subsequent
  per-container operation accepts ONLY a handle -- never a raw
  container id -- so a caller holding an arbitrary Docker id (the
  researcher's own project container, for instance) cannot drive a
  disposable operation at it.
- **Reserve-before-create, settle-on-every-exit.** A ledger reservation
  is written BEFORE the SDK create, so a crash between the two leaves a
  visible pending reservation rather than an untracked container. Any
  exception after the reserve settles the reservation honestly: pending
  with no container is dropped; created-but-unstartable is destroyed
  with proof or left visibly quarantined.
- **Identity-verified destruction.** A handle-keyed destroy inspects
  the target FIRST and refuses -- destroying nothing -- unless the
  container's ``vaibify-disposable`` label equals the handle's
  reservation id. That is what makes it impossible for this lane to
  touch the researcher's project container. An indeterminate daemon
  answer quarantines: the reservation stays visible rather than being
  reported as a clean completion.

**The ledger here is deliberately thin.** It records reservations and
their outcomes and nothing else -- no admission quotas, no per-provider
accounting, no idle-watchdog veto. Those are policies of whoever is
spending the resource, not of the daemon lane, and a caller that needs
them wraps this one.

Extracted from the Agent Council's Docker gateway so the shadow-rerun
lane and the council share one container lifecycle rather than two that
drift.
"""

import posixpath
import secrets
import socket
import time

from vaibify.docker.dockerConnection import (
    _fmoduleGetDocker,
    _fnEnsureDockerHost,
)
from vaibify.docker import disposableSpecification


__all__ = [
    "DisposableContainerError",
    "I_DAEMON_TIMEOUT_SECONDS",
    "S_RESERVATION_PENDING",
    "S_RESERVATION_LIVE",
    "S_RESERVATION_QUARANTINED",
    "fdockerCreateDisposableClient",
    "fdictCreateDisposableGateway",
    "fdictReserveAndCreateContainer",
    "fnCopyArchiveIntoContainer",
    "fdictExecuteBoundedCommand",
    "fdictDestroyAndSettle",
    "flistDescribeQuarantinedReservations",
    "fdictProbeContainerAbsence",
    "fdictDestroyContainerAndProveAbsence",
    "flistDiscoverLabeledContainers",
    "fdictSweepLabeledSurvivors",
]


# Short on purpose: teardown must DETECT an unresponsive daemon and
# quarantine, not hang. Streaming reads are not governed by this
# timeout -- the bounded pump polls its raw socket.
I_DAEMON_TIMEOUT_SECONDS = 60

F_ARCHIVE_COPY_BUDGET_SECONDS = 600.0

S_RESERVATION_PENDING = "pending"
S_RESERVATION_LIVE = "live"
S_RESERVATION_QUARANTINED = "quarantined"


class DisposableContainerError(Exception):
    """An operation was refused before any Docker resource changed.

    Derives from ``Exception``, never ``OSError``: a refusal swallowed
    by an ``except OSError`` is how a control decision silently
    downgrades into an I/O hiccup.
    """


def fdockerCreateDisposableClient(iTimeoutSeconds=None):
    """Create a Docker client for disposable-container operations.

    Follows the ``DockerConnection`` acquisition pattern
    (context-derived ``DOCKER_HOST``, lazy docker-py import) with a
    much shorter per-call timeout, for the reason recorded on
    :data:`I_DAEMON_TIMEOUT_SECONDS`.
    """
    _fnEnsureDockerHost()
    if iTimeoutSeconds is None:
        iTimeoutSeconds = I_DAEMON_TIMEOUT_SECONDS
    return _fmoduleGetDocker().from_env(timeout=iTimeoutSeconds)


def fdictCreateDisposableGateway(dockerDisposable, sResourceName=""):
    """Create the gateway state: one client, its ledger, its handles.

    ``dockerDisposable`` may be None for a ledger-only view (a caller
    reading quarantined reservations needs no daemon); every
    SDK-touching operation requires a real client and fails loudly on a
    None one.

    ``sResourceName`` is the project container whose work this gateway
    serves. It lives HERE rather than on each create call because a
    gateway already serves exactly one project, so there is one place
    it can be forgotten instead of one per call site.
    """
    return {
        "dockerDisposable": dockerDisposable,
        "sResourceName": sResourceName,
        "dictHandlesById": {},
        "dictReservationsById": {},
    }


def _fdictResolveHandle(dictGateway, sHandle):
    """Return the handle record, or refuse an identifier nobody minted.

    The refusal is the boundary: a raw container id, a guessed token, or
    a handle from another gateway instance all land here, so no gateway
    operation can ever be aimed at a container the gateway did not
    create.
    """
    dictHandle = dictGateway["dictHandlesById"].get(sHandle)
    if dictHandle is None:
        raise DisposableContainerError(
            "unknown disposable gateway handle; operations accept only "
            "a handle minted by fdictReserveAndCreateContainer, never a "
            "raw container identifier"
        )
    return dictHandle


# ----- reserve-before-create lifecycle ----------------------------------


def fdictReserveAndCreateContainer(
    dictGateway, sRole, sImageReference,
    dictLimits=None, sNetworkName=None, bReadOnlyRootFilesystem=False,
):
    """Reserve, then create and start one disposable container.

    The write-ahead order is the contract: the ledger reservation is
    recorded BEFORE the SDK create. On success the reservation goes
    live and an opaque handle is minted; on ANY exception after the
    reserve the reservation is settled honestly before the exception
    propagates. Returns the handle plus the container's name and role.
    """
    sReservationId = f"disposable-{sRole}-{secrets.token_hex(6)}"
    iEpoch = _fiReserve(dictGateway, sReservationId, sRole)
    containerDisposable = None
    try:
        dictSpecification = (
            disposableSpecification.fdictComposeCreateSpecification(
                sImageReference, sReservationId, sRole, dictLimits,
                sNetworkName, dictGateway.get("sResourceName", ""),
                bReadOnlyRootFilesystem))
        containerDisposable = (
            dictGateway["dockerDisposable"].containers.create(
                sImageReference,
                **dictSpecification["dictCreateKeywords"]))
        containerDisposable.start()
    except Exception:
        _fnSettleFailedCreation(
            dictGateway, sReservationId, iEpoch, containerDisposable)
        raise
    sHandle = secrets.token_hex(16)
    dictGateway["dictReservationsById"][sReservationId].update({
        "sStatus": S_RESERVATION_LIVE,
        "sContainerId": containerDisposable.id,
    })
    dictGateway["dictHandlesById"][sHandle] = {
        "sReservationId": sReservationId,
        "sContainerId": containerDisposable.id,
        "sContainerName": dictSpecification["sContainerName"],
        "sRole": sRole,
        "bReadOnlyRootFilesystem": bool(bReadOnlyRootFilesystem),
        "iEpoch": iEpoch,
    }
    return {"bCreated": True, "sHandle": sHandle,
            "sReservationId": sReservationId,
            "sContainerName": dictSpecification["sContainerName"],
            "sRole": sRole}


def _fiReserve(dictGateway, sReservationId, sRole):
    """Write the pending reservation ahead of the create; return its epoch."""
    iEpoch = len(dictGateway["dictReservationsById"]) + 1
    dictGateway["dictReservationsById"][sReservationId] = {
        "sReservationId": sReservationId,
        "sRole": sRole,
        "sStatus": S_RESERVATION_PENDING,
        "sContainerId": "",
        "sReason": "",
        "iEpoch": iEpoch,
    }
    return iEpoch


def _fnSettleReservation(dictGateway, sReservationId, sOutcome, iEpoch,
                         sReason=""):
    """Settle one reservation, refusing a settle that names a stale epoch.

    Every reservation carries an epoch so a late callback from a
    destroyed container can never erase a successor. A proven
    destruction drops the record; anything else leaves it visibly
    quarantined with the reason attached.
    """
    dictReservation = dictGateway["dictReservationsById"].get(sReservationId)
    if dictReservation is None or dictReservation["iEpoch"] != iEpoch:
        return
    if sOutcome == disposableSpecification.S_OUTCOME_DESTROYED:
        del dictGateway["dictReservationsById"][sReservationId]
        return
    dictReservation["sStatus"] = S_RESERVATION_QUARANTINED
    dictReservation["sReason"] = sReason


def _fnSettleFailedCreation(dictGateway, sReservationId, iEpoch,
                            containerDisposable):
    """Settle the reservation of a creation that raised.

    Pending with no container is dropped -- there is nothing to prove
    gone. A created-but-unstartable container is destroyed with proof;
    an unproven destruction leaves the reservation visibly quarantined,
    exactly as a live container's would.
    """
    if containerDisposable is None:
        _fnSettleReservation(
            dictGateway, sReservationId,
            disposableSpecification.S_OUTCOME_DESTROYED, iEpoch)
        return
    dictGateway["dictReservationsById"][sReservationId]["sContainerId"] = (
        containerDisposable.id)
    dictDestroyed = fdictDestroyContainerAndProveAbsence(
        dictGateway["dockerDisposable"], containerDisposable.id)
    _fnSettleReservation(
        dictGateway, sReservationId, dictDestroyed["sOutcome"], iEpoch,
        dictDestroyed["sReason"])


# ----- handle-keyed container operations --------------------------------


def _ftStartExecStream(dockerDisposable, sContainerId, listCommand,
                       bStdin, sWorkingDirectory=None):
    """Create an exec as the unprivileged user; return (execId, rawSocket)."""
    dictExecCreated = dockerDisposable.api.exec_create(
        sContainerId, listCommand,
        stdin=bStdin, stdout=True, stderr=True,
        user=disposableSpecification.S_DISPOSABLE_CONTAINER_USER,
        workdir=sWorkingDirectory,
    )
    socketExec = dockerDisposable.api.exec_start(
        dictExecCreated["Id"], socket=True,
    )
    socketRaw = getattr(socketExec, "_sock", socketExec)
    socketRaw.settimeout(disposableSpecification.F_STREAM_POLL_SECONDS)
    return (dictExecCreated["Id"], socketRaw)


def fnCopyArchiveIntoContainer(
    dictGateway, sHandle, baArchiveTar,
    sDestinationDirectory="/", sPathPrefix="",
):
    """Copy a tarball into a handle's container, validated and stamped.

    Every member is validated against extraction-root escape and
    stamped to the unprivileged container user (both pure, in the
    specification module), then delivered by whichever mechanism the
    container's own posture admits.

    **Which mechanism, and why it is not a preference.** The Docker
    archive endpoint (``put_archive`` / ``docker cp``) is a daemon API
    write that executes NO command in the container -- the daemon
    itself unpacks -- so nothing caller-supplied can become program
    text and the container gains no process. It is therefore the
    default. It is also refused wholesale by the daemon against a
    read-only-rootfs container ("container rootfs is marked
    read-only"), tmpfs target included, so a container created with
    that posture is served instead by streaming the tarball through an
    exec's stdin and untarring it INSIDE the container as the
    unprivileged user -- which makes a root-owned copy impossible by
    construction, because a non-root ``tar`` cannot chown. The choice
    reads the posture recorded on the handle at create time, never a
    caller's argument.
    """
    dictHandle = _fdictResolveHandle(dictGateway, sHandle)
    if not posixpath.isabs(sDestinationDirectory):
        raise ValueError(
            "Archive destination must be an absolute container path, "
            f"got {sDestinationDirectory!r}."
        )
    bufferRepacked = disposableSpecification.fbufferRepackArchiveStamped(
        baArchiveTar, sPathPrefix)
    if dictHandle["bReadOnlyRootFilesystem"]:
        _fnStreamArchiveThroughExec(
            dictGateway, dictHandle, bufferRepacked.getvalue(),
            sDestinationDirectory)
        return
    dictGateway["dockerDisposable"].api.put_archive(
        dictHandle["sContainerId"], sDestinationDirectory,
        bufferRepacked.getvalue(),
    )


def _fnStreamArchiveThroughExec(dictGateway, dictHandle, baRepacked,
                                sDestinationDirectory):
    """Stream a repacked tarball through an exec's stdin and untar it."""
    dockerDisposable = dictGateway["dockerDisposable"]
    fDeadlineMonotonic = (
        time.monotonic() + F_ARCHIVE_COPY_BUDGET_SECONDS)
    sExecId, socketRaw = _ftStartExecStream(
        dockerDisposable, dictHandle["sContainerId"],
        ["tar", "-xf", "-", "-C", sDestinationDirectory],
        bStdin=True,
    )
    try:
        disposableSpecification.fnSendAllBounded(
            socketRaw, baRepacked, fDeadlineMonotonic)
        socketRaw.shutdown(socket.SHUT_WR)
        dictPumped = disposableSpecification.fdictPumpBoundedExecStream(
            socketRaw, disposableSpecification.I_DEFAULT_OUTPUT_CAP_BYTES,
            fDeadlineMonotonic)
    finally:
        socketRaw.close()
    if dictPumped["bDeadlineExceeded"]:
        raise RuntimeError(
            "Archive copy exceeded its "
            f"{F_ARCHIVE_COPY_BUDGET_SECONDS:.0f}s budget."
        )
    dictExecInspected = dockerDisposable.api.exec_inspect(sExecId)
    iExitCode = dictExecInspected["ExitCode"]
    if iExitCode != 0:
        sOutputTail = dictPumped["baCaptured"][-2048:].decode(
            "utf-8", errors="replace")
        raise RuntimeError(
            "Archive extraction failed inside the container "
            f"(exit {iExitCode}): {sOutputTail}"
        )


def _fnKillContainerQuietly(dockerDisposable, sContainerId):
    """Kill the container, tolerating one already stopped or gone."""
    try:
        dockerDisposable.api.kill(sContainerId)
    except Exception:
        pass


def fdictExecuteBoundedCommand(
    dictGateway, sHandle, listCommand,
    iOutputByteCap=None, fWallClockSeconds=None, sWorkingDirectory=None,
    baStdinPayload=None,
):
    """Execute one bounded command in a handle's container.

    Output is captured under a byte cap and the whole command under a
    wall-clock budget, both enforced host-side. Breaching either bound
    kills the CONTAINER, not just the exec -- the container is
    disposable and a job that broke its budget has ended; the caller
    settles it with :func:`fdictDestroyAndSettle`. ``iExitCode`` is
    ``None`` when no exit code could be established (a killed command),
    never a fabricated zero. ``bOomKilled`` is read from the container
    BEFORE anything destroys it, because an exit code of 137 is SIGKILL
    and says nothing about who sent it: this module kills on a breached
    bound and the kernel kills on memory pressure, and without that
    field the two are indistinguishable.
    """
    dictHandle = _fdictResolveHandle(dictGateway, sHandle)
    dockerDisposable = dictGateway["dockerDisposable"]
    if iOutputByteCap is None:
        iOutputByteCap = disposableSpecification.I_DEFAULT_OUTPUT_CAP_BYTES
    if fWallClockSeconds is None:
        fWallClockSeconds = (
            disposableSpecification.F_DEFAULT_WALL_CLOCK_SECONDS)
    fStartedMonotonic = time.monotonic()
    fDeadlineMonotonic = fStartedMonotonic + fWallClockSeconds
    sExecId, socketRaw = _ftStartExecStream(
        dockerDisposable, dictHandle["sContainerId"], listCommand,
        bStdin=baStdinPayload is not None,
        sWorkingDirectory=sWorkingDirectory,
    )
    try:
        if baStdinPayload is not None:
            disposableSpecification.fnSendAllBounded(
                socketRaw, baStdinPayload, fDeadlineMonotonic)
            socketRaw.shutdown(socket.SHUT_WR)
        dictPumped = disposableSpecification.fdictPumpBoundedExecStream(
            socketRaw, iOutputByteCap, fDeadlineMonotonic)
    finally:
        socketRaw.close()
    if dictPumped["bOutputCapExceeded"] or dictPumped["bDeadlineExceeded"]:
        _fnKillContainerQuietly(
            dockerDisposable, dictHandle["sContainerId"])
    return _fdictDescribeCommandOutcome(
        dockerDisposable, dictHandle, sExecId, dictPumped,
        fStartedMonotonic)


def _fdictDescribeCommandOutcome(dockerDisposable, dictHandle, sExecId,
                                 dictPumped, fStartedMonotonic):
    """Assemble the bounded command's outcome from the daemon's answers.

    Each inspect result is bound before it is read. A ``.get()`` chained
    onto a docker-py call counts as its own unreadable site in the
    mutation inventory, because the scan sees a call whose chain passes
    through ``.api`` and cannot trace the client root -- so a fluent
    one-liner spends blind-spot budget per link. Subscripting a bound
    dict is not a call at all, and a missing key raises into the same
    ``except`` the chain's ``None`` already fell through to.
    """
    iExitCode = None
    try:
        dictExecInspected = dockerDisposable.api.exec_inspect(sExecId)
        iExitCode = dictExecInspected["ExitCode"]
    except Exception:
        pass
    bOomKilled = False
    try:
        dictInspected = dockerDisposable.api.inspect_container(
            dictHandle["sContainerId"])
        bOomKilled = bool(dictInspected["State"]["OOMKilled"])
    except Exception:
        pass
    return {
        "iExitCode": iExitCode,
        "sOutput": dictPumped["baCaptured"].decode(
            "utf-8", errors="replace"),
        "bOutputCapExceeded": dictPumped["bOutputCapExceeded"],
        "bWallClockExceeded": dictPumped["bDeadlineExceeded"],
        "iOutputBytes": len(dictPumped["baCaptured"]),
        "bOomKilled": bOomKilled,
        "fElapsedSeconds": time.monotonic() - fStartedMonotonic,
    }


def fdictDestroyAndSettle(dictGateway, sHandle):
    """Destroy a handle's container, identity-verified, and settle it.

    The inspect comes FIRST: destruction proceeds only when the target
    container's ``vaibify-disposable`` label equals the handle's
    reservation id. A missing or mismatched label refuses -- destroying
    nothing -- because a daemon answer that does not carry this lane's
    own stamp means the id no longer names the container the gateway
    created, and the researcher's project container is the canonical
    thing that protects. ``NotFound`` at inspect settles as destroyed
    (already gone); any other inspect fault quarantines with NO removal
    attempt.
    """
    dictHandle = _fdictResolveHandle(dictGateway, sHandle)
    dockerDisposable = dictGateway["dockerDisposable"]
    dictProbe = fdictProbeContainerAbsence(
        dockerDisposable, dictHandle["sContainerId"])
    if dictProbe["sAnswer"] == disposableSpecification.S_ABSENCE_ABSENT:
        return _fdictSettleHandleOutcome(dictGateway, sHandle, {
            "sOutcome": disposableSpecification.S_OUTCOME_DESTROYED,
            "sReason": "the container was already gone before removal",
            "dictProbe": dictProbe,
        })
    if dictProbe["sAnswer"] == (
            disposableSpecification.S_ABSENCE_INDETERMINATE):
        return _fdictSettleHandleOutcome(dictGateway, sHandle, {
            "sOutcome": disposableSpecification.S_OUTCOME_QUARANTINED,
            "sReason": (
                "the identity inspect did not answer; no removal was "
                f"attempted: {dictProbe['sDetail']}"
            ),
            "dictProbe": dictProbe,
        })
    if dictProbe["dictLabels"].get(
            disposableSpecification.S_DISPOSABLE_LABEL) != (
            dictHandle["sReservationId"]):
        raise DisposableContainerError(
            "destruction refused: the target container does not carry "
            "the disposable label matching this handle's reservation "
            f"({dictHandle['sReservationId']!r}); the id no longer names "
            "a container this gateway created, so nothing was destroyed"
        )
    dictDestroyed = fdictDestroyContainerAndProveAbsence(
        dockerDisposable, dictHandle["sContainerId"])
    return _fdictSettleHandleOutcome(dictGateway, sHandle, dictDestroyed)


def _fdictSettleHandleOutcome(dictGateway, sHandle, dictOutcome):
    """Settle a destruction outcome against the ledger and the handle.

    A proven destruction drops the reservation and retires the handle;
    a quarantine keeps both -- the reservation stays visible and the
    handle stays resolvable so a retry can re-verify and re-attempt.
    """
    dictHandle = dictGateway["dictHandlesById"][sHandle]
    _fnSettleReservation(
        dictGateway, dictHandle["sReservationId"],
        dictOutcome["sOutcome"], dictHandle["iEpoch"],
        dictOutcome["sReason"])
    if dictOutcome["sOutcome"] == (
            disposableSpecification.S_OUTCOME_DESTROYED):
        del dictGateway["dictHandlesById"][sHandle]
    return dictOutcome


def flistDescribeQuarantinedReservations(dictGateway):
    """List reservations whose container the daemon could not prove gone.

    The "a container may still exist" surface. Read-only over the
    ledger; no daemon is consulted.
    """
    return [
        {"sReservationId": dictReservation["sReservationId"],
         "sRole": dictReservation["sRole"],
         "sContainerId": dictReservation["sContainerId"],
         "sReason": dictReservation["sReason"]}
        for dictReservation
        in dictGateway["dictReservationsById"].values()
        if dictReservation["sStatus"] == S_RESERVATION_QUARANTINED
    ]


# ----- container-id primitives (drain, reconcile, crash recovery) ------


def fdictProbeContainerAbsence(dockerDisposable, sContainerId):
    """Positively establish whether a disposable container is gone.

    Three distinct answers, because "absent" and "the daemon errored"
    must never be conflated: ``absent`` is the daemon POSITIVELY
    answering 404 for the identifier; ``present`` is the daemon
    returning the container; anything else -- timeout, transport error,
    daemon fault -- is ``indeterminate``, which callers must treat as
    quarantine, never as completion. A ``present`` answer also carries
    the container's labels, which is how the handle-keyed destruction
    verifies identity from the SAME daemon answer it decides on.
    """
    moduleDocker = _fmoduleGetDocker()
    try:
        dictInspect = dockerDisposable.api.inspect_container(sContainerId)
        return {"sAnswer": disposableSpecification.S_ABSENCE_PRESENT,
                "sDetail": "",
                "dictLabels": (
                    (dictInspect.get("Config") or {}).get("Labels") or {})}
    except moduleDocker.errors.NotFound:
        return {"sAnswer": disposableSpecification.S_ABSENCE_ABSENT,
                "sDetail": "", "dictLabels": {}}
    except Exception as error:
        return {
            "sAnswer": disposableSpecification.S_ABSENCE_INDETERMINATE,
            "sDetail": f"{type(error).__name__}: {error}",
            "dictLabels": {},
        }


def fdictDestroyContainerAndProveAbsence(dockerDisposable, sContainerId):
    """Destroy a disposable container and settle only on proven absence.

    Namespace destruction is the containment: force-removal kills every
    process in the container's PID namespace, detached descendants
    included, and ``v=True`` takes the anonymous volumes with it. The
    transaction settles as ``destroyed`` only when the absence probe
    positively answers ``absent``; a daemon error during removal or an
    indeterminate probe answer yields ``quarantined`` -- the caller must
    keep the reservation visible and retry, never report a clean
    completion. This container-id form serves drain and crash recovery,
    whose survivors predate any in-memory handle; the operation lane
    uses the handle-keyed :func:`fdictDestroyAndSettle`.
    """
    moduleDocker = _fmoduleGetDocker()
    try:
        dockerDisposable.api.remove_container(
            sContainerId, force=True, v=True)
    except moduleDocker.errors.NotFound:
        pass
    except Exception as error:
        return {
            "sOutcome": disposableSpecification.S_OUTCOME_QUARANTINED,
            "sReason": (
                "Removal did not complete; the container may still be "
                f"running: {type(error).__name__}: {error}"
            ),
            "dictProbe": {
                "sAnswer": (
                    disposableSpecification.S_ABSENCE_INDETERMINATE),
                "sDetail": "removal failed before the probe",
            },
        }
    dictProbe = fdictProbeContainerAbsence(dockerDisposable, sContainerId)
    if dictProbe["sAnswer"] == disposableSpecification.S_ABSENCE_ABSENT:
        return {"sOutcome": disposableSpecification.S_OUTCOME_DESTROYED,
                "sReason": "", "dictProbe": dictProbe}
    return {
        "sOutcome": disposableSpecification.S_OUTCOME_QUARANTINED,
        "sReason": (
            "The absence probe answered "
            f"{dictProbe['sAnswer']!r}, not "
            f"{disposableSpecification.S_ABSENCE_ABSENT!r}; the "
            "container is not proven gone."
        ),
        "dictProbe": dictProbe,
    }


def flistDiscoverLabeledContainers(dockerDisposable):
    """Discover every disposable-labeled container, running or not.

    The crash-recovery entry point: a restarted hub calls this before
    any new disposable work starts and settles each survivor through
    :func:`fdictDestroyContainerAndProveAbsence`.
    """
    listContainers = dockerDisposable.containers.list(
        all=True,
        filters={"label": disposableSpecification.S_DISPOSABLE_LABEL},
    )
    listDiscovered = []
    for containerFound in listContainers:
        dictLabels = containerFound.labels or {}
        listDiscovered.append({
            "sContainerId": containerFound.id,
            "sContainerName": containerFound.name,
            "sReservationId": dictLabels.get(
                disposableSpecification.S_DISPOSABLE_LABEL, ""),
            "sRole": dictLabels.get(
                disposableSpecification.S_DISPOSABLE_ROLE_LABEL, ""),
            "sResourceName": dictLabels.get(
                disposableSpecification.S_DISPOSABLE_RESOURCE_LABEL, ""),
            "sStatus": containerFound.status,
        })
    return listDiscovered


def fdictSweepLabeledSurvivors(dockerDisposable, sResourceName=""):
    """Destroy every labeled survivor this hub is entitled to remove.

    ``sResourceName`` narrows the sweep to containers stamped with one
    project container's name. Passing it is what keeps a booting hub
    from destroying a live peer's work on a shared daemon; the empty
    default sweeps every labeled survivor and is correct only for a
    caller that owns the whole daemon. A survivor carrying NO resource
    stamp is always swept, because an unattributable container is
    exactly the leak this exists to clean.
    """
    listSurvivors = flistDiscoverLabeledContainers(dockerDisposable)
    listSettled = []
    for dictSurvivor in listSurvivors:
        sStamped = dictSurvivor["sResourceName"]
        if sResourceName and sStamped and sStamped != sResourceName:
            continue
        dictOutcome = fdictDestroyContainerAndProveAbsence(
            dockerDisposable, dictSurvivor["sContainerId"])
        listSettled.append({
            "sContainerId": dictSurvivor["sContainerId"],
            "sContainerName": dictSurvivor["sContainerName"],
            "sOutcome": dictOutcome["sOutcome"],
            "sReason": dictOutcome["sReason"],
        })
    return {"listSettled": listSettled,
            "iQuarantined": len([
                dictSettled for dictSettled in listSettled
                if dictSettled["sOutcome"]
                == disposableSpecification.S_OUTCOME_QUARANTINED])}
