"""Host-mode claim path: resource id, agent token, session push, readiness.

Symmetric-pair coverage per the standing rule: every mode-aware branch
is proven in BOTH directions — the host branch works AND the container
branch still does — with name != id fixtures on the container side and
poison legs proving what was never touched. The claim and mint
assertions drive the real ownership primitive over a real flock and a
real journal directory, never a stub keyed like the code under test.
"""

import getpass
import os

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from vaibify.config import containerLock, operationJournal, registryManager
from vaibify.gui import containerOwnership, pipelineServer, registryRoutes
from tests.sessionTokenTestHelper import fsBootstrapCredential

S_HOST_PROJECT_NAME = "claimed-host-proj"
S_CONTAINER_PROJECT_NAME = "claimed-container-proj"
S_DOCKER_CONTAINER_ID = "cid-9f31d2-distinct-from-name"


@pytest.fixture(autouse=True)
def fixtureIsolateRegistryLocksAndJournal(tmp_path, monkeypatch):
    """Registry, flocks, and journals all live under tmp_path."""
    sRegistryDir = str(tmp_path / ".vaibify")
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_DIRECTORY", sRegistryDir,
    )
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH",
        os.path.join(sRegistryDir, "registry.json"),
    )
    monkeypatch.setattr(
        registryManager, "_S_LOCK_PATH",
        os.path.join(sRegistryDir, "registry.lock"),
    )
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"),
    )
    monkeypatch.setattr(
        operationJournal, "_S_JOURNAL_DIRECTORY",
        str(tmp_path / "journal"),
    )
    registryManager.fnSaveRegistry({"listProjects": [
        {
            "sName": S_HOST_PROJECT_NAME,
            "sDirectory": str(tmp_path / "hostproj"),
            "sMode": "host",
        },
        {
            "sName": S_CONTAINER_PROJECT_NAME,
            "sDirectory": str(tmp_path / "containerproj"),
        },
    ]})


class _PoisonLeg:
    """Raises on any use — proves a code path never consulted Docker."""

    def __getattr__(self, sAttributeName):
        raise AssertionError(
            f"Docker was consulted (.{sAttributeName}) on a host path"
        )


class _ListingLeg:
    """A Docker leg answering discovery with one name != id row."""

    def flistGetRunningContainers(self):
        return [{
            "sName": S_CONTAINER_PROJECT_NAME,
            "sContainerId": S_DOCKER_CONTAINER_ID,
        }]


class TestResolveContainerId:

    def test_host_name_is_its_own_resource_id_without_docker(self):
        """Kills: dropping the host branch, which would store ''."""
        assert registryRoutes._fsResolveContainerId(
            {"docker": _PoisonLeg()}, S_HOST_PROJECT_NAME,
        ) == S_HOST_PROJECT_NAME

    def test_container_name_still_resolves_to_its_docker_id(self):
        """Kills: the branch stuck at host, which would store the name."""
        assert registryRoutes._fsResolveContainerId(
            {"docker": _ListingLeg()}, S_CONTAINER_PROJECT_NAME,
        ) == S_DOCKER_CONTAINER_ID


class TestAgentTokenMint:

    def test_host_claim_mints_no_agent_token(self):
        """The credential is unminted, not undelivered (decision 6).

        Driven through the real claim primitive over a real flock, so
        the assertion covers the path a route takes. Kills: removing
        the mode branch from ``fsMintAgentToken``.
        """
        dictContainerOwners = {}
        iStatusCode, dictPayload = containerOwnership.ftClaim(
            dictContainerOwners, S_HOST_PROJECT_NAME, "", 0,
            sContainerId=S_HOST_PROJECT_NAME,
        )
        assert iStatusCode == 200
        recordOwner = dictContainerOwners[S_HOST_PROJECT_NAME]
        assert recordOwner.sAgentToken == ""
        assert recordOwner.sContainerId == S_HOST_PROJECT_NAME

    def test_container_claim_still_mints_a_real_token(self):
        """Kills: the mint stuck at host, which would blind the agent
        lane for every container project."""
        dictContainerOwners = {}
        iStatusCode, _ = containerOwnership.ftClaim(
            dictContainerOwners, S_CONTAINER_PROJECT_NAME, "", 0,
            sContainerId=S_DOCKER_CONTAINER_ID,
        )
        assert iStatusCode == 200
        recordOwner = dictContainerOwners[S_CONTAINER_PROJECT_NAME]
        assert len(recordOwner.sAgentToken) > 20

    def test_an_empty_token_never_authorizes(self):
        """The unminted credential fails closed at the agent gate."""
        dictContainerOwners = {}
        containerOwnership.ftClaim(
            dictContainerOwners, S_HOST_PROJECT_NAME, "", 0,
            sContainerId=S_HOST_PROJECT_NAME,
        )
        assert not containerOwnership.fbAgentTokenAuthorizesContainerId(
            dictContainerOwners, "", S_HOST_PROJECT_NAME,
        )


