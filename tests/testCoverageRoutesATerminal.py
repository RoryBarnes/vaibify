"""The terminal WebSocket, served past the gate, over the real application.

``tests/testTerminalRoutesCoverage.py`` pins the gate and the host/container
branch with the serve function stubbed out. This file drives what happens
AFTER the gate admits a session: which session class is built and with
which key, what the first bytes on the socket are, what a failed start
tells the researcher, and that the session is closed and forgotten when
the socket goes. Only the shell itself is simulated -- the Docker exec and
the host PTY are the true boundaries -- so the origin check, the
per-browser credential, the bound lease and the id-to-name resolution all
run for real.

The container NAME (``test-container``) and ID (``draftcontainer``) are
distinct: the containment record is keyed by name, the exec by id, and a
session built with the two swapped would fail here.
"""

import json
import os
import shlex
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tests.testCarrierMigratedRoutes import (
    DockerDoubleThatCallsTheRealGates,
    _tConnectGatedClient,
)
from tests.testDraftRoutes import S_CONTAINER_ID, _fnConnect
from vaibify.config import registryManager
from vaibify.gui import attributionLog, browserSession, pipelineServer
from vaibify.gui.routes import terminalRoutes


S_CONTAINER_NAME = "test-container"
S_SESSION_ID = "terminalSessionCharlie"
S_REMOTE_HOSTNAME = "computeNodeDelta"
BA_SHELL_PROMPT = b"shellPrompt$ "
DICT_LOOPBACK_ORIGIN = {"origin": "http://localhost"}


class _FakeShellSession:
    """Stand in for the exec or PTY: records what the route asked of it."""

    listBuilt = []
    sStartFailure = ""

    def __init__(self, *tArguments, **dictKeywords):
        self.tArguments = tArguments
        self.dictKeywords = dictKeywords
        self.sSessionId = S_SESSION_ID
        self._bRunning = True
        self.listInput = []
        self.bClosed = False
        type(self).listBuilt.append(self)

    def fnStart(self):
        if type(self).sStartFailure:
            raise RuntimeError(type(self).sStartFailure)

    def fbaReadOutput(self):
        """Emit one prompt, then end, so the read loop settles by itself."""
        self._bRunning = False
        return BA_SHELL_PROMPT

    def fnSendInput(self, baInput):
        self.listInput.append(baInput)

    def fnResize(self, iRows, iColumns):
        return None

    def fnClose(self):
        self.bClosed = True


class _FakeContainerShell(_FakeShellSession):
    listBuilt = []
    sStartFailure = ""


class _FakeHostShell(_FakeShellSession):
    listBuilt = []
    sStartFailure = ""


class _GatedDockerWithFileSystem(DockerDoubleThatCallsTheRealGates):
    """Answer the attribution log's file probes from the content store."""

    def fbContainerPathIsFile(self, sContainerId, sPath):
        DockerDoubleThatCallsTheRealGates.fbContainerPathIsFile(
            self, sContainerId, sPath,
        )
        return sPath in self._dictFiles

    def ftResultExecuteCommand(self, sContainerId, sCommand, sWorkdir=None):
        """Model the atomic write's rename, which the base double ignores."""
        tResult = DockerDoubleThatCallsTheRealGates.ftResultExecuteCommand(
            self, sContainerId, sCommand, sWorkdir,
        )
        listWords = shlex.split(sCommand)
        if listWords[:2] == ["mv", "-f"] and len(listWords) == 4 and (
            listWords[2] in self._dictFiles
        ):
            self._dictFiles[listWords[3]] = self._dictFiles.pop(listWords[2])
        return tResult


