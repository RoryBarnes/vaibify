"""HTTP routes for the Replay axis: AI-model declarations + context.

The Replay axis records the provenance of the development process.
Phase one is the model declaration: every AI model used on the project
is declared with vendor, model identifier, and date range of use —
open-weights models additionally declare their weights source and
revision hash. Undeclared is the only failing state of the criterion.

Declarations live in ``dictWorkflow["dictAiProvenance"]`` (see
:mod:`vaibify.reproducibility.replayGate`), validated here at the
write routes like every other project-scope declaration block.

The project-context routes manage ``<repo>/.vaibify/AGENTS.md`` — the
researcher's standing instructions to the in-container agent. The
path is fixed server-side, so the generic file route's ``.vaibify``
write denylist stays fully intact; these are dedicated endpoints, not
a carve-out. The host-import route is intentionally excluded from the
agent-action catalog: an agent-invokable host read would let a
compromised in-container agent exfiltrate home-directory files into a
public repository.
"""

__all__ = ["fnRegisterAll"]

import posixpath
from datetime import datetime, timezone

from fastapi import HTTPException

from ..actionCatalog import fnAgentAction
from ..pipelineServer import fdictRequireWorkflow
from ..projectContextManager import (
    I_MAX_CONTEXT_CONTENT_BYTES,
    S_CONTEXT_TEMPLATE,
    S_PROJECT_CONTEXT_RELATIVE_PATH,
    fsReadHostImportFile,
)
from ...reproducibility.replayGate import (
    S_AI_PROVENANCE_KEY,
    S_DECLARED_MODELS_KEY,
    flistDescribeModelDeclarationGaps,
)


_LIST_MODEL_FIELDS = [
    "sVendor", "sModelId", "sUseStartDate", "sUseEndDate",
    "bOpenWeights", "sWeightsSource", "sWeightsRevisionHash",
]
_LIST_DATE_FIELDS = ["sUseStartDate", "sUseEndDate"]


