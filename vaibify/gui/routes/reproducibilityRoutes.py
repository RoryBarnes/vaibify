"""HTTP routes for the AICS Level 3 readiness + attestation surface.

Three endpoints back the AICS tab's L3 sections:

* ``GET .../level3/readiness`` — returns ``fdictL3ReadinessGaps``
  shape for the readiness checklist card.
* ``POST .../level3/verify`` — user-only; kicks off the expensive
  rebuild as a background task and returns a 202 with the in-flight
  status handle. The worker calls
  ``rerunVerification.fdictRerunAndVerifyWorkflow`` inside
  ``asyncio.to_thread``, passing the active workflow, its container
  path, and the container repo-file adapter explicitly, so the
  pipeline runner executes *that* workflow and the post-rerun re-hash
  reads the same container filesystem it wrote to. The attestation is
  then written with the real matched/diverged counts.
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

from fastapi import HTTPException, Request

from ...config.mutationAdmission import fnReRaiseControlPlaneRefusal
from ..actionCatalog import fnAgentAction
from ..aiProvenanceCapture import fdictCaptureAiProvenanceStamp
from ..pipelineServer import fdictRequireWorkflow
from ..routeContext import (
    fdictCarryARefusalBackInsteadOfRaising,
    fdictRequireLaneTupleForCommit,
    ffilesForWorkflow,
    fnCommitWorkflowSave,
    fobjRunWorkerUnderTheDrain,
)
from ..routeScope import (
    S_CARRIER_MODE_A_SYNCHRONOUS,
    S_CARRIER_MODE_B_LOCK_HELD,
    S_CARRIER_MODE_C_DURABLE,
    S_CARRIER_TYPED_READ,
    fnDeclareCarrierMode,
)
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
from ...reproducibility.rerunVerification import (
    fdictRerunAndVerifyWorkflow,
)
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
    @fnDeclareCarrierMode(
        S_CARRIER_MODE_B_LOCK_HELD, S_CARRIER_MODE_C_DURABLE,
    )
    async def fnL3Verify(sContainerId: str, requestHttp: Request):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        _fsRequireProjectRepo(dictWorkflow)
        filesRepo = ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow)
        sWorkflowPath = _fsRequireWorkflowPath(dictCtx, sContainerId)
        _fnRefuseIfTaskInFlight(sContainerId)
        sManifestDigest = await _fsGateReadinessAndSnapshotDigest(
            sContainerId, dictWorkflow, filesRepo, requestHttp,
        )
        return await _fdictLaunchVerificationDurably(
            sContainerId, filesRepo, sManifestDigest, dictWorkflow,
            dictCtx["docker"], sWorkflowPath, requestHttp,
        )


async def _fsGateReadinessAndSnapshotDigest(
    sContainerId, dictWorkflow, filesRepo, requestHttp,
):
    """Check L3 readiness and snapshot the manifest digest under one drain.

    Both reach the container and both look like reads: the readiness
    gate and the digest snapshot each hash the repository through the
    GENERAL exec primitive, which the gate must treat as mutating. They
    share ONE mode-(b) drain because they must agree -- the attestation
    is keyed to the digest snapshotted here, and a digest taken from a
    tree that changed after the readiness check passed would attest a
    state nobody verified.

    The 409 is carried back rather than raised: readiness failing is a
    decision made with the container untouched, and quarantining it
    would take the container out of service for a workflow that simply
    is not ready yet -- the ordinary state before the envelope exists.
    """
    def fsGateThenSnapshot(supervisor=None):
        del supervisor
        return fdictCarryARefusalBackInsteadOfRaising(
            lambda: _fsRequireReadinessThenDigest(dictWorkflow, filesRepo),
        )

    return await fobjRunWorkerUnderTheDrain(
        sContainerId, fsGateThenSnapshot, "level3-verify-readiness",
        requestHttp,
    )


def _fsRequireReadinessThenDigest(dictWorkflow, filesRepo):
    """Return the manifest digest, or raise 409 when L3 is not ready."""
    if not fbL3ReadinessOK(dictWorkflow, filesRepo):
        raise HTTPException(
            409,
            "L3 readiness checks must all pass before triggering "
            "verification; open the AICS tab to see gaps.",
        )
    return fsCurrentManifestDigest(filesRepo)


async def _fdictLaunchVerificationDurably(
    sContainerId, filesRepo, sManifestDigest, dictWorkflow,
    connectionDocker, sWorkflowPath, requestHttp,
):
    """Launch the rebuild as REGISTERED durable work (design §8, mode c).

    Mode (c) rather than (b) because the response returns while the
    work continues: the rebuild re-executes the whole workflow inside
    the container and can run for minutes. Registering it under the
    briefly-held mutation lock is what makes it VISIBLE -- before this,
    the task lived only in a module-global dict no other authority
    read, so an ownership hand-over, the shutdown drain and the idle
    watchdog all saw an idle container while a full workflow rerun was
    writing to it.

    The carrier refuses a second durable launch per CONTAINER, which is
    stricter than the per-container check above it and deliberately so:
    two reruns of the same repository would overwrite each other's
    outputs. The in-flight check stays because it names the specific
    thing running; this one is the authority.
    """
    from .. import commitCarrier
    dictLaneTuple = fdictRequireLaneTupleForCommit(
        requestHttp, sContainerId, "The L3 verification",
    )
    dictStatus = {
        "sPhase": "starting",
        "fStartedAtMonotonic": time.monotonic(),
        "sManifestDigestAtAttestation": sManifestDigest,
    }

    def ftaskStartVerification():
        taskWorker = asyncio.create_task(_fnRunVerificationWorker(
            sContainerId, filesRepo, sManifestDigest, dictWorkflow,
            connectionDocker, sWorkflowPath,
        ))
        _fnRegisterVerifyTask(sContainerId, taskWorker, dictStatus)
        return taskWorker

    dictLaunched = await commitCarrier.fdictLaunchDurableTask(
        requestHttp.app.state, dictLaneTuple["sContainerName"],
        sContainerId, dictLaneTuple, ftaskStartVerification,
    )
    if not dictLaunched["bLaunched"]:
        raise HTTPException(
            409,
            "This container is busy: " + dictLaunched["sReason"] + ".",
        )
    return {
        "bAccepted": True,
        "sPhase": "starting",
        "sManifestDigestAtAttestation": sManifestDigest,
    }


def _fsRequireWorkflowPath(dictCtx, sContainerId):
    """Return the active workflow's container path or raise HTTP 409.

    The rerun must target the workflow the researcher is looking at.
    Without its container path the runner would have to rediscover one,
    and in a container hosting several project repos that means running
    workflow A while the attestation names workflow B.
    """
    sWorkflowPath = (dictCtx.get("paths") or {}).get(sContainerId) or ""
    if not sWorkflowPath:
        raise HTTPException(
            409,
            "No active workflow path for this container; reconnect "
            "before running L3 verification.",
        )
    return sWorkflowPath


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
    connectionDocker, sWorkflowPath,
):
    """Run the rebuild in a worker thread and persist the attestation.

    The actual reproducibility work (step execution inside the
    container, then the output hash compare against that same
    container) is delegated to a sync helper that calls the shared
    ``rerunVerification`` entry point. Offloaded to
    ``asyncio.to_thread`` so the rerun does not block the FastAPI event
    loop. Exceptions are converted into a failed attestation so the
    UI never sees a silent hang.
    """
    dictStatus = _DICT_VERIFY_TASKS[sContainerId]["dictStatus"]
    dictStatus["sPhase"] = "running"
    fStarted = time.monotonic()
    try:
        dictResult = await asyncio.to_thread(
            _fdictRunReproductionSync, connectionDocker, sContainerId,
            dictWorkflow, sWorkflowPath, filesRepo,
        )
    except (Exception, SystemExit) as exc:  # noqa: BLE001
        # SystemExit is caught too: it is not an Exception, so an
        # sys.exit() anywhere beneath the rerun would leave the task
        # done-with-exception, the phase stuck on "running", and no
        # attestation written — a silent hang, which is the one
        # outcome this worker must never produce.
        #
        # A carrier REFUSAL is the exception to that exception. It
        # means the durable admission was not opened, and writing it
        # here would record a programming error as a FAILED L3
        # ATTESTATION — a scientific claim, keyed to a manifest digest,
        # saying this workflow does not reproduce. A stuck phase is
        # recoverable; a false attestation on disk is not.
        fnReRaiseControlPlaneRefusal(exc)
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


def _fdictRunReproductionSync(
    connectionDocker, sContainerId, dictWorkflow, sWorkflowPath, filesRepo,
):
    """Run the expensive L3 reproduction synchronously.

    Delegates to ``rerunVerification.fdictRerunAndVerifyWorkflow``, the
    single entry point the ``vaibify reproduce --rerun`` lane also uses
    — do not inline either half here again, as the two derivations
    previously drifted until the CLI stopped comparing anything.

    Every input is passed explicitly. The route already holds the active
    workflow and its container path, and passing them on is what keeps a
    container that hosts several project repos from re-running one
    workflow while the attestation names another. ``filesRepo`` is the
    container adapter rooted at ``sProjectRepoPath``, so the re-hash
    reads the filesystem the rerun actually wrote to; routing this
    through the host CLI resolver could not work at all, because
    ``sProjectRepoPath`` is a container path with no host counterpart.

    The locked-in plan decision is that the L3 badge only lights after
    this expensive rebuild succeeds; a manifest re-hash alone is the
    cheap readiness gateway exposed separately at ``/level3/readiness``.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    dictOutcome = fdictRerunAndVerifyWorkflow(
        connectionDocker, sContainerId, dictWorkflow, sWorkflowPath,
        filesRepo,
    )
    return {
        **dictOutcome,
        "sImageDigest": _fsResolveImageDigest(filesRepo),
        "sRunLogPath": "",
    }


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
    @fnDeclareCarrierMode(S_CARRIER_MODE_B_LOCK_HELD)
    async def fnL3GenerateReproduceScript(
        sContainerId: str, requestHttp: Request,
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        sProjectRepo = _fsRequireProjectRepo(dictWorkflow)
        return await _fdictGenerateScriptUnderTheDrain(
            dictCtx, sContainerId, dictWorkflow, sProjectRepo, requestHttp,
        )


async def _fdictGenerateScriptUnderTheDrain(
    dictCtx, sContainerId, dictWorkflow, sProjectRepo, requestHttp,
):
    """Write ``reproduce.sh`` and re-pin the manifest under one drain.

    ONE carrier for both, because the manifest re-pin is what makes the
    script count: the Level 3 check requires the script's hash IN the
    manifest, and without the re-pin the check stayed red after every
    generation, which read as "the button did nothing". A hand-over
    landing between them would leave the successor with a script the
    manifest does not know about -- the exact state that bug produced.

    Mode (b) rather than mode (a): the write is followed by a ``chmod``
    exec and then by a full repo hash, so it runs for as long as the
    tree takes and belongs in a worker thread.
    """
    def fnGenerateTheScript(supervisor=None):
        del supervisor
        return fdictCarryARefusalBackInsteadOfRaising(
            lambda: _fdictWriteScriptThenRepinManifest(
                dictCtx, sContainerId, dictWorkflow, sProjectRepo,
            ),
            # The generator answers 500 for a failed WRITE, and a write
            # that failed is exactly the unknown state the quarantine
            # exists for -- so no 5xx is named here and it propagates.
        )

    return await fobjRunWorkerUnderTheDrain(
        sContainerId, fnGenerateTheScript, "reproduce-script", requestHttp,
    )


def _fdictWriteScriptThenRepinManifest(
    dictCtx, sContainerId, dictWorkflow, sProjectRepo,
):
    """Write the script, then re-pin the manifest; report both outcomes.

    Synchronous because a mode-(b) worker runs in a thread and cannot
    await the ``to_thread`` hop the manifest re-pin used to make.
    """
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
        "bManifestRefreshed": _fbRepinManifestOrWarn(
            dictCtx, sContainerId, dictWorkflow,
        ),
        "sScriptPath": sPathWritten,
        "sScriptFilename": S_REPRODUCE_SCRIPT_FILENAME,
    }


