"""The reproduction job record: one run per snapshot, and no host path.

The record is what the run route consumes and the progress route
reads, so its two guarantees are asserted here directly: a job is
claimed for its run exactly once, and the view a client receives never
carries the staging token or the release handle.
"""

from unittest.mock import patch

import pytest

from vaibify.gui import reproductionProgress


@pytest.fixture(autouse=True)
def fixtureClearJobs():
    """Every test starts and ends with an empty job registry."""
    reproductionProgress.DICT_JOBS.clear()
    yield
    reproductionProgress.DICT_JOBS.clear()


def _fdictCreate(listReleases=None):
    """Register one staged job whose releaser records that it was called."""
    listReleases = listReleases if listReleases is not None else []
    return reproductionProgress.fdictCreateJob(
        "snapshotabc123", {"sRepositoryName": "project"},
        {"bReachable": True}, lambda: listReleases.append("released"),
    )


@pytest.mark.falsification
def test_a_job_is_claimed_for_its_run_exactly_once():
    """Kills: dropping the ``bConsumed`` check from the claim."""
    dictJob = _fdictCreate()
    assert reproductionProgress.fbClaimJobForRun(dictJob["sJobId"], False)
    assert not reproductionProgress.fbClaimJobForRun(dictJob["sJobId"], False)
    assert not reproductionProgress.fbClaimJobForRun("no-such-job", False)


@pytest.mark.falsification
def test_the_client_view_never_carries_the_staging_token():
    """Kills: adding ``sToken`` to the public field tuple."""
    dictJob = _fdictCreate()
    dictView = reproductionProgress.fdictReadJobView(dictJob["sJobId"])
    assert "sToken" not in dictView
    assert "fnReleaseSnapshot" not in dictView
    assert "taskWorker" not in dictView
    assert "snapshotabc123" not in repr(dictView)
    assert dictView["sPhase"] == reproductionProgress.S_PHASE_STAGED
    assert dictView["bConsumed"] is False


def test_the_view_is_a_detached_copy():
    dictJob = _fdictCreate()
    dictView = reproductionProgress.fdictReadJobView(dictJob["sJobId"])
    dictView["dictStaged"]["sRepositoryName"] = "tampered"
    dictView["listAttempts"].append("tampered")
    dictAgain = reproductionProgress.fdictReadJobView(dictJob["sJobId"])
    assert dictAgain["dictStaged"]["sRepositoryName"] == "project"
    assert dictAgain["listAttempts"] == []


def test_acquisition_events_move_the_phase_and_carry_bytes():
    dictJob = _fdictCreate()
    sJobId = dictJob["sJobId"]
    reproductionProgress.fnRecordAcquisitionEvent(
        sJobId, {"sPhase": "attempt", "sLink": "registry pull",
                 "bSucceeded": False, "sDetail": "no route to host"},
    )
    reproductionProgress.fnRecordAcquisitionEvent(
        sJobId, {"sPhase": "downloading", "iBytes": 512, "iTotalBytes": 4096},
    )
    dictView = reproductionProgress.fdictReadJobView(sJobId)
    assert dictView["sPhase"] == reproductionProgress.S_PHASE_DOWNLOADING
    assert (dictView["iBytes"], dictView["iTotalBytes"]) == (512, 4096)
    assert dictView["listAttempts"][0]["sLink"] == "registry pull"
    reproductionProgress.fnRecordAcquisitionEvent(
        sJobId, {"sPhase": "something-new"},
    )
    assert reproductionProgress.fdictReadJobView(sJobId)["sPhase"] == (
        reproductionProgress.S_PHASE_DOWNLOADING
    ), "an event the table does not name keeps the current phase"


