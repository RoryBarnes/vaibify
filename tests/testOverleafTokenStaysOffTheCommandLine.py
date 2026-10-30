"""The Overleaf token never reaches a container program.

Source: ``vaibify/gui/syncDispatcher.py`` and the two connections'
``ftRunProgramWithStdin``.

The host keyring's Overleaf token used to be composed into the text of
``printf '%s' '<token>...' | python3 overleafSync.py ...`` and run as
``bash -c`` inside the container, then handed to that program on its
standard input. Since 2026-10-29 no container program runs at all:
the push stages the figures out through the confined reader and
commits on the host, the pull clones on the host and lands the sources
through the confined tree write. These tests drive each dispatcher
lane over a connection that refuses every exec, and assert the token
is handed only to the host-side ``overleafSync`` call; the connection
tests below keep ``ftRunProgramWithStdin`` honest for the callers that
still have it.
"""

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tests.confinedWriteHarness import ExecProgramDaemon
from vaibify.config import containerLock, operationJournal
from vaibify.docker import dockerConnection as dockerConnectionModule
from vaibify.docker.dockerConnection import DockerConnection, ExecResult
from vaibify.gui import syncDispatcher
from vaibify.host import hostScratch
from vaibify.host.hostConnection import HostConnection
from vaibify.reproducibility import overleafSync

S_TOKEN = "overleaf-token-SENTINEL-9f2c"


class _NoExecConnection:
    """Serves one figure through the confined reader and refuses every exec."""

    def __init__(self):
        self.listTreeWrites = []

    def fiterReadFileConfined(self, sContainerId, sFilePath, sAuthorizedRoot=None):
        yield b"%PDF-figure"

    def fnMakeDirectory(self, sContainerId, sDirectoryPath, **dictKeywords):
        self.listTreeWrites.append(("mkdir", sDirectoryPath, dictKeywords))

    def fnWriteTreeViaTar(self, sContainerId, sDestinationDirectory, listHostPaths, **dictKeywords):
        self.listTreeWrites.append(
            (sDestinationDirectory, sorted(os.path.basename(s) for s in listHostPaths), dictKeywords),
        )

    def ftRunProgramWithStdin(self, *tArguments, **dictKeywords):
        raise AssertionError("no container program may receive the token")

    def ftResultExecuteCommand(self, *tArguments, **dictKeywords):
        raise AssertionError("the Overleaf lane must not run shell text")


@pytest.fixture
def listHostCalls(monkeypatch):
    """Record every host-side overleafSync call with its token argument."""
    listCalls = []
    monkeypatch.setattr(
        syncDispatcher, "_fsFetchOverleafToken", lambda: S_TOKEN,
    )

    def ftRecordPush(listFigurePaths, sOverleafId, sTargetDirectory, sToken, sMirrorSha=""):
        listCalls.append(("push", sOverleafId, sTargetDirectory, sToken, sMirrorSha))
        return ("pushed", "abc")

    def ftRecordAnnotated(listFigurePaths, sOverleafId, sTargetDirectory, dictWorkflow,
                          sGithubBaseUrl, sDoi, sToken, sTexFilename="main.tex", sMirrorSha=""):
        listCalls.append(("push-annotated", sOverleafId, sDoi, sToken, sMirrorSha))
        return ("pushed", "abc")

    def fnRecordPull(sOverleafId, listPullPaths, sTargetDirectory, sToken):
        listCalls.append(("pull", sOverleafId, list(listPullPaths), sToken))
        with open(os.path.join(sTargetDirectory, "main.tex"), "w") as fileTex:
            fileTex.write("pulled")

    monkeypatch.setattr(overleafSync, "ftPushFiguresToOverleaf", ftRecordPush)
    monkeypatch.setattr(overleafSync, "ftPushAnnotatedToOverleaf", ftRecordAnnotated)
    monkeypatch.setattr(overleafSync, "fnPullTexFromOverleaf", fnRecordPull)
    return listCalls


