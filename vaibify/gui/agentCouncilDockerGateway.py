"""The single Docker-SDK authority for every Agent Council container.

Remediation R4 (design/agentCouncilRemediation.md): this module is the
ONLY council module that touches the Docker SDK. Everything the council
does to a daemon — creating and starting a runner or sandbox, streaming
a snapshot in, executing a bounded turn, killing, probing absence,
destroying with proof, discovering labeled survivors, and building or
removing the per-campaign egress network and CONNECT proxy — originates
here. ``tests/testCouncilGatewayAuthority.py`` fails the build if any
other ``agentCouncil*`` module regains SDK reach.

Three properties the gateway enforces that the pre-R4 modules could not:

- **Opaque reservation handles.** Every created container is keyed by a
  server-minted ``secrets.token_hex(16)`` handle, and every subsequent
  per-container operation accepts ONLY a handle — never a raw container
  id — so a caller holding an arbitrary Docker id (the active project
  container's, for instance) cannot drive a council operation at it.
- **Reserve-before-create, settle-on-every-exit.** A registry
  reservation is written BEFORE the SDK create, and any exception after
  the reserve settles the reservation honestly: pending with no
  container is dropped, created-but-unstartable is destroyed with proof
  or left visibly quarantined. No path leaves a reservation unrecorded
  or a container untracked.
- **Identity-verified destruction.** A handle-keyed destroy inspects
  the target FIRST and refuses — destroying nothing — unless the
  container's ``vaibify-council`` label equals the handle's reservation
  id, which is what makes it impossible for the destruction lane to
  touch the active project container. An indeterminate daemon answer
  quarantines: the reservation stays visible, its admission budget
  stays consumed, and ``flistDescribeQuarantinedReservations`` feeds
  the UI's "runner may exist" surface.

The pure halves stay where they were: ``agentCouncilRunner`` keeps the
tar validation/stamping, limit validation, create-specification
composition and socket pumps; ``agentCouncilEgress`` keeps the proxy
script, allowlist validation, name/argv/environment composition and
the DNS wiring. This module supplies the daemon; they supply the
values.
"""

import posixpath
import secrets
import socket
import time

from vaibify.docker.dockerConnection import (
    _fmoduleGetDocker,
    _fnEnsureDockerHost,
)
from . import agentCouncilEgress
from . import agentCouncilRegistry
from . import agentCouncilRunner

__all__ = [
    "CouncilGatewayError",
    "fdockerCreateCouncilClient",
    "fdictCreateCouncilDockerGateway",
    "fdictReserveAndCreateRunner",
    "fnCopySnapshotIntoRunner",
    "fdictExecuteBoundedTurn",
    "fdictDestroyAndSettle",
    "flistDescribeQuarantinedReservations",
    "fdictProbeRunnerAbsence",
    "fdictDestroyRunnerAndProveAbsence",
    "flistDiscoverLabeledRunners",
    "fsCreateCampaignInternalNetwork",
    "fsLaunchAllowlistProxy",
    "fdictRemoveCampaignEgressResources",
    "fdictSweepCouncilEgressLeftovers",
]

I_COUNCIL_DAEMON_TIMEOUT_SECONDS = 60

# The hardened posture of the egress CONNECT proxy (remediation R4): the
# runner's own discipline applied to the proxy container — unprivileged
# user, all capabilities dropped, no privilege escalation, and hard
# memory / CPU / PID bounds. The listen port is above 1024 so the
# unprivileged bind succeeds.
I_PROXY_MEMORY_BYTES = 256 * 1024 * 1024
F_PROXY_CPU_COUNT = 0.5
I_PROXY_PIDS_LIMIT = 64


class CouncilGatewayError(Exception):
    """A gateway operation was refused before any Docker resource changed."""


def fdockerCreateCouncilClient(iTimeoutSeconds=None):
    """Create a Docker client for council-container operations.

    Follows the ``DockerConnection`` acquisition pattern (context-derived
    ``DOCKER_HOST``, lazy docker-py import) but with a much shorter
    per-call timeout: council teardown must DETECT an unresponsive
    daemon and quarantine, not hang. Streaming reads are not governed
    by this timeout — the bounded-turn pump polls its raw socket.
    """
    _fnEnsureDockerHost()
    if iTimeoutSeconds is None:
        iTimeoutSeconds = I_COUNCIL_DAEMON_TIMEOUT_SECONDS
    return _fmoduleGetDocker().from_env(timeout=iTimeoutSeconds)


