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

    def flistContainerDirectoriesExist(self, sResourceId, listPaths):
        del sResourceId
        self.listProbedPaths.extend(listPaths)
        return [False] * len(listPaths)


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


@pytest.mark.falsification
def testALegacyRootProjectFileSurvivesTheConnectGuard(tmp_path):
    """The shape discovery lists is the shape connect admits.

    Discovery began offering the legacy repo-root ``project.json``
    (2026-08-20); the connect guard then bounced the very card the
    researcher had just been shown, with a 400 naming a directory the
    file is not in. Any OTHER root-level .json stays refused — the
    legacy admission is one file name, not an open door.

    Kills: dropping the ``fbWorkflowPathIsLegacyRootFile`` clause from
    ``_fsValidateConnectWorkflowPath``, which restores the bounce.
    """
    from fastapi import HTTPException
    from vaibify.gui.pipelineServer import _fsValidateConnectWorkflowPath
    sDirectory = _fsRegisterProject(tmp_path, S_HOST_PROJECT, "host")
    sRoot = projectRoots.fsResolveProjectRoot(
        S_HOST_PROJECT, S_CONTAINER_ROOT,
    )
    sLegacyPath = os.path.join(sDirectory, "project.json")
    assert _fsValidateConnectWorkflowPath(
        sLegacyPath, sRoot,
    ) == sLegacyPath
    with pytest.raises(HTTPException) as excInfo:
        _fsValidateConnectWorkflowPath(
            os.path.join(sDirectory, "arbitrary.json"), sRoot,
        )
    assert excInfo.value.status_code == 400


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
    # The listing composes no command now, so the path that passed the
    # guard is read off the typed read rather than off shell text.
    assert connection.listProbedPaths[0] == f"{sDirectory}/repo"
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


@pytest.mark.falsification
def testAHostProjectNeverReachesOutToTheContainerRoot(tmp_path):
    """Superseding "the ``workspace/`` prefix is refused" (2026-08-12).

    The resolver used to promote exactly one prefix — ``workspace/``,
    the container root spelled without its leading slash — because
    that is how the dashboard sends back an absolute path. Refusing it
    for a host project was right about the destination and wrong about
    the rule: the promotion now happens against whichever root the
    project actually has, so ``workspace/Plot/figure.pdf`` is simply a
    relative path in a host project that happens to have a directory
    of that name, and the request is answered from INSIDE the
    researcher's own project.

    What is unchanged, and is the point, is that nothing takes a host
    project to ``/workspace`` — a path that means nothing on a
    laptop.

    Kills: restoring the leading slash for ANY path, which sends a
    host project to the container volume instead of answering from
    the researcher's own directory.
    """
    sDirectory = _fsRegisterProject(tmp_path, S_HOST_PROJECT, "host")
    client, connection = _ftBuildFigureClient(
        os.path.join(sDirectory, "repo"),
    )
    response = client.get(
        f"/api/figure/{S_HOST_PROJECT}/workspace/Plot/figure.pdf",
    )
    assert response.status_code == 200
    assert connection.listFetchedPaths, "nothing was read at all"
    for sFetched in connection.listFetchedPaths:
        assert sFetched.startswith(sDirectory + os.sep), sFetched


@pytest.mark.falsification
def testAHostProjectsOwnAbsolutePathIsRestoredNotJoined(tmp_path):
    """The run log, which is how this was found.

    The dashboard strips the leading slash from an absolute path
    before putting it in the URL. A host run's log path is absolute
    and under the researcher's directory, so the slash-stripped form
    is ``home/someone/project/.vaibify/logs/....log``. Read as
    repo-relative it resolved under the workflow directory and 404'd:
    every host run's log was unreachable, which the browser lane found
    only once the run started reaching its finalizer.

    Kills: restoring the leading slash for the container root alone,
    which is the spelling this resolver shipped with.
    """
    sDirectory = _fsRegisterProject(tmp_path, S_HOST_PROJECT, "host")
    sRepositoryPath = os.path.join(sDirectory, "repo")
    client, connection = _ftBuildFigureClient(sRepositoryPath)
    sLogPath = os.path.join(
        sDirectory, ".vaibify", "logs", "pipeline_20260812_015939.log",
    )
    response = client.get(
        f"/api/figure/{S_HOST_PROJECT}/{sLogPath.lstrip('/')}",
    )
    assert response.status_code == 200
    assert connection.listFetchedPaths[0] == sLogPath


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


# ── Scratch: where an ephemeral working file may be written ──────

