"""HTTP routes for the AICS Level 3 readiness + attestation surface.

Three endpoints back the AICS tab's L3 sections:

* ``GET .../level3/readiness`` — returns ``fdictL3ReadinessGaps``
  shape for the readiness checklist card.
* ``POST .../level3/verify`` — user-only; kicks off the expensive
  rebuild as a background task and returns a 202 with the in-flight
  status handle. The worker invokes ``commandReproduce.fbRerunWorkflow``
  inside ``asyncio.to_thread`` so the existing pipeline runner (the
  same machinery ``vaibify run`` uses) actually executes the workflow
  inside the container. After the rerun, the manifest is re-verified
  against the freshly produced outputs and the attestation is written
  with the real matched/diverged counts.
* ``GET .../level3/attestation`` — returns the most-recent
  attestation plus the archived history.

The locked-in plan decision is that the L3 badge only lights after a
successful rebuild — a manifest re-hash alone (the readiness gateway)
is not enough to attest.
"""

__all__ = ["fnRegisterAll"]

import asyncio
import logging
import time

from fastapi import HTTPException

from ..actionCatalog import fnAgentAction
from ..pipelineServer import fdictRequireWorkflow
from ..routeContext import ffilesForWorkflow
from ...reproducibility.repoFiles import (
    ffilesEnsureRepoFiles,
    fsRepoRootOf,
)
from ...reproducibility.l3Attestation import (
    S_STATUS_FAILED,
    S_STATUS_PASSED,
    fdictBuildAttestation,
    fdictReadAttestation,
    flistReadAttestationHistory,
    fnWriteAttestation,
    fsCurrentManifestDigest,
)
from ...reproducibility.environmentSnapshot import (
    fdictCaptureSingleBinary,
    fdictReadEnvironmentJson,
    fnWriteEnvironmentJson,
)
from ...reproducibility.levelGates import (
    fbL3ReadinessOK,
    fdictL3ReadinessGaps,
    fiAICSLevel,
)
from ...reproducibility.manifestWriter import flistVerifyManifest
from ...reproducibility.reproduceScriptGenerator import (
    S_REPRODUCE_SCRIPT_FILENAME,
    fnGenerateReproduceScript,
)


logger = logging.getLogger(__name__)

# In-process tracker for in-flight L3 verification tasks, keyed by
# container id. A task entry is the asyncio.Task plus a tiny status
# dict so polling endpoints can report progress without re-running
# the rebuild.
_DICT_VERIFY_TASKS = {}


def _fsRequireProjectRepo(dictWorkflow):
    """Return the workflow's project repo path or raise HTTP 409."""
    sProjectRepo = (
        dictWorkflow.get("sProjectRepoPath") or ""
    ).strip()
    if not sProjectRepo:
        raise HTTPException(
            409,
            "Workflow has no project repo; initialize one before "
            "running L3 verification.",
        )
    return sProjectRepo


def _fnRegisterReadiness(app, dictCtx):
    """Register GET /api/workflow/{sContainerId}/level3/readiness."""

    @fnAgentAction("check-l3-readiness")
    @app.get("/api/workflow/{sContainerId}/level3/readiness")
    async def fnL3Readiness(sContainerId: str):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        filesRepo = ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow)
        dictGaps = fdictL3ReadinessGaps(dictWorkflow, filesRepo)
        return {
            "iAICSLevel": fiAICSLevel(dictWorkflow, filesRepo),
            "dictL3ReadinessGaps": dictGaps,
        }


def _fnRegisterAttestation(app, dictCtx):
    """Register GET /api/workflow/{sContainerId}/level3/attestation."""

    @fnAgentAction("view-l3-attestation")
    @app.get("/api/workflow/{sContainerId}/level3/attestation")
    async def fnL3AttestationGet(sContainerId: str):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        filesRepo = ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow)
        return _fdictBuildAttestationResponse(
            sContainerId, filesRepo,
        )


def _fdictBuildAttestationResponse(sContainerId, filesRepo):
    """Return the attestation payload shape consumed by the AICS tab."""
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    bHasRepo = bool(fsRepoRootOf(filesRepo))
    dictCurrent = fdictReadAttestation(filesRepo) if bHasRepo else None
    listHistory = (
        flistReadAttestationHistory(filesRepo)
        if bHasRepo else []
    )
    dictStatus = _DICT_VERIFY_TASKS.get(sContainerId, {}).get(
        "dictStatus"
    )
    return {
        "dictCurrentAttestation": dictCurrent,
        "listHistory": listHistory,
        "dictInFlight": dictStatus,
        "sLiveManifestDigest": (
            fsCurrentManifestDigest(filesRepo)
            if bHasRepo else ""
        ),
    }