def testAPlainPushHandsTheTokenOnlyToTheHostProgram(listHostCalls):
    connection = _NoExecConnection()
    iExit, sOut = syncDispatcher.ftResultPushToOverleaf(
        connection, "cid", ["/work/fig.pdf"], "abcdef012345", "figures")
    assert iExit == 0 and "PUSH_STATUS=pushed" in sOut
    assert listHostCalls == [("push", "abcdef012345", "figures", S_TOKEN, "")]


def testAnAnnotatedPushHandsTheTokenOnlyToTheHostProgram(listHostCalls):
    connection = _NoExecConnection()
    iExit, _ = syncDispatcher.ftResultPushToOverleaf(
        connection, "cid", ["/work/fig.pdf"], "abcdef012345", "figures",
        dictWorkflow={"sWorkflowName": "W"},
        sGithubBaseUrl="https://github.com/o/r", sDoi="10.5281/z.1",
        sTexFilename="main.tex", sMirrorSha="abc12345")
    assert iExit == 0
    assert listHostCalls == [
        ("push-annotated", "abcdef012345", "10.5281/z.1", S_TOKEN, "abc12345"),
    ]


@pytest.mark.falsification
def testThePulledSourcesLandThroughTheConfinedTreeWrite(listHostCalls):
    """The pull clones on the host and lands the files confined to the target.

    Kills: dropping the authorized root and the deny list from the tree
    write, which would let a planted link redirect the landing.
    """
    connection = _NoExecConnection()
    iExit, _ = syncDispatcher.ftResultPullFromOverleaf(
        connection, "cid", "abcdef012345", ["main.tex"], "/work/.vaibify/manuscript",
        sProjectRepoPath="/work")
    assert iExit == 0
    assert listHostCalls == [("pull", "abcdef012345", ["main.tex"], S_TOKEN)]
    tMade, (sDestination, listNames, dictKeywords) = connection.listTreeWrites
    assert tMade == ("mkdir", "/work/.vaibify/manuscript",
                     {"sAuthorizedRoot": "/work", "tForbiddenNames": (".git",)})
    assert sDestination == "/work/.vaibify/manuscript"
    assert listNames == ["main.tex"]
    assert dictKeywords["sAuthorizedRoot"] == "/work/.vaibify/manuscript"
    assert ".git" in dictKeywords["tForbiddenNames"]
    assert ".vaibify" in dictKeywords["tForbiddenNames"]


def testAHostileTargetReachesNoShell(listHostCalls):
    connection = _NoExecConnection()
    sHostile = "/work/$(touch pwned); `id` 'x'"
    iExit, _ = syncDispatcher.ftResultPullFromOverleaf(
        connection, "cid", "abcdef012345", ["main.tex"], sHostile)
    assert iExit == 0
    assert connection.listTreeWrites[1][0] == sHostile


def testWithoutAHostTokenNothingRunsAnywhere(monkeypatch):
    monkeypatch.setattr(syncDispatcher, "_fsFetchOverleafToken", lambda: "")
    connection = _NoExecConnection()
    iExit, sOut = syncDispatcher.ftResultPushToOverleaf(
        connection, "cid", ["/work/fig.pdf"], "abcdef012345", "figures")
    assert iExit == 3 and "No Overleaf token" in sOut
    assert connection.listTreeWrites == []


