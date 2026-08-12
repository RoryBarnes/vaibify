"""Stopping a run must not depend on the hub remembering the session.

Found during the first live host-mode cancel. A researcher's hub had
been restarted under an already-open browser tab. The tab keeps its own
state, so the dashboard looked healthy — right project, right workflow
path, both steps listed — while the hub's in-memory workflow cache was
empty. Runs still started, because the run is driven from what the page
holds. Stop All Running Tasks answered ``404 Not connected to
container``, naming a container a host project does not have, and the
processes kept running with no way to stop them from the dashboard.

The cause was ordering: the route required the cached workflow before
branching, though only the CONTAINER branch reads it — the sweep needs
step directories. Host cancellation reads the operation JOURNAL, which
is on disk and survives a restart, and needs nothing from the cache.

The principle is worth more than the fix. **The stop control must not
depend on session bookkeeping**, because the situation where a
researcher most needs it is the one where something has already gone
wrong. Requiring a session to stop a process makes the control weakest
exactly when it matters.
"""

import json
import os
import subprocess
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from vaibify.config import containerLock, registryManager
from vaibify.gui import pipelineServer
from tests.sessionTokenTestHelper import fsBootstrapCredential
from tests.testAgentLaneEnforcement import (
    MockDockerConnection,
    S_CONTAINER_ID,
    S_CONTAINER_NAME,
)


S_HOST_PROJECT = "stopWithoutSessionProject"


@pytest.fixture(autouse=True)
def fixtureIsolate(tmp_path, monkeypatch):
    """A private lock dir and a registry holding one host project."""
    monkeypatch.setattr(containerLock, "_S_LOCK_DIRECTORY", str(tmp_path))
    sRegistryDirectory = str(tmp_path / ".vaibify")
    os.makedirs(sRegistryDirectory, exist_ok=True)
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_DIRECTORY", sRegistryDirectory,
    )
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH",
        os.path.join(sRegistryDirectory, "registry.json"),
    )
    sProjectDirectory = str(tmp_path / S_HOST_PROJECT)
    os.makedirs(sProjectDirectory, exist_ok=True)
    with open(
        os.path.join(sProjectDirectory, "vaibify.yml"), "w",
    ) as fileConfig:
        fileConfig.write(f"projectName: {S_HOST_PROJECT}\n")
    subprocess.run(
        ["git", "init", "-q"], cwd=sProjectDirectory, check=True,
        capture_output=True,
    )
    with open(
        os.path.join(sRegistryDirectory, "registry.json"), "w",
    ) as fileRegistry:
        json.dump({"listProjects": [{
            "sName": S_HOST_PROJECT,
            "sContainerName": S_HOST_PROJECT,
            "sMode": "host",
            "sDirectory": sProjectDirectory,
            "sConfigPath": os.path.join(sProjectDirectory, "vaibify.yml"),
        }]}, fileRegistry)


@pytest.fixture
def appHub():
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker", MockDockerConnection,
    ):
        return pipelineServer.fappCreateHubApplication(iExpectedPort=0)


@pytest.fixture
def clientBrowser(appHub):
    return TestClient(
        appHub,
        headers={"X-Session-Token": fsBootstrapCredential(appHub)},
        raise_server_exceptions=False,
    )


def _fsClaimAndReturnLease(clientBrowser, sName):
    responseClaim = clientBrowser.post(f"/api/registry/{sName}/claim")
    assert responseClaim.status_code == 200, responseClaim.text
    return responseClaim.json()["sLeaseId"]


@pytest.mark.falsification
def testAHostRunCanBeStoppedWithNoWorkflowCached(appHub, clientBrowser):
    """The reported failure, with the cache empty exactly as it was.

    No connect is performed, so ``dictCtx["workflows"]`` holds nothing
    for this project — the state a hub is in after restarting under a
    live tab.

    Kills: requiring the cached workflow before the host branch, which
    answers 404 and leaves the researcher's processes running.
    """
    sLeaseId = _fsClaimAndReturnLease(clientBrowser, S_HOST_PROJECT)
    assert not appHub.state.dictRouteContext["workflows"].get(
        S_HOST_PROJECT,
    ), "the cache must be empty or this test proves nothing"

    responseKill = clientBrowser.post(
        f"/api/pipeline/{S_HOST_PROJECT}/kill",
        headers={"X-Vaibify-Lease": sLeaseId},
    )
    assert responseKill.status_code == 200, responseKill.text
    assert responseKill.json()["bSuccess"] is True


@pytest.mark.falsification
def testAContainerStopStillRequiresItsWorkflow(appHub, clientBrowser):
    """The other direction: the container sweep genuinely needs it.

    The sweep resolves step directories out of the workflow, so a
    container stop with nothing cached cannot do its job and must say
    so rather than reporting a sweep it never performed.

    Kills: dropping the requirement for both branches, which would
    make the container lane silently sweep nothing and report success.
    """
    sLeaseId = _fsClaimAndReturnLease(clientBrowser, S_CONTAINER_NAME)
    responseKill = clientBrowser.post(
        f"/api/pipeline/{S_CONTAINER_ID}/kill",
        headers={"X-Vaibify-Lease": sLeaseId},
    )
    assert responseKill.status_code == 404, responseKill.text
