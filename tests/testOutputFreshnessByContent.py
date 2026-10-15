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
        dictOutputMtimes=dictOutputMtimes, dictMatchedPathsByRun=dictMatched)
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


def test_different_bytes_with_an_older_mtime_leave_the_run_not_current():
    dictEntries = _fdictEntriesMatching()
    dictEntries[S_PLOT] = {"sBlobSha": "q" * 40}
    dictState, bStale = _fdictIntegrityAfterPoll(
        _fdictMarker(_fdictBothHashed()), dictEntries,
        {S_DATA: F_OLDER, S_PLOT: F_OLDER})
    assert dictState["sState"] == "untested"
    assert dictState["sNotCurrentBecause"] == "drift"


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
