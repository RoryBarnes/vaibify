"""The dashboard's reproduction job: stage, run once, report, no host path.

Staging is REAL: the route clones a real git fixture under an admitted
root through the same ``reproductionSource`` the CLI uses. Only the two
things that need a daemon -- the image acquisition and the shadow
rerun -- are replaced, at the route module's own seam names, with
fakes that return the shapes phase 2 returns. What is asserted is the
job's contract: one run per snapshot, the report written under the
reproducer's home, the staging directory gone afterwards, the agent
lane refused, and nothing in any response naming a path on this host.
"""

import json
import os
import time
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.reproductionSourceFixtures import (
    S_FIXTURE_IMAGE_DIGEST,
    fdictBuildWorkflow,
    fnCommitEverything,
    fnWriteJson,
    fsBuildPublishedProject,
)
from vaibify.gui import reproductionProgress
from vaibify.gui.actionCatalog import S_SESSION_HEADER_NAME
from vaibify.gui.routes import reproductionRoutes
from vaibify.reproducibility import reproductionReport
from vaibify.reproducibility import reproductionSource
from vaibify.reproducibility.imageAcquisition import (
    ImageAcquisitionRefusedError,
)


@pytest.fixture(autouse=True)
def fixtureClearJobs():
    reproductionProgress.DICT_JOBS.clear()
    yield
    reproductionProgress.DICT_JOBS.clear()


@pytest.fixture
def sPublishedRepo(tmp_path, monkeypatch):
    """A reproduction-ready project under the one admitted local root."""
    sRoot = os.path.realpath(str(tmp_path))
    monkeypatch.setattr(
        reproductionSource, "flistAdmittedLocalCloneRoots", lambda: [sRoot],
    )
    sRepoPath = os.path.join(sRoot, "publishedProject")
    fsBuildPublishedProject(sRepoPath)
    return sRepoPath


class _DockerLegPresent:
    """The one question these routes ask the connection: is a daemon there?"""

    def fbDockerLegPresent(self):
        return True


def _fclientBuild(bDockerReachable=True):
    """Build a bare app around the reproduction routes; return its client."""
    app = FastAPI()
    dictCtx = {"docker": _DockerLegPresent() if bDockerReachable else None}
    reproductionRoutes.fnRegisterAll(app, dictCtx)
    return TestClient(app)


def _fdictAcquiredFake():
    return {
        "sImageReference": S_FIXTURE_IMAGE_DIGEST,
        "sObtainedFrom": "registry",
        "sRequiredPlatform": "linux/amd64",
        "sObtainedPlatform": "linux/amd64",
        "sDaemonArchitecture": "amd64",
        "bEmulated": False,
        "listAttempts": [{"sLink": "registry pull", "bSucceeded": True,
                          "sDetail": ""}],
    }


def _fdictOutcomeFake(bPassed=True):
    return {
        "bPassed": bPassed,
        "bRerunAttempted": True,
        "iOutputHashesMatched": 2 if bPassed else 1,
        "iOutputHashesTotal": 2,
        "listDivergedHashes": [] if bPassed else ["MakeNumbers/numbers.txt"],
        "listCarriedPaths": [],
        "dictRerunFailure": {},
        "sManifestDigest": "sha256:" + "c" * 64,
        "sShadowTeardown": "destroyed",
    }


def _fcontextFakeTheDaemonHalf(dictAcquired=None, dictOutcome=None,
                               errorAcquire=None):
    """Replace the acquisition and the rerun at the route's seam names."""
    def fdictAcquire(*args, **kwargs):
        if errorAcquire is not None:
            raise errorAcquire
        fnStatus = args[2] if len(args) > 2 else kwargs.get("fnStatusCallback")
        if fnStatus:
            fnStatus({"sPhase": "attempt", "sLink": "registry pull",
                      "bSucceeded": True, "sDetail": ""})
        return dict(dictAcquired or _fdictAcquiredFake())

    def fdictRerun(*args, **kwargs):
        return dict(dictOutcome or _fdictOutcomeFake())

    return patch.multiple(
        reproductionRoutes,
        fdictRerunAndVerifyFromSnapshot=fdictRerun,
        _fsReadDaemonArchitectureQuietly=lambda: "amd64",
    ), patch.object(
        reproductionRoutes.imageAcquisition, "fdictAcquirePinnedImage",
        fdictAcquire,
    ), patch.object(
        reproductionRoutes.reproductionReport, "fdictRecheckObtainedImage",
        lambda sToken, dictAcquired: {"sVerdict": "not-compared",
                                      "sReason": "", "bVacuous": False},
    )


