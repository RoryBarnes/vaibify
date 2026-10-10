"""The cgroup memory parser and the typed read that feeds it.

Every number here is the KERNEL's own spelling of a file's contents
(Documentation/admin-guide/cgroup-v2.rst for memory.current, memory.max,
memory.events and memory.stat; cgroup-v1/memory.rst for the v1 files),
so the expected values are read off the fixtures rather than computed by
the code under test. The working set is usage minus inactive_file, the
estimate cAdvisor and the Kubernetes eviction manager use.

The typed-read program is RUN, not assumed: it is executed on this
machine against a fake cgroup tree built in a temporary directory, and
its output is fed to the parser, so the program and the parser cannot
drift apart in shape.
"""

import subprocess
import sys

import pytest

from vaibify.docker import cgroupMemory
from vaibify.docker import dockerConnection


S_V2_STAT = (
    "anon 293601280\n"
    "file 1503238553\n"
    "kernel 41943040\n"
    "shmem 104857600\n"
    "inactive_file 1395864371\n"
    "active_file 107374182\n"
)
S_V2_EVENTS = (
    "low 0\nhigh 0\nmax 31\noom 14\noom_kill 2\noom_group_kill 0\n"
)


def _fsCompose(sVersion, dictFiles):
    """Return the typed read's output for these files (None = absent)."""
    listLines = [f"{cgroupMemory.S_VERSION_MARKER} {sVersion}"]
    for sName, sText in dictFiles.items():
        if sText is None:
            listLines.append(
                f"{cgroupMemory.S_FILE_MARKER} {sName} "
                f"{cgroupMemory.S_MISSING_MARKER}"
            )
        else:
            listLines.append(f"{cgroupMemory.S_FILE_MARKER} {sName}")
            listLines.append(sText.rstrip("\n"))
    return "\n".join(listLines) + "\n"


def _fsComposeV2(**dictOverrides):
    dictFiles = {
        "memory.current": "1838153728\n",
        "memory.max": "6442450944\n",
        "memory.events": S_V2_EVENTS,
        "memory.stat": S_V2_STAT,
    }
    dictFiles.update(dictOverrides)
    return _fsCompose("v2", dictFiles)


def testAVersionTwoReadingParsesEveryField():
    dictParsed = cgroupMemory.fdictParseCgroupMemory(_fsComposeV2())
    assert dictParsed["sCgroupVersion"] == "v2"
    assert dictParsed["iUsageBytes"] == 1838153728
    assert dictParsed["sLimitKind"] == cgroupMemory.S_LIMIT_FINITE
    assert dictParsed["iLimitBytes"] == 6442450944
    assert dictParsed["iAnonBytes"] == 293601280
    assert dictParsed["iFileBytes"] == 1503238553
    assert dictParsed["iShmemBytes"] == 104857600
    assert dictParsed["iKernelBytes"] == 41943040
    assert dictParsed["iInactiveFileBytes"] == 1395864371
    assert dictParsed["iOomCount"] == 14
    assert dictParsed["iOomKillCount"] == 2


@pytest.mark.falsification
def testTheWorkingSetIsUsageMinusInactiveFile():
    """Kills: computing the working set from anon instead.

    The usage of 1838153728 counts anon, shmem, kernel memory and
    active page cache; only the inactive file cache is subtracted. So
    the estimate is larger than anon by the shmem, the kernel memory
    and the active file pages -- which is the point: tmpfs and kernel
    memory count against the cap too.
    """
    dictParsed = cgroupMemory.fdictParseCgroupMemory(_fsComposeV2())
    assert dictParsed["iWorkingSetBytes"] == 1838153728 - 1395864371
    assert dictParsed["iWorkingSetBytes"] > dictParsed["iAnonBytes"]


def testTheWorkingSetIsClampedAtZero():
    sText = _fsComposeV2(**{"memory.current": "1000\n"})
    dictParsed = cgroupMemory.fdictParseCgroupMemory(sText)
    assert dictParsed["iWorkingSetBytes"] == 0


def testMaxInMemoryMaxMeansUnlimited():
    sText = _fsComposeV2(**{"memory.max": "max\n"})
    dictParsed = cgroupMemory.fdictParseCgroupMemory(sText)
    assert dictParsed["sLimitKind"] == cgroupMemory.S_LIMIT_UNLIMITED
    assert dictParsed["iLimitBytes"] is None


@pytest.mark.falsification
def testAMissingFileIsNoneNeverZero():
    """Kills: the parser reporting 0 for a file it could not read.

    A zero kill count from an unreadable memory.events would read as
    "no process was killed", which is a claim nobody earned.
    """
    sText = _fsComposeV2(**{
        "memory.current": None, "memory.events": None,
        "memory.stat": None, "memory.max": None,
    })
    dictParsed = cgroupMemory.fdictParseCgroupMemory(sText)
    for sKey in (
        "iUsageBytes", "iLimitBytes", "iAnonBytes", "iFileBytes",
        "iShmemBytes", "iKernelBytes", "iInactiveFileBytes",
        "iWorkingSetBytes", "iOomCount", "iOomKillCount",
    ):
        assert dictParsed[sKey] is None, sKey
    assert dictParsed["sLimitKind"] == cgroupMemory.S_LIMIT_UNKNOWN


def testAnEmptyReadIsUnknownThroughout():
    dictParsed = cgroupMemory.fdictParseCgroupMemory("")
    assert dictParsed["sCgroupVersion"] is None
    assert dictParsed["iUsageBytes"] is None
    assert dictParsed["sLimitKind"] == cgroupMemory.S_LIMIT_UNKNOWN
    assert dictParsed["iOomKillCount"] is None


