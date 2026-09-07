"""The reproduction job record: one run per snapshot, and no host path.

The record is what the run route consumes and the progress route
reads, so its two guarantees are asserted here directly: a job is
claimed for its run exactly once, and the view a client receives never
carries the staging token or the release handle.
"""

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
    assert reproductionProgress.flistSweepSettledJobs(fMaxAgeSeconds=-1) == [
        dictSettled["sJobId"],
    ]
    assert reproductionProgress.fdictReadJobView(dictLive["sJobId"]) is not None
    assert reproductionProgress.fdictReadJobView(dictSettled["sJobId"]) is None
