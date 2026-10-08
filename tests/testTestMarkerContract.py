"""What a test marker means, per test, however many sessions fed it.

A marker used to hold ONE session's summary, and the dashboard runs one
pytest session per category, so the marker held only the last category
run: the other two read untested, the aggregate folded to untested, and
a researcher watched passing tests flip back. The marker now records
each test's own last outcome, judged against its own run.

These tests drive the pure contract (``testMarkerContract``): the merge
a session performs and the states every reader derives. Nothing here
runs pytest or touches a file; ``testConftestPerTestMarker`` drives the
real plugin.
"""

import pytest

from vaibify.gui import testMarkerContract as contract

S_FILE_A = "test_quantitative_a.py"
S_FILE_B = "test_integrity_b.py"
S_FILE_C = "test_qualitative_c.py"


def _fsNodeId(sFile, sTest):
    return "tests/%s::%s" % (sFile, sTest)


def _fdictSession(
    sRunId, fTimestamp, dictFileResults, listPresentFiles=None,
    iExitStatus=0, dictOutputHashes=None, dictUnattributedFailure=None,
):
    return {
        "sRunId": sRunId, "fTimestamp": fTimestamp,
        "sRunAtUtc": "2026-10-07T00:00:%02dZ" % int(fTimestamp),
        "iExitStatus": iExitStatus, "sDirectory": "StepDir",
        "sLabel": "A01",
        "dictOutputHashes": dictOutputHashes or {"StepDir/out.dat": "a" * 40},
        "dictInputHashes": {},
        "listPresentFiles": listPresentFiles or [S_FILE_A, S_FILE_B, S_FILE_C],
        "dictFileResults": dictFileResults,
        "dictUnattributedFailure": dictUnattributedFailure,
    }


def _fdictWholeFile(sFile, dictOutcomes, listNodeIds=None):
    listIds = [_fsNodeId(sFile, s) for s in (listNodeIds or dictOutcomes)]
    return {
        "bWholeFile": True, "listNodeIds": listIds,
        "dictOutcomes": {
            _fsNodeId(sFile, sTest): sOutcome
            for sTest, sOutcome in dictOutcomes.items()},
        "dictCollectionError": None,
    }


def _fdictNarrowed(sFile, dictOutcomes):
    return {
        "bWholeFile": False, "listNodeIds": [],
        "dictOutcomes": {
            _fsNodeId(sFile, sTest): sOutcome
            for sTest, sOutcome in dictOutcomes.items()},
        "dictCollectionError": None,
    }


def _fdictStates(
    dictMarker, listPresentFiles=None, dictFileMtimes=None,
    dictRunVerdicts=None, fMaxOutputMtime=0,
):
    return contract.fdictCategoryStatesFromMarker(
        dictMarker, listPresentFiles or [S_FILE_A, S_FILE_B, S_FILE_C],
        dictFileMtimes or {}, dictRunVerdicts or {}, fMaxOutputMtime)


def _fsState(dictStates, sCategory):
    return dictStates[sCategory]["sState"]


def _fdictMergeOnto(dictMarker, *listSessions):
    for dictSession in listSessions:
        dictMarker = contract.fdictMergeSessionIntoMarker(
            dictMarker, dictSession)
    return dictMarker


def _fdictMarkerAfter(*listSessions):
    return _fdictMergeOnto(None, *listSessions)


@pytest.mark.falsification
def test_each_category_run_keeps_the_other_categories_results():
    """The reported bug: three category runs, one marker, all three survive.

    Kills: a merge that replaces the file table with the session's,
    which is the marker holding only the last category run.
    """
    dictMarker = _fdictMarkerAfter(
        _fdictSession("r1", 1, {
            S_FILE_A: _fdictWholeFile(S_FILE_A, {"test_a": "passed"})}),
        _fdictSession("r2", 2, {
            S_FILE_B: _fdictWholeFile(S_FILE_B, {"test_b": "passed"})}),
        _fdictSession("r3", 3, {
            S_FILE_C: _fdictWholeFile(S_FILE_C, {"test_c": "passed"})}),
    )
    dictStates = _fdictStates(dictMarker)
    assert _fsState(dictStates, "quantitative") == "passed"
    assert _fsState(dictStates, "integrity") == "passed"
    assert _fsState(dictStates, "qualitative") == "passed"


