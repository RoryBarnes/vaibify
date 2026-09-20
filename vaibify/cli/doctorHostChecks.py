"""Host-scope doctor checks that no other command already performs.

Three facts a researcher has historically learned at the worst
possible moment. The deposit's free-space refusal fires AFTER
``docker save`` has already written most of a multi-gigabyte tarball,
so "there is no room for this" arrives at the end of the slow part.
And both resource allocations are computed from the HOST while the
container runs on the DAEMON -- on macOS a virtual machine with its
own, smaller allocation -- so vaibify can ask for more CPUs than exist
and cap memory above anything the daemon can supply.

None of these is fatal, and the levels say so. ``--memory`` is an
upper bound rather than a reservation, so a cap above the daemon's RAM
is suspicious, not wrong; ``--cpus`` above the daemon's count is the
same shape.

The fourth fact is about the interpreter answering the command. A
Python built for a different CPU than the machine's runs only through
the operating system's translation layer, and an OS upgrade can
withdraw that layer: every command installed into that Python then
dies with "Bad CPU type in executable", the shell reports "command not
found", and ``doctor`` itself can no longer run to say why. So the
warning has to fire while the interpreter still works.
"""

import ctypes
import ctypes.util
import os
import platform
import shutil
import sys

from vaibify.docker.dockerContext import (
    fdictClassifyDockerRuntime, fdictReadDaemonFacts,
)

from vaibify.docker.runtimeRemedies import (
    S_SITUATION_MORE_DAEMON_MEMORY, S_SITUATION_MORE_HOST_DISK,
    ftRemedyForSituation,
)
from .preflightResult import (
    S_LEVEL_NOT_CHECKED, S_LEVEL_OK, S_LEVEL_WARN, S_SCOPE_HOST,
    PreflightResult,
)


__all__ = [
    "fbInterpreterRunsTranslated",
    "flistCheckDepositScratchSpace", "flistCheckResourceAllocation",
    "fpreflightInterpreterArchitecture",
]


def _fsDeepestExistingAncestor(sPath):
    """Return the nearest ancestor of sPath that exists on disk.

    The deposit's scratch root is created when a deposit runs, and a
    diagnostic must not create it: ``doctor`` changes nothing. Asking
    ``shutil.disk_usage`` about the nearest existing ancestor answers
    the same question about the same filesystem.
    """
    sCandidate = os.path.abspath(sPath)
    while not os.path.isdir(sCandidate):
        sParent = os.path.dirname(sCandidate)
        if sParent == sCandidate:
            return sCandidate
        sCandidate = sParent
    return sCandidate


def _fiReadProjectImageBytes(config):
    """Return the daemon's declared size for the project image, or 0."""
    from vaibify.reproducibility.imageDeposit import fiReadImageSizeBytes
    return fiReadImageSizeBytes(f"{config.sProjectName}:latest")


def flistCheckDepositScratchSpace(config):
    """Check the disk the environment-archive deposit will write to.

    Reads the SAME location ``imageDeposit._fnRefuseWithoutRoomOnDisk``
    measures, and applies the same headroom multiplier, so a pass here
    means that refusal will not fire and a warn here means it will.
    Deriving a second threshold would let the two disagree, which is
    the only way this check could be actively misleading.
    """
    from vaibify.reproducibility.imageDeposit import (
        _F_FREE_SPACE_MULTIPLIER, _S_SCRATCH_SUBDIRECTORY,
    )
    sScratchRoot = os.path.join(
        os.path.expanduser("~"), ".vaibify", _S_SCRATCH_SUBDIRECTORY,
    )
    iImageBytes = _fiReadProjectImageBytes(config)
    if iImageBytes <= 0:
        return [PreflightResult(
            sName="deposit-scratch-space", sLevel=S_LEVEL_NOT_CHECKED,
            sScope=S_SCOPE_HOST,
            sMessage=(
                "the daemon could not size this project's image, so "
                "the space an environment deposit needs is unknown."
            ),
        )]
    iFreeBytes = shutil.disk_usage(
        _fsDeepestExistingAncestor(sScratchRoot),
    ).free
    iNeededBytes = int(iImageBytes * _F_FREE_SPACE_MULTIPLIER)
    return [_fpreflightScratchSpace(
        sScratchRoot, iFreeBytes, iNeededBytes,
    )]