def _fbDateIsIsoFormat(sDate):
    """Return True iff the value parses as a YYYY-MM-DD date."""
    try:
        datetime.strptime(str(sDate), "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _fdictValidateModelBody(request):
    """Return the sanitized model declaration or raise HTTP 400."""
    if not isinstance(request, dict):
        raise HTTPException(400, "Model declaration must be an object.")
    dictModel = {
        sField: request[sField]
        for sField in _LIST_MODEL_FIELDS
        if sField in request
    }
    listGaps = flistDescribeModelDeclarationGaps(dictModel)
    if listGaps:
        raise HTTPException(
            400, "Model declaration is missing: " + ", ".join(listGaps),
        )
    for sField in _LIST_DATE_FIELDS:
        if not _fbDateIsIsoFormat(dictModel.get(sField)):
            raise HTTPException(
                400, f"{sField} must be a YYYY-MM-DD date.",
            )
    return dictModel


def _flistUpsertModel(listModels, dictModel):
    """Replace the (vendor, model id) entry or append a new one."""
    tKey = (dictModel.get("sVendor"), dictModel.get("sModelId"))
    listUpdated = [
        dictExisting
        for dictExisting in listModels
        if (dictExisting.get("sVendor"), dictExisting.get("sModelId")) != tKey
    ]
    listUpdated.append(dictModel)
    return listUpdated


def _fdictProvenanceOf(dictWorkflow):
    """Return the workflow's mutable AI-provenance block, creating it."""
    dictProvenance = dict(dictWorkflow.get(S_AI_PROVENANCE_KEY) or {})
    dictWorkflow[S_AI_PROVENANCE_KEY] = dictProvenance
    return dictProvenance


def _fnRegisterDeclareAiModel(app, dictCtx):
    """Register POST /api/workflow/{sContainerId}/ai-models/declare."""

    @fnAgentAction("declare-ai-model")
    @app.post("/api/workflow/{sContainerId}/ai-models/declare")
    async def fnDeclareAiModel(sContainerId: str, request: dict):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        dictModel = _fdictValidateModelBody(request)
        dictProvenance = _fdictProvenanceOf(dictWorkflow)
        dictProvenance[S_DECLARED_MODELS_KEY] = _flistUpsertModel(
            list(dictProvenance.get(S_DECLARED_MODELS_KEY) or []),
            dictModel,
        )
        dictCtx["save"](sContainerId, dictWorkflow)
        return {
            "listDeclaredModels": dictProvenance[S_DECLARED_MODELS_KEY],
        }


def _fnRegisterRemoveAiModel(app, dictCtx):
    """Register POST /api/workflow/{sContainerId}/ai-models/remove."""

    @fnAgentAction("remove-ai-model")
    @app.post("/api/workflow/{sContainerId}/ai-models/remove")
    async def fnRemoveAiModel(sContainerId: str, request: dict):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        tKey = (request.get("sVendor"), request.get("sModelId"))
        dictProvenance = _fdictProvenanceOf(dictWorkflow)
        listModels = list(dictProvenance.get(S_DECLARED_MODELS_KEY) or [])
        listRemaining = [
            dictModel
            for dictModel in listModels
            if (dictModel.get("sVendor"), dictModel.get("sModelId")) != tKey
        ]
        if len(listRemaining) == len(listModels):
            raise HTTPException(404, "No such declared model.")
        dictProvenance[S_DECLARED_MODELS_KEY] = listRemaining
        dictCtx["save"](sContainerId, dictWorkflow)
        return {"listDeclaredModels": listRemaining}


def _fsContextAbsolutePath(dictWorkflow):
    """Return the container-absolute context path or raise HTTP 400."""
    sProjectRepoPath = dictWorkflow.get("sProjectRepoPath") or ""
    if not sProjectRepoPath:
        raise HTTPException(
            400, "This workflow has no project repository.",
        )
    return posixpath.join(
        sProjectRepoPath, S_PROJECT_CONTEXT_RELATIVE_PATH,
    )


def _fsFetchContextOrNone(dictCtx, sContainerId, sAbsPath):
    """Return the context file text, or ``None`` when absent."""
    try:
        baContent = dictCtx["docker"].fbaFetchFile(
            sContainerId, sAbsPath,
        )
    except Exception:  # noqa: BLE001 — absent file, unreachable exec
        return None
    return baContent.decode("utf-8", errors="replace")


def _fnWriteContextFile(dictCtx, sContainerId, sAbsPath, sContent):
    """Write the context file with the container-user ownership default."""
    dictCtx["docker"].fnWriteFile(
        sContainerId, sAbsPath, sContent.encode("utf-8"),
    )


def _fnRequireContentWithinCap(sContent):
    """Raise HTTP 413 when the content exceeds the context size cap."""
    if len(sContent.encode("utf-8")) > I_MAX_CONTEXT_CONTENT_BYTES:
        raise HTTPException(
            413, "Context content exceeds the 256 KiB cap.",
        )


def _fnRegisterReadProjectContext(app, dictCtx):
    """Register GET /api/workflow/{sContainerId}/project-context."""

    @fnAgentAction("read-project-context")
    @app.get("/api/workflow/{sContainerId}/project-context")
    async def fnReadProjectContext(sContainerId: str):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        sAbsPath = _fsContextAbsolutePath(dictWorkflow)
        sContent = _fsFetchContextOrNone(dictCtx, sContainerId, sAbsPath)
        return {
            "bExists": sContent is not None,
            "sContent": sContent or "",
            "sRelativePath": S_PROJECT_CONTEXT_RELATIVE_PATH,
        }


def _fnRegisterUpdateProjectContext(app, dictCtx):
    """Register PUT /api/workflow/{sContainerId}/project-context."""

    @fnAgentAction("update-project-context")
    @app.put("/api/workflow/{sContainerId}/project-context")
    async def fnUpdateProjectContext(sContainerId: str, request: dict):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        sContent = str(request.get("sContent") or "")
        _fnRequireContentWithinCap(sContent)
        sAbsPath = _fsContextAbsolutePath(dictWorkflow)
        _fnWriteContextFile(dictCtx, sContainerId, sAbsPath, sContent)
        from ..routeContext import fnRecordAttributionEvent
        fnRecordAttributionEvent(
            dictCtx, sContainerId, dictWorkflow,
            "project-context", "update-project-context",
        )
        return {"bOk": True}


def _fnRegisterContextTemplate(app, dictCtx):
    """Register POST .../project-context/template (409 if it exists)."""

    @fnAgentAction("generate-project-context-template")
    @app.post("/api/workflow/{sContainerId}/project-context/template")
    async def fnGenerateContextTemplate(sContainerId: str):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        sAbsPath = _fsContextAbsolutePath(dictWorkflow)
        if _fsFetchContextOrNone(
            dictCtx, sContainerId, sAbsPath,
        ) is not None:
            raise HTTPException(
                409, "A project context file already exists.",
            )
        _fnWriteContextFile(
            dictCtx, sContainerId, sAbsPath, S_CONTEXT_TEMPLATE,
        )
        return {"bOk": True}


_SET_ADOPTABLE_ROOT_BASENAMES = frozenset({"CLAUDE.md", "AGENTS.md"})


def _fsResolveImportContent(dictCtx, sContainerId, dictWorkflow, request):
    """Return the imported content from the host or the repo root."""
    if request.get("bAdoptRepoRoot") is True:
        sBasename = str(request.get("sRootBasename") or "")
        if sBasename not in _SET_ADOPTABLE_ROOT_BASENAMES:
            raise HTTPException(
                400, "sRootBasename must be CLAUDE.md or AGENTS.md.",
            )
        sRootPath = posixpath.join(
            dictWorkflow.get("sProjectRepoPath") or "", sBasename,
        )
        sContent = _fsFetchContextOrNone(
            dictCtx, sContainerId, sRootPath,
        )
        if sContent is None:
            raise HTTPException(404, f"No {sBasename} at the repo root.")
        return sContent
    try:
        return fsReadHostImportFile(str(request.get("sHostPath") or ""))
    except ValueError as error:
        raise HTTPException(400, str(error))


def _fnReplaceRootWithSymlink(dictCtx, sContainerId, dictWorkflow, request):
    """After adopting a root file, point it at the canonical context.

    One source of truth: the adopted root file becomes a symlink to
    ``.vaibify/AGENTS.md`` so future edits cannot diverge. A failed
    replacement is surfaced, never silently ignored.
    """
    if request.get("bAdoptRepoRoot") is not True:
        return
    from ..pipelineRunner import fsShellQuote
    sRepo = dictWorkflow.get("sProjectRepoPath") or ""
    sBasename = str(request.get("sRootBasename") or "")
    sCommand = (
        "cd " + fsShellQuote(sRepo)
        + " && rm -f " + fsShellQuote(sBasename)
        + " && ln -s "
        + fsShellQuote(S_PROJECT_CONTEXT_RELATIVE_PATH)
        + " " + fsShellQuote(sBasename)
    )
    resultExec = dictCtx["docker"].texecRunInContainerStreamed(
        sContainerId, sCommand,
    )
    if resultExec.iExitCode != 0:
        raise HTTPException(
            500, "Adopted the content, but replacing the root file "
            "with a symlink failed: " + resultExec.sStderr,
        )


def _fnRegisterContextImport(app, dictCtx):
    """Register POST .../project-context/import (researcher-only).

    Excluded from the agent-action catalog: it reads the HOST
    filesystem, and an agent-invokable host read would let a
    compromised in-container agent pull arbitrary home-directory
    files into a public repository.
    """

    @app.post("/api/workflow/{sContainerId}/project-context/import")
    async def fnImportProjectContext(sContainerId: str, request: dict):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        sAbsPath = _fsContextAbsolutePath(dictWorkflow)
        sExisting = _fsFetchContextOrNone(
            dictCtx, sContainerId, sAbsPath,
        )
        if sExisting and request.get("bOverwrite") is not True:
            raise HTTPException(
                409, "A project context file already exists; pass "
                "bOverwrite to replace it.",
            )
        sContent = _fsResolveImportContent(
            dictCtx, sContainerId, dictWorkflow, request,
        )
        _fnRequireContentWithinCap(sContent)
        _fnWriteContextFile(dictCtx, sContainerId, sAbsPath, sContent)
        _fnReplaceRootWithSymlink(
            dictCtx, sContainerId, dictWorkflow, request,
        )
        return {"bOk": True}


def _fdictPromptRecordOf(dictWorkflow):
    """Return the workflow's mutable Prompt Record config block."""
    dictProvenance = _fdictProvenanceOf(dictWorkflow)
    dictRecord = dict(dictProvenance.get("dictPromptRecord") or {})
    dictProvenance["dictPromptRecord"] = dictRecord
    return dictRecord


def _flistGatherSessionSecrets(dictCtx, sContainerId):
    """Collect every vaibify session secret for exact-value redaction.

    The hub session token plus every value in the container's session
    env file (which carries the per-container agent token). A missing
    env file yields just the hub token — capture must not fail open
    by skipping redaction entirely.
    """
    listSecrets = [str(dictCtx.get("sSessionToken") or "")]
    from ..actionCatalog import S_SESSION_ENV_PATH
    try:
        baEnv = dictCtx["docker"].fbaFetchFile(
            sContainerId, S_SESSION_ENV_PATH,
        )
    except Exception:  # noqa: BLE001 — env absent when disconnected
        return [sSecret for sSecret in listSecrets if sSecret]
    for sLine in baEnv.decode("utf-8", errors="replace").splitlines():
        if "=" in sLine:
            listSecrets.append(sLine.split("=", 1)[1].strip())
    return [sSecret for sSecret in listSecrets if sSecret]


def _fnRegisterPromptRecordConfigure(app, dictCtx):
    """Register POST .../prompt-record/configure."""

    @fnAgentAction("configure-prompt-record")
    @app.post("/api/workflow/{sContainerId}/prompt-record/configure")
    async def fnConfigurePromptRecord(sContainerId: str, request: dict):
        # Late-bound so an install of vaibify[replay] (or a test
        # patch) takes effect without restarting the hub.
        from .. import transcriptSanitizer
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        bEnabled = request.get("bEnabled") is True
        if bEnabled and not transcriptSanitizer.fbSanitizerAvailable():
            raise HTTPException(
                409, "Transcript capture needs the detect-secrets "
                "scanner: install vaibify[replay] on the host, then "
                "enable again.",
            )
        dictRecord = _fdictPromptRecordOf(dictWorkflow)
        dictRecord["bEnabled"] = bEnabled
        if bEnabled and not dictRecord.get("sEnabledAtUtc"):
            dictRecord["sEnabledAtUtc"] = datetime.now(
                timezone.utc,
            ).isoformat()
        dictRecord.setdefault("bFirstCaptureReviewed", False)
        dictCtx["save"](sContainerId, dictWorkflow)
        return {"dictPromptRecord": dictRecord}


def _fnRegisterPromptRecordCapture(app, dictCtx):
    """Register POST .../prompt-record/capture (one capture pass)."""
    import asyncio
    from .. import promptRecordManager
    from ..routeContext import ffilesForWorkflow

    @fnAgentAction("capture-prompt-record")
    @app.post("/api/workflow/{sContainerId}/prompt-record/capture")
    async def fnCapturePromptRecord(sContainerId: str):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        dictRecord = _fdictPromptRecordOf(dictWorkflow)
        if dictRecord.get("bEnabled") is not True:
            raise HTTPException(409, "The Prompt Record is not enabled.")
        _fsContextAbsolutePath(dictWorkflow)
        filesRepo = ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow)
        dictSummary = await asyncio.to_thread(
            promptRecordManager.fdictRunCapturePass,
            dictCtx["docker"], sContainerId, filesRepo,
            _flistGatherSessionSecrets(dictCtx, sContainerId),
        )
        dictSummary["bPendingReview"] = (
            dictRecord.get("bFirstCaptureReviewed") is not True
        )
        return dictSummary


