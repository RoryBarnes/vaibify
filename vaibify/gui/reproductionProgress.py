"""In-process record of each "Reproduce a published project" job.

A reproduction is a one-shot dashboard job: stage a snapshot, obtain
the pinned image, re-run the snapshot in a shadow, write a report. The
hub needs one record per job so the run route can consume it ONCE,
the progress route can say where it has got to, and the staged
snapshot can be held for exactly the job's life.

WHY THIS IS NOT ``archiveProgress``
-----------------------------------

That registry is the environment-DEPOSIT record: keyed by container
id, with the deposit's phases. A reproduction has no container of its
own -- the shadow is created and destroyed inside the job -- and its
phases are the acquisition chain's and the rerun's. The frontend
borrows the deposit row's VISUAL shape (a phase word and a byte
count); the record is this module's, because keying a job by a
container it does not have would be the name == id bug wearing a
different hat.

WHAT ITS LIFETIME MEANS
-----------------------

In-process, lost on a hub restart, deliberately. The running task
cannot outlive the process, and a record claiming a rerun is still
under way after a restart would be a lie the next poll repeats. A
settled job keeps its report id for a while so the researcher can
read the result after the tab settles; the report itself is the
durable artefact and lives on disk under its own retention.

THE PHASES, AND WHICH ARE OBSERVABLE
------------------------------------

``staging`` covers the clone AND the six validation rules, because the
staging entry point runs both in one call and reports no boundary
between them. ``pulling``, ``downloading`` (with bytes) and
``loading`` come from the acquisition chain's own status events;
``running`` (with the step) from the pipeline's. The comparison and
the teardown happen inside the rerun seam with no event of their own,
so they are reported together as ``finishing`` rather than invented
from a timer. A phase is only ever written from an event that
happened.
"""

__all__ = [
    "S_PHASE_DOWNLOADING",
    "S_PHASE_FAILED",
    "S_PHASE_FINISHING",
    "S_PHASE_LOADING",
    "S_PHASE_PULLING",
    "S_PHASE_RUNNING",
    "S_PHASE_SETTLED",
    "S_PHASE_STAGED",
    "S_PHASE_STAGING",
    "T_LIVE_PHASES",
    "fbClaimJobForRun",
    "fbJobIsLive",
    "fdictCreateJob",
    "fdictReadJobView",
    "flistSweepSettledJobs",
    "fnFailJob",
    "fnForgetJob",
    "fnRecordAcquisitionEvent",
    "fnRecordPhase",
    "fnRecordPipelineEvent",
    "fnSettleJob",
    "fsStagingTokenOf",
]

import secrets
import threading
import time


S_PHASE_STAGING = "staging"
S_PHASE_STAGED = "staged"
S_PHASE_PULLING = "pulling"
S_PHASE_DOWNLOADING = "downloading"
S_PHASE_LOADING = "loading"
S_PHASE_RUNNING = "running"
S_PHASE_FINISHING = "finishing"
S_PHASE_SETTLED = "settled"
S_PHASE_FAILED = "failed"

# A job in one of these is under way; the tab polls only while the
# server reports one of them, and disarms otherwise. ``staged`` is
# NOT live: a staged job is waiting for the researcher, not working.
T_LIVE_PHASES = (
    S_PHASE_STAGING, S_PHASE_PULLING, S_PHASE_DOWNLOADING,
    S_PHASE_LOADING, S_PHASE_RUNNING, S_PHASE_FINISHING,
)

# How long a settled or failed job stays readable after it settles.
F_SETTLED_JOB_RETENTION_SECONDS = 60 * 60

# The acquisition chain's link names, mapped onto the phase a
# researcher sees while that link is being tried. Read from the
# chain's own events; a link this table does not name keeps the
# current phase rather than inventing one.
_DICT_PHASE_BY_ACQUISITION_EVENT = {
    "pulling": S_PHASE_PULLING,
    "downloading": S_PHASE_DOWNLOADING,
    "loading": S_PHASE_LOADING,
}

