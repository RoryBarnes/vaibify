"""Quit reaches the windows this hub spawned, over their real control sockets.

Each child here is a real process (``tests/quitChildHub.py``) with its
own admission gate, a real hub slot for its port and the real host
control socket. The parent is the real hub application, running
``hubQuit.fdictRunQuit`` in this test's event loop, so its settlement,
renewal and failure contract talk to the children exactly as a Quit
does -- and a child can be SIGKILLed for real in the middle of one.
"""

import asyncio
import os
import pathlib
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from vaibify.config import containerLock, mutationAdmission, sessionRegistry
from vaibify.gui import commitCarrier, hostControlChannel, hubQuit
from vaibify.gui import pipelineServer
from vaibify.gui.containerOwnership import OwnerRecord


# Real child hub processes, each holding a hub slot and a unix socket.
pytestmark = pytest.mark.exclusive

PATH_REPOSITORY = pathlib.Path(__file__).resolve().parent.parent
PATH_CHILD_SCRIPT = PATH_REPOSITORY / "tests" / "quitChildHub.py"
S_PARENT_PROJECT = "parentproj"
S_PARENT_CONTAINER_ID = "parentcid"
F_CHILD_READY_SECONDS = 30.0


@pytest.fixture
def sControlDirectory(monkeypatch):
    """A control directory short enough for an AF_UNIX path on macOS."""
    sDirectory = tempfile.mkdtemp(prefix="vaibifyQuit")
    if len(sDirectory) > 70:
        sDirectory = tempfile.mkdtemp(prefix="vaibifyQuit", dir="/tmp")
    monkeypatch.setattr(hostControlChannel, "_S_CONTROL_DIRECTORY", sDirectory)
    yield sDirectory
    for sEntry in os.listdir(sDirectory):
        os.unlink(os.path.join(sDirectory, sEntry))
    os.rmdir(sDirectory)


@pytest.fixture
def fixtureQuickQuit(monkeypatch):
    monkeypatch.setattr(hubQuit, "F_SETTLE_POLL_SECONDS", 0.05)
    monkeypatch.setattr(hubQuit, "F_CHILD_CONTROL_TIMEOUT_SECONDS", 3.0)


def _fiFreePort():
    socketProbe = socket.socket()
    socketProbe.bind(("127.0.0.1", 0))
    iPort = socketProbe.getsockname()[1]
    socketProbe.close()
    return iPort


@pytest.fixture
def fnLaunchChild(sControlDirectory):
    """Return a launcher of real child hubs; every child is reaped after."""
    listChildren = []

    def fprocessLaunch(*saOptions):
        iPort = _fiFreePort()
        dictEnvironment = dict(os.environ, PYTHONPATH=str(PATH_REPOSITORY))
        processChild = subprocess.Popen(
            [sys.executable, str(PATH_CHILD_SCRIPT), "--port", str(iPort),
             "--session-dir", sessionRegistry._S_SESSION_DIRECTORY,
             "--control-dir", sControlDirectory, *saOptions],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=dictEnvironment, start_new_session=True,
        )
        listChildren.append(processChild)
        sLine = _fsReadReadyLine(processChild)
        assert sLine == "ready", (
            f"child hub did not start: {sLine!r} "
            f"{processChild.stderr.read() if processChild.poll() else ''}")
        processChild.iHubPort = iPort
        return processChild

    yield fprocessLaunch
    for processChild in listChildren:
        if processChild.poll() is None:
            processChild.kill()
        processChild.wait(timeout=10)


def _fsReadReadyLine(processChild):
    listLine = []
    threadRead = threading.Thread(
        target=lambda: listLine.append(processChild.stdout.readline()),
        daemon=True,
    )
    threadRead.start()
    threadRead.join(F_CHILD_READY_SECONDS)
    return listLine[0].strip() if listLine else ""


def _fappBuildParentHub(listChildren):
    with patch.object(pipelineServer, "_fconnectionCreateDocker",
                      lambda: MagicMock()):
        from vaibify.gui import appFactory
        app = appFactory.fappCreateHubApplication()
    app.state.listSpawnedChildren.extend(listChildren)
    return app


async def _fdictDescribeChild(processChild):
    dictResponse = await asyncio.to_thread(
        hostControlChannel.fdictSendHostControlRequest,
        processChild.iHubPort, {"sOperation": "describe-busy"}, 5.0,
    )
    assert dictResponse["bAccepted"] is True
    return dictResponse


@pytest.mark.asyncio
@pytest.mark.falsification
async def testAnIdleParentIsRefusedWhileItsChildHoldsALiveRun(
    fnLaunchChild, fixtureQuickQuit, monkeypatch,
):
    """The child's run refuses the parent's Quit, and both gates reopen.

    Kills: the final check consulting only the parent's own reasons.
    """
    monkeypatch.setattr(hubQuit, "F_QUIT_SETTLE_SECONDS", 0.5)
    processChild = fnLaunchChild("--live-run")
    app = _fappBuildParentHub([processChild])
    with pytest.raises(hubQuit.QuitRefusedError) as errorRefused:
        await hubQuit.fdictRunQuit(app, app.state.dictRouteContext, False)
    assert {"sWindow": f"the vaibify window on port {processChild.iHubPort}",
            "sReason": "a pipeline run in 'childproj'"} in (
        errorRefused.value.dictDetail["listReasons"])
    assert not mutationAdmission.fbAdmissionGateClosed()
    assert (await _fdictDescribeChild(processChild))["bAdmissionsClosed"] is False


