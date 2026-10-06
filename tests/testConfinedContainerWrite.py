"""The backend's container file write is unprivileged and symlink-safe.

Source: ``vaibify/docker/confinedWrite.py`` and
``DockerConnection.fnWriteFileViaTar``.

The write used to hand the daemon a tarball, which the daemon extracts as
root while following every symlink inside the container. These tests drive
the REAL program the write now execs, in a real subprocess against a real
directory tree, through a fake daemon that speaks the real framing over a
real socket pair (``tests/confinedWriteHarness.py``). Nothing here asserts
on a mock's call list alone.
"""

import ast
import io
import os
import socket
import stat
import subprocess
import sys
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tests.confinedWriteHarness import ExecProgramDaemon
from vaibify.docker import confinedWrite
from vaibify.docker import dockerConnection as dockerConnectionModule
from vaibify.docker.confinedWrite import (
    ContainerWriteRefusedError,
    fsRenderConfinedWriteProgram,
)
from vaibify.docker.dockerConnection import DockerConnection


def _fexecuteProgram(sProgram, baStdin=b""):
    return subprocess.run(
        [sys.executable, "-c", sProgram], input=baStdin,
        capture_output=True,
    )


def _fsRealPath(pathTmp):
    return os.path.realpath(str(pathTmp))


def _fconnectionOverDaemon(daemon, sContainerId):
    mockDocker = MagicMock()
    mockClient = MagicMock()
    mockDocker.from_env.return_value = mockClient
    mockContainer = MagicMock()
    mockContainer.id = sContainerId
    mockContainer.image.attrs = {"Config": {"User": "researcher"}}
    mockClient.containers.get.return_value = mockContainer
    mockClient.api = daemon
    with patch.object(
        dockerConnectionModule, "_fmoduleGetDocker",
        return_value=mockDocker,
    ):
        return DockerConnection()


# ---------------------------------------------------------------------
# The program itself, against a real directory tree
# ---------------------------------------------------------------------


def testWritesTheBytesWithTheRequestedModeOwnedByTheCaller(tmp_path):
    sRoot = _fsRealPath(tmp_path)
    sTarget = os.path.join(sRoot, "secret.env")
    resultProc = _fexecuteProgram(
        fsRenderConfinedWriteProgram(sTarget, iMode=0o600, sAuthorizedRoot=sRoot),
        b"token=abc",
    )
    assert resultProc.returncode == 0, resultProc.stderr
    infoFile = os.stat(sTarget)
    assert stat.S_IMODE(infoFile.st_mode) == 0o600
    assert infoFile.st_uid == os.getuid()
    with open(sTarget, "rb") as fileTarget:
        assert fileTarget.read() == b"token=abc"


@pytest.mark.falsification
def testDefaultModeIsReadableAndIndependentOfTheUmask(tmp_path):
    """The requested mode is applied explicitly, whatever the umask says.

    Kills: removing the program's ``fchmod``, which would leave the
    private 0600 the file is created with (or the umask's choice).
    """
    sRoot = _fsRealPath(tmp_path)
    sTarget = os.path.join(sRoot, "plain.txt")
    sProgram = fsRenderConfinedWriteProgram(sTarget)
    iPreviousUmask = os.umask(0o077)
    try:
        resultProc = _fexecuteProgram(sProgram, b"x")
    finally:
        os.umask(iPreviousUmask)
    assert resultProc.returncode == 0, resultProc.stderr
    assert stat.S_IMODE(os.stat(sTarget).st_mode) == 0o644


def testALargePayloadArrivesIntactOverStdin(tmp_path):
    sRoot = _fsRealPath(tmp_path)
    sTarget = os.path.join(sRoot, "big.bin")
    baPayload = os.urandom(1 << 20) * 9
    resultProc = _fexecuteProgram(
        fsRenderConfinedWriteProgram(sTarget), baPayload)
    assert resultProc.returncode == 0, resultProc.stderr
    with open(sTarget, "rb") as fileTarget:
        assert fileTarget.read() == baPayload


