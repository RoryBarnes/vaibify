"""Real pytest sessions, then the real poll, then a fresh-clone bootstrap.

The plugin tests prove what a session writes; the contract tests prove
what a marker means. This proves they meet: a project whose test
categories were each run in their OWN session (as the dashboard runs
them) is polled by the real poll over a connection whose answers come
from the real directory, and every category survives -- across polls,
and into a fresh clone's bootstrap. A data change then unproves what it
should and nothing else.
"""

import asyncio
import hashlib
import json
import os
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tests.perTestMarkerHarness import (
    S_PASS, S_SLUG, S_STEP, fiRunPytest, fnWrite, fsBuildProject,
)
from tests.snapshotProgramHarness import LocalSnapshotConnection
from vaibify.gui import stateManager, workflowManager
from vaibify.gui.routes import pipelineRoutes

S_CONTAINER_ID = "cid-per-test-marker"
LIST_CATEGORIES = ("integrity", "qualitative", "quantitative")


class LocalProjectConnection(LocalSnapshotConnection):
    """Answers every question the poll asks from a real local directory."""

    def fbaFetchFile(self, sContainerId, sPath, iMaxBytes=None):
        with open(sPath, "rb") as fileIn:
            return fileIn.read()

    def fdictFetchSmallFiles(self, sContainerId, listPaths):
        dictFiles = {}
        for sPath in listPaths:
            try:
                dictFiles[sPath] = self.fbaFetchFile(sContainerId, sPath)
            except OSError:
                dictFiles[sPath] = None
        return dictFiles

    def fdictStatPathMtimes(self, sContainerId, listPaths):
        return {
            sPath: str(int(os.path.getmtime(sPath)))
            for sPath in listPaths if os.path.exists(sPath)}

    def fsHashContainerFileSha256(self, sContainerId, sPath):
        try:
            return hashlib.sha256(self.fbaFetchFile(
                sContainerId, sPath)).hexdigest()
        except OSError:
            return ""

    def ftResultExecuteCommand(self, sContainerId, sCommand, sWorkdir=None):
        processRun = subprocess.run(
            ["bash", "-c", sCommand], capture_output=True, text=True)
        return (processRun.returncode, processRun.stdout)

    def ftRunInContainerStreamed(self, sContainerId, sCommand, **dictKw):
        processRun = subprocess.run(
            ["bash", "-c", sCommand], capture_output=True, text=True)
        return SimpleNamespace(
            iExitCode=processRun.returncode, sStdout=processRun.stdout,
            sStderr=processRun.stderr)


def _fsRunAllCategoriesSeparately(tmp_path, dictFiles=None):
    sRepo = fsBuildProject(tmp_path, dictFiles or {
        "test_integrity_x.py": S_PASS, "test_qualitative_x.py": S_PASS,
        "test_quantitative_x.py": S_PASS})
    for sCategory in LIST_CATEGORIES:
        iExit, sOutput = fiRunPytest(
            sRepo, ["tests/test_%s_x.py" % sCategory], sCategory)
        assert iExit == 0, sOutput
    return sRepo


def _fdictWorkflowFor(sRepo):
    return {
        "sProjectRepoPath": sRepo,
        workflowManager.S_LOADED_FROM_KEY: os.path.join(
            sRepo, ".vaibify", "projects", S_SLUG + ".json"),
        "listSteps": [{
            "sName": "Step A", "sLabel": "A01", "sDirectory": S_STEP,
            "sStepId": "step-a", "bNoInputData": True,
            "saOutputDataFiles": ["out.dat"], "saPlotFiles": [],
            "saDataCommands": [], "saTestCommands": [],
            "dictTests": {
                "dict" + sCategory.capitalize(): {"saCommands": [
                    "python -m pytest tests/test_%s_x.py" % sCategory]}
                for sCategory in LIST_CATEGORIES},
            "dictVerification": {
                "sUnitTest": "untested", "sIntegrity": "untested",
                "sQualitative": "untested", "sQuantitative": "untested",
                "sUser": "untested"},
        }],
    }


def _fdictPollContext(sRepo, dictWorkflow):
    return {
        "docker": LocalProjectConnection(), "save": MagicMock(),
        "files": object(), "paths": {},
        "workflows": {S_CONTAINER_ID: dictWorkflow},
        "variables": MagicMock(return_value={}),
    }


