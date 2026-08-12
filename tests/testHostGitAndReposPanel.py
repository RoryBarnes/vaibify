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
    # Pin the branch name rather than inheriting init.defaultBranch.
    # The push below sets an upstream, and a runner whose git still
    # defaults to `master` gave the production `git push` a local
    # branch and an upstream with different names — which git refuses,
    # correctly, and which is a property of the fixture rather than of
    # anything under test. `symbolic-ref` instead of `init -b` because
    # it works on every git version.
    _fnRunGit(sProject, "symbolic-ref", "HEAD", "refs/heads/main")
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


# ── Credentials: which keyring holds this project's tokens ───────

class _PoisonConnection:
    """Raises on any use, proving a lane reached no container."""

    def __getattr__(self, sAttributeName):
        raise AssertionError(
            f"a container was consulted (.{sAttributeName}) for a host "
            "project's credential"
        )


@pytest.fixture
def fixtureHostRegistryOnly(tmp_path, monkeypatch):
    """Register one host and one container project; no hub."""
    sHome = str(tmp_path / "home")
    os.makedirs(sHome)
    for sAttribute, sValue in (
        ("_S_REGISTRY_DIRECTORY", sHome),
        ("_S_REGISTRY_PATH", os.path.join(sHome, "registry.json")),
        ("_S_LOCK_PATH", os.path.join(sHome, "registry.lock")),
    ):
        monkeypatch.setattr(registryManager, sAttribute, sValue)
    for sName, sMode in (
        ("credentialHostProject", "host"),
        ("credentialContainerProject", "container"),
    ):
        sDirectory = os.path.join(sHome, sName)
        os.makedirs(sDirectory)
        with open(
            os.path.join(sDirectory, "vaibify.yml"), "w",
        ) as fileConfig:
            fileConfig.write(f"projectName: {sName}\n")
        registryManager.fnAddProject(sDirectory, sMode=sMode)
    return sHome


@pytest.mark.falsification
def testAHostProjectsTokenGoesToTheResearchersOwnKeyring(
    fixtureHostRegistryOnly,
):
    """No container is consulted, and the token lands on the host.

    The container leg writes the value to a temporary file and runs an
    in-container python that reads it back into ``keyring``. For a host
    project that lane is wrong twice over: there is no container to run
    it in, and the file would be a secret written to the researcher's
    own disk on the way to a keyring this process can reach directly.

    The keyring here is the suite's hermetic fake (conftest), so what
    is asserted is which STORE was chosen, not that a real OS keychain
    accepted it.

    Kills: the dispatcher losing its mode test, which sends every host
    project's token into a container it does not have.
    """
    from vaibify.config.secretManager import fbSecretExists
    from vaibify.gui import syncDispatcher
    syncDispatcher.fnStoreCredentialForProject(
        _PoisonConnection(), "credentialHostProject",
        "zenodo_token_sandbox", "a-real-looking-token",
    )
    assert fbSecretExists("zenodo_token_sandbox", "keyring")


@pytest.mark.falsification
def testAContainerProjectsTokenStillGoesIntoItsContainer(
    fixtureHostRegistryOnly,
):
    """The other direction, and the one with the wider blast radius.

    A container project's keyring is inside the container, because
    that is where the sync commands run. Sending its token to the
    host keyring would leave every containerized push authenticating
    with nothing, and would put a token the researcher scoped to one
    container into their login keychain.

    Kills: the dispatcher stuck on the host branch.
    """
    from unittest.mock import MagicMock
    from vaibify.gui import syncDispatcher
    mockConnection = MagicMock()
    mockConnection.ftResultExecuteCommand.return_value = (0, "")
    syncDispatcher.fnStoreCredentialForProject(
        mockConnection, "credentialContainerProject",
        "zenodo_token_sandbox", "a-real-looking-token",
    )
    assert mockConnection.fnWriteFile.called, (
        "the container lane did not stage the credential file"
    )


def testAHostProjectsCredentialCheckAsksTheHostKeyring(
    fixtureHostRegistryOnly,
):
    """The connectivity probe follows the token it is asking about."""
    from vaibify.config.secretManager import fnStoreSecret
    from vaibify.gui import syncDispatcher
    fnStoreSecret("zenodo_token_sandbox", "tok", "keyring")
    dictProbe = syncDispatcher.fdictCheckConnectivity(
        _PoisonConnection(), "credentialHostProject", "zenodo",
    )
    assert dictProbe["bConnected"] is True


