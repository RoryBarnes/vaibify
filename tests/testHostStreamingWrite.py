"""The host connection's streamed write matches the container's contract.

Source: ``HostConnection.fnWriteFileFromStream`` in
``vaibify/host/hostConnection.py``.

The container leg's write is a fixed program; the host leg is Python in
this process, so the same properties are asserted against both
(``tests/testConfinedStreamingWrite.py``) rather than assumed to follow
from reading the code twice. Every test drives real files in a temporary
project directory.
"""

import errno
import io
import os
import stat

import pytest

from tests.testHostConnection import (  # noqa: F401  (fixtures)
    S_PROJECT_NAME,
    fixtureIsolateJournalAndScratch,
    tProjectAndConnection,
)
from vaibify.config import mutationAdmission
from vaibify.docker.confinedWrite import (
    ContainerWriteExistsError,
    ContainerWriteRefusedError,
)
from vaibify.host import hostConnection as hostConnectionModule

I_MEBIBYTE = 1 << 20


def _fsReadFile(sPath):
    with open(sPath, "rb") as fileIn:
        return fileIn.read()


def _flistStagingFiles(sDirectory):
    return [sName for sName in os.listdir(sDirectory)
            if sName.startswith(hostConnectionModule.S_STAGING_PREFIX)]


class _RecordingSource:
    """A readable that remembers how it was read."""

    def __init__(self, baContent, fnBeforeFirstRead=None):
        self._fileContent = io.BytesIO(baContent)
        self.listReadSizes = []
        self._fnBeforeFirstRead = fnBeforeFirstRead

    def read(self, iSize=-1):
        if self._fnBeforeFirstRead is not None and not self.listReadSizes:
            self._fnBeforeFirstRead()
        self.listReadSizes.append(iSize)
        return self._fileContent.read(iSize)


class _EndlessSource:
    def __init__(self, iMaxReads):
        self.listReadSizes = []
        self._iMaxReads = iMaxReads

    def read(self, iSize=-1):
        self.listReadSizes.append(iSize)
        if len(self.listReadSizes) > self._iMaxReads:
            raise AssertionError(
                f"the writer kept reading past {self._iMaxReads} reads")
        return b"x" * iSize


@pytest.mark.falsification
def testALargeStreamIsCopiedInBoundedChunksNeverSlurped(tProjectAndConnection):
    """Kills: reading the source whole (``read()``) instead of in chunks."""
    sProjectRoot, connection = tProjectAndConnection
    sTarget = os.path.join(sProjectRoot, "big.bin")
    baPayload = os.urandom(I_MEBIBYTE) * 5 + b"tail"
    sourceRecording = _RecordingSource(baPayload)
    connection.fnWriteFileFromStream(
        S_PROJECT_NAME, sTarget, sourceRecording,
        iExpectedBytes=len(baPayload))
    assert _fsReadFile(sTarget) == baPayload
    assert all(0 < iSize <= I_MEBIBYTE for iSize in sourceRecording.listReadSizes)
    assert len(sourceRecording.listReadSizes) >= 6
    assert _flistStagingFiles(sProjectRoot) == []


@pytest.mark.falsification
def testAForbiddenReplacementIsRefusedAndTheOldBytesStay(tProjectAndConnection):
    """Kills: ignoring ``bReplaceAllowed`` (treating it as always True)."""
    sProjectRoot, connection = tProjectAndConnection
    sTarget = os.path.join(sProjectRoot, "data.csv")
    with open(sTarget, "wb") as fileOld:
        fileOld.write(b"old")
    with pytest.raises(ContainerWriteExistsError):
        connection.fnWriteFileFromStream(
            S_PROJECT_NAME, sTarget, io.BytesIO(b"new"),
            bReplaceAllowed=False)
    assert _fsReadFile(sTarget) == b"old"
    assert _flistStagingFiles(sProjectRoot) == []


def testAForbiddenReplacementStillCreatesAFreshFile(tProjectAndConnection):
    sProjectRoot, connection = tProjectAndConnection
    sTarget = os.path.join(sProjectRoot, "fresh.csv")
    connection.fnWriteFileFromStream(
        S_PROJECT_NAME, sTarget, io.BytesIO(b"new"), bReplaceAllowed=False)
    assert _fsReadFile(sTarget) == b"new"
    assert os.listdir(sProjectRoot) == ["fresh.csv"]


