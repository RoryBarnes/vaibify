"""Pure values for a disposable container's lifecycle.

A *disposable* container is one vaibify creates for a single bounded
job, never hands to a researcher, and destroys with proof when the job
ends. Two lanes want one: the PROOF Level 3 shadow rerun, which
re-executes a workflow from its pinned image so the attestation
describes an independent reproduction rather than a re-run in place;
and the Agent Council, which gives every provider turn a fresh
container and destroys the whole namespace afterward.

This module holds only the PURE half of that lifecycle — the
containment vocabulary (labels, roles, absence answers, destruction
outcomes), the resource-limit validation, the SDK create-specification
composition, and the tar validation and ownership stamping. Every
Docker-SDK call lives in ``disposableContainer``, the single SDK
authority for this machinery, so there is one place a daemon can be
reached and one place a posture can be composed.

**Why the posture is composed here and not at the call site.** The
create specification fixes private PID and IPC namespaces, all
capabilities dropped, no-new-privileges, no devices, no host mounts,
the unprivileged container user, and hard memory / CPU / PID bounds.
None of those change what a workflow can COMPUTE; they bound what a
compromised one can reach. A caller chooses the image, the limits, the
network and whether the root filesystem is writable, and can choose
nothing else — so a new call site cannot quietly weaken the posture by
forgetting a keyword.

**Two knobs are deliberately open, and each has a reason.**

``bReadOnlyRootFilesystem`` is False by default because the shadow
rerun's whole claim is that it reproduces what a third party running
``reproduce.sh`` would get, and that script's ``docker run`` carries an
ordinary writable root. A read-only shadow would refuse workflows the
attested procedure accepts — a false divergence, which is worse than
the mutation it prevents, because the researcher is blocked by
vaibify's extra constraint and the message cannot say so. The council's
runner passes True, where the container is handed to a model rather
than to the researcher's own pinned pipeline.

``sNetworkName`` defaults to no network at all. That IS fail-closed:
every current caller wants isolation, and the seam exists so a lane
that must reach a network names the network it may reach.

**What is NOT bounded, stated rather than implied.** The writable
surface has no hard size limit. A tmpfs would give one, and it would be
the wrong one: tmpfs pages are charged to the container's memory
cgroup, so a workflow writing more output than the memory limit would
die of an out-of-memory kill reported as a disk problem, and a bound
small enough to be safe would refuse ordinary scientific output.
``reproduce.sh``'s own ``docker run`` is unbounded the same way. The
honest control is a pre-flight against the daemon's free space, which
lives with the caller that knows how much the job will write.
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
from vaibify.docker import daemonCapacity


__all__ = [
    "S_DISPOSABLE_LABEL",
    "S_DISPOSABLE_ROLE_LABEL",
    "S_DISPOSABLE_RESOURCE_LABEL",
    "S_DISPOSABLE_CONTAINER_USER",
    "S_SCRATCH_ROOT",
    "S_ABSENCE_ABSENT",
    "S_ABSENCE_PRESENT",
    "S_ABSENCE_INDETERMINATE",
    "S_OUTCOME_DESTROYED",
    "S_OUTCOME_QUARANTINED",
    "F_STREAM_POLL_SECONDS",
    "I_DEFAULT_OUTPUT_CAP_BYTES",
    "F_DEFAULT_WALL_CLOCK_SECONDS",
    "fdictBuildDefaultLimits",
    "fdictComposeCreateSpecification",
    "fbufferRepackArchiveStamped",
    "fnSendAllBounded",
    "fdictPumpBoundedExecStream",
]


# Every disposable container carries this label; the value is the
# reservation identifier its creator minted, which is what lets a
# restarted hub reconnect a survivor to the reservation it came from --
# and what the handle-keyed destruction verifies before it removes
# anything.
S_DISPOSABLE_LABEL = "vaibify-disposable"
S_DISPOSABLE_ROLE_LABEL = "vaibify-disposable-role"
# The project container this disposable serves, by the NAME that is the
# lease principal. Without it a startup reconcile can only sweep by the
# bare disposable label, which is daemon-wide: a second hub booting on
# the same daemon would destroy a live peer's containers because nothing
# on the container says whose they were.
S_DISPOSABLE_RESOURCE_LABEL = "vaibify-disposable-resource"

# The unprivileged container user, lock-stepped to the tar-stamping
# discipline in dockerConnection (and through it to the Dockerfile).
S_DISPOSABLE_CONTAINER_USER = (
    f"{_I_CONTAINER_DEFAULT_UID}:{_I_CONTAINER_DEFAULT_GID}"
)

# The one writable mount the posture always supplies, sized and charged
# to the memory cgroup. Scratch only -- never the job's workspace.
S_SCRATCH_ROOT = "/tmp"

# Explicit entrypoint override: bypasses whatever hub-oriented startup
# the project image declares. /bin/sh is the one interpreter every
# supported base image ships; the idle command merely keeps PID 1 alive
# between the copy-in and the job.
LIST_IDLE_ENTRYPOINT = ["/bin/sh"]
LIST_IDLE_COMMAND = ["-c", "sleep 2147483647"]

S_ABSENCE_ABSENT = "absent"
S_ABSENCE_PRESENT = "present"
S_ABSENCE_INDETERMINATE = "indeterminate"
S_OUTCOME_DESTROYED = "destroyed"
S_OUTCOME_QUARANTINED = "quarantined"

F_STREAM_POLL_SECONDS = 1.0
I_DEFAULT_OUTPUT_CAP_BYTES = 1_048_576
F_DEFAULT_WALL_CLOCK_SECONDS = 3600.0


def fdictBuildDefaultLimits(dictCapacity=None):
    """Build the default hard resource limits for one disposable container.

    ``dictCapacity`` is what THIS daemon can give (see
    ``daemonCapacity``). Omitting it yields the floors, so a caller with
    no daemon to measure is never worse off than one that measured a
    small machine.
    """
    dictResolved = (
        dictCapacity or daemonCapacity.fdictFloorDaemonCapacity())
    return {
        "fCpuCount": 2.0,
        "iMemoryBytes": dictResolved["iContainerMemoryBytes"],
        "iPidsLimit": 512,
        "iScratchBytes": dictResolved["iContainerScratchBytes"],
    }


def _fnValidateLimits(dictLimits):
    """Refuse limits whose scratch tmpfs could become the memory bound."""
    if dictLimits["iScratchBytes"] >= dictLimits["iMemoryBytes"]:
        raise ValueError(
            "Disposable container limits refused: the scratch tmpfs "
            f"({dictLimits['iScratchBytes']} bytes) must stay below the "
            f"memory limit ({dictLimits['iMemoryBytes']} bytes). tmpfs "
            "pages are charged to the memory cgroup, so a larger scratch "
            "mount would replace the disk bound with an out-of-memory "
            "kill."
        )
    if dictLimits["fCpuCount"] <= 0 or dictLimits["iPidsLimit"] <= 0:
        raise ValueError(
            "Disposable container limits refused: the CPU and PID bounds "
            "must both be positive, got "
            f"{dictLimits['fCpuCount']!r} and "
            f"{dictLimits['iPidsLimit']!r}."
        )


def fdictComposeCreateSpecification(
    sImageReference, sReservationId, sRole,
    dictLimits=None, sNetworkName=None, sResourceName="",
    bReadOnlyRootFilesystem=False,
):
    """Compose the SDK create keywords for one disposable container.

    PURE: validates the limits and RETURNS the values; the container
    module is the only place that hands them to the SDK. See the module
    docstring for which parts of the posture a caller may choose and
    why the rest is fixed here. ``sResourceName`` names the project
    container this disposable serves; an empty name is permitted and
    degrades to an unattributable survivor that any reconcile sweeps,
    which is the fail-closed direction because it preserves crash
    recovery.
    """
    if dictLimits is None:
        dictLimits = fdictBuildDefaultLimits()
    _fnValidateLimits(dictLimits)
    sContainerName = (
        f"vaibifyDisposable{sRole.capitalize()}{secrets.token_hex(6)}")
    return {
        "sContainerName": sContainerName,
        "sRole": sRole,
        "dictCreateKeywords": {
            "entrypoint": LIST_IDLE_ENTRYPOINT,
            "command": LIST_IDLE_COMMAND,
            "name": sContainerName,
            "user": S_DISPOSABLE_CONTAINER_USER,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "network_mode": (
                sNetworkName if sNetworkName is not None else "none"),
            "ipc_mode": "private",
            "read_only": bool(bReadOnlyRootFilesystem),
            "tmpfs": {
                S_SCRATCH_ROOT: (
                    f"size={dictLimits['iScratchBytes']},mode=1777"
                ),
            },
            "mem_limit": dictLimits["iMemoryBytes"],
            "memswap_limit": dictLimits["iMemoryBytes"],
            "nano_cpus": int(dictLimits["fCpuCount"] * 1_000_000_000),
            "pids_limit": dictLimits["iPidsLimit"],
            "labels": {
                S_DISPOSABLE_LABEL: sReservationId,
                S_DISPOSABLE_ROLE_LABEL: sRole,
                S_DISPOSABLE_RESOURCE_LABEL: sResourceName,
            },
        },
    }


def _fnValidateArchiveMember(infoMember):
    """Refuse a tar member that could land outside the extraction root."""
    sNormalized = posixpath.normpath(infoMember.name)
    if posixpath.isabs(sNormalized) or sNormalized.startswith(".."):
        raise ValueError(
            "Archive refused: member "
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
                "Archive refused: link member "
                f"{infoMember.name!r} targets {infoMember.linkname!r} "
                "outside the extraction root."
            )


def _finfoStampContainerOwnership(infoMember):
    """Stamp one tar member to the unprivileged container user.

    The same discipline as ``DockerConnection._finfoBuildTarEntry``,
    against the same constants: never let ``tarfile.TarInfo``'s native
    uid/gid default of 0 through, and clear the symbolic names so a
    numeric-id extractor cannot resolve ``root`` by name. Without this
    the copy lands root-owned and the unprivileged user the job runs as
    cannot write its own workspace -- the file-ownership trap this
    repository has shipped once.
    """
    infoMember.uid = _I_CONTAINER_DEFAULT_UID
    infoMember.gid = _I_CONTAINER_DEFAULT_GID
    infoMember.uname = ""
    infoMember.gname = ""
    return infoMember


def fbufferRepackArchiveStamped(baArchiveTar, sPathPrefix=""):
    """Repack a tarball with every member validated, stamped and prefixed.

    ``sPathPrefix`` relocates the whole archive under one directory. It
    is validated as a relative path for the same reason every member is:
    an absolute or escaping prefix would place the archive wherever the
    caller named.

    **Every parent directory is synthesized, not assumed.** A tarball
    is free to name ``repo/data/file.txt`` without naming ``repo`` or
    ``repo/data``, and both the daemon's archive endpoint and ``tar``
    will happily create the gap themselves -- ROOT-owned, because
    nothing in the stream said otherwise. The container user then owns
    its files and cannot create a sibling beside them, which is the
    file-ownership trap this repository has shipped once, arriving
    through a directory instead of a file. Verified live against the
    daemon: without the synthesized parents, a ``1000:1000`` file sat
    inside a ``root:root`` directory and the first write into that
    directory was refused.
    """
    sPrefix = _fsValidateArchivePrefix(sPathPrefix)
    bufferRepacked = io.BytesIO()
    setEmittedDirectories = set()
    with tarfile.open(fileobj=io.BytesIO(baArchiveTar), mode="r:*") \
            as fileTarSource:
        with tarfile.open(fileobj=bufferRepacked, mode="w") \
                as fileTarStamped:
            for infoMember in fileTarSource:
                _fnValidateArchiveMember(infoMember)
                _finfoStampContainerOwnership(infoMember)
                if sPrefix:
                    infoMember.name = posixpath.join(
                        sPrefix, infoMember.name)
                _fnEmitMissingParentDirectories(
                    fileTarStamped, infoMember.name, setEmittedDirectories)
                if infoMember.isdir():
                    if infoMember.name.rstrip("/") in setEmittedDirectories:
                        continue
                    setEmittedDirectories.add(infoMember.name.rstrip("/"))
                    infoMember.mode = infoMember.mode | 0o700
                if infoMember.isreg():
                    fileTarStamped.addfile(
                        infoMember,
                        fileTarSource.extractfile(infoMember),
                    )
                else:
                    fileTarStamped.addfile(infoMember)
    bufferRepacked.seek(0)
    return bufferRepacked


def _fsValidateArchivePrefix(sPathPrefix):
    """Return the normalized relative prefix, or refuse one that escapes."""
    sPrefix = posixpath.normpath(sPathPrefix) if sPathPrefix else ""
    if sPrefix in (".", "/"):
        return ""
    if sPrefix and (posixpath.isabs(sPrefix) or sPrefix.startswith("..")):
        raise ValueError(
            f"Archive prefix {sPathPrefix!r} is refused: it must be a "
            "relative path that stays inside the extraction root."
        )
    return sPrefix


def _fnEmitMissingParentDirectories(fileTarStamped, sMemberPath,
                                    setEmittedDirectories):
    """Emit a stamped directory member for every unseen parent of a path."""
    listComponents = posixpath.dirname(sMemberPath.rstrip("/")).split("/")
    sAccumulated = ""
    for sComponent in listComponents:
        if not sComponent:
            continue
        sAccumulated = (
            posixpath.join(sAccumulated, sComponent)
            if sAccumulated else sComponent)
        if sAccumulated in setEmittedDirectories:
            continue
        setEmittedDirectories.add(sAccumulated)
        fileTarStamped.addfile(
            _finfoStampContainerOwnership(
                _finfoBuildDirectoryEntry(sAccumulated)))


def _finfoBuildDirectoryEntry(sDirectoryPath, iMode=0o755):
    """Build one directory tar member; the caller stamps its ownership."""
    infoDirectory = tarfile.TarInfo(name=sDirectoryPath)
    infoDirectory.type = tarfile.DIRTYPE
    infoDirectory.mode = iMode
    return infoDirectory


def fnSendAllBounded(socketRaw, baPayload, fDeadlineMonotonic):
    """Send every byte before the deadline, or raise.

    Operates on an ALREADY-OPENED exec socket; the exec that produces
    the socket lives in the container module.
    """
    iOffset = 0
    while iOffset < len(baPayload):
        if time.monotonic() >= fDeadlineMonotonic:
            raise RuntimeError(
                "Disposable container write timed out while streaming "
                "into the container."
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


def fdictPumpBoundedExecStream(socketRaw, iOutputByteCap,
                               fDeadlineMonotonic):
    """Read an exec stream under an output-byte cap and a deadline.

    Host-side enforcement: the poll timeout keeps every blocking read
    bounded, so neither a silent process nor a stalled daemon can hold
    this loop past the deadline. Operates on an ALREADY-OPENED exec
    socket; the exec that produces the socket lives in the container
    module.
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
