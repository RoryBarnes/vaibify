"""A warning raised before the run is in the run log the toast points at.

The page answers a pre-run warning with "A warning was raised before
the run. See the run log." and appends the warning to its output pane;
the log FILE never carried it, because the runner emitted the event
through the raw status callback rather than the logging one, and the
log extractor knew no such event (researcher-reported 2026-11-05: a
toast with nothing to find). Both halves are pinned here against the
real runner entry point and the real logging callback.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vaibify.gui import pipelineLogger, pipelineRunner


@pytest.mark.falsification
def testAPreRunWarningReachesThePageAndTheRunLogAlike():
    """Kills: the runner emitting the warning past the logging callback."""
    listEvents = []
    listLogLines = []

    async def fnStatusCallback(dictEvent):
        listEvents.append(dictEvent)

    fnLogging = pipelineLogger.ffBuildLoggingCallback(fnStatusCallback, listLogLines)
    with patch.object(
        pipelineRunner, "_ftPrepareLogAndVariables",
        AsyncMock(return_value=("/log", listLogLines, fnLogging, {})),
    ), patch.object(
        pipelineRunner, "_flistPreflightValidate", AsyncMock(return_value=[]),
    ), patch.object(
        pipelineRunner, "_flistCollectPreflightWarnings",
        lambda *tArguments: ["Workspace free space is low: 0.4 GB free"],
    ), patch.object(
        pipelineRunner, "_fiRunStepsAndLog", AsyncMock(return_value=0),
    ):
        iResult = asyncio.run(pipelineRunner._fiRunWithLogging(
            MagicMock(), "cid", {"listSteps": []}, "/work",
            fnStatusCallback, "runAll"))
    assert iResult == 0
    assert {"sType": "preflightWarning",
            "sMessage": "Workspace free space is low: 0.4 GB free"} in listEvents
    assert listLogLines == ["WARNING: Workspace free space is low: 0.4 GB free"]


@pytest.mark.falsification
def testTheLogExtractorWritesTheWarningInThePagesWords():
    """Kills: the extractor dropping the pre-run warning event."""
    assert pipelineLogger._flistExtractLogLines({
        "sType": "preflightWarning", "sMessage": "Could not update 1 test-support file(s)",
    }) == ["WARNING: Could not update 1 test-support file(s)"]
