"""HTTP routes for the per-step falsification attestation surface.

Two endpoints back the Quantitative Tests block's Falsification row:

* ``GET .../falsification`` — returns the step's live applicability,
  the persisted record (if any), digest-keyed staleness, and any
  in-flight run status. Fetched when the researcher expands the
  quantitative block — never on the poll path.
* ``POST .../run-falsification`` — on-demand; kicks off the expensive
  cosmic-ray mutation run as a background task and returns a handle
  (mirrors ``reproducibilityRoutes``' verify kickoff). The worker
  writes the cosmic-ray config into the container, re-runs the step
  plus its quantitative tests per mutant, summarizes the session, and
  persists the digest-keyed record.

The attestation is NON-GATING: no AICS rung reads it, and the
``levelGates`` chain is untouched. The kill-rate states the tests'
fault-detection sensitivity, not the result's accuracy.
"""

__all__ = ["fnRegisterAll"]

import asyncio
import json
import logging
import posixpath
import time

from fastapi import HTTPException

from ..actionCatalog import fnAgentAction
from ..pipelineRunner import fsShellQuote
from ..pipelineServer import fdictRequireWorkflow
from ..routeContext import ffilesForWorkflow
from ...reproducibility.falsificationAttestation import (
    S_SESSION_SUMMARY_SCRIPT,
    S_STATUS_ATTAINED,
    S_STATUS_ERROR,
    fdictBuildFalsificationRecord,
    fdictBuildFalsificationStatus,
    fdictClassifyFalsificationApplicability,
    flistFalsificationDigestPaths,
    fnWriteFalsificationRecord,
    fsBuildCosmicRayConfigToml,
    fsCurrentFalsificationDigest,
    fsFalsificationStepSlug,
)


logger = logging.getLogger(__name__)

# In-process tracker for in-flight mutation runs, keyed by
# (sContainerId, iStepIndex). Each entry is the asyncio.Task plus a
# tiny status dict so the GET endpoint reports progress without
# re-running anything.
_DICT_FALSIFICATION_TASKS = {}

_S_CONTAINER_WORK_ROOT = "/tmp/vaibify-falsification"


def _fdictRequireStep(dictWorkflow, iStepIndex):
    """Return the step dict or raise HTTP 404 for a bad index."""
    listSteps = dictWorkflow.get("listSteps", [])
    if iStepIndex < 0 or iStepIndex >= len(listSteps):
        raise HTTPException(
            404, f"Step index {iStepIndex} out of range",
        )
    return listSteps[iStepIndex]


def _fsRequireProjectRepo(dictWorkflow):
    """Return the workflow's project repo path or raise HTTP 409."""
    sProjectRepo = (
        dictWorkflow.get("sProjectRepoPath") or ""
    ).strip()
    if not sProjectRepo:
        raise HTTPException(
            409,
            "Workflow has no project repo; the falsification record "
            "has nowhere canonical to live.",
        )
    return sProjectRepo


def _fdictInFlightStatus(sContainerId, iStepIndex):
    """Return the live status dict for a running task, or ``None``."""
    dictEntry = _DICT_FALSIFICATION_TASKS.get(
        (sContainerId, iStepIndex),
    )
    if not dictEntry:
        return None
    taskWorker = dictEntry.get("task")
    if taskWorker is None or taskWorker.done():
        return None
    return dictEntry.get("dictStatus")


def _fnRegisterView(app, dictCtx):
    """Register GET /api/steps/{id}/{step}/falsification."""

    @fnAgentAction("view-falsification-attestation")
    @app.get(
        "/api/steps/{sContainerId}/{iStepIndex}/falsification"
    )
    async def fnFalsificationGet(sContainerId: str, iStepIndex: int):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        dictStep = _fdictRequireStep(dictWorkflow, iStepIndex)
        filesRepo = ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow)
        dictInFlight = _fdictInFlightStatus(sContainerId, iStepIndex)
        return await asyncio.to_thread(
            fdictBuildFalsificationStatus,
            dictStep, filesRepo, dictInFlight,
        )


