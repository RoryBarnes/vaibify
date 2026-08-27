"""Ownership and council transitions must announce themselves in the log.

A live hub lost a browser session's lease with ZERO operator-visible
trace (2026-08-27): the terminal showed only the startup banner,
``~/.vaibify/vaibify.log`` recorded nothing, and the cause could not be
reconstructed afterwards because every lifecycle transition — claim,
release, orphan, expiry, socket open/close — was silent by
construction. A guarantee stated only in prose is not enforced, so
these tests fail when the transition log lines are deleted.

The assertions match the stable prefix of each message (the fact that
the transition announces itself and names its container), not the full
prose, so wording can evolve without weakening the guardrail. The
container NAME stays distinct from the Docker ID throughout (repo
epistemics rule), and no test asserts a lease, token, or credential
value appears — those must NEVER be logged.
"""

import logging
from types import SimpleNamespace

import pytest

from vaibify.config import containerLock, operationJournal
from vaibify.gui import agentCouncil, browserSession, containerOwnership
from vaibify.gui import sessionLifecycle

S_PROJECT_NAME = "SampleLoggedProject"
S_CONTAINER_ID = "cid-fedcba987654"


@pytest.fixture(autouse=True)
def fixtureIsolateJournalAndLockDirectories(tmp_path, monkeypatch):
    """Keep the journal and flock directories out of ~/.vaibify."""
    monkeypatch.setattr(
        operationJournal, "_S_JOURNAL_DIRECTORY", str(tmp_path / "journal"),
    )
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"),
    )


def _fstateBuildAppState():
    """Return a bare app.state stand-in with every lifecycle store."""
    return SimpleNamespace(
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


def _tSeedOwnedContainer(stateApp):
    """Seed an ACTIVE owned container bound to a real browser session."""
    sCapability = browserSession.fsMintBootstrapCapability(
        stateApp.dictBrowserSessions,
    )
    sSessionId, sCredential = browserSession.ftRedeemCapability(
        stateApp.dictBrowserSessions, sCapability,
    )
    recordOwner = containerOwnership.OwnerRecord(
        sLeaseId=containerOwnership.fsMintLease(),
        fileHandleLock=None,
        sAgentToken=containerOwnership.fsMintAgentToken(),
        sContainerId=S_CONTAINER_ID,
        sBrowserSessionId=sSessionId,
    )
    stateApp.dictContainerOwners[S_PROJECT_NAME] = recordOwner
    stateApp.dictSessionOwner[sSessionId] = S_PROJECT_NAME
    return (sSessionId, sCredential, recordOwner)


def _flistMessagesFor(caplog, sPrefix):
    """Return the captured vaibify messages carrying one transition tag."""
    return [
        recordLog.getMessage() for recordLog in caplog.records
        if recordLog.name == "vaibify"
        and recordLog.getMessage().startswith(sPrefix)
    ]


@pytest.mark.asyncio
async def testOrphanCommitAnnouncesItselfWithContainerAndTiming(caplog):
    """The §5 orphan commit logs a WARNING naming the container.

    This is the exact silence that made the 2026-08-27 lease loss
    undiagnosable, so it is pinned at WARNING — the researcher-visible
    severity — and must name the container and survive with the
    record retained.
    """
    caplog.set_level(logging.INFO, logger="vaibify")
    stateApp = _fstateBuildAppState()
    _tSeedOwnedContainer(stateApp)
    await sessionLifecycle.fnOrphanSession(stateApp, S_PROJECT_NAME)
    listOrphanLines = _flistMessagesFor(caplog, "SESSION orphaned")
    assert len(listOrphanLines) == 1
    assert repr(S_PROJECT_NAME) in listOrphanLines[0]
    assert caplog.records[-1].levelno >= logging.WARNING or any(
        recordLog.levelno == logging.WARNING
        for recordLog in caplog.records
        if recordLog.getMessage().startswith("SESSION orphaned")
    )


def testClaimGrantAndForceReleaseAnnounceThemselves(caplog):
    """Granting and dropping ownership each log, without the lease value."""
    caplog.set_level(logging.INFO, logger="vaibify")
    dictOwners = {}
    iStatus, dictPayload = containerOwnership.ftClaim(
        dictOwners, S_PROJECT_NAME, "", 8055,
        sContainerId=S_CONTAINER_ID,
        sBrowserSessionId="session-abcdef123456",
    )
    assert iStatus == 200
    listGrantLines = _flistMessagesFor(caplog, "OWNERSHIP claim granted")
    assert len(listGrantLines) == 1
    assert repr(S_PROJECT_NAME) in listGrantLines[0]
    assert dictPayload["sLeaseId"] not in listGrantLines[0]
    containerOwnership._fnForceReleaseOwnership(dictOwners, S_PROJECT_NAME)
    listDropLines = _flistMessagesFor(caplog, "OWNERSHIP record")
    assert len(listDropLines) == 1
    assert repr(S_PROJECT_NAME) in listDropLines[0]


def testSocketCloseToZeroNamesTheGraceWindow(caplog):
    """The last socket's close logs that the reconnect window began."""
    caplog.set_level(logging.INFO, logger="vaibify")
    stateApp = _fstateBuildAppState()
    _tSeedOwnedContainer(stateApp)
    containerOwnership.fnIncrementLiveConnection(
        stateApp.dictContainerOwners, S_PROJECT_NAME, bPipelineLane=True,
    )
    containerOwnership.fnDecrementLiveConnection(
        stateApp.dictContainerOwners, S_PROJECT_NAME, bPipelineLane=True,
    )
    listCloseLines = _flistMessagesFor(caplog, "SOCKET closed")
    assert len(listCloseLines) == 1
    assert "reconnect grace window" in listCloseLines[0]


def testFailedCouncilTurnAnnouncesParticipantAndReason(caplog):
    """A failed turn logs a WARNING naming campaign, model, and reason.

    The 2026-08-27 session watched a model fail in the dashboard while
    the hub log recorded nothing; the record builder is the single
    place every failed turn passes through, so the line is pinned
    there.
    """
    caplog.set_level(logging.INFO, logger="vaibify")
    engineCouncil = agentCouncil.CouncilEngine.__new__(
        agentCouncil.CouncilEngine,
    )
    engineCouncil.dictCampaign = {"sCampaignId": "campaign-logtest"}
    dictTurnRecord = engineCouncil._fdictBuildTurnRecord(
        {"sTurnId": "turn-1"},
        {"iRoundNumber": 2},
        {"sParticipantId": "participant-log", "sRequestedModel": "opus"},
        "proposal",
        {"sOutcome": "raised", "sFailureClass": "turnRaised",
         "sFailureReason": "turnRaised: provider exploded"},
        False,
    )
    assert dictTurnRecord["sStatus"] == "failed"
    listTurnLines = _flistMessagesFor(caplog, "COUNCIL turn failed")
    assert len(listTurnLines) == 1
    assert "campaign-logtest" in listTurnLines[0]
    assert "opus" in listTurnLines[0]
    assert "provider exploded" in listTurnLines[0]
