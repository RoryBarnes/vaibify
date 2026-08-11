"""The daemon gate asks about the resource, not about the process.

``dictCtx["require"]`` answers 503 "Docker support is not available"
when the daemon is unreachable. Every container-scoped route calls it
first, and until host mode that was right: with no daemon there was
nothing any route could do.

Host mode exists precisely FOR the researcher who has no Docker -- the
image build is what kills first contact. A hub serving a host project
on a machine with no daemon must not answer 503 about a project that
never wanted one; a diagnosis telling them to install Docker sends
them to the very thing host mode let them skip.

BOTH DIRECTIONS, and they fail oppositely: a gate that never asks the
daemon lets a containerized project reach deep into code that will
fail on ``None`` instead of getting a clean 503 with a fix in it, and
a gate that always asks makes host mode unusable on the machines it
was built for.

The gate driven here is the REAL ``_fnRequireDocker`` over a real
connection of ``None``, not a stub -- a stub would prove only that the
route calls something.
"""

import os

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from vaibify.config import registryManager


S_HOST_PROJECT = "gated-host-project"
S_CONTAINER_ID = "0123456789abcdef"


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


def _fsRegisterHostProject(tmp_path):
    """Create and register a host project; return its directory."""
    sProjectDirectory = str(tmp_path / S_HOST_PROJECT)
    os.makedirs(sProjectDirectory, exist_ok=True)
    with open(
        os.path.join(sProjectDirectory, "vaibify.yml"), "w",
    ) as fileConfig:
        fileConfig.write(f"projectName: {S_HOST_PROJECT}\n")
    registryManager.fnAddProject(sProjectDirectory, sMode="host")
    return sProjectDirectory


class HostLegConnection:
    """The paths a host leg would answer; no Docker anywhere."""

    def flistContainerPathsExist(self, sResourceId, listPaths):
        del sResourceId
        return [True] * len(listPaths)


def _ftBuildDaemonlessClient():
    """Return a client whose require gate is real and whose daemon is gone.

    ``dictCtx["docker"]`` is the connection the gate consults. ``None``
    is exactly what the factory produces when the daemon cannot be
    reached, so this is the daemon-less machine, not a simulation of
    one.
    """
    from vaibify.gui.dockerStatus import _fnRequireDocker
    from vaibify.gui.routes.fileRoutes import fnRegisterAll
    app = FastAPI()
    app.state.dictContainerOwners = {}
    dictCtx = {
        "docker": HostLegConnection(),
        "workflows": {},
        "paths": {},
        "workflowDir": lambda sResourceId: "/unused",
    }
    # The real gate, closed over a daemon connection of None -- the
    # same composition ``pipelineServer._ftBuildHelpers`` builds.
    dictCtx["require"] = lambda sResourceId=None: _fnRequireDocker(
        None, sResourceId=sResourceId,
    )
    fnRegisterAll(app, dictCtx, "/workspace")
    return TestClient(app)


@pytest.mark.falsification
def testAHostProjectIsServedWhenNoDaemonIsReachable(tmp_path):
    """A host project answers on a machine with no Docker at all.

    Kills: a route calling the gate without naming its resource --
    the bare form every one of these calls had before this sweep --
    which answers 503 "Docker support is not available" about a
    project that has no container and never asked for one.
    """
    sDirectory = _fsRegisterHostProject(tmp_path)
    client = _ftBuildDaemonlessClient()
    response = client.post(
        f"/api/files/{S_HOST_PROJECT}/exist",
        json={"saRelativePaths": [
            os.path.join(sDirectory, "repo", "Step", "output.dat"),
        ]},
    )
    assert response.status_code == 200, response.text


@pytest.mark.falsification
def testAContainerProjectStillGetsTheDaemonDiagnosis(tmp_path):
    """The other direction: a container id with no daemon still 503s.

    The 503 carries the diagnosis and the command that fixes it. A
    gate that stopped refusing would let this request reach code
    holding ``None`` for a connection, and the researcher would get
    an ``AttributeError`` in a 500 instead.

    Kills: making the host bypass unconditional in
    ``_fnRequireDocker``.
    """
    del tmp_path
    client = _ftBuildDaemonlessClient()
    response = client.post(
        f"/api/files/{S_CONTAINER_ID}/exist",
        json={"saRelativePaths": ["/workspace/repo/Step/output.dat"]},
    )
    assert response.status_code == 503, response.text
    assert "Docker" in response.json()["detail"]


def testTheGateStillRefusesAnUnregisteredResource(tmp_path):
    """An id nothing registered is not treated as a host project."""
    del tmp_path
    from fastapi import HTTPException
    from vaibify.gui.dockerStatus import _fnRequireDocker
    with pytest.raises(HTTPException) as excInfo:
        _fnRequireDocker(None, sResourceId="never-registered")
    assert excInfo.value.status_code == 503
