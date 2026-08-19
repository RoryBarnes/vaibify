"""Tests for vaibify.gui.routes.preferencesRoutes.

The agent-lane test drives the REAL viewer application through
``TestClient`` with the container name distinct from the container id,
per the house rule that lane behaviour is asserted at the boundary,
never against a unit stub.
"""

import math
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from vaibify.config import preferencesStore
from vaibify.gui import actionCatalog
from vaibify.gui import containerOwnership
from vaibify.gui import pipelineServer
from vaibify.gui import serverLifespan
from vaibify.gui.routes import preferencesRoutes
from vaibify.gui.routes.sessionRoutes import S_SUPPRESS_BROWSER_ENV
from tests.sessionTokenTestHelper import fsBootstrapCredential


S_CONTAINER_ID = "abc123container"
S_CONTAINER_NAME = "test-container"
S_AGENT_TOKEN = "agent-token-for-this-container"


@pytest.fixture(autouse=True)
def fixtureIsolatePreferences(tmp_path, monkeypatch):
    """Redirect the preferences store to a temp directory for every test."""
    sPreferencesDirectory = str(tmp_path / ".vaibify")
    monkeypatch.setattr(
        preferencesStore, "_S_PREFERENCES_DIRECTORY",
        sPreferencesDirectory,
    )
    monkeypatch.setattr(
        preferencesStore, "_S_PREFERENCES_PATH",
        os.path.join(sPreferencesDirectory, "preferences.json"),
    )
    monkeypatch.setattr(
        preferencesStore, "_S_LOCK_PATH",
        os.path.join(sPreferencesDirectory, "preferences.lock"),
    )
    # Make idle-timeout resolution deterministic: no env override and no
    # browser-suppression signal, so the launch default is "never".
    monkeypatch.delenv(serverLifespan.S_HUB_IDLE_TIMEOUT_ENV, raising=False)
    monkeypatch.delenv(S_SUPPRESS_BROWSER_ENV, raising=False)


@pytest.fixture
def fixtureClient():
    """A bare app carrying only the preferences routes."""
    from fastapi import FastAPI

    app = FastAPI()
    preferencesRoutes.fnRegisterAll(
        app, {"require": lambda *aArgs: None, "docker": None},
    )
    return TestClient(app)


def testRegisterAllIsCallable():
    assert callable(preferencesRoutes.fnRegisterAll)


def testGetPreferencesReturnsTheStore(fixtureClient, tmp_path):
    sProjectDirectory = str(tmp_path / "myProject")
    os.makedirs(sProjectDirectory)
    preferencesStore.fnRecordHostWarningAcknowledged(sProjectDirectory)
    response = fixtureClient.get("/api/preferences")
    assert response.status_code == 200
    dictAcknowledged = response.json()["dictHostWarningAcknowledged"]
    assert os.path.realpath(sProjectDirectory) in dictAcknowledged


def testGetPreferencesReadsEmptyWhenNoFileExists(fixtureClient):
    response = fixtureClient.get("/api/preferences")
    assert response.status_code == 200
    assert response.json() == {"dictHostWarningAcknowledged": {}}


def testPutHostWarningAcknowledgedRecords(fixtureClient, tmp_path):
    sProjectDirectory = str(tmp_path / "myProject")
    os.makedirs(sProjectDirectory)
    response = fixtureClient.put(
        "/api/preferences/host-warning-acknowledged",
        json={"sProjectDirectory": sProjectDirectory},
    )
    assert response.status_code == 200
    assert response.json() == {"bAcknowledged": True}
    assert preferencesStore.fbHostWarningAcknowledged(sProjectDirectory)


def testPutResolvesTheRealPath(fixtureClient, tmp_path):
    """A recorded symlink alias acknowledges the canonical directory."""
    sRealDirectory = str(tmp_path / "realProject")
    os.makedirs(sRealDirectory)
    sAliasDirectory = str(tmp_path / "aliasProject")
    os.symlink(sRealDirectory, sAliasDirectory)
    response = fixtureClient.put(
        "/api/preferences/host-warning-acknowledged",
        json={"sProjectDirectory": sAliasDirectory},
    )
    assert response.status_code == 200
    assert preferencesStore.fbHostWarningAcknowledged(sRealDirectory)


