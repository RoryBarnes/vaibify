"""Disposable runner and sandbox container lifecycle for the Agent Council.

Phase 0 of the Agent Council (design/agentCouncil.md sections 2.6 and
9.6): each provider turn — and each sandboxed script execution — gets a
fresh disposable container, and the WHOLE container is destroyed
afterward, settled only after an absence probe positively establishes
the container is gone. Process exit is never the containment proof; a
``setsid``-detached descendant dies with the process namespace it
detached inside, and namespace destruction is the only claim this
module makes about quietness.

Council containers are NEVER the active project container. They are
council-created and council-destroyed: this module never touches
``dictContainerOwners``, never mints a lease, and never opens a
commit-carrier admission — their lifecycle is governed by the council
registry (Phase 2), not the commit carrier (design section 10.3). The
Docker-client acquisition and every container primitive here will be
dispositioned in ``tests/mutationInventory.json`` at integration.

The seven-step lifecycle (design section 9.6) maps onto this module as:

1.  ``fdictCreateRunnerContainer`` — private PID/network/IPC namespaces,
    all capabilities dropped, no-new-privileges, no devices, default
    seccomp, unprivileged user, an explicit entrypoint override that
    bypasses the image's hub-oriented startup, and hard CPU / memory
    (swap pinned) / PID-count / writable-disk limits.
2.  ``fnCopySnapshotIntoRunner`` — the sealed snapshot tarball, every
    entry stamped to the unprivileged container user through the
    ``_finfoBuildTarEntry`` discipline, extracted INSIDE the container
    by that user. No mount of any host path, no workspace volume, no
    Docker socket, no host home, no credential store.
3.  (limits are baked at creation; the per-turn bounds live in step 4)
4.  ``fdictExecuteBoundedTurn`` — one bounded command with an
    output-byte cap and a wall-clock budget, both enforced host-side.
5-7. ``fdictDestroyRunnerAndProveAbsence`` — stop and remove the whole
    container, then settle only on a positive absence answer; an
    indeterminate daemon answer is a quarantine result, never success.

``flistDiscoverLabeledRunners`` is the crash-recovery half: every
council container carries the ``vaibify-council`` label with its
reservation identifier, so a restarted hub can discover labeled
survivors and settle them through the same destruction transaction.

Writable-disk bound, selected and proven for Phase 0: ``storage_opt``
is unavailable on Docker Desktop / colima (overlayfs without pquota),
so the rootfs is mounted read-only and the only writable surfaces are
two SIZED tmpfs mounts — the council working tree and ``/tmp``. Two
empirical facts shape the implementation and are pinned by the live
suite (``tests/testAgentCouncilRunnerLive.py``):

- the Docker archive endpoint (``put_archive`` / ``docker cp``) refuses
  ANY write into a read-only-rootfs container, tmpfs target included
  ("container rootfs is marked read-only"), so the snapshot is streamed
  through an exec's stdin and untarred by the unprivileged user inside
  the container — which also makes a root-owned copy impossible by
  construction, because a non-root ``tar`` cannot chown;
- tmpfs pages are charged to the container's memory cgroup, so the
  tmpfs sizes must stay below the memory limit or the "disk" bound
  silently becomes the memory bound; ``fdictCreateRunnerContainer``
  refuses limit sets that would do that.

A sandbox container differs from a runner in exactly two ways (design
section 9.6): it carries no credential of any kind — credential
delivery is a separate Phase 0 lane (section 9.7) that simply never
targets a sandbox — and it attaches to no network at all. The runner's
network is also "none" by default, fail-closed; the Phase 0 egress
allowlisting proxy passes its internal network name explicitly.
"""

import io
import posixpath
import secrets
import socket
import tarfile
import time

from vaibify.docker.dockerConnection import (
    _fmoduleGetDocker,
    _fnEnsureDockerHost,
    _I_CONTAINER_DEFAULT_UID,
    _I_CONTAINER_DEFAULT_GID,
)

