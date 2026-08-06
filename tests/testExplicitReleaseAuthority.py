"""The explicit-release authority (design §10, cases 11 and 17).

Drives ``sessionLifecycle.ftReleaseExplicit`` against real owner
records holding a REAL host flock in an isolated lock directory, so
"the flock was still held" and "the flock was freed" are observed by
trying to acquire it rather than by inspecting a stand-in. The
container NAME stays distinct from the Docker ID throughout.
"""

import asyncio
import time
from types import SimpleNamespace

import pytest

from vaibify.config import containerLock, operationJournal
from vaibify.gui import (
    browserSession,
    containerOwnership,
    sessionLifecycle,
)

S_PROJECT_NAME = "SampleProject"
S_CONTAINER_ID = "cid-aabbccddeeff"
S_LEASE_ID = "lease-explicit-release"
S_SESSION_ID = "session-explicit-release"
I_OWNER_PORT = 8137
F_GRACE_SECONDS = 30.0


@pytest.fixture(autouse=True)
def fixtureIsolateJournalAndLockDirectories(tmp_path, monkeypatch):
    """Keep the journal and flock directories out of ~/.vaibify."""
    monkeypatch.setattr(
        operationJournal, "_S_JOURNAL_DIRECTORY", str(tmp_path / "journal"),
    )
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"),
    )


def _fbFlockIsStillHeld(sName):
    """Return True when the container's host flock cannot be acquired."""
    try:
        fileHandle = containerLock.ffileAcquireContainerLock(
            sName, I_OWNER_PORT + 1,
        )
    except containerLock.ContainerLockedError:
        return True
    containerLock.fnReleaseContainerLock(fileHandle)
    return False


def _fstateBuildOwnedState():
    """Return app.state with one owner record holding a REAL flock."""
    stateApp = SimpleNamespace(
        bReapOwnerships=True,
        dictContainerOwners={},
        dictSessionOwner=containerOwnership.fdictCreateSessionOwnerIndex(),
        dictSessionSockets=(
            containerOwnership.fdictCreateSessionSocketIndex()
        ),
        dictBrowserSessions=browserSession.fdictCreateBrowserSessionStore(),
        dictMutationSupervisors={},
        dictDurableTaskRecords={},
        dictTerminalExecutionRecords={},
    )
    stateApp.dictContainerOwners[S_PROJECT_NAME] = (
        containerOwnership.OwnerRecord(
            sLeaseId=S_LEASE_ID,
            fileHandleLock=containerLock.ffileAcquireContainerLock(
                S_PROJECT_NAME, I_OWNER_PORT,
            ),
            sAgentToken=containerOwnership.fsMintAgentToken(),
            sContainerId=S_CONTAINER_ID,
            sBrowserSessionId=S_SESSION_ID,
        )
    )
    stateApp.dictSessionOwner[S_SESSION_ID] = S_PROJECT_NAME
    return stateApp


def _recordRegisterLiveDurableTask(stateApp):
    """Register a live mode-(c) durable task on the container."""
    recordTask = SimpleNamespace(
        sTaskId="task-live-run", sState="running",
        iOwnerGeneration=1,
        taskAsync=SimpleNamespace(done=lambda: False),
    )
    stateApp.dictDurableTaskRecords[S_PROJECT_NAME] = recordTask
    return recordTask


async def _tRelease(stateApp, bForce=False):
    """Run the explicit release as the true owner."""
    return await sessionLifecycle.ftReleaseExplicit(
        stateApp, S_PROJECT_NAME, S_LEASE_ID,
        sBrowserSessionId=S_SESSION_ID, bForce=bForce,
    )


# -- case 11 ----------------------------------------------------------------


@pytest.mark.falsification
def testIdleReleaseWithAStaleAgentStampSucceeds():
    """An idle container releases, stale agent stamp and all.

    Case 11 (design §10): the agent-liveness refusal is about a LIVE
    agent. A record whose agent last acted longer ago than the grace,
    with nothing in flight and no durable task, is idle — the release
    must commit, free the flock, and drop the cardinality entry. A
    refusal here would be the lockout the whole §10 arbitration exists
    to avoid: a researcher unable to give back a container nothing is
    using.

    Kills: refusing an explicit release whenever the record has ever
    seen an agent, rather than only while one is LIVE, in
    ``sessionLifecycle._fsReleaseBusyReason``.
    """
    stateApp = _fstateBuildOwnedState()
    recordOwner = stateApp.dictContainerOwners[S_PROJECT_NAME]
    recordOwner.fLastAgentActivityMonotonic = time.monotonic() - (
        F_GRACE_SECONDS * 10.0
    )
    assert recordOwner.iInFlightAgentRequests == 0
    assert _fbFlockIsStillHeld(S_PROJECT_NAME) is True
    sOutcome, _ = asyncio.run(_tRelease(stateApp))
    assert sOutcome == sessionLifecycle.S_RELEASE_RELEASED, (
        "an idle container with a stale agent stamp must release"
    )
    assert S_PROJECT_NAME not in stateApp.dictContainerOwners
    assert stateApp.dictSessionOwner == {}
    assert _fbFlockIsStillHeld(S_PROJECT_NAME) is False, (
        "a committed release must free the host flock"
    )


