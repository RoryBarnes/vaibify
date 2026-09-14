"""POST /api/registry/{sName}/delete-environment, over real HTTP.

The gate in front of an irreversible action, asserted through a
``TestClient`` rather than by calling the handler: every refusal here
is only worth something if it survives the wire, and the one that
matters most -- the typed confirmation -- is worthless if it lives only
in a dialog.

The mechanics are substituted (they are ``testEnvironmentDeletion``),
so what a refusal test proves is that the mechanics were NEVER REACHED.
That is the assertion each one carries: not "a 400 came back", but "the
deletion did not run".

Two names are deliberately distinct: the container project under test
and a second, host project, so a handler that resolved the wrong entry
could not pass by coincidence.
"""

import os

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from vaibify.config import operationJournal, registryManager
from vaibify.gui import environmentDeletionRoutes


S_CONTAINER_NAME = "sample-environment"
S_HOST_NAME = "uncontained sandbox"
S_PHRASE = f"permanently delete {S_CONTAINER_NAME}"


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


def _fnRegisterProject(tmp_path, sProjectName, sMode):
    """Create a project directory + vaibify.yml and register it."""
    sProjectDirectory = str(tmp_path / sProjectName)
    os.makedirs(sProjectDirectory, exist_ok=True)
    with open(
        os.path.join(sProjectDirectory, "vaibify.yml"), "w",
    ) as fileConfig:
        fileConfig.write(f"projectName: {sProjectName}\n")
    registryManager.fnAddProject(sProjectDirectory, sMode=sMode)


@pytest.fixture
def tclient(tmp_path, monkeypatch):
    """A hub serving one container project and one host project.

    The deletion mechanics are replaced by a recorder, so every test
    here can assert whether the deletion RAN, which is the only honest
    reading of a refusal.
    """
    _fnRegisterProject(tmp_path, S_CONTAINER_NAME, "container")
    _fnRegisterProject(tmp_path, S_HOST_NAME, "host")
    listDeleted = []
    dictOutcome = {"listFailures": []}

    def fdictDelete(dictProject):
        listDeleted.append(dictProject["sName"])
        return {
            "sName": dictProject["sName"],
            "bContainerRemoved": True,
            "listVolumesRemoved": [f"{S_CONTAINER_NAME}-workspace"],
            "listImagesRemoved": [f"{S_CONTAINER_NAME}:latest"],
            "bRegistryEntryRemoved": not dictOutcome["listFailures"],
            "listFailures": list(dictOutcome["listFailures"]),
        }

    from vaibify.gui import environmentDeletion
    monkeypatch.setattr(
        environmentDeletion, "fdictDeleteEnvironment", fdictDelete)
    app = FastAPI()
    app.state.dictContainerOwners = {}
    app.state.iHubPort = 8050
    environmentDeletionRoutes.fnRegisterAll(
        app, {"require": lambda *a: None, "docker": None},
    )
    return TestClient(app), app, listDeleted, dictOutcome


def _sUrl(sName):
    return (
        "/api/registry/" + sName.replace(" ", "%20")
        + "/delete-environment"
    )


def testTheTypedConfirmationIsEnforcedOnTheServer(tclient):
    """A confirmation only checked in the dialog is not a confirmation."""
    clientTest, _, listDeleted, _ = tclient
    for sWrong in ("", "yes", "delete sample-environment",
                   "permanently delete", "permanently delete other"):
        responseHttp = clientTest.post(
            _sUrl(S_CONTAINER_NAME), json={"sConfirmation": sWrong},
        )
        assert responseHttp.status_code == 400, sWrong
    assert listDeleted == [], "a refused confirmation still deleted"


def testTheCorrectPhraseDeletesAndReportsWhatWent(tclient):
    clientTest, _, listDeleted, _ = tclient
    responseHttp = clientTest.post(
        _sUrl(S_CONTAINER_NAME), json={"sConfirmation": S_PHRASE},
    )
    assert responseHttp.status_code == 200
    assert listDeleted == [S_CONTAINER_NAME]
    dictBody = responseHttp.json()
    assert dictBody["bRegistryEntryRemoved"] is True
    assert dictBody["listImagesRemoved"] == [f"{S_CONTAINER_NAME}:latest"]


