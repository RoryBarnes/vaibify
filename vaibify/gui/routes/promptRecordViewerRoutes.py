"""Read-only routes that let a researcher review the Prompt Record.

The record's review gate asks the researcher to confirm what the
sanitizer produced before it counts. These routes are how they see it:
the captured sessions with their integrity, and one session at a time
as a paged conversation. They read only the redacted copies already in
the project repository, through typed reads, and change nothing.
"""

__all__ = ["fnRegisterAll"]

import asyncio
import posixpath

from fastapi import HTTPException

from .. import promptRecordManager, promptRecordViewer
from ..actionCatalog import ffnAgentAction
from ..pipelineServer import fdictRequireWorkflow
from ..routeContext import ffilesForWorkflow
from ..routeScope import S_CARRIER_TYPED_READ, ffnDeclareCarrierMode

I_DEFAULT_TURNS_PER_PAGE = 300
I_MAXIMUM_TURNS_PER_PAGE = 1000


def _ftRequireRecordFiles(dictCtx, sContainerId):
    """Return ``(dictWorkflow, filesRepo)`` or refuse with 400."""
    dictCtx["require"](sContainerId)
    dictWorkflow = fdictRequireWorkflow(dictCtx["workflows"], sContainerId)
    if not dictWorkflow.get("sProjectRepoPath"):
        raise HTTPException(400, "This workflow has no project repository.")
    return dictWorkflow, ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow)


def _fdictDescribeSessions(dictWorkflow, filesRepo):
    """Return the session list with the record's integrity and coverage."""
    dictIndex = promptRecordManager.fdictLoadIndex(filesRepo)
    dictRecord = (
        (dictWorkflow.get("dictAiProvenance") or {})
        .get("dictPromptRecord") or {}
    )
    return {
        "listSessions": promptRecordManager.flistSummarizeSessions(
            dictIndex,
        ),
        "bChainIntact": promptRecordManager.fbVerifyCaptureChain(dictIndex),
        "listTamperedSessions": promptRecordManager.flistVerifyCapturedFiles(
            filesRepo, dictIndex,
        ),
        "listCoverageIntervals": dictIndex["listCoverageIntervals"],
        "iSessionsOutsideProject": dictIndex["iSessionsOutsideProject"],
        "bEnabled": dictRecord.get("bEnabled") is True,
        "bFirstCaptureReviewed":
            dictRecord.get("bFirstCaptureReviewed") is True,
    }


def _fnRegisterListSessions(app, dictCtx):
    """Register GET .../prompt-record/sessions."""

    @ffnAgentAction("list-prompt-record-sessions")
    @app.get("/api/workflow/{sContainerId}/prompt-record/sessions")
    @ffnDeclareCarrierMode(S_CARRIER_TYPED_READ)
    async def fdictListPromptRecordSessions(sContainerId: str):
        dictWorkflow, filesRepo = _ftRequireRecordFiles(dictCtx, sContainerId)
        return await asyncio.to_thread(
            _fdictDescribeSessions, dictWorkflow, filesRepo,
        )


def _fdictReadSessionPage(filesRepo, sSessionFileName, iOffset, iLimit):
    """Return one page of a captured session's turns and its summary.

    The name must be one the capture index records: a request can name
    a session, never a path, so nothing outside the record is readable.
    """
    dictIndex = promptRecordManager.fdictLoadIndex(filesRepo)
    setNames = {
        dictSession["sSessionFileName"]
        for dictSession in promptRecordManager.flistSummarizeSessions(
            dictIndex,
        )
    }
    if sSessionFileName not in setNames:
        raise HTTPException(404, "No such captured session.")
    sText = filesRepo.fsReadText(posixpath.join(
        promptRecordManager.S_PROMPT_RECORD_SESSIONS_DIRECTORY,
        sSessionFileName,
    ))
    listTurns = promptRecordViewer.flistParseTranscriptTurns(sText)
    dictPage = promptRecordViewer.fdictSummarizeTurns(sText, listTurns)
    dictPage.update({
        "sSessionFileName": sSessionFileName,
        "iOffset": iOffset,
        "listTurns": listTurns[iOffset:iOffset + iLimit],
    })
    return dictPage


def _fnRegisterReadSession(app, dictCtx):
    """Register GET .../prompt-record/sessions/{sSessionFileName}."""

    @ffnAgentAction("read-prompt-record-session")
    @app.get(
        "/api/workflow/{sContainerId}/prompt-record/sessions/"
        "{sSessionFileName}"
    )
    @ffnDeclareCarrierMode(S_CARRIER_TYPED_READ)
    async def fdictReadPromptRecordSession(
        sContainerId: str, sSessionFileName: str,
        iOffset: int = 0, iLimit: int = I_DEFAULT_TURNS_PER_PAGE,
    ):
        _dictWorkflow, filesRepo = _ftRequireRecordFiles(
            dictCtx, sContainerId,
        )
        if iOffset < 0 or not 1 <= iLimit <= I_MAXIMUM_TURNS_PER_PAGE:
            raise HTTPException(400, "Page out of range.")
        return await asyncio.to_thread(
            _fdictReadSessionPage, filesRepo, sSessionFileName,
            iOffset, iLimit,
        )


def fnRegisterAll(app, dictCtx):
    """Register the Prompt Record viewer's read-only routes."""
    _fnRegisterListSessions(app, dictCtx)
    _fnRegisterReadSession(app, dictCtx)
