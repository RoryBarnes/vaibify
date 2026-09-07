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
    async def fdictStagePublishedProject(
        request: StageRequest, requestHttp: Request,
    ):
        fnRejectAgentTokenLane(requestHttp)
        try:
            dictStaged = await asyncio.to_thread(
                fdictStageSource, request.sSource,
                request.sWorkflowName or None,
            )
        except WorkflowSelectionRequiredError as error:
            return {
                "bWorkflowSelectionRequired": True,
                "listWorkflowNames": list(error.listWorkflowNames),
            }
        except ReproductionSourceRefusedError as error:
            raise HTTPException(422, f"Refused: {error}") from None
        return _fdictRegisterStagedJob(dictCtx, dictStaged)


def _fdictRegisterStagedJob(dictCtx, dictStaged):
    """Hold the staged snapshot for the job's life and describe it."""
    sToken = dictStaged["sToken"]
    fnReleaseSnapshot = ffnHoldStagedSource(sToken)
    try:
        dictDescription = fdictDescribeStagedSource(sToken)
        dictDaemon = _fdictDescribeDaemon(dictCtx, dictDescription)
        dictJob = reproductionProgress.fdictCreateJob(
            sToken, dictDescription, dictDaemon, fnReleaseSnapshot,
        )
    except Exception:
        fnReleaseSnapshot()
        fnDiscardStagedSource(sToken)
        raise
    return {
        "sJobId": dictJob["sJobId"],
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
        return str(disposableContainer.fsReadDaemonArchitecture(
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

    async def fnRecordEvent(dictEvent):
        reproductionProgress.fnRecordPipelineEvent(
            sJobId, dictEvent, dictWorkflow,
        )

    try:
        dictOutcome = fdictRerunAndVerifyFromSnapshot(
            connectionDocker, fbaExportStagedSnapshot(sToken), dictAcquired,
            dictWorkflow, dictSource["sWorkflowPath"],
            dictSource["sRepositoryName"],
            sResourceName=f"reproduction-{sToken}",
            fnStatusCallback=fnRecordEvent,
        )
    except ShadowRerunRefusedError as error:
        dictOutcome = fdictUnrunOutcome(str(error))
    reproductionProgress.fnRecordPhase(
        sJobId, reproductionProgress.S_PHASE_FINISHING,
    )
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
    _fnRegisterProgress(app, dictCtx)
