"""An output newer than a test run leaves the run current only on content.

Rory's ruling (2026-10-15): "if the new file is byte-identical, then
tests should still be labeled as passed." The timestamp is trusted to
clear an output only when content can vouch for it: the run recorded
that output's digest AND the poll measured the same bytes. Every other
case -- no digest recorded, the file unreadable, a path the poll did
not sample -- falls back to the timestamp, so uncertainty reads as not
current, never as passed. A test SOURCE file keeps its timestamp rule.

Each case uses distinct mtimes and distinct bytes, and derives the
matched paths and run verdicts through the poll's own producers
(``hashStaleness``), so the contract is judged on the evidence the
poll would hand it.
"""

import pytest

from vaibify.gui import hashStaleness
from vaibify.gui import testMarkerContract as contract
from vaibify.gui.routes.pipelineRoutes import _fbMarkerStale

S_DATA = "Step/out.dat"
S_PLOT = "Plot/fig.png"
S_TEST_FILE = "test_integrity_step.py"
S_DATA_SHA = "d" * 40
S_PLOT_SHA = "p" * 40
F_RUN_TIME = 1_000.0
F_OLDER = 900.0
F_NEWER = 1_100.0


def _fdictMarker(dictOutputHashes):
    """One run at F_RUN_TIME whose one test passed, with the given digests."""
    sNodeId = "tests/" + S_TEST_FILE + "::test_it"
    return {
        "sDirectory": "Step", "sLabel": "A01",
        "dictRuns": {"r1": {
            "fTimestamp": F_RUN_TIME, "sRunAtUtc": "2026-10-15T00:00:00Z",
            "iExitStatus": 0, "dictOutputHashes": dictOutputHashes,
            "dictInputHashes": {}}},
        "dictTestFiles": {S_TEST_FILE: {
            "listNodeIds": [sNodeId],
            "dictOutcomes": {sNodeId: {"sOutcome": "passed", "sRunId": "r1"}},
            "dictCollectionError": None}},
        "dictLegacyCategories": {}, "dictUnattributedFailure": None,
    }


def _fdictIntegrityAfterPoll(
    dictMarker, dictHashEntries, dictOutputMtimes, dictTestFileMtimes=None,
):
    """Judge the marker as the poll does; return the integrity state."""
    dictMatched = hashStaleness.fdictMatchedPathsByRun(dictMarker, dictHashEntries)
    dictVerdicts = hashStaleness.fdictVerdictsForMarkerRuns(
        dictMarker, dictHashEntries)["dictRunVerdicts"]
    dictStates = contract.fdictCategoryStatesFromMarker(
        dictMarker, [S_TEST_FILE], dictTestFileMtimes or {}, dictVerdicts,
        dictOutputMtimes, dictMatched)
    bStale = _fbMarkerStale(
        dictMarker, {"dictMtimes": dictTestFileMtimes or {}},
        dictOutputMtimes=dictOutputMtimes, dictMatchedPathsByRun=dictMatched,
        dictRunVerdicts=dictVerdicts)
    return dictStates["integrity"], bStale


def _fdictBothHashed():
    return {S_DATA: S_DATA_SHA, S_PLOT: S_PLOT_SHA}


def _fdictEntriesMatching():
    return {S_DATA: {"sBlobSha": S_DATA_SHA}, S_PLOT: {"sBlobSha": S_PLOT_SHA}}


@pytest.mark.falsification
def test_identical_bytes_with_a_newer_mtime_leave_the_run_current():
    """Kills: dropping the content exemption, so the timestamp alone stales."""
    dictState, bStale = _fdictIntegrityAfterPoll(
        _fdictMarker(_fdictBothHashed()), _fdictEntriesMatching(),
        {S_DATA: F_OLDER, S_PLOT: F_NEWER})
    assert dictState["sState"] == "passed"
    assert bStale is False