def test_a_step_start_names_the_step_from_the_workflow():
    dictJob = _fdictCreate()
    dictWorkflow = {"listSteps": [
        {"sName": "Make Numbers", "sLabel": "A01"},
        {"sName": "Plot Numbers", "sLabel": "A02"},
    ]}
    reproductionProgress.fnRecordPipelineEvent(
        dictJob["sJobId"], {"sType": "stepStarted", "iStepNumber": 2},
        dictWorkflow,
    )
    dictView = reproductionProgress.fdictReadJobView(dictJob["sJobId"])
    assert dictView["sPhase"] == reproductionProgress.S_PHASE_RUNNING
    assert (dictView["sStepLabel"], dictView["sStepName"]) == (
        "A02", "Plot Numbers",
    )
    reproductionProgress.fnRecordPipelineEvent(
        dictJob["sJobId"], {"sType": "output", "sLine": "x"}, dictWorkflow,
    )
    assert reproductionProgress.fdictReadJobView(
        dictJob["sJobId"],
    )["sStepLabel"] == "A02"


@pytest.mark.falsification
def test_settling_releases_the_snapshot_hold_exactly_once():
    """Kills: dropping the release call from ``_fnCloseJob``."""
    listReleases = []
    dictJob = _fdictCreate(listReleases)
    assert not reproductionProgress.fbJobIsLive(dictJob["sJobId"])
    reproductionProgress.fnRecordPhase(
        dictJob["sJobId"], reproductionProgress.S_PHASE_RUNNING,
    )
    assert reproductionProgress.fbJobIsLive(dictJob["sJobId"])
    reproductionProgress.fnSettleJob(
        dictJob["sJobId"], {"sReportId": "report01", "sVerdict": "reproduced"},
    )
    assert listReleases == ["released"]
    assert not reproductionProgress.fbJobIsLive(dictJob["sJobId"])
    dictView = reproductionProgress.fdictReadJobView(dictJob["sJobId"])
    assert dictView["sReportId"] == "report01"
    assert dictView["sPhase"] == reproductionProgress.S_PHASE_SETTLED
    reproductionProgress.fnForgetJob(dictJob["sJobId"])
    assert listReleases == ["released"], "a settled job is not released twice"


def test_failing_records_the_reason_and_releases():
    listReleases = []
    dictJob = _fdictCreate(listReleases)
    reproductionProgress.fnFailJob(dictJob["sJobId"], "Refused: no link served")
    dictView = reproductionProgress.fdictReadJobView(dictJob["sJobId"])
    assert dictView["sPhase"] == reproductionProgress.S_PHASE_FAILED
    assert dictView["sFailure"] == "Refused: no link served"
    assert listReleases == ["released"]


def test_the_sweep_forgets_only_old_settled_jobs():
    dictSettled = _fdictCreate()
    dictLive = _fdictCreate()
    reproductionProgress.fnSettleJob(dictSettled["sJobId"], {"sReportId": "r"})
    reproductionProgress.fnRecordPhase(
        dictLive["sJobId"], reproductionProgress.S_PHASE_RUNNING,
    )
    assert reproductionProgress.flistSweepExpiredJobs(fMaxAgeSeconds=-1) == [
        dictSettled["sJobId"],
    ]
    assert reproductionProgress.fdictReadJobView(dictLive["sJobId"]) is not None
    assert reproductionProgress.fdictReadJobView(dictSettled["sJobId"]) is None


# ---------------------------------------------------------------------
# What a job costs while nobody runs it (review, 2026-09-07)
# ---------------------------------------------------------------------


@pytest.mark.falsification
def test_a_staged_job_nobody_ran_expires_and_its_clone_is_discarded():
    """A staged job holds the lock that keeps the staging sweep off it.

    Kills: expiring only settled and failed jobs.
    """
    listDiscarded = []
    dictJob = _fdictCreate()
    dictSettled = _fdictCreate()
    reproductionProgress.fnSettleJob(
        dictSettled["sJobId"], {"sReportId": "abc"},
    )
    with patch.object(
        reproductionProgress, "fnDiscardJob",
        side_effect=lambda sJobId: listDiscarded.append(sJobId),
    ):
        listExpired = reproductionProgress.flistSweepExpiredJobs(
            fMaxAgeSeconds=10_000, fStagedMaxAgeSeconds=-1,
        )
    assert listExpired == [dictJob["sJobId"]]
    assert listDiscarded == [dictJob["sJobId"]]


