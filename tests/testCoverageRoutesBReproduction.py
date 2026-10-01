"""Coverage of the reproduction job's failure branches in ``reproductionRoutes``.

The job's contract is that no failure leaves something behind: a stage
over the concurrency cap is refused before it opens a record, a stage
or an adoption that breaks forgets the job and removes the clone, a
worker that crashes fails the job by exception TYPE (never echoing a
message that may name a host path), and a rerun refused before any step
ran settles as a report with no verdict. Staging is REAL (a git fixture
cloned under an admitted root); only the daemon half -- acquisition and
the shadow rerun -- is replaced at the route module's own seam names.
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
    fsBuildPublishedProject,
)
from vaibify.docker import daemonDescription
from vaibify.gui import reproductionProgress
from vaibify.gui.routes import reproductionRoutes
from vaibify.reproducibility import reproductionReport, reproductionSource
from vaibify.reproducibility.shadowRerun import ShadowRerunRefusedError


S_AGENT_HEADER = {"X-Vaibify-Session": "agentTokenValue"}


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


class DockerLegPresent:
    """The one question these routes ask the connection."""

    def fbDockerLegPresent(self):
        return True


def fclientBuild(bRaiseServerExceptions=True):
    app = FastAPI()
    reproductionRoutes.fnRegisterAll(app, {"docker": DockerLegPresent()})
    return TestClient(app, raise_server_exceptions=bRaiseServerExceptions)


def fdictAcquiredFake():
    return {
        "sImageReference": S_FIXTURE_IMAGE_DIGEST,
        "sObtainedFrom": "registry",
        "sRequiredPlatform": "linux/amd64",
        "sObtainedPlatform": "linux/amd64",
        "sDaemonArchitecture": "amd64",
        "bEmulated": False,
        "listAttempts": [],
    }


def flistStagingTokens():
    sRoot = reproductionSource._fsStagingRoot()
    return sorted(os.listdir(sRoot)) if os.path.isdir(sRoot) else []


def fdictAwaitSettled(clientHub, sJobId):
    for _iAttempt in range(250):
        dictView = clientHub.get(f"/api/reproductions/{sJobId}").json()
        if not dictView["bLive"] and dictView["sPhase"] != "staged":
            return dictView
        time.sleep(0.02)
    raise AssertionError("the job never settled")


def flistDaemonHalfPatches(fnRerun):
    """Replace acquisition, the rerun and the archive re-check."""
    return [
        patch.object(reproductionRoutes, "fdictRerunAndVerifyFromSnapshot",
                     fnRerun),
        patch.object(
            reproductionRoutes.imageAcquisition, "fdictAcquirePinnedImage",
            lambda *aArgs, **dictArgs: fdictAcquiredFake(),
        ),
        patch.object(
            reproductionRoutes.reproductionReport,
            "fdictRecheckObtainedImage",
            lambda sToken, dictAcquired: {
                "sVerdict": "not-compared", "sReason": "", "bVacuous": False,
            },
        ),
        patch.object(
            daemonDescription, "_fsReadDaemonArchitectureQuietly",
            lambda: "amd64",
        ),
    ]


def fdictStageAndRun(sPublishedRepo, fnRerun):
    """Stage the fixture, run it with ``fnRerun``, return the settled view."""
    listPatches = flistDaemonHalfPatches(fnRerun)
    for patchActive in listPatches:
        patchActive.start()
    try:
        with fclientBuild() as clientHub:
            responseStage = clientHub.post(
                "/api/reproductions/stage", json={"sSource": sPublishedRepo},
            )
            assert responseStage.status_code == 200, responseStage.text
            sJobId = responseStage.json()["sJobId"]
            responseRun = clientHub.post(
                f"/api/reproductions/{sJobId}/run", json={},
            )
            assert responseRun.status_code == 200, responseRun.text
            return fdictAwaitSettled(clientHub, sJobId)
    finally:
        for patchActive in listPatches:
            patchActive.stop()


def testStagingOverTheCapIsA429AndOpensNoRecord():
    for _iJob in range(reproductionProgress.I_MAX_CONCURRENT_JOBS):
        reproductionProgress.fdictOpenStagingJob()
    responseHttp = fclientBuild().post(
        "/api/reproductions/stage", json={"sSource": "unusedSource"},
    )
    assert responseHttp.status_code == 429
    assert "Run or dismiss one" in responseHttp.json()["detail"]
    assert len(reproductionProgress.DICT_JOBS) == (
        reproductionProgress.I_MAX_CONCURRENT_JOBS
    )


def testAStageThatCrashesForgetsItsJob(monkeypatch):
    """A crash mid-clone must not hold one of the capped slots forever."""

    def fnCrashingStage(sSource, sWorkflowName, fnPhase):
        fnPhase("staging")
        raise OSError("disk vanished under the clone")

    monkeypatch.setattr(reproductionRoutes, "fdictStageSource", fnCrashingStage)
    responseHttp = fclientBuild(bRaiseServerExceptions=False).post(
        "/api/reproductions/stage", json={"sSource": "sourceAlpha"},
    )
    assert responseHttp.status_code == 500
    assert "disk vanished" not in responseHttp.text
    assert reproductionProgress.DICT_JOBS == {}


def testAnAdoptionThatFailsDiscardsTheCloneAndTheJob(
    sPublishedRepo, monkeypatch,
):
    def fnFailingDescribe(connectionDocker, sRequiredPlatform):
        raise RuntimeError("daemon answered garbage")

    monkeypatch.setattr(
        daemonDescription, "fdictDescribeDaemonForPlatform",
        fnFailingDescribe,
    )
    responseHttp = fclientBuild(bRaiseServerExceptions=False).post(
        "/api/reproductions/stage", json={"sSource": sPublishedRepo},
    )
    assert responseHttp.status_code == 500
    assert reproductionProgress.DICT_JOBS == {}
    assert flistStagingTokens() == []


def testACrashingWorkerFailsTheJobByTypeAndDiscardsTheStaging(
    sPublishedRepo,
):
    sHostPath = os.path.join(sPublishedRepo, "secretLooking")

    def fnCrashingRerun(*aArgs, **dictArgs):
        raise ValueError(f"unexpected state at {sHostPath}")

    dictView = fdictStageAndRun(sPublishedRepo, fnCrashingRerun)
    assert dictView["sPhase"] == "failed"
    assert dictView["sFailure"] == "the reproduction crashed: ValueError"
    assert sHostPath not in json.dumps(dictView)
    assert dictView["dictReport"] is None
    assert flistStagingTokens() == []


def testARefusedRerunSettlesAsAReportWithNoVerdict(sPublishedRepo):
    def fnRefusingRerun(*aArgs, **dictArgs):
        raise ShadowRerunRefusedError("the image has no python3")

    dictView = fdictStageAndRun(sPublishedRepo, fnRefusingRerun)
    assert dictView["sPhase"] == "settled"
    dictReport = dictView["dictReport"]
    assert dictReport["sVerdict"] == reproductionReport.S_VERDICT_NO_VERDICT
    assert dictReport["bRerunAttempted"] is False
    assert dictReport["iOutputHashesTotal"] == 0
    assert dictReport["listDivergedHashes"] == ["the image has no python3"]
    assert flistStagingTokens() == []


# ---------------------------------------------------------------------
# GET /api/reproductions/reports/{id}/manifest
# ---------------------------------------------------------------------


def fnWriteStoredReport(sReportId, sManifestName=None, sManifestText=""):
    """Write a report (and optionally its companion) where the reader looks."""
    sDirectory = reproductionReport.fsReportsDirectory()
    os.makedirs(sDirectory, exist_ok=True)
    if sManifestName:
        with open(os.path.join(sDirectory, sManifestName), "w") as fileHandle:
            fileHandle.write(sManifestText)
    with open(os.path.join(sDirectory, f"{sReportId}.json"), "w") as fileHandle:
        json.dump({
            "sReportId": sReportId,
            "sReproducedManifestPath": sManifestName,
        }, fileHandle)


def testTheManifestRouteServesTheCompanionAsPlainText():
    sText = "a" * 64 + "  stepAlpha/dataFile.csv\n"
    fnWriteStoredReport(
        "reportAlpha01", "reportAlpha01.sha256", sText,
    )
    responseHttp = fclientBuild().get(
        "/api/reproductions/reports/reportAlpha01/manifest",
    )
    assert responseHttp.status_code == 200
    assert responseHttp.headers["content-type"].startswith("text/plain")
    assert responseHttp.text == sText


def testAReportWithNoCompanionIsA404NamingTheManifest():
    fnWriteStoredReport("reportBeta02")
    responseHttp = fclientBuild().get(
        "/api/reproductions/reports/reportBeta02/manifest",
    )
    assert responseHttp.status_code == 404
    assert responseHttp.json()["detail"] == (
        "No reproduced manifest is stored under that id."
    )
    assert reproductionReport.fsReportsDirectory() not in responseHttp.text


def testACompanionNamingAPathOutsideTheReportsIsRefused(tmp_path):
    """A report's manifest name is a bare name, never a path to follow."""
    sOutside = str(tmp_path / "outsideManifest.sha256")
    with open(sOutside, "w") as fileHandle:
        fileHandle.write("outside contents\n")
    fnWriteStoredReport("reportGamma03", sManifestName=None)
    sPath = os.path.join(
        reproductionReport.fsReportsDirectory(), "reportGamma03.json",
    )
    with open(sPath, "w") as fileHandle:
        json.dump({"sReportId": "reportGamma03",
                   "sReproducedManifestPath": sOutside}, fileHandle)
    responseHttp = fclientBuild().get(
        "/api/reproductions/reports/reportGamma03/manifest",
    )
    assert responseHttp.status_code == 404
    assert "outside contents" not in responseHttp.text
    assert sOutside not in responseHttp.text


def testTheManifestRouteRefusesTheAgentLane():
    fnWriteStoredReport("reportDelta04", "reportDelta04.sha256", "x\n")
    responseHttp = fclientBuild().get(
        "/api/reproductions/reports/reportDelta04/manifest",
        headers=S_AGENT_HEADER,
    )
    assert responseHttp.status_code == 403
    assert "agentTokenValue" not in responseHttp.text