@pytest.mark.falsification
def test_different_bytes_with_an_older_mtime_leave_the_run_not_current():
    """Kills: _fbMarkerStale ignoring the run's hash verdict, so the page
    treats a drifted run as standing and announces its result."""
    dictEntries = _fdictEntriesMatching()
    dictEntries[S_PLOT] = {"sBlobSha": "q" * 40}
    dictState, bStale = _fdictIntegrityAfterPoll(
        _fdictMarker(_fdictBothHashed()), dictEntries,
        {S_DATA: F_OLDER, S_PLOT: F_OLDER})
    assert dictState["sState"] == "untested"
    assert dictState["sNotCurrentBecause"] == "drift"
    assert bStale is True


@pytest.mark.falsification
def test_a_newer_output_the_run_never_hashed_leaves_it_not_current():
    """Kills: excusing every newer output, whether or not its digest was recorded."""
    dictState, bStale = _fdictIntegrityAfterPoll(
        _fdictMarker({S_DATA: S_DATA_SHA}), _fdictEntriesMatching(),
        {S_DATA: F_OLDER, S_PLOT: F_NEWER})
    assert dictState["sState"] == "untested"
    assert bStale is True


@pytest.mark.falsification
def test_a_newer_unreadable_output_names_why_the_run_is_not_current():
    """Kills: the reason falling back to "" for an output that outran the run."""
    dictEntries = _fdictEntriesMatching()
    dictEntries[S_PLOT] = {"sBlobSha": ""}
    dictState, bStale = _fdictIntegrityAfterPoll(
        _fdictMarker(_fdictBothHashed()), dictEntries,
        {S_DATA: F_OLDER, S_PLOT: F_NEWER})
    assert dictState["sState"] == "untested"
    assert dictState["sNotCurrentBecause"] == (
        contract.S_REASON_OUTPUTS_NEWER_THAN_RUN)
    assert bStale is True


@pytest.mark.falsification
def test_a_newer_output_the_poll_did_not_sample_leaves_it_not_current():
    """Kills: an unknown verdict being taken for a match."""
    dictEntries = {S_DATA: {"sBlobSha": S_DATA_SHA}}
    dictState, bStale = _fdictIntegrityAfterPoll(
        _fdictMarker(_fdictBothHashed()), dictEntries,
        {S_DATA: F_OLDER, S_PLOT: F_NEWER})
    assert dictState["sState"] == "untested"
    assert bStale is True


def test_a_test_source_file_newer_than_the_run_leaves_it_not_current():
    dictState, bStale = _fdictIntegrityAfterPoll(
        _fdictMarker(_fdictBothHashed()), _fdictEntriesMatching(),
        {S_DATA: F_OLDER, S_PLOT: F_OLDER},
        dictTestFileMtimes={S_TEST_FILE: F_NEWER})
    assert dictState["sState"] == "untested"
    assert bStale is True


def test_every_output_older_than_the_run_leaves_it_current():
    dictState, bStale = _fdictIntegrityAfterPoll(
        _fdictMarker(_fdictBothHashed()), _fdictEntriesMatching(),
        {S_DATA: F_OLDER, S_PLOT: F_OLDER})
    assert dictState["sState"] == "passed"
    assert bStale is False


def test_every_axis_the_poll_changes_is_logged_with_its_reason(caplog):
    from vaibify.gui.routes.pipelineRoutes import _fbApplyExternalTestResults
    dictWorkflow = {"listSteps": [{
        "sName": "Step", "sDirectory": "Step",
        "dictVerification": {"sIntegrity": "passed", "sUnitTest": "passed"},
    }]}
    dictStates = {"integrity": {
        "sState": "untested", "bHasMarkerInfo": True,
        "sNotCurrentBecause": contract.S_REASON_OUTPUTS_NEWER_THAN_RUN}}
    with caplog.at_level("INFO", logger="vaibify"):
        assert _fbApplyExternalTestResults(
            dictWorkflow, {"0": {"dictCategoryStates": dictStates}})
    listLines = [r.getMessage() for r in caplog.records if "POLL marker set" in r.getMessage()]
    assert listLines == [
        "POLL marker set step A01 sIntegrity: passed -> untested "
        "(reason: outputs-newer-than-run)"]