def testPutRefusesARelativePath(fixtureClient):
    response = fixtureClient.put(
        "/api/preferences/host-warning-acknowledged",
        json={"sProjectDirectory": "relative/path/to/project"},
    )
    assert response.status_code == 400


def testPutRefusesAnEmptyPath(fixtureClient):
    response = fixtureClient.put(
        "/api/preferences/host-warning-acknowledged",
        json={"sProjectDirectory": "   "},
    )
    assert response.status_code == 400


# ── Idle-timeout preference (GET / PUT), live application ─────────


def testGetIdleTimeoutReflectsAppState(fixtureClient):
    """GET reports the live app.state value the watchdog reads each tick."""
    fixtureClient.app.state.fIdleTimeoutSeconds = 1800.0
    response = fixtureClient.get("/api/preferences/idle-timeout")
    assert response.status_code == 200
    dictBody = response.json()
    assert dictBody["bNever"] is False
    assert dictBody["fSeconds"] == 1800.0


def testGetIdleTimeoutReportsNeverForInfinity(fixtureClient):
    """An infinite (disabled) timeout is reported as bNever with null seconds."""
    fixtureClient.app.state.fIdleTimeoutSeconds = math.inf
    response = fixtureClient.get("/api/preferences/idle-timeout")
    assert response.status_code == 200
    dictBody = response.json()
    assert dictBody["bNever"] is True
    assert dictBody["fSeconds"] is None


def testPutIdleTimeoutPersistsAndAppliesLive(fixtureClient):
    """PUT stores the preference and updates app.state without a relaunch."""
    response = fixtureClient.put(
        "/api/preferences/idle-timeout", json={"sValue": "900"},
    )
    assert response.status_code == 200
    assert response.json()["fSeconds"] == 900.0
    assert preferencesStore.fsIdleTimeoutPreference() == "900"
    assert fixtureClient.app.state.fIdleTimeoutSeconds == 900.0


def testPutIdleTimeoutNeverDisablesReaper(fixtureClient):
    """PUT 'never' persists the token and sets the live timeout to infinity."""
    response = fixtureClient.put(
        "/api/preferences/idle-timeout", json={"sValue": "never"},
    )
    assert response.status_code == 200
    assert response.json()["bNever"] is True
    assert preferencesStore.fsIdleTimeoutPreference() == "never"
    assert math.isinf(fixtureClient.app.state.fIdleTimeoutSeconds)


@pytest.mark.parametrize("sBadValue", ["abc", "-5", "   ", "nan"])
def testPutIdleTimeoutRejectsGarbage(fixtureClient, sBadValue):
    """A malformed, negative, or empty value is refused and not persisted."""
    response = fixtureClient.put(
        "/api/preferences/idle-timeout", json={"sValue": sBadValue},
    )
    assert response.status_code == 400
    assert preferencesStore.fsIdleTimeoutPreference() == ""


def testPutIdleTimeoutEnvOverrideStillWinsLive(fixtureClient, monkeypatch):
    """A stored preference never overrides the env; env wins live too."""
    monkeypatch.setenv(serverLifespan.S_HUB_IDLE_TIMEOUT_ENV, "60")
    response = fixtureClient.put(
        "/api/preferences/idle-timeout", json={"sValue": "never"},
    )
    assert response.status_code == 200
    dictBody = response.json()
    assert dictBody["bNever"] is False
    assert dictBody["fSeconds"] == 60.0
    assert dictBody["bEnvOverride"] is True
    # The preference is still recorded, to take effect once the env clears.
    assert preferencesStore.fsIdleTimeoutPreference() == "never"
    assert fixtureClient.app.state.fIdleTimeoutSeconds == 60.0


