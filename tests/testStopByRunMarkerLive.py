"""Stop finds a run's processes by its marker, in a real container.

The marker sweep is shell run inside the container -- a walk of
``/proc``, each process's environment read and matched -- and nothing
short of a real process table shows that the quoting survives three
layers, that children inherit the marker, and that another project's
same-named process is left alone. Runs against a throwaway container
from a stock image, uniquely named, force-removed in teardown; skips
without a daemon, and ``VAIBIFY_REQUIRE_DOCKER_DAEMON`` turns that skip
into a failure.
"""

import secrets
import time

import pytest

from tests.liveContainerLabels import fdictLabels
from tests.testDockerConnectionLive import fnRequireDaemonReachable
from vaibify.gui.routes import pipelineRoutes

pytestmark = pytest.mark.docker_live

S_THROWAWAY_IMAGE = "ubuntu:24.04"


@pytest.fixture
def tLiveContainer():
    """Yield (container, sContainerId, connection) for a throwaway container."""
    fnRequireDaemonReachable()
    import docker
    from vaibify.docker.dockerConnection import DockerConnection
    clientDocker = docker.from_env()
    container = clientDocker.containers.run(
        S_THROWAWAY_IMAGE, ["sleep", "600"],
        name=f"vaibifyRunMarker{secrets.token_hex(4)}", detach=True,
        labels=fdictLabels(),
    )
    try:
        # The unprivileged user vaibify's own containers run as: the
        # sweep reads the environments of that user's processes, which
        # is exactly what a real run's processes are.
        container.exec_run(["useradd", "-m", "researcher"])
        yield (container, container.id, DockerConnection())
    finally:
        try:
            container.remove(force=True)
        except Exception:
            pass


def _fnStartDetached(container, sScript):
    container.exec_run(
        ["/bin/bash", "-c", sScript], detach=True, user="researcher",
    )


def _fbSleepIsAlive(container, iSeconds):
    iExit, _ = container.exec_run(
        ["/bin/bash", "-c", f"pgrep -fx 'sleep {iSeconds}'"],
    )
    return iExit == 0


def _fnWaitForSleeps(container, listSeconds):
    for _ in range(50):
        if all(_fbSleepIsAlive(container, i) for i in listSeconds):
            return
        time.sleep(0.1)
    raise AssertionError(f"processes never started: {listSeconds}")


@pytest.mark.falsification
def testStopKillsTheMarkedRunAndItsChildrenOnly(tLiveContainer):
    """The stopped run's process and its child die; the other run lives.

    Kills: matching the marker without anchoring it, so run 'bb22' also
    matches run 'bb223' -- another project's run dies with this one.
    """
    container, sContainerId, connectionDocker = tLiveContainer
    _fnStartDetached(
        container, "export VAIBIFY_RUN_ID=bb223 && exec sleep 401",
    )
    _fnStartDetached(
        container,
        "export VAIBIFY_RUN_ID=bb22 && sleep 402 & "
        "export VAIBIFY_RUN_ID=bb22 && exec sleep 403",
    )
    _fnWaitForSleeps(container, [401, 402, 403])
    iKilled = pipelineRoutes._fiKillRunProcesses(
        connectionDocker, sContainerId, "bb22",
    )
    time.sleep(0.3)
    assert iKilled >= 2
    assert not _fbSleepIsAlive(container, 402)
    assert not _fbSleepIsAlive(container, 403)
    assert _fbSleepIsAlive(container, 401), (
        "another project's run was killed by this project's Stop"
    )


def testANameSweepSparesAMarkedProcessWithTheSameName(tLiveContainer):
    """Two processes named alike: only the one carrying no live marker dies."""
    container, sContainerId, connectionDocker = tLiveContainer
    _fnStartDetached(
        container, "export VAIBIFY_RUN_ID=aa11 && exec sleep 404",
    )
    _fnWaitForSleeps(container, [404])
    _fnStartDetached(container, "exec sleep 404")
    time.sleep(0.3)
    iKilled = pipelineRoutes._fiKillMatchingProcessesSparingRuns(
        connectionDocker, sContainerId, "sleep 404", ["aa11"],
    )
    time.sleep(0.3)
    assert iKilled == 1
    assert _fbSleepIsAlive(container, 404)
    iExit, baOutput = container.exec_run(
        ["/bin/bash", "-c", "pgrep -fxc 'sleep 404'"],
    )
    assert baOutput.decode().strip() == "1"
