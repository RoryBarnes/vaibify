"""PROOF level readiness route handlers.

Exposes the per-workflow Level 2 readiness rollup that the PROOF tab
consumes, and the AI Declaration step's routes: add the step, ask
whether its file exists, generate a starter template, and attach a
file to the step.

Every endpoint is agent-safe: ``check-l2-readiness`` and
``check-ai-declaration-file`` are read-only,
``generate-ai-declaration-template`` only writes a new file (it
refuses to overwrite an existing one, so it cannot lose researcher
content), ``add-ai-declaration-step`` refuses when a declaration step
already exists, and ``attach-ai-declaration-file`` only points the
step at a file that already exists -- and pointing it at a DIFFERENT
file withdraws the sign-off, so an agent cannot carry a researcher's
attestation over to a document they never read. Signing off remains
a user-only action via the standard ``sUser`` badge on the step.

Generation never attaches by itself: the dashboard's Generate button
writes the template and then calls attach, two explicit actions an
agent can also take in turn.
"""

__all__ = ["fnRegisterAll"]

import os
import posixpath

from fastapi import HTTPException, Request
from pydantic import BaseModel
from typing import Optional

from ...config.registryManager import fbIsHostProject
from ..actionCatalog import ffnAgentAction
from ..pipelineServer import (
    _fsSanitizeServerError,
    fdictRequireWorkflow,
    fnRejectWriteDenylistedPath,
    fsValidatePathWithinRoot,
)
from ..pipelineUtils import fbStepDirectoryConforms, fsSlugFromStepName
from ..routeContext import (
    fdictCarryARefusalBackInsteadOfRaising,
    ffilesForWorkflow,
    fdictCommitWorkflowSave,
    fgenericRunWorkerUnderTheDrain,
)
from ..routeScope import (
    S_CARRIER_MODE_A_SYNCHRONOUS,
    S_CARRIER_MODE_B_LOCK_HELD,
    S_CARRIER_TYPED_READ,
    ffnDeclareCarrierMode,
)
from ...reproducibility.aiDeclarationStep import (
    S_DECLARATION_FILE_ABSENT,
    S_DECLARATION_FILE_PRESENT,
    S_DEFAULT_DECLARATION_FILENAME,
    S_DEFAULT_DECLARATION_STEP_NAME,
    fbDeclarationFileExists,
    fbDeclarationPathEscapesRepo,
    fbStepIsAiDeclaration,
    fdictBuildAiDeclarationStep,
    fiAiDeclarationStepIndex,
    fsWriteDeclarationTemplate,
    ftDeclarationFileState,
)
from ...reproducibility.declarationFreshness import (
    fbLatchStaleDeclaration,
    fnClearDeclarationBaseline,
)
from ...reproducibility.levelGates import (
    fdictDeclarationFreshness,
    fdictDeclarationFreshnessForDisplay,
    fdictLevel2Gaps,
    fiProofLevel,
    flistLevel1Blockers,
)


class AiDeclarationTemplateRequest(BaseModel):
    """Body for the generate-template route.

    ``sRelativePath`` is optional; when absent the default
    ``AI_USAGE.md`` at the project repo root is used.
    """
    sRelativePath: Optional[str] = None


class AiDeclarationAttachRequest(BaseModel):
    """Body for the attach route: the repo-relative file to point at."""
    sRelativePath: str


class AiDeclarationAddStepRequest(BaseModel):
    """Body for the add-step route; every override is optional.

    Defaults come from ``fdictBuildAiDeclarationStep``: sName
    "AI Declaration", sDirectory "AIDeclaration" (the slug of the
    name -- it read "aiDeclaration" until 2026-08-25, violating the
    contract), sDeclarationFile "AI_USAGE.md".
    """
    sName: Optional[str] = None
    sDirectory: Optional[str] = None
    sDeclarationFile: Optional[str] = None


def _fnRejectEscapingPath(sCleanPath, sFieldName):
    """Raise 400 when a path is absolute or contains a ``..`` segment.

    Rejecting these keeps a malicious agent invocation from writing
    outside the project repo. The check is symmetric with the
    workflow-file path validation already enforced at load time.
    """
    if os.path.isabs(sCleanPath):
        raise HTTPException(
            400, f"{sFieldName} must be repo-relative",
        )
    listParts = sCleanPath.replace("\\", "/").split("/")
    if any(sPart == ".." for sPart in listParts):
        raise HTTPException(
            400, f"{sFieldName} may not contain '..'",
        )