__all__ = [
    "S_COUNCIL_LABEL",
    "S_COUNCIL_ROLE_LABEL",
    "S_ROLE_RUNNER",
    "S_ROLE_SANDBOX",
    "S_RUNNER_SNAPSHOT_ROOT",
    "S_ABSENCE_ABSENT",
    "S_ABSENCE_PRESENT",
    "S_ABSENCE_INDETERMINATE",
    "S_OUTCOME_DESTROYED",
    "S_OUTCOME_QUARANTINED",
    "fdockerCreateCouncilClient",
    "fdictBuildDefaultRunnerLimits",
    "fdictCreateRunnerContainer",
    "fnCopySnapshotIntoRunner",
    "fdictExecuteBoundedTurn",
    "fdictProbeRunnerAbsence",
    "fdictDestroyRunnerAndProveAbsence",
    "flistDiscoverLabeledRunners",
]

# Every council container carries this label; the value is the council
# registry's reservation identifier, which is what lets a restarted hub
# reconnect a surviving container to the reservation it was minted for.
S_COUNCIL_LABEL = "vaibify-council"
S_COUNCIL_ROLE_LABEL = "vaibify-council-role"
S_ROLE_RUNNER = "runner"
S_ROLE_SANDBOX = "sandbox"

# The council working tree: a sized tmpfs, the only place the snapshot
# copy lives and the only writable directory besides /tmp.
S_RUNNER_SNAPSHOT_ROOT = "/council"
S_RUNNER_SCRATCH_ROOT = "/tmp"

# The unprivileged council user, lock-stepped to the tar-stamping
# discipline in dockerConnection (and through it to the Dockerfile).
S_COUNCIL_CONTAINER_USER = (
    f"{_I_CONTAINER_DEFAULT_UID}:{_I_CONTAINER_DEFAULT_GID}"
)

# Explicit entrypoint override: bypasses whatever hub-oriented startup
# the project image declares. /bin/sh is the one interpreter every
# supported base image ships; the idle command merely keeps PID 1 alive
# between the copy and the turn.
LIST_COUNCIL_ENTRYPOINT = ["/bin/sh"]
LIST_COUNCIL_IDLE_COMMAND = ["-c", "sleep 2147483647"]

I_COUNCIL_DAEMON_TIMEOUT_SECONDS = 60
F_STREAM_POLL_SECONDS = 1.0
F_SNAPSHOT_COPY_BUDGET_SECONDS = 120.0
F_DEFAULT_TURN_WALL_CLOCK_SECONDS = 300.0
I_DEFAULT_TURN_OUTPUT_CAP_BYTES = 1_048_576

S_ABSENCE_ABSENT = "absent"
S_ABSENCE_PRESENT = "present"
S_ABSENCE_INDETERMINATE = "indeterminate"
S_OUTCOME_DESTROYED = "destroyed"
S_OUTCOME_QUARANTINED = "quarantined"


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


def fdictBuildDefaultRunnerLimits():
    """Build the default hard resource limits for one council container.

    The tmpfs sizes deliberately sum to well under the memory limit:
    tmpfs pages are charged to the container's memory cgroup, so a
    working tree as large as the memory limit would turn the disk bound
    into an out-of-memory kill and falsify the "disk-filling script hits
    the disk bound" claim.
    """
    return {
        "fCpuCount": 1.0,
        "iMemoryBytes": 1024 * 1024 * 1024,
        "iPidsLimit": 256,
        "iWorkingTreeBytes": 512 * 1024 * 1024,
        "iScratchBytes": 64 * 1024 * 1024,
    }


def _fnValidateRunnerLimits(dictLimits):
    """Refuse limit sets whose disk bound could not be the binding one."""
    iWritableBytes = (
        dictLimits["iWorkingTreeBytes"] + dictLimits["iScratchBytes"]
    )
    if iWritableBytes >= dictLimits["iMemoryBytes"]:
        raise ValueError(
            "Council runner limits refused: the writable tmpfs total "
            f"({iWritableBytes} bytes) must stay below the memory limit "
            f"({dictLimits['iMemoryBytes']} bytes). tmpfs pages are "
            "charged to the memory cgroup, so a larger working tree "
            "would replace the disk bound with an out-of-memory kill."
        )


