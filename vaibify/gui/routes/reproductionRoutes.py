"""HTTP routes for "Reproduce a published project", the one-shot job.

Three endpoints back the hub's third kind card:

* ``POST /api/reproductions/stage`` runs phase 1 -- clone one commit,
  validate it as reproduction-ready -- and answers with a job id and
  the redacted description the confirmation card shows. NOT a typed
  read: it starts ``git``, does network I/O and writes under the
  researcher's home, so it carries the browser-hub credential and
  refuses the agent lane.
* ``POST /api/reproductions/{sJobId}/run`` consumes the job ONCE and
  starts the rerun as a background task: obtain the pinned image
  through the published chain, re-run the snapshot in a shadow,
  compare inside it, write the reproduction report. A second run on
  the same id is refused by name.
* ``GET /api/reproductions/{sJobId}`` reports where the job has got
  to, so the card polls only while the server says the job is live.

The job has no project container. Both mutating routes are declared
``separate-authority``: the only container they ever touch is the
shadow, which ``shadowRerun`` creates itself and admits through
``commitCarrier.ftOpenDisposableContainerAdmission``, and which it
destroys with proof before this module hears the outcome. Nothing in
any response names a path on this host: the description is
``reproductionSource.fdictDescribeStagedSource``'s redacted record,
the report is the reproducer's own artefact, and the staging token
stays inside the job record.
"""

__all__ = ["fnRegisterAll"]

import asyncio
import logging
import time

from fastapi import HTTPException, Request
from pydantic import BaseModel

from ...config.connectionAvailability import fbDockerReachable
from ...docker import daemonCapacity
from ...config.mutationAdmission import fnReRaiseControlPlaneRefusal
from ...reproducibility import imageAcquisition
from ...reproducibility import reproductionReport
from ...reproducibility.environmentSnapshot import (
    fdictReadEnvironmentJson,
)
from ...reproducibility.repoFiles import ffilesEnsureRepoFiles
from ...reproducibility.reproductionSource import (
    ReproductionSourceRefusedError,
    WorkflowSelectionRequiredError,
    fbaExportStagedSnapshot,
    ffnHoldStagedSource,
    fdictDescribeStagedSource,
    fdictLoadStagedWorkflow,
    fdictStageSource,
    fnDiscardStagedSource,
    fsStagedClonePath,
)
from ...reproducibility.rerunVerification import fdictUnrunOutcome
from ...reproducibility.shadowRerun import (
    ShadowRerunRefusedError,
    fdictRerunAndVerifyFromSnapshot,
)
from .. import reproductionProgress
from ..actionCatalog import ffnAgentAction
from ..routeContext import fnRejectAgentTokenLane
from ..routeScope import (
    S_CARRIER_SEPARATE_AUTHORITY,
    ffnDeclareCarrierMode,
)


logger = logging.getLogger(__name__)

# The chain the run will follow, in the order the published script
# follows it, named for the confirmation card. Spelled once here so the
# card and the acquisition module cannot describe two different chains.
LIST_CHAIN_LINKS = [
    "registry pull",
    "archived deposit (verified against the envelope's hash)",
    "a copy already on this daemon",
]


class StageRequest(BaseModel):
    """What the researcher typed: the source, and a workflow if named."""

    sSource: str
    sWorkflowName: str = ""


class RunRequest(BaseModel):
    """The one decision the confirmation card takes: emulation or not."""

    bAllowEmulation: bool = False


def _fnRegisterStage(app, dictCtx):
    """Register POST /api/reproductions/stage."""

    @app.post("/api/reproductions/stage")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    @ffnAgentAction("stage-published-project")
    async def fdictStagePublishedProject(
        request: StageRequest, requestHttp: Request,
    ):
        fnRejectAgentTokenLane(requestHttp)
        try:
            dictJob = reproductionProgress.fdictOpenStagingJob()
        except reproductionProgress.TooManyReproductionJobsError as error:
            raise HTTPException(429, str(error)) from None
        sJobId = dictJob["sJobId"]
        try:
            dictStaged = await asyncio.to_thread(
                fdictStageSource, request.sSource,
                request.sWorkflowName or None,
                lambda sPhase: reproductionProgress.fnRecordPhase(
                    sJobId, sPhase,
                ),
            )
        except WorkflowSelectionRequiredError as error:
            reproductionProgress.fnForgetJob(sJobId)
            return {
                "bWorkflowSelectionRequired": True,
                "listWorkflowNames": list(error.listWorkflowNames),
            }
        except ReproductionSourceRefusedError as error:
            reproductionProgress.fnForgetJob(sJobId)
            raise HTTPException(422, f"Refused: {error}") from None
        except BaseException:
            reproductionProgress.fnForgetJob(sJobId)
            raise
        return _fdictAdoptStagedJob(dictCtx, sJobId, dictStaged)