class TestAuthorizeContainerHostBranch:

    def _fnRecordPushes(self, monkeypatch, listPushes):
        monkeypatch.setattr(
            pipelineServer.agentSessionBridge,
            "fnPushAgentSessionToContainer",
            lambda *tArguments, **dictKeywords: listPushes.append(
                tArguments,
            ),
        )

    def test_host_connect_probes_nothing_and_pushes_nothing(
        self, monkeypatch,
    ):
        """The host user is resolved in-process; no exec, no push.

        Kills: dropping the host branch — the poison leg would be
        consulted for the container user, and the recorded push list
        would gain an entry no host container exists to receive.
        """
        listPushes = []
        self._fnRecordPushes(monkeypatch, listPushes)
        dictCtx = {
            "bIsHub": True,
            "containerUsers": {},
            "docker": _PoisonLeg(),
        }
        pipelineServer._fnAuthorizeContainer(dictCtx, S_HOST_PROJECT_NAME)
        assert dictCtx["containerUsers"] == {
            S_HOST_PROJECT_NAME: getpass.getuser(),
        }
        assert listPushes == []

    def test_container_connect_still_probes_and_pushes(self, monkeypatch):
        """Kills: the branch stuck at host, which would stop delivering
        the agent session into every container."""

        class _UserProbeLeg:
            def ftResultExecuteCommand(self, sContainerId, sCommand):
                return (0, "containeruser\n")

        listPushes = []
        self._fnRecordPushes(monkeypatch, listPushes)
        dictCtx = {
            "bIsHub": True,
            "containerUsers": {},
            "docker": _UserProbeLeg(),
            "dictContainerOwners": {},
        }
        pipelineServer._fnAuthorizeContainer(
            dictCtx, S_DOCKER_CONTAINER_ID,
        )
        assert dictCtx["containerUsers"] == {
            S_DOCKER_CONTAINER_ID: "containeruser",
        }
        assert len(listPushes) == 1


class _ReadinessProbeLeg:
    """A Docker leg whose readiness probe always answers still-booting."""

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        return (0, "NOTHING")


def _fclientOwningResource(app, sName, sResourceId):
    """Return a TestClient whose browser session owns a resource."""
    from vaibify.gui import browserSession
    sCredential = fsBootstrapCredential(app)
    sSessionId = browserSession.fsSessionIdForCredential(
        app.state.dictBrowserSessions, sCredential,
    )
    app.state.dictContainerOwners[sName] = containerOwnership.OwnerRecord(
        sLeaseId="readiness-test-lease", fileHandleLock=None,
        sAgentToken="", sContainerId=sResourceId,
        sBrowserSessionId=sSessionId,
    )
    return TestClient(app, headers={
        "X-Session-Token": sCredential,
        "X-Vaibify-Lease": "readiness-test-lease",
    })


class TestReadinessRoute:

    def test_host_project_is_ready_at_once_with_no_daemon(self):
        """Host projects are ready when claimed (plan §9) — the poll
        answers instantly, without a daemon, without a probe.

        Kills: dropping the route's host branch, which would probe a
        container that does not exist and answer an error shape.
        """
        with patch.object(
            pipelineServer, "_fconnectionCreateDocker", lambda: None,
        ):
            app = pipelineServer.fappCreateApplication(
                sWorkspaceRoot="/workspace",
            )
        clientHttp = _fclientOwningResource(
            app, S_HOST_PROJECT_NAME, S_HOST_PROJECT_NAME,
        )
        response = clientHttp.get(
            f"/api/containers/{S_HOST_PROJECT_NAME}/ready",
        )
        assert response.status_code == 200
        dictBody = response.json()
        assert dictBody["bReady"] is True
        assert dictBody["sStatus"] == "ok"

    def test_container_readiness_still_waits_for_the_entrypoint(self):
        """Kills: the route stuck at host, which would answer every
        container's boot poll with an instant false yes."""
        with patch.object(
            pipelineServer, "_fconnectionCreateDocker",
            lambda: _ReadinessProbeLeg(),
        ):
            app = pipelineServer.fappCreateApplication(
                sWorkspaceRoot="/workspace",
            )
        clientHttp = _fclientOwningResource(
            app, S_CONTAINER_PROJECT_NAME, S_DOCKER_CONTAINER_ID,
        )
        response = clientHttp.get(
            f"/api/containers/{S_DOCKER_CONTAINER_ID}/ready",
        )
        assert response.status_code == 200
        dictBody = response.json()
        assert dictBody["bReady"] is False
        assert dictBody["sStatus"] == "booting"
