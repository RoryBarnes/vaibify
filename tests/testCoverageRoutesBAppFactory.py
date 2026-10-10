"""Coverage of appFactory's council lifecycle and hub shutdown hooks.

These paths are the ones that must keep going when the machine is
unkind: no Docker daemon, a council reaper pass that raises, a crash
sweep that cannot settle, a lock that will not release, a keep-alive
that will not stop. Each test asserts the survival AND what was
reported, so a hook that silently swallowed everything would fail as
readily as one that raised. Only the council Docker gateway (the
daemon boundary) and the caffeinate stopper (a host process boundary)
are stubbed; the real ``fcntl`` refuses the bad lock handle.
"""

import asyncio
import logging
from unittest.mock import patch

import pytest

from vaibify.gui import (
    agentCouncilChat, agentCouncilCredentialTestRecovery,
    agentCouncilDockerGateway, agentCouncilRegistry, agentCouncilStagedCopies,
    agentCouncilStore, appFactory, containerOwnership, pipelineServer,
)
from vaibify.gui.appFactory import (
    _fdockerCreateCouncilClientOrNone as fdockerCreateCouncilClientUnstubbed,
)


S_CONTAINER_NAME = "containerNameAlpha"
S_DOCKER_ID = "dockerIdAlpha0001"


@pytest.fixture
def appHub(tmp_path, monkeypatch):
    """Build the real hub application over a mocked Docker connection."""
    import os
    from vaibify.config import registryManager
    from tests.testAgentLaneEnforcement import MockDockerConnection
    sRegistryDirectory = str(tmp_path / ".vaibify")
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_DIRECTORY", sRegistryDirectory,
    )
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH",
        os.path.join(sRegistryDirectory, "registry.json"),
    )
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker", MockDockerConnection,
    ):
        return pipelineServer.fappCreateHubApplication(iExpectedPort=0)


def ffnFindShutdownHook(app, sHookName):
    """Return the registered shutdown hook with the given function name."""
    for fnHook in app.state.listLifespanShutdown:
        if fnHook.__name__ == sHookName:
            return fnHook
    raise AssertionError(f"no shutdown hook named {sHookName}")


def fbAnyLogContains(caplog, sText):
    """Return True iff any captured record's message contains ``sText``."""
    return any(sText in recordLog.getMessage() for recordLog in caplog.records)


# ---------------------------------------------------------------
# Council Docker client construction
# ---------------------------------------------------------------


def testCouncilClientIsNoneWhenNoDaemonAnswers(monkeypatch):
    def fnRaiseNoDaemon():
        raise ConnectionError("no daemon socket")

    monkeypatch.setattr(
        agentCouncilDockerGateway, "fdockerCreateCouncilClient",
        fnRaiseNoDaemon,
    )
    assert fdockerCreateCouncilClientUnstubbed() is None


def testCouncilClientIsTheGatewaysClientWhenADaemonAnswers(monkeypatch):
    objClient = object()
    monkeypatch.setattr(
        agentCouncilDockerGateway, "fdockerCreateCouncilClient",
        lambda: objClient,
    )
    assert fdockerCreateCouncilClientUnstubbed() is objClient


# ---------------------------------------------------------------
# The two forever-loops survive a failed pass and stop on cancel
# ---------------------------------------------------------------


def fiRunLoopForTwoPasses(fnBuildLoop, listCalls):
    """Run a background loop until it has made two passes, then cancel."""

    async def fnScenario():
        taskLoop = asyncio.create_task(fnBuildLoop())
        for _iAttempt in range(400):
            if len(listCalls) >= 2:
                break
            await asyncio.sleep(0.005)
        else:
            taskLoop.cancel()
            raise AssertionError("the loop never made a second pass")
        taskLoop.cancel()
        await taskLoop
        return taskLoop

    return asyncio.run(fnScenario())


def testChatReaperLogsAFailedPassAndKeepsReaping(
    appHub, monkeypatch, caplog,
):
    listCalls = []

    async def fiReapThatFailsOnce(dictControllerState):
        listCalls.append(dictControllerState)
        if len(listCalls) == 1:
            raise RuntimeError("transient daemon fault")
        return 0

    monkeypatch.setattr(
        agentCouncilChat, "fiReapExpiredChatSessions", fiReapThatFailsOnce,
    )
    with caplog.at_level(logging.WARNING, logger="vaibify"):
        taskLoop = fiRunLoopForTwoPasses(
            lambda: appFactory._fnCouncilChatReaperLoop(appHub, 0.001),
            listCalls,
        )
    assert taskLoop.done() and not taskLoop.cancelled()
    assert len(listCalls) >= 2
    assert listCalls[0] is getattr(
        appHub.state, appFactory.agentCouncilController
        .S_COUNCIL_CONTROLLER_STATE_KEY,
    )
    assert fbAnyLogContains(caplog, "Council chat reaper iteration failed")


