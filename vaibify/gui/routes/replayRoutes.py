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

The personal-layer routes account for the researcher's private
host-side agent configuration (instruction stack layer 4). The
declaration records one of three statuses — ``none``,
``declared-private``, ``included`` — and, for ``declared-private``,
optional hash commitments: {sLabel, sSha256, iByteCount,
sDeclaredIso} computed from a host file whose path is NEVER
persisted, logged, or echoed. The hash route is browser-only: it is
excluded from the agent catalog AND rejects the agent token lane
outright, because an agent-reachable variant would be a hash oracle
over host files.
"""

__all__ = ["fnRegisterAll"]

import posixpath
from datetime import datetime, timezone

from fastapi import HTTPException, Request

from ..actionCatalog import fnAgentAction
from ..routeContext import (
    fdictCarryARefusalBackInsteadOfRaising,
    fdictRequireLaneTupleForCommit,
    fnCommitWorkflowSave,
    fnRejectAgentTokenLane,
    fobjRunWorkerUnderTheDrain,
    fsHashContainerFileOrEmpty,
)
from ..routeScope import (
    S_CARRIER_MODE_A_SYNCHRONOUS,
    S_CARRIER_MODE_B_LOCK_HELD,
    S_CARRIER_SEPARATE_AUTHORITY,
    fnDeclareCarrierMode,
)
from ..personalLayerManager import (
    fdictComputeHashCommitment,
    fdictValidateHashCommitment,
    flistValidateIncludedPaths,
)
from ..pipelineServer import fdictRequireWorkflow
from ...config.mutationAdmission import ControlPlaneRefusalError
from ..projectContextManager import (
    I_MAX_CONTEXT_CONTENT_BYTES,
    S_CONTEXT_TEMPLATE,
    S_PROJECT_CONTEXT_RELATIVE_PATH,
    fsReadHostImportFile,
)
from ...reproducibility.replayGate import (
    S_AI_PROVENANCE_KEY,
    S_DECLARED_MODELS_KEY,
    S_PERSONAL_LAYER_KEY,
    SET_PERSONAL_LAYER_STATUSES,
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
    @fnDeclareCarrierMode(S_CARRIER_MODE_A_SYNCHRONOUS)
    async def fnDeclareAiModel(
        sContainerId: str, request: dict, requestHttp: Request,
    ):
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
        fnCommitWorkflowSave(
            dictCtx, sContainerId, dictWorkflow, requestHttp,
            "The AI-model declaration",
        )
        return {
            "listDeclaredModels": dictProvenance[S_DECLARED_MODELS_KEY],
        }


def _fnRegisterRemoveAiModel(app, dictCtx):
    """Register POST /api/workflow/{sContainerId}/ai-models/remove."""

    @fnAgentAction("remove-ai-model")
    @app.post("/api/workflow/{sContainerId}/ai-models/remove")
    @fnDeclareCarrierMode(S_CARRIER_MODE_A_SYNCHRONOUS)
    async def fnRemoveAiModel(
        sContainerId: str, request: dict, requestHttp: Request,
    ):
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
        fnCommitWorkflowSave(
            dictCtx, sContainerId, dictWorkflow, requestHttp,
            "The AI-model removal",
        )
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


def _fnCommitContextWrite(
    dictCtx, sContainerId, sAbsPath, sContent, requestHttp,
    sOperationName,
):
    """Commit one context-file write through carrier mode (a).

    Mode (a) because this is a single write with a hash the journal can
    adjudicate afterwards: the expected sha256 IS the sha256 of the
    bytes about to be written, so a crash inside the commit window
    resolves to "landed" or "did not" rather than to a quarantine.

    Deliberately NOT routed through ``fnCommitWorkflowSave``: that
    helper's target is project.json and its expected hash is the
    workflow's fingerprint, so reusing it would hand the probe a hash
    belonging to a different file.
    """
    from .. import commitCarrier
    import hashlib
    dictLaneTuple = fdictRequireLaneTupleForCommit(
        requestHttp, sContainerId, sOperationName,
    )
    return commitCarrier.fdictCommitSynchronousMutation(
        requestHttp.app.state, dictLaneTuple["sContainerName"],
        sContainerId, dictLaneTuple, "file-write", sAbsPath,
        lambda: _fnWriteContextFile(
            dictCtx, sContainerId, sAbsPath, sContent,
        ),
        {
            "sDockerContainerId": sContainerId,
            "sExpectedSha256": hashlib.sha256(
                sContent.encode("utf-8"),
            ).hexdigest(),
            "sPriorSha256": fsHashContainerFileOrEmpty(
                dictCtx, sContainerId, sAbsPath,
            ),
        },
    )


# Mirrors COMPOSED_CONTEXT_BASENAME / COMPOSED_CONTEXT_SEPARATOR in
# vaibify/containerImage/entrypoint.sh. The container composes this
# file at startup and the host recomposes it on save, so both spellings
# must agree; testComposedContextSeparatorMatchesTheHost fails if they
# drift. Duplicated rather than shared for the reason
# introspectionScript.py duplicates dataLoaders.py -- a container
# script cannot import from the host environment.
S_COMPOSED_CONTEXT_RELATIVE_PATH = ".vaibify/agentContext.md"
S_COMPOSED_CONTEXT_SEPARATOR = """

