"""The dashboard's build hears the machine checks ``vaibify build`` prints.

An undersized Docker machine or an emulated architecture used to be
reported only on the command line; a GUI build started regardless and
the researcher found out an hour in, if at all (alpha tester,
2026-10-24). The build route now writes those warnings at the top of
the build log the window shows, and refuses what the command line
refuses (the GPU feature on Apple Silicon). Driven through the real
route; the checks' answers and the docker build itself are stubbed.
"""

import pytest

from tests.testCoverageRoutesBBuild import (  # noqa: F401 -- fixtures
    S_PROJECT_NAME,
    clientBuild,
    fdictWriteProjectEntry,
    fixtureIsolateRegistryAndHome,
)
from vaibify.cli import commandBuild
from vaibify.cli.preflightResult import PreflightResult
from vaibify.gui import buildRoutes


S_MEMORY_MESSAGE = "The Docker daemon has 1.9 GB RAM."


@pytest.fixture
def listLogAtBuildStart(monkeypatch):
    """Stub the slow checks and the build; return the log the build saw."""
    listLog = []
    monkeypatch.setattr(buildRoutes, "_fnRefuseUnbuildableConfiguration",
                        lambda dictProject: None)
    monkeypatch.setattr(buildRoutes, "_fnRefuseWhenDaemonDiskIsFull",
                        lambda: None)

    def fnRecordBuild(dictProject, bNoCache=False, dictProgress=None):
        listLog.extend(dictProgress["dequeTail"])

    monkeypatch.setattr(buildRoutes, "_fnExecuteBuild", fnRecordBuild)
    return listLog


def fnAnswerMachineChecks(monkeypatch, listArch, listMemory):
    monkeypatch.setattr(commandBuild, "_flistPreflightArch",
                        lambda config: list(listArch))
    monkeypatch.setattr(commandBuild, "_flistPreflightMemory",
                        lambda: list(listMemory))


@pytest.mark.falsification
def testAnUndersizedMachineIsNamedAtTheTopOfTheBuildLog(
        clientBuild, tmp_path, monkeypatch, listLogAtBuildStart):
    """Kills: a GUI build that skips the machine checks."""
    fdictWriteProjectEntry(tmp_path, bObtained=False)
    fnAnswerMachineChecks(monkeypatch, [], [PreflightResult(
        sName="docker-memory", sLevel="warn", sMessage=S_MEMORY_MESSAGE,
        sRemediation="Give Colima more memory.")])
    responseHttp = clientBuild.post(f"/api/containers/{S_PROJECT_NAME}/build")
    assert responseHttp.status_code == 200, responseHttp.text
    assert listLogAtBuildStart[0] == (
        "[vaib] WARNING: " + S_MEMORY_MESSAGE + " Give Colima more memory.")


def testTheGpuFeatureOnAppleSiliconIsRefusedBeforeTheBuild(
        clientBuild, tmp_path, monkeypatch, listLogAtBuildStart):
    fdictWriteProjectEntry(tmp_path, bObtained=False)
    fnAnswerMachineChecks(monkeypatch, [PreflightResult(
        sName="arch-gpu", sLevel="fail",
        sMessage="NVIDIA CUDA images are amd64-only.",
        sRemediation="Disable in vaibify.yml.")], [])
    responseHttp = clientBuild.post(f"/api/containers/{S_PROJECT_NAME}/build")
    assert responseHttp.status_code == 409
    assert responseHttp.json()["detail"]["sRefusal"] == (
        buildRoutes.S_REFUSAL_HOST_CANNOT_BUILD)
    assert listLogAtBuildStart == []


def testAMachineThatPassesAddsNothingToTheLog(
        clientBuild, tmp_path, monkeypatch, listLogAtBuildStart):
    fdictWriteProjectEntry(tmp_path, bObtained=False)
    fnAnswerMachineChecks(monkeypatch, [], [])
    responseHttp = clientBuild.post(f"/api/containers/{S_PROJECT_NAME}/build")
    assert responseHttp.status_code == 200, responseHttp.text
    assert listLogAtBuildStart == []