def _fnRegisterRun(app, dictCtx):
    """Register POST /api/steps/{id}/{step}/run-falsification."""

    @fnAgentAction("run-falsification")
    @app.post(
        "/api/steps/{sContainerId}/{iStepIndex}/run-falsification"
    )
    async def fnRunFalsification(sContainerId: str, iStepIndex: int):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        dictStep = _fdictRequireStep(dictWorkflow, iStepIndex)
        _fsRequireProjectRepo(dictWorkflow)
        filesRepo = ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow)
        _fnRefuseIfRunInFlight(sContainerId, iStepIndex)
        dictApplicability = await asyncio.to_thread(
            fdictClassifyFalsificationApplicability,
            dictStep, filesRepo,
        )
        if not dictApplicability["bApplicable"]:
            raise HTTPException(
                409, "Falsification check is not applicable: "
                + dictApplicability["sReason"],
            )
        sCosmicRayVersion = await asyncio.to_thread(
            _fsRequireCosmicRay, dictCtx["docker"], sContainerId,
        )
        return _fdictKickOffFalsification(
            dictCtx, sContainerId, iStepIndex, dictWorkflow,
            dictStep, dictApplicability, filesRepo, sCosmicRayVersion,
        )


def _fnRefuseIfRunInFlight(sContainerId, iStepIndex):
    """Raise 409 when a mutation run is already live for this step."""
    if _fdictInFlightStatus(sContainerId, iStepIndex) is not None:
        raise HTTPException(
            409,
            "A falsification check is already running for this step.",
        )


def _fsRequireCosmicRay(connectionDocker, sContainerId):
    """Return the container's cosmic-ray version or raise HTTP 409."""
    resultExec = connectionDocker.texecRunInContainerStreamed(
        sContainerId, "cosmic-ray --version",
    )
    if resultExec.iExitCode != 0:
        raise HTTPException(
            409,
            "cosmic-ray is not installed in this container image; "
            "rebuild the image (vaib build) to enable falsification "
            "checks.",
        )
    return resultExec.sStdout.strip()


def _fdictKickOffFalsification(
    dictCtx, sContainerId, iStepIndex, dictWorkflow,
    dictStep, dictApplicability, filesRepo, sCosmicRayVersion,
):
    """Schedule the mutation worker and return the accepted handle."""
    dictStatus = {
        "sPhase": "starting",
        "fStartedAtMonotonic": time.monotonic(),
    }
    taskWorker = asyncio.create_task(_fnRunFalsificationWorker(
        dictCtx, sContainerId, iStepIndex, dictWorkflow,
        dictStep, dictApplicability, filesRepo, sCosmicRayVersion,
    ))
    _fnRegisterFalsificationTask(
        (sContainerId, iStepIndex), taskWorker, dictStatus,
    )
    return {"bAccepted": True, "sPhase": "starting"}


def _fnRegisterFalsificationTask(tKey, taskWorker, dictStatus):
    """Store the task and arrange identity-checked self-eviction.

    Mirrors ``reproducibilityRoutes._fnRegisterVerifyTask``: the
    identity check on the slot's task object prevents a brand-new run
    that landed in the same slot from being evicted by the prior
    task's done-callback firing late.
    """
    _DICT_FALSIFICATION_TASKS[tKey] = {
        "task": taskWorker, "dictStatus": dictStatus,
    }

    def fnEvictOnDone(taskCompleted):
        dictEntry = _DICT_FALSIFICATION_TASKS.get(tKey)
        if dictEntry is not None and dictEntry.get("task") is taskCompleted:
            _DICT_FALSIFICATION_TASKS.pop(tKey, None)
    taskWorker.add_done_callback(fnEvictOnDone)


async def _fnRunFalsificationWorker(
    dictCtx, sContainerId, iStepIndex, dictWorkflow,
    dictStep, dictApplicability, filesRepo, sCosmicRayVersion,
):
    """Run the mutation session in a worker thread and persist the record.

    Exceptions become an ``error``-status record so the dashboard
    never sees a silent hang; the truth (including the crash reason)
    is written to disk and rendered.
    """
    tKey = (sContainerId, iStepIndex)
    dictStatus = _DICT_FALSIFICATION_TASKS[tKey]["dictStatus"]
    dictStatus["sPhase"] = "running"
    fStarted = time.monotonic()
    try:
        dictRecord = await asyncio.to_thread(
            _fdictRunMutationSync,
            dictCtx, sContainerId, dictWorkflow, dictStep,
            dictApplicability, filesRepo, sCosmicRayVersion,
        )
    except Exception as exc:  # noqa: BLE001 — surface as error record
        logger.exception("Falsification run crashed: %s", exc)
        dictRecord = fdictBuildFalsificationRecord(
            S_STATUS_ERROR, "", dictApplicability["sClassification"],
            0, 0, 0, sCosmicRayVersion=sCosmicRayVersion,
            fDurationSeconds=time.monotonic() - fStarted,
            sReason=f"falsification run crashed: {exc}",
        )
    try:
        await asyncio.to_thread(
            fnWriteFalsificationRecord,
            filesRepo, dictStep.get("sDirectory", ""), dictRecord,
        )
    except OSError as exc:
        logger.error("Could not persist falsification record: %s", exc)
    dictStatus["sPhase"] = dictRecord.get("sStatus", S_STATUS_ERROR)