def testReplacingAFileLeavesNoTemporaryBehind(tmp_path):
    sRoot = _fsRealPath(tmp_path)
    sTarget = os.path.join(sRoot, "state.json")
    with open(sTarget, "wb") as fileTarget:
        fileTarget.write(b"old")
    resultProc = _fexecuteProgram(
        fsRenderConfinedWriteProgram(sTarget), b"new")
    assert resultProc.returncode == 0, resultProc.stderr
    assert os.listdir(sRoot) == ["state.json"]
    with open(sTarget, "rb") as fileTarget:
        assert fileTarget.read() == b"new"


@pytest.mark.falsification
def testASymlinkedParentIsRefusedAndNothingLandsOutside(tmp_path):
    """A symlink planted in place of a parent directory is refused.

    Kills: dropping ``O_NOFOLLOW`` from the directory walk, which makes
    the program follow the planted link as the daemon's tar extraction did.
    """
    sRoot = _fsRealPath(tmp_path / "project")
    sOutside = _fsRealPath(tmp_path / "outside")
    os.makedirs(sRoot)
    os.makedirs(sOutside)
    os.symlink(sOutside, os.path.join(sRoot, "linked"))
    sTarget = os.path.join(sRoot, "linked", "planted.txt")
    resultProc = _fexecuteProgram(
        fsRenderConfinedWriteProgram(sTarget, sAuthorizedRoot=sRoot), b"x")
    assert resultProc.returncode == confinedWrite.I_REFUSED_EXIT_CODE
    assert b"symlink" in resultProc.stderr
    assert os.listdir(sOutside) == []


def testAFinalSymlinkIsRefusedAndItsTargetIsUntouched(tmp_path):
    sRoot = _fsRealPath(tmp_path / "project")
    os.makedirs(sRoot)
    sVictim = os.path.join(_fsRealPath(tmp_path), "victim.txt")
    with open(sVictim, "wb") as fileVictim:
        fileVictim.write(b"precious")
    os.symlink(sVictim, os.path.join(sRoot, "alias.txt"))
    resultProc = _fexecuteProgram(
        fsRenderConfinedWriteProgram(
            os.path.join(sRoot, "alias.txt"), sAuthorizedRoot=sRoot),
        b"overwritten")
    assert resultProc.returncode == confinedWrite.I_REFUSED_EXIT_CODE
    with open(sVictim, "rb") as fileVictim:
        assert fileVictim.read() == b"precious"


def testASymlinkPointingIntoGitInternalsIsRefused(tmp_path):
    sRoot = _fsRealPath(tmp_path)
    os.makedirs(os.path.join(sRoot, ".git", "hooks"))
    os.makedirs(os.path.join(sRoot, "docs"))
    os.symlink(
        os.path.join(sRoot, ".git"), os.path.join(sRoot, "docs", "innocent"))
    sTarget = os.path.join(sRoot, "docs", "innocent", "hooks", "pre-commit")
    resultProc = _fexecuteProgram(
        fsRenderConfinedWriteProgram(
            sTarget, sAuthorizedRoot=sRoot,
            tForbiddenNames=confinedWrite.T_WRITE_DENYLISTED_NAMES),
        b"#!/bin/sh\nexit 0\n")
    assert resultProc.returncode == confinedWrite.I_REFUSED_EXIT_CODE
    assert os.listdir(os.path.join(sRoot, ".git", "hooks")) == []


@pytest.mark.falsification
def testForbiddenNamesAreRefusedByNameBeforeAnythingIsOpened(tmp_path):
    """Metadata names are refused by the program itself, by name.

    Each denylisted name is tried below an empty root: the refusal must
    come from the name check (exit 3, "not permitted"), not from a
    missing directory, and nothing may be created.

    Kills: disabling the program's forbidden-name check.
    """
    sRoot = _fsRealPath(tmp_path)
    for sForbidden in confinedWrite.T_WRITE_DENYLISTED_NAMES:
        resultProc = _fexecuteProgram(
            fsRenderConfinedWriteProgram(
                os.path.join(sRoot, sForbidden, "payload"),
                sAuthorizedRoot=sRoot,
                tForbiddenNames=confinedWrite.T_WRITE_DENYLISTED_NAMES),
            b"x")
        assert resultProc.returncode == confinedWrite.I_REFUSED_EXIT_CODE
        assert b"not permitted" in resultProc.stderr
        assert os.listdir(sRoot) == []


