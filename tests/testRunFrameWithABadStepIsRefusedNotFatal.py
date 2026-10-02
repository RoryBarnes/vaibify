"""A run frame naming a step that cannot resolve is refused with an event.

The pipeline socket's message loop resolved step labels and indices
before dispatch without a guard, so a typo in a label or an index that
was not an integer raised out of the loop and closed the socket with no
event. These tests drive the real loop and assert the socket stays open,
the client is told why, and the next valid frame still dispatches.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from vaibify.gui.pipelineServer import fnPipelineMessageLoop

DICT_WORKFLOW = {"listSteps": [{"sName": "First", "sDirectory": "First"}]}


async def _flistRunLoop(listFrames):
    listSent = []
    listPending = list(listFrames)

    async def fnReceiveText():
        if listPending:
            return listPending.pop(0)
        raise ConnectionAbortedError("disconnect")

    websocket = AsyncMock()
    websocket.receive_text = fnReceiveText
    websocket.send_json = AsyncMock(
        side_effect=lambda dictEvent: listSent.append(dictEvent))
    with patch("vaibify.gui.pipelineServer._fnSafeDispatch",
               new_callable=AsyncMock) as mockDispatch:
        with pytest.raises(ConnectionAbortedError):
            await fnPipelineMessageLoop(
                websocket, AsyncMock(), "ctr", DICT_WORKFLOW, {}, "/w",
                dictPipelineTasks={},
            )
    return listSent, mockDispatch


@pytest.mark.falsification
@pytest.mark.asyncio
@pytest.mark.parametrize("dictBadFrame", [
    {"sAction": "runSelected", "listStepLabels": ["Z99"]},
    {"sAction": "runSelected", "listStepIndices": ["not-a-number"]},
    {"sAction": "runFrom", "sStartStepLabel": "typo"},
])
async def testAFrameNamingAnUnresolvableStepIsRefusedAndTheSocketSurvives(
    dictBadFrame,
):
    """Kills: letting the resolution error escape the message loop."""
    listFrames = [json.dumps(dictBadFrame), json.dumps({"sAction": "runAll"})]
    listSent, mockDispatch = await _flistRunLoop(listFrames)
    listRefusals = [d for d in listSent if d.get("sType") == "runRefused"]
    assert len(listRefusals) == 1
    assert dictBadFrame["sAction"] in listRefusals[0]["sMessage"]
    assert mockDispatch.call_count == 1


@pytest.mark.falsification
@pytest.mark.asyncio
async def testAFrameThatIsNotAJsonObjectIsRefusedAndTheSocketSurvives():
    """Kills: json.loads or .get raising out of the loop on a bad frame."""
    listSent, _ = await _flistRunLoop(["not json", "[1, 2]"])
    assert [d["sType"] for d in listSent] == ["runRefused", "runRefused"]
