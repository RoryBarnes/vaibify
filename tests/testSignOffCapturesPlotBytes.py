"""A sign-off records the exact bytes of every declared plot, server-side.

The researcher's sign-off attests the plots as they were when it was
given. Nothing recorded that until now: the poll's content pass was
the only writer of ``dictUserVerifiedHashes``, and while a step read
``passed`` it REPLACED the hashes with whatever was on disk, so a plot
changed without its mtime advancing was adopted as attested.

The route now hashes every declared plot at the moment ``sUser``
becomes ``passed``, refuses the sign-off (409, before anything is
written) when any one cannot be hashed, discards a client-supplied
baseline, and carries the stored baseline through every other update,
because the browser's PUT replaces the whole verification block.

Driven over HTTP through the real route, with a container double whose
id and name differ, and whose workflow declares one templated and one
literal plot.
"""

import copy
import json

import pytest

from tests import testAgentLaneEnforcement as agentLaneModule
from tests.testAgentUpdateStepAllowlist import (
    ClockedDockerDouble,
    S_CONTAINER_ID,
    ftBuildLanes,
)
from vaibify.gui.fileStatusManager import fsStepFileRepoRelative

S_TEMPLATED_KEY = "Plot/fig.pdf"
S_LITERAL_KEY = "stepA/figure.pdf"
S_KEY = "dictUserVerifiedHashes"
DICT_HASHES_ON_DISK = {
    S_TEMPLATED_KEY: {"sSha256": "1" * 64},
    S_LITERAL_KEY: {"sSha256": "2" * 64},
}


def _fdictWorkflowWithPlots():
    dictWorkflow = copy.deepcopy(agentLaneModule.DICT_WORKFLOW)
    dictWorkflow["listSteps"][0]["saPlotFiles"] = [
        "{sPlotDirectory}/fig.{sFigureType}", "figure.pdf"]
    return dictWorkflow


class PlotsDockerDouble(ClockedDockerDouble):
    """A container whose step declares two plots and hashes them as told."""

    dictWorkflow = _fdictWorkflowWithPlots()
    dictHashAnswers = DICT_HASHES_ON_DISK

    def fbaFetchFile(self, sContainerId, sPath, iMaxBytes=None):
        if sPath not in self._dictFiles and sPath.endswith(".json"):
            return json.dumps(self.dictWorkflow).encode("utf-8")
        return super().fbaFetchFile(sContainerId, sPath, iMaxBytes)

    def fdictHashContainerRepoPaths(self, sContainerId, sRoot, listRelPaths):
        return {
            sPath: dict(self.dictHashAnswers.get(sPath) or {"sSha256": None})
            for sPath in listRelPaths
        }


class UnreadablePlotDockerDouble(PlotsDockerDouble):
    dictHashAnswers = {
        S_TEMPLATED_KEY: {"sSha256": "1" * 64},
        S_LITERAL_KEY: {"sSha256": None},
    }


class MissingPlotDockerDouble(PlotsDockerDouble):
    dictHashAnswers = {
        S_TEMPLATED_KEY: {"sSha256": None, "bMissing": True},
        S_LITERAL_KEY: {"sSha256": "2" * 64},
    }


class NoPlotsDockerDouble(PlotsDockerDouble):
    dictWorkflow = copy.deepcopy(agentLaneModule.DICT_WORKFLOW)


def _fdictStored(clientBrowser):
    responseHttp = clientBrowser.get(f"/api/steps/{S_CONTAINER_ID}/0")
    assert responseHttp.status_code == 200, responseHttp.text
    return responseHttp.json()["dictVerification"]


def _fresponsePut(clientBrowser, dictVerification):
    return clientBrowser.put(
        f"/api/steps/{S_CONTAINER_ID}/0",
        json={"dictVerification": dictVerification})


def _fdictSignOff(clientBrowser):
    dictVerification = dict(_fdictStored(clientBrowser), sUser="passed")
    responseHttp = _fresponsePut(clientBrowser, dictVerification)
    assert responseHttp.status_code == 200, responseHttp.text
    return _fdictStored(clientBrowser)


