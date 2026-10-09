"""The memory watch against a real Docker daemon and real kernel counters.

A unit stub can agree with the parser about what ``memory.events`` says;
only a real cgroup can say whether the kernel writes what the parser
expects, whether the container user can read it, and whether a balloon
under a cap really moves ``oom_kill`` and ``oom`` together. These tests
create THROWAWAY containers from a public image, uniquely named and
force-removed in teardown; no existing container is listed, read or
touched. Each one gains the unprivileged ``researcher`` user a project
image carries, because the gateway runs every typed read as that user
when an image pins none.

The route is driven over HTTP through an owner record whose NAME differs
from the container's ID, using the in-container agent's per-container
token -- the lane an agent would use, and the one whose authorization
resolves the id through the owner record.

Live-daemon convention (``testDockerConnectionLive``): each test skips
when no daemon answers, and ``VAIBIFY_REQUIRE_DOCKER_DAEMON`` turns the
skip into a failure.
"""

import asyncio
import os
import secrets
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from tests.liveContainerLabels import fdictLabels
from tests.testDockerConnectionLive import fnRequireDaemonReachable
from vaibify.docker import cgroupMemory
from vaibify.gui import (
    actionCatalog, containerMemorySampler, containerOwnership,
    pipelineServer,
)

pytestmark = pytest.mark.docker_live

S_TEST_IMAGE = os.environ.get(
    "VAIBIFY_COUNCIL_TEST_IMAGE", "python:3.10-slim")
I_MEBIBYTE = 1024 * 1024
S_AGENT_TOKEN = "memory-live-agent-token"
S_BALLOON_SCRIPT = (
    "listBalloon = []\n"
    "for iRound in range(64):\n"
    "    listBalloon.append(bytearray(16 * 1024 * 1024))\n"
    "print('BALLOON_COMPLETED')\n"
)


@pytest.fixture
def fnCreateThrowaway():
    """Yield a factory for uniquely named throwaway containers."""
    fnRequireDaemonReachable()
    import docker
    clientDocker = docker.from_env()
    listCreated = []

    def fcontainerCreate(**dictLimits):
        container = clientDocker.containers.run(
            S_TEST_IMAGE, ["sleep", "600"], detach=True,
            name="vaibify-memory-live-" + secrets.token_hex(6),
            labels=fdictLabels({"vaibify.test": "memory-watch-live"}),
            **dictLimits)
        listCreated.append(container)
        iExitCode, baOutput = container.exec_run(
            ["useradd", "-m", "researcher"])
        assert iExitCode == 0, baOutput
        return container

    try:
        yield fcontainerCreate
    finally:
        for container in listCreated:
            try:
                container.remove(force=True)
            except Exception:  # noqa: BLE001 -- teardown is best-effort
                pass


def _fappWithOwner(sName, sContainerId):
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker",
        return_value=MagicMock(),
    ):
        app = pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace", sTerminalUserArg="testuser")
    app.state.dictContainerOwners[sName] = containerOwnership.OwnerRecord(
        sLeaseId="memory-live-lease", fileHandleLock=None,
        sAgentToken=S_AGENT_TOKEN, sContainerId=sContainerId)
    return app


def _fnSampleOnce(app):
    from vaibify.docker.dockerConnection import DockerConnection
    asyncio.run(containerMemorySampler.fnSampleOwnedContainers(
        app, {"docker": DockerConnection()}, {}))


def _fdictGetMemory(app, sContainerId):
    client = TestClient(app, headers={
        actionCatalog.S_SESSION_HEADER_NAME: S_AGENT_TOKEN,
        "Host": "host.docker.internal:8050",
    })
    responseMemory = client.get(f"/api/monitor/{sContainerId}/memory")
    assert responseMemory.status_code == 200, responseMemory.text
    return responseMemory.json()


def testABalloonUnderACapIsReportedAsAKillWithALimitEvent(fnCreateThrowaway):
    container = fnCreateThrowaway(mem_limit="256m")
    sName = "memory-live-project"
    assert sName != container.id
    app = _fappWithOwner(sName, container.id)
    _fnSampleOnce(app)
    dictBefore = _fdictGetMemory(app, container.id)
    assert dictBefore["dictCurrent"]["sState"] == "measured", dictBefore
    assert dictBefore["dictCurrent"]["iLimitBytes"] == 256 * I_MEBIBYTE
    assert dictBefore["listIncidents"] == []
    iExitCode, baOutput = container.exec_run(
        ["python3", "-c", S_BALLOON_SCRIPT])
    assert b"BALLOON_COMPLETED" not in baOutput
    assert iExitCode != 0
    _fnSampleOnce(app)
    dictAfter = _fdictGetMemory(app, container.id)
    listKills = [
        d for d in dictAfter["listIncidents"] if d["sKind"] == "oomKill"]
    assert len(listKills) == 1, dictAfter
    assert listKills[0]["iKillCount"] >= 1
    assert listKills[0]["bLimitEventInWindow"] is True
    assert listKills[0]["sContainerId"] == container.id
    assert "256 MB memory limit" in listKills[0]["sSentence"]


def testAContainerWithNoLimitReportsUnlimited(fnCreateThrowaway):
    container = fnCreateThrowaway()
    app = _fappWithOwner("memory-live-unlimited", container.id)
    _fnSampleOnce(app)
    dictCurrent = _fdictGetMemory(app, container.id)["dictCurrent"]
    assert dictCurrent["sState"] == "measured", dictCurrent
    assert dictCurrent["sLimitKind"] == cgroupMemory.S_LIMIT_UNLIMITED
    assert dictCurrent["sLevel"] == "ok"
    assert dictCurrent["iWorkingSetBytes"] is not None
    assert dictCurrent["sSentence"].startswith("No memory limit.")


def testAStoppedContainerReadsNotRunning(fnCreateThrowaway):
    container = fnCreateThrowaway(mem_limit="256m")
    container.stop(timeout=1)
    app = _fappWithOwner("memory-live-stopped", container.id)
    _fnSampleOnce(app)
    dictCurrent = _fdictGetMemory(app, container.id)["dictCurrent"]
    assert dictCurrent["sState"] == "notRunning"
    assert dictCurrent["sLevel"] == "unknown"