def _fnRegisterPromptRecordApprove(app, dictCtx):
    """Register POST .../prompt-record/approve-first-capture.

    Excluded from the agent catalog: the review gate exists so a
    human confirms what the sanitizer produced before it is treated
    as publishable — the agent must never approve publication of its
    own transcript.
    """

    @app.post(
        "/api/workflow/{sContainerId}/prompt-record/"
        "approve-first-capture"
    )
    async def fnApproveFirstCapture(sContainerId: str):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        dictRecord = _fdictPromptRecordOf(dictWorkflow)
        if dictRecord.get("bEnabled") is not True:
            raise HTTPException(409, "The Prompt Record is not enabled.")
        dictRecord["bFirstCaptureReviewed"] = True
        dictCtx["save"](sContainerId, dictWorkflow)
        return {"dictPromptRecord": dictRecord}


def _fnRegisterPromptRecordStatus(app, dictCtx):
    """Register GET .../prompt-record/status."""
    from .. import promptRecordManager
    from ..routeContext import ffilesForWorkflow

    @fnAgentAction("view-prompt-record-status")
    @app.get("/api/workflow/{sContainerId}/prompt-record/status")
    async def fnPromptRecordStatus(sContainerId: str):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        _fsContextAbsolutePath(dictWorkflow)
        filesRepo = ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow)
        dictIndex = promptRecordManager.fdictLoadIndex(filesRepo)
        dictRecord = _fdictPromptRecordOf(dictWorkflow)
        from .. import attributionLog
        listFlags = attributionLog.flistLoadFlags(filesRepo)
        dictProvenance = _fdictProvenanceOf(dictWorkflow)
        return {
            "dictPromptRecord": dictRecord,
            "listCaptures": dictIndex["listCaptures"],
            "listCoverageIntervals": dictIndex["listCoverageIntervals"],
            "bChainIntact": promptRecordManager.fbVerifyCaptureChain(
                dictIndex,
            ),
            "listTamperedSessions":
                promptRecordManager.flistVerifyCapturedFiles(
                    filesRepo, dictIndex,
                ),
            "sReviewSample": _fsReviewSample(filesRepo, dictIndex),
            "dictSupervision": dict(
                dictProvenance.get("dictSupervision") or {},
            ),
            "listSupervisionFlags": listFlags,
            "bFlagChainIntact": attributionLog.fbVerifyFlagChain(
                listFlags,
            ),
        }