def _fdictPoll(dictCtx, dictWorkflow):
    return asyncio.run(pipelineRoutes.fdictComputeFileStatus(
        dictCtx, S_CONTAINER_ID, dictWorkflow, {}))


def _fdictAxes(dictWorkflow):
    dictVerification = dictWorkflow["listSteps"][0]["dictVerification"]
    return {
        sCategory: dictVerification["s" + sCategory.capitalize()]
        for sCategory in LIST_CATEGORIES}


@pytest.mark.falsification
def test_each_category_session_survives_the_poll_and_stays_across_polls(
    tmp_path,
):
    """Run All Tests, then poll: all three read passed, poll after poll.

    The reported bug, whole: three sessions, three categories, one
    marker; the old marker held only the last category run, so two read
    untested and the aggregate folded to untested.

    Kills: applying a marker without the per-run verdicts that the file
    half of the poll computed, and dropping a category's results when a
    later session writes the marker.
    """
    sRepo = _fsRunAllCategoriesSeparately(tmp_path)
    dictWorkflow = _fdictWorkflowFor(sRepo)
    dictCtx = _fdictPollContext(sRepo, dictWorkflow)
    for _iPoll in range(3):
        dictAnswer = _fdictPoll(dictCtx, dictWorkflow)
        assert _fdictAxes(dictWorkflow) == {
            sCategory: "passed" for sCategory in LIST_CATEGORIES}
        assert dictWorkflow["listSteps"][0]["dictVerification"][
            "sUnitTest"] == "passed"
    dictStates = dictAnswer["dictTestMarkers"]["0"]["dictCategoryStates"]
    for sCategory in LIST_CATEGORIES:
        assert dictStates[sCategory]["sState"] == "passed"
        assert dictStates[sCategory]["iCurrentTests"] == 2
        assert dictStates[sCategory]["iTotalTests"] == 2
    assert "_dictRunVerdictsByStep" not in dictAnswer


@pytest.mark.falsification
def test_a_data_change_unproves_every_run_and_a_rerun_proves_only_its_own(
    tmp_path,
):
    """Passes from runs at different data states never combine.

    After the data changes, every earlier run described data that no
    longer exists. Re-running one category proves THAT category at the
    new data and leaves the other two untested, however fresh their old
    passes once were.

    Kills: judging an outcome against the newest run instead of its
    own, which would let the rerun vouch for the other categories.
    """
    sRepo = _fsRunAllCategoriesSeparately(tmp_path)
    dictWorkflow = _fdictWorkflowFor(sRepo)
    dictCtx = _fdictPollContext(sRepo, dictWorkflow)
    _fdictPoll(dictCtx, dictWorkflow)
    fnWrite(os.path.join(sRepo, S_STEP, "out.dat"), "v2 -- changed")
    _fdictPoll(dictCtx, dictWorkflow)
    assert set(_fdictAxes(dictWorkflow).values()) == {"untested"}
    fiRunPytest(sRepo, ["tests/test_integrity_x.py"], "integrity")
    _fdictPoll(dictCtx, dictWorkflow)
    assert _fdictAxes(dictWorkflow) == {
        "integrity": "passed", "qualitative": "untested",
        "quantitative": "untested"}


def test_an_unchanged_project_is_a_fixed_point_of_the_whole_poll(tmp_path):
    sRepo = _fsRunAllCategoriesSeparately(tmp_path)
    dictWorkflow = _fdictWorkflowFor(sRepo)
    dictCtx = _fdictPollContext(sRepo, dictWorkflow)
    _fdictPoll(dictCtx, dictWorkflow)
    _fdictPoll(dictCtx, dictWorkflow)
    dictCtx["save"].reset_mock()
    dictAnswer = _fdictPoll(dictCtx, dictWorkflow)
    assert dictAnswer["dictInvalidatedSteps"] == {}
    assert dictCtx["save"].call_count == 0