def testTheConfirmationIsToleratedWithSurroundingWhitespace(tclient):
    """A pasted phrase carrying a trailing space is still the phrase."""
    clientTest, _, listDeleted, _ = tclient
    responseHttp = clientTest.post(
        _sUrl(S_CONTAINER_NAME), json={"sConfirmation": f"  {S_PHRASE} "},
    )
    assert responseHttp.status_code == 200
    assert listDeleted == [S_CONTAINER_NAME]


def testAHostProjectIsRefusedRatherThanDeleted(tclient):
    """A host project's only asset is the researcher's own directory."""
    clientTest, _, listDeleted, _ = tclient
    responseHttp = clientTest.post(
        _sUrl(S_HOST_NAME),
        json={"sConfirmation": f"permanently delete {S_HOST_NAME}"},
    )
    assert responseHttp.status_code == 409
    assert listDeleted == []


def testTheAgentLaneIsRefusedBeforeAnythingElse(tclient):
    """An in-container agent must not delete the environment it runs in.

    Asserted with the CORRECT phrase, so the refusal cannot be
    mistaken for the confirmation gate doing the work.
    """
    clientTest, _, listDeleted, _ = tclient
    responseHttp = clientTest.post(
        _sUrl(S_CONTAINER_NAME),
        json={"sConfirmation": S_PHRASE},
        headers={"X-Vaibify-Session": "an-agent-token"},
    )
    assert responseHttp.status_code == 403
    assert listDeleted == []


def testAnEnvironmentOpenInAnotherSessionIsRefused(tclient):
    """Deleting out from under a live session is refused, and says so."""
    clientTest, app, listDeleted, _ = tclient

    class RecordOwner:
        sLeaseId = "another-session"
        sContainerId = "cid"

    app.state.dictContainerOwners[S_CONTAINER_NAME] = RecordOwner()
    responseHttp = clientTest.post(
        _sUrl(S_CONTAINER_NAME), json={"sConfirmation": S_PHRASE},
    )
    assert responseHttp.status_code == 409
    assert "delete it" in responseHttp.json()["detail"]["sMessage"]
    assert listDeleted == []


S_OWNER_LEASE = "lease-owning-tab"