# ---------------------------------------------------------------------
# The modified-file report and the PROOF level follow the same rule
# ---------------------------------------------------------------------


def _fdictWorkflowDeclaringAnInput(sRepoRoot):
    from tests.testInputDataStaleness import _fdictBuildStep
    return {"sProjectRepoPath": sRepoRoot,
            "listSteps": [_fdictBuildStep("step1", ["data/raw.csv"])]}


def _fdictVerificationAfterATouch(tmp_path, dictContentVouchedByStep):
    from tests.testInputDataStaleness import _fdictBuildCtx, _fsWrite
    from vaibify.gui.fileStatusManager import _fdictDetectAndInvalidate
    sRepoRoot = str(tmp_path)
    sAbsInput = _fsWrite(sRepoRoot, "data/raw.csv", "v1")
    dictWorkflow = _fdictWorkflowDeclaringAnInput(sRepoRoot)
    _fdictDetectAndInvalidate(
        _fdictBuildCtx({"cid": {sAbsInput: "100"}}), "cid", dictWorkflow,
        {sAbsInput: "200"}, dictVars={"sRepoRoot": sRepoRoot},
        dictContentVouchedByStep=dictContentVouchedByStep)
    return dictWorkflow["listSteps"][0]["dictVerification"]


@pytest.mark.falsification
def test_a_touched_file_the_newest_run_vouches_for_is_not_modified(tmp_path):
    """Kills: the mtime detector ignoring the content evidence, so a
    byte-identical file is reported modified and PROOF regresses."""
    dictVerification = _fdictVerificationAfterATouch(
        tmp_path, {0: {"data/raw.csv"}})
    assert dictVerification["sUnitTest"] == "passed"
    assert not dictVerification.get("listModifiedFiles")


def test_a_touched_file_no_run_vouches_for_is_still_modified(tmp_path):
    dictVerification = _fdictVerificationAfterATouch(tmp_path, {0: set()})
    assert dictVerification["sUnitTest"] == "untested"
    assert dictVerification["listModifiedFiles"] == ["data/raw.csv"]


def test_only_the_newest_run_vouches_for_a_file():
    from vaibify.gui.fileStatusManager import fdictContentVouchedPathsByStep
    dictMarker = _fdictMarker(_fdictBothHashed())
    dictMarker["dictRuns"]["r0"] = dict(
        dictMarker["dictRuns"]["r1"], fTimestamp=F_OLDER)
    dictMatched = {0: {"r0": [S_DATA, S_PLOT], "r1": [S_DATA]}}
    assert fdictContentVouchedPathsByStep({0: dictMarker}, dictMatched) == {
        0: {S_DATA}}


@pytest.mark.falsification
def test_every_poll_ships_each_steps_stored_sign_off():
    """Kills: the poll not shipping the server's sign-off, so a sign-off
    the server voided never reaches a client that is on the current
    workflow epoch (it is re-sent the workflow only when the epoch moves).
    """
    import asyncio
    from unittest.mock import AsyncMock, patch
    from vaibify.gui.routes import pipelineRoutes
    dictWorkflow = {"listSteps": [
        {"dictVerification": {"sUser": "stale",
                              "sLastUserUpdate": "2026-10-15 12:00:00 UTC"}},
        {"dictVerification": {"sUser": "passed"}},
        {},
    ]}
    with patch.object(pipelineRoutes, "_fdictFetchOutputStatus",
                      AsyncMock(return_value={"dictModTimes": {}})), \
            patch.object(pipelineRoutes, "_fdictFetchTestStatus",
                         AsyncMock(return_value={})):
        dictAnswer = asyncio.run(pipelineRoutes.fdictComputeFileStatus(
            {}, "cid", dictWorkflow, {}))
    assert dictAnswer["dictSignOffByStep"] == {
        "0": {"sUser": "stale", "sLastUserUpdate": "2026-10-15 12:00:00 UTC"},
        "1": {"sUser": "passed", "sLastUserUpdate": None},
        "2": {"sUser": "untested", "sLastUserUpdate": None},
    }