def testAPathOutsideItsAuthorizedRootIsRefusedBeforeRunning(tmp_path):
    sRoot = _fsRealPath(tmp_path / "project")
    with pytest.raises(ValueError):
        fsRenderConfinedWriteProgram(
            os.path.join(_fsRealPath(tmp_path), "elsewhere.txt"),
            sAuthorizedRoot=sRoot)
    with pytest.raises(ValueError):
        fsRenderConfinedWriteProgram(sRoot, sAuthorizedRoot=sRoot)


def testADirectoryAtTheFinalComponentIsRefused(tmp_path):
    sRoot = _fsRealPath(tmp_path)
    os.makedirs(os.path.join(sRoot, "folder"))
    resultProc = _fexecuteProgram(
        fsRenderConfinedWriteProgram(os.path.join(sRoot, "folder")), b"x")
    assert resultProc.returncode == confinedWrite.I_REFUSED_EXIT_CODE


def testAMissingParentIsAnErrorNotARefusalAndLeavesNoTemporary(tmp_path):
    sRoot = _fsRealPath(tmp_path)
    resultProc = _fexecuteProgram(
        fsRenderConfinedWriteProgram(os.path.join(sRoot, "nope", "f.txt")),
        b"x")
    assert resultProc.returncode not in (0, confinedWrite.I_REFUSED_EXIT_CODE)
    assert os.listdir(sRoot) == []


def testAHostilePathStaysAnInertLiteralInTheProgram(tmp_path):
    sRoot = _fsRealPath(tmp_path)
    sHostile = os.path.join(sRoot, "a'; import os; os.system('x') #.txt")
    sProgram = fsRenderConfinedWriteProgram(sHostile)
    treeProgram = ast.parse(sProgram)
    listLiterals = [
        nodeConstant.value for nodeConstant in ast.walk(treeProgram)
        if isinstance(nodeConstant, ast.Constant)
    ]
    assert sHostile in listLiterals
    assert [
        nodeCall for nodeCall in ast.walk(treeProgram)
        if isinstance(nodeCall, ast.Call)
        and getattr(nodeCall.func, "attr", "") == "system"
    ] == []
    resultProc = _fexecuteProgram(sProgram, b"ok")
    assert resultProc.returncode == 0, resultProc.stderr
    assert os.listdir(sRoot) == [os.path.basename(sHostile)]


@pytest.mark.parametrize("objBadValue", [
    "relative/path", "/tmp/with\nnewline", "/tmp/with\x00nul", 7, None,
])
def testOnlyAbsoluteCleanStringPathsAreAdmitted(objBadValue):
    with pytest.raises((ValueError, TypeError)):
        fsRenderConfinedWriteProgram(objBadValue)


def testForbiddenNamesMustBeStrings():
    with pytest.raises(TypeError):
        fsRenderConfinedWriteProgram("/tmp/x", tForbiddenNames=(".git", 1))


