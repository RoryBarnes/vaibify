"""The root a project's files live under, per resource and per mode.

Until host mode there was exactly one answer — ``/workspace``, the
volume mounted into every container — so the question was never asked
and the answer was written as a constant in each place that needed it.
A host project has no volume: its files are in the directory the
researcher registered. Two of those constants are load-bearing, and
they fail in opposite ways:

- **Discovery** searches the root. Pointed at ``/workspace`` on a host
  machine it finds nothing and reports a project with no Projects in
  it — a wrong answer that looks like an empty project, not an error.
- **The connect path guard** measures the supplied workflow path
  against the root. Pointed at ``/workspace`` for a host project it
  refuses every legitimate path with 403 "path traversal".

BOTH DIRECTIONS, per the standing rule for every mode-aware behavior:
a resolver stuck on the container root breaks host mode, and one stuck
on the registry directory breaks every containerized project — and the
two are indistinguishable in a report that only exercises one mode.
"""

import os

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from vaibify.config import registryManager
from vaibify.gui import projectRoots


S_HOST_PROJECT = "rooted-host-project"
S_CONTAINER_PROJECT = "rooted-container-project"
S_CONTAINER_ROOT = "/workspace"


@pytest.fixture(autouse=True)
def fixtureIsolateRegistry(tmp_path, monkeypatch):
    """Redirect the registry to a temp directory for every test."""
    sRegistryDirectory = str(tmp_path / ".vaibify")
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_DIRECTORY", sRegistryDirectory,
    )
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH",
        os.path.join(sRegistryDirectory, "registry.json"),
    )
    monkeypatch.setattr(
        registryManager, "_S_LOCK_PATH",
        os.path.join(sRegistryDirectory, "registry.lock"),
    )


def _fsRegisterProject(tmp_path, sProjectName, sMode):
    """Create a project directory and register it in the given mode."""
    sProjectDirectory = str(tmp_path / sProjectName)
    os.makedirs(sProjectDirectory, exist_ok=True)
    with open(
        os.path.join(sProjectDirectory, "vaibify.yml"), "w",
    ) as fileConfig:
        fileConfig.write(f"projectName: {sProjectName}\n")
    registryManager.fnAddProject(sProjectDirectory, sMode=sMode)
    return sProjectDirectory


# ── The resolver itself ──────────────────────────────────────────

@pytest.mark.falsification
def testAHostProjectResolvesToItsRegisteredDirectory(tmp_path):
    """The host answer is the directory, never the container volume.

    The oracle is the registration: the researcher named this
    directory as the project, so it is where the project's files are.
    Nothing about ``/workspace`` is true on their machine.

    Kills: a resolver that answers the container root for every
    resource, which is what the code did before this module existed.
    """
    sDirectory = _fsRegisterProject(tmp_path, S_HOST_PROJECT, "host")
    assert projectRoots.fsResolveProjectRoot(
        S_HOST_PROJECT, S_CONTAINER_ROOT,
    ) == sDirectory


@pytest.mark.falsification
def testAContainerProjectResolvesToTheContainerRoot(tmp_path):
    """The other direction: a registered container project is untouched.

    A container project also records a host ``sDirectory`` — the
    folder holding its ``vaibify.yml``, which is NOT where its files
    live at run time; they live in the volume. Answering the directory
    for it would point every path guard and every discovery search at
    the researcher's config folder.

    Kills: dropping the mode test, so the registry directory is
    answered for any registered project.
    """
    _fsRegisterProject(tmp_path, S_CONTAINER_PROJECT, "container")
    assert projectRoots.fsResolveProjectRoot(
        S_CONTAINER_PROJECT, S_CONTAINER_ROOT,
    ) == S_CONTAINER_ROOT


def testAnUnregisteredResourceResolvesToTheContainerRoot():
    """A viewer connects straight to a container id nothing registered."""
    assert projectRoots.fsResolveProjectRoot(
        "0123456789ab", S_CONTAINER_ROOT,
    ) == S_CONTAINER_ROOT


def testTheCallerSuppliedContainerRootIsHonoured():
    """The container answer is the caller's, not a second constant here."""
    assert projectRoots.fsResolveProjectRoot(
        "0123456789ab", "/srv/elsewhere",
    ) == "/srv/elsewhere"