def test_a_sign_off_records_every_declared_plot_under_the_polls_keys():
    from vaibify.gui.routes.pipelineRoutes import _flistAllOutputRepoPaths
    clientBrowser, _clientAgent, _app = ftBuildLanes(PlotsDockerDouble)
    dictStored = _fdictSignOff(clientBrowser)
    assert dictStored[S_KEY] == {
        S_TEMPLATED_KEY: "1" * 64, S_LITERAL_KEY: "2" * 64}
    dictWorkflow = dict(_fdictWorkflowWithPlots(), sProjectRepoPath="/workspace")
    dictStep = dictWorkflow["listSteps"][0]
    assert [
        fsStepFileRepoRelative(dictWorkflow, dictStep, sFile)
        for sFile in dictStep["saPlotFiles"]
    ] == [S_TEMPLATED_KEY, S_LITERAL_KEY]
    listSampled = _flistAllOutputRepoPaths(dictWorkflow, "/workspace")
    assert {S_TEMPLATED_KEY, S_LITERAL_KEY} <= set(listSampled)


@pytest.mark.falsification
def test_an_unreadable_plot_refuses_the_sign_off_and_writes_nothing():
    """Kills: a partial capture let through as the attested baseline."""
    clientBrowser, _clientAgent, _app = ftBuildLanes(UnreadablePlotDockerDouble)
    dictBefore = _fdictStored(clientBrowser)
    responseHttp = _fresponsePut(
        clientBrowser, dict(dictBefore, sUser="passed"))
    assert responseHttp.status_code == 409, responseHttp.text
    assert S_LITERAL_KEY in responseHttp.text
    assert _fdictStored(clientBrowser) == dictBefore


def test_a_missing_plot_refuses_the_sign_off_and_writes_nothing():
    clientBrowser, _clientAgent, _app = ftBuildLanes(MissingPlotDockerDouble)
    dictBefore = _fdictStored(clientBrowser)
    responseHttp = _fresponsePut(
        clientBrowser, dict(dictBefore, sUser="passed"))
    assert responseHttp.status_code == 409, responseHttp.text
    assert S_TEMPLATED_KEY in responseHttp.text
    assert _fdictStored(clientBrowser) == dictBefore


def test_a_client_supplied_baseline_is_discarded():
    clientBrowser, _clientAgent, _app = ftBuildLanes(PlotsDockerDouble)
    responseHttp = _fresponsePut(clientBrowser, {
        "sUnitTest": "untested", "sUser": "untested",
        S_KEY: {S_TEMPLATED_KEY: "f" * 64}})
    assert responseHttp.status_code == 200, responseHttp.text
    assert S_KEY not in _fdictStored(clientBrowser)
    responseHttp = _fresponsePut(clientBrowser, {
        "sUnitTest": "untested", "sUser": "passed",
        S_KEY: {S_TEMPLATED_KEY: "f" * 64}})
    assert responseHttp.status_code == 200, responseHttp.text
    assert _fdictStored(clientBrowser)[S_KEY] == {
        S_TEMPLATED_KEY: "1" * 64, S_LITERAL_KEY: "2" * 64}


def test_a_step_with_no_declared_plots_records_an_empty_baseline():
    clientBrowser, _clientAgent, _app = ftBuildLanes(NoPlotsDockerDouble)
    assert _fdictSignOff(clientBrowser)[S_KEY] == {}


@pytest.mark.falsification
def test_the_browsers_test_result_save_keeps_the_baseline():
    """Kills: an update that omits the baseline dropping it from the step.

    The browser saves a test result by PUTting its whole verification
    block, which carries no baseline; the route stores the block in
    place of the old one.
    """
    clientBrowser, _clientAgent, _app = ftBuildLanes(PlotsDockerDouble)
    dictSigned = _fdictSignOff(clientBrowser)
    dictBaseline = dictSigned[S_KEY]
    dictTestResult = {
        sKey: value for sKey, value in dictSigned.items() if sKey != S_KEY}
    dictTestResult["sUnitTest"] = "passed"
    assert dictTestResult["sUser"] == "passed"
    responseHttp = _fresponsePut(clientBrowser, dictTestResult)
    assert responseHttp.status_code == 200, responseHttp.text
    assert _fdictStored(clientBrowser)[S_KEY] == dictBaseline
    dictTestResult["sUser"] = "untested"
    responseHttp = _fresponsePut(clientBrowser, dictTestResult)
    assert responseHttp.status_code == 200, responseHttp.text
    dictAfter = _fdictStored(clientBrowser)
    assert dictAfter["sUser"] == "untested"
    assert dictAfter[S_KEY] == dictBaseline
