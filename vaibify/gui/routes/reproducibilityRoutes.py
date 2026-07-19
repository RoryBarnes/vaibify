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
from ..aiProvenanceCapture import fdictCaptureAiProvenanceStamp
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
from ...reproducibility.determinismGate import (
    S_ACCEPT_BLAS_WAIVER_KEY,
    S_MKL_CBWR_KEY,
    S_OMP_NUM_THREADS_KEY,
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
            sContainerId, filesRepo, dictWorkflow, dictCtx["docker"],
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


def _fdictKickOffVerification(
    sContainerId, filesRepo, dictWorkflow, connectionDocker,
):
    """Snapshot manifest, schedule the worker, and return the handle."""
    sManifestDigest = fsCurrentManifestDigest(filesRepo)
    dictStatus = {
        "sPhase": "starting",
        "fStartedAtMonotonic": time.monotonic(),
        "sManifestDigestAtAttestation": sManifestDigest,
    }
    coroutineWorker = _fnRunVerificationWorker(
        sContainerId, filesRepo, sManifestDigest, dictWorkflow,
        connectionDocker,
    )
    taskWorker = asyncio.create_task(coroutineWorker)
    _fnRegisterVerifyTask(sContainerId, taskWorker, dictStatus)
    return {
        "bAccepted": True,
        "sPhase": "starting",
        "sManifestDigestAtAttestation": sManifestDigest,
    }


def _fnRegisterVerifyTask(sContainerId, taskWorker, dictStatus):
    """Store the verify task and arrange identity-checked self-eviction.

    Mirrors ``pipelineServer._fnRegisterPipelineTask`` so completed
    verifications do not linger in ``_DICT_VERIFY_TASKS`` forever.
    The identity check on the slot's task object prevents a brand-new
    verification that landed in the same slot from being evicted by
    the prior task's done-callback firing late.
    """
    _DICT_VERIFY_TASKS[sContainerId] = {
        "task": taskWorker, "dictStatus": dictStatus,
    }

    def fnEvictOnDone(taskCompleted):
        dictEntry = _DICT_VERIFY_TASKS.get(sContainerId)
        if dictEntry is not None and dictEntry.get("task") is taskCompleted:
            _DICT_VERIFY_TASKS.pop(sContainerId, None)
    taskWorker.add_done_callback(fnEvictOnDone)


async def _fnRunVerificationWorker(
    sContainerId, filesRepo, sManifestDigest, dictWorkflow,
    connectionDocker,
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
    dictAiProvenance = await _fdictCaptureProvenanceOrNone(
        dictWorkflow, filesRepo, sContainerId, connectionDocker,
    )
    _fnPersistAttestation(
        filesRepo, sManifestDigest, dictResult, fDuration,
        dictAiProvenance,
    )
    dictStatus["sPhase"] = (
        "passed" if dictResult.get("bPassed") else "failed"
    )


async def _fdictCaptureProvenanceOrNone(
    dictWorkflow, filesRepo, sContainerId, connectionDocker,
):
    """Capture the Replay-axis stamp; ``None`` records capture failure.

    A stamp that cannot be captured must never block the attestation
    write — ``dictAiProvenance: None`` in the record honestly says "no
    capture was possible", which the dashboard surfaces as a gap.
    """
    try:
        return await asyncio.to_thread(
            fdictCaptureAiProvenanceStamp,
            dictWorkflow, filesRepo, sContainerId, connectionDocker,
        )
    except Exception as exc:  # noqa: BLE001 — recorded as None, not raised
        logger.error("AI-provenance capture failed: %s", exc)
        return None


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
    dictAiProvenance=None,
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
        dictAiProvenance=dictAiProvenance,
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
        # The Level 3 check requires the script's hash IN the
        # manifest, so re-pin immediately — without this the check
        # stayed red after every generation until the next envelope
        # regeneration, which read as "the button did nothing".
        bManifestRefreshed = True
        try:
            from ...reproducibility import manifestWriter
            filesRepo = ffilesForWorkflow(
                dictCtx, sContainerId, dictWorkflow,
            )
            await asyncio.to_thread(
                manifestWriter.fnWriteManifest, filesRepo, dictWorkflow,
            )
        except Exception as exc:
            logging.getLogger("vaibify").warning(
                "reproduce.sh written but manifest re-pin failed: %s",
                exc,
            )
            bManifestRefreshed = False
        return {
            "bWritten": True,
            "bManifestRefreshed": bManifestRefreshed,
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


# Wire-format keys and their accepted scalar JSON types for the
# determinism declaration route; these are exactly the keys
# determinismGate.fbWorkflowDeclaresDeterminism reads.
_DICT_DETERMINISM_KEY_TYPES = {
    S_ACCEPT_BLAS_WAIVER_KEY: (bool,),
    S_OMP_NUM_THREADS_KEY: (int, float),
    S_MKL_CBWR_KEY: (str,),
}


def _fnRequireScalarType(sKey, jsonValue, tTypesExpected):
    """Raise HTTP 422 when jsonValue is not the expected JSON scalar.

    Booleans are rejected for numeric keys (Python bool subclasses
    int) and required for boolean keys, so type confusion cannot
    smuggle a waiver through as a thread count or vice versa.
    """
    bIsBoolean = isinstance(jsonValue, bool)
    bWantsBoolean = bool in tTypesExpected
    if bIsBoolean != bWantsBoolean or not isinstance(
        jsonValue, tTypesExpected,
    ):
        sTypeNames = " or ".join(
            typeOption.__name__ for typeOption in tTypesExpected
        )
        raise HTTPException(
            422, f"{sKey} must be a JSON {sTypeNames} scalar.",
        )


def _fdictValidateDeterminismBody(dictRequest):
    """Return the validated determinism keys or raise HTTP 422.

    Accepts only the three scalar keys the L3 determinism gate reads;
    at least one must be present and every value must match its
    declared scalar type. A ``null`` value means "remove this key" —
    without it, a mistaken pin (an OpenMP thread count the researcher
    cleared in the form) survived every re-declaration because the
    route merges keys. Unknown keys are rejected outright so typos
    cannot silently fail the readiness gate later.
    """
    if not isinstance(dictRequest, dict) or not dictRequest:
        raise HTTPException(
            422,
            "Body must declare at least one of: "
            + ", ".join(sorted(_DICT_DETERMINISM_KEY_TYPES)) + ".",
        )
    dictDeclared = {}
    for sKey, jsonValue in dictRequest.items():
        tTypesExpected = _DICT_DETERMINISM_KEY_TYPES.get(sKey)
        if tTypesExpected is None:
            raise HTTPException(
                422,
                f"Unknown determinism key {sKey!r}; accepted keys: "
                + ", ".join(sorted(_DICT_DETERMINISM_KEY_TYPES)) + ".",
            )
        if jsonValue is None:
            dictDeclared[sKey] = None
            continue
        _fnRequireScalarType(sKey, jsonValue, tTypesExpected)
        dictDeclared[sKey] = jsonValue
    return dictDeclared


def _fnRegisterDeclareDeterminism(app, dictCtx):
    """Register POST /api/workflow/{sContainerId}/determinism/declare."""

    @fnAgentAction("declare-determinism")
    @app.post(
        "/api/workflow/{sContainerId}/determinism/declare"
    )
    async def fnDeclareDeterminism(sContainerId: str, request: dict):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        dictDeclared = _fdictValidateDeterminismBody(request)
        dictDeterminism = dict(
            dictWorkflow.get("dictDeterminism") or {},
        )
        for sKey, jsonValue in dictDeclared.items():
            if jsonValue is None:
                dictDeterminism.pop(sKey, None)
            else:
                dictDeterminism[sKey] = jsonValue
        dictWorkflow["dictDeterminism"] = dictDeterminism
        dictCtx["save"](sContainerId, dictWorkflow)
        return {"dictDeterminism": dictDeterminism}


def _fnRegisterRegenerateEnvelope(app, dictCtx):
    """Register POST /api/workflow/{sContainerId}/level3/envelope.

    The envelope regenerates automatically on the L1 crossing, but the
    researcher must also be able to refresh it on demand (a failed
    tier, a new dependency, a stale manifest) without waiting for the
    next promotion. Tier failures are logged-and-isolated inside the
    generator; the response returns the fresh readiness gaps so the
    caller can see what the regeneration achieved.
    """

    @fnAgentAction("regenerate-envelope")
    @app.post(
        "/api/workflow/{sContainerId}/level3/envelope"
    )
    async def fnRegenerateEnvelope(sContainerId: str):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        _fsRequireProjectRepo(dictWorkflow)
        filesRepo = ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow)
        from ...reproducibility import dataArchiver
        await asyncio.to_thread(
            dataArchiver.fnGenerateReproducibilityEnvelope,
            filesRepo, dictWorkflow,
            sContainerId, dictWorkflow.get("saHostBinaries"),
        )
        return {
            "dictL3ReadinessGaps": fdictL3ReadinessGaps(
                dictWorkflow, filesRepo,
            ),
        }


def _fnRegisterDeleteDeterminism(app, dictCtx):
    """Register DELETE /api/workflow/{sContainerId}/determinism.

    The declare endpoint merges keys, so a mistaken declaration (a
    pinned thread count the researcher wants unpinned) could never be
    removed. Deleting clears the whole declaration; the GUI confirms
    first and the researcher re-declares what still applies.
    """

    @fnAgentAction("delete-determinism")
    @app.delete(
        "/api/workflow/{sContainerId}/determinism"
    )
    async def fnDeleteDeterminism(sContainerId: str):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        dictWorkflow["dictDeterminism"] = {}
        dictCtx["save"](sContainerId, dictWorkflow)
        return {"dictDeterminism": {}}


def _fnRegisterVerifyDependencyLock(app, dictCtx):
    """Register POST /api/workflow/{sContainerId}/dependencies/verify.

    Structural check of requirements.lock: every dependency pinned by
    exact version with hashes. Returns the problem list so the GUI can
    report what is wrong rather than a bare pass/fail.
    """

    @fnAgentAction("verify-dependency-lock")
    @app.post(
        "/api/workflow/{sContainerId}/dependencies/verify"
    )
    async def fnVerifyDependencyLock(sContainerId: str):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        _fsRequireProjectRepo(dictWorkflow)
        filesRepo = ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow)
        from ...reproducibility.dependencyPinning import (
            flistVerifyRequirementsLock,
        )
        listProblems = await asyncio.to_thread(
            flistVerifyRequirementsLock, filesRepo,
        )
        return {"listProblems": list(listProblems)}


def fnRegisterAll(app, dictCtx):
    """Register every L3 reproducibility endpoint."""
    _fnRegisterReadiness(app, dictCtx)
    _fnRegisterAttestation(app, dictCtx)
    _fnRegisterVerify(app, dictCtx)
    _fnRegisterGenerateScript(app, dictCtx)
    _fnRegisterDeclareBinaries(app, dictCtx)
    _fnRegisterCaptureBinary(app, dictCtx)
    _fnRegisterDeclareDeterminism(app, dictCtx)
    _fnRegisterRegenerateEnvelope(app, dictCtx)
    _fnRegisterDeleteDeterminism(app, dictCtx)
    _fnRegisterVerifyDependencyLock(app, dictCtx)