def fdictCreateCouncilDockerGateway(dockerCouncil, dictRegistry,
                                    sResourceName=""):
    """Create the gateway state: one client, one registry, its handles.

    ``dockerCouncil`` may be None for a registry-only view (the routes
    read quarantined reservations through the gateway without ever
    needing a daemon); every SDK-touching operation requires a real
    client and will fail loudly on a None one.

    ``sResourceName`` is the project container this gateway's council
    work belongs to, and it lives HERE rather than on each create call
    because a gateway already serves exactly one campaign's project.
    Every container the gateway creates is stamped with it, so there is
    one place it can be forgotten instead of one per call site.
    """
    return {
        "dockerCouncil": dockerCouncil,
        "dictRegistry": dictRegistry,
        "sResourceName": sResourceName,
        "dictHandlesById": {},
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
        raise CouncilGatewayError(
            "unknown council gateway handle; operations accept only a "
            "handle minted by fdictReserveAndCreateRunner, never a raw "
            "container identifier"
        )
    return dictHandle


# ----- reserve-before-create runner lifecycle ---------------------------


def fdictReserveAndCreateRunner(
    dictGateway, sCampaignId, sProvider, dictRunnerCost, sImageReference,
    dictLimits=None, sNetworkName=None, bSandbox=False,
    dictEnvironment=None, listDnsServers=None, listDnsOptions=None,
):
    """Reserve admission, then create and start one council container.

    The write-ahead order is the contract (design section 9.3): the
    registry reservation is recorded BEFORE the SDK create, so a crash
    between the two leaves a visible pending reservation, never an
    untracked container. An admission refusal creates nothing and
    returns ``{"bCreated": False, "sRefusalReason", "sHandle": ""}``.
    On success the reservation is marked live and an opaque handle is
    minted; on ANY exception after the reserve the reservation is
    settled honestly (dropped, destroyed-with-proof, or quarantined)
    before the exception propagates.
    """
    dictRegistry = dictGateway["dictRegistry"]
    sReservationId = f"council-{sCampaignId}-{secrets.token_hex(6)}"
    dictReserved = agentCouncilRegistry.fdictReserveRunner(
        dictRegistry, sCampaignId, sReservationId, sProvider,
        dictRunnerCost)
    if not dictReserved["bReserved"]:
        return {"bCreated": False,
                "sRefusalReason": dictReserved["sRefusalReason"],
                "sHandle": ""}
    iEpoch = dictReserved["dictReservation"]["iEpoch"]
    containerRunner = None
    try:
        dictSpecification = (
            agentCouncilRunner.fdictComposeRunnerCreateSpecification(
                sImageReference, sReservationId, dictLimits, sNetworkName,
                bSandbox, dictEnvironment, listDnsServers, listDnsOptions,
                dictGateway.get("sResourceName", "")))
        containerRunner = dictGateway["dockerCouncil"].containers.create(
            sImageReference, **dictSpecification["dictCreateKeywords"])
        containerRunner.start()
    except Exception:
        _fnSettleFailedCreation(
            dictGateway, sReservationId, iEpoch, containerRunner)
        raise
    agentCouncilRegistry.fnMarkRunnerCreated(
        dictRegistry, sReservationId, containerRunner.id)
    sHandle = secrets.token_hex(16)
    dictGateway["dictHandlesById"][sHandle] = {
        "sReservationId": sReservationId,
        "sContainerId": containerRunner.id,
        "sContainerName": dictSpecification["sContainerName"],
        "sCampaignId": sCampaignId,
        "sRole": dictSpecification["sRole"],
        "iEpoch": iEpoch,
    }
    return {"bCreated": True, "sRefusalReason": "", "sHandle": sHandle,
            "sReservationId": sReservationId,
            "sContainerName": dictSpecification["sContainerName"],
            "sRole": dictSpecification["sRole"]}


def _fnSettleFailedCreation(dictGateway, sReservationId, iEpoch,
                            containerRunner):
    """Settle the reservation of a creation that raised.

    Pending with no container is dropped (settled as destroyed — there
    is nothing to prove gone). A created-but-unstartable container is
    bound to its reservation, then destroyed with proof; an unproven
    destruction leaves the reservation visibly quarantined, still
    holding budget, exactly as a live runner's would.
    """
    dictRegistry = dictGateway["dictRegistry"]
    if containerRunner is None:
        agentCouncilRegistry.fdictSettleReservation(
            dictRegistry, sReservationId,
            agentCouncilRunner.S_OUTCOME_DESTROYED, iEpoch)
        return
    agentCouncilRegistry.fnMarkRunnerCreated(
        dictRegistry, sReservationId, containerRunner.id)
    dictDestroyed = fdictDestroyRunnerAndProveAbsence(
        dictGateway["dockerCouncil"], containerRunner.id)
    agentCouncilRegistry.fdictSettleReservation(
        dictRegistry, sReservationId, dictDestroyed["sOutcome"], iEpoch)


# ----- handle-keyed container operations --------------------------------


def _ftStartExecStream(dockerCouncil, sContainerId, listCommand,
                       bStdin, sWorkingDirectory=None):
    """Create an exec as the council user; return (execId, rawSocket)."""
    dictExecCreated = dockerCouncil.api.exec_create(
        sContainerId, listCommand,
        stdin=bStdin, stdout=True, stderr=True,
        user=agentCouncilRunner.S_COUNCIL_CONTAINER_USER,
        workdir=sWorkingDirectory,
    )
    socketExec = dockerCouncil.api.exec_start(
        dictExecCreated["Id"], socket=True,
    )
    socketRaw = getattr(socketExec, "_sock", socketExec)
    socketRaw.settimeout(agentCouncilRunner.F_STREAM_POLL_SECONDS)
    return (dictExecCreated["Id"], socketRaw)


def fnCopySnapshotIntoRunner(
        dictGateway, sHandle, baSnapshotTar,
        sDestinationDirectory=agentCouncilRunner.S_RUNNER_SNAPSHOT_ROOT):
    """Copy a sealed tarball into a handle's container, stamped and safe.

    Every member is validated against extraction-root escape and stamped
    to the unprivileged council user (both pure, in the runner module),
    then streamed through an exec's stdin and extracted INSIDE the
    container by that user. The archive endpoint cannot be used — the
    daemon refuses it wholesale on a read-only-rootfs container — and
    the in-container extraction is what makes a root-owned copy
    impossible: a non-root ``tar`` cannot chown.
    """
    dictHandle = _fdictResolveHandle(dictGateway, sHandle)
    dockerCouncil = dictGateway["dockerCouncil"]
    if not posixpath.isabs(sDestinationDirectory):
        raise ValueError(
            "Council snapshot destination must be an absolute container "
            f"path, got {sDestinationDirectory!r}."
        )
    bufferRepacked = agentCouncilRunner.fbufferRepackSnapshotStamped(
        baSnapshotTar)
    fDeadlineMonotonic = (
        time.monotonic()
        + agentCouncilRunner.F_SNAPSHOT_COPY_BUDGET_SECONDS)
    sExecId, socketRaw = _ftStartExecStream(
        dockerCouncil, dictHandle["sContainerId"],
        ["tar", "-xf", "-", "-C", sDestinationDirectory],
        bStdin=True,
    )
    try:
        agentCouncilRunner.fnSendAllBounded(
            socketRaw, bufferRepacked.getvalue(), fDeadlineMonotonic)
        socketRaw.shutdown(socket.SHUT_WR)
        dictPumped = agentCouncilRunner.fdictPumpBoundedExecStream(
            socketRaw, agentCouncilRunner.I_DEFAULT_TURN_OUTPUT_CAP_BYTES,
            fDeadlineMonotonic)
    finally:
        socketRaw.close()
    if dictPumped["bDeadlineExceeded"]:
        raise RuntimeError(
            "Council snapshot copy exceeded its "
            f"{agentCouncilRunner.F_SNAPSHOT_COPY_BUDGET_SECONDS:.0f}s "
            "budget."
        )
    iExitCode = dockerCouncil.api.exec_inspect(sExecId).get("ExitCode")
    if iExitCode != 0:
        sOutputTail = dictPumped["baCaptured"][-2048:].decode(
            "utf-8", errors="replace")
        raise RuntimeError(
            "Council snapshot extraction failed inside the container "
            f"(exit {iExitCode}): {sOutputTail}"
        )


def _fnKillContainerQuietly(dockerCouncil, sContainerId):
    """Kill the container, tolerating one already stopped or gone."""
    try:
        dockerCouncil.api.kill(sContainerId)
    except Exception:
        pass


def fdictExecuteBoundedTurn(
    dictGateway, sHandle, listCommand,
    iOutputByteCap=None, fWallClockSeconds=None, sWorkingDirectory=None,
    baStdinPayload=None,
):
    """Execute one bounded command (the turn) in a handle's container.

    Output is captured under a byte cap and the whole turn under a
    wall-clock budget, both enforced host-side. Breaching either bound
    kills the CONTAINER, not just the exec — the runner is disposable
    and a turn that broke its budget has ended; the caller settles it
    with :func:`fdictDestroyAndSettle`. ``iExitCode`` is ``None`` when
    no exit code could be established (a killed turn), never a
    fabricated zero. ``baStdinPayload`` streams bytes to the command's
    stdin before the output pump begins (the CLI adapter's
    untrusted-material channel), under the same deadline, with the
    write half then shut so a reader waiting on EOF unblocks.
    """
    dictHandle = _fdictResolveHandle(dictGateway, sHandle)
    dockerCouncil = dictGateway["dockerCouncil"]
    if iOutputByteCap is None:
        iOutputByteCap = agentCouncilRunner.I_DEFAULT_TURN_OUTPUT_CAP_BYTES
    if fWallClockSeconds is None:
        fWallClockSeconds = (
            agentCouncilRunner.F_DEFAULT_TURN_WALL_CLOCK_SECONDS)
    fStartedMonotonic = time.monotonic()
    fDeadlineMonotonic = fStartedMonotonic + fWallClockSeconds
    sExecId, socketRaw = _ftStartExecStream(
        dockerCouncil, dictHandle["sContainerId"], listCommand,
        bStdin=baStdinPayload is not None,
        sWorkingDirectory=sWorkingDirectory,
    )
    try:
        if baStdinPayload is not None:
            agentCouncilRunner.fnSendAllBounded(
                socketRaw, baStdinPayload, fDeadlineMonotonic)
            socketRaw.shutdown(socket.SHUT_WR)
        dictPumped = agentCouncilRunner.fdictPumpBoundedExecStream(
            socketRaw, iOutputByteCap, fDeadlineMonotonic)
    finally:
        socketRaw.close()
    bBreached = (
        dictPumped["bOutputCapExceeded"] or dictPumped["bDeadlineExceeded"]
    )
    if bBreached:
        _fnKillContainerQuietly(dockerCouncil, dictHandle["sContainerId"])
    # Bind the inspect result before reading it, here and below. A
    # ``.get()`` chained onto a docker-py call counts as its own
    # unreadable site, because the scan sees a call whose chain passes
    # through ``.api`` and cannot trace the client root -- so a fluent
    # one-liner spends blind-spot budget per link. Subscripting a bound
    # dict is not a call at all. A missing key raises into the same
    # ``except`` the chain's ``None`` already fell through to.
    iExitCode = None
    try:
        dictExecInspected = dockerCouncil.api.exec_inspect(sExecId)
        iExitCode = dictExecInspected["ExitCode"]
    except Exception:
        pass
    # Read the CONTAINER's own verdict before anything destroys it. An
    # exit code of 137 is SIGKILL and says nothing about who sent it:
    # this gateway kills on a breached bound, and the kernel kills on
    # memory pressure. Without OOMKilled the two are indistinguishable,
    # and a live opus turn stayed ambiguous for a whole session because
    # nothing asked (external review, 2026-08-25).
    bOomKilled = False
    try:
        dictInspected = dockerCouncil.api.inspect_container(
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
        # The observed stream size. The diagnosis read this key before
        # anything produced it, so every record said zero bytes while
        # looking authoritative — a field that reports a constant is
        # worse than an absent one (external review, 2026-08-25).
        "iOutputBytes": len(dictPumped["baCaptured"]),
        "bOomKilled": bOomKilled,
        "fElapsedSeconds": time.monotonic() - fStartedMonotonic,
    }


def fdictDestroyAndSettle(dictGateway, sHandle):
    """Destroy a handle's container, identity-verified, and settle it.

    The inspect comes FIRST: destruction proceeds only when the target
    container's ``vaibify-council`` label equals the handle's
    reservation id. A missing or mismatched label refuses — destroying
    nothing — because a daemon answer that does not carry the council's
    own stamp means the id no longer names the container the gateway
    created (the active project container is the canonical thing this
    protects). ``NotFound`` at inspect settles as destroyed (already
    gone); any other inspect fault quarantines with NO removal attempt.
    After a removal attempt the reservation is settled through the
    registry with the handle's epoch: destroyed frees it, anything
    unproven leaves it visibly quarantined with its budget held.
    """
    dictHandle = _fdictResolveHandle(dictGateway, sHandle)
    dockerCouncil = dictGateway["dockerCouncil"]
    dictProbe = fdictProbeRunnerAbsence(
        dockerCouncil, dictHandle["sContainerId"])
    if dictProbe["sAnswer"] == agentCouncilRunner.S_ABSENCE_ABSENT:
        return _fdictSettleHandleOutcome(dictGateway, sHandle, {
            "sOutcome": agentCouncilRunner.S_OUTCOME_DESTROYED,
            "sReason": "the container was already gone before removal",
            "dictProbe": dictProbe,
        })
    if dictProbe["sAnswer"] == agentCouncilRunner.S_ABSENCE_INDETERMINATE:
        return _fdictSettleHandleOutcome(dictGateway, sHandle, {
            "sOutcome": agentCouncilRunner.S_OUTCOME_QUARANTINED,
            "sReason": (
                "the identity inspect did not answer; no removal was "
                f"attempted: {dictProbe['sDetail']}"
            ),
            "dictProbe": dictProbe,
        })
    if dictProbe["dictLabels"].get(agentCouncilRunner.S_COUNCIL_LABEL) != (
            dictHandle["sReservationId"]):
        raise CouncilGatewayError(
            "destruction refused: the target container does not carry "
            "the council label matching this handle's reservation "
            f"({dictHandle['sReservationId']!r}); the id no longer names "
            "a container this gateway created, so nothing was destroyed"
        )
    dictDestroyed = fdictDestroyRunnerAndProveAbsence(
        dockerCouncil, dictHandle["sContainerId"])
    return _fdictSettleHandleOutcome(dictGateway, sHandle, dictDestroyed)


def _fdictSettleHandleOutcome(dictGateway, sHandle, dictOutcome):
    """Settle a destruction outcome against the registry and the handle.

    A proven destruction frees the reservation and retires the handle;
    a quarantine keeps both — the reservation visibly holds its budget
    and the handle stays resolvable so a retry can re-verify and
    re-attempt.
    """
    dictHandle = dictGateway["dictHandlesById"][sHandle]
    agentCouncilRegistry.fdictSettleReservation(
        dictGateway["dictRegistry"], dictHandle["sReservationId"],
        dictOutcome["sOutcome"], dictHandle["iEpoch"])
    if dictOutcome["sOutcome"] == agentCouncilRunner.S_OUTCOME_DESTROYED:
        del dictGateway["dictHandlesById"][sHandle]
    return dictOutcome


def flistDescribeQuarantinedReservations(dictGateway, sCampaignId):
    """List a campaign's quarantined reservations for the UI.

    The "runner may exist" surface: each entry is a reservation whose
    container the daemon could not prove gone, still holding admission
    budget until ``vaibify`` reconciliation proves it absent. Read-only
    over the registry; no daemon is consulted.
    """
    return [
        {"sReservationId": dictReservation["sReservationId"],
         "sCampaignId": dictReservation["sCampaignId"],
         "sProvider": dictReservation["sProvider"]}
        for dictReservation
        in dictGateway["dictRegistry"]["dictReservationsById"].values()
        if dictReservation["sStatus"]
        == agentCouncilRegistry.S_RESERVATION_QUARANTINED
        and dictReservation["sCampaignId"] == sCampaignId
    ]


# ----- container-id primitives (drain, reconcile, crash recovery) ------


def fdictProbeRunnerAbsence(dockerCouncil, sContainerId):
    """Positively establish whether a council container is gone.

    Three distinct answers, because "absent" and "the daemon errored"
    must never be conflated: ``absent`` is the daemon POSITIVELY
    answering 404 for the identifier; ``present`` is the daemon
    returning the container; anything else — timeout, transport error,
    daemon fault — is ``indeterminate``, which callers must treat as
    quarantine, never as completion. A ``present`` answer also carries
    the container's labels in ``dictLabels``, which is how the
    handle-keyed destruction verifies the council-label identity from
    the SAME daemon answer it decides on.
    """
    moduleDocker = _fmoduleGetDocker()
    try:
        dictInspect = dockerCouncil.api.inspect_container(sContainerId)
        return {"sAnswer": agentCouncilRunner.S_ABSENCE_PRESENT,
                "sDetail": "",
                "dictLabels": (
                    (dictInspect.get("Config") or {}).get("Labels") or {})}
    except moduleDocker.errors.NotFound:
        return {"sAnswer": agentCouncilRunner.S_ABSENCE_ABSENT,
                "sDetail": "", "dictLabels": {}}
    except Exception as error:
        return {
            "sAnswer": agentCouncilRunner.S_ABSENCE_INDETERMINATE,
            "sDetail": f"{type(error).__name__}: {error}",
            "dictLabels": {},
        }


def fdictDestroyRunnerAndProveAbsence(dockerCouncil, sContainerId):
    """Destroy a council container and settle only on proven absence.

    Namespace destruction is the containment: force-removal kills every
    process in the container's PID namespace, detached descendants
    included. The transaction settles as ``destroyed`` only when the
    absence probe positively answers ``absent``; a daemon error during
    removal or an indeterminate probe answer yields ``quarantined`` —
    the caller must keep the reservation visible and retry, never
    report a clean completion. This container-id form serves the
    registry's drain and crash-recovery reconcile, whose survivors
    predate any in-memory handle; the operation lane uses the
    handle-keyed :func:`fdictDestroyAndSettle`.
    """
    moduleDocker = _fmoduleGetDocker()
    try:
        dockerCouncil.api.remove_container(sContainerId, force=True, v=True)
    except moduleDocker.errors.NotFound:
        pass
    except Exception as error:
        return {
            "sOutcome": agentCouncilRunner.S_OUTCOME_QUARANTINED,
            "sReason": (
                "Removal did not complete; the container may still be "
                f"running: {type(error).__name__}: {error}"
            ),
            "dictProbe": {
                "sAnswer": agentCouncilRunner.S_ABSENCE_INDETERMINATE,
                "sDetail": "removal failed before the probe",
            },
        }
    dictProbe = fdictProbeRunnerAbsence(dockerCouncil, sContainerId)
    if dictProbe["sAnswer"] == agentCouncilRunner.S_ABSENCE_ABSENT:
        return {"sOutcome": agentCouncilRunner.S_OUTCOME_DESTROYED,
                "sReason": "", "dictProbe": dictProbe}
    return {
        "sOutcome": agentCouncilRunner.S_OUTCOME_QUARANTINED,
        "sReason": (
            "The absence probe answered "
            f"{dictProbe['sAnswer']!r}, not "
            f"{agentCouncilRunner.S_ABSENCE_ABSENT!r}; the container is "
            "not proven gone."
        ),
        "dictProbe": dictProbe,
    }


def flistDiscoverLabeledRunners(dockerCouncil):
    """Discover every council-labeled container, running or not.

    The crash-recovery entry point: a restarted hub calls this before
    any new council starts, matches each survivor's reservation
    identifier against the council registry, and settles each one
    through :func:`fdictDestroyRunnerAndProveAbsence`.
    """
    listContainers = dockerCouncil.containers.list(
        all=True,
        filters={"label": agentCouncilRunner.S_COUNCIL_LABEL},
    )
    listDiscovered = []
    for containerFound in listContainers:
        dictLabels = containerFound.labels or {}
        listDiscovered.append({
            "sContainerId": containerFound.id,
            "sContainerName": containerFound.name,
            "sReservationId": dictLabels.get(
                agentCouncilRunner.S_COUNCIL_LABEL, ""),
            "sRole": dictLabels.get(
                agentCouncilRunner.S_COUNCIL_ROLE_LABEL, ""),
            "sResourceName": dictLabels.get(
                agentCouncilRunner.S_COUNCIL_RESOURCE_LABEL, ""),
            "sStatus": containerFound.status,
        })
    return listDiscovered


# ----- per-campaign egress resources ------------------------------------


def fsCreateCampaignInternalNetwork(dictGateway, sCampaignId):
    """Create the campaign's internal network and return its name.

    ``internal=True`` is the boundary itself: the daemon attaches no
    gateway off the host, so a runner's direct dial to any bridge,
    external or IPv6 address fails with ``ENETUNREACH`` before a
    single packet leaves the machine.
    """
    sNetworkName = agentCouncilEgress.fsComposeNetworkName(sCampaignId)
    try:
        dictGateway["dockerCouncil"].networks.create(
            sNetworkName, driver="bridge", internal=True)
    except Exception as error:
        raise agentCouncilEgress.EgressSetupError(
            f"failed to create internal network {sNetworkName}: "
            f"{type(error).__name__}: {error}"
        )
    return sNetworkName


def _fnCopyProxyScriptIntoContainer(dockerCouncil, sContainerId):
    """Deliver the server-owned proxy script into the created container.

    Reuses the runner's ownership-stamping one-file tarball builder, so
    the single tar-entry discipline in the package stays in one place
    and ``tarfile.TarInfo``'s native uid/gid default of 0 can never
    leak; the 1000-stamped entries are what let the NON-ROOT proxy read
    its own script. The archive endpoint extracts at ``/``, so the
    script lands at the egress module's script path.
    """
    baTarball = agentCouncilEgress.fbaBuildProxyScriptTarball()
    try:
        dockerCouncil.api.put_archive(sContainerId, "/", baTarball)
    except Exception as error:
        raise agentCouncilEgress.EgressSetupError(
            "failed to copy the proxy script into the egress proxy: "
            f"{type(error).__name__}: {error}"
        )


def _fnAwaitProxyListening(dockerCouncil, sContainerId):
    """Block until the proxy logs its ready line, or raise."""
    fDeadline = (time.monotonic()
                 + agentCouncilEgress.F_PROXY_READY_DEADLINE_SECONDS)
    while time.monotonic() < fDeadline:
        try:
            baLogs = dockerCouncil.api.logs(
                sContainerId, stdout=True, stderr=True)
        except Exception:
            time.sleep(agentCouncilEgress.F_PROXY_READY_POLL_SECONDS)
            continue
        sCombined = baLogs.decode("utf-8", errors="replace")
        if agentCouncilEgress.S_PROXY_READY_LINE in sCombined:
            return
        if "Traceback" in sCombined:
            raise agentCouncilEgress.EgressSetupError(
                f"the egress proxy crashed on startup: {sCombined}")
        time.sleep(agentCouncilEgress.F_PROXY_READY_POLL_SECONDS)
    raise agentCouncilEgress.EgressSetupError(
        "the egress proxy never reported "
        f"{agentCouncilEgress.S_PROXY_READY_LINE!r} within "
        f"{agentCouncilEgress.F_PROXY_READY_DEADLINE_SECONDS} seconds"
    )


def _fsReadProxyInternalAddress(dockerCouncil, sContainerId, sNetworkName):
    """Return the proxy's numeric address on the internal network."""
    try:
        dictInspect = dockerCouncil.api.inspect_container(sContainerId)
    except Exception as error:
        raise agentCouncilEgress.EgressSetupError(
            "could not read the internal-network address of the egress "
            f"proxy: {type(error).__name__}: {error}"
        )
    dictNetworks = dictInspect.get("NetworkSettings", {}).get("Networks", {})
    sAddress = dictNetworks.get(sNetworkName, {}).get("IPAddress", "")
    if not sAddress:
        raise agentCouncilEgress.EgressSetupError(
            f"the egress proxy reports no address on {sNetworkName}")
    return sAddress


def _fnAttachToDefaultBridge(dockerCouncil, sContainerId):
    """Attach the proxy to the default bridge — its resolving/dialing side."""
    try:
        dockerCouncil.api.connect_container_to_network(sContainerId, "bridge")
    except Exception as error:
        raise agentCouncilEgress.EgressSetupError(
            "failed to attach the egress proxy to the default bridge: "
            f"{type(error).__name__}: {error}"
        )


def _fnRemoveContainerQuietly(dockerCouncil, sContainerId):
    """Force-remove a container, tolerating one already gone."""
    moduleDocker = _fmoduleGetDocker()
    try:
        dockerCouncil.api.remove_container(sContainerId, force=True, v=True)
    except moduleDocker.errors.NotFound:
        pass
    except Exception:
        pass


def fsLaunchAllowlistProxy(dictGateway, sCampaignId, saAllowedHostnames,
                           iaAllowedPorts=None,
                           dictHostnameAddressMap=None):
    """Launch the campaign's CONNECT proxy; return its internal address.

    The container is created on the internal network with the RUNNER'S
    posture (remediation R4): the digest-pinned proxy image, the
    unprivileged council user, all capabilities dropped,
    no-new-privileges, and hard memory / CPU / PID bounds. It receives
    the server-owned script through the archive endpoint (1000-stamped,
    so the non-root proxy can read it), is attached to the default
    bridge (its resolving and dialing side), and only then started — so
    it is dual-homed before the first byte is served. The returned
    address is the NUMERIC internal-network address the runner's proxy
    environment points at, which is why a runner needs no resolver.
    """
    iaPorts = list(iaAllowedPorts) if iaAllowedPorts else [443]
    dictAddressMap = dict(dictHostnameAddressMap or {})
    saHostnames = [sHostname.lower() for sHostname in saAllowedHostnames]
    agentCouncilEgress.fnValidateAllowlistOrRaise(
        saHostnames, iaPorts, dictAddressMap)
    sNetworkName = agentCouncilEgress.fsComposeNetworkName(sCampaignId)
    sProxyName = agentCouncilEgress.fsComposeProxyContainerName(sCampaignId)
    dockerCouncil = dictGateway["dockerCouncil"]
    try:
        containerProxy = dockerCouncil.containers.create(
            agentCouncilEgress.S_PROXY_IMAGE,
            command=agentCouncilEgress.flistComposeProxyCommand(
                saHostnames, iaPorts, dictAddressMap),
            name=sProxyName,
            network=sNetworkName,
            user=agentCouncilRunner.S_COUNCIL_CONTAINER_USER,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            mem_limit=I_PROXY_MEMORY_BYTES,
            memswap_limit=I_PROXY_MEMORY_BYTES,
            nano_cpus=int(F_PROXY_CPU_COUNT * 1_000_000_000),
            pids_limit=I_PROXY_PIDS_LIMIT,
            # The council label makes the proxy DISCOVERABLE by the
            # same startup reconcile that settles crashed runners: an
            # indeterminate in-process teardown cannot orphan a
            # dual-homed proxy past the next hub start.
            labels={
                agentCouncilRunner.S_COUNCIL_LABEL:
                    f"egress-{sCampaignId}",
                agentCouncilRunner.S_COUNCIL_ROLE_LABEL: "egressProxy",
                agentCouncilRunner.S_COUNCIL_RESOURCE_LABEL:
                    dictGateway.get("sResourceName", ""),
            },
        )
    except Exception as error:
        raise agentCouncilEgress.EgressSetupError(
            f"failed to create the egress proxy container {sProxyName}: "
            f"{type(error).__name__}: {error}"
        )
    sContainerId = containerProxy.id
    try:
        _fnCopyProxyScriptIntoContainer(dockerCouncil, sContainerId)
        _fnAttachToDefaultBridge(dockerCouncil, sContainerId)
        containerProxy.start()
        _fnAwaitProxyListening(dockerCouncil, sContainerId)
        return _fsReadProxyInternalAddress(
            dockerCouncil, sContainerId, sNetworkName)
    except Exception:
        _fnRemoveContainerQuietly(dockerCouncil, sContainerId)
        raise


def _fsRemoveProxyContainer(dockerCouncil, sProxyName):
    """Force-remove the proxy container and probe its absence by name.

    Returns one of the three absence answers. ``absent`` is only ever a
    POSITIVE ``NotFound`` from the inspect probe AFTER removal; any
    other daemon error — a failed removal, an inspect that faulted — is
    ``indeterminate``, reported by the caller, never swallowed into a
    claim of absence.
    """
    moduleDocker = _fmoduleGetDocker()
    try:
        dockerCouncil.api.remove_container(sProxyName, force=True, v=True)
    except moduleDocker.errors.NotFound:
        pass
    except Exception:
        return agentCouncilRunner.S_ABSENCE_INDETERMINATE
    try:
        dockerCouncil.api.inspect_container(sProxyName)
    except moduleDocker.errors.NotFound:
        return agentCouncilRunner.S_ABSENCE_ABSENT
    except Exception:
        return agentCouncilRunner.S_ABSENCE_INDETERMINATE
    return agentCouncilRunner.S_ABSENCE_PRESENT


def _fsRemoveInternalNetwork(dockerCouncil, sNetworkName):
    """Remove the internal network and probe its absence by name.

    Same three-state discipline as the proxy: ``absent`` is a positive
    ``NotFound`` from the inspect probe after removal, and any other
    daemon fault is ``indeterminate``. The proxy is removed first, so a
    clean removal leaves the network with no endpoints to refuse it.
    """
    moduleDocker = _fmoduleGetDocker()
    try:
        dockerCouncil.api.remove_network(sNetworkName)
    except moduleDocker.errors.NotFound:
        pass
    except Exception:
        return agentCouncilRunner.S_ABSENCE_INDETERMINATE
    try:
        dockerCouncil.api.inspect_network(sNetworkName)
    except moduleDocker.errors.NotFound:
        return agentCouncilRunner.S_ABSENCE_ABSENT
    except Exception:
        return agentCouncilRunner.S_ABSENCE_INDETERMINATE
    return agentCouncilRunner.S_ABSENCE_PRESENT


def fdictSweepCouncilEgressLeftovers(dockerCouncil, listCampaignIds):
    """Remove every known campaign's egress resources left by a crash.

    The durable backstop for an indeterminate in-process teardown: a
    hub that died (or answered indeterminately) can leave a dual-homed
    CONNECT proxy and its internal network with nobody accounting for
    them. Every campaign id passed here has its composed proxy and
    network names removed outright, absence proven by name; the
    LABELED reconcile that runs beside this catches any proxy whose
    campaign record is gone (the proxy wears the council label exactly
    so a lost record cannot orphan it). Deliberately enumerates from
    the durable store rather than asking the daemon for name-prefixed
    resources: the two existing removal probes are reused verbatim, so
    this backstop adds NO new untraceable SDK roots to the blind-spot
    budget.

    WHICH campaigns are sweepable is the CALLER's judgement, and it is
    not "all of them". This used to reason that no council drive can be
    live at startup because drives do not survive a restart — true of
    this hub's own drives, false of a second hub booting beside a
    working one, whose live campaigns sit in the same machine-wide
    store. Passing them here cuts the egress their running turns use.

    Returns ``{listRemovedResources,
    listIndeterminateResources}``; an indeterminate answer is reported
    for the log and the NEXT start sweeps again — the record never
    silently disappears.
    """
    listRemovedResources = []
    listIndeterminateResources = []
    for sCampaignId in listCampaignIds:
        try:
            agentCouncilEgress.fnValidateCampaignIdOrRaise(sCampaignId)
        except Exception:
            continue
        for fsRemoveResource, sResourceName in (
                (_fsRemoveProxyContainer,
                 agentCouncilEgress.fsComposeProxyContainerName(
                     sCampaignId)),
                (_fsRemoveInternalNetwork,
                 agentCouncilEgress.fsComposeNetworkName(sCampaignId))):
            sAnswer = fsRemoveResource(dockerCouncil, sResourceName)
            if sAnswer == agentCouncilRunner.S_ABSENCE_ABSENT:
                listRemovedResources.append(sResourceName)
            else:
                listIndeterminateResources.append(sResourceName)
    return {"listRemovedResources": listRemovedResources,
            "listIndeterminateResources": listIndeterminateResources}


def fdictRemoveCampaignEgressResources(dictGateway, sCampaignId):
    """Remove the campaign's proxy and network, proving absence.

    Returns ``{bProxyAbsenceProven, bNetworkAbsenceProven,
    saIndeterminateResources}``. A proven flag is True only when the
    daemon positively answered ``NotFound`` for that name AFTER the
    removal attempt. A daemon that did not answer lands the resource
    in ``saIndeterminateResources`` with its flag False — reported,
    never swallowed, exactly as an unanswered container probe makes a
    settlement inconclusive rather than clean. The proxy is removed
    before the network so the network has no endpoint to refuse.
    """
    sProxyName = agentCouncilEgress.fsComposeProxyContainerName(sCampaignId)
    sNetworkName = agentCouncilEgress.fsComposeNetworkName(sCampaignId)
    dockerCouncil = dictGateway["dockerCouncil"]
    sProxyAnswer = _fsRemoveProxyContainer(dockerCouncil, sProxyName)
    sNetworkAnswer = _fsRemoveInternalNetwork(dockerCouncil, sNetworkName)
    saIndeterminateResources = []
    if sProxyAnswer == agentCouncilRunner.S_ABSENCE_INDETERMINATE:
        saIndeterminateResources.append(sProxyName)
    if sNetworkAnswer == agentCouncilRunner.S_ABSENCE_INDETERMINATE:
        saIndeterminateResources.append(sNetworkName)
    return {
        "bProxyAbsenceProven":
            sProxyAnswer == agentCouncilRunner.S_ABSENCE_ABSENT,
        "bNetworkAbsenceProven":
            sNetworkAnswer == agentCouncilRunner.S_ABSENCE_ABSENT,
        "saIndeterminateResources": saIndeterminateResources,
    }
