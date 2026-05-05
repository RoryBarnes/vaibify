"""Tests for the Repos panel route module (repoRoutes.py)."""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vaibify.gui.routes.repoRoutes import fnRegisterAll


class FakeDocker:
    """In-memory fake docker connection for repo route tests.

    Simulates a set of repositories under /workspace and a sidecar
    file at /workspace/.vaibify/tracked_repos.json.  Supports the
    small subset of shell commands issued by trackedReposManager,
    syncDispatcher.ftResultPushToGithub/PushStagedToGithub, and
    syncDispatcher.flistGetDirtyFiles.
    """

    def __init__(self):
        self.dictRepos = {}
        self.dictFiles = {}
        self.setNonGitDirs = set()
        self.listDirtyLines = []
        self.dictPushStaged = {"exit": 0, "out": "abc1234"}
        self.dictPushFiles = {"exit": 0, "out": "def5678"}
        self.listGitInitCalls = []

    def fnAddRepo(self, sName, sUrl="https://x/y.git",
                  sBranch="main", bDirty=False):
        self.dictRepos[sName] = {
            "sUrl": sUrl, "sBranch": sBranch, "bDirty": bDirty,
        }

    def fnAddNonGitDir(self, sName):
        """Register a /workspace/ subdirectory that lacks a .git/."""
        self.setNonGitDirs.add(sName)

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        if sCommand.startswith("cat /workspace/.vaibify/"):
            sContent = self.dictFiles.get(
                "/workspace/.vaibify/tracked_repos.json", "")
            if sContent:
                return (0, sContent)
            return (1, "")
        if sCommand.startswith("mkdir -p"):
            return self._ftMkdirCommand(sCommand)
        if "find /workspace -mindepth 2" in sCommand:
            listOut = [
                f"/workspace/{s}" for s in self.dictRepos
            ]
            return (0, "\n".join(listOut) + "\n")
        if "find /workspace -mindepth 1 -maxdepth 1" in sCommand:
            listAll = list(self.dictRepos) + list(self.setNonGitDirs)
            return (0, "\n".join(sorted(listAll)) + "\n")
        if (sCommand.startswith("test -e")
                and "/workspace/" in sCommand
                and "/.git" in sCommand):
            return self._ftDotGitExistsCommand(sCommand)
        if (sCommand.startswith("test -d")
                and "/workspace/" in sCommand):
            return self._ftDirExistsCommand(sCommand)
        if sCommand.startswith("git -C") and "init " in sCommand:
            return self._ftGitInitCommand(sCommand)
        if sCommand.startswith("git -C"):
            return self._ftGitCommand(sCommand)
        if sCommand.startswith("cd '/workspace/"):
            return self._ftPushCommand(sCommand)
        return (0, "")

    def _ftDirExistsCommand(self, sCommand):
        sName = sCommand.split("/workspace/")[1].split("/")[0]
        sName = sName.rstrip("'").rstrip()
        bIsGitProbe = "/.git" in sCommand
        bExists = (
            sName in self.dictRepos
            if bIsGitProbe
            else (sName in self.dictRepos or sName in self.setNonGitDirs)
        )
        if "&& echo yes" in sCommand:
            return (0, "yes\n" if bExists else "no\n")
        return (0, "") if bExists else (1, "")

    def _ftDotGitExistsCommand(self, sCommand):
        sName = sCommand.split("/workspace/")[1].split("/")[0]
        sName = sName.rstrip("'").rstrip()
        if sName in self.dictRepos:
            return (0, "")
        return (1, "")

    def _ftMkdirCommand(self, sCommand):
        sPath = sCommand.split("mkdir -p ", 1)[1].strip()
        sPath = sPath.strip("'")
        if sPath.startswith("/workspace/"):
            sName = sPath[len("/workspace/"):].split("/")[0]
            if sName and not sName.startswith("."):
                self.setNonGitDirs.add(sName)
        return (0, "")

    def _ftGitInitCommand(self, sCommand):
        sName = sCommand.split("/workspace/")[1].split(" ")[0]
        sName = sName.rstrip("'").rstrip()
        self.listGitInitCalls.append(sName)
        self.setNonGitDirs.discard(sName)
        if sName not in self.dictRepos:
            self.fnAddRepo(sName)
        return (0, "Initialized empty Git repository\n")

    def _ftGitCommand(self, sCommand):
        sTail = sCommand.split("/workspace/")[1]
        sName = sTail.split(" ")[0].rstrip("'").rstrip("/")
        dictRepo = self.dictRepos.get(sName)
        if dictRepo is None:
            return (128, "")
        if "rev-parse --abbrev-ref HEAD" in sCommand:
            return (0, dictRepo["sBranch"] + "\n")
        if "config --get remote.origin.url" in sCommand:
            return (0, dictRepo["sUrl"] + "\n")
        if "status --porcelain" in sCommand:
            sOut = "\n".join(self.listDirtyLines)
            if dictRepo["bDirty"] and not sOut:
                sOut = " M changed.py"
            return (0, sOut + ("\n" if sOut else ""))
        return (0, "")

    def _ftPushCommand(self, sCommand):
        if "git commit" in sCommand and "git add" not in sCommand:
            return (
                self.dictPushStaged["exit"],
                self.dictPushStaged["out"],
            )
        return (
            self.dictPushFiles["exit"],
            self.dictPushFiles["out"],
        )

    def fnWriteFile(self, sContainerId, sPath, baContent):
        self.dictFiles[sPath] = baContent.decode("utf-8")


