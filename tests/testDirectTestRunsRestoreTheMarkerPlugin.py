"""Every test entry point restores the marker plugin before it runs.

The marker plugin is git-ignored, so a ``git clean`` removes it, and a
pytest run without it writes no marker. Only the pipeline run
refreshed it uncached; the direct routes -- Run tests, a single
category, save-and-run -- and the ``runAllTests`` action trusted the
connect-time sweep, which is cached for the hub's whole process. So a
passing run, a removed plugin and then a FAILING run left the earlier
pass as the newest marker, and the next poll reported the failed step
as passed (found by external review, reproduced on a real host project
through the real HTTP routes).

The oracle is the poll, not the response: the response always carried
the right exit code, and the badge read the stale marker beside it.

When the plugin cannot be restored -- a directory nobody may write --
the run is REFUSED before pytest starts, naming the files: a run that
cannot be recorded would show its failure once and the previous pass
on every poll after it (found by a second external review).
"""

import json
import os
import shutil
from unittest.mock import AsyncMock, patch

import pytest

from tests.testHostGitAndReposPanel import (
    S_PROJECT, tHostPanel, _fbPytestIsInvokableAsTheDashboardInvokesIt,
    _fnAddStepToTheOpenWorkflow, _flistMarkerFiles,
)
from vaibify.gui import conftestManager


S_QUALITATIVE_FILE = "test_qualitative_flag.py"


def _fnWriteATestThatFailsWhileTheFlagExists(sProject, sFlagPath):
    """Write a qualitative test whose SOURCE never changes between runs.

    Its outcome is flipped by a file outside the project, so the second
    run's marker is not made stale by an edit -- the case under test is
    a marker that is stale because no new one was written.
    """
    sTests = os.path.join(sProject, "QuickCheck", "tests")
    os.makedirs(sTests)
    with open(os.path.join(sTests, S_QUALITATIVE_FILE), "w") as fileTest:
        fileTest.write(
            "import os\n\n\ndef testTheFlagIsAbsent():\n"
            f"    assert not os.path.exists({sFlagPath!r})\n"
        )
    with open(
        os.path.join(sProject, "QuickCheck", "numbers.json"), "w",
    ) as fileOutput:
        fileOutput.write('{"listValues": [1, 2, 3]}')


def _fnPointTheQualitativeCategoryAtTheFlagTest(client, sProject):
    """Name the qualitative test the poll files under that category."""
    sWorkflowPath = os.path.join(
        sProject, ".vaibify", "projects", "panel.json")
    with open(sWorkflowPath) as fileWorkflow:
        dictWorkflow = json.load(fileWorkflow)
    dictWorkflow["listSteps"][0]["dictTests"]["dictQualitative"] = {
        "saCommands": [f"python -m pytest tests/{S_QUALITATIVE_FILE} -q"],
        "sFilePath": f"QuickCheck/tests/{S_QUALITATIVE_FILE}",
    }
    with open(sWorkflowPath, "w") as fileWorkflow:
        json.dump(dictWorkflow, fileWorkflow)
    responseConnect = client.post(
        f"/api/connect/{S_PROJECT}",
        params={"sWorkflowPath": sWorkflowPath},
    )
    assert responseConnect.status_code == 200, responseConnect.text


def _fsQualitativeStateOnThePoll(client):
    responsePoll = client.get(f"/api/pipeline/{S_PROJECT}/file-status")
    assert responsePoll.status_code == 200, responsePoll.text
    return responsePoll.json()["dictTestMarkers"]["0"][
        "dictCategoryStates"]["qualitative"]["sState"]