def testAHostProjectsCredentialRoundTripsThroughCopyAndDelete(
    fixtureHostRegistryOnly,
):
    """Snapshot, restore and cleanup, which is what a failed token does.

    The stage-validate-commit flow copies the existing token aside
    before storing a new one and copies it back when validation fails.
    A host project that could store but not snapshot would lose the
    researcher's working token on the first typo.
    """
    from vaibify.config.secretManager import fbSecretExists
    from vaibify.gui import syncDispatcher
    connection = _PoisonConnection()
    syncDispatcher.fnStoreCredentialForProject(
        connection, "credentialHostProject", "zenodo_token_sandbox",
        "original",
    )
    assert syncDispatcher.fbCopyCredentialForProject(
        connection, "credentialHostProject",
        "zenodo_token_sandbox", "zenodo_token_sandbox_backup",
    ) is True
    syncDispatcher.fnDeleteCredentialForProject(
        connection, "credentialHostProject", "zenodo_token_sandbox_backup",
    )
    assert not fbSecretExists("zenodo_token_sandbox_backup", "keyring")
    assert syncDispatcher.fbCopyCredentialForProject(
        connection, "credentialHostProject",
        "zenodo_token_sandbox_backup", "zenodo_token_sandbox",
    ) is False, "copying an absent slot must not report success"


def testAHostProjectsCredentialSlotNameIsStillClosed(
    fixtureHostRegistryOnly,
):
    """The token-name vocabulary guards the host lane too.

    The name reaches a keyring service slot and arrives from a request
    body; the container functions each refuse an unknown one, and a
    host branch that skipped the check would be the looser lane.
    """
    from vaibify.gui import syncDispatcher
    with pytest.raises(ValueError, match="Invalid token name"):
        syncDispatcher.fnStoreCredentialForProject(
            _PoisonConnection(), "credentialHostProject",
            "evil_slot", "value",
        )


# ── The CLI's push and pull, which have no container to cross ────

def _fnRunCliCommand(fnCommand, listArguments):
    """Invoke one click command and return its result."""
    from click.testing import CliRunner
    return CliRunner().invoke(fnCommand, listArguments)


@pytest.mark.falsification
def testTheCliCopiesWithinAHostProjectInsteadOfCallingDockerCp(
    fixtureHostRegistryOnly, tmp_path, monkeypatch,
):
    """``vaibify push`` on a host project is a copy, not a docker cp.

    ``docker cp`` is wrong twice here: there is no container on the
    other side, and the files were never anywhere else. The researcher
    would have got a Docker error naming a container they never made.

    Kills: the host branch dropped, so the command reaches for
    ``docker cp`` again -- the stub below fails the test by recording
    that it was called.
    """
    from vaibify.cli import main as cliMain
    from vaibify.docker import fileTransfer
    listDockerCalls = []
    monkeypatch.setattr(
        fileTransfer, "fnPushToContainer",
        lambda *tArguments: listDockerCalls.append(tArguments),
    )
    sSource = str(tmp_path / "incoming.csv")
    with open(sSource, "w") as fileSource:
        fileSource.write("a,b\n1,2\n")
    sProjectDirectory = os.path.join(
        fixtureHostRegistryOnly, "credentialHostProject",
    )
    monkeypatch.setattr(
        cliMain, "fconfigResolveProject",
        lambda sName: type("Config", (), {
            "sProjectName": "credentialHostProject",
        })(),
    )
    os.makedirs(os.path.join(sProjectDirectory, "Step01"))
    tResult = _fnRunCliCommand(
        cliMain.fnPushCommand, [sSource, "Step01/incoming.csv"],
    )
    assert tResult.exit_code == 0, tResult.output
    assert listDockerCalls == [], (
        "the host project reached for docker cp: " f"{listDockerCalls}"
    )
    assert os.path.isfile(
        os.path.join(sProjectDirectory, "Step01", "incoming.csv"),
    ), "the file never arrived in the project's own directory"