def _fpreflightScratchSpace(sScratchRoot, iFreeBytes, iNeededBytes):
    """Grade the free space against what a deposit would need."""
    fFreeGigabytes = iFreeBytes / (2 ** 30)
    fNeededGigabytes = iNeededBytes / (2 ** 30)
    sMechanism = (
        "Compares free space under ~/.vaibify/imageArchive against the "
        "image's declared size plus the deposit's own headroom "
        "multiplier -- the same figures the deposit refuses on."
    )
    if iFreeBytes >= iNeededBytes:
        return PreflightResult(
            sName="deposit-scratch-space", sLevel=S_LEVEL_OK,
            sScope=S_SCOPE_HOST,
            sMessage=(
                f"an environment deposit needs about "
                f"{fNeededGigabytes:.1f} GB here and "
                f"{fFreeGigabytes:.1f} GB is free."
            ),
            sMechanism=sMechanism,
        )
    # The HOST filesystem, not the daemon's disk, and the situation
    # says so. An earlier version reached for the DAEMON-disk remedy,
    # which on Colima answered `colima delete && colima start` -- so a
    # researcher short of room in their own home directory was told to
    # destroy every image and volume in their virtual machine, and it
    # would not have freed a byte of the filesystem that was full.
    sRemediation, sCommand = ftRemedyForSituation(
        S_SITUATION_MORE_HOST_DISK, fdictClassifyDockerRuntime(),
    )
    return PreflightResult(
        sName="deposit-scratch-space", sLevel=S_LEVEL_WARN,
        sScope=S_SCOPE_HOST,
        sMessage=(
            f"an environment deposit would need about "
            f"{fNeededGigabytes:.1f} GB under {sScratchRoot} and only "
            f"{fFreeGigabytes:.1f} GB is free; it will refuse after "
            "`docker save` has already run."
        ),
        sRemediation=(
            "Free space on this filesystem before depositing. "
            + sRemediation
        ),
        sCommand=sCommand,
        sMechanism=sMechanism,
    )


def _fiRequestedCpuCount(config):
    """Return the CPU count vaibify will ask the daemon for.

    Mirrors ``containerManager._fnAddCpuAllocation`` -- deliberately,
    because the point of the check is to report what that function
    will do. It is the ONE piece of logic here that is a second copy,
    and it is a copy of an arithmetic expression rather than of a
    policy; the day it diverges, this check is what says so.
    """
    iHostCores = os.cpu_count() or 2
    iConfiguredLimit = getattr(config, "iCpuLimit", 0) or 0
    if iConfiguredLimit > 0:
        return min(iConfiguredLimit, iHostCores)
    return max(1, iHostCores - 1)


def _flistCheckCpuAllocation(config, dictDaemon):
    """Compare the CPUs vaibify will request against the daemon's."""
    iRequested = _fiRequestedCpuCount(config)
    iDaemonCpus = dictDaemon["iCpuCount"]
    if iRequested <= iDaemonCpus:
        return [PreflightResult(
            sName="cpu-allocation", sLevel=S_LEVEL_OK,
            sScope=S_SCOPE_HOST,
            sMessage=(
                f"vaibify will request {iRequested} CPUs; the daemon "
                f"reports {iDaemonCpus}."
            ),
        )]
    return [PreflightResult(
        sName="cpu-allocation", sLevel=S_LEVEL_WARN,
        sScope=S_SCOPE_HOST,
        sMessage=(
            f"vaibify will request {iRequested} CPUs but the daemon "
            f"has {iDaemonCpus}. The count is sized from this HOST's "
            "cores, which on a virtual-machine daemon is the wrong "
            "number."
        ),
        sRemediation=(
            "Set `cpuLimit:` in vaibify.yml to at most the daemon's "
            "count, or give the daemon's virtual machine more CPUs."
        ),
        sMechanism=(
            "Recomputes containerManager's own `--cpus` arithmetic "
            "(configured cap, else host cores minus one) and compares "
            "it against `docker info`'s NCPU."
        ),
    )]


