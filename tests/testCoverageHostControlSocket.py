"""The host-control client and bind path against a real Unix socket.

The hub side of these edges is a hand-rolled listener on a real
``AF_UNIX`` socket in a short private directory, so the blocking client
meets a real kernel: a listener that answers with a JSON value that is
not an object, one that never answers, and a hub starting over a dead
predecessor's socket file while its port still has a live slot.
"""

import asyncio
import os
import shutil
import socket
import stat
import tempfile
import threading
from types import SimpleNamespace

import pytest

from vaibify.gui import browserSession, hostControlChannel
from vaibify.gui.hostControlChannel import HostControlError

pytestmark = pytest.mark.exclusive

I_HUB_PORT = 8765


@pytest.fixture
def sShortControlDirectory(monkeypatch):
    """A control directory short enough for ``sun_path`` on macOS."""
    sDirectory = tempfile.mkdtemp(prefix="vaibifyCov")
    if len(sDirectory) > 70:
        shutil.rmtree(sDirectory)
        sDirectory = tempfile.mkdtemp(prefix="vaibifyCov", dir="/tmp")
    monkeypatch.setattr(
        hostControlChannel, "_S_CONTROL_DIRECTORY", sDirectory)
    yield sDirectory
    shutil.rmtree(sDirectory, ignore_errors=True)


class OneShotListener:
    """Accept one connection, read its request line, answer or stay mute."""

    def __init__(self, sPath, baAnswer=None):
        self.socketServer = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socketServer.bind(sPath)
        self.socketServer.listen(1)
        self.baAnswer = baAnswer
        self.listRequests = []
        self.eventRelease = threading.Event()
        self.threadServe = threading.Thread(target=self.fnServe, daemon=True)
        self.threadServe.start()

    def fnServe(self):
        socketAccepted, _ = self.socketServer.accept()
        try:
            self.listRequests.append(socketAccepted.recv(65536))
            if self.baAnswer is not None:
                socketAccepted.sendall(self.baAnswer)
            self.eventRelease.wait(5)
        finally:
            socketAccepted.close()

    def fnClose(self):
        self.eventRelease.set()
        self.threadServe.join(timeout=5)
        self.socketServer.close()


def fnSendThroughListener(sShortControlDirectory, baAnswer,
                          fTimeoutSeconds=5.0):
    sPath = hostControlChannel.fsControlSocketPathForPort(I_HUB_PORT)
    listenerOneShot = OneShotListener(sPath, baAnswer)
    try:
        return hostControlChannel.fdictSendHostControlRequest(
            I_HUB_PORT, {"sOperation": "list-reattachable"},
            fTimeoutSeconds=fTimeoutSeconds), listenerOneShot
    finally:
        listenerOneShot.fnClose()


def testAResponseThatIsNotAnObjectIsRefused(sShortControlDirectory):
    with pytest.raises(HostControlError) as excInfo:
        fnSendThroughListener(sShortControlDirectory, b'["not", "a dict"]\n')
    assert str(excInfo.value) == (
        "The hub's host-control response was not an object.")


def testAMuteHubTimesOutWithASentenceNotATraceback(sShortControlDirectory):
    sPath = hostControlChannel.fsControlSocketPathForPort(I_HUB_PORT)
    listenerOneShot = OneShotListener(sPath, baAnswer=None)
    try:
        with pytest.raises(HostControlError) as excInfo:
            hostControlChannel.fdictSendHostControlRequest(
                I_HUB_PORT, {"sOperation": "list-reattachable"},
                fTimeoutSeconds=0.2)
    finally:
        listenerOneShot.fnClose()
    assert "Timed out waiting" in str(excInfo.value)
    assert listenerOneShot.listRequests == [
        b'{"sOperation": "list-reattachable"}\n']


def testAnObjectResponseIsReturnedAsSent(sShortControlDirectory):
    dictResponse, listenerOneShot = fnSendThroughListener(
        sShortControlDirectory, b'{"bAccepted": true, "iCount": 3}\n')
    assert dictResponse == {"bAccepted": True, "iCount": 3}
    assert listenerOneShot.listRequests[0].endswith(b"\n")


def testAHubStartsOverItsOwnPortsLeftoverSocket(
        sShortControlDirectory, monkeypatch):
    """A live slot spares the file from the sweep; the bind reclaims it."""
    import vaibify.config.sessionRegistry as sessionRegistry
    monkeypatch.setattr(
        sessionRegistry, "fdictReadHubSlotByPort",
        lambda iPort: {"iPort": iPort} if iPort == I_HUB_PORT else {})
    sPath = hostControlChannel.fsControlSocketPathForPort(I_HUB_PORT)
    socketLeftover = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    socketLeftover.bind(sPath)
    socketLeftover.close()
    appHub = SimpleNamespace(state=SimpleNamespace(
        iHubPort=I_HUB_PORT, listLifespanStartup=[], listLifespanShutdown=[],
        dictContainerOwners={}, dictMutationSupervisors={},
        dictDurableTaskRecords={},
        dictBrowserSessions=browserSession.fdictCreateBrowserSessionStore()))
    hostControlChannel.fnRegisterHostControlChannel(appHub, {})

    async def fnStartAndStop():
        await appHub.state.listLifespanStartup[0](appHub)
        try:
            statBound = os.lstat(sPath)
            assert stat.S_ISSOCK(statBound.st_mode)
            assert stat.S_IMODE(statBound.st_mode) == 0o600
            return appHub.state.serverHostControl is not None
        finally:
            await appHub.state.listLifespanShutdown[0](appHub)

    assert asyncio.run(fnStartAndStop()) is True, (
        "binding over a live leftover would raise EADDRINUSE; serving "
        "means the leftover socket was unlinked first")
    assert not os.path.exists(sPath), "shutdown unlinks its own socket"