def _fdictRunMutationSync(
    dictCtx, sContainerId, dictWorkflow, dictStep,
    dictApplicability, filesRepo, sCosmicRayVersion,
):
    """Run cosmic-ray init/exec in the container and build the record.

    The digest is captured BEFORE the run so the record is keyed to
    the exact source that was mutated (cosmic-ray restores files after
    each mutant, but a crash mid-mutant must not let a mutated file
    define the record's identity).
    """
    fStarted = time.monotonic()
    sDigest = fsCurrentFalsificationDigest(
        filesRepo, flistFalsificationDigestPaths(dictApplicability),
    )
    sClassification = dictApplicability["sClassification"]
    connectionDocker = dictCtx["docker"]
    sSessionPath = _fsPrepareMutationSession(
        dictCtx, sContainerId, dictWorkflow, dictStep,
        dictApplicability,
    )
    sWorkDirectory = posixpath.dirname(sSessionPath)
    sConfigPath = posixpath.join(sWorkDirectory, "cosmic-ray.toml")
    resultExec = connectionDocker.texecRunInContainerStreamed(
        sContainerId,
        f"cosmic-ray init {fsShellQuote(sConfigPath)} "
        f"{fsShellQuote(sSessionPath)} && "
        f"cosmic-ray exec {fsShellQuote(sConfigPath)} "
        f"{fsShellQuote(sSessionPath)}",
    )
    if resultExec.iExitCode != 0:
        return fdictBuildFalsificationRecord(
            S_STATUS_ERROR, sDigest, sClassification, 0, 0, 0,
            sCosmicRayVersion=sCosmicRayVersion,
            fDurationSeconds=time.monotonic() - fStarted,
            sReason="cosmic-ray exited "
            f"{resultExec.iExitCode}: "
            + _fsTailOfOutput(resultExec),
        )
    return _fdictSummarizeMutationSession(
        connectionDocker, sContainerId, sSessionPath,
        sDigest, sClassification, sCosmicRayVersion, fStarted,
    )


def _fsPrepareMutationSession(
    dictCtx, sContainerId, dictWorkflow, dictStep, dictApplicability,
):
    """Write the config + summary script into the container.

    Returns the container path of the (not yet created) session file.
    The scratch directory lives under /tmp so the mutation session
    never pollutes the project repo or its manifest.
    """
    sWorkDirectory = posixpath.join(
        _S_CONTAINER_WORK_ROOT,
        fsFalsificationStepSlug(dictStep.get("sDirectory", "")),
    )
    connectionDocker = dictCtx["docker"]
    connectionDocker.texecRunInContainerStreamed(
        sContainerId,
        f"rm -rf {fsShellQuote(sWorkDirectory)} && "
        f"mkdir -p {fsShellQuote(sWorkDirectory)}",
    )
    sRepoRoot = dictWorkflow.get("sProjectRepoPath", "")
    listModuleAbsPaths = [
        posixpath.join(sRepoRoot, sRelPath)
        for sRelPath in dictApplicability["listScriptRelPaths"]
    ]
    sConfigToml = fsBuildCosmicRayConfigToml(
        listModuleAbsPaths,
        _fsBuildMutationTestCommand(
            dictCtx, sContainerId, dictWorkflow, dictStep,
        ),
    )
    connectionDocker.fnWriteFile(
        sContainerId, posixpath.join(sWorkDirectory, "cosmic-ray.toml"),
        sConfigToml.encode("utf-8"),
    )
    connectionDocker.fnWriteFile(
        sContainerId,
        posixpath.join(sWorkDirectory, "summarizeSession.py"),
        S_SESSION_SUMMARY_SCRIPT.encode("utf-8"),
    )
    return posixpath.join(sWorkDirectory, "session.sqlite")


