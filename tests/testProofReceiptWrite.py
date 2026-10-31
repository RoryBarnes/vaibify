"""The PROOF receipt reaches ``.vaibify`` only through the narrow write.

Source: ``vaibify/gui/syncDispatcher.py`` (the narrow write and the
pending-receipt path), ``vaibify/docker/confinedWrite.py`` (the
caller-supplied write's denylist, which must keep refusing
``.vaibify``) and ``vaibify/gui/stateManager.py`` (the managed ignore
list).

The container leg is exercised by RUNNING the rendered write program
locally against a real tree, the way ``tests/testConfinedWriteParity.py``
does, so a planted link is met by the same program a container runs.
"""

import inspect
import os
import subprocess
import sys

import pytest

from vaibify.docker import confinedWrite
from vaibify.docker.confinedWrite import ContainerWriteRefusedError
from vaibify.gui import stateManager, syncDispatcher
from vaibify.gui.pipelineServer import fdictConfinedWriteKeywords
from vaibify.reproducibility.publicationScope import S_PROOF_RECEIPT_REPO_PATH


S_CONTAINER_ID = "receiptContainer"
S_PROMOTION_ID = "promotion-" + "0123456789abcdef" * 2
BA_RECEIPT = b'{\n  "iSchemaVersion": 1\n}\n'
I_PROGRAM_TIMEOUT_SECONDS = 30

bDirectoryRelativeAccess = os.open in os.supports_dir_fd
ffnRequireDirectoryAccess = pytest.mark.skipif(
    not bDirectoryRelativeAccess,
    reason="this Python has no directory-relative file access",
)


def _fsBuildRepository(pathTmp):
    """Return a real repository root holding a .vaibify directory."""
    sRoot = os.path.realpath(str(pathTmp)) + "/repo"
    os.makedirs(sRoot + "/.vaibify")
    os.makedirs(os.path.realpath(str(pathTmp)) + "/outside")
    return sRoot


def _fsOutside(pathTmp):
    return os.path.realpath(str(pathTmp)) + "/outside"


def _fbaReadOrNone(sPath):
    if not os.path.lexists(sPath) or os.path.islink(sPath):
        return None
    with open(sPath, "rb") as fileRead:
        return fileRead.read()


def _ftRunWriteProgram(sPath, baContent, **dictKeywords):
    """Run the container's confined write program here; return exit, stderr."""
    sProgram = confinedWrite.fsRenderConfinedWriteProgram(
        sPath, **dictKeywords)
    resultProcess = subprocess.run(
        [sys.executable, "-c", sProgram], input=baContent,
        capture_output=True, timeout=I_PROGRAM_TIMEOUT_SECONDS,
    )
    return resultProcess.returncode, resultProcess.stderr.decode(
        "utf-8", errors="replace")


class _ProgramRunningConnection:
    """A connection whose one write runs the real program on a local tree."""

    def __init__(self):
        self.listWrites = []

    def fnWriteFileFromStream(
        self, sContainerId, sFilePath, fileSource, iExpectedBytes=None,
        bReplaceAllowed=True, iMode=None, sAuthorizedRoot=None,
        tForbiddenNames=(), bCreateParents=False,
    ):
        self.listWrites.append({
            "sFilePath": sFilePath, "sAuthorizedRoot": sAuthorizedRoot,
            "tForbiddenNames": tuple(tForbiddenNames),
            "bCreateParents": bCreateParents,
        })
        iExit, sError = _ftRunWriteProgram(
            sFilePath, fileSource.read(), iMode=iMode,
            sAuthorizedRoot=sAuthorizedRoot, tForbiddenNames=tForbiddenNames,
            bReplaceAllowed=bReplaceAllowed, iExpectedBytes=iExpectedBytes,
            bCreateParents=bCreateParents,
        )
        if iExit == confinedWrite.I_REFUSED_EXIT_CODE:
            raise ContainerWriteRefusedError(sError)
        if iExit != 0:
            raise OSError(sError)


@pytest.fixture(params=["container", "host"])
def fnWriteLeg(request, monkeypatch):
    """Yield a writer for one leg; the container leg runs the real program."""
    bHost = request.param == "host"
    monkeypatch.setattr(syncDispatcher, "fbIsHostProject", lambda _s: bHost)
    connectionFake = _ProgramRunningConnection()

    def fnWriteReceipt(sRoot):
        syncDispatcher.fnWriteProofReceiptIntoProject(
            connectionFake, S_CONTAINER_ID, sRoot, BA_RECEIPT)

    fnWriteReceipt.connection = connectionFake
    fnWriteReceipt.sLeg = request.param
    return fnWriteReceipt


