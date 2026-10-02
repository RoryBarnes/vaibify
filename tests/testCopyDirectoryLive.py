"""``vaibify push`` of a directory, against a real daemon and a real container.

The unit tests read the archive handed to ``put_archive``; only a real
daemon extracts it, so only a real container shows where a directory
copied to a destination that does not exist yet actually LANDS, and who
owns it. ``docker cp ./inputData /workspace/renamedData`` creates
``renamedData``; the copy used to create ``inputData`` instead.

Skipped when no daemon answers, unless ``VAIBIFY_REQUIRE_DOCKER_DAEMON``
demands one (see ``tests/testDockerConnectionLive.py``).
"""

import os

import pytest

from tests.liveContainerLabels import fdictLabels
from tests.testDockerConnectionLive import fnRequireDaemonReachable

pytestmark = pytest.mark.docker_live

S_IMAGE = "python:3.10-slim"
S_USER = "researcher"
S_PROJECT = "/home/researcher/project"


@pytest.fixture(scope="module")
def liveContainer():
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
    )
    try:
        for sSetup in (
            f"useradd -m {S_USER}",
            f"mkdir -p {S_PROJECT}/existing",
            f"chown -R {S_USER} /home/{S_USER}",
        ):
            iExit, _ = container.exec_run(["sh", "-c", sSetup], user="root")
            assert iExit == 0, sSetup
        yield container, DockerConnection()
    finally:
        container.remove(force=True)


def _fsRunAsUser(container, sCommand):
    iExit, baOutput = container.exec_run(
        ["sh", "-c", sCommand], user=S_USER)
    return iExit, baOutput.decode("utf-8", errors="replace")


@pytest.fixture
def pathHostTree(tmp_path):
    pathTree = tmp_path / "inputData"
    (pathTree / "nested").mkdir(parents=True)
    (pathTree / "dataFile.csv").write_bytes(b"1,2,3\n")
    (pathTree / "nested" / "notes.txt").write_bytes(b"notes")
    return pathTree


def testADirectoryCopiedToANewPathLandsAtThatPathOwnedByTheUser(
    liveContainer, pathHostTree,
):
    container, connection = liveContainer
    sDestination = f"{S_PROJECT}/renamedData"
    connection.fnCopyHostPathIntoContainer(
        container.id, str(pathHostTree), sDestination)
    iExit, sListing = _fsRunAsUser(
        container,
        f"cat {sDestination}/dataFile.csv; "
        f"cat {sDestination}/nested/notes.txt; echo; "
        f"stat -c '%U' {sDestination}/dataFile.csv")
    assert iExit == 0, sListing
    assert sListing.split() == ["1,2,3", "notes", S_USER]
    iExit, _ = _fsRunAsUser(container, f"test -e {S_PROJECT}/inputData")
    assert iExit != 0, (
        "the directory also landed under its source name, so the "
        "destination was not what was written")


def testADirectoryCopiedIntoAnExistingDirectoryKeepsItsOwnName(
    liveContainer, pathHostTree,
):
    container, connection = liveContainer
    connection.fnCopyHostPathIntoContainer(
        container.id, str(pathHostTree), f"{S_PROJECT}/existing")
    iExit, sListing = _fsRunAsUser(
        container, f"cat {S_PROJECT}/existing/inputData/dataFile.csv")
    assert iExit == 0, sListing
    assert sListing.strip() == "1,2,3"
    assert os.path.isdir(str(pathHostTree))
