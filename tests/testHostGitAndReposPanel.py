"""A host project's git panel, repositories panel, and GitHub push.

Phase C is parity: the panels that mutate a repository have to work on
the researcher's own machine, not only inside a container. Every test
here drives the REAL routes over HTTP against a REAL git repository on
disk, through the real ``HostConnection`` — no adapter, no scripted
command table. What ``git log`` says afterwards is the oracle.

That is deliberate. The defects this file pins were all invisible to a
unit stub and all found by driving:

- **The GitHub push answered 400 before any git ran**, because its
  path validator measured the request against ``/workspace``. A host
  project's files are never inside that, so the guard refused the
  whole feature rather than an attack.
- **Repositories → Init answered 500** ``mkdir: /workspace:
  Read-only file system``, because the panel composed
  ``"/workspace/" + name``. The 500 then quarantined the project, so
  one click on a button that could never work left the researcher
  unable to do anything else until reconcile.
- **A plain FILE was offered as somewhere to run ``git init``.**
  Discovery asked only "does <name>/.git exist"; everything that
  answered no became a non-repository DIRECTORY, and a host project's
  root is mostly files.

BOTH DIRECTIONS live where each mechanism lives: the container
direction for the roots resolver is in ``testHostModeProjectRoots``,
and the panel's container behaviour is pinned by ``testRepoRoutes``
and ``testSyncRoutesCoverage``, which drive the same routes with
``/workspace`` doubles and are unchanged by this work.
"""

import json
import os
import subprocess

import pytest
from starlette.testclient import TestClient

from vaibify.config import containerLock, preferencesStore, registryManager
from tests.sessionTokenTestHelper import fsBootstrapCredential


S_PROJECT = "hostGitPanelProject"
S_TRACKED_REPOSITORY = "InnerRepo"


def _fnRunGit(sDirectory, *aArguments):
    """Run one git command in a directory, raising on failure."""
    return subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *aArguments],
        cwd=sDirectory, check=True, capture_output=True, text=True,
    ).stdout


def _fsBuildHostProject(sHome):
    """Create a host project holding a repo, a file, and a workflow."""
    sProject = os.path.join(sHome, S_PROJECT)
    os.makedirs(os.path.join(sProject, ".vaibify", "projects"))
    with open(
        os.path.join(sProject, "vaibify.yml"), "w",
    ) as fileConfig:
        fileConfig.write(f"projectName: {S_PROJECT}\n")
    with open(
        os.path.join(sProject, ".vaibify", "projects", "panel.json"), "w",
    ) as fileWorkflow:
        json.dump({
            "sPlotDirectory": "Plot",
            "sFigureType": "png",
            "iNumberOfCores": 1,
            "listSteps": [],
        }, fileWorkflow)
    _fnRunGit(sProject, "init", "-q")
    _fnRunGit(sProject, "config", "user.email", "panel@example.invalid")
    _fnRunGit(sProject, "config", "user.name", "Panel Lane")
    _fnRunGit(sProject, "add", "-A")
    _fnRunGit(sProject, "commit", "-q", "-m", "seed")
    return sProject


