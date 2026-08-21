"""POST /api/registry/{sName}/promote-to-host-project, over real HTTP.

Every guarantee here crosses the HTTP + registry boundary, so it is
asserted through a ``TestClient`` with the host name (basename) kept
DISTINCT from the new Project name: a handler that read one where it
should read the other could not pass by coincidence. Because the route
declares ``separate-authority`` and writes no container, success is
verified against the registry entry and the rewritten vaibify.yml on
disk -- never a mutation double. The whole point of this feature is that
the promoted project STAYS host mode with no build, so every happy-path
assertion pins ``sMode == 'host'`` and the absence of a build hand-off.
"""

import os

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from vaibify.config import operationJournal, registryManager
from vaibify.config.projectConfig import fconfigLoadFromFile


S_HOST_NAME = "greenhouse sandbox"
S_NEW_NAME = "AI Greenhouse"
S_OTHER_CONTAINER = "occupied"


@pytest.fixture(autouse=True)
def fixtureIsolateHostState(tmp_path, monkeypatch):
    """Redirect registry, locks, and the journal into tmp_path."""
    from vaibify.config import containerLock
    sRegistryDirectory = str(tmp_path / ".vaibify")
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_DIRECTORY", sRegistryDirectory,
    )
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH",
        os.path.join(sRegistryDirectory, "registry.json"),
    )
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"),
    )
    monkeypatch.setattr(
        operationJournal, "_S_JOURNAL_DIRECTORY",
        str(tmp_path / "journal"),
    )
    # Keep the duplicate-name check off the developer's real daemon.
    monkeypatch.setattr(
        "vaibify.gui.registryRoutes._fbDockerContainerExists",
        lambda sName: False,
    )


def _fsRegisterProject(tmp_path, sProjectName, sMode):
    """Create a project directory + vaibify.yml and register it."""
    sProjectDirectory = str(tmp_path / sProjectName)
    os.makedirs(sProjectDirectory, exist_ok=True)
    with open(
        os.path.join(sProjectDirectory, "vaibify.yml"), "w",
    ) as fileConfig:
        fileConfig.write(f"projectName: {sProjectName}\n")
    registryManager.fnAddProject(sProjectDirectory, sMode=sMode)
    return sProjectDirectory


@pytest.fixture
def tclient(tmp_path):
    """A hub serving one host sandbox and one container project."""
    from vaibify.gui.registryRoutes import fnRegisterRegistryRoutes
    _fsRegisterProject(tmp_path, S_HOST_NAME, "host")
    _fsRegisterProject(tmp_path, S_OTHER_CONTAINER, "container")
    app = FastAPI()
    app.state.dictContainerOwners = {}
    app.state.iHubPort = 8050
    dictCtx = {"require": lambda *a: None, "docker": None}
    fnRegisterRegistryRoutes(app, dictCtx)
    return TestClient(app), app


def _fdictBody(sProjectName=S_NEW_NAME):
    return {"sProjectName": sProjectName}


def _sPromoteUrl(sName):
    return (
        "/api/registry/" + sName.replace(" ", "%20")
        + "/promote-to-host-project"
    )


def testHappyPathPromotesHostSandboxWithNoBuild(tclient):
    """The flag flips, the config is renamed, the mode stays host.

    Name != id throughout: the sandbox basename and the new Project name
    are different strings, both carrying spaces. No build is handed off.
    """
    client, _ = tclient
    response = client.post(_sPromoteUrl(S_HOST_NAME), json=_fdictBody())
    assert response.status_code == 200, response.text
    dictResult = response.json()
    assert dictResult["sName"] == S_NEW_NAME
    assert dictResult["sMode"] == "host"
    assert dictResult["sContainerName"] == S_NEW_NAME
    assert dictResult["bIsProject"] is True
    assert dictResult["bBuildRequired"] is False
    assert "sBuildPath" not in dictResult
    # The registry actually mutated: old name gone, new name is a host
    # Project, directory unchanged.
    assert registryManager.fdictGetProject(S_HOST_NAME) is None
    dictPromoted = registryManager.fdictGetProject(S_NEW_NAME)
    assert dictPromoted["sMode"] == "host"
    assert registryManager.fbIsHostProject(S_NEW_NAME)
    assert registryManager.fbIsProject(dictPromoted)
    # The vaibify.yml on disk carries the new projectName and still loads.
    configReloaded = fconfigLoadFromFile(dictPromoted["sConfigPath"])
    assert configReloaded.sProjectName == S_NEW_NAME


def testRepeatCallAfterSuccessIsRefusedNotReRegistered(tclient):
    """Idempotency: the second call sees a host Project and 409s."""
    client, _ = tclient
    assert client.post(
        _sPromoteUrl(S_HOST_NAME), json=_fdictBody(),
    ).status_code == 200
    response = client.post(
        _sPromoteUrl(S_NEW_NAME), json=_fdictBody("AI Greenhouse Two"),
    )
    assert response.status_code == 409
    assert "already a Project" in response.text