def _fdictStage(client, sSource, sWorkflowName=""):
    response = client.post(
        "/api/reproductions/stage",
        json={"sSource": sSource, "sWorkflowName": sWorkflowName},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _fdictAwaitSettled(client, sJobId):
    """Poll the job until the hub reports it settled; return the view."""
    for _iAttempt in range(200):
        dictView = client.get(f"/api/reproductions/{sJobId}").json()
        if not dictView["bLive"] and dictView["sPhase"] != "staged":
            return dictView
        time.sleep(0.02)
    raise AssertionError("the job never settled")


def _flistStagingTokens():
    sRoot = reproductionSource._fsStagingRoot()
    return sorted(os.listdir(sRoot)) if os.path.isdir(sRoot) else []


# ---------------------------------------------------------------------
# Stage
# ---------------------------------------------------------------------


def test_staging_describes_the_snapshot_and_holds_it(sPublishedRepo):
    with patch.object(
        reproductionRoutes, "_fsReadDaemonArchitectureQuietly",
        lambda: "amd64",
    ):
        dictStaged = _fdictStage(_fclientBuild(), sPublishedRepo)
    assert dictStaged["dictStaged"]["sRepositoryName"] == "publishedProject"
    assert dictStaged["dictStaged"]["sPinnedImageReference"] == (
        S_FIXTURE_IMAGE_DIGEST
    )
    assert dictStaged["dictDaemon"] == {
        "bReachable": True, "sArchitecture": "amd64",
        "sRequiredPlatform": "linux/amd64", "bArchitectureMatches": True,
    }
    assert dictStaged["listChainLinks"][0] == "registry pull"
    assert len(_flistStagingTokens()) == 1, "the snapshot is held, not discarded"
    dictView = reproductionProgress.fdictReadJobView(dictStaged["sJobId"])
    assert dictView["sPhase"] == "staged"


def test_a_refused_source_is_a_422_naming_the_rule(sPublishedRepo):
    dictWorkflow = fdictBuildWorkflow()
    del dictWorkflow["sPlotDirectory"]
    fnWriteJson(sPublishedRepo, ".vaibify/projects/project.json", dictWorkflow)
    fnCommitEverything(sPublishedRepo, "invalid")
    response = _fclientBuild().post(
        "/api/reproductions/stage", json={"sSource": sPublishedRepo},
    )
    assert response.status_code == 422, response.text
    assert "rule 1" in response.json()["detail"]
    assert _flistStagingTokens() == []


def test_several_workflows_offer_a_choice_instead_of_a_sentence(
    sPublishedRepo,
):
    fnWriteJson(
        sPublishedRepo, ".vaibify/projects/second.json",
        fdictBuildWorkflow("Second"),
    )
    fnCommitEverything(sPublishedRepo, "second workflow")
    client = _fclientBuild()
    dictResponse = _fdictStage(client, sPublishedRepo)
    assert dictResponse["bWorkflowSelectionRequired"] is True
    assert sorted(dictResponse["listWorkflowNames"]) == ["Demo", "Second"]
    assert _flistStagingTokens() == []
    with patch.object(
        reproductionRoutes, "_fsReadDaemonArchitectureQuietly", lambda: "amd64",
    ):
        dictStaged = _fdictStage(client, sPublishedRepo, "Second")
    assert dictStaged["dictStaged"]["sWorkflowName"] == "Second"


@pytest.mark.falsification
def test_the_agent_lane_is_refused_on_both_mutating_routes(sPublishedRepo):
    """Kills: dropping ``fnRejectAgentTokenLane`` from the stage handler."""
    client = _fclientBuild()
    dictHeaders = {S_SESSION_HEADER_NAME: "agent-token"}
    response = client.post(
        "/api/reproductions/stage", json={"sSource": sPublishedRepo},
        headers=dictHeaders,
    )
    assert response.status_code == 403, response.text
    assert _flistStagingTokens() == []
    response = client.post(
        "/api/reproductions/anyjob/run", json={}, headers=dictHeaders,
    )
    assert response.status_code == 403, response.text


# ---------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------


@pytest.mark.falsification
def test_a_snapshot_runs_once_and_a_second_run_is_refused_by_name(
    sPublishedRepo,
):
    """Kills: claiming the job without consulting ``fbClaimJobForRun``."""
    tPatches = _fcontextFakeTheDaemonHalf()
    with tPatches[0], tPatches[1], tPatches[2], _fclientBuild() as client:
        sJobId = _fdictStage(client, sPublishedRepo)["sJobId"]
        response = client.post(f"/api/reproductions/{sJobId}/run", json={})
        assert response.status_code == 200, response.text
        dictView = _fdictAwaitSettled(client, sJobId)
        responseAgain = client.post(
            f"/api/reproductions/{sJobId}/run", json={},
        )
    assert responseAgain.status_code == 409, responseAgain.text
    assert "already been run" in responseAgain.json()["detail"]
    assert dictView["sPhase"] == "settled"
    assert dictView["dictReport"]["sVerdict"] == "reproduced"


def test_a_settled_run_writes_the_report_and_discards_the_staging(
    sPublishedRepo,
):
    tPatches = _fcontextFakeTheDaemonHalf()
    with tPatches[0], tPatches[1], tPatches[2], _fclientBuild() as client:
        sJobId = _fdictStage(client, sPublishedRepo)["sJobId"]
        client.post(f"/api/reproductions/{sJobId}/run", json={})
        dictView = _fdictAwaitSettled(client, sJobId)
    dictReport = reproductionReport.fdictReadReproductionReport(
        dictView["sReportId"],
    )
    assert dictReport["sVerdict"] == "reproduced"
    assert dictReport["dictSource"]["sRepositoryName"] == "publishedProject"
    assert dictView["dictReport"]["sVerdictRendered"] == "reproduced"
    assert dictView["dictAcquired"]["sObtainedFrom"] == "registry"
    assert _flistStagingTokens() == [], "staging is scratch and is gone"


def test_a_diverged_run_reports_the_files(sPublishedRepo):
    tPatches = _fcontextFakeTheDaemonHalf(
        dictOutcome=_fdictOutcomeFake(bPassed=False),
    )
    with tPatches[0], tPatches[1], tPatches[2], _fclientBuild() as client:
        sJobId = _fdictStage(client, sPublishedRepo)["sJobId"]
        client.post(f"/api/reproductions/{sJobId}/run", json={})
        dictView = _fdictAwaitSettled(client, sJobId)
    assert dictView["dictReport"]["sVerdict"] == "diverged"
    assert dictView["dictReport"]["listDivergedHashes"] == [
        "MakeNumbers/numbers.txt",
    ]


def test_an_acquisition_refusal_fails_the_job_with_its_reason(sPublishedRepo):
    tPatches = _fcontextFakeTheDaemonHalf(
        errorAcquire=ImageAcquisitionRefusedError("no link served the image"),
    )
    with tPatches[0], tPatches[1], tPatches[2], _fclientBuild() as client:
        sJobId = _fdictStage(client, sPublishedRepo)["sJobId"]
        client.post(f"/api/reproductions/{sJobId}/run", json={})
        dictView = _fdictAwaitSettled(client, sJobId)
    assert dictView["sPhase"] == "failed"
    assert "no link served the image" in dictView["sFailure"]
    assert dictView["dictReport"] is None
    assert _flistStagingTokens() == []


def test_a_run_without_a_daemon_is_refused_and_keeps_the_snapshot(
    sPublishedRepo,
):
    client = _fclientBuild(bDockerReachable=False)
    dictStaged = _fdictStage(client, sPublishedRepo)
    assert dictStaged["dictDaemon"]["bReachable"] is False
    assert dictStaged["dictDaemon"]["bArchitectureMatches"] is False
    response = client.post(
        f"/api/reproductions/{dictStaged['sJobId']}/run", json={},
    )
    assert response.status_code == 409
    assert "Docker" in response.json()["detail"]
    assert len(_flistStagingTokens()) == 1
    assert not reproductionProgress.fdictReadJobView(
        dictStaged["sJobId"],
    )["bConsumed"]


def test_an_unknown_job_is_a_404_on_both_routes():
    client = _fclientBuild()
    assert client.get("/api/reproductions/nope").status_code == 404
    assert client.post("/api/reproductions/nope/run", json={}).status_code == 404


# ---------------------------------------------------------------------
# No host path in any response
# ---------------------------------------------------------------------


@pytest.mark.falsification
def test_no_response_names_a_path_on_this_host(sPublishedRepo, tmp_path):
    """Kills: adding the staging token to the job's public fields."""
    listBodies = []
    tPatches = _fcontextFakeTheDaemonHalf()
    with tPatches[0], tPatches[1], tPatches[2], _fclientBuild() as client:
        dictStaged = _fdictStage(client, sPublishedRepo)
        listBodies.append(json.dumps(dictStaged))
        sJobId = dictStaged["sJobId"]
        listBodies.append(client.post(
            f"/api/reproductions/{sJobId}/run", json={},
        ).text)
        dictView = _fdictAwaitSettled(client, sJobId)
        listBodies.append(json.dumps(dictView))
    sStagingRoot = reproductionSource._fsStagingRoot()
    sToken = list(reproductionProgress.DICT_JOBS.values())[0]["sToken"]
    for sBody in listBodies:
        assert sPublishedRepo not in sBody
        assert str(tmp_path) not in sBody
        assert sStagingRoot not in sBody
        assert sToken not in sBody
        assert os.path.expanduser("~") not in sBody


# ---------------------------------------------------------------------
# What a dismissed card costs, and what a report is served as
# (review, 2026-09-07)
# ---------------------------------------------------------------------


@pytest.mark.falsification
def test_dismissing_a_staged_job_deletes_its_clone(sPublishedRepo):
    """A staged job holds the lock the staging sweep skips.

    Kills: dropping the discard route's registration or its delete.
    """
    with patch.object(
        reproductionRoutes, "_fsReadDaemonArchitectureQuietly",
        lambda: "amd64",
    ), _fclientBuild() as client:
        sJobId = _fdictStage(client, sPublishedRepo)["sJobId"]
        assert len(_flistStagingTokens()) == 1
        responseDiscard = client.post(
            f"/api/reproductions/{sJobId}/discard", json={},
        )
    assert responseDiscard.status_code == 200, responseDiscard.text
    assert responseDiscard.json() == {"bDiscarded": True}
    assert _flistStagingTokens() == [], "the clone is gone, not just released"
    assert reproductionProgress.fdictReadJobView(sJobId) is None


def test_a_running_job_is_not_discarded_out_from_under_its_shadow(
    sPublishedRepo,
):
    with patch.object(
        reproductionRoutes, "_fsReadDaemonArchitectureQuietly",
        lambda: "amd64",
    ), _fclientBuild() as client:
        sJobId = _fdictStage(client, sPublishedRepo)["sJobId"]
        reproductionProgress.fnRecordPhase(
            sJobId, reproductionProgress.S_PHASE_RUNNING,
        )
        responseDiscard = client.post(
            f"/api/reproductions/{sJobId}/discard", json={},
        )
    assert responseDiscard.status_code == 409
    assert "running" in responseDiscard.text
    reproductionProgress.fnForgetJob(sJobId)


def test_the_report_route_serves_one_report_and_refuses_a_spelled_path():
    dictReport = {
        "sReportId": "reportfixture01", "iSchemaVersion": 1,
        "sVerdict": "reproduced",
    }
    with patch.object(
        reproductionReport, "fdictReadReproductionReport",
        side_effect=lambda sReportId: (
            dictReport if sReportId == "reportfixture01"
            else _fnRaiseLookup(sReportId)
        ),
    ), _fclientBuild() as client:
        responseGood = client.get(
            "/api/reproductions/reports/reportfixture01",
        )
        responseMissing = client.get("/api/reproductions/reports/nope")
    assert responseGood.status_code == 200
    assert responseGood.json()["sVerdict"] == "reproduced"
    assert responseMissing.status_code == 404


def _fnRaiseLookup(sReportId):
    raise LookupError(sReportId)


def test_the_agent_lane_is_refused_on_the_report_and_discard_routes(
    sPublishedRepo,
):
    with _fclientBuild() as client:
        for sMethod, sPath in (
            ("post", "/api/reproductions/anyjob/discard"),
            ("get", "/api/reproductions/reports/anyreport"),
        ):
            response = getattr(client, sMethod)(
                sPath,
                headers={S_SESSION_HEADER_NAME: "agent-token-not-a-browser"},
                **({"json": {}} if sMethod == "post" else {}),
            )
            assert response.status_code == 403, (sPath, response.text)
