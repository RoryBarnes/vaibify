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
    "S_PHASE_VALIDATING",
    "S_PHASE_COMPARING",
    "S_PHASE_TEARING_DOWN",
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
    "TooManyReproductionJobsError",
    "fbHubHoldsLiveReproduction",
    "fdictOpenStagingJob",
    "fnAdoptStagedSnapshot",
    "flistSweepExpiredJobs",
    "fnDiscardJob",
    "fnSweepOnTheHubsTick",
    "fnFailJob",
    "fnForgetJob",
    "fnRecordAcquisitionEvent",
    "fnRecordPhase",
    "fnRecordPipelineEvent",
    "fnSettleJob",
    "fsStagingTokenOf",
]

import logging
import secrets
import threading
import time


logger = logging.getLogger(__name__)


S_PHASE_STAGING = "staging"
S_PHASE_VALIDATING = "validating"
S_PHASE_STAGED = "staged"
S_PHASE_PULLING = "pulling"
S_PHASE_DOWNLOADING = "downloading"
S_PHASE_LOADING = "loading"
S_PHASE_RUNNING = "running"
S_PHASE_COMPARING = "comparing"
S_PHASE_TEARING_DOWN = "tearing-down"
S_PHASE_SETTLED = "settled"
S_PHASE_FAILED = "failed"

# A job in one of these is under way; the tab polls only while the
# server reports one of them, and disarms otherwise. ``staged`` is
# NOT live: a staged job is waiting for the researcher, not working.
T_LIVE_PHASES = (
    S_PHASE_STAGING, S_PHASE_VALIDATING, S_PHASE_PULLING,
    S_PHASE_DOWNLOADING, S_PHASE_LOADING, S_PHASE_RUNNING,
    S_PHASE_COMPARING, S_PHASE_TEARING_DOWN,
)

# How long a settled or failed job stays readable after it settles.
F_SETTLED_JOB_RETENTION_SECONDS = 60 * 60

# How long a STAGED job nobody ran keeps its clone. A staged snapshot
# holds its own live lock, which is what keeps the staging TTL sweep
# off a job still in use -- so without an expiry of its own, a
# researcher who stages and then closes the card leaves a clone on
# disk until the hub restarts, and repeating that fills the disk one
# repository at a time (found by review, 2026-09-07). Long enough to
# read a confirmation card and think about it; far shorter than the
# staging TTL, which is the backstop for a hub that died.
F_STAGED_JOB_RETENTION_SECONDS = 30 * 60

# How many jobs may hold a staged clone at once. Each may occupy up to
# the staging ceiling, so this is the number that bounds the disk.
I_MAX_CONCURRENT_JOBS = 3

# The acquisition chain's link names, mapped onto the phase a
# researcher sees while that link is being tried. Read from the
# chain's own events; a link this table does not name keeps the
# current phase rather than inventing one.
# The shadow lane's own events, mapped onto the phase a researcher
# sees. Both are emitted from inside the lane, at the moment the thing
# they name begins -- a phase set by the caller AFTER the lane returns
# would describe work that is already over.
_DICT_PHASE_BY_LANE_EVENT = {
    "comparingOutputs": S_PHASE_COMPARING,
    "tearingDownShadow": S_PHASE_TEARING_DOWN,
}

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


class TooManyReproductionJobsError(RuntimeError):
    """This hub already holds as many staged snapshots as it allows."""


def fdictOpenStagingJob():
    """Register a job BEFORE its snapshot exists, in the staging phase.

    Staging clones a whole repository and then applies the six rules,
    which is the longest a researcher waits without a container being
    involved. Opening the record first is what makes ``staging`` and
    ``validating`` observable at all: a concurrent poll sees them, and
    the concurrency cap counts a stage in flight rather than only the
    snapshots already on disk.
    """
    return _fdictRegisterJob("", {}, {}, None, S_PHASE_STAGING)