def _fsValidateRelativePath(sRelativePath):
    """Return a sanitized, non-escaping repo-relative path or raise 400."""
    sClean = (sRelativePath or "").strip()
    if not sClean:
        return S_DEFAULT_DECLARATION_FILENAME
    _fnRejectEscapingPath(sClean, "sRelativePath")
    return sClean


def _fsValidateDeclarationPathInRepo(sRelativePath, sProjectRepo):
    """Return a repo-relative path proven to stay inside the repo, or raise.

    The lexical refusals (absolute, ``..``) come first so their 400s
    name the field; the normalized join is then held to the project
    repository and kept out of ``.git/``, ``.vaibify/`` and
    ``project.json``, none of which is a declaration.
    """
    sClean = (sRelativePath or "").strip()
    if not sClean:
        raise HTTPException(400, "sRelativePath is required")
    _fnRejectEscapingPath(sClean, "sRelativePath")
    sAbsolute = fsValidatePathWithinRoot(
        posixpath.join(sProjectRepo, sClean), sProjectRepo,
    )
    try:
        fnRejectWriteDenylistedPath(sAbsolute, sProjectRepo)
    except HTTPException:
        raise HTTPException(
            403, "The AI Declaration cannot be project.json or a file "
            "under .git/ or .vaibify/.",
        )
    return posixpath.relpath(sAbsolute, posixpath.normpath(sProjectRepo))


def _fnRejectDirectoryDisagreeingWithName(sDirectory, sName):
    """Raise 400 when a directory violates the name->slug contract.

    The generic update-step path already 400s a rename for exactly
    this reason, so that the directory, marker, and manifest can never
    drift from the name. Creation had no such guard, which is how the
    shipped declaration step came to be born non-conforming.
    """
    if fbStepDirectoryConforms(
        {"sName": sName, "sDirectory": sDirectory},
    ):
        return
    sExpected = fsSlugFromStepName(sName)
    raise HTTPException(
        400,
        f"sDirectory '{sDirectory}' does not match the step name "
        f"'{sName}' — the slug contract derives '{sExpected}'. "
        f"Omit sDirectory to have it derived, or rename the step.",
    )


def _fsValidateNewStepDirectory(dictWorkflow, sDirectory, sName):
    """Return a validated, unique step directory or raise 400/409.

    Mirrors the load-time step-directory boundary rules (repo-relative,
    no ``..`` escape) and additionally requires uniqueness among the
    workflow's existing step directories so per-step state keys in
    state.json cannot collide.

    An omitted directory is DERIVED from the step's own name rather
    than defaulting to a constant: a caller who overrides ``sName``
    and leaves ``sDirectory`` alone would otherwise get a step born
    violating the slug contract, which the dashboard paints as a red
    error against a step the product just built for them.
    """
    sClean = (sDirectory or "").strip() or fsSlugFromStepName(sName)
    _fnRejectEscapingPath(sClean, "sDirectory")
    _fnRejectDirectoryDisagreeingWithName(sClean, sName)
    setExistingDirectories = {
        (dictStep.get("sDirectory") or "").strip()
        for dictStep in dictWorkflow.get("listSteps", []) or []
        if isinstance(dictStep, dict)
    }
    if sClean in setExistingDirectories:
        raise HTTPException(
            409,
            f"Step directory '{sClean}' is already used by another "
            f"step; choose a unique sDirectory.",
        )
    return sClean


def _fsRequireProjectRepo(dictWorkflow):
    """Return the workflow's project repo path or raise 409."""
    sProjectRepo = (
        dictWorkflow.get("sProjectRepoPath") or ""
    ).strip()
    if not sProjectRepo:
        raise HTTPException(
            409,
            "Workflow has no project repo; initialize one before "
            "writing canonical artifacts.",
        )
    return sProjectRepo