def testUnknownProjectIs404(tclient):
    client, _ = tclient
    response = client.post(
        _sPromoteUrl("no-such-project"), json=_fdictBody(),
    )
    assert response.status_code == 404


def testContainerProjectIs409(tclient):
    """A container project cannot be promoted -- it is already a Project."""
    client, _ = tclient
    response = client.post(
        _sPromoteUrl(S_OTHER_CONTAINER),
        json=_fdictBody("Occupied Project"),
    )
    assert response.status_code == 409
    assert "already a containerized project" in response.text


def testClaimedProjectIs409AndDoesNotMutate(tclient):
    """An owned project is refused, and nothing on disk changes.

    The owner map is keyed by the registry NAME (the host basename), the
    same key the lock/lease use, so the guard reads the same key the
    promotion would rename.
    """
    client, app = tclient
    app.state.dictContainerOwners[S_HOST_NAME] = object()
    response = client.post(_sPromoteUrl(S_HOST_NAME), json=_fdictBody())
    assert response.status_code == 409
    assert "open in a browser session" in response.text
    # Still a host sandbox, still under its original name.
    dictHost = registryManager.fdictGetProject(S_HOST_NAME)
    assert registryManager.fbIsHostProject(S_HOST_NAME)
    assert not registryManager.fbIsProject(dictHost)
    assert registryManager.fdictGetProject(S_NEW_NAME) is None


def testDuplicateNewNameIs409(tclient):
    """The new name must be free of every other registered project."""
    client, _ = tclient
    response = client.post(
        _sPromoteUrl(S_HOST_NAME), json=_fdictBody(S_OTHER_CONTAINER),
    )
    assert response.status_code == 409
    assert not registryManager.fbIsProject(
        registryManager.fdictGetProject(S_HOST_NAME))


def testNonStorageSafeNameIs400AndDoesNotMutate(tclient):
    """A name with a path separator is refused before any write."""
    client, _ = tclient
    response = client.post(
        _sPromoteUrl(S_HOST_NAME),
        json=_fdictBody("bad/name"),
    )
    assert response.status_code == 400
    # The config was NOT rewritten: the host projectName still stands and
    # the project is still an un-promoted sandbox.
    dictHost = registryManager.fdictGetProject(S_HOST_NAME)
    configHost = fconfigLoadFromFile(dictHost["sConfigPath"])
    assert configHost.sProjectName == S_HOST_NAME
    assert not registryManager.fbIsProject(dictHost)


def testKeepingTheSameNameStillGraduatesToProject(tmp_path):
    """A sandbox may keep its basename and still become a Project.

    The route's own duplicate check must skip the project being
    promoted, or the entry would collide with itself and 409. A
    Docker-unsafe basename with a space is fine here -- host mode never
    turns the name into a Docker object.
    """
    from vaibify.gui.registryRoutes import fnRegisterRegistryRoutes
    _fsRegisterProject(tmp_path, S_HOST_NAME, "host")
    app = FastAPI()
    app.state.dictContainerOwners = {}
    app.state.iHubPort = 8050
    fnRegisterRegistryRoutes(
        app, {"require": lambda *a: None, "docker": None},
    )
    client = TestClient(app)
    response = client.post(
        _sPromoteUrl(S_HOST_NAME), json=_fdictBody(S_HOST_NAME),
    )
    assert response.status_code == 200, response.text
    dictPromoted = registryManager.fdictGetProject(S_HOST_NAME)
    assert dictPromoted["sMode"] == "host"
    assert dictPromoted["bIsProject"] is True


S_OWNER_LEASE = "lease-owning-tab"


def _fsInstallOwningBrowserSession(app, sHostName=S_HOST_NAME):
    """Install a REAL owner record bound to a real browser session.

    Returns the session's credential. The record holds a real host
    flock and a session-bound lease, so a promote presenting the right
    headers exercises the actual lifecycle release -- never a stand-in
    ``object()`` -- and "the flock was freed" can be observed by trying
    to acquire it.
    """
    from vaibify.config import containerLock
    from vaibify.gui import browserSession, containerOwnership
    dictStore = browserSession.fdictCreateBrowserSessionStore()
    sSessionId, sCredential = browserSession.ftMintDetachedSessionRecord(
        dictStore,
    )
    app.state.dictBrowserSessions = dictStore
    app.state.dictSessionOwner = (
        containerOwnership.fdictCreateSessionOwnerIndex()
    )
    app.state.dictSessionSockets = (
        containerOwnership.fdictCreateSessionSocketIndex()
    )
    app.state.dictMutationSupervisors = {}
    app.state.dictDurableTaskRecords = {}
    app.state.dictTerminalExecutionRecords = {}
    app.state.dictContainerOwners[sHostName] = (
        containerOwnership.OwnerRecord(
            sLeaseId=S_OWNER_LEASE,
            fileHandleLock=containerLock.ffileAcquireContainerLock(
                sHostName, 8050,
            ),
            sAgentToken=containerOwnership.fsMintAgentToken(),
            sContainerId=sHostName,
            sBrowserSessionId=sSessionId,
        )
    )
    app.state.dictSessionOwner[sSessionId] = sHostName
    return sCredential