def _fdictAdoptStagedJob(dictCtx, sJobId, dictStaged):
    """Hold the staged snapshot for the job's life and describe it."""
    sToken = dictStaged["sToken"]
    fnReleaseSnapshot = ffnHoldStagedSource(sToken)
    try:
        dictDescription = fdictDescribeStagedSource(sToken)
        dictDaemon = _fdictDescribeDaemon(dictCtx, dictDescription)
        reproductionProgress.fnAdoptStagedSnapshot(
            sJobId, sToken, dictDescription, dictDaemon, fnReleaseSnapshot,
        )
    except Exception:
        fnReleaseSnapshot()
        reproductionProgress.fnForgetJob(sJobId)
        fnDiscardStagedSource(sToken)
        raise
    return {
        "sJobId": sJobId,
        "dictStaged": dictDescription,
        "dictDaemon": dictDaemon,
        "listChainLinks": list(LIST_CHAIN_LINKS),
    }


def _fdictDescribeDaemon(dictCtx, dictDescription):
    """Return the daemon's architecture beside the envelope's requirement.

    Three facts, kept apart by name: the platform the envelope
    REQUIRES, and the architecture this DAEMON has. Whether the second
    matches the first decides if the emulation checkbox is offered. A
    daemon that cannot be asked is reported as unknown -- never as a
    match -- and the run route refuses without one.
    """
    sRequiredPlatform = str(dictDescription.get("sRequiredPlatform") or "")
    sRequiredArchitecture = imageAcquisition.fsArchitectureOfPlatform(
        sRequiredPlatform,
    )
    bReachable = fbDockerReachable(dictCtx.get("docker"))
    sDaemonArchitecture = (
        _fsReadDaemonArchitectureQuietly() if bReachable else ""
    )
    return {
        "bReachable": bReachable,
        "sArchitecture": sDaemonArchitecture,
        "sRequiredPlatform": sRequiredPlatform,
        "bArchitectureMatches": bool(
            sDaemonArchitecture
            and sDaemonArchitecture == sRequiredArchitecture
        ),
    }


def _fsReadDaemonArchitectureQuietly():
    """Return the daemon's architecture, or empty when it cannot be asked."""
    from ...docker import disposableContainer
    try:
        return str(daemonCapacity.fsReadDaemonArchitecture(
            disposableContainer.fdockerCreateDisposableClient(),
        ) or "")
    except Exception as error:  # noqa: BLE001 - reported as unknown, never guessed
        fnReRaiseControlPlaneRefusal(error)
        logger.info("daemon architecture unavailable: %s", error)
        return ""


def _fnRegisterRun(app, dictCtx):
    """Register POST /api/reproductions/{sJobId}/run."""

    @app.post("/api/reproductions/{sJobId}/run")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    @ffnAgentAction("run-reproduction")
    async def fdictRunReproduction(
        sJobId: str, request: RunRequest, requestHttp: Request,
    ):
        fnRejectAgentTokenLane(requestHttp)
        _fnRequireJob(sJobId)
        if not fbDockerReachable(dictCtx.get("docker")):
            raise HTTPException(
                409,
                "No Docker daemon is reachable, and a reproduction runs "
                "in a shadow container. The staged snapshot is kept; "
                "start Docker and run again.",
            )
        if not reproductionProgress.fbClaimJobForRun(
            sJobId, request.bAllowEmulation,
        ):
            raise HTTPException(
                409,
                "This staged snapshot has already been run. A "
                "reproduction is one run of one snapshot; stage the "
                "source again to run it again.",
            )
        taskWorker = asyncio.create_task(
            _fnRunReproductionWorker(sJobId, dictCtx.get("docker")),
        )
        reproductionProgress.fnRecordPhase(
            sJobId, reproductionProgress.S_PHASE_PULLING,
        )
        _fnKeepTaskReferenced(taskWorker)
        return {
            "bAccepted": True,
            "sPhase": reproductionProgress.S_PHASE_PULLING,
        }


