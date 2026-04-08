"""Interactive step handling for pipeline execution."""

import asyncio
from datetime import datetime, timezone


def fdictCreateInteractiveContext():
    """Return a context dict for pause/resume at interactive steps."""
    return {
        "eventResume": asyncio.Event(),
        "sResponse": "",
    }


def fnSetInteractiveResponse(dictContext, sResponse):
    """Set the response and trigger the resume event."""
    dictContext["sResponse"] = sResponse
    dictContext["eventResume"].set()


async def _fiHandleInteractiveStep(
    connectionDocker, sContainerId, dictStep,
    iStepNumber, fnStatusCallback, dictInteractive,
):
    """Pause the pipeline and wait for user decision."""
    if dictInteractive is None:
        return 0
    sStepName = dictStep.get("sName", f"Step {iStepNumber}")
    await fnStatusCallback({
        "sType": "interactivePause",
        "iStepIndex": iStepNumber - 1,
        "iStepNumber": iStepNumber,
        "sStepName": sStepName,
    })
    sResponse = await _fsAwaitInteractiveDecision(
        dictInteractive,
    )
    if sResponse == "skip":
        return 0
    return await _fiRunInteractiveAndRecord(
        connectionDocker, sContainerId, dictStep,
        iStepNumber, fnStatusCallback, dictInteractive,
    )


async def _fiRunInteractiveAndRecord(
    connectionDocker, sContainerId, dictStep,
    iStepNumber, fnStatusCallback, dictInteractive,
):
    """Run the interactive terminal session and record results."""
    import time
    from .pipelineRunner import _fnRecordInputHashes
    from .pipelineUtils import _fnEmitStepResult, _fnRecordRunStats

    fStartTime = time.time()
    sStartTimestamp = datetime.now(timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )
    await fnStatusCallback({
        "sType": "interactiveTerminalStart",
        "iStepNumber": iStepNumber,
        "sStepName": dictStep.get("sName", ""),
        "dictStep": dictStep,
    })
    iExitCode = await _fiAwaitInteractiveComplete(dictInteractive)
    _fnRecordRunStats(dictStep, sStartTimestamp, fStartTime, 0.0)
    await _fnRecordInputHashes(
        connectionDocker, sContainerId, dictStep,
    )
    await fnStatusCallback({
        "sType": "stepStats", "iStepNumber": iStepNumber,
        "dictRunStats": dictStep.get("dictRunStats", {}),
    })
    await _fnEmitStepResult(fnStatusCallback, iStepNumber, iExitCode)
    return iExitCode


async def _fsAwaitInteractiveDecision(dictInteractive):
    """Wait for the user to resume or skip, return response."""
    dictInteractive["eventResume"].clear()
    dictInteractive["sResponse"] = ""
    await dictInteractive["eventResume"].wait()
    return dictInteractive["sResponse"]


async def _fiAwaitInteractiveComplete(dictInteractive):
    """Wait for the frontend to signal interactive step done."""
    dictInteractive["eventResume"].clear()
    dictInteractive["sResponse"] = ""
    await dictInteractive["eventResume"].wait()
    sResponse = dictInteractive["sResponse"]
    if sResponse.startswith("complete:"):
        return int(sResponse.split(":")[1])
    return 0
