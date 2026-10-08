"""The poll judges a test marker against what the CONTAINER hashed.

The marker lane used to open each recorded output on the host. For a
container project that path is inside a Docker volume, so every read
failed, a failed read counted as drift, and every step with a hashed
marker was invalidated on every poll: unit tests passed, then read
Untested five seconds later, and ``state.json`` was rewritten twice per
tick, forever.

Three verdicts now exist per path and they are not interchangeable.
DRIFT is proven by evidence: a digest that differs, or the container
saying the open raised ``FileNotFoundError``. UNKNOWN is the absence of
evidence -- a path missing from the answer, a torn read, a failed
snapshot -- and it never invalidates anything.

Every test here drives the REAL poll over a container root asserted
absent on this machine (``tests/markerPollHarness.py``); a root under
``tmp_path`` is how this bug stayed hidden.
"""

import os

import pytest

from tests.markerPollHarness import (
    MarkerPollProject,
    fsBaselineDigest,
    fsOutputRelativePath,
)
from tests.snapshotProgramHarness import fdictSteadyHashEntry
from vaibify.reproducibility.repoFiles import ffilesConservativeSnapshot


@pytest.mark.falsification
def test_a_container_project_whose_files_match_stays_passed():
    """The reported bug: tests pass, then flip to Untested on the next poll.

    The container hashed every output and each digest equals the one
    its marker recorded. Nothing is invalidated, the unit-test axis
    stays ``passed`` across polls, and Level 1 is attained.

    Kills: restoring the host-side read of the recorded outputs, which
    cannot open a container path and so judges every file drifted.
    """
    project = MarkerPollProject()
    assert not os.path.exists(project.sRoot)
    dictAnswer = project.fdictRunPoll()
    assert dictAnswer["dictInvalidatedSteps"] == {}
    assert project.flistInvalidatedStepNames() == []
    assert dictAnswer["iProofLevel"] == 1
    assert dictAnswer["dictStepLevels"]["0"]["s1"]["sState"] == "attained"
    assert project.fdictVerificationOf("A")["sUnitTest"] == "passed"


@pytest.mark.falsification
def test_a_drifted_output_invalidates_its_step_and_the_steps_downstream():
    """One differing digest invalidates that step and what depends on it.

    Step A's output no longer matches; step B consumes A's output; C is
    unrelated and must keep its pass.

    Kills: invalidating every step on any drift, and dropping the
    downstream closure from the hash lane.
    """
    project = MarkerPollProject(dictHashOverrides={
        fsOutputRelativePath("A"): fdictSteadyHashEntry("f" * 40),
    })
    dictAnswer = project.fdictRunPoll()
    assert project.flistInvalidatedStepNames() == ["A", "B"]
    assert set(dictAnswer["dictInvalidatedSteps"]) == {0, 1}
    assert project.fdictVerificationOf("C")["sUnitTest"] == "passed"
    assert fsOutputRelativePath("A") in project.fdictVerificationOf(
        "A")["listModifiedFiles"]


@pytest.mark.falsification
def test_a_deletion_the_container_reports_invalidates_the_step():
    """``bMissing`` is the container's own report of FileNotFoundError.

    Kills: ignoring ``bMissing``, which would leave a deleted output
    reading as a passing test forever.
    """
    project = MarkerPollProject(dictHashOverrides={
        fsOutputRelativePath("C"): {
            "sSha256": None, "sBlobSha": None, "bMissing": True,
            "sSymlinkSegment": None, "bEscapesRoot": False},
    })
    project.fdictRunPoll()
    assert project.flistInvalidatedStepNames() == ["C"]


@pytest.mark.falsification
def test_a_path_absent_from_the_answer_is_unknown_and_invalidates_nothing():
    """Silence from the container is not a deletion.

    The snapshot answered, but not for step C's output. The poll's own
    mtime map is ALSO missing it (that map drops a path on any OSError,
    and is empty when the container vanishes) -- and neither fact
    proves the file is gone.

    Kills: inferring deletion from the stat map or from an omitted
    answer, which would invalidate a step on no evidence.
    """
    project = MarkerPollProject(
        setUnstattableRelativePaths={fsOutputRelativePath("C")},
    )
    del project.connection.dictHashEntries[fsOutputRelativePath("C")]
    dictAnswer = project.fdictRunPoll()
    assert project.flistInvalidatedStepNames() == []
    assert dictAnswer["dictInvalidatedSteps"] == {}
    assert dictAnswer["iProofLevel"] == 0
    assert dictAnswer["listBlockers"][0]["sCriterion"] == (
        "test-freshness-unchecked")


