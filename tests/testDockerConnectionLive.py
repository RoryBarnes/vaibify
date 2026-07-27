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

That job sets ``VAIBIFY_REQUIRE_DOCKER_DAEMON``, which turns the
convenience skip into a failure. A job advertised as live-daemon
coverage that reports success *without* a daemon is a false green —
the same dishonesty as the ``docker info || exit 0`` guard it
replaced, and the same class of defect as a dashboard that hides an
error.
"""

import os

import pytest


pytestmark = pytest.mark.docker_live

S_REQUIRE_DAEMON_ENV = "VAIBIFY_REQUIRE_DOCKER_DAEMON"


def _fbDaemonReachable():
    """Return True iff a Docker daemon answers a cheap ping."""
    try:
        import docker
        clientDocker = docker.from_env()
        clientDocker.ping()
        return True
    except Exception:
        return False


S_OUTCOME_PROCEED = "proceed"
S_OUTCOME_SKIP = "skip"
S_OUTCOME_FAIL = "fail"


def fsDaemonRequirementOutcome(bReachable, bDemanded):
    """Return what an unreachable daemon should do to this run.

    Kept separate from the pytest call so the decision can be asserted
    directly. Testing it only through ``pytest.skip`` /``pytest.fail``
    would make a broken guard surface as a SKIPPED test, and a skip is
    not a failure -- the falsification harness would score the mutant
    as surviving when the guard is exactly what it must catch.
    """
    if bReachable:
        return S_OUTCOME_PROCEED
    if bDemanded:
        return S_OUTCOME_FAIL
    return S_OUTCOME_SKIP


def fnRequireDaemonReachable():
    """Skip when no daemon answers, unless the run demanded one."""
    sOutcome = fsDaemonRequirementOutcome(
        _fbDaemonReachable(),
        bool(os.environ.get(S_REQUIRE_DAEMON_ENV)),
    )
    if sOutcome == S_OUTCOME_PROCEED:
        return
    if sOutcome == S_OUTCOME_FAIL:
        pytest.fail(
            "No Docker daemon reachable, but "
            f"{S_REQUIRE_DAEMON_ENV} is set: this run was required to "
            "exercise the live adapter, so skipping would report a "
            "false green."
        )
    pytest.skip("no docker daemon reachable")


def test_running_containers_listed_through_real_daemon():
    """A live container-list call must succeed end-to-end.

    Was previously broken by a pool-tune commit that clobbered
    docker-py's UnixHTTPAdapter with a vanilla HTTPAdapter. The
    mocked tests passed; this one would not have.
    """
    fnRequireDaemonReachable()
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
    fnRequireDaemonReachable()
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
