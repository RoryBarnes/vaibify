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
    """A connection that records commands and finds no candidates."""

    def __init__(self):
        self.listCommands = []

    def ftResultExecuteCommand(self, sResourceId, sCommand, **kwargs):
        del sResourceId, kwargs
        self.listCommands.append(sCommand)
        return 0, ""


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