def _fbRepinManifestOrWarn(dictCtx, sContainerId, dictWorkflow):
    """Re-pin MANIFEST.sha256; return False (never raise) on failure.

    A failed re-pin degrades to ``bManifestRefreshed: False`` because
    the script itself did land and the researcher can regenerate the
    envelope. A carrier REFUSAL is not that: it means this route's
    carrier call was forgotten, and answering 200 with a soft flag
    would hide the migration's only proof behind a checkbox.
    """
    from ...reproducibility import manifestWriter
    try:
        manifestWriter.fnWriteManifest(
            ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow),
            dictWorkflow,
        )
    except Exception as exc:
        fnReRaiseControlPlaneRefusal(exc)
        logging.getLogger("vaibify").warning(
            "reproduce.sh written but manifest re-pin failed: %s", exc,
        )
        return False
    return True


def _fnRegisterDeclareBinaries(app, dictCtx):
    """Register POST /api/workflow/{sContainerId}/binaries/declare."""

    @fnAgentAction("declare-standalone-binaries")
    @app.post(
        "/api/workflow/{sContainerId}/binaries/declare"
    )
    @fnDeclareCarrierMode(S_CARRIER_MODE_A_SYNCHRONOUS)
    async def fnDeclareBinaries(
        sContainerId: str, request: dict, requestHttp: Request,
    ):
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
        fnCommitWorkflowSave(
            dictCtx, sContainerId, dictWorkflow, requestHttp,
            "The standalone-binary declaration",
        )
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
    @fnDeclareCarrierMode(S_CARRIER_MODE_B_LOCK_HELD)
    async def fnCaptureBinary(
        sContainerId: str, request: dict, requestHttp: Request,
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        _fsRequireProjectRepo(dictWorkflow)
        filesRepo = ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow)
        sBinaryPath = (request or {}).get("sBinaryPath") or ""
        if not isinstance(sBinaryPath, str) or not sBinaryPath.strip():
            raise HTTPException(400, "sBinaryPath is required.")
        return await _fdictCaptureBinaryUnderTheDrain(
            filesRepo, sContainerId, sBinaryPath, requestHttp,
        )