# The fields a client may see. The staging token, the held lock and
# the task object stay behind: the token names a directory on this
# host, and a record is not a handle.
_T_PUBLIC_FIELDS = (
    "sJobId", "sPhase", "sStepLabel", "sStepName", "iBytes",
    "iTotalBytes", "sCurrentLink", "listAttempts", "dictStaged",
    "dictDaemon", "dictAcquired", "sReportId", "dictReport",
    "sFailure", "bConsumed", "bAllowEmulation",
)

DICT_JOBS = {}
_LOCK_JOBS = threading.Lock()


def fdictCreateJob(sToken, dictStaged, dictDaemon, fnReleaseSnapshot):
    """Register a freshly staged snapshot as a job awaiting its run.

    ``fnReleaseSnapshot`` releases the staged snapshot's live lock; it
    is called when the job settles, fails or is forgotten, which is
    what keeps the sweep off a snapshot a job still needs.
    """
    flistSweepSettledJobs()
    sJobId = secrets.token_hex(8)
    dictJob = {
        "sJobId": sJobId,
        "sToken": sToken,
        "sPhase": S_PHASE_STAGED,
        "sStepLabel": "",
        "sStepName": "",
        "iBytes": 0,
        "iTotalBytes": 0,
        "sCurrentLink": "",
        "listAttempts": [],
        "dictStaged": dict(dictStaged),
        "dictDaemon": dict(dictDaemon),
        "dictAcquired": None,
        "sReportId": "",
        "dictReport": None,
        "sFailure": "",
        "bConsumed": False,
        "bAllowEmulation": False,
        "fnReleaseSnapshot": fnReleaseSnapshot,
        "taskWorker": None,
        "fSettledAtMonotonic": 0.0,
    }
    with _LOCK_JOBS:
        DICT_JOBS[sJobId] = dictJob
    return dictJob


def fdictReadJobView(sJobId):
    """Return the client-visible copy of a job, or ``None`` if unknown."""
    with _LOCK_JOBS:
        dictJob = DICT_JOBS.get(sJobId)
        if dictJob is None:
            return None
        return {
            sField: _fgenericCopyField(dictJob.get(sField))
            for sField in _T_PUBLIC_FIELDS
        }


def _fgenericCopyField(jsonValue):
    """Return a detached copy of a job field so a view cannot be mutated through."""
    if isinstance(jsonValue, dict):
        return dict(jsonValue)
    if isinstance(jsonValue, list):
        return list(jsonValue)
    return jsonValue


def fsStagingTokenOf(sJobId):
    """Return the staging token a job holds; ``LookupError`` if unknown."""
    with _LOCK_JOBS:
        dictJob = DICT_JOBS.get(sJobId)
        if dictJob is None:
            raise LookupError(f"no reproduction job {sJobId!r}")
        return dictJob["sToken"]


def fbClaimJobForRun(sJobId, bAllowEmulation, taskWorker=None):
    """Consume the job for its one run; False when it was already run.

    The check and the mark happen under the lock, so two Run clicks
    that race cannot both start a shadow for one snapshot -- the
    second is refused by name, never silently doubled.
    """
    with _LOCK_JOBS:
        dictJob = DICT_JOBS.get(sJobId)
        if dictJob is None or dictJob["bConsumed"]:
            return False
        dictJob["bConsumed"] = True
        dictJob["bAllowEmulation"] = bool(bAllowEmulation)
        dictJob["taskWorker"] = taskWorker
        return True


def fnRecordPhase(sJobId, sPhase, **dictFields):
    """Move a job to ``sPhase``, carrying any progress fields given."""
    with _LOCK_JOBS:
        dictJob = DICT_JOBS.get(sJobId)
        if dictJob is None:
            return
        dictJob["sPhase"] = sPhase
        for sField, jsonValue in dictFields.items():
            if sField in _T_PUBLIC_FIELDS:
                dictJob[sField] = jsonValue


