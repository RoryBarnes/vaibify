"""What THIS machine allows a disposable container to carry.

A container's resource limits used to be constants chosen for the
smallest machine anyone might run on. That is the right *floor* and the
wrong *ceiling*: a researcher with a 64 GB workstation gets refused work
their machine could hold many times over, and the refusal says nothing
about why the number was what it was. This module answers "what will fit
here", once, from the two figures that actually bind — and every caller
keeps the floor as the guaranteed minimum, so a machine that cannot be
measured behaves exactly as it did before.

**Two figures, and they are not the same figure.** Which one binds
depends on WHOSE memory holds the bytes:

- A **host** bound governs anything the hub process materialises in its
  own address space — a repository archive read out of one container to
  be written into another, for instance. That is the researcher's own
  RAM.
- A **daemon** bound governs a container's ``mem_limit`` and any tmpfs
  mounted inside it, because tmpfs pages are charged to the container's
  memory cgroup.

Conflating them is the trap this module exists to avoid, and it is
invisible on Linux, where the daemon shares the host kernel and the two
numbers are equal. On macOS they differ by whatever the researcher gave
Docker Desktop — 16 GB of host RAM over a 7.7 GB Docker VM on the
machine this was measured on. Sizing a container from host RAM
over-provisions it there and the kill arrives at run time.

**Scaling only ever raises, never lowers.** Every resolved bound is
``max(floor, computed)``. A machine smaller than the floor gets the
floor: the floor is what the design was reviewed against, and shrinking
below it would make work that ran yesterday refuse today for a reason no
message could usefully explain.

Extracted from the Agent Council's capacity module so the two lanes
resolve one set of bounds rather than two that drift.
"""

import os
import sys


__all__ = [
    "I_FLOOR_ARCHIVE_MEMBER_BYTES",
    "I_FLOOR_ARCHIVE_TOTAL_BYTES",
    "I_FLOOR_CONTAINER_MEMORY_BYTES",
    "I_FLOOR_CONTAINER_SCRATCH_BYTES",
    "I_CEILING_ARCHIVE_MEMBER_BYTES",
    "fdictFloorDaemonCapacity",
    "fdictResolveDaemonCapacity",
    "fiReadHostMemoryBytes",
]


# The floors: the bounds every supported machine honours.
I_FLOOR_ARCHIVE_MEMBER_BYTES = 64 * 1024 * 1024
I_FLOOR_ARCHIVE_TOTAL_BYTES = 512 * 1024 * 1024
I_FLOOR_CONTAINER_MEMORY_BYTES = 1024 * 1024 * 1024
I_FLOOR_CONTAINER_SCRATCH_BYTES = 64 * 1024 * 1024

# Past this a single archive member is a dataset rather than a file
# being copied, whatever the machine could technically hold.
I_CEILING_ARCHIVE_MEMBER_BYTES = 1024 * 1024 * 1024

# The hub holds ONE member in memory at a time, so the per-member bound
# is a small fraction of host RAM rather than a large one: the hub is
# also serving the dashboard, and a member near the whole machine would
# trade a clean refusal for a swap storm.
_I_HOST_MEMORY_DIVISOR_FOR_ONE_MEMBER = 64

# A whole repository archive is materialised in the hub's address space
# once, so it may claim a larger slice than a single member -- but the
# hub is also serving the dashboard, and an archive near the whole
# machine would trade a clean refusal for a swap storm.
_I_HOST_MEMORY_DIVISOR_FOR_WHOLE_ARCHIVE = 8

# A disposable container is not alone on the daemon -- the researcher's
# project container and anything else they are running share it -- so
# only this share of the daemon's memory is ever offered to one.
_F_DAEMON_SHARE_AVAILABLE = 0.25

# The scratch tmpfs, as a fraction of the container's memory limit. Well
# under 1.0 because tmpfs pages are charged to the memory cgroup: a
# scratch mount at the memory limit turns the disk bound into an
# out-of-memory kill.
_F_MEMORY_FOR_SCRATCH = 0.0625


def fiReadHostMemoryBytes():
    """Return this host's physical memory in bytes, or 0 when unknown.

    Three readings, tried in the order of how directly each names the
    quantity on the platform that offers it. Zero is the honest answer
    for a platform none of them fit: every caller has a declared floor,
    and a guessed number would be worse than no number.
    """
    iFromSysconf = _fiReadMemoryFromSysconf()
    if iFromSysconf:
        return iFromSysconf
    if sys.platform.startswith("linux"):
        return _fiReadMemoryFromProcMeminfo()
    if sys.platform == "darwin":
        return _fiReadMemoryFromDarwinSysctl()
    return 0