def test_a_matching_digest_outranks_a_missing_stat():
    """The stat map never decides anything; the hash read does.

    Kills: letting a missing mtime override a digest that matches.
    """
    project = MarkerPollProject(
        setUnstattableRelativePaths={fsOutputRelativePath("C")},
    )
    dictAnswer = project.fdictRunPoll()
    assert project.flistInvalidatedStepNames() == []
    assert dictAnswer["dictInvalidatedSteps"] == {}


@pytest.mark.falsification
def test_a_path_a_marker_recorded_but_the_workflow_no_longer_declares_is_judged():
    """The marker's claim is judged even after the declaration moved on.

    The step's declared outputs no longer include the file its marker
    still vouches for. It is in the snapshot request, and its drift
    reaches the step as a modified file (as it always did: the unit-test
    axis is demoted only for a DECLARED output, which this is not).

    Kills: requesting only the declared outputs, which would never
    answer for a path only the marker names.
    """
    sRetired = "StepC/retired.dat"
    dictMarker = {
        "sLabel": "A03", "sDirectory": "StepC", "iExitStatus": 0,
        "dictOutputHashes": {
            fsOutputRelativePath("C"): "c" * 40, sRetired: "9" * 40},
    }
    project = MarkerPollProject(
        dictMarkerOverrides={
            ".vaibify/test_markers/demo/StepC.json": dictMarker},
        dictHashOverrides={
            sRetired: fdictSteadyHashEntry("8" * 40)},
    )
    project.fdictRunPoll()
    assert sRetired in project.connection.listRequestedHashPaths
    assert project.fdictVerificationOf("C")["listModifiedFiles"] == [sRetired]


@pytest.mark.falsification
def test_a_failed_snapshot_is_unknown_never_drift():
    """A snapshot that could not be taken says nothing about any file.

    The exec fails, the poll degrades to ONE conservative tick, and no
    step is invalidated; Level 1 is withheld and says why.

    Kills: collapsing unknown into drift, which would demote every
    passing test whenever the container hiccuped.
    """
    project = MarkerPollProject(iSnapshotExitCode=1)
    dictAnswer = project.fdictRunPoll()
    assert project.flistInvalidatedStepNames() == []
    assert dictAnswer["dictInvalidatedSteps"] == {}
    assert dictAnswer["iProofLevel"] == 0
    assert {dictBlocker["sCriterion"]
            for dictBlocker in dictAnswer["listBlockers"]} == {
        "test-freshness-unchecked"}


def test_a_conservative_snapshot_reads_every_path_unknown():
    from vaibify.gui import fileStatusManager
    project = MarkerPollProject()
    dictMarkers = {
        iIndex: project.connection.dictMarkersByRelativePath[
            ".vaibify/test_markers/demo/Step%s.json" % sName]
        for iIndex, sName in enumerate(("A", "B", "C"))
    }
    dictVerdicts = fileStatusManager.fdictMarkerVerdictsByStep(
        project.dictWorkflow, dictMarkers,
        ffilesConservativeSnapshot(project.sRoot),
    )
    assert all(not dictEntry["listDrifted"]
               for dictEntry in dictVerdicts.values())
    assert {iStep: dictEntry["listUnknown"]
            for iStep, dictEntry in dictVerdicts.items()} == {
        0: [fsOutputRelativePath("A")], 1: [fsOutputRelativePath("B")],
        2: [fsOutputRelativePath("C")]}


@pytest.mark.falsification
def test_an_unchanged_project_is_a_fixed_point_of_the_poll():
    """Polling an unchanged project must change nothing and save nothing.

    The first poll may legitimately save once (a level is attained and
    stamped). The second finds nothing new: zero ``save`` calls and no
    invalidated step. On ``main`` the hash lane invalidated every step
    on every poll, and for an attested step the stale-check cleared
    ``listModifiedFiles`` for the hash lane to add again, so
    ``state.json`` was written twice per tick.

    Kills: restoring the host read, and moving the flag reconcile ahead
    of the snapshot-based invalidation (a drift would then be set by
    one poll and cleared by the next).
    """
    project = MarkerPollProject()
    project.fdictRunPoll()
    project.dictCtx["save"].reset_mock()
    dictSecond = project.fdictRunPoll()
    assert dictSecond["dictInvalidatedSteps"] == {}
    assert project.dictCtx["save"].call_count == 0


def test_a_persistent_drift_reads_the_same_on_every_poll():
    """A drift that never heals must not flicker between polls.

    Step A's output stays different and is newer than B's, as a real
    edit would leave it. Every poll after the first must show the same
    invalidated steps and the same flags. (Not kill-confirmed: the
    write on each poll while a drift persists is existing behaviour and
    not asserted here.)
    """
    project = MarkerPollProject(
        dictHashOverrides={
            fsOutputRelativePath("A"): fdictSteadyHashEntry("f" * 40)},
        dictMtimeByRelativePath={fsOutputRelativePath("A"): "1700000100"},
    )
    project.fdictRunPoll()
    dictAfterFirst = {
        sName: dict(project.fdictVerificationOf(sName))
        for sName in ("A", "B", "C")}
    for _iPoll in range(3):
        project.fdictRunPoll()
    for sName in ("A", "B", "C"):
        assert project.fdictVerificationOf(sName) == dictAfterFirst[sName]
    assert project.fdictVerificationOf("B")["bUpstreamModified"] is True


