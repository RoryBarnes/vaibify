"""HTTP routes for a pending containerization: watch, retry, finish, give up.

The hub owns the containerization (``conversionLifecycle``); these routes
are a browser's window onto it. A reopened tab re-attaches by reading
the view; the three decisions a researcher can take are Try again
(from waiting or interrupted), Finish (commit a staged image once the
project is closed) and Stay in host mode (give up, deleting the
partial). Every route is browser-hub control plane: it reads and
writes host state under ``~/.vaibify`` and the clone's own
``vaibify.yml``, touches no container of the project's (the project has
none yet), and refuses the in-container agent lane by name -- an agent
must never decide to abandon or finish the researcher's conversion.
"""

__all__ = ["fnRegisterAll"]

import asyncio

from fastapi import HTTPException, Request
from pydantic import BaseModel

from ...config import pendingConversions
from .. import conversionLifecycle
from ..routeContext import fnRejectAgentTokenLane
from ..routeScope import S_CARRIER_SEPARATE_AUTHORITY, ffnDeclareCarrierMode


class ReturnToHostModeRequest(BaseModel):
    """Whether to restore the committed vaibify.yml over edits of the researcher's own."""

    bRestoreConfigAnyway: bool = False


def _fnRequireSafeName(sHostName):
    from ...config.containerLock import fbIsValidProjectName
    if not fbIsValidProjectName(sHostName):
        raise HTTPException(400, detail=f"Invalid project name: {sHostName!r}")


def _fdictRequireView(sHostName):
    dictView = conversionLifecycle.fdictReadConversionView(sHostName)
    if dictView is None:
        raise HTTPException(
            404, detail={"sMessage": (
                f"No containerization of '{sHostName}' is pending."
            )},
        )
    return dictView


def _fnRegisterRead(app, dictCtx):
    """Register GET /api/conversions/{sHostName}."""
    del dictCtx

    @app.get("/api/conversions/{sHostName}")
    async def fdictReadConversion(sHostName: str, requestHttp: Request):
        fnRejectAgentTokenLane(requestHttp)
        _fnRequireSafeName(sHostName)
        return _fdictRequireView(sHostName)


def _fnRegisterRetry(app, dictCtx):
    """Register POST /api/conversions/{sHostName}/retry."""
    del dictCtx

    @app.post("/api/conversions/{sHostName}/retry")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictRetryConversion(sHostName: str, requestHttp: Request):
        fnRejectAgentTokenLane(requestHttp)
        _fnRequireSafeName(sHostName)
        _fdictRequireView(sHostName)
        try:
            await asyncio.to_thread(conversionLifecycle.fnRetryConversion, sHostName)
        except conversionLifecycle.ConversionRefusedError as error:
            raise HTTPException(409, detail={"sMessage": str(error)}) from None
        except pendingConversions.ConversionLockHeldError as error:
            raise HTTPException(409, detail={"sMessage": str(error)}) from None
        return _fdictRequireView(sHostName) or {}


def _fnRegisterCommit(app, dictCtx):
    """Register POST /api/conversions/{sHostName}/commit (Finish)."""
    del dictCtx

    @app.post("/api/conversions/{sHostName}/commit")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictCommitConversion(sHostName: str, requestHttp: Request):
        fnRejectAgentTokenLane(requestHttp)
        _fnRequireSafeName(sHostName)
        _fdictRequireView(sHostName)
        try:
            await asyncio.to_thread(conversionLifecycle.fnCommitConversion, sHostName)
        except conversionLifecycle.ConversionBusyError as error:
            raise HTTPException(409, detail={
                "sMessage": str(error), "sRefusal": "busy",
            }) from None
        except (
            conversionLifecycle.ConversionRefusedError,
            pendingConversions.ConversionLockHeldError,
        ) as error:
            raise HTTPException(409, detail={"sMessage": str(error)}) from None
        dictView = conversionLifecycle.fdictReadConversionView(sHostName)
        return {"bCommitted": dictView is None, "dictConversion": dictView}


def _fnRegisterCancel(app, dictCtx):
    """Register POST /api/conversions/{sHostName}/cancel (give up)."""
    del dictCtx

    @app.post("/api/conversions/{sHostName}/cancel")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictCancelConversion(sHostName: str, requestHttp: Request):
        fnRejectAgentTokenLane(requestHttp)
        _fnRequireSafeName(sHostName)
        _fdictRequireView(sHostName)
        try:
            await asyncio.to_thread(conversionLifecycle.fnCancelConversion, sHostName)
        except Exception as error:  # noqa: BLE001 -- the sentence is the answer
            raise HTTPException(409, detail={"sMessage": (
                f"The containerization could not be cancelled cleanly: {error}"
            )}) from None
        return {"bCancelled": True}


def _fnRegisterReturnToHostMode(app, dictCtx):
    """Register POST /api/registry/{sName}/return-to-host-mode.

    The way back for a container entry stranded by a conversion that
    ran before the image was in hand (vaibify 0.1.0): the entry obtains
    its image, no origin record admits a start, and the daemon holds no
    image for it. ``vaibify.yml`` is restored from git HEAD when the
    conversion's own edits are the only differences; otherwise the two
    files are returned for the researcher to decide, and a second call
    with ``bRestoreConfigAnyway`` restores regardless (ruled 2026-10-13).
    """

    @app.post("/api/registry/{sName}/return-to-host-mode")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictReturnToHostMode(
        requestHttp: Request, sName: str, request: ReturnToHostModeRequest,
    ):
        from ...config import registryManager
        from ..hostModeReturn import (
            HostModeReturnRefusedError, fdictJudgeReturnToHostMode,
            fnRestoreCommittedConfig,
        )
        from ..registryRoutes import _fdictRequireProject, _fnRefuseBusyProject
        fnRejectAgentTokenLane(requestHttp)
        _fnRequireSafeName(sName)
        dictProject = _fdictRequireProject(sName)
        await _fnRefuseBusyProject(app, sName, dictCtx, "return it to host mode")
        try:
            dictJudgement = await asyncio.to_thread(
                fdictJudgeReturnToHostMode, dictProject, dictCtx.get("docker"),
            )
        except HostModeReturnRefusedError as error:
            raise HTTPException(409, detail={"sMessage": str(error)}) from None
        if not dictJudgement["bOnlyTheConversionsEdits"] and not request.bRestoreConfigAnyway:
            return {
                "bReturned": False, "bAskToRestore": True,
                "sWorkingConfig": dictJudgement["sWorkingConfig"],
                "sCommittedConfig": dictJudgement["sCommittedConfig"],
            }
        await asyncio.to_thread(
            fnRestoreCommittedConfig, dictProject, dictJudgement["sCommittedConfig"],
        )
        try:
            sHostName = await asyncio.to_thread(
                registryManager.fsReturnProjectToHost, sName,
            )
        except KeyError as error:
            raise HTTPException(404, str(error)) from None
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
        return {"bReturned": True, "sHostName": sHostName}


def fnRegisterAll(app, dictCtx):
    """Register the pending-containerization routes and the way back."""
    _fnRegisterRead(app, dictCtx)
    _fnRegisterRetry(app, dictCtx)
    _fnRegisterCommit(app, dictCtx)
    _fnRegisterCancel(app, dictCtx)
    _fnRegisterReturnToHostMode(app, dictCtx)