async def _fdictCaptureBinaryUnderTheDrain(
    filesRepo, sContainerId, sBinaryPath, requestHttp,
):
    """Hash the binary, run it, and merge the entry under one drain.

    Mode (b) rather than mode (a) for two reasons that compound. The
    capture RUNS the declared binary (``<path> --version``, bounded at
    five seconds), so it belongs in a worker thread rather than on the
    event loop where it used to sit. And the environment record is
    read-modify-written with no lock of its own, so two captures
    arriving together could each read the file before either wrote and
    one entry would vanish; the drain is now that lock.

    Nothing here raises for an expected refusal -- an unreadable binary
    comes back as a capture entry with an empty hash -- so the worker
    does not poison its record for an outcome the researcher can read.
    """
    def fnCaptureTheBinary(supervisor=None):
        del supervisor
        return fdictCarryARefusalBackInsteadOfRaising(
            lambda: _fdictCaptureAndRecordBinary(filesRepo, sBinaryPath),
        )

    return await fobjRunWorkerUnderTheDrain(
        sContainerId, fnCaptureTheBinary, "binary-capture", requestHttp,
    )


def _fdictCaptureAndRecordBinary(filesRepo, sBinaryPath):
    """Capture one binary and merge it into the environment record."""
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
    @fnDeclareCarrierMode(S_CARRIER_MODE_A_SYNCHRONOUS)
    async def fnDeclareDeterminism(
        sContainerId: str, request: dict, requestHttp: Request,
    ):
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
        fnCommitWorkflowSave(
            dictCtx, sContainerId, dictWorkflow, requestHttp,
            "The determinism declaration",
        )
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
    @fnDeclareCarrierMode(S_CARRIER_MODE_B_LOCK_HELD)
    async def fnRegenerateEnvelope(
        sContainerId: str, requestHttp: Request,
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        _fsRequireProjectRepo(dictWorkflow)
        return await _fdictRegenerateEnvelopeUnderTheDrain(
            dictCtx, sContainerId, dictWorkflow, requestHttp,
        )


async def _fdictRegenerateEnvelopeUnderTheDrain(
    dictCtx, sContainerId, dictWorkflow, requestHttp,
):
    """Regenerate the envelope and re-read its gaps under one drain.

    ONE carrier covering the generation AND the readiness re-read,
    which is the only shape that does not leave half of this route
    uncarried: the gap check hashes the repository to compare the
    manifest digest, so it reaches the exec primitive exactly as the
    generation does. It used to run on the event loop after the thread
    returned; under the enforced branch that would be refused.

    The generator writes three files across three tiers and isolates
    each tier's own failure, on the stated principle that a partial
    envelope beats no envelope. Those handlers cannot absorb a carrier
    refusal -- ``ControlPlaneRefusalError`` descends from ``Exception``
    alone, and every tier catches a narrower type (verified at the
    console) -- so a forgotten carrier still raises out of the worker.
    """
    filesRepo = ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow)

    def fnRegenerateTheEnvelope(supervisor=None):
        del supervisor
        return fdictCarryARefusalBackInsteadOfRaising(
            lambda: _fdictGenerateEnvelopeThenReadGaps(
                filesRepo, dictWorkflow, sContainerId,
            ),
        )

    return await fobjRunWorkerUnderTheDrain(
        sContainerId, fnRegenerateTheEnvelope, "level3-envelope",
        requestHttp,
    )