@pytest.mark.falsification
def testTheCliStillUsesDockerCpForAContainerProject(
    fixtureHostRegistryOnly, tmp_path, monkeypatch,
):
    """The other direction: a container project still crosses the wall.

    Kills: the branch stuck on the host answer, which would make
    ``vaibify push`` copy a file next to itself on the host and report
    success, while the container never received it.
    """
    from vaibify.cli import main as cliMain
    from vaibify.docker import fileTransfer
    listDockerCalls = []
    monkeypatch.setattr(
        fileTransfer, "fnPushToContainer",
        lambda *tArguments: listDockerCalls.append(tArguments),
    )
    sSource = str(tmp_path / "incoming.csv")
    with open(sSource, "w") as fileSource:
        fileSource.write("a,b\n1,2\n")
    monkeypatch.setattr(
        cliMain, "fconfigResolveProject",
        lambda sName: type("Config", (), {
            "sProjectName": "credentialContainerProject",
        })(),
    )
    tResult = _fnRunCliCommand(
        cliMain.fnPushCommand, [sSource, "/workspace/Step01/incoming.csv"],
    )
    assert tResult.exit_code == 0, tResult.output
    assert len(listDockerCalls) == 1, tResult.output


def testTheCliRefusesToCopyAHostFileOntoItself(
    fixtureHostRegistryOnly, monkeypatch,
):
    """Copying a file onto itself truncates it; say so and stop."""
    from vaibify.cli import main as cliMain
    sProjectDirectory = os.path.join(
        fixtureHostRegistryOnly, "credentialHostProject",
    )
    sPath = os.path.join(sProjectDirectory, "results.json")
    with open(sPath, "w") as fileResults:
        fileResults.write('{"kept": true}')
    monkeypatch.setattr(
        cliMain, "fconfigResolveProject",
        lambda sName: type("Config", (), {
            "sProjectName": "credentialHostProject",
        })(),
    )
    tResult = _fnRunCliCommand(
        cliMain.fnPullCommand, ["results.json", sPath],
    )
    assert tResult.exit_code == 0, tResult.output
    assert "already where you asked" in tResult.output
    with open(sPath) as fileResults:
        assert fileResults.read() == '{"kept": true}'


def testTheCliRefusesAHostDestinationWhoseParentIsMissing(
    fixtureHostRegistryOnly, tmp_path, monkeypatch,
):
    """A missing directory is named, not built and not tracebacked.

    ``docker cp`` does not create the tree either, so the two lanes
    agree; what differs is that the researcher gets a sentence instead
    of a FileNotFoundError from inside shutil.
    """
    from vaibify.cli import main as cliMain
    sSource = str(tmp_path / "incoming.csv")
    with open(sSource, "w") as fileSource:
        fileSource.write("a\n")
    monkeypatch.setattr(
        cliMain, "fconfigResolveProject",
        lambda sName: type("Config", (), {
            "sProjectName": "credentialHostProject",
        })(),
    )
    tResult = _fnRunCliCommand(
        cliMain.fnPushCommand, [sSource, "NoSuchStep/incoming.csv"],
    )
    assert tResult.exit_code != 0
    assert "does not exist" in tResult.output, tResult.output


# ── The marker cycle: a test result that survives the dashboard ──

_S_STEP_SCRIPT = """import json
with open("numbers.json", "w") as fileOutput:
    json.dump({"listValues": [1, 2, 3]}, fileOutput)
"""

_S_TEST_SOURCE = """import json
import os


def testNumbersAreWhatTheStepWrote():
    sHere = os.path.dirname(os.path.abspath(__file__))
    sRoot = os.path.dirname(sHere)
    with open(os.path.join(sRoot, "numbers.json")) as fileInput:
        assert json.load(fileInput)["listValues"] == [1, 2, 3]
"""


def _fbPytestIsInvokableAsTheDashboardInvokesIt():
    """Return True when ``python -m pytest`` runs on this machine.

    The dashboard runs a step's tests with ``python -m pytest``, and
    on a host project that is the researcher's own interpreter. This
    is an environment fact, not a lane that may skip itself green:
    when it is false the machine genuinely cannot run the thing under
    test, and the reason is printed.
    """
    return subprocess.run(
        ["python", "-m", "pytest", "--version"],
        capture_output=True,
    ).returncode == 0