def _fsBuildMutationTestCommand(
    dictCtx, sContainerId, dictWorkflow, dictStep,
):
    """Return the per-mutant test command cosmic-ray will execute.

    The step's data commands re-run before the quantitative tests
    because the benchmarks read cached output files — without the
    re-run, every mutant would be graded against the unmutated
    outputs and trivially survive. Cross-step ``{StepNN.*}`` tokens
    resolve exactly as the pipeline runner resolves them. cosmic-ray
    launches the command via ``shlex.split`` (no shell), so the
    compound is wrapped in ``bash -c``.
    """
    from ..workflowManager import (
        fdictBuildStepVariables,
        fsResolveCommand,
        fsResolveStepWorkdir,
    )
    dictVars = dictCtx["variables"](sContainerId)
    dictAllVars = dict(dictVars)
    dictAllVars.update(fdictBuildStepVariables(dictWorkflow, dictVars))
    sAbsStepDir = fsResolveStepWorkdir(
        dictStep.get("sDirectory", ""), dictVars,
    )
    listParts = [f"cd {fsShellQuote(sAbsStepDir)}"]
    for sCommand in dictStep.get("saDataCommands", []):
        listParts.append(fsResolveCommand(sCommand, dictAllVars))
    listParts.append("python -m pytest -x -q tests/test_quantitative.py")
    return "bash -c " + fsShellQuote(" && ".join(listParts))


def _fdictSummarizeMutationSession(
    connectionDocker, sContainerId, sSessionPath,
    sDigest, sClassification, sCosmicRayVersion, fStarted,
):
    """Read the finished session inside the container and build the record."""
    sSummaryPath = posixpath.join(
        posixpath.dirname(sSessionPath), "summarizeSession.py",
    )
    resultSummary = connectionDocker.texecRunInContainerStreamed(
        sContainerId,
        f"python {fsShellQuote(sSummaryPath)} "
        f"{fsShellQuote(sSessionPath)}",
    )
    dictSummary = _fdictParseSummaryOutput(resultSummary)
    if dictSummary is None:
        return fdictBuildFalsificationRecord(
            S_STATUS_ERROR, sDigest, sClassification, 0, 0, 0,
            sCosmicRayVersion=sCosmicRayVersion,
            fDurationSeconds=time.monotonic() - fStarted,
            sReason="could not summarize the mutation session: "
            + _fsTailOfOutput(resultSummary),
        )
    if dictSummary["iMutantsTotal"] == 0:
        return fdictBuildFalsificationRecord(
            S_STATUS_ERROR, sDigest, sClassification, 0, 0, 0,
            sCosmicRayVersion=sCosmicRayVersion,
            fDurationSeconds=time.monotonic() - fStarted,
            sReason="cosmic-ray graded no mutants for this step",
        )
    return fdictBuildFalsificationRecord(
        S_STATUS_ATTAINED, sDigest, sClassification,
        dictSummary["iMutantsTotal"], dictSummary["iMutantsKilled"],
        dictSummary["iMutantsSurvived"],
        listSurvivors=dictSummary.get("listSurvivors", []),
        sCosmicRayVersion=sCosmicRayVersion,
        fDurationSeconds=time.monotonic() - fStarted,
    )


def _fdictParseSummaryOutput(resultSummary):
    """Return the parsed summary dict, or ``None`` on any failure."""
    if resultSummary.iExitCode != 0:
        return None
    for sLine in reversed(resultSummary.sStdout.strip().splitlines()):
        try:
            dictParsed = json.loads(sLine)
        except ValueError:
            continue
        if isinstance(dictParsed, dict) and "iMutantsTotal" in dictParsed:
            return dictParsed
    return None


def _fsTailOfOutput(resultExec, iMaxCharacters=600):
    """Return the tail of an exec result's combined output for a reason."""
    sCombined = (resultExec.sStdout + resultExec.sStderr).strip()
    return sCombined[-iMaxCharacters:]


def fnRegisterAll(app, dictCtx):
    """Register all falsification attestation routes."""
    _fnRegisterView(app, dictCtx)
    _fnRegisterRun(app, dictCtx)