def testForeignLeaseReleaseIsRefusedAndRetainsEverything():
    """A release presenting somebody else's lease commits nothing."""
    stateApp = _fstateBuildOwnedState()
    sOutcome, dictPayload = asyncio.run(
        sessionLifecycle.ftReleaseExplicit(
            stateApp, S_PROJECT_NAME, "lease-belonging-to-nobody",
            sBrowserSessionId="another-session",
        ),
    )
    assert sOutcome == sessionLifecycle.S_RELEASE_NOT_OWNER
    assert "not held by this browser session" in dictPayload["sMessage"]
    assert S_PROJECT_NAME in stateApp.dictContainerOwners
    assert _fbFlockIsStillHeld(S_PROJECT_NAME) is True


# -- case 17 ----------------------------------------------------------------


@pytest.mark.falsification
def testReleaseUnderALiveAgentNeedsForceAndForceNeverBeatsALiveRun():
    """Force overrides the agent refusal — and ONLY that one.

    Case 17 (design §10): a live in-container agent makes a release a
    retained refusal, which the route answers 409; the same call with
    force commits. But force must never override a live durable task:
    a run can still commit to the container, so freeing the flock over
    it would hand the container to a second owner while the first
    owner's work is still writing. Impatience is not a proof of
    safety.

    Kills: letting ``bForce`` short-circuit the whole busy
    arbitration — including the live-durable-task refusal — in
    ``sessionLifecycle._fsReleaseBusyReason``.
    """
    stateApp = _fstateBuildOwnedState()
    recordOwner = stateApp.dictContainerOwners[S_PROJECT_NAME]
    recordTask = _recordRegisterLiveDurableTask(stateApp)
    recordOwner.fLastAgentActivityMonotonic = time.monotonic()
    sOutcomeForced, dictForced = asyncio.run(_tRelease(stateApp, True))
    assert sOutcomeForced == sessionLifecycle.S_RELEASE_BUSY, (
        "force must not release a container whose run is still live"
    )
    assert "run still in progress" in dictForced["sMessage"]
    assert S_PROJECT_NAME in stateApp.dictContainerOwners
    assert _fbFlockIsStillHeld(S_PROJECT_NAME) is True
    assert recordTask.taskAsync.done() is False
    # The run finishes; now only the live agent stands in the way.
    stateApp.dictDurableTaskRecords.pop(S_PROJECT_NAME)
    sOutcomeAgent, dictAgent = asyncio.run(_tRelease(stateApp))
    assert sOutcomeAgent == sessionLifecycle.S_RELEASE_BUSY
    assert "in-container agent" in dictAgent["sMessage"]
    assert S_PROJECT_NAME in stateApp.dictContainerOwners
    sOutcomeOverride, _ = asyncio.run(_tRelease(stateApp, True))
    assert sOutcomeOverride == sessionLifecycle.S_RELEASE_RELEASED, (
        "force exists precisely to override the agent refusal"
    )
    assert _fbFlockIsStillHeld(S_PROJECT_NAME) is False


class _ObservingWebSocketConnection:
    """A socket that records what was still true when it was closed."""

    def __init__(self, stateApp):
        self.stateApp = stateApp
        self.listCloseCodes = []
        self.listRecordHeldAtClose = []
        self.listFlockHeldAtClose = []

    async def close(self, code=1000):
        self.listCloseCodes.append(code)
        self.listRecordHeldAtClose.append(
            S_PROJECT_NAME in self.stateApp.dictContainerOwners,
        )
        self.listFlockHeldAtClose.append(
            _fbFlockIsStillHeld(S_PROJECT_NAME),
        )


@pytest.mark.falsification
def testPermittedReleaseClosesChannelsBeforeFreeingTheFlock():
    """The channels go down while the container is still ours.

    Case 17, ordering half (design §10): a permitted release closes
    every container-bound channel BEFORE freeing the flock. Reversed,
    the hub would hold a live socket pointed at a container it no
    longer owns — reachable by whichever session claims it next. The
    socket itself reports what was true at the moment it was closed,
    so the ordering is observed rather than assumed.

    Kills: freeing the flock before closing the channels (moving the
    drain-and-close after the release commit) in
    ``sessionLifecycle.ftReleaseExplicit``.
    """
    stateApp = _fstateBuildOwnedState()
    connectionObserving = _ObservingWebSocketConnection(stateApp)
    containerOwnership.fnIncrementLiveConnection(
        stateApp.dictContainerOwners, S_PROJECT_NAME, bPipelineLane=True,
    )
    containerOwnership.fnRegisterSessionSocket(
        stateApp.dictSessionSockets,
        containerOwnership.ConnectionRecord(
            connection=connectionObserving,
            sBrowserSessionId=S_SESSION_ID,
            iOwnerGeneration=1,
            sLane=containerOwnership.S_LANE_PIPELINE,
        ),
    )
    sOutcome, _ = asyncio.run(_tRelease(stateApp))
    assert sOutcome == sessionLifecycle.S_RELEASE_RELEASED
    assert connectionObserving.listCloseCodes == [4401], (
        "a permitted release must close the session's channels"
    )
    assert connectionObserving.listRecordHeldAtClose == [True], (
        "the channel was closed after the owner record was dropped"
    )
    assert connectionObserving.listFlockHeldAtClose == [True], (
        "the channel was closed after the host flock was freed, so a "
        "live socket briefly pointed at a container this hub no "
        "longer owned"
    )
    assert _fbFlockIsStillHeld(S_PROJECT_NAME) is False
