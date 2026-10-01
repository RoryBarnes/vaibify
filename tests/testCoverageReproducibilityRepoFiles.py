"""Edge behaviors of the three repo-file adapters: host, container, snapshot.

The host adapter runs against real directories and a real ``flock``.
The container adapter is driven through a recording connection double
that answers the ``DockerConnection`` contract; the container ID and
the repository root are distinct strings, so a path built from the
wrong one would be visible in the recorded commands.
"""

import fcntl
import os

import pytest

from vaibify.reproducibility import repoFiles
from vaibify.reproducibility.repoFiles import (
    ContainerRepoFiles,
    HostRepoFiles,
    SnapshotRepoFiles,
    fnInjectManifestTextIntoSnapshot,
)


S_CONTAINER_ID = "c0ffee12ab34"
S_CONTAINER_ROOT = "/workspace/projectAlpha"


class _ExecResult:
    def __init__(self, iExitCode, sStdout="", sStderr=""):
        self.iExitCode = iExitCode
        self.sStdout = sStdout
        self.sStderr = sStderr


class ConnectionDouble:
    """Record every container call; answer exec with a queued result."""

    def __init__(self):
        self.listCommands = []
        self.listWrites = []
        self.listTypedReads = []
        self.listExecResults = []

    def ftRunInContainerStreamed(self, sContainerId, sCommand):
        self.listCommands.append((sContainerId, sCommand))
        if self.listExecResults:
            return self.listExecResults.pop(0)
        return _ExecResult(0)

    def fnWriteFile(self, sContainerId, sPath, baContent):
        self.listWrites.append((sContainerId, sPath, baContent))

    def fbContainerPathIsFile(self, sContainerId, sPath):
        self.listTypedReads.append(("file", sContainerId, sPath))
        return True

    def fbContainerPathIsDirectory(self, sContainerId, sPath):
        self.listTypedReads.append(("dir", sContainerId, sPath))
        return True


# ── host adapter ─────────────────────────────────────────────────


def testAJsonNameThatCannotBeReadIsSkippedNotFatal(tmp_path):
    """A directory named like a JSON file is skipped; real files are read."""
    pathDirectory = tmp_path / "records"
    pathDirectory.mkdir()
    (pathDirectory / "alpha.json").write_text('{"a": 1}', "utf-8")
    (pathDirectory / "beta.json").mkdir()
    (pathDirectory / "notes.txt").write_text("ignored", "utf-8")
    assert HostRepoFiles(str(tmp_path)).fdictReadDirJsonContents(
        "records",
    ) == {"alpha.json": '{"a": 1}'}