@pytest.mark.falsification
def testAComponentSwappedForASymlinkAfterItWasOpenedCannotRedirect(
    tmp_path, monkeypatch,
):
    """The race closes by construction: descriptors, not names.

    Kills: walking the path by NAME again for each component instead of
    against the descriptor already held, which re-resolves a swapped link.

    Runs the program in-process and swaps the already-opened parent
    directory for a symlink to an outside directory the moment the walk
    has descended into it. A write that re-resolved the NAME would land
    outside; this one lands in the directory it holds a descriptor for.
    """
    sRoot = _fsRealPath(tmp_path / "project")
    sOutside = _fsRealPath(tmp_path / "outside")
    os.makedirs(os.path.join(sRoot, "inner"))
    os.makedirs(sOutside)
    sTarget = os.path.join(sRoot, "inner", "result.txt")
    sProgram = fsRenderConfinedWriteProgram(sTarget, sAuthorizedRoot=sRoot)
    fnRealOpen = os.open
    dictState = {"bSwapped": False}

    def fiOpenThenSwap(sName, iFlags, *tArguments, **dictKeywords):
        iDescriptor = fnRealOpen(sName, iFlags, *tArguments, **dictKeywords)
        if sName == "inner" and not dictState["bSwapped"]:
            dictState["bSwapped"] = True
            os.rename(
                os.path.join(sRoot, "inner"), os.path.join(sRoot, "moved"))
            os.symlink(sOutside, os.path.join(sRoot, "inner"))
        return iDescriptor

    monkeypatch.setattr(os, "open", fiOpenThenSwap)
    monkeypatch.setattr(
        sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b"payload")))
    exec(compile(sProgram, "<confined-write>", "exec"), {"__name__": "x"})
    monkeypatch.undo()
    assert dictState["bSwapped"]
    assert os.listdir(sOutside) == []
    with open(os.path.join(sRoot, "moved", "result.txt"), "rb") as fileLanded:
        assert fileLanded.read() == b"payload"


# ---------------------------------------------------------------------
# DockerConnection over a daemon that really runs the program
# ---------------------------------------------------------------------


@pytest.mark.falsification
def testTheBackendWriteExecsAsTheContainerUserNeverRoot(tmp_path):
    """The confined write runs with no more authority than the container user.

    Kills: naming root as the exec user of the stdin-fed program.
    """
    daemon = ExecProgramDaemon()
    connection = _fconnectionOverDaemon(daemon, "idUserNeverRoot")
    sTarget = os.path.join(_fsRealPath(tmp_path), "out.txt")
    connection.fnWriteFile("idUserNeverRoot", sTarget, b"data")
    dictKeywords = daemon.listExecCreateKeywords[0]
    assert dictKeywords["user"] == "researcher"
    assert dictKeywords["stdin"] is True
    assert dictKeywords["cmd"][:2] == ["python3", "-c"]
    assert dictKeywords["cmd"][2].count(repr(sTarget)) >= 1
    assert daemon.listReceivedStdin == [b"data"]
    with open(sTarget, "rb") as fileTarget:
        assert fileTarget.read() == b"data"


def testTheBackendWriteNeverCallsPutArchive(tmp_path):
    daemon = ExecProgramDaemon()
    connection = _fconnectionOverDaemon(daemon, "idNoArchive")
    connection.fnWriteFile(
        "idNoArchive", os.path.join(_fsRealPath(tmp_path), "f"), b"1")
    mockContainer = connection.fcontainerGetById("idNoArchive")
    mockContainer.put_archive.assert_not_called()


def testASecretFileIsPrivateOwnedByTheWriterAndIntact(tmp_path):
    daemon = ExecProgramDaemon()
    connection = _fconnectionOverDaemon(daemon, "idSecret")
    sTarget = os.path.join(_fsRealPath(tmp_path), "session.env")
    connection.fnWriteFile(
        "idSecret", sTarget, b"TOKEN=abc\n", iMode=0o600, iUid=0, iGid=0)
    infoFile = os.stat(sTarget)
    assert stat.S_IMODE(infoFile.st_mode) == 0o600
    assert infoFile.st_uid == os.getuid()


def testALargePayloadCrossesTheDaemonSocketIntact(tmp_path):
    daemon = ExecProgramDaemon()
    connection = _fconnectionOverDaemon(daemon, "idLarge")
    sTarget = os.path.join(_fsRealPath(tmp_path), "large.bin")
    baPayload = os.urandom(1 << 20) * 12
    connection.fnWriteFile("idLarge", sTarget, baPayload)
    with open(sTarget, "rb") as fileTarget:
        assert fileTarget.read() == baPayload
    assert daemon.listReceivedStdin == [baPayload]