def _fnRegisterVerify(app, dictCtx):
    """Register POST /api/workflow/{sContainerId}/level3/verify."""

    @fnAgentAction("verify-l3-reproducibility")
    @app.post("/api/workflow/{sContainerId}/level3/verify")
    async def fnL3Verify(sContainerId: str):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        _fsRequireProjectRepo(dictWorkflow)
        filesRepo = ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow)
        _fnRefuseIfTaskInFlight(sContainerId)
        if not fbL3ReadinessOK(dictWorkflow, filesRepo):
            raise HTTPException(
                409,
                "L3 readiness checks must all pass before triggering "
                "verification; open the AICS tab to see gaps.",
            )
        return _fdictKickOffVerification(
            sContainerId, filesRepo, dictWorkflow,
        )


def _fnRefuseIfTaskInFlight(sContainerId):
    """Raise 409 when a verification is already running for the container."""
    dictExisting = _DICT_VERIFY_TASKS.get(sContainerId)
    if not dictExisting:
        return
    taskExisting = dictExisting.get("task")
    if taskExisting is not None and not taskExisting.done():
        raise HTTPException(
            409,
            "L3 verification already running for this container.",
        )


def _fdictKickOffVerification(sContainerId, filesRepo, dictWorkflow):
    """Snapshot manifest, schedule the worker, and return the handle."""
    sManifestDigest = fsCurrentManifestDigest(filesRepo)
    dictStatus = {
        "sPhase": "starting",
        "fStartedAtMonotonic": time.monotonic(),
        "sManifestDigestAtAttestation": sManifestDigest,
    }
    coroutineWorker = _fnRunVerificationWorker(
        sContainerId, filesRepo, sManifestDigest, dictWorkflow,
    )
    taskWorker = asyncio.create_task(coroutineWorker)
    _DICT_VERIFY_TASKS[sContainerId] = {
        "task": taskWorker, "dictStatus": dictStatus,
    }
    return {
        "bAccepted": True,
        "sPhase": "starting",
        "sManifestDigestAtAttestation": sManifestDigest,
    }


async def _fnRunVerificationWorker(
    sContainerId, filesRepo, sManifestDigest, dictWorkflow,
):
    """Run the rebuild in a worker thread and persist the attestation.

    The actual reproducibility work (docker pull, pip install, step
    execution, output hash compare) is delegated to a sync helper that
    invokes ``commandReproduce.fbRerunWorkflow`` so the existing
    pipeline runner does the container work. Offloaded to
    ``asyncio.to_thread`` so the rerun does not block the FastAPI event
    loop. Exceptions are converted into a failed attestation so the
    UI never sees a silent hang.
    """
    dictStatus = _DICT_VERIFY_TASKS[sContainerId]["dictStatus"]
    dictStatus["sPhase"] = "running"
    fStarted = time.monotonic()
    try:
        dictResult = await asyncio.to_thread(
            _fdictRunReproductionSync, filesRepo, dictWorkflow,
        )
    except Exception as exc:  # noqa: BLE001 — surface as failed attestation
        logger.exception("L3 verification crashed: %s", exc)
        dictResult = {
            "bPassed": False,
            "iOutputHashesMatched": 0,
            "iOutputHashesTotal": 0,
            "listDivergedHashes": [f"verification crashed: {exc}"],
            "sImageDigest": "",
            "sRunLogPath": "",
        }
    fDuration = time.monotonic() - fStarted
    _fnPersistAttestation(
        filesRepo, sManifestDigest, dictResult, fDuration,
    )
    dictStatus["sPhase"] = (
        "passed" if dictResult.get("bPassed") else "failed"
    )