@pytest.fixture(autouse=True)
def fixtureIsolatedRegistryAndShells(monkeypatch, tmp_path):
    """An empty registry, and both shell classes replaced by recorders."""
    sRegistryDirectory = str(tmp_path / "registryHome")
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_DIRECTORY", sRegistryDirectory,
    )
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH",
        os.path.join(sRegistryDirectory, "registry.json"),
    )
    monkeypatch.setattr(
        registryManager, "_S_LOCK_PATH",
        os.path.join(sRegistryDirectory, "registry.lock"),
    )
    for classShell in (_FakeContainerShell, _FakeHostShell):
        monkeypatch.setattr(classShell, "listBuilt", [])
        monkeypatch.setattr(classShell, "sStartFailure", "")
    monkeypatch.setattr(terminalRoutes, "TerminalSession", _FakeContainerShell)
    monkeypatch.setattr(terminalRoutes, "HostTerminalSession", _FakeHostShell)


def _tConnect(connectionDocker=None):
    client, connectionDocker = _tConnectGatedClient(
        connectionDocker or _GatedDockerWithFileSystem(),
    )
    return client, connectionDocker


def _fsTerminalUrl(client):
    return (
        f"/ws/terminal/{S_CONTAINER_ID}"
        f"?sToken={client.headers['X-Session-Token']}"
        f"&sLeaseId={client.headers['X-Vaibify-Lease']}"
    )


def _fnRegisterAsHostProject():
    os.makedirs(registryManager._S_REGISTRY_DIRECTORY, exist_ok=True)
    with open(registryManager._S_REGISTRY_PATH, "w") as fileOut:
        json.dump({"listProjects": [{
            "sName": S_CONTAINER_NAME, "sDirectory": "/unused",
            "sMode": "host",
        }]}, fileOut)


def testAContainerShellIsBuiltByIdAndContainedByName():
    """The exec addresses the container id; the record, its name."""
    client, _connectionDocker = _tConnect()
    recordOwner = client.app.state.dictContainerOwners[S_CONTAINER_NAME]
    client.app.state.dictRouteContext["containerUsers"][S_CONTAINER_ID] = (
        "researcherEcho"
    )
    with client.websocket_connect(
        _fsTerminalUrl(client), headers=DICT_LOOPBACK_ORIGIN,
    ) as websocketTerminal:
        assert websocketTerminal.receive_json() == {
            "sType": "connected", "sSessionId": S_SESSION_ID,
        }
        assert websocketTerminal.receive_bytes() == BA_SHELL_PROMPT
        websocketTerminal.send_bytes(b"echo hello\n")
    assert _FakeHostShell.listBuilt == []
    sessionBuilt = _FakeContainerShell.listBuilt[0]
    assert sessionBuilt.tArguments[1] == S_CONTAINER_ID
    assert sessionBuilt.dictKeywords["sUser"] == "researcherEcho"
    dictContainment = sessionBuilt.dictKeywords["dictContainment"]
    assert dictContainment["sContainerName"] == S_CONTAINER_NAME
    assert dictContainment["iOwnerGeneration"] == (
        recordOwner.iOwnerGeneration
    )
    assert dictContainment["appState"] is client.app.state


def testAClosedSocketClosesTheShellAndForgetsIt():
    client, _connectionDocker = _tConnect()
    with client.websocket_connect(
        _fsTerminalUrl(client), headers=DICT_LOOPBACK_ORIGIN,
    ) as websocketTerminal:
        websocketTerminal.receive_json()
        websocketTerminal.send_bytes(b"ls\n")
    sessionBuilt = _FakeContainerShell.listBuilt[0]
    assert sessionBuilt.bClosed is True
    assert S_SESSION_ID not in client.app.state.dictRouteContext["terminals"]


def testAShellThatFailsToStartSaysWhyAndIsNeverRegistered():
    _FakeContainerShell.sStartFailure = "exec create refused by daemon"
    client, _connectionDocker = _tConnect()
    with client.websocket_connect(
        _fsTerminalUrl(client), headers=DICT_LOOPBACK_ORIGIN,
    ) as websocketTerminal:
        assert websocketTerminal.receive_json() == {
            "sType": "error",
            "sMessage": "Terminal failed: exec create refused by daemon",
        }
    assert client.app.state.dictRouteContext["terminals"] == {}