@pytest.mark.asyncio
@pytest.mark.falsification
async def testAChildKilledMidQuitFailsItAndEveryOtherGateReopens(
    fnLaunchChild, fixtureQuickQuit, monkeypatch,
):
    """SIGKILL between close and final check: Quit fails, the rest reopen.

    Kills: removing the reopen from the Quit's ``finally``.
    """
    monkeypatch.setattr(hubQuit, "F_QUIT_SETTLE_SECONDS", 20.0)
    processDoomed = fnLaunchChild("--in-flight-seconds", "60")
    processSurvivor = fnLaunchChild()
    app = _fappBuildParentHub([processDoomed, processSurvivor])

    async def fnKillTheDoomedChildOnceClosed():
        while not (await _fdictDescribeChild(processSurvivor))[
                "bAdmissionsClosed"]:
            await asyncio.sleep(0.05)
        os.kill(processDoomed.pid, signal.SIGKILL)

    taskKill = asyncio.ensure_future(fnKillTheDoomedChildOnceClosed())
    with pytest.raises(hubQuit.QuitRefusedError) as errorRefused:
        await hubQuit.fdictRunQuit(app, app.state.dictRouteContext, False)
    await taskKill
    dictDetail = errorRefused.value.dictDetail
    assert {"sWindow": f"the vaibify window on port {processDoomed.iHubPort}",
            "sReason": hubQuit.S_NOT_RESPONDING} in dictDetail["listReasons"]
    assert dictDetail["sLeaseNote"]
    assert not mutationAdmission.fbAdmissionGateClosed()
    assert (await _fdictDescribeChild(processSurvivor))[
        "bAdmissionsClosed"] is False
    mutationAdmission.fnAssertAdmissionGateOpen("New work after the Quit")


@pytest.mark.asyncio
@pytest.mark.falsification
async def testAChildsGateStaysClosedPastItsLeaseUntilItExits(
    fnLaunchChild, fixtureQuickQuit, monkeypatch,
):
    """A settlement longer than the lease keeps the child refusing new work.

    The lease is 2 s; a carrier mutation in the parent settles at 3 s.
    A probe of the child at 2.5 s must find its gate closed, and so must
    one well after the Quit succeeded, until the shutdown hook has
    terminated the child and seen it exit.

    Kills: removing the lease renewal.
    """
    monkeypatch.setattr(hubQuit, "F_CHILD_GATE_LEASE_SECONDS", 2.0)
    monkeypatch.setattr(hubQuit, "F_QUIT_SETTLE_SECONDS", 10.0)
    processChild = fnLaunchChild()
    app = _fappBuildParentHub([processChild])
    fileHandleLock = containerLock.ffileAcquireContainerLock(
        S_PARENT_PROJECT, 8137)
    app.state.dictContainerOwners[S_PARENT_PROJECT] = OwnerRecord(
        sLeaseId="lease-1", fileHandleLock=fileHandleLock,
        sContainerId=S_PARENT_CONTAINER_ID, sBrowserSessionId="sess-1",
    )
    dictLaneTuple = {
        "sLane": "browser", "iOwnerGeneration": 1,
        "sBrowserSessionId": "sess-1", "sLeaseId": "lease-1",
        "sContainerName": S_PARENT_PROJECT,
    }
    taskMutation = asyncio.ensure_future(commitCarrier.fdictRunLockHeldMutation(
        app.state, S_PARENT_PROJECT, S_PARENT_CONTAINER_ID, dictLaneTuple,
        "helper", "slowWrite", lambda supervisor: time.sleep(3.0),
    ))
    await asyncio.sleep(0.05)
    fStarted = time.monotonic()
    taskQuit = asyncio.ensure_future(
        hubQuit.fdictRunQuit(app, app.state.dictRouteContext, False))
    await asyncio.sleep(2.5)
    assert (await _fdictDescribeChild(processChild))["bAdmissionsClosed"], (
        "the child admitted new work at 2.5 s, past its 2 s lease")
    dictResult = await taskQuit
    assert dictResult["bQuitting"] is True
    assert time.monotonic() - fStarted >= 2.8
    await taskMutation
    await asyncio.sleep(2.5)
    assert (await _fdictDescribeChild(processChild))["bAdmissionsClosed"], (
        "the child's gate lapsed after the Quit succeeded and before the "
        "child was terminated")
    [fnTerminateChildren] = [
        fnHook for fnHook in app.state.listLifespanShutdown
        if fnHook.__name__ == "fnTerminateSpawnedChildren"
    ]
    await fnTerminateChildren(app)
    assert processChild.poll() is not None
    assert app.state.taskQuitLeaseRenewal is None
    containerLock.fnReleaseContainerLock(fileHandleLock)
