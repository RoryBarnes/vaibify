"""The marker plugin records every test's own outcome, session by session.

The plugin (``conftestManager``'s template) runs inside a researcher's
pytest session, so what it writes is only as good as a REAL session
says. Every test here runs pytest in a subprocess, in a throwaway
project, with the generated ``conftest.py``: the run ids, the hooks, the
locking and the merge are the ones a container would use.

The dashboard runs one session per test category, so a marker that holds
only the last session's summary forgets the other two. These tests pin
the replacement: each test keeps its own last outcome, narrowing a run
never hides a failure, and two sessions at once both survive.
"""

import json
import os

import pytest

from tests.perTestMarkerHarness import (
    S_PASS, S_ONE_FAILS, S_SLUG, S_STEP,
    fdictReadMarker, fdictStates, fiRunPytest, fnStartPytest, fnWrite,
    fsBuildProject, fsMarkerPath,
)

def _fsState(dictStates, sCategory):
    return dictStates[sCategory]["sState"]


@pytest.mark.falsification
def test_three_category_runs_leave_all_three_categories_recorded(tmp_path):
    """Run All Tests: one session per category, one marker, all three.

    Kills: skipping the read-modify-write, which would leave the marker
    holding only the last category's file.
    """
    sRepo = fsBuildProject(tmp_path, {
        "test_integrity_x.py": S_PASS, "test_qualitative_x.py": S_PASS,
        "test_quantitative_x.py": S_PASS})
    for sCategory in ("integrity", "qualitative", "quantitative"):
        iExit, sOutput = fiRunPytest(
            sRepo, ["tests/test_%s_x.py" % sCategory], sCategory)
        assert iExit == 0, sOutput
    dictStates = fdictStates(sRepo)
    for sCategory in ("integrity", "qualitative", "quantitative"):
        assert _fsState(dictStates, sCategory) == "passed", sCategory
    dictMarker = fdictReadMarker(sRepo)
    assert sorted(dictMarker["dictTestFiles"]) == [
        "test_integrity_x.py", "test_qualitative_x.py",
        "test_quantitative_x.py"]


def test_a_node_id_run_names_tests_relative_to_the_step(tmp_path):
    """Node ids do not depend on where pytest decided the rootdir was."""
    sRepo = fsBuildProject(tmp_path, {"test_quantitative_x.py": S_PASS})
    fiRunPytest(sRepo, ["tests/test_quantitative_x.py"])
    dictFile = fdictReadMarker(sRepo)["dictTestFiles"][
        "test_quantitative_x.py"]
    assert dictFile["listNodeIds"] == [
        "tests/test_quantitative_x.py::test_one",
        "tests/test_quantitative_x.py::test_two"]
    assert {sOutcome["sOutcome"] for sOutcome in
            dictFile["dictOutcomes"].values()} == {"passed"}


@pytest.mark.falsification
def test_a_narrowed_pass_after_a_failing_full_run_leaves_the_category_failed(
    tmp_path,
):
    """A full run with ``test_two`` failing, then ``-k test_one`` passing.

    Pytest exits 0 for the second run, and a marker that believed it
    would call the category passed while ``test_two`` is still broken.

    Kills: recording a narrowed run's pass at the category level.
    """
    sRepo = fsBuildProject(tmp_path, {"test_quantitative_x.py": S_ONE_FAILS})
    iFull, _ = fiRunPytest(sRepo, ["tests"])
    iNarrow, _ = fiRunPytest(sRepo, ["tests", "-k", "test_one"])
    assert (iFull, iNarrow) == (1, 0)
    assert _fsState(fdictStates(sRepo), "quantitative") == "failed"


def test_a_narrowed_pass_after_a_full_pass_keeps_the_category_passed(tmp_path):
    sRepo = fsBuildProject(tmp_path, {"test_quantitative_x.py": S_PASS})
    fiRunPytest(sRepo, ["tests"])
    fiRunPytest(sRepo, ["tests", "-k", "test_one"])
    assert _fsState(fdictStates(sRepo), "quantitative") == "passed"


