"""Every lane that verifies a remote says when the check could not be done.

Three lanes verify: the dashboard's open-time refresh, the scheduled
pass, and Verify now (which ``vaibify do verify-remote`` drives). Only
the refresh used to record a check that failed; the other two left the
badge as they found it, so a remote that stopped answering -- a rate
limit, a network that dropped, a repository GitHub would no longer
describe -- kept its last reading with no word of why. Now each lane
settles the poll's per-project check state: SETTLED when the record
was rewritten, UNCHECKABLE with the reason when it was not. An
uncheckable result never writes the record.

Each lane is keyed the way the poll reads it -- the container ID, with
the project's repository -- and the fixtures make the container's NAME
differ from its ID, because a map keyed by one and read by the other
has shipped under a green suite here before.
"""

import asyncio
import hashlib
import json
import os
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.carrierStandDown import S_STAND_IN_CONTAINER_NAME, fnStandCarrierDown
from vaibify.cli import hubSession
from vaibify.gui.routes import remoteRefreshRoutes, syncRoutes
from vaibify.reproducibility import githubMirror, remoteCheckState, scheduledReverify


BA_DATA = b"a,b\n1,2\n"
S_DATA_SHA = hashlib.sha256(BA_DATA).hexdigest()
S_PROBE_REFUSAL = "GitHub asked vaibify to wait before checking (test)"


@pytest.fixture(autouse=True)
def fixtureForgetEveryCheck():
    with remoteCheckState._LOCK_CHECKS:
        remoteCheckState._DICT_CHECKS_BY_RESOURCE.clear()
    yield
    with remoteCheckState._LOCK_CHECKS:
        remoteCheckState._DICT_CHECKS_BY_RESOURCE.clear()


def _fsMakeRepo(tmp_path, sName):
    sRepo = str(tmp_path / sName)
    os.makedirs(os.path.join(sRepo, "step01"))
    with open(os.path.join(sRepo, "step01", "data.csv"), "wb") as fileOut:
        fileOut.write(BA_DATA)
    return sRepo


def _fdictWorkflow(sRepo, sOwner):
    return {
        "sWorkflowId": "wf-" + sOwner, "sProjectRepoPath": sRepo,
        "dictRemotes": {"github": {
            "sOwner": sOwner, "sRepo": "something", "sBranch": "main"}},
        "listSteps": [{
            "sDirectory": "step01", "saOutputDataFiles": ["data.csv"],
            "saPlotFiles": [],
        }],
    }


def _fnAnswerTheProbe(monkeypatch, setUncheckableOwners):
    """GitHub serves every file; the reader probe fails for these owners."""
    monkeypatch.setattr(
        githubMirror, "fdictFetchRemoteHashes",
        lambda sOwner, sRepo, sBranch, listPaths: {
            "step01/data.csv": S_DATA_SHA},
    )

    def fdictProbe(sOwner, sRepo):
        if sOwner in setUncheckableOwners:
            raise githubMirror.GithubMirrorError(S_PROBE_REFUSAL)
        return {"sState": "public", "sReason": "(test) public"}

    monkeypatch.setattr(githubMirror, "fdictProbePublicAccess", fdictProbe)


def _fdictCheckOf(sContainerId, sRepo):
    return remoteCheckState.fdictDescribeChecks(
        remoteCheckState.ftProjectCheckKey(sContainerId, sRepo),
    ).get("github")


def _fsCachePath(sRepo):
    return os.path.join(sRepo, ".vaibify", "syncStatus.json")


# ── the scheduled pass ───────────────────────────────────────────────


@pytest.mark.falsification
def test_the_scheduled_pass_settles_each_project_under_its_own_key(
    tmp_path, monkeypatch,
):
    """Two projects in one hub: one check fails, one answers.

    Each project's state lands under the key the poll reads -- its
    container's ID with its repository -- and neither is written under
    the other's, nor under a container name.

    Kills: fdictRunReverifyOnce no longer recording check outcomes.
    """
    sRepoAlpha = _fsMakeRepo(tmp_path, "alpha")
    sRepoBeta = _fsMakeRepo(tmp_path, "beta")
    _fnAnswerTheProbe(monkeypatch, {"alpha"})
    dictCtx = {"workflows": {
        "c0ffee-alpha-id": _fdictWorkflow(sRepoAlpha, "alpha"),
        "c0ffee-beta-id": _fdictWorkflow(sRepoBeta, "beta"),
    }, "files": lambda sContainerId: None}
    dictReport = scheduledReverify.fdictRunReverifyOnce(
        dictCtx, scheduledReverify._flistEnumerateWorkflows(dictCtx),
    )
    assert [d["sStatus"] for d in dictReport["listResults"]] == ["error", "ok"]
    dictAlpha = _fdictCheckOf("c0ffee-alpha-id", sRepoAlpha)
    assert dictAlpha["sState"] == remoteCheckState.S_STATE_UNCHECKABLE
    assert S_PROBE_REFUSAL in dictAlpha["sReason"]
    assert _fdictCheckOf("c0ffee-beta-id", sRepoBeta)["sState"] == (
        remoteCheckState.S_STATE_SETTLED)
    assert _fdictCheckOf("c0ffee-alpha-id", sRepoBeta) is None
    assert not os.path.exists(_fsCachePath(sRepoAlpha)), (
        "an uncheckable scheduled check wrote the project's record")
    assert os.path.exists(_fsCachePath(sRepoBeta))