@pytest.mark.falsification
def test_a_narrowed_pass_never_hides_another_tests_failure():
    """A full run with ``test_b`` failing, then ``-k test_a`` passing.

    The category stays failed: the second run says nothing about
    ``test_b``, so its last outcome stands.

    Kills: recording a narrowed run's pass at the category level.
    """
    dictMarker = _fdictMarkerAfter(
        _fdictSession("r1", 1, {S_FILE_A: _fdictWholeFile(
            S_FILE_A, {"test_a": "passed", "test_b": "failed"})}),
        _fdictSession("r2", 2, {S_FILE_A: _fdictWholeFile(
            S_FILE_A, {"test_a": "passed"},
            listNodeIds=["test_a", "test_b"])}),
    )
    assert _fsState(_fdictStates(dictMarker), "quantitative") == "failed"


def test_a_narrowed_pass_after_a_full_passing_run_stays_passed():
    dictMarker = _fdictMarkerAfter(
        _fdictSession("r1", 1, {S_FILE_A: _fdictWholeFile(
            S_FILE_A, {"test_a": "passed", "test_b": "passed"})}),
        _fdictSession("r2", 2, {S_FILE_A: _fdictWholeFile(
            S_FILE_A, {"test_a": "passed"},
            listNodeIds=["test_a", "test_b"])}),
    )
    assert _fsState(_fdictStates(dictMarker), "quantitative") == "passed"


@pytest.mark.falsification
def test_passes_from_runs_at_one_data_state_accumulate_but_not_across_data():
    """Two runs, each passing half the tests.

    At the same data state the category is passed. If the first run's
    data has drifted from the files now, its half no longer counts and
    the category is untested: passes from different data states never
    combine.

    Kills: counting an outcome without judging it against its own run's
    data.
    """
    dictMarker = _fdictMarkerAfter(
        _fdictSession("r1", 1, {S_FILE_A: _fdictWholeFile(
            S_FILE_A, {"test_a": "passed"},
            listNodeIds=["test_a", "test_b"])}),
        _fdictSession("r2", 2, {S_FILE_A: _fdictWholeFile(
            S_FILE_A, {"test_b": "passed"},
            listNodeIds=["test_a", "test_b"])}),
    )
    assert _fsState(_fdictStates(dictMarker), "quantitative") == "passed"
    dictAfterDrift = _fdictStates(
        dictMarker, dictRunVerdicts={"r1": "drift", "r2": "match"})
    assert _fsState(dictAfterDrift, "quantitative") == "untested"
    assert dictAfterDrift["quantitative"]["iCurrentTests"] == 1
    assert dictAfterDrift["quantitative"]["iTotalTests"] == 2


def test_an_unknown_verdict_does_not_make_a_result_stale():
    """Unknown is the absence of evidence; the dashboard says so elsewhere."""
    dictMarker = _fdictMarkerAfter(
        _fdictSession("r1", 1, {S_FILE_A: _fdictWholeFile(
            S_FILE_A, {"test_a": "passed"})}))
    dictStates = _fdictStates(dictMarker, dictRunVerdicts={"r1": "unknown"})
    assert _fsState(dictStates, "quantitative") == "passed"


