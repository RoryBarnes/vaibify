"""Workflow management route handlers."""

__all__ = ["fnRegisterAll"]

import json
import posixpath

from fastapi import HTTPException
from typing import Optional

from .. import workflowManager
from ..pipelineRunner import fsShellQuote
from ..pipelineServer import (
    CreateWorkflowRequest,
    fdictHandleConnect,
    _fsSanitizeServerError,
)


def _fbIsContainerStopped(error):
    """Return True if the error indicates a stopped container."""
    sMessage = str(error).lower()
    return "409" in sMessage and "conflict" in sMessage


def _fnRejectDuplicateWorkflowName(
    connectionDocker, sContainerId, sWorkflowName
):
    """Raise 409 if another workflow in container uses this name."""
    listExisting = workflowManager.flistFindWorkflowsInContainer(
        connectionDocker, sContainerId
    )
    for dictWorkflow in listExisting:
        if dictWorkflow["sName"] == sWorkflowName:
            raise HTTPException(
                409,
                f"A workflow named '{sWorkflowName}' already "
                f"exists at {dictWorkflow['sPath']}",
            )


def _fsValidateRepoDirectory(
    connectionDocker, sContainerId, sRepoDirectory
):
    """Validate the repo directory exists under /workspace/."""
    sClean = sRepoDirectory.strip().strip("/")
    if not sClean:
        raise HTTPException(
            400, "sRepoDirectory is required"
        )
    if ".." in sClean.split("/"):
        raise HTTPException(
            400, "sRepoDirectory may not contain '..'"
        )
    sFullPath = posixpath.join("/workspace", sClean)
    iExitCode, _ = connectionDocker.ftResultExecuteCommand(
        sContainerId,
        f"test -d {fsShellQuote(sFullPath)}",
    )
    if iExitCode != 0:
        raise HTTPException(
            404,
            f"Repo directory not found: {sFullPath}",
        )
    return sFullPath


def _fnRegisterWorkflowSearch(app, dictCtx):
    """Register GET /api/workflows route."""

    @app.get("/api/workflows/{sContainerId}")
    async def fnFindWorkflows(sContainerId: str):
        dictCtx["require"]()
        try:
            return workflowManager.flistFindWorkflowsInContainer(
                dictCtx["docker"], sContainerId
            )
        except Exception as error:
            if _fbIsContainerStopped(error):
                raise HTTPException(
                    409, "Container is not running. "
                    "Start it before selecting a workflow.")
            raise HTTPException(
                500, f"Search failed: "
                f"{_fsSanitizeServerError(str(error))}")


def _fnRegisterWorkflowCreate(app, dictCtx):
    """Register POST /api/workflows/{id}/create route."""

    @app.post("/api/workflows/{sContainerId}/create")
    async def fnCreateWorkflow(
        sContainerId: str, request: CreateWorkflowRequest
    ):
        dictCtx["require"]()
        _fnRejectDuplicateWorkflowName(
            dictCtx["docker"], sContainerId,
            request.sWorkflowName,
        )
        sRepoDirectory = _fsValidateRepoDirectory(
            dictCtx["docker"], sContainerId,
            request.sRepoDirectory,
        )
        sFileName = request.sFileName.strip()
        if not sFileName.endswith(".json"):
            sFileName += ".json"
        dictBlank = {
            "sWorkflowName": request.sWorkflowName,
            "sPlotDirectory": "Plot",
            "sFigureType": "pdf",
            "iNumberOfCores": -1,
            "listSteps": [],
        }
        sContent = json.dumps(dictBlank, indent=2) + "\n"
        sWorkflowDir = posixpath.join(
            sRepoDirectory,
            workflowManager.VAIBIFY_WORKFLOWS_DIR,
        )
        dictCtx["docker"].ftResultExecuteCommand(
            sContainerId,
            f"mkdir -p {fsShellQuote(sWorkflowDir)}",
        )
        sFullPath = posixpath.join(sWorkflowDir, sFileName)
        dictCtx["docker"].fnWriteFile(
            sContainerId, sFullPath,
            sContent.encode("utf-8"),
        )
        return {
            "sPath": sFullPath,
            "sName": request.sWorkflowName,
            "sSource": "vaibify",
        }


def _fnRegisterConnect(app, dictCtx):
    """Register POST /api/connect route."""

    @app.post("/api/connect/{sContainerId}")
    async def fnConnect(
        sContainerId: str,
        sWorkflowPath: Optional[str] = None,
    ):
        dictCtx["require"]()
        return fdictHandleConnect(
            dictCtx, sContainerId, sWorkflowPath)


def fnRegisterAll(app, dictCtx):
    """Register all workflow management routes."""
    _fnRegisterWorkflowSearch(app, dictCtx)
    _fnRegisterWorkflowCreate(app, dictCtx)
    _fnRegisterConnect(app, dictCtx)
