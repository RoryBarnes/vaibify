"""Draft persistence routes for the in-browser text editor.

The dashboard's text editor mirrors every keystroke to ``localStorage``
and, after a longer debounce, into a JSON blob on disk through these
endpoints. Drafts live under
``<sProjectRepoPath>/.vaibify/drafts/<workflowSlug>/`` so they
namespace by workflow exactly like test markers and survive container
restarts, browser crashes, and accidental tab closure.

Identity of a draft is the (sFilePath, sWorkdir) pair, hashed into a
flat filename by :mod:`vaibify.gui.draftManager`. Path validation
keeps user input from escaping the per-workflow draft directory.
"""

__all__ = ["fnRegisterAll"]

import posixpath

from fastapi import HTTPException
from pydantic import BaseModel
from typing import Optional

from ..actionCatalog import fnAgentAction
from .. import draftManager
from ..pipelineServer import (
    fnValidatePathWithinRoot,
    _fsSanitizeServerError,
)


class DraftWriteRequest(BaseModel):
    sContent: str
    sBaseHash: str = ""
    sWorkdir: str = ""


def _fsRequireProjectRepoAndWorkflowPath(dictCtx, sContainerId):
    """Return ``(sProjectRepoPath, sWorkflowPath)`` or raise HTTP 400.

    The workflow path lives in ``dictCtx["paths"]`` because the
    connect handler is the only place it's resolved authoritatively;
    the cached workflow dict does not carry it directly. The slug
    derivation in :mod:`vaibify.gui.draftManager` mirrors what
    ``fnCollectMarkerPathsByStep`` uses for test markers, so drafts
    namespace by the same workflow basename as markers.
    """
    dictWorkflow = dictCtx["workflows"].get(sContainerId)
    if not dictWorkflow:
        raise HTTPException(400, "Not connected to container")
    sProjectRepoPath = dictWorkflow.get("sProjectRepoPath", "")
    sWorkflowPath = dictCtx.get("paths", {}).get(sContainerId, "")
    if not sProjectRepoPath or not sWorkflowPath:
        raise HTTPException(
            400, "Active project lacks repository or project-file path",
        )
    return sProjectRepoPath, sWorkflowPath


def _fsResolveDraftFile(dictCtx, sContainerId, sFilePath, sWorkdir):
    """Return the absolute draft path inside the project repo.

    Validates that the computed draft path lives under the per-workflow
    draft directory. Raises HTTP 400 if no draft directory is available
    for the workflow.
    """
    sProjectRepoPath, sWorkflowPath = (
        _fsRequireProjectRepoAndWorkflowPath(dictCtx, sContainerId)
    )
    sDraftDir = draftManager.fsDraftDirectory(
        sProjectRepoPath, sWorkflowPath,
    )
    if not sDraftDir:
        raise HTTPException(400, "Cannot derive draft directory")
    sDraftPath = posixpath.join(
        sDraftDir, draftManager.fsDraftFilename(sFilePath, sWorkdir),
    )
    fnValidatePathWithinRoot(sDraftPath, sDraftDir)
    return sDraftDir, sDraftPath


def _fnEnsureDraftDir(dictCtx, sContainerId, sDraftDir):
    """Run ``mkdir -p`` for the draft directory inside the container."""
    sCommand = "mkdir -p " + _fsQuotePath(sDraftDir)
    iExitCode, sOutput = dictCtx["docker"].ftResultExecuteCommand(
        sContainerId, sCommand,
    )
    if iExitCode != 0:
        raise HTTPException(
            500,
            f"Cannot create draft directory: "
            f"{_fsSanitizeServerError(sOutput)}",
        )


def _fsQuotePath(sPath):
    """Single-quote a path for safe shell embedding."""
    return "'" + sPath.replace("'", "'\\''") + "'"


def _fnRejectOversize(sContent):
    """Cap the per-draft payload so a runaway write can't fill the disk."""
    iLength = len(sContent.encode("utf-8"))
    if iLength > draftManager.I_MAX_DRAFT_CONTENT_BYTES:
        raise HTTPException(
            413,
            f"Draft exceeds {draftManager.I_MAX_DRAFT_CONTENT_BYTES} bytes",
        )


def _fnRegisterDraftWrite(app, dictCtx):
    """Register PUT /api/draft/{sContainerId}/{sFilePath:path}."""

    @fnAgentAction("write-draft")
    @app.put("/api/draft/{sContainerId}/{sFilePath:path}")
    async def fnWriteDraft(
        sContainerId: str, sFilePath: str,
        request: DraftWriteRequest,
    ):
        dictCtx["require"]()
        _fnRejectOversize(request.sContent)
        sDraftDir, sDraftPath = _fsResolveDraftFile(
            dictCtx, sContainerId, sFilePath, request.sWorkdir,
        )
        _fnEnsureDraftDir(dictCtx, sContainerId, sDraftDir)
        sJsonPayload = draftManager.fjsonBuildDraftPayload(
            sFilePath, request.sWorkdir, request.sContent,
            request.sBaseHash,
        )
        try:
            dictCtx["docker"].fnWriteFile(
                sContainerId, sDraftPath,
                sJsonPayload.encode("utf-8"),
            )
        except Exception as error:
            raise HTTPException(
                500,
                f"Draft write failed: "
                f"{_fsSanitizeServerError(str(error))}",
            )
        return {"bSuccess": True, "sPath": sDraftPath}


