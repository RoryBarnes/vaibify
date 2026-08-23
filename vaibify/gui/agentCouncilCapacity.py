"""What THIS machine allows a council to carry.

The snapshot bounds and the runner's resource limits used to be five
constants chosen for the smallest machine anyone might run on. That is
the right *floor* and the wrong *ceiling*: a researcher with a 64 GB
workstation was refused a repository their machine could hold twenty
times over, and the refusal said nothing about why the number was what
it was. This module answers "what will fit here", once, from the two
figures that actually bind — and every caller keeps the old constant as
the guaranteed minimum, so a machine that cannot be measured behaves
exactly as it did before.

**Two figures, and they are not the same figure.** Which one binds
depends on WHOSE memory holds the bytes:

- The **per-member** bound is a HOST bound. ``agentCouncilContext``
  materialises each file once, in the hub process, to hash and
  re-archive it. That is the researcher's own RAM.
- The **total** bound and the runner limits are DAEMON bounds. The
  snapshot is copied into a tmpfs inside each runner, and tmpfs pages
  are charged to that container's memory cgroup, so the snapshot total
  can never exceed the working tree, which can never approach the
  runner's memory limit, which is a slice of what the daemon has.

Conflating them is the trap this module exists to avoid, and it is
invisible on Linux, where the daemon shares the host kernel and the two
numbers are equal. On macOS they differ by whatever the researcher gave
Docker Desktop.

**Scaling only ever raises, never lowers.** Every resolved bound is
``max(floor, computed)``. A machine smaller than the floor gets the
floor: the floor is what the design was reviewed against, and shrinking
below it would make a repository that snapshotted yesterday refuse
today for a reason no message could usefully explain.

**The per-member bound also has a ceiling, and it is a judgement.** Past
``I_CEILING_SNAPSHOT_MEMBER_BYTES`` a single file is a dataset rather
than context by any reading, whatever the machine could technically
hold; a participant cannot read a gigabyte of one file usefully. That
judgement is the researcher's to override for a specific file — by
excluding it, not by raising the bound — which is why the ceiling is a
constant here and an exclusion is a choice at convene time.
"""

__all__ = [
    "I_FLOOR_SNAPSHOT_FILE_COUNT",
    "I_FLOOR_SNAPSHOT_MEMBER_BYTES",
    "I_FLOOR_SNAPSHOT_TOTAL_BYTES",
    "I_CEILING_SNAPSHOT_MEMBER_BYTES",
    "I_ASSUMED_CONCURRENT_RUNNERS",
    "fdictResolveCouncilCapacity",
    "fdictFloorCouncilCapacity",
    "fiReadHostMemoryBytes",
]

import os
import sys


# The floors: the bounds every supported machine honours, and the ones
# the design was reviewed against. Identical to the constants these
# replaced, deliberately, so nothing gets smaller for anybody.
I_FLOOR_SNAPSHOT_FILE_COUNT = 20000
I_FLOOR_SNAPSHOT_MEMBER_BYTES = 64 * 1024 * 1024
I_FLOOR_SNAPSHOT_TOTAL_BYTES = 512 * 1024 * 1024
I_FLOOR_RUNNER_MEMORY_BYTES = 1024 * 1024 * 1024
I_FLOOR_RUNNER_SCRATCH_BYTES = 64 * 1024 * 1024

# See the module docstring: past this a member is a dataset, not
# context, however much memory the machine has.
I_CEILING_SNAPSHOT_MEMBER_BYTES = 1024 * 1024 * 1024

# The hub holds ONE member in memory at a time, so the per-member bound
# is a small fraction of host RAM rather than a large one: the hub is
# also serving the dashboard, and a member near the whole machine would
# trade a clean refusal for a swap storm.
_I_HOST_MEMORY_DIVISOR_FOR_ONE_MEMBER = 64

# The runners are not alone on the daemon -- the project container and
# anything else the researcher is running share it -- so only this share
# of the daemon's memory is divided between them.
_F_DAEMON_SHARE_AVAILABLE_TO_RUNNERS = 0.5