@pytest.mark.falsification
def testAFileThatAppearsWhileTheUploadArrivesIsNotReplaced(
    tProjectAndConnection,
):
    """The existence check is repeated atomically at publication.

    Kills: publishing with a plain rename when replacement is forbidden.
    """
    sProjectRoot, connection = tProjectAndConnection
    sTarget = os.path.join(sProjectRoot, "contested.txt")

    def fnAppear():
        with open(sTarget, "wb") as fileAppeared:
            fileAppeared.write(b"someone else's")

    with pytest.raises(ContainerWriteExistsError):
        connection.fnWriteFileFromStream(
            S_PROJECT_NAME, sTarget,
            _RecordingSource(b"mine", fnBeforeFirstRead=fnAppear),
            bReplaceAllowed=False)
    assert _fsReadFile(sTarget) == b"someone else's"
    assert _flistStagingFiles(sProjectRoot) == []


@pytest.mark.falsification
def testAShortBodyIsRefusedTheStagingRemovedAndTheOldBytesStay(
    tProjectAndConnection,
):
    """Kills: dropping the final size comparison."""
    sProjectRoot, connection = tProjectAndConnection
    sTarget = os.path.join(sProjectRoot, "state.json")
    with open(sTarget, "wb") as fileOld:
        fileOld.write(b"old")
    with pytest.raises(ContainerWriteRefusedError, match="5 bytes arrived"):
        connection.fnWriteFileFromStream(
            S_PROJECT_NAME, sTarget, io.BytesIO(b"short"),
            iExpectedBytes=1000)
    assert _fsReadFile(sTarget) == b"old"
    assert _flistStagingFiles(sProjectRoot) == []


@pytest.mark.falsification
def testAnOverlongStreamIsRefusedAtOnceNotDrainedToTheEnd(
    tProjectAndConnection,
):
    """Kills: removing the in-loop size check so an endless sender is
    copied until it stops."""
    sProjectRoot, connection = tProjectAndConnection
    sTarget = os.path.join(sProjectRoot, "bounded.bin")
    sourceEndless = _EndlessSource(iMaxReads=50)
    with pytest.raises(ContainerWriteRefusedError):
        connection.fnWriteFileFromStream(
            S_PROJECT_NAME, sTarget, sourceEndless,
            iExpectedBytes=3 * I_MEBIBYTE)
    assert len(sourceEndless.listReadSizes) <= 4
    assert not os.path.exists(sTarget)
    assert _flistStagingFiles(sProjectRoot) == []


@pytest.mark.falsification
def testASymlinkedFinalComponentIsRefusedAndItsTargetUntouched(
    tProjectAndConnection,
):
    """An upload never writes THROUGH a link the researcher placed.

    Kills: validating only the resolved path, which hides the link.
    """
    sProjectRoot, connection = tProjectAndConnection
    sRealFile = os.path.join(sProjectRoot, "real.txt")
    with open(sRealFile, "wb") as fileReal:
        fileReal.write(b"real")
    sLink = os.path.join(sProjectRoot, "alias.txt")
    os.symlink(sRealFile, sLink)
    with pytest.raises(ContainerWriteRefusedError, match="symlink"):
        connection.fnWriteFileFromStream(
            S_PROJECT_NAME, sLink, io.BytesIO(b"through"))
    assert os.path.islink(sLink)
    assert _fsReadFile(sRealFile) == b"real"
    assert _flistStagingFiles(sProjectRoot) == []


def testADirectoryAsTheTargetIsRefused(tProjectAndConnection):
    sProjectRoot, connection = tProjectAndConnection
    os.mkdir(os.path.join(sProjectRoot, "folder"))
    with pytest.raises(ContainerWriteRefusedError):
        connection.fnWriteFileFromStream(
            S_PROJECT_NAME, os.path.join(sProjectRoot, "folder"),
            io.BytesIO(b"x"))


@pytest.mark.falsification
def testTheBytesAreStagedInTheDestinationDirectoryNotElsewhere(
    tProjectAndConnection, monkeypatch,
):
    """Staging beside the target keeps the rename on one filesystem.

    A rename across filesystems is a copy, not atomic, and can be
    interrupted half-done. The same-device property cannot be observed on
    a single-device test machine, so the DIRECTORY handed to the staging
    call is what is asserted.

    Kills: staging in the system temp directory (omitting ``dir=``).
    """
    sProjectRoot, connection = tProjectAndConnection
    sTarget = os.path.join(sProjectRoot, "sub", "out.bin")
    os.mkdir(os.path.dirname(sTarget))
    listStagingDirectories = []
    fnRealMkstemp = hostConnectionModule.tempfile.mkstemp

    def fnRecordingMkstemp(*tArguments, **dictKeywords):
        iDescriptor, sPath = fnRealMkstemp(*tArguments, **dictKeywords)
        listStagingDirectories.append(os.path.dirname(sPath))
        return iDescriptor, sPath

    monkeypatch.setattr(
        hostConnectionModule.tempfile, "mkstemp", fnRecordingMkstemp)
    connection.fnWriteFileFromStream(
        S_PROJECT_NAME, sTarget, io.BytesIO(b"x"))
    assert listStagingDirectories == [os.path.realpath(os.path.dirname(sTarget))]


