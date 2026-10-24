"""The dashboard's "Set up Docker" wizard: plan, run one step, watch it.

Three routes over the same plan ``vaibify setup-docker`` uses
(``vaibify/install/runtimeSetup*``). The GET reads this machine and
answers with the remaining steps; the POST runs ONE action of the first
step, named by the client and checked against a plan the server builds
again for the purpose, so the client chooses among the server's
actions and never supplies a command; the progress GET is the build
modal's pattern, a tail the open modal polls while a step runs.

All three answer about, and act on, the researcher's own machine: they
refuse the in-container agent lane by name, and the POST is excluded
from the agent catalog. An agent can never install software on the
host or start a virtual machine.

The progress record is in-process, like the build's: a hub restart
forgets it, which is honest, since the step it described died with the
process.
"""

__all__ = ["fnRegisterAll"]

import asyncio
from collections import deque

from typing import Optional

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

from ..routeContext import fnRejectAgentTokenLane


_I_PROGRESS_TAIL_LINES = 300
_DICT_SETUP_PROGRESS = {
    "bLive": False, "dequeTail": deque(maxlen=_I_PROGRESS_TAIL_LINES),
    "sOutcome": "", "sTitle": "",
}


class RuntimeSetupRunRequest(BaseModel):
    """One action of the plan's first step, and a new machine's size.

    The two sizes are strict: a JSON ``true`` or ``"4"`` is refused at
    the boundary rather than read as a number.
    """

    sActionName: str
    iCpus: Optional[int] = Field(default=None, strict=True)
    iMemoryGigabytes: Optional[int] = Field(default=None, strict=True)


def fnRegisterAll(app, dictCtx):
    """Register the setup plan, step-run and progress routes."""
    _fnRegisterReadPlan(app)
    _fnRegisterRunStep(app, dictCtx)
    _fnRegisterReadProgress(app)


def _fdictStepForClient(dictStep):
    """Return a step without its invocations: the client never sees argv.

    It needs the action names to send back and the commands to SHOW; the
    commands to RUN stay on the server, so nothing the page holds could
    be edited into something the server would execute.
    """
    dictClient = dict(dictStep)
    dictClient["listActions"] = [
        {"sActionName": dictAction["sActionName"],
         "sLabel": dictAction["sLabel"]}
        for dictAction in dictStep["listActions"]
    ]
    return dictClient


def _fdictPlanForClient(dictPlan):
    return {"bReady": dictPlan["bReady"],
            "listSteps": [_fdictStepForClient(d) for d in dictPlan["listSteps"]]}


def _ftReadFactsAndPlan(dictRequested=None):
    from vaibify.install.runtimeSetupFacts import fdictReadHostFacts
    from vaibify.install.runtimeSetupPlan import (
        fdictPlanRuntimeSetup, fdictValidateColimaSettings,
    )
    dictFacts = fdictReadHostFacts()
    dictSettings = fdictValidateColimaSettings(dictRequested or {}, dictFacts)
    return dictFacts, fdictPlanRuntimeSetup(dictFacts, dictSettings)


def _fnRegisterReadPlan(app):
    @app.get("/api/runtime/setup")
    async def fdictReadRuntimeSetupPlan(requestHttp: Request):
        fnRejectAgentTokenLane(requestHttp)
        _, dictPlan = await asyncio.to_thread(_ftReadFactsAndPlan)
        return _fdictPlanForClient(dictPlan)


def _fnRecordLine(sLine):
    _DICT_SETUP_PROGRESS["dequeTail"].append(sLine)


def _fnRunStepRecordingProgress(dictPlan, sActionName, sPrivilegeChannel):
    """Run one step, owning the progress record only while it holds the lock."""
    from vaibify.install.runtimeSetupRunner import fnRunAction
    dictOwnership = {"bOwnsRecord": False}

    def fnBeginRecording():
        _DICT_SETUP_PROGRESS["dequeTail"].clear()
        _DICT_SETUP_PROGRESS["sTitle"] = dictPlan["listSteps"][0]["sTitle"]
        _DICT_SETUP_PROGRESS["sOutcome"] = ""
        _DICT_SETUP_PROGRESS["bLive"] = True
        dictOwnership["bOwnsRecord"] = True

    try:
        fnRunAction(dictPlan, sActionName, sPrivilegeChannel, _fnRecordLine,
                    fnLockHeld=fnBeginRecording)
        _DICT_SETUP_PROGRESS["sOutcome"] = "succeeded"
    except Exception:
        if dictOwnership["bOwnsRecord"]:
            _DICT_SETUP_PROGRESS["sOutcome"] = "failed"
        raise
    finally:
        if dictOwnership["bOwnsRecord"]:
            _DICT_SETUP_PROGRESS["bLive"] = False


def _fnRegisterRunStep(app, dictCtx):
    @app.post("/api/runtime/setup/run")
    async def fdictRunRuntimeSetupStep(
        request: RuntimeSetupRunRequest, requestHttp: Request,
    ):
        from vaibify.gui.dockerStatus import fdictRetryDockerConnection
        from vaibify.install.runtimeSetupRunner import RuntimeSetupError
        fnRejectAgentTokenLane(requestHttp)
        dictRequested = {
            sKey: iValue for sKey, iValue in (
                ("iCpus", request.iCpus),
                ("iMemoryGigabytes", request.iMemoryGigabytes))
            if iValue is not None
        }
        try:
            dictFacts, dictPlan = await asyncio.to_thread(
                _ftReadFactsAndPlan, dictRequested)
        except ValueError as error:
            raise HTTPException(422, detail={"sMessage": str(error)})
        try:
            await asyncio.to_thread(
                _fnRunStepRecordingProgress, dictPlan, request.sActionName,
                dictFacts["sPrivilegeChannel"])
        except RuntimeSetupError as error:
            raise HTTPException(409, detail={"sMessage": str(error)})
        await asyncio.to_thread(fdictRetryDockerConnection, dictCtx)
        _, dictPlanAfter = await asyncio.to_thread(_ftReadFactsAndPlan)
        return _fdictPlanForClient(dictPlanAfter)


def _fnRegisterReadProgress(app):
    @app.get("/api/runtime/setup/progress")
    async def fdictReadRuntimeSetupProgress(requestHttp: Request):
        fnRejectAgentTokenLane(requestHttp)
        return {
            "bLive": _DICT_SETUP_PROGRESS["bLive"],
            "sTitle": _DICT_SETUP_PROGRESS["sTitle"],
            "sOutcome": _DICT_SETUP_PROGRESS["sOutcome"],
            "listTail": list(_DICT_SETUP_PROGRESS["dequeTail"]),
        }