---

# Project Context (authored by the researcher)

The section below is the researcher's own standing instructions for
this repository, kept in `.vaibify/AGENTS.md`. Where it conflicts with
anything above, it wins. That file is the authoritative copy: if it has
been edited since this container started, read it directly."""


def _fnRecomposeAgentContext(
    dictCtx, sContainerId, dictWorkflow, sContent, requestHttp,
):
    """Rewrite the composed agent context so a save is not stale.

    The repo-root name every provider reads is a symlink onto the
    composed file, which the entrypoint builds at container start. A
    context saved mid-session would therefore not reach any agent
    until the container restarted -- the researcher's own instructions
    silently lagging is worse than the craft guidance being late.

    Best-effort by design: the researcher's file is already committed
    by the caller, so failing here must not fail their save. The
    composed file is regenerated at every container start, so the worst
    case is the staleness this exists to shorten, not a lost edit.
    """
    sWorkspaceContext = _fsFetchContextOrNone(
        dictCtx, sContainerId, "/workspace/CLAUDE.md",
    )
    if sWorkspaceContext is None:
        return
    sComposed = sWorkspaceContext + S_COMPOSED_CONTEXT_SEPARATOR + (
        "\n\n" + sContent
    )
    sComposedPath = posixpath.join(
        dictWorkflow.get("sProjectRepoPath") or "",
        S_COMPOSED_CONTEXT_RELATIVE_PATH,
    )
    try:
        _fnCommitContextWrite(
            dictCtx, sContainerId, sComposedPath, sComposed,
            requestHttp, "The composed agent-context refresh",
        )
    except ControlPlaneRefusalError:
        raise
    except Exception:  # noqa: BLE001 — see the best-effort note above
        return


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
    @fnDeclareCarrierMode(S_CARRIER_MODE_A_SYNCHRONOUS)
    async def fnUpdateProjectContext(
        sContainerId: str, request: dict, requestHttp: Request,
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        sContent = str(request.get("sContent") or "")
        _fnRequireContentWithinCap(sContent)
        sAbsPath = _fsContextAbsolutePath(dictWorkflow)
        _fnCommitContextWrite(
            dictCtx, sContainerId, sAbsPath, sContent, requestHttp,
            "The project-context update",
        )
        _fnRecomposeAgentContext(
            dictCtx, sContainerId, dictWorkflow, sContent, requestHttp,
        )
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
    @fnDeclareCarrierMode(S_CARRIER_MODE_B_LOCK_HELD)
    async def fnGenerateContextTemplate(
        sContainerId: str, requestHttp: Request,
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        sAbsPath = _fsContextAbsolutePath(dictWorkflow)
        await _fnWriteTheTemplateUnderTheDrain(
            dictCtx, sContainerId, sAbsPath, requestHttp,
        )
        return {"bOk": True}


async def _fnWriteTheTemplateUnderTheDrain(
    dictCtx, sContainerId, sAbsPath, requestHttp,
):
    """Probe for an existing context file and write the template as one.

    Mode (b) rather than (a) because the probe and the write must share
    ONE held drain. The probe IS the guard -- the whole contract of this
    route is "create it only if it is not there" -- so with the drain
    dropped between them two sessions both read "absent" and the second
    silently overwrites the first researcher's freshly written context.
    Mode (a) could hold them together too, but it runs its effect on the
    event loop, and the probe is a container round-trip.

    The 409 is carried back rather than raised out of the worker.
    Nothing has been written when it fires, so there is nothing to
    reconcile, and a raise would quarantine the container for the
    ordinary case of asking twice.
    """
    def fnProbeThenWrite():
        if _fsFetchContextOrNone(
            dictCtx, sContainerId, sAbsPath,
        ) is not None:
            raise HTTPException(
                409, "A project context file already exists.",
            )
        _fnWriteContextFile(
            dictCtx, sContainerId, sAbsPath, S_CONTEXT_TEMPLATE,
        )

    def fnWriteTheTemplate(supervisor=None):
        del supervisor
        return fdictCarryARefusalBackInsteadOfRaising(fnProbeThenWrite)

    await fobjRunWorkerUnderTheDrain(
        sContainerId, fnWriteTheTemplate, "project-context-template",
        requestHttp,
    )


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
    files into a public repository — the imported file lands at
    ``.vaibify/AGENTS.md``, which is readable through the agent-safe
    read-project-context action and pushable through the agent-safe
    push-to-github one.

    Catalog exclusion is metadata, not a gate, so the route rejects
    the agent token lane itself, exactly as the personal-layer hash
    route does. A docstring promising unreachability with no
    enforcement point is how this stayed open.
    """

    @app.post("/api/workflow/{sContainerId}/project-context/import")
    @fnDeclareCarrierMode(S_CARRIER_MODE_B_LOCK_HELD)
    async def fnImportProjectContext(
        sContainerId: str, request: dict, requestHttp: Request,
    ):
        fnRejectAgentTokenLane(requestHttp)
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        await _fnImportTheContextUnderTheDrain(
            dictCtx, sContainerId, dictWorkflow, request, requestHttp,
        )
        return {"bOk": True}


