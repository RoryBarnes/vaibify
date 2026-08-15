"""Ruling R6's downstream mark: dependents RUN, visibly marked.

When a step's remote-data documentation degrades — marker refused,
records refused, marker retained — later steps still execute, and each
one's result event carries ``bDownstreamOfDegradedProvenance`` so the
dashboard can mark it. The mark is decided by the taint state AT STEP
ENTRY: a step's own degradation marks its successors, never itself,
whose own events (``stepMarkerRefused`` / ``provenanceDegraded`` /
``remoteDataMarkerRetained``) already say what happened. The flag
persists with the step-result state record so a reconnect re-renders
the mark; the browser-side rendering is pinned in
``tests/browser/testHostProjectJourney.py``.
"""

import asyncio
import threading

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from vaibify.gui import pipelineLogger, pipelineState
from vaibify.gui.pipelineRunner import _fiRunStepList


def _fdictTwoStepWorkflow():
    """A remote-data step followed by a plain dependent."""
    return {"listSteps": [
        {
            "sStepId": "pull-archive",
            "sName": "Pull Archive",
            "sDirectory": "PullArchive",
            "saDataCommands": ["fetch data"],
            "listRemoteData": [{
                "sPath": "data/pull.fits",
                "sSourceUrl": "https://archive.example/query",
            }],
        },
        {
            "sStepId": "analyze",
            "sName": "Analyze",
            "sDirectory": "Analyze",
            "saDataCommands": ["run analysis"],
        },
    ]}


def _flistRunTwoSteps(fdictCommitStub):
    """Run the two-step list with every container edge stubbed."""
    listEvents = []

    async def fnCallback(dictEvent):
        listEvents.append(dictEvent)

    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (0, "")
    with patch(
        "vaibify.gui.pipelineRunner.ftRunStepCommands",
        new=AsyncMock(return_value=(0, 1.0)),
    ), patch(
        "vaibify.gui.pipelineRunner._fsetSnapshotDirectory",
        new=AsyncMock(return_value=set()),
    ), patch(
        "vaibify.gui.pipelineRunner._fnEmitDiscoveredOutputs",
        new=AsyncMock(),
    ), patch(
        "vaibify.gui.pipelineRunner._fiCheckDependencies",
        new=AsyncMock(return_value=0),
    ), patch(
        "vaibify.gui.workflowManager.flistCleanStepScratchDirs",
        new=MagicMock(),
    ):
        asyncio.run(_fiRunStepList(
            mockDocker, "cid", _fdictTwoStepWorkflow(), "ws",
            {"sRepoRoot": "/workspace/repo"}, fnCallback,
            fdictCommitProvenance=fdictCommitStub,
        ))
    return listEvents


def _fdictRefusedCommitStub(sStepId, listRecords):
    """A commit outcome that degrades the run's provenance."""
    return {
        "bCommitted": False,
        "sDetail": "the record merge was refused",
        "listRefusals": [],
    }


def _fdictResultEventFor(listEvents, iStepNumber):
    """Return the stepPass/stepFail event for one step."""
    return next(
        dictEvent for dictEvent in listEvents
        if dictEvent.get("sType") in ("stepPass", "stepFail")
        and dictEvent.get("iStepNumber") == iStepNumber
    )


@pytest.mark.falsification
def testAStepAfterADegradedOneWearsTheDownstreamMark():
    """Kills: the taint record never being set beside the degradation
    events — later steps then run with no visible connection to the
    undocumented data they may have consumed (ruling R6's whole
    point)."""
    listEvents = _flistRunTwoSteps(_fdictRefusedCommitStub)
    assert any(
        dictEvent.get("sType") == "provenanceDegraded"
        for dictEvent in listEvents
    ), "control failed: the fixture no longer degrades provenance"
    dictSecond = _fdictResultEventFor(listEvents, 2)
    assert dictSecond.get("bDownstreamOfDegradedProvenance") is True


@pytest.mark.falsification
def testTheDegradingStepItselfWearsNoMark():
    """Kills: reading the taint state at emit time instead of step
    entry — the degrading step then marks ITSELF, and the glyph's
    tooltip ("ran downstream of...") becomes a false statement about
    that step."""
    listEvents = _flistRunTwoSteps(_fdictRefusedCommitStub)
    dictFirst = _fdictResultEventFor(listEvents, 1)
    assert "bDownstreamOfDegradedProvenance" not in dictFirst


def testACleanRunMarksNothing():
    def fdictCleanCommitStub(sStepId, listRecords):
        return {
            "bCommitted": True, "sDetail": "",
            "iInstalled": 1, "listRefusals": [],
        }

    listEvents = _flistRunTwoSteps(fdictCleanCommitStub)
    for iStepNumber in (1, 2):
        dictResult = _fdictResultEventFor(listEvents, iStepNumber)
        assert "bDownstreamOfDegradedProvenance" not in dictResult


@pytest.mark.falsification
def testABuiltResultCarriesTheFlagOnlyWhenTrue():
    """Kills: the state record dropping the flag — the mark then
    exists only as a live WebSocket event, and any reconnect silently
    forgets which results ran downstream of undocumented data."""
    dictFlagged = pipelineState.fdictBuildStepResult(
        2, "passed", 0, bDownstreamOfDegradedProvenance=True,
    )
    assert dictFlagged["bDownstreamOfDegradedProvenance"] is True
    dictPlain = pipelineState.fdictBuildStepResult(2, "passed", 0)
    assert "bDownstreamOfDegradedProvenance" not in dictPlain


@pytest.mark.falsification
def testAFlaggedEventReachesTheDurableRecordOnBothWritePaths():
    """Kills: either persistence dispatch dropping the event's flag
    (the inline lane and the StateWriter lane both build the durable
    record from the event; a copy that forgets the flag makes the
    reconnect render depend on which lane happened to serve the
    run)."""
    dictEvent = {
        "sType": "stepPass", "iStepNumber": 2, "iExitCode": 0,
        "bDownstreamOfDegradedProvenance": True,
    }
    listRecords = []
    with patch.object(
        pipelineState, "fnRecordStepResult",
        side_effect=lambda connection, sId, dictState, dictResult: (
            listRecords.append(dictResult)
        ),
    ):
        pipelineLogger._fnApplyStepResultEvent(
            None, "cid", {}, dictEvent, threading.Lock(),
        )
    assert listRecords[0]["bDownstreamOfDegradedProvenance"] is True
    writerFake = MagicMock()
    pipelineLogger._fnDispatchEventToWriter(writerFake, dictEvent)
    dictQueued = writerFake.fnEnqueueStepResult.call_args.args[0]
    assert dictQueued["bDownstreamOfDegradedProvenance"] is True