def _fbFlockIsStillHeld(sName):
    """Return True when the project's host flock cannot be acquired."""
    from vaibify.config import containerLock
    try:
        fileHandle = containerLock.ffileAcquireContainerLock(sName, 8051)
    except containerLock.ContainerLockedError:
        return True
    containerLock.fnReleaseContainerLock(fileHandle)
    return False


def _fdictOwnerHeaders(sCredential, sLeaseId=S_OWNER_LEASE):
    return {"X-Vaibify-Lease": sLeaseId, "X-Session-Token": sCredential}


@pytest.mark.falsification
def testPromoteFromTheOwningBrowserSessionReleasesAndPromotes(tclient):
    """The owning tab promotes its own open project in one request.

    The route used to refuse ANY owned project, so promoting from
    inside the browser was impossible -- the researcher was told to
    close the project and had no in-browser way to finish. Now the
    presenting session, when it IS the holder, is released through the
    lifecycle authority and the promotion proceeds: registry flipped,
    owner record gone, flock freed.

    Kills: reinstating the unconditional owned-project refusal, and a
    release that drops the owner record without freeing the flock.
    """
    client, app = tclient
    sCredential = _fsInstallOwningBrowserSession(app)
    response = client.post(
        _sPromoteUrl(S_HOST_NAME), json=_fdictBody(),
        headers=_fdictOwnerHeaders(sCredential),
    )
    assert response.status_code == 200, response.text
    dictPromoted = registryManager.fdictGetProject(S_NEW_NAME)
    assert dictPromoted is not None and dictPromoted["bIsProject"] is True
    assert registryManager.fdictGetProject(S_HOST_NAME) is None
    assert app.state.dictContainerOwners == {}
    assert not _fbFlockIsStillHeld(S_HOST_NAME)


@pytest.mark.falsification
def testACopiedLeaseFromAnotherSessionStillRefuses(tclient):
    """A second session replaying the owner's lease value releases nothing.

    The lease is bound to the browser session that claimed it, so a
    caller presenting the right lease STRING under a different session
    credential must be refused exactly as before -- session retained,
    flock held, registry unchanged.

    Kills: authorizing the self-release on the lease value alone in
    ``_fnReleaseCallerOwnedSessionForConversion``.
    """
    from vaibify.gui import browserSession
    client, app = tclient
    _fsInstallOwningBrowserSession(app)
    _sForeignSessionId, sForeignCredential = (
        browserSession.ftMintDetachedSessionRecord(
            app.state.dictBrowserSessions,
        )
    )
    response = client.post(
        _sPromoteUrl(S_HOST_NAME), json=_fdictBody(),
        headers=_fdictOwnerHeaders(sForeignCredential),
    )
    assert response.status_code == 409
    assert "open in a browser session" in response.text
    assert S_HOST_NAME in app.state.dictContainerOwners
    assert _fbFlockIsStillHeld(S_HOST_NAME)
    assert registryManager.fdictGetProject(S_NEW_NAME) is None


def testAValidatorRefusalDoesNotCostTheCallerTheirSession(tclient):
    """A refused new name leaves the caller's open session intact.

    The validators run BEFORE the self-release, so a duplicate name
    409s while the researcher's project view stays owned and usable.
    """
    client, app = tclient
    sCredential = _fsInstallOwningBrowserSession(app)
    response = client.post(
        _sPromoteUrl(S_HOST_NAME), json=_fdictBody(S_OTHER_CONTAINER),
        headers=_fdictOwnerHeaders(sCredential),
    )
    assert response.status_code == 409
    assert S_HOST_NAME in app.state.dictContainerOwners
    assert _fbFlockIsStillHeld(S_HOST_NAME)
    assert not registryManager.fbIsProject(
        registryManager.fdictGetProject(S_HOST_NAME))