def fdictCreateRunnerContainer(
    dockerCouncil, sImageReference, sReservationId,
    dictLimits=None, sNetworkName=None, bSandbox=False,
):
    """Create and start one disposable council container.

    Private PID, network and IPC namespaces (nothing shared with the
    host or any other container), all capabilities dropped,
    no-new-privileges, no devices, the daemon's default seccomp
    profile, the unprivileged council user, a read-only rootfs with two
    sized tmpfs mounts as the entire writable surface, and no mounts of
    any host path. ``sNetworkName`` is the Phase 0 egress-proxy seam: a
    runner may be attached to a named internal network whose only exit
    is the allowlisting proxy; the fail-closed default is no network. A
    sandbox refuses any network by contract.
    """
    if dictLimits is None:
        dictLimits = fdictBuildDefaultRunnerLimits()
    _fnValidateRunnerLimits(dictLimits)
    if bSandbox and sNetworkName is not None:
        raise ValueError(
            "A council sandbox attaches to no network at all (design "
            "section 9.6); refuse the sandbox rather than widen it."
        )
    sRole = S_ROLE_SANDBOX if bSandbox else S_ROLE_RUNNER
    sNetworkMode = sNetworkName if sNetworkName is not None else "none"
    sContainerName = f"vaibifyCouncil{sRole.capitalize()}{secrets.token_hex(6)}"
    dictTmpfsMounts = {
        S_RUNNER_SNAPSHOT_ROOT: (
            f"size={dictLimits['iWorkingTreeBytes']},"
            f"uid={_I_CONTAINER_DEFAULT_UID},"
            f"gid={_I_CONTAINER_DEFAULT_GID},mode=0700"
        ),
        S_RUNNER_SCRATCH_ROOT: (
            f"size={dictLimits['iScratchBytes']},mode=1777"
        ),
    }
    containerRunner = dockerCouncil.containers.create(
        sImageReference,
        entrypoint=LIST_COUNCIL_ENTRYPOINT,
        command=LIST_COUNCIL_IDLE_COMMAND,
        name=sContainerName,
        user=S_COUNCIL_CONTAINER_USER,
        cap_drop=["ALL"],
        security_opt=["no-new-privileges:true"],
        network_mode=sNetworkMode,
        ipc_mode="private",
        read_only=True,
        tmpfs=dictTmpfsMounts,
        mem_limit=dictLimits["iMemoryBytes"],
        memswap_limit=dictLimits["iMemoryBytes"],
        nano_cpus=int(dictLimits["fCpuCount"] * 1_000_000_000),
        pids_limit=dictLimits["iPidsLimit"],
        labels={
            S_COUNCIL_LABEL: sReservationId,
            S_COUNCIL_ROLE_LABEL: sRole,
        },
    )
    containerRunner.start()
    return {
        "sContainerId": containerRunner.id,
        "sContainerName": sContainerName,
        "sReservationId": sReservationId,
        "sRole": sRole,
    }


def _fnValidateSnapshotMember(infoMember):
    """Refuse a tar member that could land outside the snapshot root."""
    sNormalized = posixpath.normpath(infoMember.name)
    if posixpath.isabs(sNormalized) or sNormalized.startswith(".."):
        raise ValueError(
            "Snapshot tarball refused: member "
            f"{infoMember.name!r} escapes the extraction root."
        )
    if infoMember.issym() or infoMember.islnk():
        sLinkNormalized = posixpath.normpath(
            posixpath.join(posixpath.dirname(sNormalized),
                           infoMember.linkname)
        )
        if posixpath.isabs(infoMember.linkname) or \
                sLinkNormalized.startswith(".."):
            raise ValueError(
                "Snapshot tarball refused: link member "
                f"{infoMember.name!r} targets {infoMember.linkname!r} "
                "outside the extraction root."
            )