def fnRecordAcquisitionEvent(sJobId, dictEvent):
    """Fold one acquisition-chain event into the job's progress."""
    sEventPhase = str((dictEvent or {}).get("sPhase") or "")
    with _LOCK_JOBS:
        dictJob = DICT_JOBS.get(sJobId)
        if dictJob is None:
            return
        if sEventPhase == "attempt":
            dictJob["listAttempts"].append({
                "sLink": dictEvent.get("sLink", ""),
                "bSucceeded": bool(dictEvent.get("bSucceeded")),
                "sDetail": dictEvent.get("sDetail", ""),
            })
            dictJob["sCurrentLink"] = str(dictEvent.get("sLink") or "")
            return
        sPhase = _DICT_PHASE_BY_ACQUISITION_EVENT.get(sEventPhase)
        if sPhase is None:
            return
        dictJob["sPhase"] = sPhase
        if sEventPhase == "downloading":
            dictJob["iBytes"] = int(dictEvent.get("iBytes") or 0)
            dictJob["iTotalBytes"] = int(dictEvent.get("iTotalBytes") or 0)


def fnRecordPipelineEvent(sJobId, dictEvent, dictWorkflow):
    """Fold one pipeline status event into the job's running phase."""
    if str((dictEvent or {}).get("sType") or "") != "stepStarted":
        return
    iStepNumber = int(dictEvent.get("iStepNumber") or 0)
    listSteps = (dictWorkflow or {}).get("listSteps") or []
    dictStep = (
        listSteps[iStepNumber - 1]
        if 0 < iStepNumber <= len(listSteps) else {}
    )
    fnRecordPhase(
        sJobId, S_PHASE_RUNNING,
        sStepLabel=str(dictStep.get("sLabel") or dictEvent.get("sLabel") or ""),
        sStepName=str(dictStep.get("sName") or ""),
    )


def fnSettleJob(sJobId, dictReport):
    """Record the finished report and release the staged snapshot."""
    _fnCloseJob(sJobId, S_PHASE_SETTLED, dictReport=dictReport,
                sReportId=str(dictReport.get("sReportId") or ""))


def fnFailJob(sJobId, sFailure):
    """Record why the job could not finish and release the snapshot."""
    _fnCloseJob(sJobId, S_PHASE_FAILED, sFailure=str(sFailure))


def _fnCloseJob(sJobId, sPhase, **dictFields):
    """Settle or fail a job, closing the hold on its snapshot."""
    with _LOCK_JOBS:
        dictJob = DICT_JOBS.get(sJobId)
        if dictJob is None:
            return
        dictJob["sPhase"] = sPhase
        dictJob["fSettledAtMonotonic"] = time.monotonic()
        dictJob.update(dictFields)
        fnReleaseSnapshot = dictJob.pop("fnReleaseSnapshot", None)
    _fnReleaseHold(fnReleaseSnapshot)


def fbJobIsLive(sJobId):
    """Return True while a job is between its run and its settlement."""
    with _LOCK_JOBS:
        dictJob = DICT_JOBS.get(sJobId)
        return bool(dictJob) and dictJob["sPhase"] in T_LIVE_PHASES


def fnForgetJob(sJobId):
    """Drop a job's record, releasing its snapshot if still held."""
    with _LOCK_JOBS:
        dictJob = DICT_JOBS.pop(sJobId, None)
    if dictJob is not None:
        _fnReleaseHold(dictJob.get("fnReleaseSnapshot"))


def flistSweepSettledJobs(fMaxAgeSeconds=F_SETTLED_JOB_RETENTION_SECONDS):
    """Forget settled and failed jobs older than the retention; return ids."""
    fCutoff = time.monotonic() - fMaxAgeSeconds
    with _LOCK_JOBS:
        listExpired = [
            sJobId for sJobId, dictJob in DICT_JOBS.items()
            if dictJob["sPhase"] in (S_PHASE_SETTLED, S_PHASE_FAILED)
            and dictJob["fSettledAtMonotonic"] < fCutoff
        ]
    for sJobId in listExpired:
        fnForgetJob(sJobId)
    return listExpired


def _fnReleaseHold(fnReleaseSnapshot):
    """Release the hold on a staged snapshot, tolerating one already released."""
    if fnReleaseSnapshot is None:
        return
    try:
        fnReleaseSnapshot()
    except Exception:  # noqa: BLE001 - releasing must never mask the outcome
        pass
