"""What a running L3 rerun prints reaches the PROOF tab, bounded and redacted.

The rerun used to be given no status callback at all, so a verification
that ran for an hour showed one sentence and a pulse. The hub now keeps
the last lines of the throwaway container's output per project; these
tests drive the record and the route that fills and serves it.
"""

from unittest.mock import patch

import pytest

from vaibify.gui import verificationProgress
from vaibify.gui.routes.reproducibilityRoutes import _fdictRunReproductionSync


S_CONTAINER_ID = "containerLiveOutput"
S_PROJECT = "/workspace/projectAlpha"
S_OTHER_PROJECT = "/workspace/projectBeta"
DICT_WORKFLOW = {"listSteps": [
    {"sName": "GenerateSamples", "sDirectory": "GenerateSamples"},
    {"sName": "PlotHistogram", "sDirectory": "PlotHistogram"},
]}


@pytest.fixture(autouse=True)
def fixtureForgetLiveOutput():
    verificationProgress.DICT_LIVE_OUTPUT.clear()
    yield
    verificationProgress.DICT_LIVE_OUTPUT.clear()


def fnRecord(dictEvent, sProject=S_PROJECT):
    verificationProgress.fnRecordRerunEvent(
        S_CONTAINER_ID, sProject, dictEvent, DICT_WORKFLOW,
    )


def test_the_record_keeps_the_newest_lines_names_the_step_and_counts_all():
    verificationProgress.fnOpenLiveOutput(S_CONTAINER_ID, S_PROJECT, "attempt1")
    fnRecord({"sType": "stepStarted", "iStepNumber": 2})
    iLines = verificationProgress.I_LIVE_OUTPUT_LINES + 50
    fnRecord({"sType": "outputBatch",
              "listLines": [f"line {iLine}" for iLine in range(iLines)]})
    dictLive = verificationProgress.fdictReadLiveOutput(S_CONTAINER_ID, S_PROJECT)
    assert dictLive["sStepLabel"] == "A02"
    assert dictLive["sStepName"] == "PlotHistogram"
    assert dictLive["iLineCount"] == iLines + 1
    assert len(dictLive["saTailLines"]) == verificationProgress.I_LIVE_OUTPUT_LINES
    assert dictLive["saTailLines"][-1] == f"line {iLines - 1}"
    assert dictLive["sAttemptId"] == "attempt1"


def test_a_line_is_redacted_and_capped_before_it_is_kept():
    verificationProgress.fnOpenLiveOutput(S_CONTAINER_ID, S_PROJECT, "attempt1")
    sToken = "ghp_" + "b" * 36
    fnRecord({"sType": "output", "sLine": f"pushing with {sToken}"})
    fnRecord({"sType": "output", "sLine": "x" * 50000})
    listLines = verificationProgress.fdictReadLiveOutput(
        S_CONTAINER_ID, S_PROJECT)["saTailLines"]
    assert sToken not in listLines[0]
    assert len(listLines[1]) < 50000


def test_one_projects_output_is_never_another_projects():
    verificationProgress.fnOpenLiveOutput(S_CONTAINER_ID, S_PROJECT, "attempt1")
    fnRecord({"sType": "output", "sLine": "alpha"})
    fnRecord({"sType": "output", "sLine": "beta"}, sProject=S_OTHER_PROJECT)
    assert verificationProgress.fdictReadLiveOutput(
        S_CONTAINER_ID, S_OTHER_PROJECT) is None
    assert verificationProgress.fdictReadLiveOutput(
        S_CONTAINER_ID, S_PROJECT)["saTailLines"] == ["alpha"]


def test_a_new_launch_starts_an_empty_record():
    verificationProgress.fnOpenLiveOutput(S_CONTAINER_ID, S_PROJECT, "attempt1")
    fnRecord({"sType": "output", "sLine": "old"})
    verificationProgress.fnOpenLiveOutput(S_CONTAINER_ID, S_PROJECT, "attempt2")
    dictLive = verificationProgress.fdictReadLiveOutput(S_CONTAINER_ID, S_PROJECT)
    assert dictLive["saTailLines"] == [] and dictLive["sAttemptId"] == "attempt2"


@pytest.mark.falsification
def test_the_route_gives_the_rerun_a_recorder_that_fills_the_record(tmp_path):
    """Kills: running the shadow rerun with no status callback.

    The route is the only place the rerun is told where its output
    goes; without the callback every line goes to the floor and the
    PROOF tab can only pulse.
    """
    sProject = str(tmp_path)
    verificationProgress.fnOpenLiveOutput(S_CONTAINER_ID, sProject, "attempt1")

    def fdictFakeRerun(*aArgs, fnStatusCallback=None, **kwargs):
        fnStatusCallback({"sType": "stepStarted", "iStepNumber": 1})
        fnStatusCallback({"sType": "outputBatch", "listLines": ["sampled 40"]})
        fnStatusCallback({"sType": "comparingOutputs"})
        return {"bPassed": True, "iOutputHashesMatched": 1,
                "iOutputHashesTotal": 1, "listDivergedHashes": []}
    with patch(
        "vaibify.gui.routes.reproducibilityRoutes.fdictRerunAndVerifyThroughShadow",
        side_effect=fdictFakeRerun,
    ):
        _fdictRunReproductionSync(
            None, S_CONTAINER_ID, DICT_WORKFLOW,
            sProject + "/.vaibify/workflows/project.json", sProject,
        )
    dictLive = verificationProgress.fdictReadLiveOutput(S_CONTAINER_ID, sProject)
    assert dictLive["saTailLines"] == [
        "--- Step A01: GenerateSamples ---", "sampled 40",
        "--- Every step has run; comparing the outputs with the manifest ---",
    ]