@pytest.fixture(autouse=True)
def fixtureIsolateHostScratch(tmp_path, monkeypatch):
    """Keep every scratch directory this module creates in tmp_path."""
    from vaibify.host import hostScratch
    monkeypatch.setattr(
        hostScratch, "_S_HOST_DIAGNOSTICS_ROOT",
        str(tmp_path / "hostDiagnostics"),
    )


class RecordingWriteConnection:
    """Records the paths written and the commands run, answering JSON.

    The introspection runner rejects unparseable output, so the run
    answers an empty report; everything else answers success. Both are
    what the real legs answer for a program that ran and found no
    files.
    """

    def __init__(self):
        self.listWrittenPaths = []
        self.listCommands = []

    def fnWriteFile(self, sResourceId, sPath, baContent):
        del sResourceId, baContent
        self.listWrittenPaths.append(sPath)

    def ftResultExecuteCommand(self, sResourceId, sCommand, **kwargs):
        del sResourceId, kwargs
        self.listCommands.append(sCommand)
        return 0, "[]"


@pytest.mark.falsification
def testAHostProjectsScratchIsSomewhereItsPathGuardAdmits(tmp_path):
    """The resolved scratch path passes the REAL host path guard.

    Not "it is under some directory": the oracle is the guard itself,
    because the guard is what refused the ``/tmp`` literal. It admits
    exactly the project root and the host-diagnostics subtree, so a
    scratch directory it accepts is by construction one the connection
    can write to.

    Kills: answering the container's temporary root for a host
    project, which is what every one of these call sites did — the
    introspection lane answered 500 "Path escapes the project and
    scratch roots" for the first host project that reached it.
    """
    from vaibify.host.hostConnection import HostConnection
    sDirectory = _fsRegisterProject(tmp_path, S_HOST_PROJECT, "host")
    sScratchDirectory = projectRoots.fsResolveScratchDirectory(
        S_HOST_PROJECT, "probe-operation", "/tmp",
    )
    sScriptPath = os.path.join(sScratchDirectory, "probe.py")
    assert HostConnection()._fsValidateHostPath(
        S_HOST_PROJECT, sScriptPath,
    ) == os.path.realpath(sScriptPath)
    assert not sScriptPath.startswith(sDirectory + os.sep), (
        "scratch landed inside the researcher's project directory; "
        "a throwaway program would show up as an untracked file in "
        "their repository"
    )


@pytest.mark.falsification
def testAContainerProjectsScratchStaysInTheContainerTemporaryRoot(
    tmp_path,
):
    """The other direction, and it is the one with a footprint.

    A container's ``/tmp`` is thrown away with the container. Sending
    its scratch to the host-diagnostics subtree instead would write
    the researcher's home directory for work that never left their
    container, and — worse — hand the container a path that does not
    exist inside it, so every write would fail where nothing failed
    before.

    Kills: dropping the mode test, so every resource is answered the
    host subtree.
    """
    _fsRegisterProject(tmp_path, S_CONTAINER_PROJECT, "container")
    assert projectRoots.fsResolveScratchDirectory(
        S_CONTAINER_PROJECT, "probe-operation", "/tmp",
    ) == "/tmp"


def testTheScratchDirectoryIsCreatedPrivately(tmp_path):
    """It exists when the caller gets it, and only the owner may read it.

    Scratch holds a credential on its way to a keyring and whatever a
    diagnostic program was handed, in a directory under the
    researcher's home that other local accounts can otherwise list.
    """
    _fsRegisterProject(tmp_path, S_HOST_PROJECT, "host")
    sScratchDirectory = projectRoots.fsResolveScratchDirectory(
        S_HOST_PROJECT, "probe-operation", "/tmp",
    )
    assert os.path.isdir(sScratchDirectory)
    assert oct(os.stat(sScratchDirectory).st_mode & 0o777) == oct(0o700)


@pytest.mark.falsification
def testTheIntrospectionProgramIsWrittenWhereTheHostMayWriteIt(tmp_path):
    """The lane that found this, driven end to end for a host project.

    The generator writes a program, runs it and removes it, all
    through the connection. On the host those are three real
    operations on the researcher's machine, and the path guard admits
    neither ``/tmp`` nor anything else outside the two roots.

    Kills: the runner composing its path from a ``/tmp`` literal.
    """
    from vaibify.gui.introspectionScript import _fsRunIntrospection
    _fsRegisterProject(tmp_path, S_HOST_PROJECT, "host")
    connection = RecordingWriteConnection()
    _fsRunIntrospection(
        connection, S_HOST_PROJECT, "Step", ["numbers.json"],
    )
    from vaibify.host.hostConnection import HostConnection
    assert len(connection.listWrittenPaths) == 1
    HostConnection()._fsValidateHostPath(
        S_HOST_PROJECT, connection.listWrittenPaths[0],
    )