def fnAdoptStagedSnapshot(
    sJobId, sToken, dictStaged, dictDaemon, fnReleaseSnapshot,
):
    """Attach a finished snapshot to the job that was staging it.

    Returns nothing: a caller that wants the view asks for it. A job
    that vanished mid-stage (swept, or dismissed from another tab)
    releases the hold rather than resurrecting a record nobody holds.
    """
    with _LOCK_JOBS:
        dictJob = DICT_JOBS.get(sJobId)
        if dictJob is None:
            _fnReleaseHold(fnReleaseSnapshot)
            return
        dictJob.update({
            "sToken": sToken,
            "sPhase": S_PHASE_STAGED,
            "dictStaged": dict(dictStaged),
            "dictDaemon": dict(dictDaemon),
            "fnReleaseSnapshot": fnReleaseSnapshot,
            "fStagedAtMonotonic": time.monotonic(),
        })


def fdictCreateJob(sToken, dictStaged, dictDaemon, fnReleaseSnapshot):
    """Register a freshly staged snapshot as a job awaiting its run.

    ``fnReleaseSnapshot`` releases the staged snapshot's live lock; it
    is called when the job settles, fails or is forgotten, which is
    what keeps the sweep off a snapshot a job still needs.

    Raises :class:`TooManyReproductionJobsError` when this hub already
    holds ``I_MAX_CONCURRENT_JOBS`` snapshots, counted AFTER the sweep
    so an expired one never blocks a new one.
    """
    return _fdictRegisterJob(
        sToken, dictStaged, dictDaemon, fnReleaseSnapshot, S_PHASE_STAGED,
    )


def _fdictRegisterJob(
    sToken, dictStaged, dictDaemon, fnReleaseSnapshot, sPhase,
):
    """Register one job in ``sPhase``, refusing over the concurrency cap.

    The count and the insert happen under ONE acquisition of the lock.
    They used to be two, and a cap enforced across a gap is not a cap:
    twelve registrations released into the gap together all read the
    same count and all inserted, three times over the limit, each
    entitled to a clone (found by review, 2026-09-07). The sweep runs
    BEFORE the lock, because it takes the lock itself; an expired job
    it misses only makes this refuse sooner than it must.
    """
    flistSweepExpiredJobs()
    sJobId = secrets.token_hex(8)
    with _LOCK_JOBS:
        iHolding = sum(
            1 for dictOther in DICT_JOBS.values()
            if dictOther["sPhase"] not in (S_PHASE_SETTLED, S_PHASE_FAILED)
        )
        if iHolding >= I_MAX_CONCURRENT_JOBS:
            raise TooManyReproductionJobsError(
                f"this hub is already holding {iHolding} staged "
                f"snapshots, the most it keeps at once "
                f"({I_MAX_CONCURRENT_JOBS}). Run or dismiss one before "
                "staging another."
            )
        DICT_JOBS[sJobId] = _fdictBuildJobRecord(
            sJobId, sToken, dictStaged, dictDaemon, fnReleaseSnapshot,
            sPhase,
        )
    return fdictReadJobView(sJobId)


def _fdictBuildJobRecord(
    sJobId, sToken, dictStaged, dictDaemon, fnReleaseSnapshot, sPhase,
):
    """Return one job's record. Called with the registry lock HELD."""
    return {
        "sJobId": sJobId,
        "sToken": sToken,
        "sPhase": sPhase,
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
        "fStagedAtMonotonic": time.monotonic(),
        "fSettledAtMonotonic": 0.0,
    }


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

    The claim also moves the job into a LIVE phase, in the same
    acquisition. It used to leave it ``staged`` until the route set
    ``pulling`` after creating the task, and in that window the job
    was claimed but not live -- so a Discard arriving there passed the
    liveness check and deleted the snapshot out from under a run that
    had already been authorized. The frontend made the window
    reachable, because its own "running" flag is set only when the Run
    request RETURNS (found by review, 2026-09-07).
    """
    with _LOCK_JOBS:
        dictJob = DICT_JOBS.get(sJobId)
        if dictJob is None or dictJob["bConsumed"]:
            return False
        dictJob["bConsumed"] = True
        dictJob["bAllowEmulation"] = bool(bAllowEmulation)
        dictJob["taskWorker"] = taskWorker
        dictJob["sPhase"] = S_PHASE_PULLING
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
    """Fold one pipeline status event into the job's phase."""
    sType = str((dictEvent or {}).get("sType") or "")
    sPhase = _DICT_PHASE_BY_LANE_EVENT.get(sType)
    if sPhase is not None:
        fnRecordPhase(sJobId, sPhase)
        return
    if sType != "stepStarted":
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


