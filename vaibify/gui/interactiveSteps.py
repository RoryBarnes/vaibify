"""Interactive step handling for pipeline execution."""

__all__ = [
    "fdictCreateInteractiveContext",
    "fnSetInteractiveResponse",
]

import asyncio


F_INTERACTIVE_WAIT_HOURS = 24.0
I_ABANDONED_EXIT_CODE = 124
S_ABANDONED_SENTINEL = f"abandoned:{I_ABANDONED_EXIT_CODE}"


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


def fsBuildAbandonedReason(fHours):
    """Build the human-readable failure reason for an abandoned step."""
    return (
        f"interactive step abandoned: no user response for "
        f"{fHours:g}h"
    )


async def _fbWaitWithTimeout(dictInteractive, fHours):
    """Wait for resume up to fHours; return True if resumed, False if timeout."""
    fSeconds = fHours * 3600.0
    try:
        await asyncio.wait_for(
            dictInteractive["eventResume"].wait(), timeout=fSeconds,
        )
        return True
    except asyncio.TimeoutError:
        return False


async def _fnEmitInteractivePause(
    fnStatusCallback, iStepNumber, dictStep,
):
    """Emit the pause event for an interactive step."""
    sStepName = dictStep.get("sName", f"Step {iStepNumber}")
    await fnStatusCallback({
        "sType": "interactivePause",
        "iStepIndex": iStepNumber - 1,
        "iStepNumber": iStepNumber,
        "sStepName": sStepName,
    })


async def _fiDispatchInteractiveResponse(
    sResponse, connectionDocker, sContainerId,
    dictStep, iStepNumber, fnStatusCallback, dictInteractive,
):
    """Route the user's response to the appropriate handler."""
    if sResponse == S_ABANDONED_SENTINEL:
        return await _fiEmitAbandonment(fnStatusCallback, iStepNumber)
    if sResponse == "skip":
        return 0
    return await _fiRunInteractiveAndRecord(
        connectionDocker, sContainerId, dictStep,
        iStepNumber, fnStatusCallback, dictInteractive,
    )


async def _fiHandleInteractiveStep(
    connectionDocker, sContainerId, dictStep,
    iStepNumber, fnStatusCallback, dictInteractive,
):
    """Pause the pipeline and wait for user decision."""
    if dictInteractive is None:
        return 0
    await _fnEmitInteractivePause(
        fnStatusCallback, iStepNumber, dictStep,
    )
    sResponse = await _fsAwaitInteractiveDecision(dictInteractive)
    return await _fiDispatchInteractiveResponse(
        sResponse, connectionDocker, sContainerId,
        dictStep, iStepNumber, fnStatusCallback, dictInteractive,
    )


async def _fiEmitAbandonment(fnStatusCallback, iStepNumber):
    """Emit a step-fail event with the abandonment reason and return 124."""
    from .pipelineUtils import _fnEmitStepResult

    sReason = fsBuildAbandonedReason(F_INTERACTIVE_WAIT_HOURS)
    await fnStatusCallback({
        "sType": "interactiveAbandoned",
        "iStepNumber": iStepNumber,
        "sFailureReason": sReason,
        "iExitCode": I_ABANDONED_EXIT_CODE,
    })
    await _fnEmitStepResult(
        fnStatusCallback, iStepNumber, I_ABANDONED_EXIT_CODE,
    )
    return I_ABANDONED_EXIT_CODE


async def _fnEmitAbandonedEvent(fnStatusCallback, iStepNumber):
    """Emit the interactiveAbandoned status event."""
    await fnStatusCallback({
        "sType": "interactiveAbandoned",
        "iStepNumber": iStepNumber,
        "sFailureReason": fsBuildAbandonedReason(
            F_INTERACTIVE_WAIT_HOURS,
        ),
        "iExitCode": I_ABANDONED_EXIT_CODE,
    })


async def _fnEmitTerminalStart(fnStatusCallback, iStepNumber, dictStep):
    """Emit the interactiveTerminalStart event."""
    await fnStatusCallback({
        "sType": "interactiveTerminalStart",
        "iStepNumber": iStepNumber,
        "sStepName": dictStep.get("sName", ""),
        "dictStep": dictStep,
    })


async def _fiRunInteractiveAndRecord(
    connectionDocker, sContainerId, dictStep,
    iStepNumber, fnStatusCallback, dictInteractive,
):
    """Run the interactive terminal session and record results."""
    import time
    from .pipelineUtils import _fnEmitStepResult, _fnRecordRunStats

    fStartTime = time.time()
    await _fnEmitTerminalStart(fnStatusCallback, iStepNumber, dictStep)
    iExitCode = await _fiAwaitInteractiveComplete(dictInteractive)
    if iExitCode == I_ABANDONED_EXIT_CODE:
        await _fnEmitAbandonedEvent(fnStatusCallback, iStepNumber)
    _fnRecordRunStats(dictStep, fStartTime, 0.0, iExitCode=iExitCode)
    await fnStatusCallback({
        "sType": "stepStats", "iStepNumber": iStepNumber,
        "dictRunStats": dictStep.get("dictRunStats", {}),
    })
    await _fnEmitStepResult(fnStatusCallback, iStepNumber, iExitCode)
    return iExitCode


async def _fsAwaitInteractiveDecision(dictInteractive):
    """Wait for the user to resume or skip; return response or abandoned sentinel."""
    dictInteractive["eventResume"].clear()
    dictInteractive["sResponse"] = ""
    bResumed = await _fbWaitWithTimeout(
        dictInteractive, F_INTERACTIVE_WAIT_HOURS,
    )
    if not bResumed:
        return S_ABANDONED_SENTINEL
    return dictInteractive["sResponse"]


async def _fiAwaitInteractiveComplete(dictInteractive):
    """Wait for the frontend to signal interactive step done."""
    dictInteractive["eventResume"].clear()
    dictInteractive["sResponse"] = ""
    bResumed = await _fbWaitWithTimeout(
        dictInteractive, F_INTERACTIVE_WAIT_HOURS,
    )
    if not bResumed:
        return I_ABANDONED_EXIT_CODE
    sResponse = dictInteractive["sResponse"]
    if sResponse.startswith("complete:"):
        return int(sResponse.split(":")[1])
    return 0