def _fsReviewSample(filesRepo, dictIndex):
    """Return the head of the most recent sanitized session, or ''."""
    from ..promptRecordManager import (
        S_PROMPT_RECORD_SESSIONS_DIRECTORY,
    )
    listCaptures = dictIndex.get("listCaptures") or []
    if not listCaptures:
        return ""
    sRelPath = posixpath.join(
        S_PROMPT_RECORD_SESSIONS_DIRECTORY,
        listCaptures[-1]["sSessionFileName"],
    )
    try:
        sText = filesRepo.fsReadText(sRelPath)
    except (OSError, FileNotFoundError):
        return ""
    return "\n".join(sText.split("\n")[:40])


def _fnRegisterSupervisionConfigure(app, dictCtx):
    """Register POST .../supervision/configure.

    Excluded from the agent catalog: the supervised party must never
    switch its own supervision on or off. Requires the Prompt Record
    to be enabled and reviewed first — Supervised is the rung above
    Recorded, not a parallel toggle.
    """

    @app.post("/api/workflow/{sContainerId}/supervision/configure")
    async def fnConfigureSupervision(sContainerId: str, request: dict):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        bEnabled = request.get("bEnabled") is True
        dictRecord = _fdictPromptRecordOf(dictWorkflow)
        if bEnabled and not (
            dictRecord.get("bEnabled") is True
            and dictRecord.get("bFirstCaptureReviewed") is True
        ):
            raise HTTPException(
                409, "Supervised mode requires the Prompt Record to "
                "be enabled and its first capture reviewed.",
            )
        dictProvenance = _fdictProvenanceOf(dictWorkflow)
        dictSupervision = dict(
            dictProvenance.get("dictSupervision") or {},
        )
        dictSupervision["bEnabled"] = bEnabled
        if bEnabled and not dictSupervision.get("sEnabledAtUtc"):
            dictSupervision["sEnabledAtUtc"] = datetime.now(
                timezone.utc,
            ).isoformat()
        dictProvenance["dictSupervision"] = dictSupervision
        dictCtx["save"](sContainerId, dictWorkflow)
        return {"dictSupervision": dictSupervision}


def fnRegisterAll(app, dictCtx):
    """Register all Replay-axis routes."""
    _fnRegisterDeclareAiModel(app, dictCtx)
    _fnRegisterRemoveAiModel(app, dictCtx)
    _fnRegisterReadProjectContext(app, dictCtx)
    _fnRegisterUpdateProjectContext(app, dictCtx)
    _fnRegisterContextTemplate(app, dictCtx)
    _fnRegisterContextImport(app, dictCtx)
    _fnRegisterPromptRecordConfigure(app, dictCtx)
    _fnRegisterPromptRecordCapture(app, dictCtx)
    _fnRegisterPromptRecordApprove(app, dictCtx)
    _fnRegisterPromptRecordStatus(app, dictCtx)
    _fnRegisterSupervisionConfigure(app, dictCtx)
