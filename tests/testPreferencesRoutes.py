"""Tests for vaibify.gui.routes.preferencesRoutes.

The agent-lane test drives the REAL viewer application through
``TestClient`` with the container name distinct from the container id,
per the house rule that lane behaviour is asserted at the boundary,
never against a unit stub.
"""

import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from vaibify.config import preferencesStore
from vaibify.gui import actionCatalog
from vaibify.gui import containerOwnership
from vaibify.gui import pipelineServer
from vaibify.gui.routes import preferencesRoutes
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