@pytest.fixture
def fixtureDocker():
    """Fresh fake docker for each test."""
    return FakeDocker()


@pytest.fixture
def fixtureClient(fixtureDocker):
    """FastAPI TestClient wired to the fake docker."""
    app = FastAPI()
    dictCtx = {
        "docker": fixtureDocker,
        "require": lambda: None,
    }
    fnRegisterAll(app, dictCtx)
    return TestClient(app)


# ------- GET /status -------

def testStatusAutoSeedsDiscoveredRepos(
    fixtureDocker, fixtureClient
):
    fixtureDocker.fnAddRepo("alpha")
    response = fixtureClient.get("/api/repos/cid1/status")
    assert response.status_code == 200
    dictBody = response.json()
    listTrackedNames = [
        d["sName"] for d in dictBody["listTracked"]
    ]
    assert listTrackedNames == ["alpha"]
    assert dictBody["listIgnored"] == []
    assert dictBody["listUndecided"] == []


def testStatusAutoSeedsWhenSidecarMissing(
    fixtureDocker, fixtureClient
):
    fixtureDocker.fnAddRepo("alpha", sUrl="https://x/alpha.git")
    fixtureDocker.fnAddRepo("beta", sUrl="https://x/beta.git")
    response = fixtureClient.get("/api/repos/cid1/status")
    assert response.status_code == 200
    dictBody = response.json()
    listTrackedNames = sorted(
        d["sName"] for d in dictBody["listTracked"]
    )
    assert listTrackedNames == ["alpha", "beta"]
    assert dictBody["listUndecided"] == []
    sSidecar = fixtureDocker.dictFiles[
        "/workspace/.vaibify/tracked_repos.json"
    ]
    dictStored = json.loads(sSidecar)
    listStoredNames = sorted(
        d["sName"] for d in dictStored["listTracked"]
    )
    assert listStoredNames == ["alpha", "beta"]


def testStatusAutoSeedsEmptyWhenNoRepos(
    fixtureDocker, fixtureClient
):
    response = fixtureClient.get("/api/repos/cid1/status")
    assert response.status_code == 200
    dictBody = response.json()
    assert dictBody["listTracked"] == []
    assert dictBody["listUndecided"] == []
    assert (
        "/workspace/.vaibify/tracked_repos.json"
        in fixtureDocker.dictFiles
    )


def testStatusReturnsTrackedWithUrl(
    fixtureDocker, fixtureClient
):
    fixtureDocker.fnAddRepo("alpha", sUrl="https://x/alpha.git")
    fixtureClient.post("/api/repos/cid1/alpha/track")
    response = fixtureClient.get("/api/repos/cid1/status")
    dictTracked = response.json()["listTracked"][0]
    assert dictTracked["sName"] == "alpha"
    assert dictTracked["sUrl"] == "https://x/alpha.git"
    assert dictTracked["bMissing"] is False
    assert dictTracked["sBranch"] == "main"