def _fsPencilStatusWithTheDataNewerThanTheMarker(setVouched):
    from vaibify.gui.fileStatusManager import _fdictBuildScriptStatus
    dictWorkflow = {"sProjectRepoPath": "/repo", "listSteps": [{
        "sName": "Step", "sDirectory": "Step",
        "saDataCommands": ["python make.py"], "saPlotCommands": [],
        "saOutputDataFiles": ["data.txt"], "saPlotFiles": [],
        "dictVerification": {"sUser": "untested"},
    }]}
    dictModTimes = {"/repo/Step/data.txt": "200", "/repo/Step/make.py": "50"}
    dictStatus = _fdictBuildScriptStatus(
        dictWorkflow, dictModTimes, {"sRepoRoot": "/repo"},
        dictMarkerMtimeByStep={"0": "100"},
        dictContentVouchedByStep={0: setVouched})
    return dictStatus[0]["sStatus"]


@pytest.mark.falsification
def test_a_data_file_whose_bytes_are_vouched_for_does_not_stale_the_step():
    """Kills: the pencil check (PROOF's script-stale) judging a vouched
    file by its mtime, so a byte-identical touch drops the level."""
    assert _fsPencilStatusWithTheDataNewerThanTheMarker(
        {"Step/data.txt"}) == "unchanged"


def test_a_data_file_nothing_vouches_for_still_stales_the_step():
    assert _fsPencilStatusWithTheDataNewerThanTheMarker(set()) == "modified"


def test_a_plot_matching_the_sign_off_is_vouched_for_and_a_different_one_is_not():
    from vaibify.gui.fileStatusManager import fdictWithSignedOffPlots
    from vaibify.reproducibility.repoFiles import SnapshotRepoFiles

    class _Snapshot(SnapshotRepoFiles):
        def __init__(self, dictHashes):
            self._dictHashes = dictHashes

    dictWorkflow = {"listSteps": [{"dictVerification": {
        "sUser": "passed",
        "dictUserVerifiedHashes": {"Plot/a.png": "1" * 64, "Plot/b.png": "2" * 64},
    }}]}
    filesPoll = _Snapshot({"Plot/a.png": {"sSha256": "1" * 64},
                           "Plot/b.png": {"sSha256": "9" * 64}})
    assert fdictWithSignedOffPlots(dictWorkflow, {0: {"Step/data.txt"}}, filesPoll) == {
        0: {"Step/data.txt", "Plot/a.png"}}


# ---------------------------------------------------------------------
# An upstream redraw flags a downstream step only when its bytes are new
# ---------------------------------------------------------------------


def _fdictTwoStepWorkflow():
    """Step 1 reads step 0's data through a token; step 0 also draws a plot."""
    return {"sProjectRepoPath": "/repo", "sPlotDirectory": "Plot",
            "sFigureType": "png", "listSteps": [
        {"sStepId": "up", "sDirectory": "Up", "saOutputDataFiles": ["a.json"],
         "saPlotFiles": ["{sPlotDirectory}/up.{sFigureType}"],
         "saDataCommands": ["python up.py"], "dictVerification": {}},
        {"sStepId": "down", "sDirectory": "Down",
         "saOutputDataFiles": ["b.json"],
         "saDataCommands": ["python down.py {step:up.a}"],
         "dictVerification": {}},
    ]}


S_UPSTREAM_DATA = "Up/a.json"