def _flistCheckMemoryAllocation(config, dictDaemon):
    """Compare the configured memory cap against the daemon's RAM."""
    fLimitGigabytes = getattr(config, "fMemoryLimitGigabytes", 0.0) or 0.0
    if fLimitGigabytes <= 0:
        return []
    fDaemonGigabytes = dictDaemon["iMemoryBytes"] / (2 ** 30)
    if fLimitGigabytes <= fDaemonGigabytes:
        return []
    sRemediation, sCommand = ftRemedyForSituation(
        S_SITUATION_MORE_DAEMON_MEMORY, fdictClassifyDockerRuntime(),
    )
    return [PreflightResult(
        sName="memory-allocation", sLevel=S_LEVEL_WARN,
        sScope=S_SCOPE_HOST,
        sMessage=(
            f"the project caps memory at {fLimitGigabytes:g} GB but "
            f"the daemon only has {fDaemonGigabytes:.1f} GB. `--memory` "
            "is an upper bound, not a reservation, so the container "
            "still starts -- it is the cap that cannot be reached."
        ),
        sRemediation=sRemediation,
        sCommand=sCommand,
        sMechanism=(
            "Compares vaibify.yml's `memoryLimitGigabytes` against "
            "`docker info`'s MemTotal, which is the DAEMON's memory."
        ),
    )]


def flistCheckResourceAllocation(config):
    """Check what vaibify will ask the daemon for against what it has."""
    dictDaemon = fdictReadDaemonFacts()
    if not dictDaemon["bAnswered"] or dictDaemon["iCpuCount"] <= 0:
        return [PreflightResult(
            sName="cpu-allocation", sLevel=S_LEVEL_NOT_CHECKED,
            sScope=S_SCOPE_HOST,
            sMessage=(
                "the daemon did not report its CPU and memory totals, "
                "so what vaibify will request could not be compared "
                "against them."
            ),
        )]
    listResults = _flistCheckCpuAllocation(config, dictDaemon)
    listResults.extend(_flistCheckMemoryAllocation(config, dictDaemon))
    return listResults


def fbInterpreterRunsTranslated():
    """True when the kernel reports THIS process as CPU-translated.

    Asks the running process about itself rather than spawning a
    probe, because a child inherits the parent's translation and so
    would only report the same fact one step removed. The kernel key
    is absent on a machine with no translation layer, and absent is
    "not translated".

    Only macOS ships a translation layer the kernel will admit to, and
    only its libc exports ``sysctlbyname`` at all: glibc has no such
    symbol, so the lookup itself raises there. The platform is settled
    here, in the probe, because the first caller outside the doctor
    (the build preflight's host-architecture read) reached it on a
    Linux runner and crashed the build before it started (2026-09-20).
    """
    if sys.platform != "darwin":
        return False
    fiSysctlByName = ctypes.CDLL(
        ctypes.util.find_library("c"), use_errno=True,
    ).sysctlbyname
    iTranslated = ctypes.c_int(0)
    iSize = ctypes.c_size_t(ctypes.sizeof(iTranslated))
    iStatus = fiSysctlByName(
        b"sysctl.proc_translated", ctypes.byref(iTranslated),
        ctypes.byref(iSize), None, 0,
    )
    return iStatus == 0 and iTranslated.value == 1


def fpreflightInterpreterArchitecture():
    """Report whether the Python answering this command is native.

    Only macOS ships a translation layer the kernel will admit to, so
    every other platform returns None and the report says nothing.
    Naming the interpreter matters more than naming the layer: the
    remedy is to install into a different Python, and ``sys.executable``
    is the one thing the researcher must not reuse.
    """
    if sys.platform != "darwin":
        return None
    sMechanism = (
        "Asks the kernel whether this process runs under CPU translation "
        "(the sysctl.proc_translated key) and names the interpreter's "
        "own build architecture from platform.machine()."
    )
    sMachine = platform.machine()
    if not fbInterpreterRunsTranslated():
        return PreflightResult(
            sName="interpreter-architecture", sLevel=S_LEVEL_OK,
            sScope=S_SCOPE_HOST,
            sMessage=(
                f"the Python answering this command ({sys.executable}) "
                f"is a native {sMachine} build."
            ),
            sMechanism=sMechanism,
        )
    return PreflightResult(
        sName="interpreter-architecture", sLevel=S_LEVEL_WARN,
        sScope=S_SCOPE_HOST,
        sMessage=(
            f"the Python answering this command ({sys.executable}) is "
            f"an {sMachine} build running through CPU translation on a "
            "machine with a different processor. An operating-system "
            "upgrade can remove that layer, after which every command "
            "installed into this Python fails with 'Bad CPU type in "
            "executable' and the shell reports 'command not found'."
        ),
        sRemediation=(
            "Install vaibify into a Python built for this machine's "
            "processor -- one whose `file` output names the same "
            "architecture as the machine -- and reinstall the other "
            "commands you rely on from it, before an upgrade forces "
            "the move."
        ),
        sCommand='file "$(command -v python3)"',
        sMechanism=sMechanism,
    )