def _finfoStampCouncilOwnership(infoMember):
    """Stamp one tar member to the unprivileged council user.

    The same discipline as ``DockerConnection._finfoBuildTarEntry``,
    against the same constants: never let ``tarfile.TarInfo``'s native
    uid/gid default of 0 through, and clear the symbolic names so a
    numeric-id extractor cannot resolve ``root`` by name. The live
    extraction is performed by the unprivileged user (a non-root tar
    cannot chown), so these stamps are defense in depth — the record of
    intent that keeps a future writable-rootfs variant from resurrecting
    the root-owned-copy bug class.
    """
    infoMember.uid = _I_CONTAINER_DEFAULT_UID
    infoMember.gid = _I_CONTAINER_DEFAULT_GID
    infoMember.uname = ""
    infoMember.gname = ""
    return infoMember


def _fbufferRepackSnapshotStamped(baSnapshotTar):
    """Repack a snapshot tarball with every member validated and stamped."""
    bufferRepacked = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(baSnapshotTar), mode="r:*") \
            as fileTarSource:
        with tarfile.open(fileobj=bufferRepacked, mode="w") \
                as fileTarStamped:
            for infoMember in fileTarSource:
                _fnValidateSnapshotMember(infoMember)
                _finfoStampCouncilOwnership(infoMember)
                if infoMember.isreg():
                    fileTarStamped.addfile(
                        infoMember,
                        fileTarSource.extractfile(infoMember),
                    )
                else:
                    fileTarStamped.addfile(infoMember)
    bufferRepacked.seek(0)
    return bufferRepacked


def _ftStartExecStream(dockerCouncil, sContainerId, listCommand,
                            bStdin, sWorkingDirectory=None):
    """Create an exec as the council user; return (execId, rawSocket)."""
    dictExecCreated = dockerCouncil.api.exec_create(
        sContainerId, listCommand,
        stdin=bStdin, stdout=True, stderr=True,
        user=S_COUNCIL_CONTAINER_USER,
        workdir=sWorkingDirectory,
    )
    socketExec = dockerCouncil.api.exec_start(
        dictExecCreated["Id"], socket=True,
    )
    socketRaw = getattr(socketExec, "_sock", socketExec)
    socketRaw.settimeout(F_STREAM_POLL_SECONDS)
    return (dictExecCreated["Id"], socketRaw)


def _fnSendAllBounded(socketRaw, baPayload, fDeadlineMonotonic):
    """Send every byte before the deadline, or raise."""
    iOffset = 0
    while iOffset < len(baPayload):
        if time.monotonic() >= fDeadlineMonotonic:
            raise RuntimeError(
                "Council snapshot copy timed out while streaming the "
                "tarball into the container."
            )
        try:
            iOffset += socketRaw.send(
                baPayload[iOffset:iOffset + 65536]
            )
        except socket.timeout:
            continue


def _ftExtractFramePayloads(baPending):
    """Split complete Docker stream frames from a pending byte buffer.

    A non-TTY exec stream is multiplexed into 8-byte-header frames
    (stream type, three zero bytes, big-endian payload size). Returns
    the concatenated payload of every complete frame plus the
    still-incomplete remainder.
    """
    baPayload = b""
    while len(baPending) >= 8:
        iFrameSize = int.from_bytes(baPending[4:8], "big")
        if len(baPending) < 8 + iFrameSize:
            break
        baPayload += baPending[8:8 + iFrameSize]
        baPending = baPending[8 + iFrameSize:]
    return (baPayload, baPending)


def _fdictPumpBoundedExecStream(socketRaw, iOutputByteCap,
                                fDeadlineMonotonic):
    """Read an exec stream under an output-byte cap and a deadline.

    Host-side enforcement: the poll timeout keeps every blocking read
    bounded, so neither a silent process nor a stalled daemon can hold
    this loop past the deadline.
    """
    baCaptured = b""
    baPending = b""
    bOutputCapExceeded = False
    bDeadlineExceeded = False
    while True:
        if time.monotonic() >= fDeadlineMonotonic:
            bDeadlineExceeded = True
            break
        try:
            baChunk = socketRaw.recv(65536)
        except socket.timeout:
            continue
        except OSError:
            break
        if not baChunk:
            break
        baPending += baChunk
        tSplitFrames = _ftExtractFramePayloads(baPending)
        baCaptured += tSplitFrames[0]
        baPending = tSplitFrames[1]
        if len(baCaptured) > iOutputByteCap:
            baCaptured = baCaptured[:iOutputByteCap]
            bOutputCapExceeded = True
            break
    return {
        "baCaptured": baCaptured,
        "bOutputCapExceeded": bOutputCapExceeded,
        "bDeadlineExceeded": bDeadlineExceeded,
    }