def _fnRegisterLevel2Readiness(app, dictCtx):
    """Register GET /api/workflow/{sContainerId}/level2/readiness.

    Named for L2 but already the whole-ladder readiness endpoint: it
    has always answered ``iProofLevel``, which is a statement about
    every rung. ``listLevel1Blockers`` joins it rather than getting a
    route of its own because the awaiting allow-list may only shrink,
    and because one question -- "where does this project stand and
    why" -- is better answered in one call than two.

    That field is the only answer to "why is this project not at Level
    1 yet" an agent can obtain. Before it, the blocker list was
    computed on the dashboard's poll path and delivered only to the
    browser, so an agent asked that question read project.json and
    state.json and reconstructed an answer from raw fields. That is how
    a researcher came to be told an unattested AI Declaration blocked
    Level 1 (it does not -- that is a Level 2 criterion) and that
    qualitative tests were user-only (they are not). Both were
    inventions filling the space where a verdict should have been.

    Each blocker carries ``sRemediationHint``: the same plain-English
    sentence the dashboard shows the researcher ("Step has never been
    verified -- click verify when satisfied"). That text, not the
    ``sCriterion`` identifier, is what an agent should relay.
    """

    @ffnAgentAction("check-l2-readiness")
    @app.get(
        "/api/workflow/{sContainerId}/level2/readiness"
    )
    async def fdictLevel2Readiness(sContainerId: str):
        dictCtx["require"](sContainerId)
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        filesRepo = ffilesForWorkflow(
            dictCtx, sContainerId, dictWorkflow,
        )
        _fnLatchAnObservedStaleDeclaration(
            dictCtx, sContainerId, dictWorkflow, filesRepo,
        )
        dictGaps = fdictLevel2Gaps(dictWorkflow, filesRepo)
        return {
            "dictAiDeclarationFreshness":
                fdictDeclarationFreshnessForDisplay(
                    dictWorkflow, filesRepo),
            "iProofLevel": fiProofLevel(
                dictWorkflow, filesRepo,
                bHostProject=fbIsHostProject(sContainerId),
            ),
            "dictLevel2Gaps": dictGaps,
            "listLevel1Blockers": flistLevel1Blockers(
                dictWorkflow, {}, filesRepo,
            ),
            # Declared, never silent. The script-stale criterion needs
            # the per-step mtime scan the poll builds from live session
            # state, which this GET has no access to -- so the list can
            # omit a script-stale blocker, and a caller reporting
            # "nothing is blocking Level 1" from an empty list would be
            # making a claim this route cannot support.
            "bScriptStalenessEvaluated": False,
        }


def _fnLatchAnObservedStaleDeclaration(
    dictCtx, sContainerId, dictWorkflow, filesRepo,
):
    """Persist a stale AI Declaration this request is the first to see.

    Stale is sticky only if every check that observes it records it:
    reverting a file after this answer must not turn the sign-off fresh
    again. The latch can only withdraw a sign-off, never grant one, and
    it is saved exactly as the dashboard's file-status poll saves its
    own latch.
    """
    if fbLatchStaleDeclaration(
        dictWorkflow, fdictDeclarationFreshness(dictWorkflow, filesRepo),
    ):
        dictCtx["save"](sContainerId, dictWorkflow)


def _fnRegisterGenerateTemplate(app, dictCtx):
    """Register POST /api/workflow/{sContainerId}/ai-declaration/generate-template."""

    @ffnAgentAction("generate-ai-declaration-template")
    @app.post(
        "/api/workflow/{sContainerId}"
        "/ai-declaration/generate-template"
    )
    @ffnDeclareCarrierMode(S_CARRIER_MODE_B_LOCK_HELD)
    async def fdictHandleGenerateTemplate(
        sContainerId: str,
        request: AiDeclarationTemplateRequest,
        requestHttp: Request,
    ):
        dictCtx["require"](sContainerId)
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        _fsRequireProjectRepo(dictWorkflow)
        return await _fdictGenerateTemplateUnderTheDrain(
            ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow),
            sContainerId,
            _fsValidateRelativePath(request.sRelativePath),
            requestHttp,
        )


async def _fdictGenerateTemplateUnderTheDrain(
    filesRepo, sContainerId, sRelative, requestHttp,
):
    """Probe for an existing declaration, then write, under one drain.

    The probe is the GUARD -- "generate only if absent" -- so it and
    the write belong to one carrier: split across two, a second tab or
    the in-container agent could pass the absence check between them
    and the loser's template would overwrite a declaration the
    researcher had already started editing. The generator's own
    ``FileExistsError`` is a second line of defence, not a substitute:
    it raises AFTER the drain would already have been released.

    Mode (b) because the adapter spends three container round-trips per
    logical write (``mkdir -p``, the ``.tmp`` write, ``mv -f``) plus
    two typed reads for the probe, all of which belong in a worker.

    Both refusals are carried back rather than raised. The 409 is
    decided with the container untouched -- the file is simply already
    there -- and the 500 is raised for a template generation that
    refused to overwrite or was handed an empty path, which is equally
    a decision made BEFORE any byte is written. Neither is the unknown
    state a quarantine exists for.
    """
    def fdictGenerateTheTemplate(supervisor=None):
        del supervisor
        return fdictCarryARefusalBackInsteadOfRaising(
            lambda: _fdictProbeThenWriteTemplate(filesRepo, sRelative),
            setAlsoCarriedStatusCodes=frozenset({500}),
        )

    return await fgenericRunWorkerUnderTheDrain(
        sContainerId, fdictGenerateTheTemplate, "ai-declaration-template",
        requestHttp,
    )