def testAHostEntryWithNoDirectoryRefusesToResolve(tmp_path):
    """A corrupt host entry raises rather than falling back.

    Answering ``/workspace`` for it would send discovery to a
    directory that does not exist on the host and report the project
    as empty — the silent-fallback shape this repository has already
    shipped once as all-grey badges.
    """
    _fsRegisterProject(tmp_path, S_HOST_PROJECT, "host")
    dictRegistry = registryManager.fdictLoadRegistry()
    dictRegistry["listProjects"][0]["sDirectory"] = ""
    registryManager.fnSaveRegistry(dictRegistry)
    with pytest.raises(ValueError) as excInfo:
        projectRoots.fsResolveProjectRoot(
            S_HOST_PROJECT, S_CONTAINER_ROOT,
        )
    assert S_HOST_PROJECT in str(excInfo.value)


# ── Discovery: the route asks, and passes the answer on ──────────

class RecordingConnection:
    """A connection that records what it was asked to reach for.

    Deliberately not a permissive mock: every method it answers is one
    these routes are expected to call, and it records the paths so a
    test can assert WHICH path passed the guard rather than only that
    the request did not 403.
    """

    def __init__(self):
        self.listCommands = []
        self.listProbedPaths = []
        self.listFetchedPaths = []

    def ftResultExecuteCommand(self, sResourceId, sCommand, **kwargs):
        del sResourceId, kwargs
        self.listCommands.append(sCommand)
        return 0, ""

    def flistContainerPathsExist(self, sResourceId, listPaths):
        del sResourceId
        self.listProbedPaths.extend(listPaths)
        return [True] * len(listPaths)

    def fbaFetchFile(self, sResourceId, sPath, iMaxBytes=None):
        del sResourceId, iMaxBytes
        self.listFetchedPaths.append(sPath)
        return b"%PDF-1.4 figure bytes"

    def flistDirectoryEntries(self, sResourceId, sPath):
        del sResourceId
        self.listProbedPaths.append(sPath)
        return []


def _ftBuildDiscoveryClient():
    """Return ``(client, connection)`` serving the workflow routes."""
    from vaibify.gui.routes.workflowRoutes import fnRegisterAll
    connection = RecordingConnection()
    app = FastAPI()
    app.state.dictContainerOwners = {}
    dictCtx = {
        "require": lambda *aArgs: None,
        "docker": connection,
        "workflows": {},
        "paths": {},
    }
    fnRegisterAll(app, dictCtx)
    return TestClient(app), connection


@pytest.mark.falsification
def testWorkflowDiscoverySearchesTheHostProjectDirectory(tmp_path):
    """The find runs under the registered directory, not the volume.

    Kills: the route reverting to ``flistFindWorkflowsInContainer``'s
    module default, which searches ``/workspace`` — a path that does
    not exist on the researcher's machine, so the project renders
    with no Projects in it and no error anywhere.
    """
    sDirectory = _fsRegisterProject(tmp_path, S_HOST_PROJECT, "host")
    client, connection = _ftBuildDiscoveryClient()
    response = client.get(f"/api/workflows/{S_HOST_PROJECT}")
    assert response.status_code == 200
    assert connection.listCommands, "discovery ran no command at all"
    sFindCommand = connection.listCommands[0]
    assert sDirectory in sFindCommand, (
        f"discovery did not search the project directory: {sFindCommand}"
    )
    assert S_CONTAINER_ROOT not in sFindCommand, (
        "discovery searched the container volume for a project that "
        f"has no container: {sFindCommand}"
    )


@pytest.mark.falsification
def testWorkflowDiscoveryStillSearchesTheVolumeForAContainer(tmp_path):
    """The other direction: container discovery is unchanged.

    Kills: resolving every project to its registered directory, which
    would search the researcher's config folder on the HOST from
    inside the container — where it does not exist.
    """
    _fsRegisterProject(tmp_path, S_CONTAINER_PROJECT, "container")
    client, connection = _ftBuildDiscoveryClient()
    response = client.get(f"/api/workflows/{S_CONTAINER_PROJECT}")
    assert response.status_code == 200
    assert connection.listCommands
    assert S_CONTAINER_ROOT in connection.listCommands[0], (
        "container discovery stopped searching the workspace volume: "
        f"{connection.listCommands[0]}"
    )


