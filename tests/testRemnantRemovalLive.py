"""The panel's container removal, against a real daemon, end to end.

The remnant panel's headline destructive action is ``docker rm`` of a
stopped, untracked container. Every route test stubs that authority,
which is exactly the "stubs agree with each other" trap this repo has
shipped a fatal bug under, so this lane drives the real removal: a
stopped container the scanner would list is removed, the daemon no
longer lists it, and a NAMED volume it mounted is kept. A running
container is refused rather than killed.
"""

import secrets

import pytest

from vaibify.docker import containerManager
from vaibify.docker.dockerConnection import DockerConnection
from tests.liveContainerLabels import fdictLabels
from tests.testDockerConnectionLive import fnRequireDaemonReachable

pytestmark = pytest.mark.docker_live

S_IMAGE = "python:3.10-slim"


@pytest.fixture
def tStoppedContainerWithVolume():
    """Yield (sName, sId, sVolume) for a stopped container holding a volume."""
    fnRequireDaemonReachable()
    import docker
    clientDocker = docker.from_env()
    sName = f"vaibifyDisposable{secrets.token_hex(4)}"
    sVolume = f"vaibifyVol{secrets.token_hex(4)}"
    container = clientDocker.containers.run(
        S_IMAGE, ["sh", "-c", "true"], name=sName, detach=True,
        labels=fdictLabels(), volumes={sVolume: {"bind": "/data", "mode": "rw"}},
    )
    container.wait()
    try:
        yield (sName, container.id, sVolume, clientDocker)
    finally:
        for fnCleanup in (
            lambda: clientDocker.containers.get(container.id).remove(force=True),
            lambda: clientDocker.volumes.get(sVolume).remove(force=True),
        ):
            try:
                fnCleanup()
            except Exception:
                pass


def test_a_stopped_container_is_removed_and_its_named_volume_kept(
    tStoppedContainerWithVolume,
):
    sName, sContainerId, sVolume, clientDocker = tStoppedContainerWithVolume
    connectionDocker = DockerConnection()
    assert any(
        dictRow["sContainerId"] == sContainerId
        for dictRow in connectionDocker.flistListAllContainers()
    ), "the stopped container should be listed before removal"

    containerManager.fnRemoveStoppedContainerById(sContainerId)

    assert not any(
        dictRow["sContainerId"] == sContainerId
        for dictRow in connectionDocker.flistListAllContainers()
    ), "the container is gone from the daemon after removal"
    assert clientDocker.volumes.get(sVolume) is not None, (
        "a named volume the container mounted must survive the removal")


def test_a_running_container_is_refused_rather_than_killed():
    fnRequireDaemonReachable()
    import docker
    clientDocker = docker.from_env()
    sName = f"vaibifyDisposable{secrets.token_hex(4)}"
    container = clientDocker.containers.run(
        S_IMAGE, ["sh", "-c", "sleep 120"], name=sName, detach=True,
        labels=fdictLabels(),
    )
    try:
        with pytest.raises(RuntimeError):
            containerManager.fnRemoveStoppedContainerById(container.id)
        assert clientDocker.containers.get(container.id).status == "running", (
            "a running container must be refused, never force-removed")
    finally:
        try:
            container.remove(force=True)
        except Exception:
            pass
