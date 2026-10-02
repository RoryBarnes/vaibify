"""Interactive step handling for pipeline execution."""

__all__ = [
    "fdictCreateInteractiveContext",
    "fnSetInteractiveResponse",
]

import asyncio
import itertools


_COUNTER_PAUSE_SEQUENCE = itertools.count(1)

F_INTERACTIVE_WAIT_HOURS = 24.0
I_ABANDONED_EXIT_CODE = 124
S_ABANDONED_SENTINEL = f"abandoned:{I_ABANDONED_EXIT_CODE}"


def fdictCreateInteractiveContext(sRunId=""):
    """Return a context dict for pause/resume at interactive steps.

    One context belongs to one RUN, not to the socket that started it:
    the run outlives its socket, so a response arriving on a later
    socket must reach the context the run is waiting on.
    ``dictPendingEvent`` is the event the run is waiting behind (its
    pause, or its terminal start) while it waits, else ``None``, so a
    reconnecting client can be told what the run is waiting for.
    """
    return {
        "eventResume": asyncio.Event(),
        "sResponse": "",
        "sRunId": sRunId,
        "dictPendingEvent": None,
        "iPauseSequence": 0,
    }


def _fnMarkAwaiting(dictInteractive, dictEvent):
    """Record the event the run is now waiting behind."""
    if dictInteractive is None:
        return
    if dictInteractive["sRunId"]:
        dictEvent["sRunId"] = dictInteractive["sRunId"]
    dictInteractive["dictPendingEvent"] = dictEvent
    dictInteractive["iPauseSequence"] = next(_COUNTER_PAUSE_SEQUENCE)


def _fnClearAwaiting(dictInteractive):
    """Record that the run is no longer waiting."""
    if dictInteractive is not None:
        dictInteractive["dictPendingEvent"] = None


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
    fnStatusCallback, iStepNumber, dictStep, dictInteractive=None,
):
    """Emit the pause event for an interactive step and remember it."""
    sStepName = dictStep.get("sName", f"Step {iStepNumber}")
    dictEvent = {
        "sType": "interactivePause",
        "iStepIndex": iStepNumber - 1,
        "iStepNumber": iStepNumber,
        "sStepName": sStepName,
    }
    _fnMarkAwaiting(dictInteractive, dictEvent)
    await fnStatusCallback(dictEvent)


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
        fnStatusCallback, iStepNumber, dictStep, dictInteractive,
    )
    try:
        sResponse = await _fsAwaitInteractiveDecision(dictInteractive)
    finally:
        _fnClearAwaiting(dictInteractive)
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


async def _fnEmitTerminalStart(
    fnStatusCallback, iStepNumber, dictStep, dictInteractive=None,
):
    """Emit the interactiveTerminalStart event and remember it."""
    dictEvent = {
        "sType": "interactiveTerminalStart",
        "iStepNumber": iStepNumber,
        "sStepName": dictStep.get("sName", ""),
        "dictStep": dictStep,
    }
    _fnMarkAwaiting(dictInteractive, dictEvent)
    await fnStatusCallback(dictEvent)


async def _fiRunInteractiveAndRecord(
    connectionDocker, sContainerId, dictStep,
    iStepNumber, fnStatusCallback, dictInteractive,
):
    """Run the interactive terminal session and record results."""
    import time
    from .pipelineUtils import _fnEmitStepResult, _fnRecordRunStats

    fStartTime = time.time()
    await _fnEmitTerminalStart(
        fnStatusCallback, iStepNumber, dictStep, dictInteractive)
    try:
        iExitCode = await _fiAwaitInteractiveComplete(dictInteractive)
    finally:
        _fnClearAwaiting(dictInteractive)
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
