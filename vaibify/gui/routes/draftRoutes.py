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

import hashlib
import posixpath

from fastapi import HTTPException, Request
from pydantic import BaseModel
from typing import Optional

from ..actionCatalog import ffnAgentAction
from .. import draftManager
from ..routeContext import (
    fdictRequireLaneTupleForCommit,
    fdictStampDockerIdForJournal,
    fsHashContainerFileOrEmpty,
)
from ..routeScope import (
    S_CARRIER_MODE_A_SYNCHRONOUS,
    ffnDeclareCarrierMode,
)
from ..pipelineServer import (
    fsValidatePathWithinRoot,
    _fsSanitizeServerError,
)


class DraftWriteRequest(BaseModel):
    sContent: str
    sBaseHash: str = ""
    sWorkdir: str = ""


def _ftRequireProjectRepoAndWorkflowPath(dictCtx, sContainerId):
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


def _ftResolveDraftFile(dictCtx, sContainerId, sFilePath, sWorkdir):
    """Return the absolute draft path inside the project repo.

    Validates that the computed draft path lives under the per-workflow
    draft directory. Raises HTTP 400 if no draft directory is available
    for the workflow.
    """
    sProjectRepoPath, sWorkflowPath = (
        _ftRequireProjectRepoAndWorkflowPath(dictCtx, sContainerId)
    )
    sDraftDir = draftManager.fsDraftDirectory(
        sProjectRepoPath, sWorkflowPath,
    )
    if not sDraftDir:
        raise HTTPException(400, "Cannot derive draft directory")
    sDraftPath = posixpath.join(
        sDraftDir, draftManager.fsDraftFilename(sFilePath, sWorkdir),
    )
    fsValidatePathWithinRoot(sDraftPath, sDraftDir)
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

    @ffnAgentAction("write-draft")
    @app.put("/api/draft/{sContainerId}/{sFilePath:path}")
    @ffnDeclareCarrierMode(S_CARRIER_MODE_A_SYNCHRONOUS)
    async def fdictWriteDraft(
        sContainerId: str, sFilePath: str,
        request: DraftWriteRequest, requestHttp: Request,
    ):
        dictCtx["require"]()
        _fnRejectOversize(request.sContent)
        sDraftDir, sDraftPath = _ftResolveDraftFile(
            dictCtx, sContainerId, sFilePath, request.sWorkdir,
        )
        sJsonPayload = draftManager.fsBuildDraftPayload(
            sFilePath, request.sWorkdir, request.sContent,
            request.sBaseHash,
        )
        _fnCommitDraftWrite(
            dictCtx, sContainerId, sDraftDir, sDraftPath,
            sJsonPayload.encode("utf-8"), requestHttp,
        )
        return {"bSuccess": True, "sPath": sDraftPath}


def _fnCommitDraftWrite(
    dictCtx, sContainerId, sDraftDir, sDraftPath, baPayload, requestHttp,
):
    """Commit the draft save through carrier mode (a) (design §8).

    One logical mutation, so one write-ahead record. Creating the
    per-workflow draft directory is a precondition of the write rather
    than an operation a researcher asks for on its own, and the
    record's ``file-write`` postcondition covers the pair: a draft file
    holding the intended bytes proves the ``mkdir`` ran AND the write
    landed. Both reach container primitives, so both must run inside
    the carrier's admission — the ``mkdir`` outside it is an arbitrary
    exec, which the gate refuses.
    """
    from .. import commitCarrier
    dictLaneTuple = fdictRequireLaneTupleForCommit(
        requestHttp, sContainerId, "The draft save",
    )
    sPriorSha256 = fsHashContainerFileOrEmpty(
        dictCtx, sContainerId, sDraftPath,
    )

    def fnWriteTheDraft():
        _fnEnsureDraftDir(dictCtx, sContainerId, sDraftDir)
        try:
            dictCtx["docker"].fnWriteFile(
                sContainerId, sDraftPath, baPayload,
            )
        except PermissionError:
            # A carrier refusal is the migration's only proof that a
            # mutation was carried; flattening it into a generic 500
            # would hide exactly what this boundary exists to surface.
            raise
        except Exception as error:
            raise HTTPException(
                500,
                f"Draft write failed: "
                f"{_fsSanitizeServerError(str(error))}",
            )

    commitCarrier.fdictCommitSynchronousMutation(
        requestHttp.app.state, dictLaneTuple["sContainerName"],
        sContainerId, dictLaneTuple, "file-write", sDraftPath,
        fnWriteTheDraft,
        {
            **fdictStampDockerIdForJournal(sContainerId),
            "sExpectedSha256": hashlib.sha256(baPayload).hexdigest(),
            "sPriorSha256": sPriorSha256,
        },
    )


def _fnRegisterDraftRead(app, dictCtx):
    """Register GET /api/draft/{sContainerId}/{sFilePath:path}."""

    @app.get("/api/draft/{sContainerId}/{sFilePath:path}")
    async def fdictReadDraft(
        sContainerId: str, sFilePath: str,
        sWorkdir: str = "",
    ):
        dictCtx["require"]()
        _, sDraftPath = _ftResolveDraftFile(
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

    @ffnAgentAction("delete-draft")
    @app.delete("/api/draft/{sContainerId}/{sFilePath:path}")
    @ffnDeclareCarrierMode(S_CARRIER_MODE_A_SYNCHRONOUS)
    async def fdictDeleteDraft(
        sContainerId: str, sFilePath: str, requestHttp: Request,
        sWorkdir: str = "",
    ):
        dictCtx["require"]()
        _, sDraftPath = _ftResolveDraftFile(
            dictCtx, sContainerId, sFilePath, sWorkdir,
        )
        _fnCommitDraftDelete(dictCtx, sContainerId, sDraftPath, requestHttp)
        return {"bSuccess": True}


def _fnCommitDraftDelete(dictCtx, sContainerId, sDraftPath, requestHttp):
    """Commit the draft removal through carrier mode (a) (design §8).

    A delete is a ``file-write`` whose intended content is nothing, so
    the expected hash is the empty string the journal uses for "this
    file does not exist" — and the prior hash is what the draft holds
    now. The two together make a crash inside the delete window
    provable in either direction: the file gone reads as landed, the
    file unchanged reads as never started.
    """
    from .. import commitCarrier
    dictLaneTuple = fdictRequireLaneTupleForCommit(
        requestHttp, sContainerId, "The draft delete",
    )
    sPriorSha256 = fsHashContainerFileOrEmpty(
        dictCtx, sContainerId, sDraftPath,
    )

    def fnRemoveTheDraft():
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

    commitCarrier.fdictCommitSynchronousMutation(
        requestHttp.app.state, dictLaneTuple["sContainerName"],
        sContainerId, dictLaneTuple, "file-write", sDraftPath,
        fnRemoveTheDraft,
        {
            **fdictStampDockerIdForJournal(sContainerId),
            "sExpectedSha256": "",
            "sPriorSha256": sPriorSha256,
        },
    )


def _fnRegisterDraftList(app, dictCtx):
    """Register GET /api/drafts/{sContainerId}."""

    @app.get("/api/drafts/{sContainerId}")
    async def fdictHandleListDrafts(sContainerId: str):
        dictCtx["require"]()
        sProjectRepoPath, sWorkflowPath = (
            _ftRequireProjectRepoAndWorkflowPath(dictCtx, sContainerId)
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