def testAGarbledNumberIsNoneNotAnError():
    sText = _fsComposeV2(**{"memory.current": "not-a-number\n"})
    dictParsed = cgroupMemory.fdictParseCgroupMemory(sText)
    assert dictParsed["iUsageBytes"] is None
    assert dictParsed["iWorkingSetBytes"] is None


def testAVersionOneReadingUsesTheVersionOneFiles():
    sText = _fsCompose("v1", {
        "memory.usage_in_bytes": "524288000\n",
        "memory.limit_in_bytes": "1073741824\n",
        "memory.oom_control": "oom_kill_disable 0\nunder_oom 0\noom_kill 3\n",
        "memory.stat": (
            "cache 300000000\nrss 200000000\nshmem 1000\n"
            "inactive_file 100000000\ntotal_cache 310000000\n"
            "total_rss 210000000\ntotal_shmem 2000\n"
            "total_inactive_file 120000000\n"
        ),
    })
    dictParsed = cgroupMemory.fdictParseCgroupMemory(sText)
    assert dictParsed["sCgroupVersion"] == "v1"
    assert dictParsed["iUsageBytes"] == 524288000
    assert dictParsed["sLimitKind"] == cgroupMemory.S_LIMIT_FINITE
    assert dictParsed["iLimitBytes"] == 1073741824
    assert dictParsed["iOomKillCount"] == 3
    assert dictParsed["iOomCount"] is None
    assert dictParsed["iAnonBytes"] == 210000000
    assert dictParsed["iFileBytes"] == 310000000
    assert dictParsed["iShmemBytes"] == 2000
    assert dictParsed["iKernelBytes"] is None
    assert dictParsed["iInactiveFileBytes"] == 120000000
    assert dictParsed["iWorkingSetBytes"] == 524288000 - 120000000


def testTheVersionOneNoLimitSentinelIsUnlimited():
    """The kernel spells v1 "no limit" as LONG_MAX rounded to a page."""
    sText = _fsCompose("v1", {
        "memory.usage_in_bytes": "1000\n",
        "memory.limit_in_bytes": "9223372036854771712\n",
        "memory.oom_control": None, "memory.stat": None,
    })
    dictParsed = cgroupMemory.fdictParseCgroupMemory(sText)
    assert dictParsed["sLimitKind"] == cgroupMemory.S_LIMIT_UNLIMITED
    assert dictParsed["iOomKillCount"] is None


def testTheOomCountMatchesOnlyTheExactToken():
    """``oom`` must not read the ``oom_kill`` or ``oom_group_kill`` lines."""
    assert cgroupMemory.fiParseOomCount(S_V2_EVENTS) == 14
    assert cgroupMemory.fiParseOomCount("oom_kill 2\noom_group_kill 5\n") is None
    assert cgroupMemory.fiParseOomCount("") is None
    assert cgroupMemory.fiParseOomCount("oom many\n") is None


def _fsRunTheTypedReadOn(pathRoot):
    sProgram = dockerConnection._DICT_TYPED_READ_PROGRAMS[
        dockerConnection.S_TYPED_READ_CGROUP_MEMORY
    ].replace(
        dockerConnection._S_TYPED_READ_PATH_SLOT,
        dockerConnection._fsTypedReadPathLiteral(str(pathRoot)),
    )
    processResult = subprocess.run(
        [sys.executable, "-c", sProgram], capture_output=True, text=True,
    )
    assert processResult.returncode == 0, processResult.stderr
    return processResult.stdout


def testTheTypedReadProgramFeedsTheParserOnVersionTwo(tmp_path):
    (tmp_path / "memory.current").write_text("1838153728\n")
    (tmp_path / "memory.max").write_text("max\n")
    (tmp_path / "memory.events").write_text(S_V2_EVENTS)
    (tmp_path / "memory.stat").write_text(S_V2_STAT)
    dictParsed = cgroupMemory.fdictParseCgroupMemory(
        _fsRunTheTypedReadOn(tmp_path))
    assert dictParsed["sCgroupVersion"] == "v2"
    assert dictParsed["iUsageBytes"] == 1838153728
    assert dictParsed["sLimitKind"] == cgroupMemory.S_LIMIT_UNLIMITED
    assert dictParsed["iOomKillCount"] == 2
    assert dictParsed["iWorkingSetBytes"] == 1838153728 - 1395864371


def testTheTypedReadProgramFallsBackToVersionOne(tmp_path):
    pathMemory = tmp_path / "memory"
    pathMemory.mkdir()
    (pathMemory / "memory.usage_in_bytes").write_text("2048\n")
    (pathMemory / "memory.limit_in_bytes").write_text("4096")
    dictParsed = cgroupMemory.fdictParseCgroupMemory(
        _fsRunTheTypedReadOn(tmp_path))
    assert dictParsed["sCgroupVersion"] == "v1"
    assert dictParsed["iUsageBytes"] == 2048
    assert dictParsed["iLimitBytes"] == 4096
    assert dictParsed["iOomKillCount"] is None


def testTheTypedReadProgramNamesEveryMissingFile(tmp_path):
    sOutput = _fsRunTheTypedReadOn(tmp_path)
    assert sOutput.count(cgroupMemory.S_MISSING_MARKER) == 4
    dictParsed = cgroupMemory.fdictParseCgroupMemory(sOutput)
    assert dictParsed["iUsageBytes"] is None
    assert dictParsed["sLimitKind"] == cgroupMemory.S_LIMIT_UNKNOWN