@pytest.mark.falsification
def testTheBytesAreFlushedToDiskBeforeTheRename(
    tProjectAndConnection, monkeypatch,
):
    """Kills: removing the ``fsync`` before publication."""
    sProjectRoot, connection = tProjectAndConnection
    sTarget = os.path.join(sProjectRoot, "durable.txt")
    listEvents = []
    fnRealFsync, fnRealRename = os.fsync, os.rename
    monkeypatch.setattr(
        os, "fsync", lambda iFile: (listEvents.append("fsync"),
                                    fnRealFsync(iFile))[1])
    monkeypatch.setattr(
        os, "rename", lambda *tArgs, **dictArgs: (listEvents.append("rename"),
                                                  fnRealRename(
                                                      *tArgs, **dictArgs))[1])
    connection.fnWriteFileFromStream(
        S_PROJECT_NAME, sTarget, io.BytesIO(b"payload"))
    assert listEvents == ["fsync", "rename"]


def testAFullDiskRaisesEnospcAndLeavesTheOldFileAlone(
    tProjectAndConnection, monkeypatch,
):
    sProjectRoot, connection = tProjectAndConnection
    sTarget = os.path.join(sProjectRoot, "full.bin")
    with open(sTarget, "wb") as fileOld:
        fileOld.write(b"old")

    def fnFull(iFile):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(os, "fsync", fnFull)
    with pytest.raises(OSError) as infoError:
        connection.fnWriteFileFromStream(
            S_PROJECT_NAME, sTarget, io.BytesIO(b"new"))
    monkeypatch.undo()
    assert infoError.value.errno == errno.ENOSPC
    assert _fsReadFile(sTarget) == b"old"
    assert _flistStagingFiles(sProjectRoot) == []


@pytest.mark.falsification
def testAReplacedFileKeepsItsModeAndANewFileIsReadable(tProjectAndConnection):
    """Kills: dropping the staged file's ``fchmod``, which lands 0600."""
    sProjectRoot, connection = tProjectAndConnection
    sScript = os.path.join(sProjectRoot, "run.sh")
    with open(sScript, "wb") as fileScript:
        fileScript.write(b"#!/bin/sh\n")
    os.chmod(sScript, 0o755)
    connection.fnWriteFileFromStream(
        S_PROJECT_NAME, sScript, io.BytesIO(b"#!/bin/sh\necho hi\n"))
    assert stat.S_IMODE(os.stat(sScript).st_mode) == 0o755
    sFresh = os.path.join(sProjectRoot, "fresh.txt")
    connection.fnWriteFileFromStream(
        S_PROJECT_NAME, sFresh, io.BytesIO(b"x"))
    assert stat.S_IMODE(os.stat(sFresh).st_mode) == 0o644


@pytest.mark.falsification
def testAnUnadmittedStreamedWriteIsRefusedBeforeAnyByteLands(
    tProjectAndConnection,
):
    """The streamed writer is a gated mutation like every other write.

    Kills: removing ``fnAssertContainerWriteAdmitted`` from
    ``HostConnection.fnWriteFileFromStream``.
    """
    sProjectRoot, connection = tProjectAndConnection
    sTarget = os.path.join(sProjectRoot, "gated.txt")
    tokenLane = mutationAdmission.ftokenMarkEnforcedLane()
    try:
        with pytest.raises(mutationAdmission.MutationNotAdmittedError):
            connection.fnWriteFileFromStream(
                S_PROJECT_NAME, sTarget, io.BytesIO(b"x"))
    finally:
        mutationAdmission.fnResetEnforcedLane(tokenLane)
    assert os.listdir(sProjectRoot) == []


@pytest.mark.falsification
def testMissingParentsAreCreatedInsideTheProjectWhenAsked(
    tProjectAndConnection,
):
    """Kills: ignoring ``bCreateParents`` on the host leg."""
    sProjectRoot, connection = tProjectAndConnection
    sTarget = os.path.join(sProjectRoot, "a", "b", "file.txt")
    connection.fnWriteFileFromStream(
        S_PROJECT_NAME, sTarget, io.BytesIO(b"nested"), bCreateParents=True)
    assert _fsReadFile(sTarget) == b"nested"


def testAMissingParentWithoutTheFlagIsFileNotFound(tProjectAndConnection):
    sProjectRoot, connection = tProjectAndConnection
    with pytest.raises(FileNotFoundError):
        connection.fnWriteFileFromStream(
            S_PROJECT_NAME, os.path.join(sProjectRoot, "a", "file.txt"),
            io.BytesIO(b"x"))
    assert os.listdir(sProjectRoot) == []