def _fdictDownstreamAfterReconcile(dictEdge, dictConsumed=None):
    """Step 1's outputs date from 200; return its verification after one pass."""
    from vaibify.gui.fileStatusManager import fbReconcileUpstreamFlags
    dictWorkflow = _fdictTwoStepWorkflow()
    if dictConsumed is not None:
        dictWorkflow["listSteps"][1]["dictVerification"][
            "dictConsumedUpstreamDigests"] = dict(dictConsumed)
    fbReconcileUpstreamFlags(
        dictWorkflow, {"0": "300", "1": "200"},
        None if dictEdge is None else {(0, 1): dictEdge})
    return dictWorkflow["listSteps"][1]["dictVerification"]


def _fbDownstreamFlagged(dictEdge, dictConsumed=None):
    return _fdictDownstreamAfterReconcile(dictEdge, dictConsumed).get(
        "bUpstreamModified") is True


@pytest.mark.falsification
def test_upstream_bytes_the_step_below_consumed_do_not_flag_it():
    """Kills: the upstream check judging by the timestamp alone again, so
    a re-run that reproduced the same bytes drops the step below."""
    assert _fbDownstreamFlagged(
        {"iMtime": 300, "dictDigests": {S_UPSTREAM_DATA: "1" * 64}},
        {S_UPSTREAM_DATA: "1" * 64}) is False


def test_new_upstream_bytes_flag_the_step_below():
    assert _fbDownstreamFlagged(
        {"iMtime": 300, "dictDigests": {S_UPSTREAM_DATA: "2" * 64}},
        {S_UPSTREAM_DATA: "1" * 64}) is True


def test_upstream_bytes_never_recorded_or_unmeasured_flag_the_step_below():
    assert _fbDownstreamFlagged(
        {"iMtime": 300, "dictDigests": {S_UPSTREAM_DATA: "1" * 64}}) is True
    assert _fbDownstreamFlagged(
        {"iMtime": 300, "dictDigests": {S_UPSTREAM_DATA: None}},
        {S_UPSTREAM_DATA: None}) is True
    assert _fbDownstreamFlagged(None) is True


def test_a_current_step_records_the_upstream_bytes_it_consumed():
    dictVerification = _fdictDownstreamAfterReconcile(
        {"iMtime": 150, "dictDigests": {S_UPSTREAM_DATA: "1" * 64}})
    assert dictVerification["dictConsumedUpstreamDigests"] == {
        S_UPSTREAM_DATA: "1" * 64}
    assert not dictVerification.get("bUpstreamModified")


@pytest.mark.falsification
def test_a_flagged_step_never_records_the_new_upstream_bytes():
    """Kills: recording while the step is stale, so the new upstream bytes
    are adopted as consumed and the next poll clears the flag."""
    dictVerification = _fdictDownstreamAfterReconcile(
        {"iMtime": 300, "dictDigests": {S_UPSTREAM_DATA: "2" * 64}},
        {S_UPSTREAM_DATA: "1" * 64})
    assert dictVerification["bUpstreamModified"] is True
    assert dictVerification["dictConsumedUpstreamDigests"] == {
        S_UPSTREAM_DATA: "1" * 64}


@pytest.mark.falsification
def test_a_plot_the_step_below_never_names_is_not_compared():
    """Kills: comparing every upstream output, so redrawing a plot the
    step below never reads drops it from Level 1."""
    from vaibify.gui.fileStatusManager import fdictUpstreamEvidenceByEdge
    dictWorkflow = _fdictTwoStepWorkflow()
    dictModTimes = {"/repo/Up/a.json": "100", "/repo/Plot/up.png": "300",
                    "/repo/Down/b.json": "200"}
    dictEdges = fdictUpstreamEvidenceByEdge(
        dictWorkflow, dictModTimes, {"sRepoRoot": "/repo",
                                     "sPlotDirectory": "/repo/Plot",
                                     "sFigureType": "png"}, None)
    assert dictEdges[(0, 1)]["iMtime"] == 100
    assert list(dictEdges[(0, 1)]["dictDigests"]) == [S_UPSTREAM_DATA]
    assert _fbDownstreamFlagged(dictEdges[(0, 1)]) is False