def testADirectoryWithShellCharactersIsQuotedIntoTheFind():
    """A search root is user-chosen text entering ``bash -c``.

    It was a module constant until host mode, so it was never quoted.
    A host root carries whatever the researcher's filesystem allows: a
    space alone truncates the search silently, and a semicolon ends
    the ``find`` and starts a command of the caller's choosing.
    """
    from vaibify.gui import workflowManager
    connection = RecordingConnection()
    workflowManager.flistFindWorkflowsInContainer(
        connection, "resource-id", "/tmp/my project; touch pwned",
    )
    assert connection.listCommands
    sFindCommand = connection.listCommands[0]
    assert "; touch pwned" not in sFindCommand.split("'")[0], (
        f"the search root entered the command unquoted: {sFindCommand}"
    )
    assert "'/tmp/my project; touch pwned'" in sFindCommand, (
        f"the search root was not quoted as one word: {sFindCommand}"
    )


# ── Connect: the path guard measures against the same root ───────

@pytest.mark.falsification
def testAHostWorkflowPathIsMeasuredAgainstItsProjectDirectory(tmp_path):
    """A path inside the registered directory survives the guard.

    Kills: validating the connect path against the container volume,
    which answers 403 "path traversal" for every legitimate host
    workflow — a refusal that reads as an attack, not as a bug.
    """
    from vaibify.gui.pipelineServer import _fsValidateConnectWorkflowPath
    sDirectory = _fsRegisterProject(tmp_path, S_HOST_PROJECT, "host")
    sWorkflowPath = os.path.join(
        sDirectory, "repo", ".vaibify", "projects", "study.json",
    )
    sRoot = projectRoots.fsResolveProjectRoot(
        S_HOST_PROJECT, S_CONTAINER_ROOT,
    )
    assert _fsValidateConnectWorkflowPath(
        sWorkflowPath, sRoot,
    ) == sWorkflowPath


def testAContainerPathIsRefusedForAHostProject(tmp_path):
    """The guard still refuses: it moved, it did not open."""
    from fastapi import HTTPException
    from vaibify.gui.pipelineServer import _fsValidateConnectWorkflowPath
    _fsRegisterProject(tmp_path, S_HOST_PROJECT, "host")
    sRoot = projectRoots.fsResolveProjectRoot(
        S_HOST_PROJECT, S_CONTAINER_ROOT,
    )
    with pytest.raises(HTTPException) as excInfo:
        _fsValidateConnectWorkflowPath(
            "/workspace/repo/.vaibify/projects/study.json", sRoot,
        )
    assert excInfo.value.status_code == 403


def testEscapingTheHostProjectDirectoryIsStillRefused(tmp_path):
    """Traversal out of the host root is refused like any other."""
    from fastapi import HTTPException
    from vaibify.gui.pipelineServer import _fsValidateConnectWorkflowPath
    sDirectory = _fsRegisterProject(tmp_path, S_HOST_PROJECT, "host")
    sRoot = projectRoots.fsResolveProjectRoot(
        S_HOST_PROJECT, S_CONTAINER_ROOT,
    )
    with pytest.raises(HTTPException) as excInfo:
        _fsValidateConnectWorkflowPath(
            os.path.join(
                sDirectory, "..", "elsewhere", ".vaibify",
                "projects", "study.json",
            ),
            sRoot,
        )
    assert excInfo.value.status_code == 403


def testASiblingDirectorySharingThePrefixIsRefused(tmp_path):
    """``projectXY`` must not pass as inside ``projectX``."""
    from fastapi import HTTPException
    from vaibify.gui.pipelineServer import _fsValidateConnectWorkflowPath
    sDirectory = _fsRegisterProject(tmp_path, S_HOST_PROJECT, "host")
    sRoot = projectRoots.fsResolveProjectRoot(
        S_HOST_PROJECT, S_CONTAINER_ROOT,
    )
    with pytest.raises(HTTPException) as excInfo:
        _fsValidateConnectWorkflowPath(
            sDirectory + "Sibling/.vaibify/projects/study.json", sRoot,
        )
    assert excInfo.value.status_code == 403