@pytest.mark.falsification
def test_a_node_id_run_never_rewrites_the_test_list():
    """``-k`` updates the list; a ``file::test`` argument does not.

    A whole-file collection (even with ``-k``) lists every test it
    collected or deselected. A run named by node id collected one test of
    many and must not shrink the file's list to it.

    Kills: treating a node-id run as a whole-file collection.
    """
    dictMarker = _fdictMarkerAfter(
        _fdictSession("r1", 1, {S_FILE_A: _fdictWholeFile(
            S_FILE_A, {"test_a": "passed", "test_b": "passed"})}),
        _fdictSession("r2", 2, {S_FILE_A: _fdictNarrowed(
            S_FILE_A, {"test_a": "passed"})}),
    )
    assert dictMarker["dictTestFiles"][S_FILE_A]["listNodeIds"] == [
        _fsNodeId(S_FILE_A, "test_a"), _fsNodeId(S_FILE_A, "test_b")]
    dictAfterKRun = _fdictMergeOnto(
        dictMarker,
        _fdictSession("r3", 3, {S_FILE_A: _fdictWholeFile(
            S_FILE_A, {"test_a": "passed"},
            listNodeIds=["test_a", "test_b", "test_c"])}))
    assert len(dictAfterKRun["dictTestFiles"][S_FILE_A]["listNodeIds"]) == 3
    assert _fsState(_fdictStates(dictAfterKRun), "quantitative") == "untested"


def test_a_deleted_test_stops_counting_after_the_next_whole_file_collection():
    dictMarker = _fdictMarkerAfter(
        _fdictSession("r1", 1, {S_FILE_A: _fdictWholeFile(
            S_FILE_A, {"test_a": "passed", "test_gone": "failed"})}))
    assert _fsState(_fdictStates(dictMarker), "quantitative") == "failed"
    dictAfter = _fdictMergeOnto(
        dictMarker,
        _fdictSession("r2", 2, {S_FILE_A: _fdictWholeFile(
            S_FILE_A, {"test_a": "passed"})}))
    assert _fsNodeId(S_FILE_A, "test_gone") not in dictAfter[
        "dictTestFiles"][S_FILE_A]["dictOutcomes"]
    assert _fsState(_fdictStates(dictAfter), "quantitative") == "passed"


@pytest.mark.falsification
def test_a_collection_error_fails_the_category_and_the_older_pass_dies():
    """A file that no longer collects is failed, not stale-green.

    Kills: ignoring ``dictCollectionError`` so an older pass survives a
    file the next session could not even import.
    """
    dictError = {"sRunId": "r2", "fTimestamp": 2, "sMessage": "SyntaxError"}
    dictMarker = _fdictMarkerAfter(
        _fdictSession("r1", 1, {S_FILE_A: _fdictWholeFile(
            S_FILE_A, {"test_a": "passed"})}),
        _fdictSession("r2", 2, {S_FILE_A: {
            "bWholeFile": False, "listNodeIds": [], "dictOutcomes": {},
            "dictCollectionError": dictError}}, iExitStatus=2),
    )
    assert _fsState(_fdictStates(dictMarker), "quantitative") == "failed"
    dictFixed = _fdictMergeOnto(
        dictMarker,
        _fdictSession("r3", 3, {S_FILE_A: _fdictWholeFile(
            S_FILE_A, {"test_a": "passed"})}))
    assert dictFixed["dictTestFiles"][S_FILE_A]["dictCollectionError"] is None
    assert _fsState(_fdictStates(dictFixed), "quantitative") == "passed"


@pytest.mark.falsification
def test_an_unattributed_failure_makes_older_results_unproven():
    """A session that failed with no category to blame proves nothing older.

    Kills: ignoring ``dictUnattributedFailure`` when judging whether a
    run is current.
    """
    dictMarker = _fdictMarkerAfter(
        _fdictSession("r1", 1, {S_FILE_A: _fdictWholeFile(
            S_FILE_A, {"test_a": "passed"})}),
        _fdictSession("r2", 2, {}, iExitStatus=2, dictUnattributedFailure={
            "sRunId": "r2", "fTimestamp": 2, "iExitStatus": 2,
            "sMessage": "conftest import failed"}),
    )
    assert _fsState(_fdictStates(dictMarker), "quantitative") == "untested"
    dictCleared = _fdictMergeOnto(
        dictMarker,
        _fdictSession("r3", 3, {S_FILE_A: _fdictWholeFile(
            S_FILE_A, {"test_a": "passed"})}))
    assert dictCleared["dictUnattributedFailure"] is None
    assert _fsState(_fdictStates(dictCleared), "quantitative") == "passed"