def flistSweepExpiredJobs(
    fMaxAgeSeconds=F_SETTLED_JOB_RETENTION_SECONDS,
    fStagedMaxAgeSeconds=F_STAGED_JOB_RETENTION_SECONDS,
):
    """Forget jobs past their retention and discard their clones.

    TWO retentions, because the two states cost different things. A
    settled job holds a report id and no disk; a STAGED job nobody ran
    holds a whole repository and its live lock, so it expires sooner
    and its snapshot is discarded rather than merely released.
    """
    fNow = time.monotonic()
    with _LOCK_JOBS:
        listExpired = [
            sJobId for sJobId, dictJob in DICT_JOBS.items()
            if _fbJobIsPastRetention(
                dictJob, fNow, fMaxAgeSeconds, fStagedMaxAgeSeconds,
            )
        ]
    for sJobId in listExpired:
        fnDiscardJob(sJobId)
    return listExpired


def _fbJobIsPastRetention(
    dictJob, fNow, fMaxAgeSeconds, fStagedMaxAgeSeconds,
):
    """Return True when a job has outlived the retention for its state."""
    if dictJob["sPhase"] in (S_PHASE_SETTLED, S_PHASE_FAILED):
        return dictJob["fSettledAtMonotonic"] < fNow - fMaxAgeSeconds
    if dictJob["sPhase"] == S_PHASE_STAGED:
        return dictJob["fStagedAtMonotonic"] < fNow - fStagedMaxAgeSeconds
    return False


def fnDiscardJob(sJobId, fnDiscardSnapshot=None):
    """Forget a job and DELETE its staged snapshot.

    ``fnForgetJob`` releases the hold and leaves the clone for the
    staging TTL; this removes it now, which is what a dismissed card
    and an expired staged job both want. The discard is late-bound so
    this module keeps its one job -- being the record -- and the
    caller supplies the deleter; the default is the staging module's.
    """
    sToken = fsStagingTokenOf(sJobId)
    fnForgetJob(sJobId)
    if not sToken:
        return
    if fnDiscardSnapshot is None:
        from vaibify.reproducibility.reproductionSource import (
            fnDiscardStagedSource,
        )
        fnDiscardSnapshot = fnDiscardStagedSource
    try:
        fnDiscardSnapshot(sToken)
    except Exception:  # noqa: BLE001 - a discard must never mask an outcome
        pass


def fnSweepOnTheHubsTick():
    """Expire abandoned staged jobs, swallowing any failure.

    Called from the hub's periodic sweep. The expiry used to be
    evaluated only when the NEXT job was registered, so a researcher
    who staged one snapshot and walked away kept a whole repository
    until the hub restarted -- the check existed and nothing ran it
    (found by review, 2026-09-07). It lives HERE rather than in the
    lifespan module because the retention policy is this record's, and
    a sweep must never be able to end the loop that calls it.
    """
    try:
        flistSweepExpiredJobs()
    except Exception:  # noqa: BLE001 - a sweep must not end the tick
        logger.warning(
            "Could not sweep expired reproduction jobs", exc_info=True,
        )


def fbHubHoldsLiveReproduction():
    """Return True while any job is between its run and its settlement.

    The idle watchdog's veto reads this. A reproduction holds no
    container and no WebSocket -- the same blind spot the Agent
    Council has, and for the same reason -- so without it a closed tab
    lets the activity clock go stale and the hub SIGTERMs itself in
    the middle of somebody's two-hour reproduction. Fail-closed is not
    available here and not needed: the record is in this process.
    """
    with _LOCK_JOBS:
        return any(
            dictJob["sPhase"] in T_LIVE_PHASES
            for dictJob in DICT_JOBS.values()
        )


def _fnReleaseHold(fnReleaseSnapshot):
    """Release the hold on a staged snapshot, tolerating one already released."""
    if fnReleaseSnapshot is None:
        return
    try:
        fnReleaseSnapshot()
    except Exception:  # noqa: BLE001 - releasing must never mask the outcome
        pass