_SET_LIVE_TASKS = set()


def _fnKeepTaskReferenced(taskWorker):
    """Hold a strong reference until the task ends, as asyncio advises."""
    _SET_LIVE_TASKS.add(taskWorker)
    taskWorker.add_done_callback(_SET_LIVE_TASKS.discard)


def _fnRequireJob(sJobId):
    """Raise 404 for a job this hub does not hold."""
    if reproductionProgress.fdictReadJobView(sJobId) is None:
        raise HTTPException(
            404,
            "No staged snapshot is held under that job. Jobs live only "
            "as long as this hub; stage the source again.",
        )


async def _fnRunReproductionWorker(sJobId, connectionDocker):
    """Obtain the image, re-run the snapshot, write the report, settle.

    Every long step runs in a worker thread so the event loop keeps
    answering the progress poll. The staging directory is removed on
    EVERY exit path -- it is scratch -- and the report is the one
    durable thing a run leaves. A crash is a failed job with its
    reason, never a stuck phase: a stuck phase is the one outcome a
    poll-driven card cannot tell from a long run.
    """
    sToken = reproductionProgress.fsStagingTokenOf(sJobId)
    fStarted = time.monotonic()
    try:
        dictAcquired = await asyncio.to_thread(
            _fdictAcquireForJob, sJobId, sToken,
        )
        reproductionProgress.fnRecordPhase(
            sJobId, reproductionProgress.S_PHASE_RUNNING,
            dictAcquired=_fdictAcquiredView(dictAcquired),
        )
        dictReport = await asyncio.to_thread(
            _fdictRerunAndReport, sJobId, sToken, dictAcquired,
            connectionDocker, fStarted,
        )
        reproductionProgress.fnSettleJob(sJobId, dictReport)
    except imageAcquisition.ImageAcquisitionRefusedError as error:
        reproductionProgress.fnFailJob(sJobId, f"Refused: {error}")
    except Exception as error:  # noqa: BLE001 - a stuck phase is worse
        fnReRaiseControlPlaneRefusal(error)
        logger.exception("reproduction job %s crashed", sJobId)
        reproductionProgress.fnFailJob(
            sJobId, f"the reproduction crashed: {type(error).__name__}",
        )
    finally:
        fnDiscardStagedSource(sToken)


def _fdictAcquireForJob(sJobId, sToken):
    """Run the published acquisition chain, reporting each link as it goes."""
    dictEnvironment = fdictReadEnvironmentJson(
        ffilesEnsureRepoFiles(fsStagedClonePath(sToken)),
    )
    dictDescription = fdictDescribeStagedSource(sToken)
    dictView = reproductionProgress.fdictReadJobView(sJobId) or {}
    return imageAcquisition.fdictAcquirePinnedImage(
        dictEnvironment, dictDescription["sRequiredPlatform"],
        lambda dictEvent: reproductionProgress.fnRecordAcquisitionEvent(
            sJobId, dictEvent,
        ),
        bool(dictView.get("bAllowEmulation")),
    )


def _fdictAcquiredView(dictAcquired):
    """Return the acquisition facts the card shows: the three platform facts."""
    return {
        sKey: dictAcquired.get(sKey)
        for sKey in (
            "sObtainedFrom", "sImageReference", "sRequiredPlatform",
            "sObtainedPlatform", "sDaemonArchitecture", "bEmulated",
        )
    }