def testABusyOwnSessionIsRefusedWithTheLifecycleReason(tclient):
    """A live run refuses the self-release, session retained.

    The lifecycle authority's own busy arbitration governs: a live
    durable task must never be released out from under, so the promote
    is refused with the run named -- not with the stale "close it"
    message -- and nothing mutates.
    """
    from types import SimpleNamespace
    client, app = tclient
    sCredential = _fsInstallOwningBrowserSession(app)
    app.state.dictDurableTaskRecords[S_HOST_NAME] = SimpleNamespace(
        sTaskId="task-live-run", sState="running", iOwnerGeneration=1,
        taskAsync=SimpleNamespace(done=lambda: False),
    )
    response = client.post(
        _sPromoteUrl(S_HOST_NAME), json=_fdictBody(),
        headers=_fdictOwnerHeaders(sCredential),
    )
    assert response.status_code == 409
    assert "run still in progress" in response.text
    assert S_HOST_NAME in app.state.dictContainerOwners
    assert registryManager.fdictGetProject(S_NEW_NAME) is None


def testPromotedHostProjectCanStillBeContainerizedLater(tclient):
    """Promotion and containerization are independent axes.

    A host Project is still host mode, so the convert route's non-host
    guard (which reads sMode, not bIsProject) still admits it.
    """
    client, _ = tclient
    assert client.post(
        _sPromoteUrl(S_HOST_NAME), json=_fdictBody(),
    ).status_code == 200
    response = client.post(
        "/api/registry/" + S_NEW_NAME.replace(" ", "%20")
        + "/convert-to-container",
        json={"sProjectName": "aiGreenhouseBox"},
    )
    assert response.status_code == 200, response.text
    dictConverted = registryManager.fdictGetProject("aiGreenhouseBox")
    assert dictConverted["sMode"] == "container"


def _fsScaffoldedWorkflowPath(tmp_path):
    return str(
        tmp_path / S_HOST_NAME / ".vaibify" / "projects" / "project.json"
    )


@pytest.mark.falsification
def testPromotionScaffoldsTheWorkflowTheDashboardWillOpen(
    tclient, tmp_path,
):
    """A promoted Project owns a workflow file, named after itself.

    A sandbox scaffolds no workflow at all, so promotion is the moment
    the first workflow file comes into being. Without it the dashboard's
    post-promotion re-entry found zero workflow cards and stranded the
    researcher on an empty picker with the birth animation firing over
    nothing (live report, 2026-08-20). The file carries the PROMOTED
    name — not the sandbox basename — so the picker card reads what the
    researcher just typed.

    Kills: dropping the _fnScaffoldEmptyWorkflowForPromotion call from
    the promote route, which re-strands every promotion from a blank
    sandbox.
    """
    import json as moduleJson
    client, _ = tclient
    response = client.post(_sPromoteUrl(S_HOST_NAME), json=_fdictBody())
    assert response.status_code == 200, response.text
    sWorkflowPath = _fsScaffoldedWorkflowPath(tmp_path)
    assert os.path.isfile(sWorkflowPath), (
        "promotion left the Project with no workflow file"
    )
    with open(sWorkflowPath) as fileWorkflow:
        dictWorkflow = moduleJson.load(fileWorkflow)
    assert dictWorkflow["sWorkflowName"] == S_NEW_NAME
    assert dictWorkflow["listSteps"] == []


@pytest.mark.falsification
@pytest.mark.parametrize(
    "sExistingRelativePath",
    [".vaibify/projects/myAnalysis.json", "project.json"],
    ids=["canonical", "legacyRoot"],
)
def testAnExistingWorkflowIsNeverOverwrittenByPromotion(
    tclient, tmp_path, sExistingRelativePath,
):
    """The scaffold steps aside for a workflow the researcher created.

    Both discovered locations count — the canonical directory and the
    legacy repository root — because a scaffold that only checked one
    would silently shadow a legacy-root workflow with an empty twin,
    and the picker would offer two cards for one Project.

    Kills: dropping the bWorkflowExists guard so the scaffold always
    writes.
    """
    import json as moduleJson
    client, _ = tclient
    sExistingPath = str(tmp_path / S_HOST_NAME / sExistingRelativePath)
    os.makedirs(os.path.dirname(sExistingPath), exist_ok=True)
    dictExisting = {
        "sWorkflowName": "My Analysis",
        "listSteps": [{"sName": "First Step"}],
    }
    with open(sExistingPath, "w") as fileWorkflow:
        moduleJson.dump(dictExisting, fileWorkflow)
    response = client.post(_sPromoteUrl(S_HOST_NAME), json=_fdictBody())
    assert response.status_code == 200, response.text
    with open(sExistingPath) as fileWorkflow:
        assert moduleJson.load(fileWorkflow) == dictExisting
    sScaffoldPath = _fsScaffoldedWorkflowPath(tmp_path)
    if sExistingPath != sScaffoldPath:
        assert not os.path.exists(sScaffoldPath), (
            "the scaffold wrote an empty twin beside an existing "
            "workflow"
        )
