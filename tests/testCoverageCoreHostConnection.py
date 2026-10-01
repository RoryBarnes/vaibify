"""HostConnection read, write and process edges on a real temp project.

Every call here runs against real files, real git and real
subprocesses under tmp_path, with the journal, lock and scratch roots
redirected, as ``testHostConnection`` does. The one substitution is the
typed-read program's result for two malformed-answer cases, because a
real git never answers them and the parse guard is what is asserted.
"""

import hashlib
import os
import shutil
import subprocess
import sys

import pytest

from vaibify.config import containerLock, operationJournal, processLiveness
from vaibify.host import hostConnection as hostConnectionModule
from vaibify.host import hostScratch
from vaibify.host.hostConnection import (
    HostConnection,
    HostPathOutsideProjectError,
)


S_RESOURCE_NAME = "hostProjectAlpha"


@pytest.fixture(autouse=True)
def fixtureIsolateJournalAndScratch(tmp_path, monkeypatch):
    """Redirect the journal, locks, and scratch roots to tmp_path."""
    monkeypatch.setattr(
        operationJournal, "_S_JOURNAL_DIRECTORY", str(tmp_path / "journal"),
    )
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"),
    )
    monkeypatch.setattr(
        hostScratch, "_S_HOST_DIAGNOSTICS_ROOT",
        str(tmp_path / "host-diagnostics"),
    )


@pytest.fixture()
def tProjectAndConnection(tmp_path):
    """Return (sProjectRoot, HostConnection) over a temp project."""
    sProjectRoot = str(tmp_path / "project")
    os.makedirs(sProjectRoot)
    connectionHost = HostConnection(
        fnResolveProjectRoot=lambda sResourceId: sProjectRoot,
    )
    return os.path.realpath(sProjectRoot), connectionHost


def testRepoPathHashesMatchTheFileContent(tProjectAndConnection):
    """A repo-relative file hashes to its sha256; a missing one to None."""
    sProjectRoot, connectionHost = tProjectAndConnection
    with open(os.path.join(sProjectRoot, "dataFile.csv"), "wb") as fileHandle:
        fileHandle.write(b"a,b\n1,2\n")
    dictHashes = connectionHost.fdictHashContainerRepoPaths(
        S_RESOURCE_NAME, sProjectRoot, ["dataFile.csv", "absent.csv"],
    )
    assert dictHashes["dataFile.csv"]["sSha256"] == (
        hashlib.sha256(b"a,b\n1,2\n").hexdigest()
    )
    assert dictHashes["absent.csv"]["sSha256"] is None


def testRepoPathHashRefusesARootOutsideTheProject(
    tProjectAndConnection, tmp_path,
):
    """The root itself passes through the host path guard first."""
    _, connectionHost = tProjectAndConnection
    with pytest.raises(HostPathOutsideProjectError):
        connectionHost.fdictHashContainerRepoPaths(
            S_RESOURCE_NAME, str(tmp_path), ["anything.txt"],
        )


def testFileShaIsEmptyForMissingFileAndExactForPresentOne(
    tProjectAndConnection,
):
    """No fingerprint means cannot compare; a present file is hashed."""
    sProjectRoot, connectionHost = tProjectAndConnection
    assert connectionHost.fsHashContainerFileSha256(
        S_RESOURCE_NAME, os.path.join(sProjectRoot, "absent.txt"),
    ) == ""
    with open(os.path.join(sProjectRoot, "present.txt"), "wb") as fileHandle:
        fileHandle.write(b"payload")
    assert connectionHost.fsHashContainerFileSha256(
        S_RESOURCE_NAME, "present.txt",
    ) == hashlib.sha256(b"payload").hexdigest()