def test_skipped_and_xfailed_tests_do_not_block_a_pass():
    """Matching pytest's own exit status: only a failure is a failure."""
    dictMarker = _fdictMarkerAfter(
        _fdictSession("r1", 1, {S_FILE_A: _fdictWholeFile(S_FILE_A, {
            "test_a": "passed", "test_b": "skipped", "test_c": "xfailed",
            "test_d": "xpassed"})}))
    assert _fsState(_fdictStates(dictMarker), "quantitative") == "passed"


def test_a_test_file_newer_than_a_run_unproves_only_its_own_tests():
    """Narrowed from "any test file" to the test's own file."""
    dictMarker = _fdictMarkerAfter(
        _fdictSession("r1", 5, {
            S_FILE_A: _fdictWholeFile(S_FILE_A, {"test_a": "passed"}),
            S_FILE_B: _fdictWholeFile(S_FILE_B, {"test_b": "passed"})}))
    dictStates = _fdictStates(
        dictMarker, dictFileMtimes={S_FILE_A: 9, S_FILE_B: 1})
    assert _fsState(dictStates, "quantitative") == "untested"
    assert _fsState(dictStates, "integrity") == "passed"


def test_outputs_newer_than_a_run_make_its_results_stale():
    dictMarker = _fdictMarkerAfter(
        _fdictSession("r1", 5, {S_FILE_A: _fdictWholeFile(
            S_FILE_A, {"test_a": "passed"})}))
    assert _fsState(_fdictStates(
        dictMarker, fMaxOutputMtime=9), "quantitative") == "untested"
    assert _fsState(_fdictStates(
        dictMarker, fMaxOutputMtime=1), "quantitative") == "passed"


def test_a_present_file_the_marker_never_collected_is_untested_and_unspoken():
    dictMarker = _fdictMarkerAfter(
        _fdictSession("r1", 1, {S_FILE_A: _fdictWholeFile(
            S_FILE_A, {"test_a": "passed"})}))
    dictStates = _fdictStates(dictMarker)
    assert _fsState(dictStates, "integrity") == "untested"
    assert dictStates["integrity"]["bHasMarkerInfo"] is False
    assert dictStates["quantitative"]["bHasMarkerInfo"] is True


def test_the_counts_ride_beside_the_state():
    dictMarker = _fdictMarkerAfter(
        _fdictSession("r1", 1, {S_FILE_A: _fdictWholeFile(
            S_FILE_A, {"test_a": "passed"},
            listNodeIds=["test_a", "test_b", "test_c"])}))
    dictCategory = _fdictStates(dictMarker)["quantitative"]
    assert dictCategory["sState"] == "untested"
    assert (dictCategory["iCurrentTests"], dictCategory["iTotalTests"]) == (
        1, 3)


