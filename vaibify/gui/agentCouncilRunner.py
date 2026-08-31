"""Pure runner/sandbox lifecycle values for the Agent Council.

Phase 0 of the Agent Council (design/agentCouncil.md sections 2.6 and
9.6): each provider turn — and each sandboxed script execution — gets a
fresh disposable container, and the WHOLE container is destroyed
afterward, settled only after an absence probe positively establishes
the container is gone. Process exit is never the containment proof; a
``setsid``-detached descendant dies with the process namespace it
detached inside, and namespace destruction is the only claim the
council makes about quietness.

Since remediation R4 this module holds ONLY the pure half of that
lifecycle: the containment vocabulary (labels, roles, absence answers,
destruction outcomes), the resource-limit validation, the SDK
create-specification composition, the tar validation/ownership-stamping
discipline, and the socket pumps that drive an already-opened exec
stream. Every Docker-SDK call — client creation, container create and
start, the exec that PRODUCES a stream socket, kill, absence probe,
destruction, discovery — lives in ``agentCouncilDockerGateway``, the
single council SDK authority (``tests/testCouncilGatewayAuthority.py``
fails the build if SDK reach reappears here).

Two empirical facts shape the create specification and are pinned by
the live suite (``tests/testAgentCouncilRunnerLive.py``):

- the Docker archive endpoint (``put_archive`` / ``docker cp``) refuses
  ANY write into a read-only-rootfs container, tmpfs target included
  ("container rootfs is marked read-only"), so the snapshot is streamed
  through an exec's stdin and untarred by the unprivileged user inside
  the container — which also makes a root-owned copy impossible by
  construction, because a non-root ``tar`` cannot chown;
- tmpfs pages are charged to the container's memory cgroup, so the
  tmpfs sizes must stay below the memory limit or the "disk" bound
  silently becomes the memory bound; ``_fnValidateRunnerLimits``
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
    _I_CONTAINER_DEFAULT_UID,
    _I_CONTAINER_DEFAULT_GID,
)
from vaibify.gui import agentCouncilCapacity

__all__ = [
    "S_COUNCIL_LABEL",
    "S_COUNCIL_ROLE_LABEL",
    "S_COUNCIL_RESOURCE_LABEL",
    "S_ROLE_RUNNER",
    "S_ROLE_SANDBOX",
    "S_RUNNER_SNAPSHOT_ROOT",
    "S_ABSENCE_ABSENT",
    "S_ABSENCE_PRESENT",
    "S_ABSENCE_INDETERMINATE",
    "S_OUTCOME_DESTROYED",
    "S_OUTCOME_QUARANTINED",
    "fdictBuildDefaultRunnerLimits",
    "fdictComposeRunnerCreateSpecification",
    "fbufferRepackSnapshotStamped",
    "fnSendAllBounded",
    "fdictPumpBoundedExecStream",
    "fbaBuildStampedFileTarball",
]

# Every council container carries this label; the value is the council
# registry's reservation identifier, which is what lets a restarted hub
# reconnect a surviving container to the reservation it was minted for
# — and what the gateway's handle-keyed destruction verifies before it
# removes anything.
S_COUNCIL_LABEL = "vaibify-council"
S_COUNCIL_ROLE_LABEL = "vaibify-council-role"
# The project container this council serves, by the NAME that is the
# lease principal — the same key ``containerLock`` flocks and
# ``dictContainerOwners`` uses. Without it the startup reconcile can
# only sweep by the bare council label, which is daemon-wide: a second
# hub booting on the same daemon destroyed a live peer's runners
# because nothing on the container said whose they were (2026-08-21).
S_COUNCIL_RESOURCE_LABEL = "vaibify-council-resource"
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

F_STREAM_POLL_SECONDS = 1.0
F_SNAPSHOT_COPY_BUDGET_SECONDS = 120.0
# One hour. Was 300s, which killed every agent that explored the
# repository with tool calls: a live opus turn was destroyed mid-loop
# after 52 assistant messages and 32 tool results, and reported as an
# unexplained empty result. The researcher runs opus turns beyond an
# hour routinely (2026-08-24), so five minutes was never the right
# shape for this work — it was a placeholder nobody had measured
# against a real deliberation.
F_DEFAULT_TURN_WALL_CLOCK_SECONDS = 3600.0
I_DEFAULT_TURN_OUTPUT_CAP_BYTES = 1_048_576

S_ABSENCE_ABSENT = "absent"
S_ABSENCE_PRESENT = "present"
S_ABSENCE_INDETERMINATE = "indeterminate"
S_OUTCOME_DESTROYED = "destroyed"
S_OUTCOME_QUARANTINED = "quarantined"


def fdictBuildDefaultRunnerLimits(dictCapacity=None):
    """Build the default hard resource limits for one council container.

    The tmpfs sizes deliberately sum to well under the memory limit:
    tmpfs pages are charged to the container's memory cgroup, so a
    working tree as large as the memory limit would turn the disk bound
    into an out-of-memory kill and falsify the "disk-filling script hits
    the disk bound" claim.

    ``dictCapacity`` is what THIS daemon can give (see
    agentCouncilCapacity). Omitting it yields the floors — the fixed
    limits these were before they were machine-scaled — so a caller
    with no daemon to measure is never worse off. The working tree is
    taken from the capacity rather than recomputed, because it IS the
    largest snapshot the snapshot bounds will admit: two derivations of
    that number would be two chances for the copy-in to overflow the
    tmpfs it was sized against.
    """
    dictResolved = (
        dictCapacity or agentCouncilCapacity.fdictFloorCouncilCapacity())
    return {
        "fCpuCount": 1.0,
        "iMemoryBytes": dictResolved["iRunnerMemoryBytes"],
        "iPidsLimit": 256,
        "iWorkingTreeBytes": dictResolved["iRunnerWorkingTreeBytes"],
        "iScratchBytes": dictResolved["iRunnerScratchBytes"],
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


def _fnValidateSandboxIsolation(bSandbox, sNetworkName, dictEnvironment,
                                listDnsServers, listDnsOptions):
    """Refuse a sandbox specification that widens the sandbox contract."""
    if bSandbox and sNetworkName is not None:
        raise ValueError(
            "A council sandbox attaches to no network at all (design "
            "section 9.6); refuse the sandbox rather than widen it."
        )
    if bSandbox and (dictEnvironment or listDnsServers or listDnsOptions):
        raise ValueError(
            "A council sandbox carries no credential, no network and no "
            "resolver (design section 9.6); refuse it rather than wire "
            "egress or environment into it."
        )


def fdictComposeRunnerCreateSpecification(
    sImageReference, sReservationId,
    dictLimits=None, sNetworkName=None, bSandbox=False,
    dictEnvironment=None, listDnsServers=None, listDnsOptions=None,
    sResourceName="",
):
    """Compose the SDK create keywords for one disposable council container.

    PURE: validates the limits and the sandbox contract and RETURNS the
    values; the gateway is the only module that hands them to the SDK.
    The posture is fixed here so no caller can weaken it: private PID,
    network and IPC namespaces, all capabilities dropped,
    no-new-privileges, no devices, the daemon's default seccomp profile,
    the unprivileged council user, a read-only rootfs with two sized
    tmpfs mounts as the entire writable surface, and no mounts of any
    host path. ``sNetworkName`` is the egress-proxy seam; the
    fail-closed default is no network, and a sandbox refuses any
    network, environment or resolver by contract. ``sResourceName``
    names the project container this runner serves; it is stamped as a
    label so a peer hub's startup reconcile can tell whose runner it is
    rather than sweeping by the daemon-wide council label. An empty
    name is permitted and degrades to exactly the old behaviour — the
    survivor is unattributable, so a reconcile sweeps it — which is the
    fail-closed direction, because it preserves crash recovery.
    """
    if dictLimits is None:
        dictLimits = fdictBuildDefaultRunnerLimits()
    _fnValidateRunnerLimits(dictLimits)
    _fnValidateSandboxIsolation(
        bSandbox, sNetworkName, dictEnvironment, listDnsServers,
        listDnsOptions)
    sRole = S_ROLE_SANDBOX if bSandbox else S_ROLE_RUNNER
    sContainerName = (
        f"vaibifyCouncil{sRole.capitalize()}{secrets.token_hex(6)}")
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
    return {
        "sContainerName": sContainerName,
        "sRole": sRole,
        "dictCreateKeywords": {
            "entrypoint": LIST_COUNCIL_ENTRYPOINT,
            "command": LIST_COUNCIL_IDLE_COMMAND,
            "name": sContainerName,
            "user": S_COUNCIL_CONTAINER_USER,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "network_mode": (
                sNetworkName if sNetworkName is not None else "none"),
            "ipc_mode": "private",
            "read_only": True,
            "tmpfs": dictTmpfsMounts,
            "mem_limit": dictLimits["iMemoryBytes"],
            "memswap_limit": dictLimits["iMemoryBytes"],
            "nano_cpus": int(dictLimits["fCpuCount"] * 1_000_000_000),
            "pids_limit": dictLimits["iPidsLimit"],
            "labels": {
                S_COUNCIL_LABEL: sReservationId,
                S_COUNCIL_ROLE_LABEL: sRole,
                S_COUNCIL_RESOURCE_LABEL: sResourceName,
            },
            "environment": dictEnvironment or None,
            "dns": list(listDnsServers) if listDnsServers else None,
            "dns_opt": list(listDnsOptions) if listDnsOptions else None,
        },
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


def fbufferRepackSnapshotStamped(baSnapshotTar):
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


def fnSendAllBounded(socketRaw, baPayload, fDeadlineMonotonic):
    """Send every byte before the deadline, or raise.

    Operates on an ALREADY-OPENED exec socket; the exec that produces
    the socket lives in the gateway.
    """
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


# How long a turn may emit NOTHING before the pump calls it stopped.
# The default lives in the campaign settings; this is the fallback for
# direct library callers, matching it.
F_DEFAULT_TURN_STALL_SECONDS = 600.0


def fdictPumpBoundedExecStream(socketRaw, iOutputByteCap,
                               fDeadlineMonotonic, fStallSeconds=None):
    """Read an exec stream under an output-byte cap and a deadline.

    Host-side enforcement: the poll timeout keeps every blocking read
    bounded, so neither a silent process nor a stalled daemon can hold
    this loop past the deadline. Operates on an ALREADY-OPENED exec
    socket; the exec that produces the socket lives in the gateway.
    """
    baCaptured = b""
    baPending = b""
    bOutputCapExceeded = False
    bDeadlineExceeded = False
    bStalled = False
    if fStallSeconds is None:
        fStallSeconds = F_DEFAULT_TURN_STALL_SECONDS
    fLastOutputMonotonic = time.monotonic()
    while True:
        if time.monotonic() >= fDeadlineMonotonic:
            bDeadlineExceeded = True
            break
        # SILENCE, not lack of progress. A model narrating in a loop
        # emits bytes and achieves nothing, and this cannot tell the
        # difference — but a turn whose provider connection died, whose
        # CLI wedged, or whose container lost its network goes quiet,
        # and that is the failure a four-hour budget would otherwise
        # hide until the afternoon was gone (2026-08-30).
        if time.monotonic() - fLastOutputMonotonic >= fStallSeconds:
            bStalled = True
            break
        try:
            baChunk = socketRaw.recv(65536)
        except socket.timeout:
            continue
        except OSError:
            break
        if not baChunk:
            break
        # Stamped on BYTES ARRIVING, not on frames decoded: a partial
        # frame is still the far end being alive, and requiring a whole
        # decoded frame would call a slow large message a stall.
        fLastOutputMonotonic = time.monotonic()
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
        "bStalled": bStalled,
        "fStallSeconds": fStallSeconds,
    }


def fbaBuildStampedFileTarball(
    sDirectoryBasename, sFileBasename, baContent,
    iFileMode=0o600, iDirectoryMode=0o700,
):
    """Build a one-file-under-one-directory tarball, ownership-stamped.

    The runner-backend credential delivery (design section 9.7) needs a
    tiny tarball to hand the copy-in path. Both entries are stamped to
    the unprivileged council user through the same
    ``_finfoStampCouncilOwnership`` discipline the snapshot repack uses,
    so ``tarfile.TarInfo``'s native uid/gid default of 0 can never leak
    a root-owned credential — the file-ownership trap this repository has
    shipped once. The single-file shape keeps this builder generic: the
    Claude-specific directory and file names arrive as arguments.
    """
    bufferTar = io.BytesIO()
    with tarfile.open(fileobj=bufferTar, mode="w") as fileTar:
        infoDirectory = tarfile.TarInfo(name=sDirectoryBasename)
        infoDirectory.type = tarfile.DIRTYPE
        infoDirectory.mode = iDirectoryMode
        fileTar.addfile(_finfoStampCouncilOwnership(infoDirectory))
        infoFile = tarfile.TarInfo(
            name=posixpath.join(sDirectoryBasename, sFileBasename))
        infoFile.size = len(baContent)
        infoFile.mode = iFileMode
        fileTar.addfile(
            _finfoStampCouncilOwnership(infoFile), io.BytesIO(baContent))
    return bufferTar.getvalue()
