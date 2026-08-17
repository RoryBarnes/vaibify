"""Unresolved pull markers gate the reproducibility level (§4.5 C2).

A set marker means remote data may sit on disk with no committed
record of its origin. Ruling R2's second condition: reconciliation
GATES the level, never prints beside it — so a workflow carrying any
unresolved marker is level 0 regardless of how green its steps look,
until the marked step's next successful run clears it. The degraded
terminal report is the same truth at run scope: "completed with
degraded provenance", never plain "completed".
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from vaibify.gui.pipelineLogger import (
    _fnDispatchEventToWriter,
    _fnFinalizeRun,
)
from vaibify.reproducibility.levelGates import fiProofLevel


def _fdictAllGreenWorkflow():
    """One step satisfying every L1 criterion (mirrors the L1 tests)."""
    return {"listSteps": [{
        "sName": "A", "sDirectory": "A",
        "bNoInputData": True,
        "dictVerification": {
            "sUser": "passed",
            "sUnitTest": "passed",
            "sIntegrity": "passed",
            "sQualitative": "passed",
            "sQuantitative": "passed",
        },
    }]}


@pytest.mark.falsification
def testAnUnresolvedMarkerCapsTheLevelAtZero():
    """Kills: the marker gate at the base of the ladder.

    Without it, a workflow whose pulled data has no committed record
    still reports Self-Consistent — the level would print beside the
    problem instead of gating on it.
    """
    dictWorkflow = _fdictAllGreenWorkflow()
    assert fiProofLevel(dictWorkflow, "/repo") == 1, (
        "control failed: the fixture no longer reaches L1, so the "
        "gate assertion below proves nothing"
    )
    dictWorkflow["listUnresolvedRemoteDataMarkers"] = ["pull-archive"]
    assert fiProofLevel(dictWorkflow, "/repo") == 0, (
        "an unresolved pull marker did not gate the level; "
        "undocumented remote data reports Self-Consistent"
    )


class _RecordingWriter:
    def __init__(self):
        self.listUpdates = []

    def fnEnqueueUpdate(self, dictUpdate):
        self.listUpdates.append(dictUpdate)


def testEveryDegradationEventSetsTheDurableFlag():
    for sEventType in (
        "stepMarkerRefused", "remoteDataMarkerRetained",
        "provenanceDegraded",
    ):
        writerFake = _RecordingWriter()
        _fnDispatchEventToWriter(writerFake, {"sType": sEventType})
        assert {"bProvenanceDegraded": True} in writerFake.listUpdates, (
            f"{sEventType} did not set the durable degradation flag"
        )


@pytest.mark.falsification
def testTheTerminalEventCarriesTheDegradedVerdict():
    """Kills: the §4.6 terminal report — degraded, not plain completed.

    The flag was durably recorded during the run; a terminal event
    without it lets the dashboard toast a clean completion over
    undocumented pulled data.
    """
    listEvents = []

    async def fnCallback(dictEvent):
        listEvents.append(dictEvent)

    with patch(
        "vaibify.gui.pipelineLogger.fnWriteLogToContainer",
        new=AsyncMock(),
    ):
        asyncio.run(_fnFinalizeRun(
            None, "cid",
            {"bProvenanceDegraded": True, "dictStepStats": {}},
            0, "/logs/run.log", [], {"listSteps": []}, "",
            fnCallback,
        ))
    listTerminal = [
        dictEvent for dictEvent in listEvents
        if dictEvent.get("sType") in ("completed", "failed")
    ]
    assert listTerminal and listTerminal[0]["bProvenanceDegraded"] is (
        True
    ), (
        "the terminal event dropped the degradation verdict; the "
        "dashboard reports a clean completion"
    )