@ffnRequireDirectoryAccess
@pytest.mark.falsification
def testTheCallerSuppliedWriteStillRefusesVaibify(tmp_path):
    """A write a caller names may never land inside ``.vaibify``.

    Kills: the denylist loses ``.vaibify``.
    """
    sRoot = _fsBuildRepository(tmp_path)
    sTarget = sRoot + "/.vaibify/proofReceipt.json"
    iExit, _sError = _ftRunWriteProgram(
        sTarget, BA_RECEIPT, **fdictConfinedWriteKeywords(sRoot))
    assert iExit == confinedWrite.I_REFUSED_EXIT_CODE
    assert not os.path.lexists(sTarget)


@ffnRequireDirectoryAccess
def testTheNarrowWriteLandsTheReceipt(tmp_path, fnWriteLeg):
    sRoot = _fsBuildRepository(tmp_path)
    fnWriteLeg(sRoot)
    assert _fbaReadOrNone(sRoot + "/" + S_PROOF_RECEIPT_REPO_PATH) == (
        BA_RECEIPT)


@ffnRequireDirectoryAccess
def testThePendingReceiptCreatesItsDirectory(tmp_path, monkeypatch):
    for bHost in (False, True):
        sRoot = _fsBuildRepository(tmp_path / ("host" if bHost else "box"))
        monkeypatch.setattr(
            syncDispatcher, "fbIsHostProject", lambda _s, b=bHost: b)
        syncDispatcher.fnWritePendingReceipt(
            _ProgramRunningConnection(), S_CONTAINER_ID, sRoot,
            S_PROMOTION_ID, BA_RECEIPT)
        assert _fbaReadOrNone(
            sRoot + "/.vaibify/pendingReceipts/" + S_PROMOTION_ID + ".json"
        ) == BA_RECEIPT


def testTheContainerLegHandsTheProgramTheNarrowDenylist(monkeypatch):
    """The program, not only the host, refuses ``.git`` and project.json."""
    monkeypatch.setattr(syncDispatcher, "fbIsHostProject", lambda _s: False)
    listCalls = []

    class _Recording:
        def fnWriteFileFromStream(self, *listArgs, **dictKeywords):
            listCalls.append((listArgs, dictKeywords))

    syncDispatcher.fnWriteProofReceiptIntoProject(
        _Recording(), S_CONTAINER_ID, "/workspace/proj/", BA_RECEIPT)
    listArgs, dictKeywords = listCalls[0]
    assert listArgs[1] == "/workspace/proj/" + S_PROOF_RECEIPT_REPO_PATH
    assert dictKeywords["sAuthorizedRoot"] == "/workspace/proj"
    assert set(dictKeywords["tForbiddenNames"]) == {".git", "project.json"}
    assert dictKeywords["iExpectedBytes"] == len(BA_RECEIPT)


def testTheNarrowWriteRefusesGitAndProjectJson(monkeypatch):
    monkeypatch.setattr(syncDispatcher, "fbIsHostProject", lambda _s: True)
    for sForbidden in (".git/hooks/pre-commit", ".vaibify/projects/project.json"):
        with pytest.raises(ContainerWriteRefusedError):
            syncDispatcher._fnWriteReceiptTarget(
                None, S_CONTAINER_ID, "/nowhere", sForbidden, BA_RECEIPT)


@ffnRequireDirectoryAccess
@pytest.mark.falsification
def testTheNarrowWriteRefusesAPlantedSymlinkAtTheReceipt(
    tmp_path, monkeypatch,
):
    """A link planted at the receipt's name never redirects the write.

    Kills: the narrow write follows links (a plain open of the target).
    """
    monkeypatch.setattr(syncDispatcher, "fbIsHostProject", lambda _s: True)
    sRoot = _fsBuildRepository(tmp_path)
    sVictim = _fsOutside(tmp_path) + "/victim.txt"
    os.symlink(sVictim, sRoot + "/" + S_PROOF_RECEIPT_REPO_PATH)
    with pytest.raises(ContainerWriteRefusedError):
        syncDispatcher.fnWriteProofReceiptIntoProject(
            None, S_CONTAINER_ID, sRoot, BA_RECEIPT)
    assert not os.path.lexists(sVictim)