def _fdictRunReproductionSync(filesRepo, dictWorkflow):
    """Run the expensive L3 reproduction synchronously.

    Delegates to ``commandReproduce.fbRerunWorkflow`` which resolves the
    project's docker connection, requires a running container, and
    invokes the same pipeline runner that ``vaibify run`` uses. After
    the rerun completes, the manifest is re-verified against the freshly
    produced outputs so the attestation records what actually matched on
    disk. The locked-in plan decision is that the L3 badge only lights
    after this expensive rebuild succeeds; a manifest re-hash alone is
    the cheap readiness gateway exposed separately at
    ``/level3/readiness``.
    """
    del dictWorkflow  # workflow state is loaded from the container
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    bRerunSucceeded = _fbInvokeRerunWorkflow(fsRepoRootOf(filesRepo))
    listMismatches = flistVerifyManifest(filesRepo)
    iTotalEntries = _fiManifestEntryCount(filesRepo)
    iMatching = max(iTotalEntries - len(listMismatches), 0)
    listDiverged = [
        dictMismatch["sPath"] for dictMismatch in listMismatches
    ]
    if not bRerunSucceeded:
        listDiverged = (
            ["pipeline rerun exited non-zero"] + listDiverged
        )
    sImageDigest = _fsResolveImageDigest(filesRepo)
    return {
        "bPassed": bRerunSucceeded and not listMismatches,
        "iOutputHashesMatched": iMatching,
        "iOutputHashesTotal": iTotalEntries,
        "listDivergedHashes": listDiverged,
        "sImageDigest": sImageDigest,
        "sRunLogPath": "",
    }


def _fbInvokeRerunWorkflow(sProjectRepo):
    """Invoke the CLI rerun helper inside the worker thread.

    The import lives inside the function so the FastAPI app boot does
    not pull the CLI machinery into the route module's import graph.
    Exceptions are swallowed and surfaced as a failed rerun so the
    worker can still write a "failed" attestation rather than crashing
    silently inside the background task.
    """
    try:
        from ...cli.commandReproduce import fbRerunWorkflow
    except ImportError as exc:
        logger.error("Could not import fbRerunWorkflow: %s", exc)
        return False
    try:
        return bool(fbRerunWorkflow(sProjectRepo))
    except (Exception, SystemExit) as exc:  # noqa: BLE001
        logger.exception("fbRerunWorkflow raised: %s", exc)
        return False


def _fiManifestEntryCount(filesRepo):
    """Return the manifest entry count, treating absence as zero."""
    from ...reproducibility.manifestWriter import (
        fiCountManifestEntries,
    )
    try:
        return fiCountManifestEntries(filesRepo)
    except (FileNotFoundError, OSError, ValueError):
        return 0


def _fsResolveImageDigest(filesRepo):
    """Return the recorded image digest or empty string."""
    dictPayload = fdictReadEnvironmentJson(filesRepo)
    if not dictPayload:
        return ""
    dictContainer = dictPayload.get("dictContainer")
    if isinstance(dictContainer, dict):
        return dictContainer.get("sImageDigest") or ""
    return dictPayload.get("sImageDigest") or ""


def _fnPersistAttestation(
    filesRepo, sManifestDigest, dictResult, fDuration,
):
    """Write the attestation file and update the in-flight status dict."""
    sStatus = S_STATUS_PASSED if dictResult["bPassed"] else S_STATUS_FAILED
    dictAttestation = fdictBuildAttestation(
        sStatus=sStatus,
        sManifestDigest=sManifestDigest,
        sImageDigest=dictResult.get("sImageDigest", ""),
        fDurationSeconds=fDuration,
        iOutputHashesMatched=dictResult["iOutputHashesMatched"],
        iOutputHashesTotal=dictResult["iOutputHashesTotal"],
        listDivergedHashes=dictResult["listDivergedHashes"],
        sRunLogPath=dictResult.get("sRunLogPath", ""),
    )
    try:
        fnWriteAttestation(filesRepo, dictAttestation)
    except OSError as exc:
        logger.error("Could not persist L3 attestation: %s", exc)


def _fnRegisterGenerateScript(app, dictCtx):
    """Register POST /api/workflow/{sContainerId}/level3/reproduce-script."""

    @fnAgentAction("generate-reproduce-script")
    @app.post(
        "/api/workflow/{sContainerId}/level3/reproduce-script"
    )
    async def fnL3GenerateReproduceScript(sContainerId: str):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        sProjectRepo = _fsRequireProjectRepo(dictWorkflow)
        try:
            sPathWritten = fnGenerateReproduceScript(
                sProjectRepo, dictWorkflow,
                connectionDocker=dictCtx["docker"],
                sContainerId=sContainerId,
            )
        except OSError as exc:
            raise HTTPException(
                500, f"Could not write reproduce.sh: {exc}",
            ) from exc
        return {
            "bWritten": True,
            "sScriptPath": sPathWritten,
            "sScriptFilename": S_REPRODUCE_SCRIPT_FILENAME,
        }