def test_a_young_staged_job_survives_the_sweep():
    dictJob = _fdictCreate()
    assert reproductionProgress.flistSweepExpiredJobs() == []
    assert dictJob["sJobId"] in reproductionProgress.DICT_JOBS


@pytest.mark.falsification
def test_the_hub_refuses_more_snapshots_than_it_will_hold():
    """Each staged clone may occupy the staging ceiling.

    Kills: dropping the concurrency cap from the registration.
    """
    for _iIndex in range(reproductionProgress.I_MAX_CONCURRENT_JOBS):
        _fdictCreate()
    with pytest.raises(reproductionProgress.TooManyReproductionJobsError):
        _fdictCreate()


def test_discarding_a_job_deletes_its_snapshot_and_releases_the_hold():
    listReleases = []
    listDiscarded = []
    dictJob = _fdictCreate(listReleases)
    reproductionProgress.fnDiscardJob(
        dictJob["sJobId"], listDiscarded.append,
    )
    assert listDiscarded == ["snapshotabc123"]
    assert listReleases == ["released"]
    assert dictJob["sJobId"] not in reproductionProgress.DICT_JOBS


@pytest.mark.falsification
def test_a_live_reproduction_vetoes_the_hubs_self_exit():
    """It holds no socket and no container, exactly like council work.

    Kills: returning False unconditionally from the veto predicate.
    """
    dictJob = _fdictCreate()
    assert not reproductionProgress.fbHubHoldsLiveReproduction()
    reproductionProgress.fnRecordPhase(
        dictJob["sJobId"], reproductionProgress.S_PHASE_RUNNING,
    )
    assert reproductionProgress.fbHubHoldsLiveReproduction()
    reproductionProgress.fnSettleJob(dictJob["sJobId"], {"sReportId": "x"})
    assert not reproductionProgress.fbHubHoldsLiveReproduction()


def test_the_staging_phases_are_observable_before_a_snapshot_exists():
    dictJob = reproductionProgress.fdictOpenStagingJob()
    assert dictJob["sPhase"] == reproductionProgress.S_PHASE_STAGING
    assert reproductionProgress.fbJobIsLive(dictJob["sJobId"])
    reproductionProgress.fnRecordPhase(
        dictJob["sJobId"], reproductionProgress.S_PHASE_VALIDATING,
    )
    assert reproductionProgress.fdictReadJobView(
        dictJob["sJobId"],
    )["sPhase"] == reproductionProgress.S_PHASE_VALIDATING
    reproductionProgress.fnAdoptStagedSnapshot(
        dictJob["sJobId"], "snapshotdef456", {"sRepositoryName": "p"},
        {"bReachable": True}, None,
    )
    assert reproductionProgress.fdictReadJobView(
        dictJob["sJobId"],
    )["sPhase"] == reproductionProgress.S_PHASE_STAGED


def test_the_comparison_event_moves_the_job_out_of_running():
    dictJob = _fdictCreate()
    reproductionProgress.fnRecordPipelineEvent(
        dictJob["sJobId"], {"sType": "comparingOutputs"}, {},
    )
    assert reproductionProgress.fdictReadJobView(
        dictJob["sJobId"],
    )["sPhase"] == reproductionProgress.S_PHASE_COMPARING


# ---------------------------------------------------------------------
# Races the second review reproduced (2026-09-07)
# ---------------------------------------------------------------------


