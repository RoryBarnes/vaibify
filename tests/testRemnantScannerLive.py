"""The scanner's in-container claims, against a real container.

An interactive exec vaibify did not open, running a long job, is listed
as a possibly-orphaned session with its process count; a container
created without ``--init`` is listed as a proven fact with its zombie
count. The image carries Python and the container user, which the
typed process-table read needs, as every vaibify image does.
"""

import secrets
import time
from types import SimpleNamespace

import pytest

from vaibify.config import containerLock
from vaibify.gui import remnantScanner, terminalContainment
from tests.liveContainerLabels import fdictLabels
from tests.testDockerConnectionLive import fnRequireDaemonReachable

pytestmark = pytest.mark.docker_live

S_IMAGE = "python:3.10-slim"


@pytest.fixture
def tLiveContainer():
    fnRequireDaemonReachable()
    import docker
    from vaibify.docker.dockerConnection import DockerConnection
    clientDocker = docker.from_env()
    sName = f"vaibifyTermContain{secrets.token_hex(4)}"
    container = clientDocker.containers.run(
        S_IMAGE, ["sh", "-c", "sleep 300"], name=sName, detach=True,
        labels=fdictLabels(), init=False,
    )
    try:
        iExit, baOutput = container.exec_run(["useradd", "-u", "1000", "-m", "researcher"])
        assert iExit == 0, baOutput
        yield (sName, container.id, DockerConnection(), clientDocker)
    finally:
        try:
            container.remove(force=True)
        except Exception:
            pass


def _flistScan(tLiveContainer, monkeypatch):
    sName, sContainerId, connectionDocker, _ = tLiveContainer
    monkeypatch.setattr(terminalContainment, "fbContainerDrainInProgress", lambda s: False)
    monkeypatch.setattr(containerLock, "fbContainerLockIsHeld", lambda s: False)
    dictTable = remnantScanner.fdictParseProcessTable(
        connectionDocker.fsReadProcessTable(sContainerId))
    return remnantScanner.flistClassifyContainerProcesses(
        SimpleNamespace(dictContainerOwners={}), sName, sContainerId, dictTable,
        connectionDocker.fdictReadContainerHostConfig(sContainerId),
        remnantScanner._flistRunningTtyExecIds(connectionDocker, sContainerId))


def test_an_untracked_tty_exec_is_listed_as_possibly_orphaned(tLiveContainer, monkeypatch):
    sName, sContainerId, connectionDocker, clientDocker = tLiveContainer
    container = clientDocker.containers.get(sContainerId)
    sExecId = clientDocker.api.exec_create(
        sContainerId, ["sh", "-c", "sleep 120 & sleep 120"], tty=True)["Id"]
    clientDocker.api.exec_start(sExecId, tty=True, detach=True)
    fDeadline = time.monotonic() + 15.0
    listSessions = []
    while time.monotonic() < fDeadline and not listSessions:
        listSessions = [d for d in _flistScan(tLiveContainer, monkeypatch)
                        if d["sCategory"] == remnantScanner.S_CATEGORY_UNTRACKED_SESSION]
        time.sleep(0.3)
    assert len(listSessions) == 1, listSessions
    [dictItem] = listSessions
    assert dictItem["sTier"] == remnantScanner.S_TIER_POSSIBLY
    assert dictItem["sAction"] == remnantScanner.S_ACTION_TERMINATE
    assert dictItem["dictIdentity"]["sContainerId"] == sContainerId
    assert "3 process(es)" in dictItem["sEvidence"], dictItem["sEvidence"]
    del container


def test_a_container_without_init_shows_its_zombie_count(tLiveContainer, monkeypatch):
    sName, sContainerId, connectionDocker, clientDocker = tLiveContainer
    # pid 1 is `sh -c sleep 300`, which never waits; a child that exits
    # under it stays a zombie.
    clientDocker.api.exec_start(clientDocker.api.exec_create(
        sContainerId, ["sh", "-c", "sh -c 'exit 0' & exec sleep 60"])["Id"], detach=True)
    time.sleep(1.0)
    listInit = [d for d in _flistScan(tLiveContainer, monkeypatch)
                if d["sCategory"] == remnantScanner.S_CATEGORY_CONTAINER_WITHOUT_INIT]
    assert len(listInit) == 1
    assert listInit[0]["sTier"] == remnantScanner.S_TIER_PROVEN
    assert "init process" in listInit[0]["sEvidence"]
