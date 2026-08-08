"""Tests for the connection router and the Docker-reachability predicate.

Every dispatch assertion uses name != id fixtures and recording (or
poison) legs, per the repo's epistemics rule: a router stuck at either
leg must fail these tests, and the stuck-at mutants are registered as
falsifications in both directions.
"""

import os

import pytest
from fastapi import HTTPException

from vaibify.config import registryManager
from vaibify.config.connectionAvailability import fbDockerReachable
from vaibify.gui import dockerStatus, pipelineServer
from vaibify.gui.connectionRouter import (
    ConnectionRouter,
    DockerLegUnavailableError,
)

S_HOST_PROJECT_NAME = "routed-host-proj"
S_DOCKER_CONTAINER_ID = "abc123dockerid"


@pytest.fixture(autouse=True)
def fixtureIsolateRegistry(tmp_path, monkeypatch):
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
    registryManager.fnSaveRegistry({"listProjects": [{
        "sName": S_HOST_PROJECT_NAME,
        "sDirectory": str(tmp_path / "hostproj"),
        "sMode": "host",
    }]})


class _RecordingLeg:
    """Records every routed call as (method, resource id)."""

    def __init__(self, sLegName):
        self.sLegName = sLegName
        self.listCalls = []

    def ftResultExecuteCommand(self, sContainerId, sCommand, **dictKw):
        self.listCalls.append(("ftResultExecuteCommand", sContainerId))
        return (0, self.sLegName)

    def fbaFetchFile(self, sContainerId, sFilePath, **dictKw):
        self.listCalls.append(("fbaFetchFile", sContainerId))
        return self.sLegName.encode("utf-8")

    def flistGetRunningContainers(self):
        self.listCalls.append(("flistGetRunningContainers", None))
        return []


class _PoisonLeg:
    """Raises on any use — proves a dispatch never touched this leg."""

    def __getattr__(self, sAttributeName):
        raise AssertionError(
            f"the wrong leg was consulted (.{sAttributeName})"
        )


class TestRouterDispatch:

    def test_host_project_routes_to_the_host_leg(self):
        router = ConnectionRouter(_PoisonLeg(), _RecordingLeg("host"))
        iExit, sOutput = router.ftResultExecuteCommand(
            S_HOST_PROJECT_NAME, "echo hi",
        )
        assert sOutput == "host"
        assert router.connectionHostLeg.listCalls == [
            ("ftResultExecuteCommand", S_HOST_PROJECT_NAME),
        ]

    def test_container_id_routes_to_the_docker_leg(self):
        router = ConnectionRouter(_RecordingLeg("docker"), _PoisonLeg())
        baContent = router.fbaFetchFile(
            S_DOCKER_CONTAINER_ID, "/workspace/file.txt",
        )
        assert baContent == b"docker"
        assert router.connectionDockerLeg.listCalls == [
            ("fbaFetchFile", S_DOCKER_CONTAINER_ID),
        ]

    def test_host_leg_serves_without_any_docker_leg(self):
        router = ConnectionRouter(None, _RecordingLeg("host"))
        assert router.ftResultExecuteCommand(
            S_HOST_PROJECT_NAME, "echo hi",
        ) == (0, "host")

    def test_docker_lane_without_a_leg_raises_the_typed_error(self):
        router = ConnectionRouter(None, _PoisonLeg())
        with pytest.raises(DockerLegUnavailableError):
            router.ftResultExecuteCommand(S_DOCKER_CONTAINER_ID, "id")
        with pytest.raises(DockerLegUnavailableError):
            router.flistGetRunningContainers()

    def test_docker_only_surface_delegates_verbatim(self):
        legDocker = _RecordingLeg("docker")
        router = ConnectionRouter(legDocker, _PoisonLeg())
        assert router.flistGetRunningContainers() == []
        assert legDocker.listCalls == [
            ("flistGetRunningContainers", None),
        ]


class TestDockerReachablePredicate:

    def test_none_reads_unreachable(self):
        assert fbDockerReachable(None) is False

    def test_plain_connection_reads_reachable(self):
        assert fbDockerReachable(_RecordingLeg("docker")) is True

    def test_router_answers_for_its_docker_leg(self):
        assert fbDockerReachable(
            ConnectionRouter(_RecordingLeg("docker"), _PoisonLeg()),
        ) is True
        assert fbDockerReachable(
            ConnectionRouter(None, _PoisonLeg()),
        ) is False


class TestFactoryAndRetryWiring:

    def test_routed_factory_honors_the_patched_symbol(self, monkeypatch):
        """The browser-lane seam: the patched fake becomes the leg."""
        legFake = _RecordingLeg("fake")
        monkeypatch.setattr(
            pipelineServer, "_fconnectionCreateDocker", lambda: legFake,
        )
        router = pipelineServer.fconnectionBuildRouted()
        assert router.connectionDockerLeg is legFake
        assert router.fbDockerLegPresent()

    def test_retry_swaps_the_leg_in_place(self, monkeypatch):
        """A daemon retry must not replace the router object itself —
        route closures hold ``dictCtx`` whose value they re-read, but
        the router's identity is what carries the host leg."""
        legNew = _RecordingLeg("recovered")
        monkeypatch.setattr(
            pipelineServer, "_fconnectionCreateDocker", lambda: legNew,
        )
        router = ConnectionRouter(None, _PoisonLeg())
        dictCtx = {"docker": router}
        dockerStatus.fdictRetryDockerConnection(dictCtx)
        assert dictCtx["docker"] is router
        assert router.connectionDockerLeg is legNew

    def test_retry_keeps_legacy_behavior_for_a_bare_connection(
        self, monkeypatch,
    ):
        legNew = _RecordingLeg("recovered")
        monkeypatch.setattr(
            pipelineServer, "_fconnectionCreateDocker", lambda: legNew,
        )
        dictCtx = {"docker": None}
        dockerStatus.fdictRetryDockerConnection(dictCtx)
        assert dictCtx["docker"] is legNew


class TestRequireDockerHostBypass:

    def test_host_resource_needs_no_daemon(self):
        dockerStatus._fnRequireDocker(None, sResourceId=S_HOST_PROJECT_NAME)

    def test_container_resource_still_503s_without_a_daemon(self):
        with pytest.raises(HTTPException) as excInfo:
            dockerStatus._fnRequireDocker(
                None, sResourceId=S_DOCKER_CONTAINER_ID,
            )
        assert excInfo.value.status_code == 503

    def test_bare_call_keeps_the_historical_contract(self):
        with pytest.raises(HTTPException):
            dockerStatus._fnRequireDocker(None)
        dockerStatus._fnRequireDocker(_RecordingLeg("docker"))

    def test_legless_router_reads_unreachable_for_containers(self):
        with pytest.raises(HTTPException):
            dockerStatus._fnRequireDocker(
                ConnectionRouter(None, _PoisonLeg()),
                sResourceId=S_DOCKER_CONTAINER_ID,
            )


class TestNameResolverHostBranch:

    def test_host_name_resolves_to_itself_without_touching_docker(self):
        assert pipelineServer.fsContainerNameForId(
            _PoisonLeg(), S_HOST_PROJECT_NAME,
        ) == S_HOST_PROJECT_NAME