def testIdleTimeoutAgentLaneCatalogRefusesTheRoute():
    """The catalog refuses the agent lane for the idle-timeout write."""
    assert not actionCatalog.fbAgentLanePermitsRoute(
        "PUT", "/api/preferences/idle-timeout",
    )


def testIdleTimeoutAgentLaneIsRefusedAtBoundary(clientAgent):
    """The in-container agent cannot disable the reaper over the real app."""
    response = clientAgent.put(
        "/api/preferences/idle-timeout", json={"sValue": "never"},
    )
    assert response.status_code == 401
    assert preferencesStore.fsIdleTimeoutPreference() == ""


def testIdleTimeoutBrowserLaneAppliesLive(appViewer, monkeypatch):
    """The researcher's browser sets the live timeout over the real app."""
    monkeypatch.delenv(serverLifespan.S_HUB_IDLE_TIMEOUT_ENV, raising=False)
    clientBrowser = TestClient(
        appViewer,
        headers={"X-Session-Token": fsBootstrapCredential(appViewer)},
    )
    response = clientBrowser.put(
        "/api/preferences/idle-timeout", json={"sValue": "900"},
    )
    assert response.status_code == 200
    assert preferencesStore.fsIdleTimeoutPreference() == "900"
    assert appViewer.state.fIdleTimeoutSeconds == 900.0


# ── Agent-lane refusal, at the real boundary ─────────────────────


@pytest.fixture
def appViewer():
    """Build the real viewer application over a mocked Docker."""
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker",
        return_value=MagicMock(),
    ):
        return pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace", sTerminalUserArg="testuser",
        )


@pytest.fixture
def clientAgent(appViewer):
    """A client authenticated as the in-container agent.

    The owner map is keyed by container NAME while the agent token is
    bound to the container ID, and name != id here on purpose.
    """
    appViewer.state.dictContainerOwners[S_CONTAINER_NAME] = (
        containerOwnership.OwnerRecord(
            sLeaseId="researcher-lease", fileHandleLock=None,
            sAgentToken=S_AGENT_TOKEN, sContainerId=S_CONTAINER_ID,
        )
    )
    return TestClient(
        appViewer,
        headers={
            actionCatalog.S_SESSION_HEADER_NAME: S_AGENT_TOKEN,
            "Host": "host.docker.internal:8050",
        },
    )


def testAgentLaneIsRefused(clientAgent, tmp_path):
    """The agent must not be able to suppress the host warning.

    A per-container agent token authorizes only paths that carry its
    container id, and this hub-scoped path carries none — so the token
    fails to authenticate at all and the request falls to the browser
    lane, which refuses it 401 for lacking a browser credential. The
    catalog's 403 backstop is asserted separately below.
    """
    sProjectDirectory = str(tmp_path / "myProject")
    os.makedirs(sProjectDirectory)
    response = clientAgent.put(
        "/api/preferences/host-warning-acknowledged",
        json={"sProjectDirectory": sProjectDirectory},
    )
    assert response.status_code == 401
    assert not preferencesStore.fbHostWarningAcknowledged(
        sProjectDirectory,
    )


def testAgentLaneCatalogRefusesTheRoute():
    assert not actionCatalog.fbAgentLanePermitsRoute(
        "PUT", "/api/preferences/host-warning-acknowledged",
    )


def testBrowserLaneStillRecords(appViewer, tmp_path):
    """The researcher's browser reaches the route through the real app."""
    clientBrowser = TestClient(
        appViewer,
        headers={"X-Session-Token": fsBootstrapCredential(appViewer)},
    )
    sProjectDirectory = str(tmp_path / "myProject")
    os.makedirs(sProjectDirectory)
    response = clientBrowser.put(
        "/api/preferences/host-warning-acknowledged",
        json={"sProjectDirectory": sProjectDirectory},
    )
    assert response.status_code == 200
    assert preferencesStore.fbHostWarningAcknowledged(sProjectDirectory)