def _fnRegisterDraftRead(app, dictCtx):
    """Register GET /api/draft/{sContainerId}/{sFilePath:path}."""

    @app.get("/api/draft/{sContainerId}/{sFilePath:path}")
    async def fnReadDraft(
        sContainerId: str, sFilePath: str,
        sWorkdir: str = "",
    ):
        dictCtx["require"]()
        _, sDraftPath = _fsResolveDraftFile(
            dictCtx, sContainerId, sFilePath, sWorkdir,
        )
        try:
            baBody = dictCtx["docker"].fbaFetchFile(
                sContainerId, sDraftPath,
            )
        except FileNotFoundError:
            return {"bExists": False}
        try:
            dictDraft = draftManager.fdictParseDraftPayload(
                baBody.decode("utf-8"),
            )
        except (ValueError, UnicodeDecodeError):
            return {"bExists": False, "sError": "corrupt-draft"}
        dictDraft["bExists"] = True
        return dictDraft


def _fnRegisterDraftDelete(app, dictCtx):
    """Register DELETE /api/draft/{sContainerId}/{sFilePath:path}."""

    @fnAgentAction("delete-draft")
    @app.delete("/api/draft/{sContainerId}/{sFilePath:path}")
    async def fnDeleteDraft(
        sContainerId: str, sFilePath: str,
        sWorkdir: str = "",
    ):
        dictCtx["require"]()
        _, sDraftPath = _fsResolveDraftFile(
            dictCtx, sContainerId, sFilePath, sWorkdir,
        )
        sCommand = "rm -f " + _fsQuotePath(sDraftPath)
        iExitCode, sOutput = dictCtx["docker"].ftResultExecuteCommand(
            sContainerId, sCommand,
        )
        if iExitCode != 0:
            raise HTTPException(
                500,
                f"Draft delete failed: "
                f"{_fsSanitizeServerError(sOutput)}",
            )
        return {"bSuccess": True}


def _fnRegisterDraftList(app, dictCtx):
    """Register GET /api/drafts/{sContainerId}."""

    @app.get("/api/drafts/{sContainerId}")
    async def fnListDrafts(sContainerId: str):
        dictCtx["require"]()
        sProjectRepoPath, sWorkflowPath = (
            _fsRequireProjectRepoAndWorkflowPath(dictCtx, sContainerId)
        )
        sDraftDir = draftManager.fsDraftDirectory(
            sProjectRepoPath, sWorkflowPath,
        )
        if not sDraftDir:
            return {"listDrafts": []}
        return _fdictListDraftsFromDir(dictCtx, sContainerId, sDraftDir)


def _fdictListDraftsFromDir(dictCtx, sContainerId, sDraftDir):
    """List drafts under ``sDraftDir`` as a JSON-friendly dict."""
    sCommand = (
        "find " + _fsQuotePath(sDraftDir) +
        " -maxdepth 1 -name '*.json' -type f 2>/dev/null"
    )
    iExitCode, sOutput = dictCtx["docker"].ftResultExecuteCommand(
        sContainerId, sCommand,
    )
    if iExitCode != 0:
        return {"listDrafts": []}
    listResults = []
    for sLine in sOutput.splitlines():
        sLine = sLine.strip()
        if not sLine:
            continue
        dictDraft = _fdictLoadOneDraft(dictCtx, sContainerId, sLine)
        if dictDraft is not None:
            listResults.append(dictDraft)
    return {"listDrafts": listResults}


def _fdictLoadOneDraft(dictCtx, sContainerId, sDraftPath):
    """Load and parse one draft file; return ``None`` on failure."""
    try:
        baBody = dictCtx["docker"].fbaFetchFile(
            sContainerId, sDraftPath,
        )
        return draftManager.fdictParseDraftPayload(
            baBody.decode("utf-8"),
        )
    except (FileNotFoundError, ValueError, UnicodeDecodeError):
        return None


def fnRegisterAll(app, dictCtx):
    """Register all draft persistence routes."""
    _fnRegisterDraftWrite(app, dictCtx)
    _fnRegisterDraftDelete(app, dictCtx)
    _fnRegisterDraftList(app, dictCtx)
    _fnRegisterDraftRead(app, dictCtx)