def _fdictProbeThenWriteTemplate(filesRepo, sRelative):
    """Refuse an existing declaration, else write the starter template."""
    if fbDeclarationFileExists(filesRepo, sRelative):
        raise HTTPException(
            409,
            f"Declaration file already exists at '{sRelative}'; "
            f"edit it in place rather than regenerating.",
        )
    try:
        sAbsolute = fsWriteDeclarationTemplate(filesRepo, sRelative)
    except (OSError, ValueError) as error:
        raise HTTPException(
            500,
            f"Template generation failed: "
            f"{_fsSanitizeServerError(str(error))}",
        )
    return {
        "bSuccess": True,
        "sRelativePath": sRelative,
        "sAbsolutePath": sAbsolute,
    }


def _fnRefuseDuplicateAiDeclarationStep(dictWorkflow):
    """Raise 409 when the workflow already has an ai-declaration step."""
    for dictStep in dictWorkflow.get("listSteps", []) or []:
        if fbStepIsAiDeclaration(dictStep):
            raise HTTPException(
                409,
                "Workflow already has an AI Declaration step; edit "
                "the existing step instead of adding another.",
            )


def _fdictBuildStepFromAddRequest(dictWorkflow, request):
    """Translate the optional add-step body into a validated new step.

    The name is resolved to its effective value FIRST, because the
    directory is validated against it — deriving from the raw request
    would check a custom directory against the default name.
    """
    sName = (
        (request.sName or "").strip() or S_DEFAULT_DECLARATION_STEP_NAME
    )
    sDirectory = _fsValidateNewStepDirectory(
        dictWorkflow, request.sDirectory, sName,
    )
    sDeclarationFile = _fsValidateRelativePath(
        request.sDeclarationFile,
    )
    return fdictBuildAiDeclarationStep(
        sName=sName,
        sDeclarationFile=sDeclarationFile,
        sDirectory=sDirectory,
    )


def _fnRegisterAddStep(app, dictCtx):
    """Register POST /api/workflow/{sContainerId}/ai-declaration/add-step."""

    @ffnAgentAction("add-ai-declaration-step")
    @app.post(
        "/api/workflow/{sContainerId}"
        "/ai-declaration/add-step"
    )
    @ffnDeclareCarrierMode(S_CARRIER_MODE_A_SYNCHRONOUS)
    async def fdictHandleAddAiDeclarationStep(
        sContainerId: str,
        request: AiDeclarationAddStepRequest,
        requestHttp: Request,
    ):
        dictCtx["require"](sContainerId)
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        _fnRefuseDuplicateAiDeclarationStep(dictWorkflow)
        dictStep = _fdictBuildStepFromAddRequest(dictWorkflow, request)
        dictWorkflow.setdefault("listSteps", []).append(dictStep)
        fdictCommitWorkflowSave(
            dictCtx, sContainerId, dictWorkflow, requestHttp,
            "The AI Declaration step",
        )
        return {
            "iIndex": len(dictWorkflow["listSteps"]) - 1,
            "dictStep": dictStep,
        }


def _fnRejectPathResolvingOutsideRepo(filesRepo, sRelative):
    """Raise 403 when a symlink carries the path out of the repository."""
    if fbDeclarationPathEscapesRepo(filesRepo, sRelative):
        raise HTTPException(
            403, f"'{sRelative}' resolves outside the project "
            f"repository; the AI Declaration must be a file inside it.",
        )


def _fsDeclarationPathToCheck(dictWorkflow, sRequestedPath):
    """Return the path a file-state request asks about.

    An explicit path wins; otherwise the declaration step's own file;
    otherwise the default the Generate button would write.
    """
    if (sRequestedPath or "").strip():
        return sRequestedPath
    iIndex = fiAiDeclarationStepIndex(dictWorkflow)
    if iIndex >= 0:
        sAttached = (dictWorkflow["listSteps"][iIndex].get(
            "sDeclarationFile") or "").strip()
        if sAttached:
            return sAttached
    return S_DEFAULT_DECLARATION_FILENAME