@pytest.mark.falsification
def testAFailureAfterTheIgnoredPluginWasRemovedIsNotShownAsAPass(
    tHostPanel, tmp_path,
):
    """Kills: running a direct test route without the uncached refresh."""
    if not _fbPytestIsInvokableAsTheDashboardInvokesIt():
        pytest.skip(
            "`python -m pytest` does not run on this machine, which is "
            "exactly what the dashboard invokes for a host project"
        )
    client, sProject = tHostPanel
    sFlagPath = str(tmp_path / "failTheTest.flag")
    _fnWriteATestThatFailsWhileTheFlagExists(sProject, sFlagPath)
    _fnAddStepToTheOpenWorkflow(client, sProject)
    _fnPointTheQualitativeCategoryAtTheFlagTest(client, sProject)
    responsePassed = client.post(f"/api/steps/{S_PROJECT}/0/run-tests")
    assert responsePassed.json()["bPassed"] is True, responsePassed.text
    assert _flistMarkerFiles(sProject)
    assert _fsQualitativeStateOnThePoll(client) == "passed"

    sPlugin = conftestManager.flistMarkerPluginPaths(sProject)[1]
    shutil.rmtree(os.path.dirname(sPlugin))
    open(sFlagPath, "w").close()
    responseFailed = client.post(f"/api/steps/{S_PROJECT}/0/run-tests")
    assert responseFailed.status_code == 200, responseFailed.text
    assert responseFailed.json()["bPassed"] is False, responseFailed.text
    assert os.path.isfile(sPlugin), "the run did not restore the plugin"
    assert _fsQualitativeStateOnThePoll(client) == "failed", (
        "the poll reports the step as passed after its tests failed"
    )


def _fnPassThenRemoveThePlugin(client, sProject, sFlagPath):
    """Record a passing run, then take the plugin away as git clean does."""
    _fnWriteATestThatFailsWhileTheFlagExists(sProject, sFlagPath)
    _fnAddStepToTheOpenWorkflow(client, sProject)
    _fnPointTheQualitativeCategoryAtTheFlagTest(client, sProject)
    responsePassed = client.post(f"/api/steps/{S_PROJECT}/0/run-tests")
    assert responsePassed.json()["bPassed"] is True, responsePassed.text
    sPlugin = conftestManager.flistMarkerPluginPaths(sProject)[1]
    shutil.rmtree(os.path.dirname(sPlugin))
    return sPlugin


def _fdictMarkerRuns(sProject):
    listMarkers = _flistMarkerFiles(sProject)
    assert len(listMarkers) == 1, listMarkers
    with open(listMarkers[0]) as fileMarker:
        return json.load(fileMarker).get("dictRuns") or {}


@pytest.mark.falsification
def testATestRunWhosePluginCannotBeRestoredIsRefusedNotRun(
    tHostPanel, tmp_path,
):
    """Kills: running the tests although the refresh could not land.

    Run, the dashboard would show this run's failure once and then the
    previous run's pass on every poll after it, because nothing could
    record it. Refused, nothing ran and the last recorded run is still
    the truth -- and the refusal names the file to fix.
    """
    if not _fbPytestIsInvokableAsTheDashboardInvokesIt():
        pytest.skip(
            "`python -m pytest` does not run on this machine, which is "
            "exactly what the dashboard invokes for a host project"
        )
    if os.geteuid() == 0:
        pytest.skip("permissions do not bind root, so nothing is refused")
    client, sProject = tHostPanel
    sFlagPath = str(tmp_path / "failTheTest.flag")
    sPlugin = _fnPassThenRemoveThePlugin(client, sProject, sFlagPath)
    dictRunsBefore = _fdictMarkerRuns(sProject)
    sPluginDirectory = os.path.dirname(sPlugin)
    os.makedirs(sPluginDirectory)
    os.chmod(sPluginDirectory, 0o500)
    open(sFlagPath, "w").close()
    try:
        responseRefused = client.post(f"/api/steps/{S_PROJECT}/0/run-tests")
    finally:
        os.chmod(sPluginDirectory, 0o700)
    assert responseRefused.status_code == 409, responseRefused.text
    sDetail = responseRefused.json()["detail"]
    assert "were not run" in sDetail and "markerPlugin.py" in sDetail, sDetail
    assert _fdictMarkerRuns(sProject) == dictRunsBefore, (
        "pytest ran although its result could not be recorded")