async def _fnImportTheContextUnderTheDrain(
    dictCtx, sContainerId, dictWorkflow, request, requestHttp,
):
    """Probe, read the source, write, and re-point the root as one.

    Four operations that must not be separated. The overwrite probe
    guards the write, so a dropped drain between them lets a second
    import land on top of the first without ever seeing it. The symlink
    replacement is stranger and more important: it deletes the adopted
    root file and recreates it pointing at the canonical context, so
    between the write and the symlink there is a window in which the
    repository holds two real files with different contents -- exactly
    the divergence the symlink exists to prevent.

    The 4xx refusals are carried back: a missing root file, a bad
    basename, an unreadable host path and "it already exists" are all
    decided before any container write, so none is a state to
    reconcile. The 500 from a failed symlink replacement is NOT carried,
    and must not be -- the content has landed by then and the root file
    has been removed, so nobody knows what the repository holds. That is
    the quarantine's whole purpose.
    """
    sAbsPath = _fsContextAbsolutePath(dictWorkflow)

    def fnImportTheContent():
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

    def fnRunTheImport(supervisor=None):
        del supervisor
        return fdictCarryARefusalBackInsteadOfRaising(fnImportTheContent)

    await fobjRunWorkerUnderTheDrain(
        sContainerId, fnRunTheImport, "project-context-import",
        requestHttp,
    )


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


