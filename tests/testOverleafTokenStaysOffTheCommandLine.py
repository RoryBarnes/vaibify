"""The Overleaf token reaches its program on stdin, never on a command line.

Source: ``vaibify/gui/syncDispatcher.py`` and the two connections'
``ftRunProgramWithStdin``.

The host keyring's Overleaf token used to be composed into the text of
``printf '%s' '<token>...' | python3 overleafSync.py ...`` and run as
``bash -c`` inside the container, so it sat in the exec's argument vector,
in ``docker inspect`` and in the in-container process list. These tests
capture what each leg is actually asked to run and assert the token is in
the input stream and nowhere else; the connection tests drive the real
gated launch and the real exec framing.
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

S_TOKEN = "overleaf-token-SENTINEL-9f2c"


class _CapturingConnection:
    """A connection that records the program and the input it was given."""

    def __init__(self):
        self.listCalls = []

    def ftRunProgramWithStdin(self, sContainerId, listCommand, baStdin):
        self.listCalls.append((list(listCommand), baStdin))
        return ExecResult(iExitCode=0, sStdout="ok\n", sStderr="")

    def ftResultExecuteCommand(self, *tArguments, **dictKeywords):
        raise AssertionError(
            "the Overleaf CLI must not be run through shell text")


def _fnAssertTokenOnlyInStdin(connection):
    assert len(connection.listCalls) == 1
    listCommand, baStdin = connection.listCalls[0]
    assert all(isinstance(sWord, str) for sWord in listCommand)
    assert S_TOKEN not in " ".join(listCommand)
    assert baStdin.decode("utf-8").split("\n", 1)[0] == S_TOKEN


@pytest.fixture(autouse=True)
def fixtureKeyringToken():
    with patch.object(
        syncDispatcher, "_fsFetchOverleafToken", return_value=S_TOKEN,
    ):
        yield


@pytest.mark.falsification
def testAPlainPushKeepsTheTokenOffTheCommandLine():
    """Kills: composing the token back into a ``printf | cli`` command."""
    connection = _CapturingConnection()
    syncDispatcher.ftResultPushToOverleaf(
        connection, "cid", ["/work/fig.pdf"], "abcdef012345", "figures")
    _fnAssertTokenOnlyInStdin(connection)
    listCommand, baStdin = connection.listCalls[0]
    assert "push" in listCommand and "abcdef012345" in listCommand
    assert baStdin.decode("utf-8").endswith("/work/fig.pdf")


def testAnAnnotatedPushKeepsTheTokenOffTheCommandLine():
    connection = _CapturingConnection()
    syncDispatcher.ftResultPushToOverleaf(
        connection, "cid", ["/work/fig.pdf"], "abcdef012345", "figures",
        dictWorkflow={"sWorkflowName": "W"},
        sGithubBaseUrl="https://github.com/o/r", sDoi="10.5281/z.1",
        sTexFilename="main.tex", sMirrorSha="abc12345")
    _fnAssertTokenOnlyInStdin(connection)
    listCommand, _ = connection.listCalls[0]
    assert listCommand[listCommand.index("--doi") + 1] == "10.5281/z.1"
    assert listCommand[listCommand.index("--mirror-sha") + 1] == "abc12345"


def testAPullKeepsTheTokenOffTheCommandLine():
    connection = _CapturingConnection()
    syncDispatcher.ftResultPullFromOverleaf(
        connection, "cid", "abcdef012345", ["main.tex"], "/work/tex")
    _fnAssertTokenOnlyInStdin(connection)
    listCommand, _ = connection.listCalls[0]
    assert listCommand[listCommand.index("--target") + 1] == "/work/tex"


def testAHostileTargetStaysOneInertArgument():
    connection = _CapturingConnection()
    sHostile = "/work/$(touch pwned); `id` 'x'"
    syncDispatcher.ftResultPullFromOverleaf(
        connection, "cid", "abcdef012345", ["main.tex"], sHostile)
    listCommand, _ = connection.listCalls[0]
    assert sHostile in listCommand


def testTheDockerLegPutsTheTokenOnStdinNotInTheExecArguments():
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
    syncDispatcher.ftResultPullFromOverleaf(
        connection, "idOverleaf", "abcdef012345", ["main.tex"], "/work")
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