def testAParentSwappedForASymlinkBetweenRequestAndWriteIsRefused(tmp_path):
    """Deterministic swap-during-write through the real backend path."""
    sRoot = _fsRealPath(tmp_path / "project")
    sOutside = _fsRealPath(tmp_path / "outside")
    os.makedirs(os.path.join(sRoot, "docs"))
    os.makedirs(sOutside)

    def fnSwapParentForSymlink():
        os.rename(os.path.join(sRoot, "docs"), os.path.join(sRoot, "kept"))
        os.symlink(sOutside, os.path.join(sRoot, "docs"))

    daemon = ExecProgramDaemon(fnBeforeRun=fnSwapParentForSymlink)
    connection = _fconnectionOverDaemon(daemon, "idSwap")
    with pytest.raises(ContainerWriteRefusedError):
        connection.fnWriteFile(
            "idSwap", os.path.join(sRoot, "docs", "note.txt"), b"x",
            sAuthorizedRoot=sRoot,
            tForbiddenNames=confinedWrite.T_WRITE_DENYLISTED_NAMES)
    assert os.listdir(sOutside) == []


def testAMetadataSymlinkIsRefusedThroughTheBackendPath(tmp_path):
    sRoot = _fsRealPath(tmp_path)
    os.makedirs(os.path.join(sRoot, ".vaibify"))
    os.symlink(os.path.join(sRoot, ".vaibify"), os.path.join(sRoot, "shortcut"))
    daemon = ExecProgramDaemon()
    connection = _fconnectionOverDaemon(daemon, "idMeta")
    with pytest.raises(ContainerWriteRefusedError):
        connection.fnWriteFile(
            "idMeta", os.path.join(sRoot, "shortcut", "state.json"), b"{}",
            sAuthorizedRoot=sRoot,
            tForbiddenNames=confinedWrite.T_WRITE_DENYLISTED_NAMES)
    assert os.listdir(os.path.join(sRoot, ".vaibify")) == []


def testAGenuineWriteFailureIsAnOSErrorNotARefusal(tmp_path):
    daemon = ExecProgramDaemon()
    connection = _fconnectionOverDaemon(daemon, "idMissingParent")
    with pytest.raises(OSError) as infoError:
        connection.fnWriteFile(
            "idMissingParent",
            os.path.join(_fsRealPath(tmp_path), "absent", "f.txt"), b"x")
    assert not isinstance(infoError.value, ContainerWriteRefusedError)


def testAnUnadmittedWriteInAnEnforcedLaneNeverReachesTheDaemon(tmp_path):
    from vaibify.config import mutationAdmission
    daemon = ExecProgramDaemon()
    connection = _fconnectionOverDaemon(daemon, "idUnadmitted")
    tokenLane = mutationAdmission.ftokenMarkEnforcedLane()
    try:
        with pytest.raises(mutationAdmission.MutationNotAdmittedError):
            connection.fnWriteFile(
                "idUnadmitted", os.path.join(_fsRealPath(tmp_path), "f"), b"x")
    finally:
        mutationAdmission.fnResetEnforcedLane(tokenLane)
    assert daemon.listExecCreateKeywords == []


# ---------------------------------------------------------------------
# The socket exchange, with a peer that half-closes like the daemon
# ---------------------------------------------------------------------


def testTheExchangeHalfClosesSoThePeerSeesEndOfInput():
    socketNear, socketFar = socket.socketpair()
    listReceived = []

    def fnPeer():
        socketFar.settimeout(30)
        listChunks = []
        try:
            while True:
                baChunk = socketFar.recv(65536)
                if not baChunk:
                    break
                listChunks.append(baChunk)
            listReceived.append(b"".join(listChunks))
            socketFar.sendall(b"\x02\x00\x00\x00\x00\x00\x00\x04warn")
        finally:
            socketFar.close()

    threadPeer = threading.Thread(target=fnPeer, daemon=True)
    threadPeer.start()
    baStdout, baStderr = dockerConnectionModule._ftExchangeWithExecSocket(
        socketNear, b"hello" * 100000)
    threadPeer.join(timeout=30)
    assert listReceived == [b"hello" * 100000]
    assert (baStdout, baStderr) == (b"", b"warn")