def testAHostProjectGetsThePtyTwinAndTheOwnMachineBanner():
    """The first bytes say the shell runs on the researcher's machine."""
    client, _connectionDocker = _tConnect()
    _fnRegisterAsHostProject()
    with client.websocket_connect(
        _fsTerminalUrl(client), headers=DICT_LOOPBACK_ORIGIN,
    ) as websocketTerminal:
        assert websocketTerminal.receive_json()["sType"] == "connected"
        baBanner = websocketTerminal.receive_bytes()
        assert websocketTerminal.receive_bytes() == BA_SHELL_PROMPT
    assert baBanner.decode("utf-8") == terminalRoutes.S_HOST_TERMINAL_BANNER
    assert "YOUR OWN machine" in baBanner.decode("utf-8")
    assert _FakeContainerShell.listBuilt == []
    sessionBuilt = _FakeHostShell.listBuilt[0]
    assert sessionBuilt.tArguments[1] == S_CONTAINER_NAME
    assert sessionBuilt.tArguments[2]["sContainerName"] == S_CONTAINER_NAME


def _fclientConnectedFromARemoteBrowser(connectionDocker):
    """Connect as a browser whose session was minted for a tunnel."""
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker", lambda: connectionDocker,
    ):
        app = pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace", sTerminalUserArg="testuser",
        )
    dictStore = app.state.dictBrowserSessions
    _sSessionId, sRemoteCredential = browserSession.ftRedeemCapability(
        dictStore, browserSession.fsMintBootstrapCapability(
            dictStore, bRemoteSession=True,
        ),
    )
    client = TestClient(app, headers={"X-Session-Token": sRemoteCredential})
    _fnConnect(client)
    return client


def testAShellReachedOverATunnelNamesTheMachineItRunsOn(
    monkeypatch,
):
    """"Your own machine" would be false through a tunnel; name it."""
    monkeypatch.setattr(
        "socket.gethostname", lambda: S_REMOTE_HOSTNAME,
    )
    client = _fclientConnectedFromARemoteBrowser(
        _GatedDockerWithFileSystem(),
    )
    with client.websocket_connect(
        _fsTerminalUrl(client), headers=DICT_LOOPBACK_ORIGIN,
    ) as websocketTerminal:
        websocketTerminal.receive_json()
        sBanner = websocketTerminal.receive_bytes().decode("utf-8")
    assert sBanner == terminalRoutes.fsRemoteTerminalBanner(S_REMOTE_HOSTNAME)
    assert S_REMOTE_HOSTNAME in sBanner
    assert "YOUR OWN machine" not in sBanner
    assert "quiescence as unproven" in sBanner


def testTheRemoteBannerFallsBackWhenTheHostnameIsUnknown():
    sBanner = terminalRoutes.fsRemoteTerminalBanner("")
    assert "the remote machine" in sBanner
    assert "quiescence as unproven" in sBanner


def testASupervisedProjectRecordsTheSessionOpenAndClose():
    """The pair is what makes a long session an attributive interval."""
    client, connectionDocker = _tConnect()
    dictWorkflow = client.app.state.dictRouteContext["workflows"][
        S_CONTAINER_ID
    ]
    dictWorkflow["dictAiProvenance"] = {
        "dictSupervision": {"bEnabled": True},
    }
    with client.websocket_connect(
        _fsTerminalUrl(client), headers=DICT_LOOPBACK_ORIGIN,
    ) as websocketTerminal:
        websocketTerminal.receive_json()
    listEventFiles = [
        baContent for sPath, baContent in connectionDocker._dictFiles.items()
        if sPath.endswith(attributionLog.S_ATTRIBUTION_EVENTS_PATH)
    ]
    assert len(listEventFiles) == 1
    listEvents = [
        json.loads(sLine)
        for sLine in listEventFiles[0].decode("utf-8").splitlines()
    ]
    assert [
        (dictEvent["sChannel"], dictEvent["sDetail"])
        for dictEvent in listEvents
    ] == [
        (attributionLog.S_TERMINAL_CHANNEL,
         attributionLog.S_TERMINAL_OPENED_DETAIL),
        (attributionLog.S_TERMINAL_CHANNEL,
         attributionLog.S_TERMINAL_CLOSED_DETAIL),
    ]