def testStatusMarksTrackedButMissingAsMissing(
    fixtureDocker, fixtureClient
):
    fixtureDocker.fnAddRepo("alpha")
    fixtureClient.post("/api/repos/cid1/alpha/track")
    del fixtureDocker.dictRepos["alpha"]
    response = fixtureClient.get("/api/repos/cid1/status")
    listTracked = response.json()["listTracked"]
    assert len(listTracked) == 1
    assert listTracked[0]["sName"] == "alpha"
    assert listTracked[0]["bMissing"] is True


def testStatusIgnoredAppearsInList(
    fixtureDocker, fixtureClient
):
    fixtureDocker.fnAddRepo("alpha")
    fixtureClient.post("/api/repos/cid1/alpha/ignore")
    response = fixtureClient.get("/api/repos/cid1/status")
    dictBody = response.json()
    assert dictBody["listIgnored"] == ["alpha"]
    assert dictBody["listUndecided"] == []


# ------- listNonRepoDirs in /status -------

def testStatusListNonRepoDirsEmptyByDefault(
    fixtureDocker, fixtureClient
):
    fixtureDocker.fnAddRepo("alpha")
    response = fixtureClient.get("/api/repos/cid1/status")
    assert response.status_code == 200
    assert response.json()["listNonRepoDirs"] == []


def testStatusListNonRepoDirsIncludesPlainDirs(
    fixtureDocker, fixtureClient
):
    fixtureDocker.fnAddRepo("alpha")
    fixtureDocker.fnAddNonGitDir("scratch")
    fixtureDocker.fnAddNonGitDir("data_only")
    response = fixtureClient.get("/api/repos/cid1/status")
    listNames = sorted(
        d["sName"] for d in response.json()["listNonRepoDirs"]
    )
    assert listNames == ["data_only", "scratch"]


def testStatusListNonRepoDirsExcludesIgnored(
    fixtureDocker, fixtureClient
):
    fixtureDocker.fnAddRepo("alpha")
    fixtureDocker.fnAddNonGitDir("scratch")
    fixtureClient.post("/api/repos/cid1/scratch/ignore")
    response = fixtureClient.get("/api/repos/cid1/status")
    assert response.json()["listNonRepoDirs"] == []


# ------- POST /init -------

def testInitProjectRepoConvertsExistingDir(
    fixtureDocker, fixtureClient
):
    fixtureDocker.fnAddNonGitDir("scratch")
    response = fixtureClient.post(
        "/api/repos/cid1/init",
        json={"sDirectory": "scratch", "bCreateIfMissing": False},
    )
    assert response.status_code == 200
    dictBody = response.json()
    assert dictBody["sDirectory"] == "scratch"
    assert dictBody["sFullPath"] == "/workspace/scratch"
    assert "scratch" in fixtureDocker.listGitInitCalls


def testInitProjectRepoCreatesMissingDir(
    fixtureDocker, fixtureClient
):
    response = fixtureClient.post(
        "/api/repos/cid1/init",
        json={"sDirectory": "fresh", "bCreateIfMissing": True},
    )
    assert response.status_code == 200
    assert "fresh" in fixtureDocker.listGitInitCalls


def testInitProjectRepoRejectsAlreadyGitRepo(
    fixtureDocker, fixtureClient
):
    fixtureDocker.fnAddRepo("alpha")
    response = fixtureClient.post(
        "/api/repos/cid1/init",
        json={"sDirectory": "alpha", "bCreateIfMissing": False},
    )
    assert response.status_code == 409


def testInitProjectRepoMissingDirReturns404(
    fixtureDocker, fixtureClient
):
    response = fixtureClient.post(
        "/api/repos/cid1/init",
        json={"sDirectory": "ghost", "bCreateIfMissing": False},
    )
    assert response.status_code == 404