# ── The file and figure lanes measure against the same root ──────

def _ftBuildFileClient(dictWorkflows=None):
    """Return ``(client, connection)`` serving the file routes."""
    from vaibify.gui.routes.fileRoutes import fnRegisterAll
    connection = RecordingConnection()
    app = FastAPI()
    app.state.dictContainerOwners = {}
    dictCtx = {
        "require": lambda *aArgs: None,
        "docker": connection,
        "workflows": dictWorkflows or {},
        "paths": {},
        "workflowDir": lambda sResourceId: "/unused",
    }
    fnRegisterAll(app, dictCtx, S_CONTAINER_ROOT)
    return TestClient(app), connection


@pytest.mark.falsification
def testTheExistenceProbeAcceptsPathsInsideTheHostProject(tmp_path):
    """Every file badge in the dashboard runs through this guard.

    The batch is what the file panel asks on every poll, so a root
    that refuses host paths does not fail once and loudly — it paints
    every badge in the project with a 403.

    Kills: the batch measuring against the app-wide workspace
    constant, which no host path can ever be inside.
    """
    sDirectory = _fsRegisterProject(tmp_path, S_HOST_PROJECT, "host")
    sFilePath = os.path.join(sDirectory, "repo", "Step", "output.dat")
    client, connection = _ftBuildFileClient()
    response = client.post(
        f"/api/files/{S_HOST_PROJECT}/exist",
        json={"saRelativePaths": [sFilePath]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["dictExists"] == {sFilePath: True}
    assert connection.listProbedPaths == [sFilePath]


@pytest.mark.falsification
def testTheExistenceProbeStillJailsAContainerToTheVolume(tmp_path):
    """The other direction: a container project keeps the volume jail.

    Kills: reading ``sDirectory`` straight out of the registry
    without consulting the mode — the plausible shortcut — which
    would jail every containerized project inside the researcher's
    HOST config folder, a path the container cannot see.
    """
    _fsRegisterProject(tmp_path, S_CONTAINER_PROJECT, "container")
    client, connection = _ftBuildFileClient()
    response = client.post(
        f"/api/files/{S_CONTAINER_PROJECT}/exist",
        json={"saRelativePaths": ["/workspace/repo/Step/output.dat"]},
    )
    assert response.status_code == 200, response.text
    assert connection.listProbedPaths == [
        "/workspace/repo/Step/output.dat",
    ]


def testTheExistenceProbeRefusesAContainerPathForAHostProject(tmp_path):
    """The guard moved; it did not open."""
    _fsRegisterProject(tmp_path, S_HOST_PROJECT, "host")
    client, _ = _ftBuildFileClient()
    response = client.post(
        f"/api/files/{S_HOST_PROJECT}/exist",
        json={"saRelativePaths": ["/workspace/repo/Step/output.dat"]},
    )
    assert response.status_code == 403


def testTheExistenceProbeRefusesAnEscapeFromTheHostProject(tmp_path):
    """Traversal out of a host root is refused like any other."""
    sDirectory = _fsRegisterProject(tmp_path, S_HOST_PROJECT, "host")
    client, _ = _ftBuildFileClient()
    response = client.post(
        f"/api/files/{S_HOST_PROJECT}/exist",
        json={"saRelativePaths": [
            os.path.join(sDirectory, "..", "secrets", "keys.txt"),
        ]},
    )
    assert response.status_code == 403


def testDirectoryListingIsJailedToTheHostProjectRoot(tmp_path):
    """Browsing a host project lists inside it, and nowhere else."""
    sDirectory = _fsRegisterProject(tmp_path, S_HOST_PROJECT, "host")
    client, connection = _ftBuildFileClient()
    response = client.get(
        f"/api/files/{S_HOST_PROJECT}{sDirectory}/repo",
    )
    assert response.status_code == 200, response.text
    assert f"{sDirectory}/repo" in connection.listCommands[0]
    responseEscape = client.get(f"/api/files/{S_HOST_PROJECT}/etc")
    assert responseEscape.status_code == 403


def _ftBuildFigureClient(sWorkflowDirectory):
    """Return ``(client, connection)`` serving the figure routes."""
    from vaibify.gui.routes.figureRoutes import fnRegisterAll
    connection = RecordingConnection()
    app = FastAPI()
    app.state.dictContainerOwners = {}
    dictCtx = {
        "require": lambda *aArgs: None,
        "docker": connection,
        "workflows": {},
        "paths": {},
        "workflowDir": lambda sResourceId: sWorkflowDirectory,
    }
    fnRegisterAll(app, dictCtx)
    return TestClient(app), connection


@pytest.mark.falsification
def testAHostProjectsFigureIsServedFromItsOwnDirectory(tmp_path):
    """A produced figure is the output half of the walkthrough.

    Kills: the figure lane taking the container volume back as its
    jail, which answers 403 for every figure a host pipeline
    produces — the researcher sees a broken image and a traversal
    error about their own file.
    """
    sDirectory = _fsRegisterProject(tmp_path, S_HOST_PROJECT, "host")
    sWorkflowDirectory = os.path.join(sDirectory, "repo")
    client, connection = _ftBuildFigureClient(sWorkflowDirectory)
    response = client.get(
        f"/api/figure/{S_HOST_PROJECT}/Plot/figure.pdf",
    )
    assert response.status_code == 200, response.text
    assert connection.listFetchedPaths == [
        os.path.join(sWorkflowDirectory, "Plot", "figure.pdf"),
    ]


@pytest.mark.falsification
def testAContainerFigureIsStillJailedToTheVolume(tmp_path):
    """The other direction: container figure serving is unchanged.

    Kills: reading the registry directory without consulting the
    mode, which jails a containerized project's figures inside the
    researcher's host config folder and 403s every one of them.
    """
    _fsRegisterProject(tmp_path, S_CONTAINER_PROJECT, "container")
    client, connection = _ftBuildFigureClient("/workspace/repo")
    response = client.get(
        f"/api/figure/{S_CONTAINER_PROJECT}/Plot/figure.pdf",
    )
    assert response.status_code == 200, response.text
    assert connection.listFetchedPaths == [
        "/workspace/repo/Plot/figure.pdf",
    ]


def testTheContainerEscapeHatchDoesNotOpenAHostProject(tmp_path):
    """``fsResolveFigurePath`` promotes a ``workspace/``-prefixed name.

    That prefix is a container convenience: it turns a relative name
    into ``/workspace/...`` rather than joining it onto the workflow
    directory. For a host project that lands outside the root, and the
    guard must say so rather than reaching for a path on the
    researcher's machine that has nothing to do with their project.
    """
    sDirectory = _fsRegisterProject(tmp_path, S_HOST_PROJECT, "host")
    client, connection = _ftBuildFigureClient(
        os.path.join(sDirectory, "repo"),
    )
    response = client.get(
        f"/api/figure/{S_HOST_PROJECT}/workspace/Plot/figure.pdf",
    )
    assert response.status_code == 403
    assert connection.listFetchedPaths == []


def testTheTestWriteFallbackRootFollowsTheMode(tmp_path):
    """A workflow with no detected repo falls back to its own root.

    Regression cover rather than a registered falsification: the
    mutation that breaks it is the resolver pair already registered
    above. What this pins is that the save-and-run-test path threads
    the resolved root instead of the module constant.
    """
    from fastapi import HTTPException
    from vaibify.gui.routes.testRoutes import _fsResolveTestFilePath
    sDirectory = _fsRegisterProject(tmp_path, S_HOST_PROJECT, "host")
    sRoot = projectRoots.fsResolveProjectRoot(
        S_HOST_PROJECT, S_CONTAINER_ROOT,
    )
    assert _fsResolveTestFilePath(
        "Step/testStep.py", "", sRoot,
    ) == os.path.join(sDirectory, "Step", "testStep.py")
    with pytest.raises(HTTPException) as excInfo:
        _fsResolveTestFilePath("/workspace/Step/testStep.py", "", sRoot)
    assert excInfo.value.status_code == 403