def fnCopySnapshotIntoRunner(dockerCouncil, sContainerId, baSnapshotTar,
                             sDestinationDirectory=S_RUNNER_SNAPSHOT_ROOT):
    """Copy the sealed snapshot tarball into a council container.

    Every member is validated against extraction-root escape and
    stamped to the unprivileged council user, then the tarball is
    streamed through an exec's stdin and extracted INSIDE the container
    by that user. The archive endpoint (``put_archive``) cannot be used
    here — the daemon refuses it wholesale on a read-only-rootfs
    container — and the in-container extraction is what makes a
    root-owned copy impossible: a non-root ``tar`` cannot chown.
    """
    if not posixpath.isabs(sDestinationDirectory):
        raise ValueError(
            "Council snapshot destination must be an absolute container "
            f"path, got {sDestinationDirectory!r}."
        )
    bufferRepacked = _fbufferRepackSnapshotStamped(baSnapshotTar)
    fDeadlineMonotonic = time.monotonic() + F_SNAPSHOT_COPY_BUDGET_SECONDS
    tExecStream = _ftStartExecStream(
        dockerCouncil, sContainerId,
        ["tar", "-xf", "-", "-C", sDestinationDirectory],
        bStdin=True,
    )
    sExecId, socketRaw = tExecStream
    try:
        _fnSendAllBounded(
            socketRaw, bufferRepacked.getvalue(), fDeadlineMonotonic,
        )
        socketRaw.shutdown(socket.SHUT_WR)
        dictPumped = _fdictPumpBoundedExecStream(
            socketRaw, I_DEFAULT_TURN_OUTPUT_CAP_BYTES, fDeadlineMonotonic,
        )
    finally:
        socketRaw.close()
    if dictPumped["bDeadlineExceeded"]:
        raise RuntimeError(
            "Council snapshot copy exceeded its "
            f"{F_SNAPSHOT_COPY_BUDGET_SECONDS:.0f}s budget."
        )
    iExitCode = dockerCouncil.api.exec_inspect(sExecId).get("ExitCode")
    if iExitCode != 0:
        sOutputTail = dictPumped["baCaptured"][-2048:].decode(
            "utf-8", errors="replace",
        )
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
    dockerCouncil, sContainerId, listCommand,
    iOutputByteCap=None, fWallClockSeconds=None, sWorkingDirectory=None,
):
    """Execute one bounded command (the turn) in a council container.

    Output is captured under a byte cap and the whole turn under a
    wall-clock budget, both enforced host-side. Breaching either bound
    kills the CONTAINER, not just the exec — the runner is disposable
    and a turn that broke its budget has ended; the caller settles it
    with ``fdictDestroyRunnerAndProveAbsence``. ``iExitCode`` is
    ``None`` when no exit code could be established (a killed turn),
    never a fabricated zero.
    """
    if iOutputByteCap is None:
        iOutputByteCap = I_DEFAULT_TURN_OUTPUT_CAP_BYTES
    if fWallClockSeconds is None:
        fWallClockSeconds = F_DEFAULT_TURN_WALL_CLOCK_SECONDS
    fStartedMonotonic = time.monotonic()
    fDeadlineMonotonic = fStartedMonotonic + fWallClockSeconds
    tExecStream = _ftStartExecStream(
        dockerCouncil, sContainerId, listCommand,
        bStdin=False, sWorkingDirectory=sWorkingDirectory,
    )
    sExecId, socketRaw = tExecStream
    try:
        dictPumped = _fdictPumpBoundedExecStream(
            socketRaw, iOutputByteCap, fDeadlineMonotonic,
        )
    finally:
        socketRaw.close()
    bBreached = (
        dictPumped["bOutputCapExceeded"] or dictPumped["bDeadlineExceeded"]
    )
    if bBreached:
        _fnKillContainerQuietly(dockerCouncil, sContainerId)
    iExitCode = None
    try:
        iExitCode = dockerCouncil.api.exec_inspect(sExecId).get("ExitCode")
    except Exception:
        pass
    return {
        "iExitCode": iExitCode,
        "sOutput": dictPumped["baCaptured"].decode(
            "utf-8", errors="replace",
        ),
        "bOutputCapExceeded": dictPumped["bOutputCapExceeded"],
        "bWallClockExceeded": dictPumped["bDeadlineExceeded"],
        "fElapsedSeconds": time.monotonic() - fStartedMonotonic,
    }


