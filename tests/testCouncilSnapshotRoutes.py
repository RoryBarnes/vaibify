"""The snapshot-scope routes carry the same four layers as consent.

The omission page and the scope choice are driven over real HTTP with
the container name distinct from its id. The page must never serve a
stale or foreign observation; the choice must be refused on every lane
but the lease-holding browser session, and must write nothing when it
is refused.
"""

import os

import pytest

from vaibify.gui import (
    actionCatalog,
    agentCouncilSnapshotScope,
    councilRouteGuards,
)
from tests.testCouncilCredentialRoutes import (  # noqa: F401 — fixtures
    AppHarness,
    fixtureJobsRunNothing,
    harness,
    sEvidencePath,
)
from tests.testCouncilRoutes import (
    S_CONTAINER_ID,
    S_CONTAINER_NAME,
    S_PROJECT_REPO,
)

S_PREFIX = f"/api/council-snapshots/{S_CONTAINER_ID}"
S_OBSERVATION_ID = "0f" * 16


@pytest.fixture(autouse=True)
def sScopeDirectory(tmp_path, monkeypatch, harness):
    sDirectory = str(tmp_path / "scopeStore")
    monkeypatch.setattr(agentCouncilSnapshotScope,
                        "fsResolveSnapshotScopeDirectory", lambda: sDirectory)
    harness.app.state.dictRouteContext["workflows"][S_CONTAINER_ID] = {
        "sProjectRepoPath": S_PROJECT_REPO}
    return sDirectory


def _fsRecordInventory(sContainerId=S_CONTAINER_ID):
    """Record one real inventory through the module's own writer."""
    return agentCouncilSnapshotScope._fsRecordObservation(
        sContainerId, S_PROJECT_REPO,
        [["output/a.bin", "ignored", 10], ["output/b.bin", "ignored", 20],
         ["notes.txt", "untracked", 3]],
        {"bComplete": True})


LIST_ROUTES = [
    ("GET", f"{S_PREFIX}/omissions/{S_OBSERVATION_ID}"
            "?sDirectory=output&sReason=ignored", None),
    ("POST", f"{S_PREFIX}/scope", {"sScope": "gitTracked"}),
]


def test_the_owner_pages_a_current_observation(harness):
    sObservationId = _fsRecordInventory()
    response = harness.fclientOwner().get(
        f"{S_PREFIX}/omissions/{sObservationId}"
        "?sDirectory=output&sReason=ignored&iLimit=1")
    assert response.status_code == 200, response.text
    dictPage = response.json()
    assert dictPage["iTotal"] == 2
    assert [dictPath["sPath"] for dictPath in dictPage["listPaths"]] == [
        "output/a.bin"]


def test_a_superseded_observation_answers_410_with_the_remedy(harness):
    sFirst = _fsRecordInventory()
    _fsRecordInventory()
    response = harness.fclientOwner().get(
        f"{S_PREFIX}/omissions/{sFirst}?sDirectory=output&sReason=ignored")
    assert response.status_code == 410
    assert "measure it again" in response.json()["detail"]


def test_another_containers_observation_reads_as_expired(harness):
    sForeign = _fsRecordInventory("another-container-id")
    response = harness.fclientOwner().get(
        f"{S_PREFIX}/omissions/{sForeign}?sDirectory=output&sReason=ignored")
    assert response.status_code == 410


def test_the_owner_chooses_a_scope_and_it_is_remembered(harness):
    response = harness.fclientOwner().post(f"{S_PREFIX}/scope",
                                           json={"sScope": "gitTracked"})
    assert response.status_code == 200, response.text
    assert agentCouncilSnapshotScope.fdictReadRememberedScope(
        S_CONTAINER_NAME, S_PROJECT_REPO)["sScope"] == "gitTracked"


def test_an_unknown_scope_is_refused(harness):
    response = harness.fclientOwner().post(f"{S_PREFIX}/scope",
                                           json={"sScope": "everything"})
    assert response.status_code == 400


def _fnAssertNothingRemembered(sScopeDirectory):
    assert not os.path.exists(os.path.join(
        sScopeDirectory, "rememberedScopes.json"))


@pytest.mark.parametrize("sMethod,sPath,jsonBody", LIST_ROUTES)
@pytest.mark.parametrize("sClient", [
    "fclientAnonymous", "fclientWithoutLease",
    "fclientOtherSessionWithStolenLease", "fclientAgent"])
def test_every_other_lane_is_refused(harness, sScopeDirectory, sMethod,
                                     sPath, jsonBody, sClient):
    clientHttp = getattr(harness, sClient)()
    response = clientHttp.request(sMethod, sPath, json=jsonBody)
    assert response.status_code in (401, 403), (sClient, response.text)
    _fnAssertNothingRemembered(sScopeDirectory)


@pytest.mark.falsification
def test_the_handler_alone_refuses_an_agent_page_read(harness):
    """The middleware admits agent GETs; only the handler says no.

    Kills: the omission page skipping the shared council guard, which
    would let an in-container agent read the researcher's file names.
    """
    sObservationId = _fsRecordInventory()
    response = harness.fclientAgent().get(
        f"{S_PREFIX}/omissions/{sObservationId}"
        "?sDirectory=output&sReason=ignored")
    assert response.status_code == 403, response.text


def test_the_scope_choice_is_refused_by_the_middleware_alone(
        harness, sScopeDirectory, monkeypatch):
    monkeypatch.setattr(councilRouteGuards, "fnRejectAgentTokenLane",
                        lambda requestHttp: None)
    assert ("POST", "/api/council-snapshots/{sContainerId}/scope") in (
        actionCatalog.SET_INTENTIONALLY_EXCLUDED_PATHS)
    response = harness.fclientAgent().post(f"{S_PREFIX}/scope",
                                           json={"sScope": "gitTracked"})
    assert response.status_code == 403
    _fnAssertNothingRemembered(sScopeDirectory)
