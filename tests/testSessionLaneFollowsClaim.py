"""A claimed, running ``neverSleep`` container is held awake.

The session-lane keep-alive used to start only when vaibify STARTED a
container. A hub that restarted and then claimed a running
``neverSleep`` container held nothing, and the work lane skips owned
containers, so the machine stayed awake only while some other
container's leaked shells happened to hold a work lane. These tests
drive the real claim path and the real ``keepAliveManager`` registry
(redirected by the autouse fixture in ``conftest``); only the
``caffeinate`` spawn is stubbed.
"""

import asyncio
import os

import pytest

from vaibify.config import keepAliveManager, registryManager
from vaibify.gui import containerOwnership, sessionLifecycle, sleepPrevention

S_PROJECT = "sleepyProject"
S_CONTAINER_ID = "0123456789abcdef0123456789abcdef"


class _StateStub:
    def __init__(self):
        self.dictContainerOwners = {}
        self.dictSessionOwner = containerOwnership.fdictCreateSessionOwnerIndex()
        self.dictLifecycleLocks = sessionLifecycle.fdictCreateLifecycleLockStore()


class _DaemonFake:
    def __init__(self, dictRunningIdByName):
        self._dictRunningIdByName = dictRunningIdByName

    def flistGetRunningContainers(self):
        return [{"sName": sName, "sContainerId": sId}
                for sName, sId in self._dictRunningIdByName.items()]

    def fbPing(self):
        return True


@pytest.fixture(autouse=True)
def listSpawns(monkeypatch):
    """Record each would-be caffeinate spawn without launching one."""
    listRecorded = []

    def fiRecordSpawn():
        listRecorded.append(4242 + len(listRecorded))
        return listRecorded[-1]

    monkeypatch.setattr(keepAliveManager, "_fiSpawnCaffeinate", fiRecordSpawn)
    monkeypatch.setattr(
        keepAliveManager, "_fnKillIfRunning", lambda iPid, sStartedIso: None)
    monkeypatch.setattr(
        keepAliveManager, "fbIsProcessAliveSince", lambda iPid, sStartedIso: True)
    monkeypatch.setattr(
        keepAliveManager, "fbPlatformSupportsKeepAlive", lambda: True)
    monkeypatch.setattr(sleepPrevention, "fbDockerReachable", lambda c: True)
    return listRecorded


@pytest.fixture
def fnRegisterProjectConfig(monkeypatch, tmp_path):
    """Register S_PROJECT with a real vaibify.yml carrying the given flag."""

    def fnRegister(bNeverSleep):
        sConfigPath = os.path.join(tmp_path, "vaibify.yml")
        with open(sConfigPath, "w") as fileHandle:
            fileHandle.write(
                f"projectName: {S_PROJECT}\n"
                f"neverSleep: {'true' if bNeverSleep else 'false'}\n")
        monkeypatch.setattr(
            registryManager, "fdictGetProject",
            lambda sName: {"sName": sName, "sConfigPath": sConfigPath}
            if sName == S_PROJECT else None)
    return fnRegister


def _ftClaim(stateStub, sLeaseId="", sContainerId=S_CONTAINER_ID):
    return asyncio.run(sessionLifecycle.ftClaimWithCardinality(
        stateStub, S_PROJECT, sLeaseId, 8050,
        sContainerId=sContainerId, sBrowserSessionId="session-a"))


@pytest.mark.falsification
def test_a_claim_of_a_running_never_sleep_container_leaves_a_live_keep_alive(
    fnRegisterProjectConfig, listSpawns,
):
    """Kills: the session-lane call on a granted claim in
    ``sessionLifecycle.ftClaimWithCardinality`` removed, so a hub that
    restarted holds nothing for a running neverSleep container.

    Oracle: the ``neverSleep`` contract in the configuration reference
    ("keep the Mac awake while the container runs"), read against the
    keep-alive registry on disk, never against the claim's own payload.
    """
    fnRegisterProjectConfig(bNeverSleep=True)
    assert not keepAliveManager.fbKeepAliveIsLive(S_PROJECT)
    iStatus, _ = _ftClaim(_StateStub())
    assert iStatus == 200
    assert keepAliveManager.fbKeepAliveIsLive(S_PROJECT), (
        "the claimed neverSleep container has no session-lane keep-alive")
    assert listSpawns == [4242]