def testTheHostClockIsTheClockThatStampsHostFiles(tProjectAndConnection):
    """A host project's files live on the hub's machine: same clock."""
    import re
    import time
    _, connectionHost = tProjectAndConnection
    sBefore = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    sClock = connectionHost.fsReadClockUtc(S_RESOURCE_NAME)
    sAfter = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    assert re.match(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d UTC$", sClock)
    assert sBefore <= sClock <= sAfter


def testFilesystemUsageOfMissingPathIsFileNotFound(tProjectAndConnection):
    """A statvfs failure is reported as a missing path, naming it."""
    _, connectionHost = tProjectAndConnection
    with pytest.raises(FileNotFoundError, match="Cannot stat host filesystem"):
        connectionHost.fdictReadFilesystemUsage(
            S_RESOURCE_NAME, "absentDirectory/inner",
        )


def testFailedAtomicWriteLeavesNoTemporaryFileBehind(tProjectAndConnection):
    """A rename onto a directory fails, and the temp file is removed."""
    sProjectRoot, connectionHost = tProjectAndConnection
    os.makedirs(os.path.join(sProjectRoot, "occupied"))
    with open(os.path.join(sProjectRoot, "occupied", "keep.txt"), "w") as fileHandle:
        fileHandle.write("keep")
    listBefore = sorted(os.listdir(sProjectRoot))
    with pytest.raises(OSError):
        connectionHost.fnWriteFile(S_RESOURCE_NAME, "occupied", b"new bytes")
    assert sorted(os.listdir(sProjectRoot)) == listBefore
    assert os.path.isdir(os.path.join(sProjectRoot, "occupied"))


def testTreeCopyIntoAHostProjectIsRefusedByName(tProjectAndConnection):
    """There is no container workspace to copy a host tree into."""
    _, connectionHost = tProjectAndConnection
    with pytest.raises(HostPathOutsideProjectError, match="is a host project"):
        connectionHost.fnWriteTreeViaTar(
            S_RESOURCE_NAME, "/destination", ["/source/file"],
        )


def testTerminalLaunchIntoMissingDirectoryRaisesAndLeaksNoDescriptor(tmp_path):
    """A launch that cannot start closes both descriptors it opened."""
    connectionHost = HostConnection(
        fnResolveProjectRoot=lambda sResourceId: str(tmp_path / "absent"),
    )
    iDescriptorsBefore = len(os.listdir("/dev/fd"))
    with pytest.raises(OSError):
        connectionHost.fdictLaunchTerminalShellSuspended(S_RESOURCE_NAME)
    assert len(os.listdir("/dev/fd")) == iDescriptorsBefore


@pytest.mark.parametrize("iGroup", [0, -3, "7"])
def testProbeOfUnusableGroupIsInconclusive(tProjectAndConnection, iGroup):
    """A non-positive or non-integer group is never read as empty."""
    _, connectionHost = tProjectAndConnection
    dictProbe = connectionHost.fdictProbeProcessGroupMembers(
        S_RESOURCE_NAME, iGroup,
    )
    assert dictProbe["bConclusive"] is False
    assert dictProbe["iMemberCount"] == -1
    assert "unusable process group" in dictProbe["sDetail"]


def testProbeWhoseEnumerationCannotRunIsInconclusive(
    tProjectAndConnection, monkeypatch,
):
    """An enumeration that could not run is inconclusive, not zero."""
    _, connectionHost = tProjectAndConnection
    monkeypatch.setattr(
        processLiveness, "ftEnumerateSessionMembers",
        lambda iSession: (False, []),
    )
    dictProbe = connectionHost.fdictProbeProcessGroupMembers(
        S_RESOURCE_NAME, os.getpid(),
    )
    assert dictProbe == {
        "bConclusive": False, "iMemberCount": -1,
        "sDetail": "the session enumeration could not run",
    }


def testSignalOutsideTheAllowlistIsRefused(tProjectAndConnection):
    """Only TERM and KILL may be delivered to a recorded group."""
    _, connectionHost = tProjectAndConnection
    with pytest.raises(ValueError, match="only TERM and KILL"):
        connectionHost.fnSignalProcessGroupMembers(
            S_RESOURCE_NAME, os.getpid(), "HUP",
        )


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def testRepositoryStatusIsReadThroughTheJournaledTypedRead(
    tProjectAndConnection,
):
    """A real repository answers one status record; an empty list, none."""
    sProjectRoot, connectionHost = tProjectAndConnection
    subprocess.run(["git", "init", "-q"], cwd=sProjectRoot, check=True)
    assert connectionHost.flistReadGitRepoStatuses(S_RESOURCE_NAME, []) == []
    listStatuses = connectionHost.flistReadGitRepoStatuses(
        S_RESOURCE_NAME, [sProjectRoot],
    )
    assert len(listStatuses) == 1
    assert isinstance(listStatuses[0], dict)
    assert operationJournal.fdictReadJournalOutcome(S_RESOURCE_NAME)[
        "dictOperations"
    ] == {}


class TypedReadResult:
    """The exec-result shape the typed-read program returns."""

    def __init__(self, iExitCode, sStdout, sStderr):
        self.iExitCode = iExitCode
        self.sStdout = sStdout
        self.sStderr = sStderr


@pytest.mark.parametrize(
    "resultTyped,sExpectedFragment",
    [
        (TypedReadResult(2, "", "fatal: broken\n"), "fatal: broken"),
        (TypedReadResult(0, "not json", ""), "unparseable output"),
    ],
)
def testRepositoryStatusFailuresAreRaisedAsOsError(
    tProjectAndConnection, monkeypatch, resultTyped, sExpectedFragment,
):
    """A failed or garbled status read raises rather than answering []."""
    sProjectRoot, connectionHost = tProjectAndConnection
    monkeypatch.setattr(
        connectionHost, "_ftRunTypedReadProgram",
        lambda sResourceId, sOperation, listPaths: resultTyped,
    )
    with pytest.raises(OSError, match=sExpectedFragment):
        connectionHost.flistReadGitRepoStatuses(S_RESOURCE_NAME, [sProjectRoot])


def testReapLostToAnotherCollectorFallsBackToPopenWait():
    """When wait4 finds no child, completion is reported without CPU time."""
    processChild = subprocess.Popen([sys.executable, "-c", "pass"])
    os.waitpid(processChild.pid, 0)
    bCompleted, fCpuSeconds = hostConnectionModule._ftAwaitProcessWithinBound(
        processChild, 5.0,
    )
    assert bCompleted is True
    assert fCpuSeconds is None