def fdictProbeRunnerAbsence(dockerCouncil, sContainerId):
    """Positively establish whether a council container is gone.

    Three distinct answers, because "absent" and "the daemon errored"
    must never be conflated: ``absent`` is the daemon POSITIVELY
    answering 404 for the identifier; ``present`` is the daemon
    returning the container; anything else — timeout, transport error,
    daemon fault — is ``indeterminate``, which callers must treat as
    quarantine, never as completion.
    """
    moduleDocker = _fmoduleGetDocker()
    try:
        dockerCouncil.api.inspect_container(sContainerId)
        return {"sAnswer": S_ABSENCE_PRESENT, "sDetail": ""}
    except moduleDocker.errors.NotFound:
        return {"sAnswer": S_ABSENCE_ABSENT, "sDetail": ""}
    except Exception as error:
        return {
            "sAnswer": S_ABSENCE_INDETERMINATE,
            "sDetail": f"{type(error).__name__}: {error}",
        }


def fdictDestroyRunnerAndProveAbsence(dockerCouncil, sContainerId):
    """Destroy a council container and settle only on proven absence.

    Namespace destruction is the containment: force-removal kills every
    process in the container's PID namespace, detached descendants
    included. The transaction settles as ``destroyed`` only when the
    absence probe positively answers ``absent``; a daemon error during
    removal or an indeterminate probe answer yields ``quarantined`` —
    the caller must keep the reservation visible and retry, never
    report a clean completion.
    """
    moduleDocker = _fmoduleGetDocker()
    try:
        dockerCouncil.api.remove_container(sContainerId, force=True, v=True)
    except moduleDocker.errors.NotFound:
        pass
    except Exception as error:
        return {
            "sOutcome": S_OUTCOME_QUARANTINED,
            "sReason": (
                "Removal did not complete; the container may still be "
                f"running: {type(error).__name__}: {error}"
            ),
            "dictProbe": {
                "sAnswer": S_ABSENCE_INDETERMINATE,
                "sDetail": "removal failed before the probe",
            },
        }
    dictProbe = fdictProbeRunnerAbsence(dockerCouncil, sContainerId)
    if dictProbe["sAnswer"] == S_ABSENCE_ABSENT:
        return {"sOutcome": S_OUTCOME_DESTROYED, "sReason": "",
                "dictProbe": dictProbe}
    return {
        "sOutcome": S_OUTCOME_QUARANTINED,
        "sReason": (
            "The absence probe answered "
            f"{dictProbe['sAnswer']!r}, not {S_ABSENCE_ABSENT!r}; the "
            "container is not proven gone."
        ),
        "dictProbe": dictProbe,
    }


def flistDiscoverLabeledRunners(dockerCouncil):
    """Discover every council-labeled container, running or not.

    The crash-recovery entry point: a restarted hub calls this before
    any new council starts, matches each survivor's reservation
    identifier against the council registry, and settles each one
    through ``fdictDestroyRunnerAndProveAbsence``.
    """
    listContainers = dockerCouncil.containers.list(
        all=True, filters={"label": S_COUNCIL_LABEL},
    )
    listDiscovered = []
    for containerFound in listContainers:
        dictLabels = containerFound.labels or {}
        listDiscovered.append({
            "sContainerId": containerFound.id,
            "sContainerName": containerFound.name,
            "sReservationId": dictLabels.get(S_COUNCIL_LABEL, ""),
            "sRole": dictLabels.get(S_COUNCIL_ROLE_LABEL, ""),
            "sStatus": containerFound.status,
        })
    return listDiscovered