def _fiReadMemoryFromSysconf():
    """Return memory from ``os.sysconf``, or 0 when it cannot answer."""
    try:
        iPageSize = os.sysconf("SC_PAGE_SIZE")
        iPageCount = os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, ValueError, OSError):
        return 0
    if iPageSize > 0 and iPageCount > 0:
        return iPageSize * iPageCount
    return 0


def _fiReadMemoryFromProcMeminfo():
    """Return ``MemTotal`` from ``/proc/meminfo``, or 0 when unreadable."""
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as fileMeminfo:
            for sLine in fileMeminfo:
                if sLine.startswith("MemTotal:"):
                    return int(sLine.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return 0
    return 0


def _fiReadMemoryFromDarwinSysctl():
    """Return ``hw.memsize`` through libc; 0 off macOS or on any failure.

    Read through ``ctypes`` rather than by launching ``sysctl``: a
    subprocess to learn a number is command authority this module has
    no reason to acquire.
    """
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


def fdictFloorDaemonCapacity():
    """Return the guaranteed bounds, with nothing measured."""
    return {
        "iArchiveMemberBytes": I_FLOOR_ARCHIVE_MEMBER_BYTES,
        "iArchiveTotalBytes": I_FLOOR_ARCHIVE_TOTAL_BYTES,
        "iContainerMemoryBytes": I_FLOOR_CONTAINER_MEMORY_BYTES,
        "iContainerScratchBytes": I_FLOOR_CONTAINER_SCRATCH_BYTES,
        "iHostMemoryBytes": 0,
        "iDaemonMemoryBytes": 0,
    }


def fdictResolveDaemonCapacity(connectionDocker=None):
    """Return the bounds THIS machine allows, never below the floors.

    ``connectionDocker`` supplies the daemon reading through
    ``fdictReadDaemonCapacity``; omitting it (or a daemon that will not
    answer) leaves the daemon-scaled bounds at their floors, which is
    exactly the behaviour that predates any scaling.
    """
    dictCapacity = fdictFloorDaemonCapacity()
    iHostMemoryBytes = fiReadHostMemoryBytes()
    dictCapacity["iHostMemoryBytes"] = iHostMemoryBytes
    _fnScaleMemberBoundToHost(dictCapacity, iHostMemoryBytes)
    iDaemonMemoryBytes = _fiReadDaemonMemoryBytes(connectionDocker)
    dictCapacity["iDaemonMemoryBytes"] = iDaemonMemoryBytes
    _fnScaleContainerBoundsToDaemon(dictCapacity, iDaemonMemoryBytes)
    return dictCapacity


def _fiReadDaemonMemoryBytes(connectionDocker):
    """Return the daemon's memory, or 0 when there is nothing to ask."""
    if connectionDocker is None:
        return 0
    try:
        dictDaemon = connectionDocker.fdictReadDaemonCapacity()
    except Exception:
        return 0
    return int(dictDaemon.get("iMemoryBytes") or 0)


def _fnScaleMemberBoundToHost(dictCapacity, iHostMemoryBytes):
    """Raise the per-member bound toward this host's RAM, within reason."""
    if iHostMemoryBytes <= 0:
        return
    iScaled = iHostMemoryBytes // _I_HOST_MEMORY_DIVISOR_FOR_ONE_MEMBER
    dictCapacity["iArchiveMemberBytes"] = max(
        I_FLOOR_ARCHIVE_MEMBER_BYTES,
        min(iScaled, I_CEILING_ARCHIVE_MEMBER_BYTES),
    )
    dictCapacity["iArchiveTotalBytes"] = max(
        I_FLOOR_ARCHIVE_TOTAL_BYTES,
        iHostMemoryBytes // _I_HOST_MEMORY_DIVISOR_FOR_WHOLE_ARCHIVE,
    )


def _fnScaleContainerBoundsToDaemon(dictCapacity, iDaemonMemoryBytes):
    """Raise the container bounds toward the DAEMON's share, never the host's."""
    if iDaemonMemoryBytes <= 0:
        return
    iMemoryBytes = max(
        I_FLOOR_CONTAINER_MEMORY_BYTES,
        int(iDaemonMemoryBytes * _F_DAEMON_SHARE_AVAILABLE),
    )
    dictCapacity["iContainerMemoryBytes"] = iMemoryBytes
    dictCapacity["iContainerScratchBytes"] = max(
        I_FLOOR_CONTAINER_SCRATCH_BYTES,
        int(iMemoryBytes * _F_MEMORY_FOR_SCRATCH),
    )