# Every participant may hold a runner at once, so the worst case is one
# runner per participant. Pinned to councilRoutes.I_MAX_PARTICIPANTS by
# testTheRunnerDivisorMatchesTheParticipantCap rather than imported:
# a gui module importing a route module is the wrong direction, and an
# unpinned copy of a number is how two copies drift.
I_ASSUMED_CONCURRENT_RUNNERS = 8

# The runner's writable surface, as fractions of its memory limit. They
# sum to well under 1.0 because tmpfs pages are charged to the memory
# cgroup: a working tree at the memory limit turns the disk bound into
# an out-of-memory kill, which is the invariant
# agentCouncilRunner._fnValidateRunnerLimits refuses to let anyone break.
_F_RUNNER_MEMORY_FOR_WORKING_TREE = 0.5
_F_RUNNER_MEMORY_FOR_SCRATCH = 0.0625


def fiReadHostMemoryBytes():
    """Return the host's physical memory, or 0 when it cannot be read.

    Three sources are tried because none of them answers everywhere,
    and a bound that silently falls back to its floor for half the
    users is worse than no scaling at all.

    ``SC_PHYS_PAGES`` is the obvious one and is NOT portable in
    practice: it is an optional sysconf name, present in Apple's
    system Python 3.9 and absent from an Anaconda Python 3.9 on the
    same machine (measured, 2026-08-22). Which interpreter the hub
    happens to run under is not a defensible input to a resource
    bound, so the platform-specific readings below back it up --
    ``/proc/meminfo`` on Linux, and the ``hw.memsize`` sysctl through
    libc on macOS. The sysctl name is a fixed literal and the call is
    a read; no caller value reaches either.

    Zero means "unknown", and every caller falls back to its declared
    floor -- which is also what Windows gets, correctly, for a
    platform the council has never claimed to size itself for.
    """
    for fiReadSource in (
        _fiReadMemoryFromSysconf,
        _fiReadMemoryFromProcMeminfo,
        _fiReadMemoryFromDarwinSysctl,
    ):
        try:
            iMemoryBytes = fiReadSource()
        except Exception:
            iMemoryBytes = 0
        if iMemoryBytes > 0:
            return iMemoryBytes
    return 0