@pytest.mark.falsification
def test_two_halves_at_one_data_state_pass_and_across_a_data_change_do_not(
    tmp_path,
):
    """Each half is judged against its own run's recorded hashes.

    Kills: counting an outcome without its run's data (the two halves
    would combine across a data change).
    """
    sRepo = fsBuildProject(tmp_path, {"test_quantitative_x.py": S_PASS})
    fiRunPytest(sRepo, ["tests", "-k", "test_one"])
    fnWrite(os.path.join(sRepo, S_STEP, "out.dat"), "v2")
    fiRunPytest(sRepo, ["tests", "-k", "test_two"])
    dictMarker = fdictReadMarker(sRepo)
    dictRuns = dictMarker["dictRuns"]
    assert len(dictRuns) == 2
    sFirst, sSecond = sorted(
        dictRuns, key=lambda sKey: dictRuns[sKey]["fTimestamp"])
    assert dictRuns[sFirst]["dictOutputHashes"] != dictRuns[sSecond][
        "dictOutputHashes"]
    dictStates = fdictStates(
        sRepo, {sFirst: "drift", sSecond: "match"})
    assert _fsState(dictStates, "quantitative") == "untested"
    dictStatesSameData = fdictStates(
        sRepo, {sFirst: "match", sSecond: "match"})
    assert _fsState(dictStatesSameData, "quantitative") == "passed"


@pytest.mark.falsification
def test_a_k_run_updates_the_test_list_and_a_node_id_run_does_not(tmp_path):
    """Whole-file collections list every test; ``file::test`` collects one.

    Kills: treating a node-id run as a whole-file collection.
    """
    sRepo = fsBuildProject(tmp_path, {"test_quantitative_x.py": S_PASS})
    fiRunPytest(sRepo, ["tests"])
    fnWrite(
        os.path.join(sRepo, S_STEP, "tests", "test_quantitative_x.py"),
        S_PASS + "\ndef test_three():\n    assert True\n")
    fiRunPytest(sRepo, ["tests/test_quantitative_x.py::test_one"])
    dictFile = fdictReadMarker(sRepo)["dictTestFiles"][
        "test_quantitative_x.py"]
    assert len(dictFile["listNodeIds"]) == 2
    fiRunPytest(sRepo, ["tests", "-k", "test_one"])
    dictFile = fdictReadMarker(sRepo)["dictTestFiles"][
        "test_quantitative_x.py"]
    assert len(dictFile["listNodeIds"]) == 3
    assert _fsState(fdictStates(sRepo), "quantitative") == "untested"


def test_a_deleted_test_stops_counting_after_the_next_whole_file_run(
    tmp_path,
):
    sRepo = fsBuildProject(tmp_path, {"test_quantitative_x.py": S_ONE_FAILS})
    fiRunPytest(sRepo, ["tests"])
    assert _fsState(fdictStates(sRepo), "quantitative") == "failed"
    fnWrite(
        os.path.join(sRepo, S_STEP, "tests", "test_quantitative_x.py"),
        "def test_one():\n    assert True\n")
    fiRunPytest(sRepo, ["tests"])
    assert _fsState(fdictStates(sRepo), "quantitative") == "passed"


@pytest.mark.falsification
def test_a_gui_category_collection_error_fails_the_category(tmp_path):
    """The category the dashboard names is authoritative.

    The file no longer imports, so the session never reaches a test; the
    older pass must not survive it.

    Kills: ignoring a collection report, which leaves the last good
    result standing over a file that cannot even be imported.
    """
    sRepo = fsBuildProject(tmp_path, {"test_quantitative_x.py": S_PASS})
    fiRunPytest(sRepo, ["tests"], "quantitative")
    assert _fsState(fdictStates(sRepo), "quantitative") == "passed"
    fnWrite(
        os.path.join(sRepo, S_STEP, "tests", "test_quantitative_x.py"),
        "def test_one(:\n")
    iExit, _ = fiRunPytest(
        sRepo, ["tests/test_quantitative_x.py"], "quantitative")
    assert iExit != 0
    dictFile = fdictReadMarker(sRepo)["dictTestFiles"][
        "test_quantitative_x.py"]
    assert dictFile["dictCollectionError"]["sMessage"]
    assert _fsState(fdictStates(sRepo), "quantitative") == "failed"
    fnWrite(
        os.path.join(sRepo, S_STEP, "tests", "test_quantitative_x.py"),
        S_PASS)
    fiRunPytest(sRepo, ["tests"], "quantitative")
    assert _fsState(fdictStates(sRepo), "quantitative") == "passed"