def testALockHeldElsewhereIsSurfacedAfterBoundedRetries(
    monkeypatch, tmp_path,
):
    """A held lock raises naming its path, never overwriting silently."""
    monkeypatch.setattr(repoFiles, "_I_LOCK_RETRY_MAX", 2)
    monkeypatch.setattr(repoFiles, "_F_LOCK_RETRY_SLEEP", 0.0)
    filesHost = HostRepoFiles(str(tmp_path))
    sLockPath = str(tmp_path / ".vaibify" / "syncStatus.json.lock")
    os.makedirs(os.path.dirname(sLockPath))
    iHeldDescriptor = os.open(sLockPath, os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(iHeldDescriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match="after 2 attempts"):
            filesHost.flockAcquireForFile(".vaibify/syncStatus.json")
    finally:
        os.close(iHeldDescriptor)
    with filesHost.flockAcquireForFile(".vaibify/syncStatus.json"):
        pass


# ── container adapter ────────────────────────────────────────────


def testAContainerAdapterWithNoRootAnswersNoForEveryPath():
    """An unrooted adapter never asks the container about a path."""
    connectionDouble = ConnectionDouble()
    filesContainer = ContainerRepoFiles(connectionDouble, S_CONTAINER_ID, "")
    assert filesContainer.fbIsFile("MANIFEST.sha256") is False
    assert filesContainer.fbIsDir(".vaibify") is False
    assert connectionDouble.listTypedReads == []


def testARootedContainerAdapterAsksByContainerIdAndAbsolutePath():
    """The typed reads carry the container id and the rooted path."""
    connectionDouble = ConnectionDouble()
    filesContainer = ContainerRepoFiles(
        connectionDouble, S_CONTAINER_ID, S_CONTAINER_ROOT,
    )
    assert filesContainer.fbIsFile("MANIFEST.sha256") is True
    assert filesContainer.fbIsDir(".vaibify") is True
    assert connectionDouble.listTypedReads == [
        ("file", S_CONTAINER_ID, S_CONTAINER_ROOT + "/MANIFEST.sha256"),
        ("dir", S_CONTAINER_ID, S_CONTAINER_ROOT + "/.vaibify"),
    ]


def testAFailedAtomicRenameInTheContainerRaises():
    """A non-zero ``mv`` is an OSError naming the destination."""
    connectionDouble = ConnectionDouble()
    connectionDouble.listExecResults = [
        _ExecResult(0), _ExecResult(1, "mv: cannot move: read-only"),
    ]
    filesContainer = ContainerRepoFiles(
        connectionDouble, S_CONTAINER_ID, S_CONTAINER_ROOT,
    )
    with pytest.raises(OSError, match="container atomic write failed"):
        filesContainer.fnWriteTextAtomic(".vaibify/state.json", "{}")
    assert connectionDouble.listWrites == [(
        S_CONTAINER_ID, S_CONTAINER_ROOT + "/.vaibify/state.json.tmp", b"{}",
    )]


def testASuccessfulAtomicWriteMovesTheTemporaryIntoPlace():
    """The same write with a clean ``mv`` raises nothing and renames."""
    connectionDouble = ConnectionDouble()
    filesContainer = ContainerRepoFiles(
        connectionDouble, S_CONTAINER_ID, S_CONTAINER_ROOT,
    )
    filesContainer.fnWriteTextAtomic(".vaibify/state.json", "{}")
    sLastCommand = connectionDouble.listCommands[-1][1]
    assert sLastCommand == (
        "mv -f '" + S_CONTAINER_ROOT + "/.vaibify/state.json.tmp' '"
        + S_CONTAINER_ROOT + "/.vaibify/state.json'"
    )


def testStatParsingKeepsOnlyWellFormedLinesForRequestedPaths():
    """Malformed, unrequested and non-numeric lines are all dropped."""
    connectionDouble = ConnectionDouble()
    connectionDouble.listExecResults = [_ExecResult(0, "\n".join([
        S_CONTAINER_ROOT + "/a.csv 1700000000",
        "garbage-without-a-space",
        "/elsewhere/b.csv 1700000001",
        S_CONTAINER_ROOT + "/c.csv notANumber",
    ]))]
    filesContainer = ContainerRepoFiles(
        connectionDouble, S_CONTAINER_ID, S_CONTAINER_ROOT,
    )
    assert filesContainer.fdictStatMtimes(["a.csv", "c.csv"]) == {
        "a.csv": 1700000000,
    }
    assert filesContainer._fdictStatBatch([]) == {}


def testHashingNoAbsolutePathsRunsNoExec():
    """An empty request answers empty without touching the container."""
    connectionDouble = ConnectionDouble()
    filesContainer = ContainerRepoFiles(
        connectionDouble, S_CONTAINER_ID, S_CONTAINER_ROOT,
    )
    assert filesContainer.fdictHashAbsolutePaths([]) == {}
    assert connectionDouble.listCommands == []


# ── snapshot adapter ─────────────────────────────────────────────


def fsnapshotWithManifestEntry(dictEntry):
    """Return a snapshot whose only sampled file is the manifest."""
    return SnapshotRepoFiles(
        S_CONTAINER_ROOT, {"MANIFEST.sha256": dictEntry}, {},
    )


def testInjectingIntoSomethingThatIsNotASnapshotChangesNothing(tmp_path):
    """A live adapter is left alone by the manifest splice."""
    filesHost = HostRepoFiles(str(tmp_path))
    fnInjectManifestTextIntoSnapshot(filesHost, "abc  a.txt\n")
    assert not (tmp_path / "MANIFEST.sha256").exists()


def testInjectingIntoASnapshotWithoutAManifestEntryChangesNothing():
    """A snapshot that never sampled the manifest still refuses to read it."""
    filesSnapshot = SnapshotRepoFiles(S_CONTAINER_ROOT, {}, {})
    fnInjectManifestTextIntoSnapshot(filesSnapshot, "abc  a.txt\n")
    with pytest.raises(KeyError, match="not in poll snapshot"):
        filesSnapshot.fsReadText("MANIFEST.sha256")


def testAnInjectedManifestBodyIsReadBackAndMarksTheFilePresent():
    """The spliced body reads back as text and as UTF-8 bytes."""
    filesSnapshot = fsnapshotWithManifestEntry(
        {"bIsFile": False, "sText": None, "iMtime": 5},
    )
    fnInjectManifestTextIntoSnapshot(filesSnapshot, "abc  a.txt\n")
    assert filesSnapshot.fbIsFile("MANIFEST.sha256") is True
    assert filesSnapshot.fsReadText("MANIFEST.sha256") == "abc  a.txt\n"
    assert filesSnapshot.fbaReadBytes("MANIFEST.sha256") == b"abc  a.txt\n"


def testInjectingNoBodyLeavesAnAbsentManifestAbsent():
    """A None body never flips an absent file to present."""
    filesSnapshot = fsnapshotWithManifestEntry(
        {"bIsFile": False, "sText": None, "iMtime": None},
    )
    fnInjectManifestTextIntoSnapshot(filesSnapshot, None)
    assert filesSnapshot.fbIsFile("MANIFEST.sha256") is False
    with pytest.raises(FileNotFoundError):
        filesSnapshot.fbaReadBytes("MANIFEST.sha256")


def testASnapshotRefusesQuestionsItDidNotSample():
    """Directories are not sampled, so the snapshot will not guess."""
    filesSnapshot = SnapshotRepoFiles(S_CONTAINER_ROOT, {}, {})
    with pytest.raises(NotImplementedError, match="directories"):
        filesSnapshot.fbIsDir(".vaibify")
    with pytest.raises(NotImplementedError, match="directory listings"):
        filesSnapshot.fdictReadDirJsonContents(".vaibify")