@pytest.mark.falsification
def test_a_fresh_clone_restores_all_three_categories(tmp_path):
    """The bootstrap reads the marker the sessions left, per category.

    Kills: reading only the last run's categories when synthesizing
    state on a fresh checkout.
    """
    sRepo = _fsRunAllCategoriesSeparately(tmp_path)
    dictWorkflow = _fdictWorkflowFor(sRepo)
    dictState = stateManager.fdictBootstrapStateFromMarkers(
        LocalProjectConnectionForBootstrap(), S_CONTAINER_ID, dictWorkflow,
        sRepo)
    dictVerification = dictState["dictStepState"]["step-a"][
        "dictVerification"]
    for sCategory in LIST_CATEGORIES:
        assert dictVerification["s" + sCategory.capitalize()] == (
            "passed-from-marker"), sCategory
    assert dictVerification["sUnitTest"] == "passed-from-marker"


class LocalProjectConnectionForBootstrap(LocalProjectConnection):
    """The bootstrap's own reads: markers and on-disk blob digests."""

    def fdictHashContainerRepoPaths(self, sContainerId, sRoot, listPaths):
        return {}


def test_an_oversized_marker_reads_unknown_and_the_poll_still_succeeds(
    tmp_path, monkeypatch,
):
    """One marker over the batched read's ceiling must not fail the poll.

    The ceiling is lowered for this test rather than a four-megabyte
    marker written: what matters is the shape of the failure, which is
    that one step's marker is unreadable, that step reads unknown, and
    nothing else about the poll changes.
    """
    from vaibify.docker import dockerConnection
    sRepo = _fsRunAllCategoriesSeparately(tmp_path)
    dictWorkflow = _fdictWorkflowFor(sRepo)
    dictCtx = _fdictPollContext(sRepo, dictWorkflow)
    connection = dictCtx["docker"]
    fnOriginal = connection.fdictFetchSmallFiles

    def fdictRefusingTheBatch(sContainerId, listPaths):
        if len(listPaths) == 1 and listPaths[0].endswith("StepA.json"):
            raise ValueError("StepA.json exceeds the small-file ceiling.")
        return fnOriginal(sContainerId, listPaths)

    connection.fdictFetchSmallFiles = fdictRefusingTheBatch
    _fdictPoll(dictCtx, dictWorkflow)
    dictAnswer = _fdictPoll(dictCtx, dictWorkflow)
    assert dictAnswer["dictInvalidatedSteps"] == {}
    listBlockers = [
        dictBlocker for dictBlocker in dictAnswer["listBlockers"]
        if dictBlocker["sCriterion"] == "test-freshness-unchecked"]
    assert listBlockers and listBlockers[0]["listOffendingFiles"] == [
        os.path.join(
            sRepo, ".vaibify", "test_markers", S_SLUG, "StepA.json")]
    assert dictAnswer["iProofLevel"] == 0


@pytest.mark.falsification
def test_the_poll_wire_carries_states_and_a_run_summary_never_the_test_lists(
    tmp_path,
):
    """The page reads three fields per step, and the poll never grows with the tests.

    A marker now holds a node id per test, which is the reason it can
    outgrow the batched read. The wire carries only what the page
    applies: each category's derived state with its counts, the newest
    run's identity, and whether that run still stands. The per-run
    verdicts the file half computes ride a private key that is popped
    before the answer leaves.

    Kills: putting the marker itself on the wire.
    """
    sRepo = _fsRunAllCategoriesSeparately(tmp_path)
    dictWorkflow = _fdictWorkflowFor(sRepo)
    dictAnswer = _fdictPoll(
        _fdictPollContext(sRepo, dictWorkflow), dictWorkflow)
    dictEntry = json.loads(json.dumps(dictAnswer))["dictTestMarkers"]["0"]
    assert set(dictEntry) == {"dictMarker", "dictCategoryStates", "bStale"}
    assert set(dictEntry["dictMarker"]) == {
        "sRunId", "fTimestamp", "sRunAtUtc", "iExitStatus"}
    assert dictEntry["dictMarker"]["sRunId"]
    assert set(dictEntry["dictCategoryStates"]) == set(LIST_CATEGORIES)
    for dictState in dictEntry["dictCategoryStates"].values():
        assert set(dictState) == {
            "sState", "iTotalTests", "iCurrentTests", "iFailedTests",
            "bHasMarkerInfo", "bLegacy", "sNotCurrentBecause"}
        assert dictState["sState"] in ("failed", "passed", "untested")
    assert "dictTestFiles" not in json.dumps(dictAnswer)
