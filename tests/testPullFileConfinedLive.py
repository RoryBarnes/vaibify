"""A pull reads the container through the confined reader, against a real daemon.

Source: ``vaibify/gui/routes/fileRoutes.py`` (``_fsPullContainerFileToHost``).

The pull used to stream through the daemon's ``get_archive``, which
reads as root and follows a symlink a container process planted after
the host's path check. It now reads through the confined reader as the
container user and refuses a swapped component; the regular file still
lands on the host through the confined writer.

Live-daemon convention (``testDockerConnectionLive``): skipped without
a daemon unless ``VAIBIFY_REQUIRE_DOCKER_DAEMON`` demands one.
"""

import os
import secrets

import pytest

from tests.liveContainerLabels import fdictLabels
from tests.testDockerConnectionLive import fnRequireDaemonReachable
from vaibify.docker.confinedRead import ContainerReadRefusedError

pytestmark = pytest.mark.docker_live

S_IMAGE = "python:3.10-slim"
S_USER = "researcher"
S_PROJECT = "/home/researcher/project"
S_OUTSIDE = "/home/researcher/outside"


@pytest.fixture(scope="module")
def tLiveContainer():
    fnRequireDaemonReachable()
    import docker
    from vaibify.docker.dockerConnection import (
        DockerConnection, _fnEnsureDockerHost,
    )
    _fnEnsureDockerHost()
    clientDocker = docker.from_env()
    try:
        clientDocker.images.get(S_IMAGE)
    except docker.errors.ImageNotFound:
        clientDocker.images.pull(S_IMAGE)
    container = clientDocker.containers.run(
        S_IMAGE, ["sleep", "600"], detach=True, labels=fdictLabels(),
        name=f"vaibifyPullConfined{secrets.token_hex(4)}",
    )
    try:
        for sSetup in (
            f"useradd -m -u 1000 {S_USER} && chmod 750 /home/{S_USER}",
            f"mkdir -p {S_PROJECT}/results {S_OUTSIDE} && "
            f"printf 'private' > {S_OUTSIDE}/secret.txt && "
            f"printf 'measured' > {S_PROJECT}/results/data.txt && "
            f"ln -s {S_OUTSIDE} {S_PROJECT}/swapped && "
            f"chown -R {S_USER}:{S_USER} /home/{S_USER}",
        ):
            iExit, baOutput = container.exec_run(["sh", "-c", sSetup], user="root")
            assert iExit == 0, (sSetup, baOutput)
        yield container, DockerConnection()
    finally:
        container.remove(force=True)


def testARegularFileLandsOnTheHost(tLiveContainer, tmp_path):
    from vaibify.gui.routes.fileRoutes import _fsPullContainerFileToHost
    container, connection = tLiveContainer
    sLanded = _fsPullContainerFileToHost(
        connection, container.id, S_PROJECT + "/results/data.txt",
        str(tmp_path), sAuthorizedRoot=S_PROJECT,
    )
    assert os.path.basename(sLanded) == "data.txt"
    with open(sLanded, "rb") as fileLanded:
        assert fileLanded.read() == b"measured"


def testASourceBehindASwappedSymlinkIsRefused(tLiveContainer, tmp_path):
    from vaibify.gui.routes.fileRoutes import _fsPullContainerFileToHost
    container, connection = tLiveContainer
    with pytest.raises(ContainerReadRefusedError):
        _fsPullContainerFileToHost(
            connection, container.id, S_PROJECT + "/swapped/secret.txt",
            str(tmp_path / "secret.txt"), sAuthorizedRoot=S_PROJECT,
        )
    assert sorted(os.listdir(str(tmp_path))) == []
