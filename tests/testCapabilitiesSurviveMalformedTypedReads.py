"""The council capabilities poll survives a malformed typed-read answer.

The pre-flight behind the council button reads the repository through
typed reads whose JSON the CONTAINER produces. A repository that makes
the container answer with the wrong shape (a list where a mapping
belongs, a string where a size belongs) raised out of the capabilities
route as a 500, and the dashboard polls that route, so one hostile
repository took the council button away. A probe that cannot be read is
not a refusal: the route answers 200, the offer is withdrawn with a
reason, and the capability stays as it was.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tests.testCouncilRoutes import (
    MockDockerCouncil,
    S_CONTAINER_ID,
    S_CONTAINER_NAME,
    S_PROJECT_REPO,
    _tEstablishOwnership,
)
from vaibify.gui import pipelineServer

DICT_TOO_LARGE_WEIGHT = {
    "iFileCount": 22342, "iTotalBytes": 30 * 1024 * 1024 * 1024,
    "bTruncated": False, "bLargestFilesTruncated": False,
    "listLargestFiles": [], "listEscapingSymlinks": [],
    "listSpecialFiles": [], "listSubmodules": [],
}

LIST_MALFORMED_TRACKED_ANSWERS = [
    ["not", "a", "mapping"],
    {"bSuccess": True, "dictEntries": ["a", "list"]},
    {"bSuccess": True, "dictEntries": None},
    {"bSuccess": True, "dictEntries": {"a.py": "a string, not a mapping"}},
    {"bSuccess": True, "dictEntries": {"a.py": {
        "sMode": "100644", "listStages": 7, "sType": "file"}}},
    {"bSuccess": True, "dictEntries": {"a.py": {
        "sMode": "100644", "listStages": [0], "sType": "file",
        "sIdentity": "ab" * 20, "iSizeBytes": "not a number"}}},
    {"bSuccess": True, "dictEntries": {"a.py": {
        "sMode": "100644", "listStages": [0], "sType": "file",
        "sIdentity": "ab" * 20, "iSizeBytes": None}}},
]

LIST_MALFORMED_WEIGHT_ANSWERS = [
    ["not", "a", "mapping"],
    {"iFileCount": "many", "iTotalBytes": 1, "bTruncated": False},
    {"iFileCount": 5, "iTotalBytes": 1, "bTruncated": False,
     "listLargestFiles": [{"sPath": "a", "iSizeBytes": "huge"}]},
    {"iFileCount": 5, "iTotalBytes": 1, "bTruncated": False,
     "listLargestFiles": "not a list"},
    {"iFileCount": 5, "iTotalBytes": 1, "bTruncated": False,
     "listLargestFiles": ["not a mapping"]},
]


def _fdictCapabilities(tmp_path, fnConfigure):
    docker = MockDockerCouncil()
    fnConfigure(docker)
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker", lambda: docker,
    ):
        app = pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace", sTerminalUserArg="testuser")
    app.state.dictRouteContext["workflows"][S_CONTAINER_ID] = {
        "sProjectRepoPath": S_PROJECT_REPO}
    sCredential, sLease = _tEstablishOwnership(
        app, S_CONTAINER_NAME, S_CONTAINER_ID)
    with TestClient(app, headers={
        "X-Session-Token": sCredential, "X-Vaibify-Lease": sLease,
    }, raise_server_exceptions=False) as client:
        return client.get(
            f"/api/agent-councils/{S_CONTAINER_ID}/capabilities")


@pytest.mark.falsification
def testAMalformedTrackedScopeAnswerWithdrawsTheOfferInsteadOfFailing(
        tmp_path):
    """Kills: letting a malformed tracked-set answer escape the route."""
    for dictAnswer in LIST_MALFORMED_TRACKED_ANSWERS:
        def fnConfigure(docker, dictAnswer=dictAnswer):
            docker.dictRepositoryWeight = dict(DICT_TOO_LARGE_WEIGHT)
            docker.fdictFetchTrackedIdentities = (
                lambda sContainerId, sPath: dictAnswer)

        response = _fdictCapabilities(tmp_path, fnConfigure)
        assert response.status_code == 200, (dictAnswer, response.text)
        dictCapabilities = response.json()
        assert dictCapabilities["dictTrackedScopeOffer"]["bOffered"] is False
        assert dictCapabilities["bAvailable"] is False, (
            "the oversized repository still stands refused")
        assert dictCapabilities["sUnavailableIn"] == "snapshot-too-large"


@pytest.mark.falsification
def testAMalformedWeightAnswerLeavesTheCapabilityAsItWas(tmp_path):
    """Kills: narrowing the feasibility probe's caught errors again."""
    for dictAnswer in LIST_MALFORMED_WEIGHT_ANSWERS:
        def fnConfigure(docker, dictAnswer=dictAnswer):
            docker.fdictWeighRepository = (
                lambda sContainerId, sPath: dictAnswer)

        response = _fdictCapabilities(tmp_path, fnConfigure)
        assert response.status_code == 200, (dictAnswer, response.text)
        assert "dictSnapshotFeasibility" not in response.json()


def testWellFormedAnswersStillProduceTheOffer(tmp_path):
    def fnConfigure(docker):
        docker.dictRepositoryWeight = dict(DICT_TOO_LARGE_WEIGHT)

    response = _fdictCapabilities(tmp_path, fnConfigure)
    assert response.status_code == 200
    assert response.json()["dictTrackedScopeOffer"]["bOffered"] is True