def testTheDockerLegPutsAProgramsInputOnStdinNotInTheExecArguments():
    """The primitive other callers still use keeps its input off the argv."""
    mockDocker = MagicMock()
    mockClient = MagicMock()
    mockDocker.from_env.return_value = mockClient
    mockContainer = MagicMock()
    mockContainer.id = "idOverleaf"
    mockContainer.image.attrs = {"Config": {"User": "researcher"}}
    mockClient.containers.get.return_value = mockContainer
    daemon = ExecProgramDaemon(bExecute=False)
    mockClient.api = daemon
    with patch.object(
        dockerConnectionModule, "_fmoduleGetDocker",
        return_value=mockDocker,
    ):
        connection = DockerConnection()
    connection.ftRunProgramWithStdin(
        "idOverleaf", ["python3", "-c", "pass"], (S_TOKEN + "\nrest").encode("utf-8"))
    dictKeywords = daemon.listExecCreateKeywords[0]
    assert S_TOKEN not in repr(dictKeywords)
    assert dictKeywords["cmd"][0] == "python3"
    assert dictKeywords["user"] == "researcher"
    assert daemon.listReceivedStdin[0].decode().split("\n", 1)[0] == S_TOKEN


# ---------------------------------------------------------------------
# The host leg, through the real gated launch and a real subprocess
# ---------------------------------------------------------------------


@pytest.fixture()
def hostConnection(tmp_path, monkeypatch):
    monkeypatch.setattr(
        operationJournal, "_S_JOURNAL_DIRECTORY", str(tmp_path / "journal"))
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"))
    monkeypatch.setattr(
        hostScratch, "_S_HOST_DIAGNOSTICS_ROOT",
        str(tmp_path / "diagnostics"))
    sProjectRoot = str(tmp_path / "project")
    os.makedirs(sProjectRoot)
    return HostConnection(fnResolveProjectRoot=lambda sResourceId: sProjectRoot)


def testTheHostLegDeliversStdinWithoutPuttingItInTheProcessList(
    hostConnection, tmp_path,
):
    sProbe = (
        "import os,sys;"
        "baInput=sys.stdin.buffer.read();"
        "sys.stdout.write('stdin=%d;' % len(baInput));"
        "sys.stdout.write('argv=' + os.popen('ps -o args= -p %d' "
        "% os.getpid()).read())"
    )
    baPayload = (S_TOKEN + "\n" + "x" * 300000).encode("utf-8")
    tExecResult = hostConnection.ftRunProgramWithStdin(
        "host-conn-proj", [sys.executable, "-c", sProbe], baPayload)
    assert tExecResult.iExitCode == 0, tExecResult.sStderr
    assert f"stdin={len(baPayload)};" in tExecResult.sStdout
    assert S_TOKEN not in tExecResult.sStdout


def testTheHostLegQuotesEveryWordOfTheVector(hostConnection):
    sHostile = "a b; $(echo injected) 'q'"
    tExecResult = hostConnection.ftRunProgramWithStdin(
        "host-conn-proj",
        [sys.executable, "-c", "import sys; print(sys.argv[1])", sHostile],
        b"")
    assert tExecResult.iExitCode == 0, tExecResult.sStderr
    assert tExecResult.sStdout.strip() == sHostile


def testTheCommandGateRefusesAnUnadmittedProgramInAnEnforcedLane(
    hostConnection,
):
    from vaibify.config import mutationAdmission
    tokenLane = mutationAdmission.ftokenMarkEnforcedLane()
    try:
        with pytest.raises(mutationAdmission.MutationNotAdmittedError):
            hostConnection.ftRunProgramWithStdin(
                "host-conn-proj", [sys.executable, "-c", "pass"], b"")
    finally:
        mutationAdmission.fnResetEnforcedLane(tokenLane)


def testTheDockerLegRefusesAnUnadmittedProgramInAnEnforcedLane():
    from vaibify.config import mutationAdmission
    connection = object.__new__(DockerConnection)
    connection._dictContainers = {}
    connection._clientDocker = SimpleNamespace(api=ExecProgramDaemon())
    tokenLane = mutationAdmission.ftokenMarkEnforcedLane()
    try:
        with pytest.raises(mutationAdmission.MutationNotAdmittedError):
            connection.ftRunProgramWithStdin("cid", ["python3", "-c", ""], b"")
    finally:
        mutationAdmission.fnResetEnforcedLane(tokenLane)
    assert connection._clientDocker.api.listExecCreateKeywords == []