def _fsInstallOwningBrowserSession(app):
    """Install a REAL owner record bound to a real browser session.

    Returns the session's credential. The record holds a real host
    flock and a session-bound lease, so a delete presenting the right
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
    app.state.dictContainerOwners[S_CONTAINER_NAME] = (
        containerOwnership.OwnerRecord(
            sLeaseId=S_OWNER_LEASE,
            fileHandleLock=containerLock.ffileAcquireContainerLock(
                S_CONTAINER_NAME, 8050,
            ),
            sAgentToken=containerOwnership.fsMintAgentToken(),
            sContainerId=S_CONTAINER_NAME,
            sBrowserSessionId=sSessionId,
        )
    )
    app.state.dictSessionOwner[sSessionId] = S_CONTAINER_NAME
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


def testTheOwningBrowserSessionDeletesItsOwnOpenEnvironment(tclient):
    """The tab that has the environment open can still delete it.

    Without the self-release, the researcher's OWN open tab is what
    refuses the deletion -- the "close it, then delete it" dead end
    that only the command line could finish, and the same one the
    conversion route already had to fix. The release goes through the
    lifecycle authority, so the flock is freed rather than the record
    merely dropped.
    """
    clientTest, app, listDeleted, _ = tclient
    sCredential = _fsInstallOwningBrowserSession(app)
    responseHttp = clientTest.post(
        _sUrl(S_CONTAINER_NAME), json={"sConfirmation": S_PHRASE},
        headers=_fdictOwnerHeaders(sCredential),
    )
    assert responseHttp.status_code == 200, responseHttp.text
    assert listDeleted == [S_CONTAINER_NAME]
    assert app.state.dictContainerOwners == {}
    assert not _fbFlockIsStillHeld(S_CONTAINER_NAME)


def testACopiedLeaseFromAnotherSessionStillRefuses(tclient):
    """A second session replaying the owner's lease value releases nothing.

    The lease is bound to the browser session that claimed it, so a
    caller presenting the right lease STRING under a different session
    credential must be refused -- session retained, flock held, nothing
    deleted. Falsifies authorizing the self-release on the lease value
    alone, which would hand any tab on the hub a delete.
    """
    from vaibify.gui import browserSession
    clientTest, app, listDeleted, _ = tclient
    _fsInstallOwningBrowserSession(app)
    _sForeignSessionId, sForeignCredential = (
        browserSession.ftMintDetachedSessionRecord(
            app.state.dictBrowserSessions,
        )
    )
    responseHttp = clientTest.post(
        _sUrl(S_CONTAINER_NAME), json={"sConfirmation": S_PHRASE},
        headers=_fdictOwnerHeaders(sForeignCredential),
    )
    assert responseHttp.status_code == 409
    assert "open in a browser session" in responseHttp.text
    assert S_CONTAINER_NAME in app.state.dictContainerOwners
    assert _fbFlockIsStillHeld(S_CONTAINER_NAME)
    assert listDeleted == []


def testAMistypedConfirmationDoesNotCostTheCallerTheirSession(tclient):
    """The confirmation runs BEFORE the self-release.

    A typo must not end the researcher's open session on an
    environment they then decided not to delete.
    """
    clientTest, app, listDeleted, _ = tclient
    sCredential = _fsInstallOwningBrowserSession(app)
    responseHttp = clientTest.post(
        _sUrl(S_CONTAINER_NAME), json={"sConfirmation": "permanently delete"},
        headers=_fdictOwnerHeaders(sCredential),
    )
    assert responseHttp.status_code == 400
    assert S_CONTAINER_NAME in app.state.dictContainerOwners
    assert _fbFlockIsStillHeld(S_CONTAINER_NAME)
    assert listDeleted == []


def testAnUnknownProjectIs404(tclient):
    clientTest, _, listDeleted, _ = tclient
    responseHttp = clientTest.post(
        _sUrl("no-such-environment"),
        json={"sConfirmation": "permanently delete no-such-environment"},
    )
    assert responseHttp.status_code == 404
    assert listDeleted == []


def testAPartialDeletionAnswers500AndNamesWhatDidGo(tclient):
    """The dashboard must be able to say "half gone", not "failed"."""
    clientTest, _, _, dictOutcome = tclient
    dictOutcome["listFailures"] = [
        f"could not remove the image {S_CONTAINER_NAME}:base",
    ]
    responseHttp = clientTest.post(
        _sUrl(S_CONTAINER_NAME), json={"sConfirmation": S_PHRASE},
    )
    assert responseHttp.status_code == 500
    sMessage = responseHttp.json()["detail"]["sMessage"]
    assert f"{S_CONTAINER_NAME}:base" in sMessage
    assert "Already removed" in sMessage
    assert "still listed" in sMessage


def testTheRouteIsNotAgentInvokableInTheCatalog():
    """The catalog exclusion and the handler's refusal must agree.

    Enforced separately from the 403 above: the exclusion is what the
    fail-closed middleware reads, so a route excluded in one place and
    not the other is a security decision made twice, differently.
    """
    from vaibify.gui import actionCatalog
    assert (
        "POST", "/api/registry/{sName}/delete-environment",
    ) in actionCatalog.SET_INTENTIONALLY_EXCLUDED_PATHS


def testTheRouteCarriesAContainerLifecycleScope():
    """Lease-enforced when owned; still answerable for an unowned one."""
    from vaibify.gui import routeScope
    assert routeScope.DICT_CONTROL_PLANE_SCOPES[
        ("POST", "/api/registry/{sName}/delete-environment")
    ] == routeScope.S_SCOPE_CONTAINER_LIFECYCLE