@ffnRequireDirectoryAccess
@pytest.mark.parametrize("sLinkedDirectory", [
    ".vaibify", ".vaibify/pendingReceipts",
])
def testANarrowWriteRefusesALinkedDirectoryAboveItsTarget(
    tmp_path, monkeypatch, sLinkedDirectory,
):
    for bHost in (False, True):
        pathLeg = tmp_path / ("host" if bHost else "box")
        sRoot = _fsBuildRepository(pathLeg)
        sOutside = _fsOutside(pathLeg)
        sLink = sRoot + "/" + sLinkedDirectory
        if os.path.isdir(sLink):
            os.rmdir(sLink)
        os.makedirs(os.path.dirname(sLink), exist_ok=True)
        os.symlink(sOutside, sLink)
        monkeypatch.setattr(
            syncDispatcher, "fbIsHostProject", lambda _s, b=bHost: b)
        with pytest.raises((ContainerWriteRefusedError, OSError)):
            syncDispatcher.fnWritePendingReceipt(
                _ProgramRunningConnection(), S_CONTAINER_ID, sRoot,
                S_PROMOTION_ID, BA_RECEIPT)
        assert os.listdir(sOutside) == [], (
            "the write followed a link out of the repository")


@ffnRequireDirectoryAccess
def testTheContainerLegRefusesALinkAtTheReceipt(tmp_path, monkeypatch):
    monkeypatch.setattr(syncDispatcher, "fbIsHostProject", lambda _s: False)
    sRoot = _fsBuildRepository(tmp_path)
    sVictim = _fsOutside(tmp_path) + "/victim.txt"
    os.symlink(sVictim, sRoot + "/" + S_PROOF_RECEIPT_REPO_PATH)
    with pytest.raises((ContainerWriteRefusedError, OSError)):
        syncDispatcher.fnWriteProofReceiptIntoProject(
            _ProgramRunningConnection(), S_CONTAINER_ID, sRoot, BA_RECEIPT)
    assert not os.path.lexists(sVictim)


@pytest.mark.falsification
def testThePendingPathRefusesAMalformedPromotionId():
    """An id that is not exactly a promotion id never becomes a path.

    Kills: the id pattern check removed.
    """
    for sBad in (
        "promotion-../../../../etc/passwd",
        "../" + S_PROMOTION_ID,
        S_PROMOTION_ID + "/../../x",
        "promotion-" + "G" * 32,
        "promotion-" + "0" * 31,
        S_PROMOTION_ID + "\n",
    ):
        with pytest.raises(ValueError):
            syncDispatcher.fsPendingReceiptRepoPath(sBad)
    assert syncDispatcher.fsPendingReceiptRepoPath(S_PROMOTION_ID) == (
        ".vaibify/pendingReceipts/" + S_PROMOTION_ID + ".json")


def testAMalformedIdWritesNothing(monkeypatch):
    monkeypatch.setattr(syncDispatcher, "fbIsHostProject", lambda _s: False)
    connectionFake = _ProgramRunningConnection()
    with pytest.raises(ValueError):
        syncDispatcher.fnWritePendingReceipt(
            connectionFake, S_CONTAINER_ID, "/workspace/proj",
            "promotion-../../x", BA_RECEIPT)
    assert connectionFake.listWrites == []


def testTheNarrowWriteTakesNoCallerPath():
    """The two entry points name roots and ids, never a destination path."""
    assert list(inspect.signature(
        syncDispatcher.fnWriteProofReceiptIntoProject).parameters) == [
        "connectionDocker", "sContainerId", "sProjectRepoPath", "baReceipt"]
    assert list(inspect.signature(
        syncDispatcher.fnWritePendingReceipt).parameters) == [
        "connectionDocker", "sContainerId", "sProjectRepoPath",
        "sPromotionId", "baReceipt"]


def testTheManagedIgnoreListCoversPendingReceipts(tmp_path):
    """Untracked files refuse the reproduction export, so git must not see them."""
    sRepo = str(tmp_path)
    subprocess.run(["git", "init", "-q", sRepo], check=True)
    os.makedirs(sRepo + "/.vaibify/pendingReceipts")
    with open(sRepo + "/.vaibify/.gitignore", "w") as fileIgnore:
        fileIgnore.write(stateManager.S_VAIBIFY_GITIGNORE_BODY)
    with open(sRepo + "/.vaibify/pendingReceipts/" + S_PROMOTION_ID
              + ".json", "wb") as filePending:
        filePending.write(BA_RECEIPT)
    sStatus = subprocess.run(
        ["git", "-C", sRepo, "status", "--porcelain",
         "--untracked-files=all"],
        capture_output=True, text=True, check=True,
        env=dict(os.environ, GIT_CONFIG_NOSYSTEM="1",
                 GIT_CONFIG_GLOBAL=os.devnull),
    ).stdout
    assert "pendingReceipts" not in sStatus
    assert ".vaibify/.gitignore" in sStatus