def _fnRequireSupervisionOffBeforeDisabling(dictWorkflow, bEnabled):
    """Refuse to switch the Prompt Record off while supervision is on.

    Supervised is the rung above Recorded, so disabling the record
    while the watchdog is enabled would leave the Replay axis claiming
    a state its evidence no longer supports. The researcher must stand
    supervision down first — deliberately, in the place that says so.
    """
    if bEnabled:
        return
    dictSupervision = (
        (dictWorkflow.get(S_AI_PROVENANCE_KEY) or {})
        .get("dictSupervision") or {}
    )
    if dictSupervision.get("bEnabled") is True:
        raise HTTPException(
            409, "Supervised mode is on and rests on the Prompt "
            "Record. Turn supervision off first, then disable the "
            "record.",
        )


def _fnRegisterPromptRecordConfigure(app, dictCtx):
    """Register POST .../prompt-record/configure."""

    @fnAgentAction("configure-prompt-record")
    @app.post("/api/workflow/{sContainerId}/prompt-record/configure")
    @fnDeclareCarrierMode(S_CARRIER_MODE_A_SYNCHRONOUS)
    async def fnConfigurePromptRecord(
        sContainerId: str, request: dict, requestHttp: Request,
    ):
        # Late-bound so an install of vaibify[replay] (or a test
        # patch) takes effect without restarting the hub.
        from .. import transcriptSanitizer
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        bEnabled = request.get("bEnabled") is True
        _fnRequireSupervisionOffBeforeDisabling(dictWorkflow, bEnabled)
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
        fnCommitWorkflowSave(
            dictCtx, sContainerId, dictWorkflow, requestHttp,
            "The Prompt Record setting",
        )
        return {"dictPromptRecord": dictRecord}


def _fnRegisterPromptRecordCapture(app, dictCtx):
    """Register POST .../prompt-record/capture (one capture pass)."""
    from .. import promptRecordManager
    from ..routeContext import ffilesForWorkflow

    @fnAgentAction("capture-prompt-record")
    @app.post("/api/workflow/{sContainerId}/prompt-record/capture")
    @fnDeclareCarrierMode(S_CARRIER_MODE_B_LOCK_HELD)
    async def fnCapturePromptRecord(
        sContainerId: str, requestHttp: Request,
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        dictRecord = _fdictPromptRecordOf(dictWorkflow)
        if dictRecord.get("bEnabled") is not True:
            raise HTTPException(409, "The Prompt Record is not enabled.")
        _fsContextAbsolutePath(dictWorkflow)
        filesRepo = ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow)

        def fnRunTheCapturePass(supervisor=None):
            del supervisor
            return promptRecordManager.fdictRunCapturePass(
                dictCtx["docker"], sContainerId, filesRepo,
                _flistGatherSessionSecrets(dictCtx, sContainerId),
            )

        # The journal target is the compile-time constant below, never
        # a transcript path or any part of a captured prompt: this
        # route's whole subject matter is text that may contain
        # secrets, and the journal is an on-disk record with a
        # different lifetime from the sanitized transcript.
        dictSummary = await _fdictRunTheCaptureUnderTheDrain(
            sContainerId, fnRunTheCapturePass, requestHttp,
        )
        dictSummary["bPendingReview"] = (
            dictRecord.get("bFirstCaptureReviewed") is not True
        )
        return dictSummary