def testAHostProjectsTestRunLeavesAMarkerTheDashboardCanRead(
    tHostPanel,
):
    """The verification cycle, on the researcher's own machine.

    The conftest vaibify installs into the step's tests directory is
    what writes the marker, and the marker is what the dashboard reads
    to colour the badge. Every path in that chain is a host path here:
    the conftest is written into their repository, pytest runs on
    their interpreter, and the marker lands under their
    ``.vaibify/test_markers/``.

    The oracle is the FILE, not the response: a route can report a
    pass from the exit code alone, and the badge would still be grey
    on the next poll because nothing was recorded.
    """
    if not _fbPytestIsInvokableAsTheDashboardInvokesIt():
        pytest.skip(
            "`python -m pytest` does not run on this machine, which is "
            "exactly what the dashboard invokes for a host project"
        )
    client, sProject = tHostPanel
    sStepDirectory = os.path.join(sProject, "QuickCheck")
    os.makedirs(os.path.join(sStepDirectory, "tests"))
    with open(
        os.path.join(sStepDirectory, "makeNumbers.py"), "w",
    ) as fileScript:
        fileScript.write(_S_STEP_SCRIPT)
    with open(
        os.path.join(sStepDirectory, "tests", "testNumbers.py"), "w",
    ) as fileTest:
        fileTest.write(_S_TEST_SOURCE)
    with open(
        os.path.join(sStepDirectory, "numbers.json"), "w",
    ) as fileOutput:
        fileOutput.write('{"listValues": [1, 2, 3]}')
    _fnAddStepToTheOpenWorkflow(client, sProject)

    responseTests = client.post(f"/api/steps/{S_PROJECT}/0/run-tests")
    assert responseTests.status_code == 200, responseTests.text
    assert responseTests.json()["bPassed"] is True, responseTests.text
    listMarkers = _flistMarkerFiles(sProject)
    assert listMarkers, (
        "the test run wrote no marker; the dashboard has nothing to "
        "colour the badge from, so a passing step reads as untested"
    )
    with open(listMarkers[0]) as fileMarker:
        dictMarker = json.load(fileMarker)
    dictCategories = dictMarker.get("dictCategories") or {}
    assert dictCategories, dictMarker
    assert all(
        dictCategory["iFailed"] == 0 and dictCategory["iPassed"] > 0
        for dictCategory in dictCategories.values()
    ), dictMarker
    # The hashes are the half that proves this ran on the host: the
    # conftest opened the step's real output file, on this filesystem,
    # and recorded what it found.
    assert dictMarker["dictOutputHashes"], dictMarker


def _flistMarkerFiles(sProject):
    """Return every marker file under the project's test_markers tree."""
    sMarkerRoot = os.path.join(sProject, ".vaibify", "test_markers")
    if not os.path.isdir(sMarkerRoot):
        return []
    return [
        os.path.join(sRoot, sFile)
        for sRoot, _listDirectories, listFiles in os.walk(sMarkerRoot)
        for sFile in listFiles if sFile.endswith(".json")
    ]


def _fnAddStepToTheOpenWorkflow(client, sProject):
    """Give the open workflow one step with a qualitative test.

    Written to the project document and re-opened rather than posted
    through the step editor: what this test is about is the marker the
    RUN writes, and building the workflow through five route calls
    would put four other features in front of it.
    """
    sWorkflowPath = os.path.join(
        sProject, ".vaibify", "projects", "panel.json",
    )
    with open(sWorkflowPath) as fileWorkflow:
        dictWorkflow = json.load(fileWorkflow)
    dictWorkflow["listSteps"] = [{
        "sName": "QuickCheck",
        "sStepId": "quick-check",
        "sDirectory": "QuickCheck",
        "bRunEnabled": True,
        "bPlotOnly": False,
        "saDataCommands": ["python makeNumbers.py"],
        "saOutputDataFiles": ["numbers.json"],
        "saPlotCommands": [],
        "saPlotFiles": [],
        "dictTests": {
            "dictQualitative": {
                "saCommands": ["python -m pytest tests/testNumbers.py -q"],
                "sFilePath": "QuickCheck/tests/testNumbers.py",
            },
            "dictQuantitative": {"saCommands": [], "sFilePath": ""},
        },
    }]
    with open(sWorkflowPath, "w") as fileWorkflow:
        json.dump(dictWorkflow, fileWorkflow)
    responseConnect = client.post(
        f"/api/connect/{S_PROJECT}",
        params={"sWorkflowPath": sWorkflowPath},
    )
    assert responseConnect.status_code == 200, responseConnect.text


# ── Which interpreter runs a vaibify-authored helper program ─────