def _fiReadMemoryFromSysconf():
    """Return physical memory via POSIX sysconf, or 0 where unsupported."""
    if not hasattr(os, "sysconf") or "SC_PHYS_PAGES" not in getattr(
            os, "sysconf_names", {}):
        return 0
    return int(os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE"))


def _fiReadMemoryFromProcMeminfo():
    """Return MemTotal from ``/proc/meminfo``; 0 anywhere it is absent."""
    if not os.path.exists("/proc/meminfo"):
        return 0
    with open("/proc/meminfo", "r", encoding="utf-8") as fileMeminfo:
        for sLine in fileMeminfo:
            if sLine.startswith("MemTotal:"):
                return int(sLine.split()[1]) * 1024
    return 0


def _fiReadMemoryFromDarwinSysctl():
    """Return ``hw.memsize`` through libc; 0 off macOS or on any failure."""
    if sys.platform != "darwin":
        return 0
    import ctypes
    import ctypes.util

    sLibraryPath = ctypes.util.find_library("c")
    if not sLibraryPath:
        return 0
    iMemorySize = ctypes.c_uint64()
    iResultSize = ctypes.c_size_t(ctypes.sizeof(iMemorySize))
    iStatus = ctypes.CDLL(sLibraryPath, use_errno=True).sysctlbyname(
        b"hw.memsize", ctypes.byref(iMemorySize),
        ctypes.byref(iResultSize), None, 0,
    )
    return iMemorySize.value if iStatus == 0 else 0


def fdictFloorCouncilCapacity():
    """Return the capacity every supported machine is guaranteed.

    The answer when nothing can be measured, and the baseline every
    resolved capacity is the maximum of.
    """
    return {
        "iMaxSnapshotFileCount": I_FLOOR_SNAPSHOT_FILE_COUNT,
        "iMaxSnapshotMemberBytes": I_FLOOR_SNAPSHOT_MEMBER_BYTES,
        "iMaxSnapshotTotalBytes": I_FLOOR_SNAPSHOT_TOTAL_BYTES,
        "iRunnerMemoryBytes": I_FLOOR_RUNNER_MEMORY_BYTES,
        "iRunnerWorkingTreeBytes": I_FLOOR_SNAPSHOT_TOTAL_BYTES,
        "iRunnerScratchBytes": I_FLOOR_RUNNER_SCRATCH_BYTES,
        "iHostMemoryBytes": 0,
        "iDaemonMemoryBytes": 0,
        "bMeasured": False,
    }


def fdictResolveCouncilCapacity(connectionDocker=None):
    """Return the snapshot bounds and runner limits this machine allows.

    ``connectionDocker`` is optional: without it the daemon half falls
    back to its floors and only the host-bound per-member cap scales.
    That is the honest answer for a caller that has no daemon to ask,
    and it is never worse than the previous fixed constants.
    """
    dictCapacity = fdictFloorCouncilCapacity()
    iHostMemoryBytes = fiReadHostMemoryBytes()
    iDaemonMemoryBytes = 0
    if connectionDocker is not None:
        iDaemonMemoryBytes = connectionDocker.fdictReadDaemonCapacity()[
            "iMemoryBytes"]
    dictCapacity["iHostMemoryBytes"] = iHostMemoryBytes
    dictCapacity["iDaemonMemoryBytes"] = iDaemonMemoryBytes
    dictCapacity["bMeasured"] = bool(iHostMemoryBytes or iDaemonMemoryBytes)
    _fnScaleMemberBoundToHost(dictCapacity, iHostMemoryBytes)
    _fnScaleRunnerBoundsToDaemon(dictCapacity, iDaemonMemoryBytes)
    # A member can never be larger than the whole snapshot it belongs
    # to. The two bounds scale from different machines, so on a big
    # host behind a small daemon the member cap can otherwise overtake
    # the total and admit a file the runner could never receive.
    dictCapacity["iMaxSnapshotMemberBytes"] = min(
        dictCapacity["iMaxSnapshotMemberBytes"],
        dictCapacity["iMaxSnapshotTotalBytes"],
    )
    return dictCapacity


def _fnScaleMemberBoundToHost(dictCapacity, iHostMemoryBytes):
    """Raise the per-member cap toward what the hub process can hold."""
    iScaled = iHostMemoryBytes // _I_HOST_MEMORY_DIVISOR_FOR_ONE_MEMBER
    dictCapacity["iMaxSnapshotMemberBytes"] = min(
        I_CEILING_SNAPSHOT_MEMBER_BYTES,
        max(I_FLOOR_SNAPSHOT_MEMBER_BYTES, iScaled),
    )


def _fnScaleRunnerBoundsToDaemon(dictCapacity, iDaemonMemoryBytes):
    """Raise the runner limits, and with them the snapshot total.

    The chain runs one way and is stated here because reading it out of
    three modules is how it gets broken: daemon memory bounds a
    runner's memory limit, which bounds its working tree, which IS the
    largest snapshot that can be copied into it.
    """
    iRunnerMemoryBytes = max(
        I_FLOOR_RUNNER_MEMORY_BYTES,
        int(
            iDaemonMemoryBytes * _F_DAEMON_SHARE_AVAILABLE_TO_RUNNERS,
        ) // I_ASSUMED_CONCURRENT_RUNNERS,
    )
    iWorkingTreeBytes = max(
        I_FLOOR_SNAPSHOT_TOTAL_BYTES,
        int(iRunnerMemoryBytes * _F_RUNNER_MEMORY_FOR_WORKING_TREE),
    )
    iScratchBytes = max(
        I_FLOOR_RUNNER_SCRATCH_BYTES,
        int(iRunnerMemoryBytes * _F_RUNNER_MEMORY_FOR_SCRATCH),
    )
    dictCapacity["iRunnerMemoryBytes"] = iRunnerMemoryBytes
    dictCapacity["iRunnerWorkingTreeBytes"] = iWorkingTreeBytes
    dictCapacity["iRunnerScratchBytes"] = iScratchBytes
    dictCapacity["iMaxSnapshotTotalBytes"] = iWorkingTreeBytes