@pytest.mark.falsification
def test_an_unattributed_collection_error_unproves_older_results(tmp_path):
    """A broken file no category claims leaves older passes unproven.

    Kills: dropping the unattributed failure, which would let a session
    that failed to collect anything leave every earlier pass standing.
    """
    sRepo = fsBuildProject(tmp_path, {"test_quantitative_x.py": S_PASS})
    fiRunPytest(sRepo, ["tests"])
    fnWrite(
        os.path.join(sRepo, S_STEP, "tests", "test_misc.py"),
        "def test_one(:\n")
    iExit, _ = fiRunPytest(sRepo, ["tests/test_misc.py"])
    assert iExit != 0
    dictMarker = fdictReadMarker(sRepo)
    assert dictMarker["dictUnattributedFailure"]["sMessage"]
    assert _fsState(fdictStates(sRepo), "quantitative") == "untested"


@pytest.mark.falsification
def test_a_session_that_collects_nothing_writes_nothing(tmp_path):
    """Exit 5 with no attribution is not a result and leaves no trace.

    Kills: writing a marker for a session that ran no test.
    """
    sRepo = fsBuildProject(tmp_path, {"test_quantitative_x.py": S_PASS})
    iExit, _ = fiRunPytest(sRepo, ["tests", "-k", "no_test_has_this_name"])
    assert iExit == 5
    assert not os.path.exists(fsMarkerPath(sRepo))
    fiRunPytest(sRepo, ["tests"])
    sBefore = open(fsMarkerPath(sRepo)).read()
    iExit, _ = fiRunPytest(sRepo, ["tests", "-k", "no_test_has_this_name"])
    assert iExit == 5
    assert open(fsMarkerPath(sRepo)).read() == sBefore


@pytest.mark.falsification
def test_a_test_file_outside_the_dashboards_naming_is_still_recorded(tmp_path):
    """A run records the tests it ran, whatever their file is called.

    The dashboard lists ``test_*.py`` and counts the category files
    among them, but a researcher can run any file by path. Its outcome
    and the run's output hashes belong in the marker anyway: without
    them the run holds no result, is pruned, and takes the data-state
    baseline with it, so changed outputs would go unnoticed.

    Kills: keeping only ``test_*.py`` files when a session is merged.
    """
    sRepo = fsBuildProject(tmp_path, {"customChecks.py": S_PASS})
    iExit, sOutput = fiRunPytest(sRepo, ["tests/customChecks.py"])
    assert iExit == 0, sOutput
    dictMarker = fdictReadMarker(sRepo)
    listOutcomes = [
        sOutcome["sOutcome"]
        for dictFile in dictMarker["dictTestFiles"].values()
        for sOutcome in dictFile["dictOutcomes"].values()]
    assert listOutcomes == ["passed", "passed"], dictMarker
    assert dictMarker["dictRuns"] and all(
        dictRun["dictOutputHashes"]
        for dictRun in dictMarker["dictRuns"].values()), dictMarker
    assert "customChecks.py" in dictMarker["dictTestFiles"]


def test_an_old_format_marker_is_carried_forward_by_a_new_session(tmp_path):
    """A category that was not re-run keeps its old outcome."""
    sRepo = fsBuildProject(tmp_path, {
        "test_integrity_x.py": S_PASS, "test_quantitative_x.py": S_PASS})
    fnWrite(fsMarkerPath(sRepo), json.dumps({
        "sDirectory": S_STEP, "sLabel": "A01", "iExitStatus": 0,
        "fTimestamp": 4102444800.0, "sRunAtUtc": "2099-12-31T00:00:00Z",
        "dictCategories": {"integrity": {"iPassed": 2, "iFailed": 0}},
        "dictOutputHashes": {}}))
    fiRunPytest(sRepo, ["tests/test_quantitative_x.py"], "quantitative")
    dictMarker = fdictReadMarker(sRepo)
    assert "dictRuns" in dictMarker
    assert dictMarker["dictLegacyCategories"]["integrity"]["iPassed"] == 2
    assert "quantitative" not in dictMarker["dictLegacyCategories"]
    assert _fsState(fdictStates(sRepo), "quantitative") == "passed"