def _fnRegisterDeclareBinaries(app, dictCtx):
    """Register POST /api/workflow/{sContainerId}/binaries/declare."""

    @fnAgentAction("declare-standalone-binaries")
    @app.post(
        "/api/workflow/{sContainerId}/binaries/declare"
    )
    async def fnDeclareBinaries(sContainerId: str, request: dict):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        _fnValidateBinaryDeclarationBody(request)
        dictWorkflow["bNoStandaloneBinaries"] = bool(
            request.get("bNoStandaloneBinaries", False),
        )
        dictWorkflow["listDeclaredBinaries"] = list(
            request.get("listDeclaredBinaries") or [],
        )
        dictCtx["save"](sContainerId, dictWorkflow)
        return {
            "bNoStandaloneBinaries":
                dictWorkflow["bNoStandaloneBinaries"],
            "listDeclaredBinaries":
                dictWorkflow["listDeclaredBinaries"],
        }


def _fnValidateBinaryDeclarationBody(dictRequest):
    """Raise HTTP 400 when the declaration body violates the state machine."""
    if not isinstance(dictRequest, dict):
        raise HTTPException(400, "Body must be a JSON object.")
    bWaiver = bool(dictRequest.get("bNoStandaloneBinaries", False))
    listDeclared = dictRequest.get("listDeclaredBinaries") or []
    if not isinstance(listDeclared, list):
        raise HTTPException(
            400, "listDeclaredBinaries must be a list.",
        )
    if bWaiver and listDeclared:
        raise HTTPException(
            400,
            "Waiver requires listDeclaredBinaries to be empty.",
        )
    if not bWaiver and not listDeclared:
        raise HTTPException(
            400,
            "Without the waiver, listDeclaredBinaries must be "
            "non-empty.",
        )
    _fnValidateDeclaredBinaryEntries(listDeclared)


def _fnValidateDeclaredBinaryEntries(listDeclared):
    """Raise HTTP 400 when any declared entry is missing required fields."""
    for iIndex, dictEntry in enumerate(listDeclared):
        if not isinstance(dictEntry, dict):
            raise HTTPException(
                400, f"Entry {iIndex} is not an object.",
            )
        for sKey in ("sBinaryPath", "sPurpose", "sExpectedVersion"):
            sValue = dictEntry.get(sKey)
            if not isinstance(sValue, str) or not sValue.strip():
                raise HTTPException(
                    400,
                    f"Entry {iIndex} missing string {sKey!r}.",
                )


def _fnRegisterCaptureBinary(app, dictCtx):
    """Register POST /api/workflow/{sContainerId}/binaries/capture."""

    @fnAgentAction("capture-binary-environment")
    @app.post(
        "/api/workflow/{sContainerId}/binaries/capture"
    )
    async def fnCaptureBinary(sContainerId: str, request: dict):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        _fsRequireProjectRepo(dictWorkflow)
        filesRepo = ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow)
        sBinaryPath = (request or {}).get("sBinaryPath") or ""
        if not isinstance(sBinaryPath, str) or not sBinaryPath.strip():
            raise HTTPException(400, "sBinaryPath is required.")
        dictCaptured = fdictCaptureSingleBinary(filesRepo, sBinaryPath)
        _fnAppendBinaryToEnvironmentJson(filesRepo, dictCaptured)
        return {"dictCaptured": dictCaptured}


def _fnAppendBinaryToEnvironmentJson(filesRepo, dictCaptured):
    """Append or replace a binary entry in .vaibify/environment.json."""
    dictPayload = fdictReadEnvironmentJson(filesRepo) or {}
    dictHost = dictPayload.get("dictHostBinaries")
    if not isinstance(dictHost, dict):
        dictHost = {"listBinaries": []}
    listBinaries = dictHost.get("listBinaries")
    if not isinstance(listBinaries, list):
        listBinaries = []
    listFiltered = [
        d for d in listBinaries
        if not (
            isinstance(d, dict)
            and d.get("sBinaryPath") == dictCaptured["sBinaryPath"]
        )
    ]
    listFiltered.append(dictCaptured)
    dictHost["listBinaries"] = listFiltered
    dictPayload["dictHostBinaries"] = dictHost
    fnWriteEnvironmentJson(filesRepo, dictPayload)


def fnRegisterAll(app, dictCtx):
    """Register every L3 reproducibility endpoint."""
    _fnRegisterReadiness(app, dictCtx)
    _fnRegisterAttestation(app, dictCtx)
    _fnRegisterVerify(app, dictCtx)
    _fnRegisterGenerateScript(app, dictCtx)
    _fnRegisterDeclareBinaries(app, dictCtx)
    _fnRegisterCaptureBinary(app, dictCtx)