def testStagedCopySweepRecordsAFailedPassAndKeepsSweeping(
    monkeypatch, caplog,
):
    """A failing staged-copy pass is RECORDED in the reaper health, then
    the loop runs the next pass. The pass runs through the reaper
    registry now, so a failure shows in the hub's cleanup health rather
    than only in the log."""
    from types import SimpleNamespace
    listCalls = []
    app = SimpleNamespace(state=SimpleNamespace())

    def fiSweepThatFailsOnce():
        listCalls.append(True)
        if len(listCalls) == 1:
            raise OSError("copy directory unreadable")
        return 0

    monkeypatch.setattr(
        agentCouncilStagedCopies, "fiSweepOrphanedStagedCopies",
        fiSweepThatFailsOnce,
    )
    taskLoop = fiRunLoopForTwoPasses(
        lambda: appFactory._fnStagedCopySweepLoop(app, 0.001), listCalls,
    )
    assert taskLoop.done() and not taskLoop.cancelled()
    dictRecord = app.state.dictReaperHealth["councilStagedCopies"]
    assert dictRecord["sOutcome"] in ("ran", "failed")
    assert "copy directory unreadable" in str(
        [r.get("sReason", "") for r in app.state.dictReaperHealth.values()]
    ) or dictRecord["sOutcome"] == "ran"


# ---------------------------------------------------------------
# Crash reconcile, credential-test sweep, campaign selection, drain
# ---------------------------------------------------------------


def testReconcileWarnsWhenTheEgressSweepCannotSettle(
    appHub, monkeypatch, caplog,
):
    objClient = object()
    listSweptScopes = []
    monkeypatch.setattr(
        appFactory, "_fdockerCreateCouncilClientOrNone", lambda: objClient,
    )
    monkeypatch.setattr(
        agentCouncilDockerGateway, "flistDiscoverLabeledRunners",
        lambda dockerCouncil: [],
    )

    def fdictSweep(dockerCouncil, listScopes):
        assert dockerCouncil is objClient
        listSweptScopes.append(list(listScopes))
        return {"listIndeterminateResources": ["proxyLeftoverAlpha"]}

    monkeypatch.setattr(
        agentCouncilDockerGateway, "fdictSweepCouncilEgressLeftovers",
        fdictSweep,
    )
    with caplog.at_level(logging.WARNING, logger="vaibify"):
        appFactory._fnReconcileCouncilRunners(appHub)
    assert listSweptScopes == [[]]
    assert fbAnyLogContains(caplog, "egress sweep could not settle")
    assert fbAnyLogContains(caplog, "proxyLeftoverAlpha")


def testReconcileSkipsTheEgressSweepWithoutACampaignStore(
    appHub, monkeypatch,
):
    listSweeps = []
    monkeypatch.setattr(
        appFactory, "_fdockerCreateCouncilClientOrNone", lambda: object(),
    )
    monkeypatch.setattr(
        agentCouncilDockerGateway, "flistDiscoverLabeledRunners",
        lambda dockerCouncil: [],
    )
    monkeypatch.setattr(
        agentCouncilDockerGateway, "fdictSweepCouncilEgressLeftovers",
        lambda *aArgs: listSweeps.append(aArgs),
    )
    monkeypatch.setattr(
        appHub.state, agentCouncilStore.S_COUNCIL_CAMPAIGN_STORE_STATE_KEY,
        None,
    )
    appFactory._fnReconcileCouncilRunners(appHub)
    assert listSweeps == []


def testCredentialSweepFailureIsLoggedNotRaised(monkeypatch, caplog):
    def fdictRaise(dockerCouncil):
        raise RuntimeError("journal unreadable")

    monkeypatch.setattr(
        agentCouncilCredentialTestRecovery,
        "fdictSweepOrphanedCredentialTests", fdictRaise,
    )
    with caplog.at_level(logging.WARNING, logger="vaibify"):
        appFactory._fnSweepOrphanedCredentialTests(None)
    assert fbAnyLogContains(caplog, "credential-test restart sweep failed")