def testInitProjectRepoRejectsPathTraversal(fixtureClient):
    response = fixtureClient.post(
        "/api/repos/cid1/init",
        json={"sDirectory": "../etc", "bCreateIfMissing": False},
    )
    assert response.status_code == 400


def testInitProjectRepoRejectsLeadingDot(fixtureClient):
    response = fixtureClient.post(
        "/api/repos/cid1/init",
        json={"sDirectory": ".hidden", "bCreateIfMissing": True},
    )
    assert response.status_code == 400


def testInitProjectRepoRejectsCreateOnExistingDir(
    fixtureDocker, fixtureClient
):
    """bCreateIfMissing=True must 409 if the dir already exists.

    Prevents silently absorbing a pre-existing /workspace/<name>
    directory into a brand-new git repo (failure mode #10).
    """
    fixtureDocker.fnAddNonGitDir("scratch")
    response = fixtureClient.post(
        "/api/repos/cid1/init",
        json={"sDirectory": "scratch", "bCreateIfMissing": True},
    )
    assert response.status_code == 409
    assert "scratch" not in fixtureDocker.listGitInitCalls


# ------- POST /track -------

def testTrackAddsToSidecar(fixtureDocker, fixtureClient):
    fixtureDocker.fnAddRepo("alpha", sUrl="https://x/alpha.git")
    response = fixtureClient.post("/api/repos/cid1/alpha/track")
    assert response.status_code == 200
    assert response.json() == {"bSuccess": True}
    dictStored = json.loads(
        fixtureDocker.dictFiles[
            "/workspace/.vaibify/tracked_repos.json"]
    )
    listNames = [r["sName"] for r in dictStored["listTracked"]]
    assert "alpha" in listNames


def testTrackRejectsPathTraversal(fixtureClient):
    response = fixtureClient.post("/api/repos/cid1/..etc/track")
    assert response.status_code == 400


def testTrackRejectsSemicolonInjection(fixtureClient):
    response = fixtureClient.post(
        "/api/repos/cid1/foo;rm/track"
    )
    assert response.status_code == 400


def testValidateRepoNameRejectsSlash():
    from vaibify.gui.routes.repoRoutes import _fbValidateRepoName
    assert _fbValidateRepoName("foo/bar") is False
    assert _fbValidateRepoName("foo..bar") is False
    assert _fbValidateRepoName(".hidden") is False
    assert _fbValidateRepoName("") is False
    assert _fbValidateRepoName("validName") is True
    assert _fbValidateRepoName("valid-repo_2.x") is True


def testTrackRejectsLeadingDot(fixtureClient):
    response = fixtureClient.post("/api/repos/cid1/.hidden/track")
    assert response.status_code == 400


def testTrackMissingRepoReturns404(fixtureClient):
    response = fixtureClient.post("/api/repos/cid1/ghost/track")
    assert response.status_code == 404


# ------- POST /ignore -------

def testIgnoreAddsToSidecar(fixtureDocker, fixtureClient):
    fixtureDocker.fnAddRepo("alpha")
    response = fixtureClient.post("/api/repos/cid1/alpha/ignore")
    assert response.status_code == 200
    dictStored = json.loads(
        fixtureDocker.dictFiles[
            "/workspace/.vaibify/tracked_repos.json"]
    )
    listNames = [r["sName"] for r in dictStored["listIgnored"]]
    assert "alpha" in listNames


def testIgnoreRejectsPathTraversal(fixtureClient):
    response = fixtureClient.post("/api/repos/cid1/..x/ignore")
    assert response.status_code == 400


# ------- POST /untrack -------

def testUntrackRemovesFromSidecar(
    fixtureDocker, fixtureClient
):
    fixtureDocker.fnAddRepo("alpha")
    fixtureClient.post("/api/repos/cid1/alpha/track")
    response = fixtureClient.post("/api/repos/cid1/alpha/untrack")
    assert response.status_code == 200
    dictStored = json.loads(
        fixtureDocker.dictFiles[
            "/workspace/.vaibify/tracked_repos.json"]
    )
    assert dictStored["listTracked"] == []