def test_a_bare_workflow_records_nothing(tmp_path, monkeypatch):
    """A library caller has no container to key a badge by."""
    sRepo = _fsMakeRepo(tmp_path, "alpha")
    _fnAnswerTheProbe(monkeypatch, {"alpha"})
    scheduledReverify.fdictRunReverifyOnce({}, [_fdictWorkflow(sRepo, "alpha")])
    assert remoteCheckState._DICT_CHECKS_BY_RESOURCE == {}


# ── Verify now, and the CLI that drives it ───────────────────────────


S_CONTAINER_ID = "verify-now-container-id"


@pytest.fixture
def fixtureVerifyNow(tmp_path, monkeypatch):
    """The Verify-now route on a bare app; the stand-in name is not the ID."""
    assert S_STAND_IN_CONTAINER_NAME != S_CONTAINER_ID
    fnStandCarrierDown(monkeypatch, syncRoutes)
    monkeypatch.setattr(syncRoutes, "_fnRequireNetworkAccess", lambda sId: None)
    sRepo = _fsMakeRepo(tmp_path, "project")
    app = FastAPI()
    dictCtx = {"docker": None, "require": lambda *listArgs: None,
               "workflows": {S_CONTAINER_ID: _fdictWorkflow(sRepo, "someone")}}
    syncRoutes._fnRegisterRemoteVerify(app, dictCtx)
    return TestClient(app), sRepo


@pytest.mark.falsification
def test_verify_now_records_a_failed_check_under_the_container_id(
    fixtureVerifyNow, monkeypatch,
):
    """The badge says why, in the response's own words, and nothing is written.

    Kills: _fnMarkAndRaiseVerifyFailure no longer marking the check
    uncheckable.
    """
    clientTest, sRepo = fixtureVerifyNow
    _fnAnswerTheProbe(monkeypatch, {"someone"})
    responseHttp = clientTest.post(f"/api/sync/{S_CONTAINER_ID}/github/verify")
    assert responseHttp.status_code == 502
    sDetail = responseHttp.json()["detail"]
    assert S_PROBE_REFUSAL in sDetail
    dictCheck = _fdictCheckOf(S_CONTAINER_ID, sRepo)
    assert dictCheck == {
        "sState": remoteCheckState.S_STATE_UNCHECKABLE, "sReason": sDetail}
    assert _fdictCheckOf(S_STAND_IN_CONTAINER_NAME, sRepo) is None
    assert not os.path.exists(_fsCachePath(sRepo))


def test_verify_now_settles_a_check_that_answered(fixtureVerifyNow, monkeypatch):
    clientTest, sRepo = fixtureVerifyNow
    _fnAnswerTheProbe(monkeypatch, set())
    assert clientTest.post(
        f"/api/sync/{S_CONTAINER_ID}/github/verify").status_code == 200
    assert _fdictCheckOf(S_CONTAINER_ID, sRepo)["sState"] == (
        remoteCheckState.S_STATE_SETTLED)
    with open(_fsCachePath(sRepo)) as fileIn:
        assert json.load(fileIn)["github"]["dictPublicAccess"]["sState"] == (
            "public")


def test_the_cli_prints_the_sentence_and_writes_nothing(
    fixtureVerifyNow, monkeypatch, capsys,
):
    """``vaibify do verify-remote`` prints the hub's answer as it came."""
    clientTest, sRepo = fixtureVerifyNow
    _fnAnswerTheProbe(monkeypatch, {"someone"})
    responseHttp = clientTest.post(f"/api/sync/{S_CONTAINER_ID}/github/verify")
    with patch.object(
        hubSession, "ftSendSessionRequest",
        return_value=(responseHttp.status_code, responseHttp.json()),
    ):
        iExit = hubSession.fiSendHttpAction(
            {}, "POST", f"/api/sync/{S_CONTAINER_ID}/github/verify", {},
            False, 30.0,
        )
    assert iExit == 2
    assert S_PROBE_REFUSAL in capsys.readouterr().out
    assert not os.path.exists(_fsCachePath(sRepo))


# ── the open-time refresh, unchanged ─────────────────────────────────


def test_the_refresh_reads_an_uncheckable_probe_as_uncheckable(
    tmp_path, monkeypatch,
):
    """The lane that already did this still does, for the new question too."""
    sRepo = _fsMakeRepo(tmp_path, "project")
    _fnAnswerTheProbe(monkeypatch, {"someone"})
    tCheckKey = remoteCheckState.ftProjectCheckKey(S_CONTAINER_ID, sRepo)
    asyncio.run(remoteRefreshRoutes._fnCheckOneRemote(
        {"tCheckKey": tCheckKey}, _fdictWorkflow(sRepo, "someone"), sRepo,
        "github",
    ))
    dictCheck = _fdictCheckOf(S_CONTAINER_ID, sRepo)
    assert dictCheck["sState"] == remoteCheckState.S_STATE_UNCHECKABLE
    assert S_PROBE_REFUSAL in dictCheck["sReason"]
    assert not os.path.exists(_fsCachePath(sRepo))
