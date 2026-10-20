"""Dismissing a scan-found dependency, over HTTP, through the real route.

A dismissal is the researcher's review and binds the script hash it was
made against. The route therefore refuses a finding that does not exist
(404) and one whose scan is no longer current (409) -- dismissing a stale
finding would record a review of code nobody saw -- and persists an
accepted one through the workflow save. The agent lane cannot reach it
(``bAgentSafe: False``, enforced server-side and pinned by the catalog
tests); the rescan beside it is read-only and agent-safe.
"""

import hashlib
from unittest.mock import patch

import pytest

from tests.testSyncRoutesCoverage import (  # noqa: F401 (fixture)
    S_CONTAINER_ID,
    _fnConnectToContainer,
    clientHttp,
)
from tests.testDependencyReconciliationGatesLevelOne import (
    S_PLOT_SCRIPT,
    S_PLOT_SOURCE,
    _fdictScanned,
    _fdictWorkflow,
)
from vaibify.gui.actionCatalog import LIST_AGENT_ACTIONS
from vaibify.reproducibility.repoFiles import HostRepoFiles


S_SCRIPT_ROUTES = "vaibify.gui.routes.scriptRoutes"


@pytest.fixture
def tRoute(tmp_path, clientHttp):  # noqa: F811
    sRepo = str(tmp_path / "repo")
    (tmp_path / "repo" / "PlotResults").mkdir(parents=True)
    (tmp_path / "repo" / S_PLOT_SCRIPT).write_text(S_PLOT_SOURCE)
    dictWorkflow = _fdictWorkflow(sRepo, _fdictScanned(sRepo))
    listSaves = []
    _fnConnectToContainer(clientHttp)
    with patch(S_SCRIPT_ROUTES + ".fdictRequireWorkflow",
               return_value=dictWorkflow), \
            patch(S_SCRIPT_ROUTES + ".ffilesForWorkflow",
                  return_value=HostRepoFiles(sRepo)), \
            patch(S_SCRIPT_ROUTES + ".fdictCommitWorkflowSave",
                  lambda *args, **kwargs: listSaves.append(args[2])):
        yield clientHttp, dictWorkflow, listSaves, sRepo


def _fresponseDismiss(clientHttp, sScanned="../MakeData/samples.csv",  # noqa: F811
                      sUpstream="make-data"):
    return clientHttp.post(
        f"/api/steps/{S_CONTAINER_ID}/2/dependencies/dismiss",
        json={"sScannedPath": sScanned, "sUpstreamStepId": sUpstream})


def testACurrentFindingIsDismissedAtItsScriptHash(tRoute):
    clientHttp, dictWorkflow, listSaves, _ = tRoute
    responseHttp = _fresponseDismiss(clientHttp)
    assert responseHttp.status_code == 200, responseHttp.text
    assert dictWorkflow["listSteps"][2]["listDismissedDependencyFindings"] == [{
        "sScannedPath": "../MakeData/samples.csv",
        "sUpstreamStepId": "make-data", "sScriptPath": S_PLOT_SCRIPT,
        "sScriptSha256": hashlib.sha256(
            S_PLOT_SOURCE.encode("utf-8")).hexdigest()}]
    assert listSaves == [dictWorkflow]


def testAnUnknownFindingIsRefusedByName(tRoute):
    clientHttp, dictWorkflow, listSaves, _ = tRoute
    responseHttp = _fresponseDismiss(clientHttp, sUpstream="fit-model")
    assert responseHttp.status_code == 404
    assert "Rescan dependencies" in responseHttp.json()["detail"]
    assert listSaves == []


@pytest.mark.falsification
def testAFindingWhoseScanIsNotCurrentCannotBeDismissed(tRoute):
    """Kills: dismissing a finding without checking it is current."""
    clientHttp, dictWorkflow, listSaves, sRepo = tRoute
    with open(sRepo + "/" + S_PLOT_SCRIPT, "a") as fileOut:
        fileOut.write("# edited after the scan\n")
    responseHttp = _fresponseDismiss(clientHttp)
    assert responseHttp.status_code == 409, responseHttp.text
    assert "not current" in responseHttp.json()["detail"]
    assert listSaves == []
    assert "listDismissedDependencyFindings" not in dictWorkflow[
        "listSteps"][2]


def testOnlyTheRescanIsAgentSafe():
    dictByName = {dictEntry["sName"]: dictEntry
                  for dictEntry in LIST_AGENT_ACTIONS}
    assert dictByName["rescan-dependencies"]["bAgentSafe"] is True
    assert dictByName["dismiss-dependency-finding"]["bAgentSafe"] is False