@pytest.mark.falsification
def testTheIntrospectionProgramStillGoesToTmpInAContainer(tmp_path):
    """The other direction: a container has no scratch subtree at all.

    ``~/.vaibify`` is the HOST's directory. A container answered the
    host subtree would be handed a path nothing inside it can create,
    and the write would fail for every containerized project — the
    failure mode that matters most here, because it is the mode
    almost every user is in.

    Kills: the runner resolving unconditionally to the host subtree.
    """
    from vaibify.gui.introspectionScript import _fsRunIntrospection
    _fsRegisterProject(tmp_path, S_CONTAINER_PROJECT, "container")
    connection = RecordingWriteConnection()
    _fsRunIntrospection(
        connection, S_CONTAINER_PROJECT, "Step", ["numbers.json"],
    )
    # The PARENT is /tmp, not merely a prefix of it. A prefix check let
    # this mutant survive on Linux, where pytest's own temp directory
    # lives under /tmp: the host answer this test exists to reject
    # started with "/tmp/" too, so the assertion was satisfied by the
    # wrong cause. The scratch answer is nested three levels deeper and
    # can never have /tmp as its parent.
    assert os.path.dirname(
        connection.listWrittenPaths[0],
    ) == "/tmp", connection.listWrittenPaths


class RecordingDagConnection(RecordingWriteConnection):
    """Answers the DAG render and hands back the rendered bytes."""

    def __init__(self):
        super().__init__()
        self.listFetchedPaths = []

    def fbaFetchFile(self, sResourceId, sPath, iMaxBytes=None):
        del sResourceId, iMaxBytes
        self.listFetchedPaths.append(sPath)
        return b"<svg/>"


@pytest.mark.falsification
def testTheDagRenderWritesAndPersistsUnderTheHostsOwnRoots(tmp_path):
    """Both of the DAG's paths follow the mode, and they differ.

    The renderer writes its DOT source to scratch and copies the
    result into the project's own ``.vaibify`` directory. For a host
    project the first is the diagnostics subtree and the second is the
    researcher's repository; a ``/workspace`` literal for either one
    is a path that does not exist on their machine.

    Kills: either literal surviving — the scratch ``/tmp`` write, or
    the ``/workspace/.vaibify`` persist target.
    """
    from vaibify.gui import syncDispatcher
    sDirectory = _fsRegisterProject(tmp_path, S_HOST_PROJECT, "host")
    connection = RecordingDagConnection()
    syncDispatcher.ftResultGenerateDagSvg(
        connection, S_HOST_PROJECT, {"listSteps": []},
    )
    from vaibify.host.hostConnection import HostConnection
    HostConnection()._fsValidateHostPath(
        S_HOST_PROJECT, connection.listWrittenPaths[0],
    )
    assert os.path.join(sDirectory, ".vaibify", "dag.svg") in (
        connection.listCommands[0]
    ), connection.listCommands


@pytest.mark.falsification
def testTheDagRenderStillUsesTheContainersOwnPaths(tmp_path):
    """The other direction: a container renders where it always did.

    Kills: resolving either path unconditionally to the host answer,
    which would hand every containerized project a scratch directory
    and a persist target that exist only outside it.
    """
    from vaibify.gui import syncDispatcher
    _fsRegisterProject(tmp_path, S_CONTAINER_PROJECT, "container")
    connection = RecordingDagConnection()
    syncDispatcher.ftResultGenerateDagSvg(
        connection, S_CONTAINER_PROJECT, {"listSteps": []},
    )
    assert os.path.dirname(
        connection.listWrittenPaths[0],
    ) == "/tmp", connection.listWrittenPaths
    assert "/workspace/.vaibify/dag.svg" in connection.listCommands[0]


def testTwoDagExportsDoNotShareOneTemporaryName(tmp_path):
    """The rendered input is per-call, so a second export cannot eat it.

    Both renders previously wrote ``/tmp/_vaibify_dag.dot`` and read
    ``/tmp/_vaibify_dag.svg``: two exports in flight together produced
    one diagram twice, silently. The same fix the state files needed.
    """
    from vaibify.gui import syncDispatcher
    _fsRegisterProject(tmp_path, S_CONTAINER_PROJECT, "container")
    connection = RecordingDagConnection()
    for _iRender in range(2):
        syncDispatcher.ftResultGenerateDagSvg(
            connection, S_CONTAINER_PROJECT, {"listSteps": []},
        )
    assert len(set(connection.listWrittenPaths)) == 2, (
        connection.listWrittenPaths
    )