def _fnRegisterFileState(app, dictCtx):
    """Register GET /api/workflow/{sContainerId}/ai-declaration/file-state.

    The dashboard asks here, never infers absence from a failed
    preview: a preview fails for a stopped container exactly as it
    does for a missing file, and only ABSENT may offer "Generate".
    """

    @ffnAgentAction("check-ai-declaration-file")
    @app.get(
        "/api/workflow/{sContainerId}/ai-declaration/file-state"
    )
    @ffnDeclareCarrierMode(S_CARRIER_TYPED_READ)
    async def fdictHandleDeclarationFileState(
        sContainerId: str, sRelativePath: Optional[str] = None,
    ):
        dictCtx["require"](sContainerId)
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        sProjectRepo = _fsRequireProjectRepo(dictWorkflow)
        sRelative = _fsValidateDeclarationPathInRepo(
            _fsDeclarationPathToCheck(dictWorkflow, sRelativePath),
            sProjectRepo,
        )
        filesRepo = ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow)
        _fnRejectPathResolvingOutsideRepo(filesRepo, sRelative)
        sState, sReason = ftDeclarationFileState(filesRepo, sRelative)
        return {
            "sRelativePath": sRelative,
            "sFileState": sState,
            "sReason": _fsSanitizeServerError(sReason) if sReason else "",
        }


def _fnRequireAttachableFile(filesRepo, sRelative):
    """Raise unless the file is PRESENT; absent and unknown differ."""
    sState, sReason = ftDeclarationFileState(filesRepo, sRelative)
    if sState == S_DECLARATION_FILE_PRESENT:
        return
    if sState == S_DECLARATION_FILE_ABSENT:
        raise HTTPException(
            409, f"'{sRelative}' does not exist in the project "
            f"repository; generate a template or choose an existing "
            f"file.",
        )
    raise HTTPException(
        503, _fsSanitizeServerError(sReason) or
        f"Could not check whether '{sRelative}' exists.",
    )


def _fbAttachDeclarationFile(dictStep, sRelative):
    """Point the step at a file; return True if a sign-off was withdrawn.

    The sign-off attests to one document. Re-attaching the same path
    changes nothing; a different path returns the step to untested,
    because the researcher has not read the new file.
    """
    sPrevious = (dictStep.get("sDeclarationFile") or "").strip()
    dictStep["sDeclarationFile"] = sRelative
    if sPrevious == sRelative:
        return False
    dictVerification = dictStep.setdefault("dictVerification", {})
    bWasSigned = dictVerification.get("sUser", "untested") != "untested"
    dictVerification["sUser"] = "untested"
    fnClearDeclarationBaseline(dictVerification)
    return bWasSigned


def _fnRegisterAttach(app, dictCtx):
    """Register POST /api/workflow/{sContainerId}/ai-declaration/attach."""

    @ffnAgentAction("attach-ai-declaration-file")
    @app.post(
        "/api/workflow/{sContainerId}/ai-declaration/attach"
    )
    @ffnDeclareCarrierMode(S_CARRIER_MODE_A_SYNCHRONOUS)
    async def fdictHandleAttachDeclarationFile(
        sContainerId: str,
        request: AiDeclarationAttachRequest,
        requestHttp: Request,
    ):
        dictCtx["require"](sContainerId)
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        iIndex = fiAiDeclarationStepIndex(dictWorkflow)
        if iIndex < 0:
            raise HTTPException(
                409, "This project has no AI Declaration step; add "
                "one before attaching a file.",
            )
        sRelative = _fsValidateDeclarationPathInRepo(
            request.sRelativePath, _fsRequireProjectRepo(dictWorkflow),
        )
        filesRepo = ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow)
        _fnRejectPathResolvingOutsideRepo(filesRepo, sRelative)
        _fnRequireAttachableFile(filesRepo, sRelative)
        bSignOffWithdrawn = _fbAttachDeclarationFile(
            dictWorkflow["listSteps"][iIndex], sRelative,
        )
        fdictCommitWorkflowSave(
            dictCtx, sContainerId, dictWorkflow, requestHttp,
            "The AI Declaration file",
        )
        return {
            "iIndex": iIndex,
            "sRelativePath": sRelative,
            "bSignOffWithdrawn": bSignOffWithdrawn,
        }


def fnRegisterAll(app, dictCtx):
    """Register the PROOF level readiness routes."""
    _fnRegisterLevel2Readiness(app, dictCtx)
    _fnRegisterGenerateTemplate(app, dictCtx)
    _fnRegisterAddStep(app, dictCtx)
    _fnRegisterFileState(app, dictCtx)
    _fnRegisterAttach(app, dictCtx)