def test_a_repeated_claim_does_not_respawn(fnRegisterProjectConfig, listSpawns):
    fnRegisterProjectConfig(bNeverSleep=True)
    stateStub = _StateStub()
    _, dictPayload = _ftClaim(stateStub)
    iStatus, _ = _ftClaim(stateStub, sLeaseId=dictPayload["sLeaseId"])
    assert iStatus == 200
    assert listSpawns == [4242], "a tab reload must not churn the process"


def test_a_host_project_or_an_unset_flag_starts_nothing(
    fnRegisterProjectConfig, listSpawns,
):
    fnRegisterProjectConfig(bNeverSleep=False)
    iStatus, _ = _ftClaim(_StateStub())
    assert iStatus == 200
    assert listSpawns == []
    fnRegisterProjectConfig(bNeverSleep=True)
    sleepPrevention.fnEnsureSessionLaneForClaim(S_PROJECT, S_PROJECT)
    sleepPrevention.fnEnsureSessionLaneForClaim(S_PROJECT, "")
    assert listSpawns == [], "a host project has no container to hold awake"


def test_an_unloadable_config_declines_the_lane_without_failing_the_claim(
    monkeypatch, tmp_path, listSpawns,
):
    sConfigPath = os.path.join(tmp_path, "vaibify.yml")
    with open(sConfigPath, "w") as fileHandle:
        fileHandle.write("neverSleep: [not: valid\n")
    monkeypatch.setattr(
        registryManager, "fdictGetProject",
        lambda sName: {"sName": sName, "sConfigPath": sConfigPath})
    iStatus, _ = _ftClaim(_StateStub())
    assert iStatus == 200
    assert listSpawns == []


def test_saving_the_setting_starts_and_stops_the_lane_of_a_held_running_container(
    listSpawns,
):
    stateStub = _StateStub()
    dictCtx = {"docker": _DaemonFake({S_PROJECT: S_CONTAINER_ID})}
    sleepPrevention.fnApplySessionLaneSetting(stateStub, dictCtx, S_PROJECT, True)
    assert listSpawns == [], "a container this hub does not hold is left alone"
    stateStub.dictContainerOwners[S_PROJECT] = object()
    sleepPrevention.fnApplySessionLaneSetting(stateStub, dictCtx, S_PROJECT, True)
    assert keepAliveManager.fbKeepAliveIsLive(S_PROJECT)
    sleepPrevention.fnApplySessionLaneSetting(stateStub, dictCtx, S_PROJECT, True)
    assert listSpawns == [4242], "re-saving true does not respawn"
    sleepPrevention.fnApplySessionLaneSetting(stateStub, dictCtx, S_PROJECT, False)
    assert not keepAliveManager.fbKeepAliveIsLive(S_PROJECT)
    dictCtx = {"docker": _DaemonFake({})}
    sleepPrevention.fnApplySessionLaneSetting(stateStub, dictCtx, S_PROJECT, True)
    assert listSpawns == [4242], "a stopped container gets no lane"


def test_the_reaper_stops_lanes_of_stopped_containers_only(listSpawns):
    keepAliveManager.fnStartKeepAlive("stoppedOne")
    keepAliveManager.fnStartKeepAlive("runningOne")
    keepAliveManager.fnStartKeepAlive(
        sleepPrevention.fsWorkLaneKeepAliveName("stoppedOne"))
    dictCtx = {"docker": _DaemonFake({"runningOne": "id-running"})}
    dictOutcome = sleepPrevention.fdictReapSessionLanesOfStoppedContainers(dictCtx)
    assert dictOutcome["sOutcome"] == "ran"
    assert dictOutcome["iRemoved"] == 1
    assert not keepAliveManager.fbKeepAliveIsLive("stoppedOne")
    assert keepAliveManager.fbKeepAliveIsLive("runningOne"), (
        "a running container's lane is left for the next claim to adopt")
    assert keepAliveManager.fbKeepAliveIsLive(
        sleepPrevention.fsWorkLaneKeepAliveName("stoppedOne")), (
        "the work lane is the watchdog's, never this reaper's")


def test_the_reaper_declines_when_the_daemon_cannot_answer(monkeypatch, listSpawns):
    keepAliveManager.fnStartKeepAlive("stoppedOne")
    monkeypatch.setattr(sleepPrevention, "fbDockerReachable", lambda c: False)
    dictOutcome = sleepPrevention.fdictReapSessionLanesOfStoppedContainers(
        {"docker": None})
    assert dictOutcome["sOutcome"] == "forbidden"
    assert dictOutcome["sRemedy"]
    assert keepAliveManager.fbKeepAliveIsLive("stoppedOne")