@pytest.mark.falsification
def testAHostProjectsHelperProgramRunsOnVaibifysInterpreter(
    fixtureHostRegistryOnly,
):
    """The interpreter with the dependencies, not the one on PATH.

    Every program vaibify composes here imports something vaibify
    depends on — numpy through the loaders, ``keyring``, ``requests``,
    ``vaibify`` itself. Inside the container ``python3`` is the image's
    and has them all. On the host it is whatever the researcher's PATH
    resolves, and on a machine with a science stack in a virtual
    environment that is routinely the system interpreter with none of
    them: the first host project ever to generate a test failed with
    ``ModuleNotFoundError: No module named 'numpy'``, from an
    interpreter the researcher uses for nothing.

    Kills: the resolver answering ``python3`` for a host project,
    which is the literal every one of these composers shipped with.
    """
    import sys
    from vaibify.gui.helperPrograms import fsResolveHelperInterpreter
    from vaibify.gui.syncDispatcher import fsPythonCommand
    sInterpreter = fsResolveHelperInterpreter("credentialHostProject")
    assert sys.executable in sInterpreter
    assert fsPythonCommand(
        "import keyring", "print(1)", "credentialHostProject",
    ).startswith(sInterpreter + " -c ")


@pytest.mark.falsification
def testAContainerProjectsHelperProgramStillRunsOnPython3(
    fixtureHostRegistryOnly,
):
    """The other direction, and it cannot be anything else.

    ``sys.executable`` is a path on the HOST. Inside a container it
    names nothing, so a container handed it would fail to start every
    helper program vaibify has — the keyring probes, the archive
    build, the introspection pass.

    Kills: the resolver stuck on the host answer.
    """
    from vaibify.gui.helperPrograms import fsResolveHelperInterpreter
    assert fsResolveHelperInterpreter(
        "credentialContainerProject",
    ) == "python3"


def testTheStepRunnerNeverAsksWhichInterpreterToUse():
    """A step's interpreter is the researcher's choice and stays theirs.

    The line is authorship: vaibify's programs run on vaibify's
    interpreter, the researcher's run on theirs. ``python3
    makeNumbers.py`` names the environment they built for their
    science, and a step command rewritten to vaibify's interpreter
    would run their analysis under vaibify's dependencies instead of
    their own — silently producing different numbers.

    Asserted structurally, over the source, because the failure is a
    call that should not exist rather than an output that can be
    compared.
    """
    import pathlib
    for sModuleName in (
        "pipelineRunner.py", "workflowManager.py", "pipelineUtils.py",
    ):
        pathModule = (
            pathlib.Path(__file__).resolve().parents[1]
            / "vaibify" / "gui" / sModuleName
        )
        assert "fsResolveHelperInterpreter" not in pathModule.read_text(), (
            f"{sModuleName} resolves an interpreter; the step command "
            "is the researcher's text and must be composed from it "
            "unchanged"
        )


@pytest.mark.falsification
def testAHostProjectsPushNeverAsksDockerAboutIsolation(
    fixtureHostRegistryOnly, monkeypatch,
):
    """No container exists, so nothing can have sealed one.

    The isolation gate exists so a researcher whose container runs
    with ``--network none`` gets an actionable refusal instead of a
    thirty-second DNS timeout. For a host project it is a ``docker
    inspect`` subprocess about a name Docker never heard of, started
    on the researcher's own machine, outside the gated primitive every
    host subprocess is meant to go through — and it costs up to its
    five-second timeout on every push.

    Kills: dropping the mode branch, so the probe runs again.
    """
    from vaibify.docker import containerManager
    from vaibify.gui.routes.syncRoutes import _fnRequireNetworkAccess
    listProbes = []
    monkeypatch.setattr(
        containerManager, "fbContainerIsNetworkIsolated",
        lambda sIdentifier: listProbes.append(sIdentifier) or False,
    )
    _fnRequireNetworkAccess("credentialHostProject")
    assert listProbes == [], (
        f"Docker was asked about a host project: {listProbes}"
    )


@pytest.mark.falsification
def testAContainerProjectsPushStillChecksIsolation(
    fixtureHostRegistryOnly, monkeypatch,
):
    """The other direction: a sealed container must still refuse.

    Kills: the branch stuck on the host answer, which would send every
    containerized Overleaf and Zenodo push into a thirty-second DNS
    timeout instead of the refusal that names the cause.
    """
    from fastapi import HTTPException
    from vaibify.docker import containerManager
    from vaibify.gui.routes.syncRoutes import _fnRequireNetworkAccess
    monkeypatch.setattr(
        containerManager, "fbContainerIsNetworkIsolated",
        lambda sIdentifier: True,
    )
    with pytest.raises(HTTPException) as excInfo:
        _fnRequireNetworkAccess("credentialContainerProject")
    assert excInfo.value.status_code == 409