@pytest.mark.falsification
def test_the_flag_reconcile_runs_after_the_drift_is_applied():
    """``fbReconcileUpstreamFlags`` clears flags the invalidation just set.

    It clears ``bUpstreamModified`` wherever mtimes say nothing is
    stale, including on a downstream step this poll's invalidation has
    just flagged. Run BEFORE the invalidation, a persistent drift sets
    the flag after the reconcile and the next poll clears it again: a
    second write per poll. The order is a contract, so it is pinned.

    Kills: moving the reconcile ahead of the snapshot-based
    invalidation.
    """
    from unittest.mock import patch
    from vaibify.gui.routes import pipelineRoutes
    listCalls = []
    sModule = "vaibify.gui.routes.pipelineRoutes."
    project = MarkerPollProject()

    def fdictInvalidate(*aArgs, **dictKeywords):
        listCalls.append("invalidate")
        return {}

    def fbReconcile(*aArgs, **dictKeywords):
        listCalls.append("reconcile")
        return False

    with patch(sModule + "_fdictDetectAndInvalidate", fdictInvalidate), \
            patch(sModule + "fbReconcileUpstreamFlags", fbReconcile):
        pipelineRoutes._flistRunPollSideEffects(
            project.dictCtx, "cid", project.dictWorkflow, {}, {}, {},
        )
    assert listCalls == ["invalidate", "reconcile"]


S_PATH_ONLY_THE_SECOND_RUN_RECORDED = "StepC/second.dat"


def _fdictRunRecordedAt(fTimestamp, dictOutputHashes):
    return {
        "fTimestamp": fTimestamp, "sRunAtUtc": "2026-10-07T00:00:00Z",
        "iExitStatus": 0, "dictOutputHashes": dictOutputHashes,
        "dictInputHashes": {},
    }


def _fdictOutcomeOf(sNodeId, sRunId):
    return {
        "listNodeIds": [sNodeId],
        "dictOutcomes": {sNodeId: {"sOutcome": "passed", "sRunId": sRunId}},
        "dictCollectionError": None,
    }


def fdictMarkerWithTheIntegrityPassUnderAnUncheckedRun():
    """Step C: run one (qualitative) matches, run two (integrity) does not.

    Distinct paths and distinct outcomes: the second run recorded a path
    the first never did, and the integrity pass belongs to the second.
    """
    sOutput = fsOutputRelativePath("C")
    sDigest = fsBaselineDigest("C")
    return {
        "sLabel": "A03", "sDirectory": "StepC",
        "dictRuns": {
            "runOne": _fdictRunRecordedAt(1.0, {sOutput: sDigest}),
            "runTwo": _fdictRunRecordedAt(2.0, {
                sOutput: sDigest,
                S_PATH_ONLY_THE_SECOND_RUN_RECORDED: "f" * 40}),
        },
        "dictTestFiles": {
            "test_qualitative_stepC.py": _fdictOutcomeOf("q", "runOne"),
            "test_integrity_stepC.py": _fdictOutcomeOf("i", "runTwo"),
        },
        "dictLegacyCategories": {}, "dictUnattributedFailure": None,
    }


@pytest.mark.falsification
def test_a_matching_run_does_not_hide_a_pass_whose_run_could_not_be_checked():
    """One run's files match; the run holding the integrity pass cannot be checked.

    Matching keeps the step from being invalidated, but it cannot vouch
    for another run's results: the integrity pass still depends on data
    the poll could not compare, so Level 1 must say the freshness is
    unchecked instead of attaining on the strength of the other run.

    Kills: letting a matching run suppress the step's unknown paths,
    which hid the unchecked pass from the Level 1 gate.
    """
    project = MarkerPollProject(dictMarkerOverrides={
        ".vaibify/test_markers/demo/StepC.json":
            fdictMarkerWithTheIntegrityPassUnderAnUncheckedRun()})
    dictAnswer = project.fdictRunPoll()
    assert project.flistInvalidatedStepNames() == []
    listUnchecked = [
        dictBlocker for dictBlocker in dictAnswer["listBlockers"]
        if dictBlocker["sCriterion"] == "test-freshness-unchecked"]
    assert [d["sStepLabel"] for d in listUnchecked] == ["A03"]
    assert listUnchecked[0]["listOffendingFiles"] == [
        S_PATH_ONLY_THE_SECOND_RUN_RECORDED]
    assert dictAnswer["iProofLevel"] == 0
