"""End-to-end smoke test against a real Docker daemon.

Catches regressions in the docker-py transport wiring that the
fully-mocked tests cannot see — most recently, a pool-tune commit
that replaced docker-py's ``UnixHTTPAdapter`` with a vanilla
``requests.HTTPAdapter``. The mocks accepted any subsequent attribute
access; against a real daemon every container call raised
``URLSchemeUnknown: http+docker``.

Skipped automatically when no daemon is reachable so local-dev and
docker-less CI runners do not fail. The CI job that opts in runs
``pytest -m docker_live`` on Ubuntu, where the GitHub Actions runner
ships with Docker pre-installed and running.
"""

import pytest


pytestmark = pytest.mark.docker_live


def _fbDaemonReachable():
    """Return True iff a Docker daemon answers a cheap ping."""
    try:
        import docker
        clientDocker = docker.from_env()
        clientDocker.ping()
        return True
    except Exception:
        return False


def test_running_containers_listed_through_real_daemon():
    """A live container-list call must succeed end-to-end.

    Was previously broken by a pool-tune commit that clobbered
    docker-py's UnixHTTPAdapter with a vanilla HTTPAdapter. The
    mocked tests passed; this one would not have.
    """
    if not _fbDaemonReachable():
        pytest.skip("no docker daemon reachable")
    from vaibify.docker.dockerConnection import DockerConnection
    connection = DockerConnection()
    listContainers = connection.flistGetRunningContainers()
    assert isinstance(listContainers, list)


def test_unix_socket_adapter_preserved_after_pool_tune():
    """The http+docker:// adapter must remain a docker-py UnixHTTPAdapter.

    The bug class this catches: anyone who later edits
    ``_fnTuneDockerSessionPool`` and mounts the wrong adapter class
    at ``http+docker://`` breaks every unix-socket call. Asserting
    the class identity at the adapter slot makes that immediate.
    """
    if not _fbDaemonReachable():
        pytest.skip("no docker daemon reachable")
    from docker.transport.unixconn import UnixHTTPAdapter
    from vaibify.docker.dockerConnection import (
        DockerConnection, I_DOCKER_POOL_MAX_SIZE,
    )
    connection = DockerConnection()
    dictAdapters = connection._clientDocker.api.adapters
    adapterUnix = dictAdapters.get("http+docker://")
    if adapterUnix is None:
        pytest.skip("daemon reached over TCP, not unix socket")
    assert isinstance(adapterUnix, UnixHTTPAdapter)
    iMaxPool = getattr(adapterUnix, "max_pool_size", 0)
    assert iMaxPool == I_DOCKER_POOL_MAX_SIZE