def testUntrackRejectsBadName(fixtureClient):
    response = fixtureClient.post("/api/repos/cid1/..x/untrack")
    assert response.status_code == 400


# ------- POST /push-staged -------

def testPushStagedSucceedsForTrackedRepo(
    fixtureDocker, fixtureClient
):
    fixtureDocker.fnAddRepo("alpha")
    fixtureClient.post("/api/repos/cid1/alpha/track")
    response = fixtureClient.post(
        "/api/repos/cid1/alpha/push-staged",
        json={"sCommitMessage": "update"},
    )
    assert response.status_code == 200
    assert response.json()["bSuccess"] is True


def testPushStagedRejectsUntrackedRepo(
    fixtureDocker, fixtureClient
):
    fixtureDocker.fnAddRepo("alpha")
    response = fixtureClient.post(
        "/api/repos/cid1/alpha/push-staged",
        json={"sCommitMessage": "update"},
    )
    assert response.status_code == 400


def testPushStagedRejectsBadName(fixtureClient):
    response = fixtureClient.post(
        "/api/repos/cid1/..x/push-staged",
        json={"sCommitMessage": "update"},
    )
    assert response.status_code == 400


# ------- POST /push-files -------

def testPushFilesSucceedsForTrackedRepo(
    fixtureDocker, fixtureClient
):
    fixtureDocker.fnAddRepo("alpha")
    fixtureClient.post("/api/repos/cid1/alpha/track")
    response = fixtureClient.post(
        "/api/repos/cid1/alpha/push-files",
        json={
            "sCommitMessage": "update",
            "listFilePaths": ["foo.py", "bar.py"],
        },
    )
    assert response.status_code == 200
    assert response.json()["bSuccess"] is True


def testPushFilesRejectsUntrackedRepo(
    fixtureDocker, fixtureClient
):
    fixtureDocker.fnAddRepo("alpha")
    response = fixtureClient.post(
        "/api/repos/cid1/alpha/push-files",
        json={
            "sCommitMessage": "update",
            "listFilePaths": ["foo.py"],
        },
    )
    assert response.status_code == 400


def testPushFilesRejectsBadName(fixtureClient):
    response = fixtureClient.post(
        "/api/repos/cid1/..x/push-files",
        json={
            "sCommitMessage": "update",
            "listFilePaths": ["foo.py"],
        },
    )
    assert response.status_code == 400


# ------- GET /dirty-files -------

def testDirtyFilesReturnsParsedList(
    fixtureDocker, fixtureClient
):
    fixtureDocker.fnAddRepo("alpha")
    fixtureClient.post("/api/repos/cid1/alpha/track")
    fixtureDocker.listDirtyLines = [" M file1.py", "?? file2.py"]
    response = fixtureClient.get(
        "/api/repos/cid1/alpha/dirty-files"
    )
    assert response.status_code == 200
    listDirty = response.json()["listDirtyFiles"]
    assert len(listDirty) == 2
    listStatuses = [d["sStatus"] for d in listDirty]
    assert "modified" in listStatuses
    assert "untracked" in listStatuses


def testDirtyFilesRejectsUntracked(
    fixtureDocker, fixtureClient
):
    fixtureDocker.fnAddRepo("alpha")
    response = fixtureClient.get(
        "/api/repos/cid1/alpha/dirty-files"
    )
    assert response.status_code == 400


def testDirtyFilesRejectsBadName(fixtureClient):
    response = fixtureClient.get(
        "/api/repos/cid1/..x/dirty-files"
    )
    assert response.status_code == 400


def testRepoRoutesRegisteredInApplication():
    """Regression guard: fappCreateApplication wires repoRoutes."""
    from vaibify.gui.pipelineServer import fappCreateApplication
    app = fappCreateApplication()
    listRepoPaths = [
        route.path for route in app.routes
        if "/api/repos/" in getattr(route, "path", "")
    ]
    assert len(listRepoPaths) >= 7, (
        f"Expected at least 7 repo routes, got {len(listRepoPaths)}"
    )
    sPaths = " ".join(listRepoPaths)
    assert "status" in sPaths
    assert "track" in sPaths
    assert "push-staged" in sPaths