@pytest.mark.falsification
def test_the_count_and_the_insert_share_one_acquisition_of_the_lock():
    """A cap enforced across a GAP in the lock is not a cap.

    Asserted structurally rather than by racing threads, because the
    race is real but not reliably lost: twelve threads released
    together usually serialise anyway, so the concurrent test below
    passes against the broken code and cannot be the guard. What makes
    the cap sound is that nothing can release the lock between reading
    the count and inserting the record, and that is what this counts.

    Kills: counting under one acquisition of the lock and inserting
    under another.
    """
    lockReal = reproductionProgress._LOCK_JOBS
    listAcquisitions = []

    class _CountingLock:
        def __enter__(self):
            listAcquisitions.append("acquired")
            return lockReal.__enter__()

        def __exit__(self, *args):
            return lockReal.__exit__(*args)

    fBuildReal = reproductionProgress._fdictBuildJobRecord
    listAcquisitionsAtInsert = []

    def fdictBuildNotingTheAcquisition(*args, **kwargs):
        listAcquisitionsAtInsert.append(len(listAcquisitions))
        return fBuildReal(*args, **kwargs)

    with patch.object(
        reproductionProgress, "flistSweepExpiredJobs", lambda **kwargs: [],
    ), patch.object(
        reproductionProgress, "_fdictBuildJobRecord",
        fdictBuildNotingTheAcquisition,
    ), patch.object(reproductionProgress, "_LOCK_JOBS", _CountingLock()):
        reproductionProgress.fdictCreateJob("snapshot0001", {}, {}, None)
    # The record is built during the FIRST acquisition -- the same one
    # that counted. A later acquisition means the lock was released in
    # between, which is the gap. (The read that composes the return
    # value takes the lock again, after the insert, and is harmless.)
    assert listAcquisitionsAtInsert == [1], (
        "the registration released the lock between counting and "
        "inserting; a cap checked across that gap is not a cap"
    )


def test_the_concurrency_cap_holds_against_simultaneous_registrations():
    """The outcome the structural guard above protects.

    Not marked falsification: threads released together serialise
    often enough that this passes against a registration whose count
    and insert are two critical sections.
    """
    import threading
    barrierStart = threading.Barrier(12)
    listAccepted = []

    def fnRegisterOne(iIndex):
        barrierStart.wait()
        try:
            reproductionProgress.fdictCreateJob(
                f"snapshot{iIndex:04d}", {}, {}, None,
            )
            listAccepted.append(iIndex)
        except reproductionProgress.TooManyReproductionJobsError:
            pass

    listThreads = [
        threading.Thread(target=fnRegisterOne, args=(iIndex,))
        for iIndex in range(12)
    ]
    for threadOne in listThreads:
        threadOne.start()
    for threadOne in listThreads:
        threadOne.join()
    assert len(listAccepted) == reproductionProgress.I_MAX_CONCURRENT_JOBS
    assert len(reproductionProgress.DICT_JOBS) == (
        reproductionProgress.I_MAX_CONCURRENT_JOBS
    )


@pytest.mark.falsification
def test_a_claimed_job_is_live_before_the_claim_returns():
    """The window between claiming and the route's first phase.

    A job claimed but still reading ``staged`` is one a Discard can
    delete the snapshot out from under, and the card sets its own
    running flag only when the Run request returns.

    Kills: leaving the phase alone in the claim.
    """
    dictJob = _fdictCreate()
    assert reproductionProgress.fbClaimJobForRun(dictJob["sJobId"], False)
    assert reproductionProgress.fbJobIsLive(dictJob["sJobId"]), (
        "a claimed job that is not live can be discarded mid-run"
    )


def test_the_hubs_periodic_sweep_expires_a_staged_job_nobody_ran():
    """The expiry must run on a tick, not only when the next job stages.

    A researcher who stages one snapshot and walks away used to keep
    it until the hub restarted: the check existed and nothing called
    it.
    """
    dictJob = _fdictCreate()
    reproductionProgress.DICT_JOBS[dictJob["sJobId"]][
        "fStagedAtMonotonic"
    ] -= 10 * reproductionProgress.F_STAGED_JOB_RETENTION_SECONDS
    with patch.object(reproductionProgress, "fnDiscardJob") as fnDiscard:
        reproductionProgress.fnSweepOnTheHubsTick()
    fnDiscard.assert_called_once_with(dictJob["sJobId"])


def test_the_hubs_periodic_tick_calls_the_sweep():
    """The lifespan loop's tick reaches the record's sweep.

    The sweep lives with the retention policy it enforces, so this is
    the one assertion that the loop actually calls it -- the defect it
    fixes was a check nothing ran.
    """
    import asyncio
    from vaibify.gui import serverLifespan
    with patch.object(
        reproductionProgress, "fnSweepOnTheHubsTick",
    ) as fnSweep:
        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            serverLifespan._fnRunOneContainerSweep({"docker": None}),
        )
    fnSweep.assert_called_once_with()