@pytest.mark.parametrize("sRoute,dictBody", [
    ("run-test-category", {"sCategory": "qualitative"}),
    ("save-and-run-test", {
        "sFilePath": "QuickCheck/tests/testNumbers.py",
        "sContent": "def testOne():\n    assert True\n",
    }),
])
def testEveryDirectTestRouteRefusesAnUnrecordableRun(
    tHostPanel, monkeypatch, sRoute, dictBody,
):
    """The category and save-and-run routes refuse the same way."""
    from vaibify.gui import pipelineRunner
    sFailure = "Could not update 1 test-support file(s): a sentinel."
    monkeypatch.setattr(
        pipelineRunner, "_flistRefreshConftestsBeforeRun",
        lambda *tArguments, **dictKeywords: [sFailure],
    )
    client, sProject = tHostPanel
    os.makedirs(os.path.join(sProject, "QuickCheck", "tests"))
    _fnAddStepToTheOpenWorkflow(client, sProject)
    response = client.post(
        f"/api/steps/{S_PROJECT}/0/{sRoute}", json=dictBody)
    assert response.status_code == 409, response.text
    assert response.json()["detail"].startswith(sFailure), response.text


def _ftRunAllTestsWithRefreshAnswering(listRefreshAnswer):
    """Run ``runAllTests``; return (events, refreshed, steps-run mock)."""
    import asyncio
    from vaibify.gui.pipelineTestRunner import fiRunAllTests
    dictWorkflow = {"listSteps": []}
    listRefreshed = []

    def flistRefresh(connectionDocker, sContainerId, dictWorkflowSeen):
        listRefreshed.append((sContainerId, dictWorkflowSeen))
        return listRefreshAnswer

    fnCallback = AsyncMock()
    with patch("vaibify.gui.pipelineRunner._flistRefreshConftestsBeforeRun",
               flistRefresh), \
            patch("vaibify.gui.pipelineRunner._fnInjectDeterminismEnvPrefix",
                  new_callable=AsyncMock), \
            patch("vaibify.gui.pipelineTestRunner._fiRunTestsForAllSteps",
                  new_callable=AsyncMock, return_value=0) as mockRunSteps:
        iExit = asyncio.run(fiRunAllTests(
            object(), "containerAlpha", dictWorkflow, "/workspace",
            fnCallback,
        ))
    listEvents = [tCall.args[0] for tCall in fnCallback.await_args_list]
    return iExit, listEvents, listRefreshed, mockRunSteps


@pytest.mark.falsification
def testRunAllTestsRefreshesTheTestSupportFilesBeforeRunning():
    """Kills: dropping the uncached refresh from ``runAllTests``."""
    iExit, _listEvents, listRefreshed, mockRunSteps = (
        _ftRunAllTestsWithRefreshAnswering([]))
    assert iExit == 0
    assert [sContainerId for sContainerId, _ in listRefreshed] == [
        "containerAlpha"]
    mockRunSteps.assert_awaited_once()


@pytest.mark.falsification
def testRunAllTestsRefusesWhenTheRefreshCannotLand():
    """Kills: running every step's tests after a refresh that failed."""
    sFailure = "Could not update 1 test-support file(s): a sentinel."
    iExit, listEvents, _listRefreshed, mockRunSteps = (
        _ftRunAllTestsWithRefreshAnswering([sFailure]))
    assert iExit == 1
    mockRunSteps.assert_not_awaited()
    listRefusals = [dictEvent for dictEvent in listEvents
                    if dictEvent.get("sType") == "preflightFailed"]
    assert len(listRefusals) == 1, listEvents
    assert listRefusals[0]["listErrors"][0].startswith(sFailure)
    assert "were not run" in listRefusals[0]["listErrors"][0]