def _fdictGenerateEnvelopeThenReadGaps(
    filesRepo, dictWorkflow, sContainerId,
):
    """Write the envelope, then report what the regeneration achieved.

    Synchronous because a mode-(b) worker runs in a thread and cannot
    await the ``to_thread`` hop the generation used to make.
    """
    from ...reproducibility import dataArchiver
    dataArchiver.fnGenerateReproducibilityEnvelope(
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
    @fnDeclareCarrierMode(S_CARRIER_MODE_A_SYNCHRONOUS)
    async def fnDeleteDeterminism(
        sContainerId: str, requestHttp: Request,
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        dictWorkflow["dictDeterminism"] = {}
        fnCommitWorkflowSave(
            dictCtx, sContainerId, dictWorkflow, requestHttp,
            "The determinism deletion",
        )
        return {"dictDeterminism": {}}


def _fnRegisterVerifyDependencyLock(app, dictCtx):
    """Register POST /api/workflow/{sContainerId}/dependencies/verify.

    Structural check of requirements.lock: every dependency pinned by
    exact version with hashes. Returns the problem list so the GUI can
    report what is wrong rather than a bare pass/fail.

    ``typed-read`` despite the POST verb and the "verify" name, which
    is the point of declaring behaviour rather than inferring it from
    the method: ``flistVerifyRequirementsLock`` calls exactly
    ``fbIsFile`` and ``fsReadText``, both of which reach the container
    through the typed-read adapter and neither of which is
    mutation-capable. The verb is POST because the GUI models it as an
    action, not because anything is written. A carrier mode here would
    state that the route mutates, which is false.
    """

    @fnAgentAction("verify-dependency-lock")
    @app.post(
        "/api/workflow/{sContainerId}/dependencies/verify"
    )
    @fnDeclareCarrierMode(S_CARRIER_TYPED_READ)
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