@pytest.mark.falsification
def test_an_old_format_marker_reads_as_it_always_did():
    """Published archives carry old markers; they cannot be rewritten.

    The counts speak for a category until a new run collects its files,
    and not a moment longer. A stamp-less (pre-2026-04) marker still
    cannot be trusted.

    Kills: skipping the normalizer, which would read an old marker as
    having no runs at all.
    """
    dictOld = {
        "sDirectory": "StepDir", "sLabel": "A01", "iExitStatus": 0,
        "fTimestamp": 5.0, "sRunAtUtc": "2026-10-01T00:00:00Z",
        "dictCategories": {
            "integrity": {"iPassed": 3, "iFailed": 0},
            "quantitative": {"iPassed": 1, "iFailed": 2}},
        "dictOutputHashes": {"StepDir/out.dat": "a" * 40},
    }
    dictStates = _fdictStates(dictOld)
    assert _fsState(dictStates, "integrity") == "passed"
    assert _fsState(dictStates, "quantitative") == "failed"
    assert dictStates["integrity"]["bLegacy"] is True
    assert _fsState(dictStates, "qualitative") == "untested"
    assert contract.fdictNormalizeMarker(
        contract.fdictNormalizeMarker(dictOld)) == (
        contract.fdictNormalizeMarker(dictOld))
    dictNoStamp = dict(dictOld, sRunAtUtc="")
    assert _fsState(_fdictStates(dictNoStamp), "integrity") == "untested"
    dictAfterRun = _fdictMergeOnto(
        dictOld, _fdictSession("r1", 9, {S_FILE_B: _fdictWholeFile(
            S_FILE_B, {"test_b": "passed"})}))
    dictAfterStates = _fdictStates(dictAfterRun)
    assert _fsState(dictAfterStates, "integrity") == "passed"
    assert dictAfterStates["integrity"]["bLegacy"] is False
    assert _fsState(dictAfterStates, "quantitative") == "failed"
    assert dictAfterStates["quantitative"]["bLegacy"] is True


def test_a_legacy_marker_with_a_newer_test_file_is_stale():
    dictOld = {
        "fTimestamp": 5.0, "sRunAtUtc": "2026-10-01T00:00:00Z",
        "dictCategories": {"integrity": {"iPassed": 3, "iFailed": 0}}}
    dictStates = _fdictStates(dictOld, dictFileMtimes={S_FILE_C: 9})
    assert _fsState(dictStates, "integrity") == "untested"


def test_runs_nothing_refers_to_are_pruned_and_absent_files_dropped():
    dictMarker = _fdictMarkerAfter(
        _fdictSession("r1", 1, {S_FILE_A: _fdictWholeFile(
            S_FILE_A, {"test_a": "passed"})}),
        _fdictSession("r2", 2, {S_FILE_A: _fdictWholeFile(
            S_FILE_A, {"test_a": "passed"})}))
    assert list(dictMarker["dictRuns"]) == ["r2"]
    dictAfter = _fdictMergeOnto(
        dictMarker,
        _fdictSession("r3", 3, {}, listPresentFiles=[S_FILE_B]))
    assert dictAfter["dictTestFiles"] == {}
    assert dictAfter["dictRuns"] == {}


def test_the_latest_run_is_the_newest_by_timestamp():
    dictMarker = _fdictMarkerAfter(
        _fdictSession("r1", 1, {S_FILE_A: _fdictWholeFile(
            S_FILE_A, {"test_a": "passed", "test_b": "passed"})}),
        _fdictSession("r2", 2, {S_FILE_A: _fdictNarrowed(
            S_FILE_A, {"test_a": "passed"})}))
    assert contract.ftLatestRun(dictMarker)[0] == "r2"
    assert contract.ftLatestRun(None) == ("", {})
    assert contract.ftLatestRun({"fTimestamp": 4})[0] == "legacy"


def test_a_non_dict_marker_has_no_states():
    assert contract.fdictNormalizeMarker(None) is None
    assert contract.fdictNormalizeMarker("not a marker") is None
    assert contract.fdictCategoryStatesFromMarker(
        None, [S_FILE_A], {}, {}, 0) == {}


def test_category_of_file_name_mirrors_the_conftest_prefix_rule():
    assert contract.fsCategoryOfFileName("test_integrity_x.py") == "integrity"
    assert contract.fsCategoryOfFileName("test_qualitative.py") == "qualitative"
    assert contract.fsCategoryOfFileName("test_quantitative_x.py") == (
        "quantitative")
    assert contract.fsCategoryOfFileName("test_other.py") == "other"
