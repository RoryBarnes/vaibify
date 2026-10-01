"""A GitHub verify is bound to the remote the project last pushed to.

``verify-remote`` is agent-safe, and it queried whatever owner, repository
and branch the checkout's ``origin`` named, with the researcher's own
GitHub token, then wrote the remote files' digests into a file the
container reads. An agent that rewrote ``.git/config`` could therefore ask
the hub whether, and with what content, any repository the researcher can
read contains a given path. The binding that already existed on the PUSH
path (token owner equals remote owner) was absent on the verify path.

The push now records, in the hub's registry where the container cannot
write, the remote it reached; a verify follows that record and refuses a
remote the container names instead.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from vaibify.config import registryManager
from vaibify.gui import workflowManager
from vaibify.gui.routes import syncRoutes
from vaibify.reproducibility import scheduledReverify
from vaibify.reproducibility.scheduledReverify import (
    ReverifyConfigError,
    S_GITHUB_NOT_BOUND,
    S_HOST_GITHUB_BINDING_KEY,
    _fdictRequireServiceConfig,
)

S_REPO_PATH = "/workspace/project"


class _FilesWithOrigin:
    """A files adapter whose .git/config names one origin."""

    sRootPath = S_REPO_PATH

    def __init__(self, sUrl):
        self._sConfig = f'[remote "origin"]\n\turl = {sUrl}\n'

    def fbIsFile(self, sPath):
        return sPath == ".git/config"

    def fsReadText(self, sPath):
        return self._sConfig


def _fdictWorkflow(dictBound=None, dictDeclared=None):
    dictWorkflow = {"sProjectRepoPath": S_REPO_PATH, "listSteps": []}
    if dictBound is not None:
        dictWorkflow[S_HOST_GITHUB_BINDING_KEY] = dictBound
    if dictDeclared:
        dictWorkflow["dictRemotes"] = {"github": dictDeclared}
    return dictWorkflow


DICT_PUSHED = {S_REPO_PATH: {
    "sOwner": "Researcher", "sRepo": "Project", "sBranch": "trunk"}}


@pytest.mark.falsification
def testAnOriginTheContainerRewroteIsNotQueriedWithTheResearchersToken():
    """Kills: dropping the binding check from the GitHub config resolver."""
    filesRepo = _FilesWithOrigin("https://github.com/victim/private.git")
    with patch.object(
        scheduledReverify.githubMirror, "fdictFetchRemoteHashes",
        side_effect=AssertionError("the token was used on a foreign remote"),
    ) as mockFetch:
        with pytest.raises(ReverifyConfigError) as errorRaised:
            _fdictRequireServiceConfig(
                _fdictWorkflow(DICT_PUSHED), "github", filesRepo)
    assert "Researcher/Project" in str(errorRaised.value)
    assert "trunk" in str(errorRaised.value)
    mockFetch.assert_not_called()


def testADeclaredRemoteIsBoundJustTheSame():
    filesRepo = _FilesWithOrigin("https://github.com/researcher/project.git")
    dictWorkflow = _fdictWorkflow(
        DICT_PUSHED, {"sOwner": "victim", "sRepo": "private"})
    with pytest.raises(ReverifyConfigError):
        _fdictRequireServiceConfig(dictWorkflow, "github", filesRepo)


def testTheRemoteThePushReachedIsVerifiedOnTheRecordedBranch():
    filesRepo = _FilesWithOrigin(
        "https://github.com/researcher/PROJECT.git")
    dictConfig = _fdictRequireServiceConfig(
        _fdictWorkflow(DICT_PUSHED), "github", filesRepo)
    assert (dictConfig["sOwner"], dictConfig["sRepo"]) == (
        "researcher", "PROJECT")
    assert dictConfig["sBranch"] == "trunk"


def testADeclaredBranchThatDiffersFromThePushedOneIsRefused():
    filesRepo = _FilesWithOrigin("https://github.com/researcher/project.git")
    dictWorkflow = _fdictWorkflow(
        DICT_PUSHED, {"sOwner": "researcher", "sRepo": "project",
                      "sBranch": "somethingElse"})
    with pytest.raises(ReverifyConfigError):
        _fdictRequireServiceConfig(dictWorkflow, "github", filesRepo)
    dictWorkflow["dictRemotes"]["github"]["sBranch"] = "trunk"
    assert _fdictRequireServiceConfig(
        dictWorkflow, "github", filesRepo)["sBranch"] == "trunk"


@pytest.mark.falsification
def testAProjectThatNeverPushedIsNotVerifiedAgainstGithub():
    """Kills: treating an empty record as 'verify as declared'."""
    filesRepo = _FilesWithOrigin("https://github.com/researcher/project.git")
    with pytest.raises(ReverifyConfigError) as errorRaised:
        _fdictRequireServiceConfig(_fdictWorkflow({}), "github", filesRepo)
    assert str(errorRaised.value) == S_GITHUB_NOT_BOUND
    assert "github" not in scheduledReverify.flistSelectConfiguredServices(
        _fdictWorkflow({}), filesRepo)


def testAnotherRepositorysRecordDoesNotBindThisOne():
    filesRepo = _FilesWithOrigin("https://github.com/researcher/project.git")
    dictOther = {"/workspace/other": DICT_PUSHED[S_REPO_PATH]}
    with pytest.raises(ReverifyConfigError):
        _fdictRequireServiceConfig(
            _fdictWorkflow(dictOther), "github", filesRepo)


def testOtherServicesAreNotAffectedByTheGithubBinding():
    dictWorkflow = _fdictWorkflow({})
    dictWorkflow["dictRemotes"] = {"overleaf": {"sProjectId": "abc"}}
    dictConfig = _fdictRequireServiceConfig(dictWorkflow, "overleaf", None)
    assert dictConfig["sProjectId"] == "abc"


def testAWorkflowBuiltOutsideTheHubIsComparedAsDeclared():
    filesRepo = _FilesWithOrigin("https://github.com/someone/else.git")
    dictConfig = _fdictRequireServiceConfig(
        _fdictWorkflow(), "github", filesRepo)
    assert (dictConfig["sOwner"], dictConfig["sRepo"]) == ("someone", "else")


# ---------------------------------------------------------------------
# The record: host-held, attached at load, never persisted
# ---------------------------------------------------------------------


@pytest.fixture
def pathRegistry(tmp_path, monkeypatch):
    sDirectory = str(tmp_path / "vaibifyHome")
    monkeypatch.setattr(registryManager, "_S_REGISTRY_DIRECTORY", sDirectory)
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH", sDirectory + "/registry.json")
    monkeypatch.setattr(
        registryManager, "_S_LOCK_PATH", sDirectory + "/registry.lock")
    registryManager.fnSaveRegistry({"listProjects": [
        {"sName": "boundProject", "sContainerName": "boundProject",
         "sDirectory": "/x", "sConfigPath": "/x/vaibify.yml"}]})
    return sDirectory


def testThePushedRemoteRoundTripsThroughTheHubsRegistry(pathRegistry):
    assert registryManager.fdictGetPushedGithubRemotes("boundProject") == {}
    registryManager.fnRecordPushedGithubRemote(
        "boundProject", S_REPO_PATH, "researcher", "project", "main")
    registryManager.fnRecordPushedGithubRemote(
        "boundProject", "/workspace/second", "researcher", "second", "")
    dictRemotes = registryManager.fdictGetPushedGithubRemotes("boundProject")
    assert dictRemotes[S_REPO_PATH] == {
        "sOwner": "researcher", "sRepo": "project", "sBranch": "main"}
    assert set(dictRemotes) == {S_REPO_PATH, "/workspace/second"}
    assert registryManager.fdictGetPushedGithubRemotes("nobody") == {}
    registryManager.fnRecordPushedGithubRemote(
        "nobody", S_REPO_PATH, "a", "b", "c")


def testTheLoaderAttachesTheRecordAndTheSaveNeverPersistsIt(pathRegistry):
    registryManager.fnRecordPushedGithubRemote(
        "boundProject", S_REPO_PATH, "researcher", "project", "main")
    mockDocker = MagicMock()
    mockDocker.fbaFetchFile.return_value = json.dumps({
        "sPlotDirectory": "Plot", "listSteps": []}).encode("utf-8")
    mockDocker.fcontainerGetById.return_value.name = "boundProject"
    dictWorkflow = workflowManager.fdictLoadWorkflowFromContainer(
        mockDocker, "dockerid123",
        "/workspace/project/.vaibify/projects/project.json")
    assert dictWorkflow[S_HOST_GITHUB_BINDING_KEY][S_REPO_PATH][
        "sOwner"] == "researcher"
    assert S_HOST_GITHUB_BINDING_KEY not in (
        workflowManager._fdictStripComputedFields(dictWorkflow))


def testAnUnresolvableContainerNameLeavesTheProjectUnbound(pathRegistry):
    mockDocker = MagicMock()
    mockDocker.fbaFetchFile.return_value = json.dumps({
        "sPlotDirectory": "Plot", "listSteps": []}).encode("utf-8")
    dictWorkflow = workflowManager.fdictLoadWorkflowFromContainer(
        mockDocker, "dockerid123",
        "/workspace/project/.vaibify/projects/project.json")
    assert dictWorkflow[S_HOST_GITHUB_BINDING_KEY] == {}


# ---------------------------------------------------------------------
# The push records what it reached
# ---------------------------------------------------------------------


class _FakeDockerReadingTheRemote:
    def __init__(self, sUrl, sBranch):
        self._sUrl = sUrl
        self._sBranch = sBranch

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        assert "remote get-url" in sCommand
        return (0, self._sUrl + "\n")

    def fbaFetchFile(self, sContainerId, sPath):
        assert sPath.endswith("/.git/HEAD")
        return f"ref: refs/heads/{self._sBranch}\n".encode("utf-8")


def testThePushReadsTheRemoteAndBranchItReached():
    docker = _FakeDockerReadingTheRemote(
        "https://github.com/researcher/project.git", "trunk")
    assert syncRoutes._fdictReadReachedRemote(
        docker, "cid", S_REPO_PATH) == {
        "sOwner": "researcher", "sRepo": "project", "sBranch": "trunk"}


def testAnUnreadableRemoteRecordsNothingAndDoesNotFailThePush():
    assert syncRoutes._fdictReadReachedRemote(
        object(), "cid", S_REPO_PATH) == {}


def testTheRecorderWritesOnlyWhatThePushReached():
    dictCtx = {"docker": MagicMock()}
    with patch.object(
        registryManager, "fnRecordPushedGithubRemote",
    ) as mockRecord, patch.object(
        syncRoutes, "fsContainerNameForId", return_value="boundProject",
    ):
        syncRoutes._fnRecordPushedRemoteHostSide(
            dictCtx, "dockerid123", S_REPO_PATH,
            {"sOwner": "researcher", "sRepo": "project", "sBranch": "main"})
        syncRoutes._fnRecordPushedRemoteHostSide(
            dictCtx, "dockerid123", S_REPO_PATH, {})
    mockRecord.assert_called_once_with(
        "boundProject", S_REPO_PATH, "researcher", "project", "main")


# ---------------------------------------------------------------------
# The push route, end to end through the real handler
# ---------------------------------------------------------------------


def _fmockPushThroughTheRoute(iPushExitCode):
    """Drive the real push handler; return the registry-record mock."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from tests import testGithubTokenBinding as moduleBinding

    class _DockerWithHead(moduleBinding._ConnectionAllFilesExist):
        def fbaFetchFile(self, sContainerId, sPath):
            return b"ref: refs/heads/trunk\n"

    dictCtx = moduleBinding._fdictBuildPushContext()
    dictCtx["docker"] = _DockerWithHead()
    app = FastAPI()
    app.state.listLifespanStartup = []
    app.state.listLifespanShutdown = []
    syncRoutes.fnRegisterAll(app, dictCtx)
    listPatches = [
        patch("vaibify.gui.containerGit.fsRemoteUrlInContainer",
              return_value="https://github.com/victim/myrepo.git"),
        moduleBinding._fpatchHostCredentialSources("ghp_validToken"),
        patch("vaibify.reproducibility.githubAuth._ftFetchLoginFresh",
              return_value=("victim", "")),
        patch("vaibify.gui.syncDispatcher.ftResultPushToGithub",
              return_value=(iPushExitCode, "abc1234")),
        patch("vaibify.gui.routes.syncRoutes._fnRequireNetworkAccess"),
        patch("vaibify.gui.routes.syncRoutes._fnValidateGithubPushPaths"),
        patch("vaibify.gui.routes.scriptRoutes._fnStoreCommitHash"),
    ]
    with patch.object(
        registryManager, "fnRecordPushedGithubRemote",
    ) as mockRecord:
        for patchOne in listPatches:
            patchOne.start()
        try:
            TestClient(app).post("/api/github/cid/push", json={
                "listFilePaths": ["step01/output.dat"],
                "sCommitMessage": "msg"})
        finally:
            for patchOne in reversed(listPatches):
                patchOne.stop()
    return mockRecord


@pytest.fixture
def fixtureCarrierStoodDown(monkeypatch):
    from tests.carrierStandDown import fnStandCarrierDown
    fnStandCarrierDown(monkeypatch, syncRoutes)


@pytest.mark.falsification
def testThePushRouteRecordsTheRemoteItReachedOnlyOnSuccess(
        fixtureCarrierStoodDown):
    """Kills: the push route not recording where a successful push landed."""
    _fmockPushThroughTheRoute(0).assert_called_once_with(
        "cid", "/workspace/myrepo", "victim", "myrepo", "trunk")
    assert not _fmockPushThroughTheRoute(1).called, (
        "a failed push binds nothing")