@pytest.fixture
def tHostPanel(tmp_path, monkeypatch):
    """Serve the real hub over a real host project; yield (client, dirs).

    The registry, the flocks and the preferences all move into
    ``tmp_path``: this fixture registers a project and claims it, and
    a lane that wrote the researcher's real ``~/.vaibify`` has already
    happened once in this repository's history.
    """
    from vaibify.gui.appFactory import fappCreateHubApplication
    sHome = str(tmp_path / "home")
    os.makedirs(sHome)
    for objModule, sAttribute, sValue in (
        (registryManager, "_S_REGISTRY_DIRECTORY", sHome),
        (registryManager, "_S_REGISTRY_PATH",
         os.path.join(sHome, "registry.json")),
        (registryManager, "_S_LOCK_PATH",
         os.path.join(sHome, "registry.lock")),
        (containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks")),
        (preferencesStore, "_S_PREFERENCES_DIRECTORY", sHome),
        (preferencesStore, "_S_PREFERENCES_PATH",
         os.path.join(sHome, "preferences.json")),
        (preferencesStore, "_S_LOCK_PATH",
         os.path.join(sHome, "preferences.lock")),
    ):
        monkeypatch.setattr(objModule, sAttribute, sValue)

    sProject = _fsBuildHostProject(sHome)
    registryManager.fnAddProject(sProject, sMode="host")
    app = fappCreateHubApplication(iExpectedPort=0)
    with TestClient(app) as client:
        client.headers.update(
            {"X-Session-Token": fsBootstrapCredential(app)},
        )
        responseClaim = client.post(f"/api/registry/{S_PROJECT}/claim")
        assert responseClaim.status_code == 200, responseClaim.text
        client.headers.update(
            {"X-Vaibify-Lease": responseClaim.json()["sLeaseId"]},
        )
        responseConnect = client.post(
            f"/api/connect/{S_PROJECT}",
            params={"sWorkflowPath": os.path.join(
                sProject, ".vaibify", "projects", "panel.json",
            )},
        )
        assert responseConnect.status_code == 200, responseConnect.text
        yield client, sProject


def _fsAddOriginRepository(sProject, sOriginDirectory):
    """Give the project a remote it can really push to."""
    subprocess.run(
        ["git", "init", "--bare", "-q", sOriginDirectory], check=True,
    )
    _fnRunGit(sProject, "remote", "add", "origin", sOriginDirectory)
    _fnRunGit(
        sProject, "-c", "protocol.file.allow=always",
        "push", "-q", "-u", "origin", "HEAD:refs/heads/main",
    )


@pytest.mark.falsification
def testAHostProjectsGitHubPushReachesTheRemote(
    tHostPanel, tmp_path, monkeypatch,
):
    """The Phase C bar, as far as a test can carry it.

    The remote is a bare repository on disk rather than GitHub, so the
    credential exchange is the one thing this cannot exercise; every
    other link in the chain is production code, and the assertion is
    what the REMOTE holds afterwards.

    The hardening flags are relaxed for the file transport only. They
    refuse ``file://`` deliberately, which is correct and is why the
    substitution is named here rather than worked around: without it
    the push fails for a reason that has nothing to do with the guard
    under test.

    Kills: the push validator measuring against the container volume,
    which refused every host project with 400 "must be within
    workspace root" before any git ran.
    """
    from vaibify.gui import syncDispatcher
    client, sProject = tHostPanel
    _fsAddOriginRepository(sProject, str(tmp_path / "origin.git"))
    with open(
        os.path.join(sProject, "MANIFEST.sha256"), "w",
    ) as fileManifest:
        fileManifest.write("a manifest the researcher wants published\n")
    monkeypatch.setattr(
        syncDispatcher, "LIST_GIT_HARDENING_CONFIG",
        ["-c", "core.symlinks=false", "-c", "protocol.file.allow=always"],
    )
    responsePush = client.post(
        f"/api/github/{S_PROJECT}/push",
        json={
            "listFilePaths": ["MANIFEST.sha256"],
            "sCommitMessage": "publish the manifest",
        },
    )
    assert responsePush.status_code == 200, responsePush.text
    assert responsePush.json()["bSuccess"] is True, responsePush.text
    sRemoteLog = subprocess.run(
        ["git", "log", "--oneline", "--all"],
        cwd=str(tmp_path / "origin.git"),
        capture_output=True, text=True, check=True,
    ).stdout
    assert "publish the manifest" in sRemoteLog, sRemoteLog


@pytest.mark.falsification
def testAHostProjectCommitsItsCanonicalFiles(tHostPanel):
    """Commit-canonical lands a real commit in the researcher's repo.

    The oracle is ``git log`` in the project, not the response body: a
    route can report a commit hash it read back from a repository
    nothing was committed to.

    Kills: the canonical commit reaching for a container that a host
    project does not have -- every git verb in this panel goes through
    the connection the router picks by mode.
    """
    client, sProject = tHostPanel
    with open(
        os.path.join(sProject, "MANIFEST.sha256"), "w",
    ) as fileManifest:
        fileManifest.write("checksums\n")
    responseCommit = client.post(
        f"/api/git/{S_PROJECT}/commit-canonical",
        json={"sCommitMessage": "record the canonical state"},
    )
    assert responseCommit.status_code == 200, responseCommit.text
    assert responseCommit.json()["iFilesCommitted"] >= 1
    assert "record the canonical state" in _fnRunGit(
        sProject, "log", "--oneline", "-1",
    )


@pytest.mark.falsification
def testTheRepositoriesPanelInitializesInsideTheHostProject(tHostPanel):
    """Init makes a repository where the researcher's project is.

    Kills: the panel composing "/workspace/" + name. On the
    researcher's Mac that answered 500 ``mkdir: /workspace:
    Read-only file system`` and quarantined the project on the way
    out; on a Linux box with a writable root it would instead have
    created a real ``/workspace`` directory nobody asked for.
    """
    client, sProject = tHostPanel
    responseInit = client.post(
        f"/api/repos/{S_PROJECT}/init",
        json={"sDirectory": S_TRACKED_REPOSITORY, "bCreateIfMissing": True},
    )
    assert responseInit.status_code == 200, responseInit.text
    assert os.path.isdir(
        os.path.join(sProject, S_TRACKED_REPOSITORY, ".git"),
    ), "no repository was created inside the host project"


@pytest.mark.falsification
def testAPlainFileIsNotOfferedAsARepositoryToBe(tHostPanel):
    """Discovery separates files from directories, not repos from rest.

    ``vaibify.yml`` sits in the root of every host project. It was
    listed under "directories that are not repositories", where the
    panel offers to run ``git init`` in them -- an offer that can only
    fail, on a file the researcher cannot even see is a file from the
    dashboard.

    Kills: dropping the is-a-directory half of discovery's probe.
    """
    client, sProject = tHostPanel
    os.makedirs(os.path.join(sProject, "PlainDirectory"))
    responseStatus = client.get(f"/api/repos/{S_PROJECT}/status")
    assert responseStatus.status_code == 200, responseStatus.text
    listNames = [
        dictEntry["sName"]
        for dictEntry in responseStatus.json()["listNonRepoDirs"]
    ]
    assert "PlainDirectory" in listNames, listNames
    assert "vaibify.yml" not in listNames, (
        "a plain file is being offered as a directory to initialize: "
        f"{listNames}"
    )