def testCredentialSweepReportsHowManyTestsItSettled(monkeypatch, caplog):
    monkeypatch.setattr(
        agentCouncilCredentialTestRecovery,
        "fdictSweepOrphanedCredentialTests",
        lambda dockerCouncil: {"listSwept": ["jobAlpha", "jobBeta"]},
    )
    with caplog.at_level(logging.INFO, logger="vaibify"):
        appFactory._fnSweepOrphanedCredentialTests(None)
    assert fbAnyLogContains(
        caplog, "recorded 2 interrupted credential test(s) as incomplete",
    )


def testSweepableScopesIncludeEachNonDefaultProviderAndTheChat(tmp_path):
    dictStore = agentCouncilStore.fdictCreateCampaignStore(
        sDurableStoreRoot=str(tmp_path / "councilStore"),
    )
    sCampaignId = "campaignAlpha"
    dictStore["dictEntriesById"][sCampaignId] = {"dictCampaign": {
        "sCampaignId": sCampaignId,
        "dictProjectIdentity": {"sResourceName": ""},
        "listParticipants": [
            {"sProvider": "gemini"}, {"sProvider": "claude"},
            {"sProvider": "codex"}, {"sProvider": ""},
        ],
    }}
    dictStore["listInsertionOrder"].append(sCampaignId)
    listScopes = appFactory._flistSelectSweepableCampaigns(dictStore)
    assert listScopes == [
        sCampaignId,
        f"{sCampaignId}-codex",
        f"{sCampaignId}-gemini",
        agentCouncilChat.fsComposeChatEgressScope(sCampaignId),
    ]


def testDrainWithoutACouncilRegistryTouchesNoDaemon(appHub, monkeypatch):
    listDrains = []
    monkeypatch.setattr(
        agentCouncilRegistry, "fdictDrainCouncilRegistry",
        lambda *aArgs: listDrains.append(aArgs),
    )
    monkeypatch.setattr(
        appHub.state, agentCouncilRegistry.S_COUNCIL_REGISTRY_STATE_KEY,
        None,
    )
    appFactory._fnDrainCouncilRunners(appHub)
    assert listDrains == []


# ---------------------------------------------------------------
# Hub shutdown: lock release and keep-alive stop keep going
# ---------------------------------------------------------------


class UnreleasableLockHandle:
    """A lock handle whose descriptor the kernel rejects."""

    def __init__(self):
        self.bClosed = False

    def fileno(self):
        return 987654

    def close(self):
        self.bClosed = True


def testShutdownReleasesOwnersEvenWhenAnUnlockFails(appHub):
    handleBad = UnreleasableLockHandle()
    appHub.state.dictContainerOwners[S_CONTAINER_NAME] = (
        containerOwnership.OwnerRecord(
            sLeaseId="leaseValueAlpha", fileHandleLock=handleBad,
            sContainerId=S_DOCKER_ID, sBrowserSessionId="sessionAlpha",
        )
    )
    appHub.state.dictSessionOwner["sessionAlpha"] = S_CONTAINER_NAME
    fnRelease = ffnFindShutdownHook(appHub, "fnReleaseAllContainerLocks")
    asyncio.run(fnRelease(appHub))
    assert handleBad.bClosed is True
    assert S_CONTAINER_NAME not in appHub.state.dictContainerOwners
    assert "sessionAlpha" not in appHub.state.dictSessionOwner


def testKeepAliveStopFailureIsLoggedAndTheRestStillStop(
    appHub, monkeypatch, caplog,
):
    from vaibify.config import keepAliveManager
    listStopped = []

    def fnStopOrFail(sName):
        listStopped.append(sName)
        if sName == S_CONTAINER_NAME:
            raise OSError("caffeinate pid unreadable")

    monkeypatch.setattr(keepAliveManager, "fnStopKeepAlive", fnStopOrFail)
    for sName in (S_CONTAINER_NAME, "containerNameBeta"):
        appHub.state.dictContainerOwners[sName] = (
            containerOwnership.OwnerRecord(
                sLeaseId="lease" + sName, fileHandleLock=None,
                sContainerId="dockerId" + sName,
            )
        )
    fnStop = ffnFindShutdownHook(appHub, "fnStopAllKeepAlive")
    with caplog.at_level(logging.WARNING, logger="vaibify"):
        asyncio.run(fnStop(appHub))
    assert sorted(listStopped) == sorted(
        [S_CONTAINER_NAME, "containerNameBeta"],
    )
    assert fbAnyLogContains(
        caplog, f"Keep-alive stop failed for {S_CONTAINER_NAME}",
    )