async def _fdictRunTheCaptureUnderTheDrain(
    sContainerId, fnRunTheCapturePass, requestHttp,
):
    """Run one Prompt Record capture pass under the drain.

    Mode (b): the pass reads every agent transcript in the container,
    scans each for secrets, and writes the sanitized copies plus an
    index. It is unbounded in the number of transcripts, so an ownership
    hand-over landing mid-capture would otherwise hand somebody else a
    container still having transcripts written into it.

    Nothing is carried back because the pass raises no HTTPException --
    this route's one refusal is decided in the handler, before the
    carrier exists. Anything escaping the pass leaves a partially
    written transcript set and poisons, which is correct.
    """
    from .. import commitCarrier
    dictLaneTuple = fdictRequireLaneTupleForCommit(
        requestHttp, sContainerId, "The Prompt Record capture",
    )
    dictOutcome = await commitCarrier.fdictRunLockHeldMutation(
        requestHttp.app.state, dictLaneTuple["sContainerName"],
        sContainerId, dictLaneTuple, "helper", "prompt-record-capture",
        fnRunTheCapturePass,
    )
    return dictOutcome["result"]


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
    @fnDeclareCarrierMode(S_CARRIER_MODE_A_SYNCHRONOUS)
    async def fnApproveFirstCapture(
        sContainerId: str, requestHttp: Request,
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        dictRecord = _fdictPromptRecordOf(dictWorkflow)
        if dictRecord.get("bEnabled") is not True:
            raise HTTPException(409, "The Prompt Record is not enabled.")
        dictRecord["bFirstCaptureReviewed"] = True
        fnCommitWorkflowSave(
            dictCtx, sContainerId, dictWorkflow, requestHttp,
            "The first-capture approval",
        )
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
    @fnDeclareCarrierMode(S_CARRIER_MODE_A_SYNCHRONOUS)
    async def fnConfigureSupervision(
        sContainerId: str, request: dict, requestHttp: Request,
    ):
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
        fnCommitWorkflowSave(
            dictCtx, sContainerId, dictWorkflow, requestHttp,
            "The Supervised-mode setting",
        )
        return {"dictSupervision": dictSupervision}


def _fsValidatePersonalLayerStatus(request):
    """Return the declared status string or raise HTTP 400."""
    if not isinstance(request, dict):
        raise HTTPException(
            400, "Personal-layer declaration must be an object.",
        )
    sStatus = str(request.get("sStatus") or "")
    if sStatus not in SET_PERSONAL_LAYER_STATUSES:
        raise HTTPException(
            400, "sStatus must be one of: "
            + ", ".join(sorted(SET_PERSONAL_LAYER_STATUSES)) + ".",
        )
    return sStatus


def _fnRegisterDeclarePersonalLayer(app, dictCtx):
    """Register POST /api/workflow/{sContainerId}/personal-layer/declare.

    Answering the question — with ANY of the three statuses — is what
    the Level 2 criterion requires; disclosure is never required, and
    ``declared-private`` with zero hash commitments is a fully valid
    answer. User-only in the catalog: like the other L2 consent
    moments, the statement about the researcher's private
    configuration must come from the researcher.
    """

    @fnAgentAction("declare-personal-layer")
    @app.post("/api/workflow/{sContainerId}/personal-layer/declare")
    @fnDeclareCarrierMode(S_CARRIER_MODE_A_SYNCHRONOUS)
    async def fnDeclarePersonalLayer(
        sContainerId: str, request: dict, requestHttp: Request,
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        sStatus = _fsValidatePersonalLayerStatus(request)
        dictProvenance = _fdictProvenanceOf(dictWorkflow)
        dictLayer = dict(
            dictProvenance.get(S_PERSONAL_LAYER_KEY) or {},
        )
        dictLayer["sStatus"] = sStatus
        dictLayer["sDeclaredIso"] = datetime.now(
            timezone.utc,
        ).isoformat()
        if "dictHashCommitment" in request:
            if sStatus != "declared-private":
                raise HTTPException(
                    400, "Hash commitments only accompany the "
                    "'declared-private' status.",
                )
            listCommitments = list(
                dictLayer.get("listHashCommitments") or [],
            )
            try:
                listCommitments.append(
                    fdictValidateHashCommitment(
                        request["dictHashCommitment"],
                    ),
                )
            except ValueError as error:
                raise HTTPException(400, str(error))
            dictLayer["listHashCommitments"] = listCommitments
        if "listIncludedPaths" in request:
            if sStatus != "included":
                raise HTTPException(
                    400, "listIncludedPaths only accompanies the "
                    "'included' status.",
                )
            try:
                dictLayer["listIncludedPaths"] = (
                    flistValidateIncludedPaths(
                        request["listIncludedPaths"],
                    )
                )
            except ValueError as error:
                raise HTTPException(400, str(error))
        dictProvenance[S_PERSONAL_LAYER_KEY] = dictLayer
        fnCommitWorkflowSave(
            dictCtx, sContainerId, dictWorkflow, requestHttp,
            "The personal-layer declaration",
        )
        return {"dictPersonalLayer": dictLayer}


def _fnRegisterHashPersonalLayerFile(app, dictCtx):
    """Register POST .../personal-layer/hash (researcher-only).

    Reads a HOST file at the researcher's request and returns its
    SHA-256 commitment; nothing is persisted. Excluded from the
    agent-action catalog AND guarded against the agent token lane at
    the route itself: an agent-reachable variant would hand a
    compromised in-container agent a hash oracle over host files.
    """
    import asyncio

    # separate-authority, not typed-read. This route reaches the
    # container NOT AT ALL: it reads a file on the researcher's own
    # machine and returns a hash, persisting nothing. `typed-read`
    # would be doubly wrong -- it claims container reads through the
    # typed adapter, of which this makes none, and any reader takes it
    # to mean "the route is harmless", which is the opposite of the
    # concern here. What governs it is a separate authority: the
    # agent-lane rejection on the first line, without which a
    # compromised in-container agent would hold a hash oracle over the
    # researcher's home directory, plus fdictComputeHashCommitment's own
    # host-path handling. Ruling 2026-08-05, same reasoning as
    # fileRoutes' pull route.
    @app.post("/api/workflow/{sContainerId}/personal-layer/hash")
    @fnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fnHashPersonalLayerFile(
        sContainerId: str, request: dict, requestHttp: Request,
    ):
        fnRejectAgentTokenLane(requestHttp)
        dictCtx["require"]()
        fdictRequireWorkflow(dictCtx["workflows"], sContainerId)
        sLabel = str(request.get("sLabel") or "").strip()
        if not sLabel:
            raise HTTPException(
                400, "A hash commitment needs a non-empty sLabel.",
            )
        try:
            dictCommitment = await asyncio.to_thread(
                fdictComputeHashCommitment,
                str(request.get("sHostPath") or ""), sLabel,
            )
        except ValueError as error:
            raise HTTPException(400, str(error))
        except OSError:
            # OSError text can embed the full path; a fixed message
            # keeps the path out of the response.
            raise HTTPException(400, "Could not read the file.")
        return {"dictHashCommitment": dictCommitment}


def fnRegisterAll(app, dictCtx):
    """Register all Replay-axis routes."""
    _fnRegisterDeclareAiModel(app, dictCtx)
    _fnRegisterRemoveAiModel(app, dictCtx)
    _fnRegisterDeclarePersonalLayer(app, dictCtx)
    _fnRegisterHashPersonalLayerFile(app, dictCtx)
    _fnRegisterReadProjectContext(app, dictCtx)
    _fnRegisterUpdateProjectContext(app, dictCtx)
    _fnRegisterContextTemplate(app, dictCtx)
    _fnRegisterContextImport(app, dictCtx)
    _fnRegisterPromptRecordConfigure(app, dictCtx)
    _fnRegisterPromptRecordCapture(app, dictCtx)
    _fnRegisterPromptRecordApprove(app, dictCtx)
    _fnRegisterPromptRecordStatus(app, dictCtx)
    _fnRegisterSupervisionConfigure(app, dictCtx)