@pytest.mark.falsification
def test_two_sessions_at_once_both_survive(tmp_path):
    """The dashboard may start two categories together.

    Kills: dropping the lock around the read-modify-write, which lets
    the second session overwrite what the first just merged. Repeated,
    because a racy test passes often enough to hide the bug.
    """
    sRepo = fsBuildProject(tmp_path, {
        "test_integrity_x.py": S_PASS, "test_quantitative_x.py": S_PASS})
    # The throwaway project's own conftest holds the merge for half a
    # second, so two sessions finishing together overlap in it every
    # time; without the lock the second would overwrite the first.
    sConftest = os.path.join(sRepo, S_STEP, "tests", "conftest.py")
    with open(sConftest, "a") as fileConftest:
        fileConftest.write(
            "\n_fdictMergeUnslowed = fdictMergeSessionIntoMarker\n"
            "def fdictMergeSessionIntoMarker(dictExisting, dictSession):\n"
            "    time.sleep(0.5)\n"
            "    return _fdictMergeUnslowed(dictExisting, dictSession)\n")
    for _iRound in range(2):
        listProcesses = [
            fnStartPytest(
                sRepo, ["tests/test_%s_x.py" % sCategory], sCategory)
            for sCategory in ("integrity", "quantitative")]
        for processRun in listProcesses:
            processRun.communicate()
        dictMarker = fdictReadMarker(sRepo)
        assert sorted(dictMarker["dictTestFiles"]) == [
            "test_integrity_x.py", "test_quantitative_x.py"], _iRound
        for dictFile in dictMarker["dictTestFiles"].values():
            assert dictFile["listNodeIds"], _iRound
    dictStates = fdictStates(sRepo)
    assert _fsState(dictStates, "integrity") == "passed"
    assert _fsState(dictStates, "quantitative") == "passed"


def test_skipped_and_xfailed_tests_do_not_block_a_pass(tmp_path):
    sRepo = fsBuildProject(tmp_path, {"test_quantitative_x.py": (
        "import pytest\n\n"
        "def test_one():\n    assert True\n\n"
        "@pytest.mark.skip(reason='not now')\n"
        "def test_two():\n    assert False\n\n"
        "@pytest.mark.xfail\n"
        "def test_three():\n    assert False\n\n"
        "@pytest.mark.xfail\n"
        "def test_four():\n    assert True\n")})
    iExit, sOutput = fiRunPytest(sRepo, ["tests"])
    assert iExit == 0, sOutput
    dictOutcomes = fdictReadMarker(sRepo)["dictTestFiles"][
        "test_quantitative_x.py"]["dictOutcomes"]
    assert sorted(dictOutcome["sOutcome"]
                  for dictOutcome in dictOutcomes.values()) == [
        "passed", "skipped", "xfailed", "xpassed"]
    assert _fsState(fdictStates(sRepo), "quantitative") == "passed"


def test_a_setup_error_is_a_failure(tmp_path):
    sRepo = fsBuildProject(tmp_path, {"test_quantitative_x.py": (
        "import pytest\n\n"
        "@pytest.fixture\n"
        "def broken():\n    raise RuntimeError('no')\n\n"
        "def test_one(broken):\n    assert True\n")})
    fiRunPytest(sRepo, ["tests"])
    assert _fsState(fdictStates(sRepo), "quantitative") == "failed"


def test_an_unwritable_marker_directory_never_fails_the_session(tmp_path):
    """Bookkeeping must not turn a passing suite red."""
    sRepo = fsBuildProject(tmp_path, {"test_quantitative_x.py": S_PASS})
    sMarkerDirectory = os.path.dirname(fsMarkerPath(sRepo))
    os.makedirs(os.path.dirname(sMarkerDirectory), exist_ok=True)
    with open(sMarkerDirectory, "w") as fileBlocker:
        fileBlocker.write("a file where the directory should be")
    iExit, sOutput = fiRunPytest(sRepo, ["tests"])
    assert iExit == 0, sOutput
    assert "could not write the test-result marker" in sOutput