def _fdictRerunAndReport(
    sJobId, sToken, dictAcquired, connectionDocker, fStarted,
):
    """Re-run the snapshot in a shadow and write the reproduction report.

    The same sequence ``vaibify reproduce --from --rerun`` runs, through
    the same seams, so the two lanes cannot describe two different
    reproductions. A refusal before any step ran is a report with NO
    verdict and the reason, exactly as the CLI records it.
    """
    dictSource = fdictDescribeStagedSource(sToken)
    dictWorkflow = fdictLoadStagedWorkflow(sToken)

    def fnRecordEvent(dictEvent):
        """Fold one lane event into the job record.

        SYNCHRONOUS on purpose. The shadow lane runs in a worker
        thread and calls this straight, so an ``async def`` here
        returned a coroutine nobody awaited: every step label, and
        both new phases, went to the floor with a RuntimeWarning
        nobody was reading (found by review, 2026-09-07). The record
        it writes takes a threading lock, which is what makes calling
        it from that thread correct.
        """
        reproductionProgress.fnRecordPipelineEvent(
            sJobId, dictEvent, dictWorkflow,
        )

    try:
        iArchiveBound = daemonCapacity.fdictResolveDaemonCapacity(
            connectionDocker,
        )["iArchiveTotalBytes"]
        dictOutcome = fdictRerunAndVerifyFromSnapshot(
            connectionDocker,
            fbaExportStagedSnapshot(sToken, iArchiveBound), dictAcquired,
            dictWorkflow, dictSource["sWorkflowPath"],
            dictSource["sRepositoryName"],
            sResourceName=f"reproduction-{sToken}",
            fnStatusCallback=fnRecordEvent,
        )
    except ShadowRerunRefusedError as error:
        dictOutcome = fdictUnrunOutcome(str(error))
    dictReport = reproductionReport.fdictBuildReproductionReport(
        dictSource, dictAcquired, dictOutcome,
        reproductionReport.fdictRecheckObtainedImage(sToken, dictAcquired),
        time.monotonic() - fStarted,
    )
    reproductionReport.fsWriteReproductionReport(dictReport)
    dictReport["sVerdictRendered"] = reproductionReport.fsRenderVerdict(
        dictReport,
    )
    return dictReport


def _fnRegisterDiscard(app, dictCtx):
    """Register POST /api/reproductions/{sJobId}/discard."""
    del dictCtx

    @app.post("/api/reproductions/{sJobId}/discard")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    @ffnAgentAction("discard-reproduction")
    async def fdictDiscardReproduction(sJobId: str, requestHttp: Request):
        """Delete a staged snapshot the researcher is not going to run.

        Closing the card used to hide it and leave the clone: a staged
        job holds its own live lock, which is exactly what keeps the
        staging sweep off it, so an abandoned confirmation kept a whole
        repository until the hub restarted (found by review,
        2026-09-07). A job already RUNNING is refused rather than
        yanked out from under its shadow container; it discards its own
        staging when it settles.
        """
        fnRejectAgentTokenLane(requestHttp)
        _fnRequireJob(sJobId)
        if reproductionProgress.fbJobIsLive(sJobId):
            raise HTTPException(
                409,
                "This reproduction is running. It discards its staged "
                "snapshot when it settles.",
            )
        reproductionProgress.fnDiscardJob(sJobId)
        return {"bDiscarded": True}


def _fnRegisterReportRead(app, dictCtx):
    """Register GET /api/reproductions/reports/{sReportId}."""
    del dictCtx

    @app.get("/api/reproductions/reports/{sReportId}")
    async def fdictReadReproductionReport(
        sReportId: str, requestHttp: Request,
    ):
        """Serve one reproduction report so the result can link to it.

        Browser-only, like every route in this module: the report
        describes a run on this researcher's own machine. The id is
        validated as a bare name by the reader it delegates to -- it
        indexes a directory, and a caller must never be able to spell
        a path.
        """
        fnRejectAgentTokenLane(requestHttp)
        try:
            return reproductionReport.fdictReadReproductionReport(sReportId)
        except LookupError:
            raise HTTPException(
                404, "No reproduction report is stored under that id.",
            ) from None


def _fnRegisterProgress(app, dictCtx):
    """Register GET /api/reproductions/{sJobId}."""
    del dictCtx

    @app.get("/api/reproductions/{sJobId}")
    async def fdictReadReproductionProgress(sJobId: str):
        dictView = reproductionProgress.fdictReadJobView(sJobId)
        if dictView is None:
            raise HTTPException(
                404, "No reproduction job is held under that id.",
            )
        dictView["bLive"] = reproductionProgress.fbJobIsLive(sJobId)
        return dictView


def fnRegisterAll(app, dictCtx):
    """Register the reproduction routes."""
    _fnRegisterStage(app, dictCtx)
    _fnRegisterRun(app, dictCtx)
    _fnRegisterDiscard(app, dictCtx)
    _fnRegisterReportRead(app, dictCtx)
    _fnRegisterProgress(app, dictCtx)
