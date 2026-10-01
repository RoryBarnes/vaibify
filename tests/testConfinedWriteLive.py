"""The confined container write, against a real daemon and a real container.

The unit tests drive the write program in a subprocess behind a fake
daemon; only a real container proves the transport (a hijacked exec socket
that must carry the payload on stdin and then half-close it), the exec
user, and the symlink-swap race against a concurrently running swapper.

Skipped when no daemon answers, unless ``VAIBIFY_REQUIRE_DOCKER_DAEMON``
demands one (see ``tests/testDockerConnectionLive.py``).
"""

import os
import stat
import time

import pytest

from tests.liveContainerLabels import fdictLabels
from tests.testDockerConnectionLive import fnRequireDaemonReachable
from vaibify.docker.confinedWrite import (
    ContainerWriteRefusedError, T_WRITE_DENYLISTED_NAMES,
)

pytestmark = pytest.mark.docker_live

S_IMAGE = "python:3.10-slim"
S_USER = "researcher"
S_PROJECT = "/home/researcher/project"
S_OUTSIDE = "/home/researcher/outside"


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
            f"mkdir -p {S_PROJECT}/docs {S_OUTSIDE}",
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


def testALargeSecretIsWrittenIntactPrivateAndOwnedByTheUser(liveContainer):
    container, connection = liveContainer
    baPayload = os.urandom(1 << 20) * 5
    sTarget = f"{S_PROJECT}/docs/secret.bin"
    connection.fnWriteFile(container.id, sTarget, baPayload, iMode=0o600)
    iExit, sStat = _fsRunAsUser(container, f"stat -c '%a %U %s' {sTarget}")
    assert iExit == 0
    assert sStat.split() == ["600", S_USER, str(len(baPayload))]
    baReadBack = connection.fbaFetchFile(container.id, sTarget)
    assert baReadBack == baPayload


def testASymlinkedParentIsRefusedByTheRealProgram(liveContainer):
    container, connection = liveContainer
    iExit, _ = _fsRunAsUser(
        container,
        f"ln -sfn {S_OUTSIDE} {S_PROJECT}/linked && ls {S_OUTSIDE}")
    assert iExit == 0
    with pytest.raises(ContainerWriteRefusedError):
        connection.fnWriteFile(
            container.id, f"{S_PROJECT}/linked/planted.txt", b"x",
            sAuthorizedRoot=S_PROJECT,
            tForbiddenNames=T_WRITE_DENYLISTED_NAMES)
    iExit, sListing = _fsRunAsUser(container, f"ls -A {S_OUTSIDE}")
    assert sListing.strip() == ""


def testASymlinkIntoGitInternalsIsRefusedByTheRealProgram(liveContainer):
    container, connection = liveContainer
    iExit, _ = _fsRunAsUser(
        container,
        f"mkdir -p {S_PROJECT}/.git/hooks && "
        f"ln -sfn {S_PROJECT}/.git {S_PROJECT}/docs/shortcut")
    assert iExit == 0
    with pytest.raises(ContainerWriteRefusedError):
        connection.fnWriteFile(
            container.id, f"{S_PROJECT}/docs/shortcut/hooks/pre-commit",
            b"#!/bin/sh\n", sAuthorizedRoot=S_PROJECT,
            tForbiddenNames=T_WRITE_DENYLISTED_NAMES)
    iExit, sListing = _fsRunAsUser(container, f"ls -A {S_PROJECT}/.git/hooks")
    assert sListing.strip() == ""


def testNothingEverLandsOutsideWhileADirectoryIsSwappedForASymlink(
    liveContainer,
):
    """The race, live: a swapper flips a parent between directory and link.

    Every write either lands inside the project, is refused, or fails
    because the parent momentarily did not exist; none may appear in the
    directory the link points at.
    """
    container, connection = liveContainer
    _fsRunAsUser(container, f"rm -rf {S_PROJECT}/racing {S_OUTSIDE}/* ; "
                 f"mkdir -p {S_PROJECT}/racing")
    sSwapper = (
        f"cd {S_PROJECT}; while true; do "
        f"mv racing racing.kept 2>/dev/null; ln -s {S_OUTSIDE} racing; "
        f"rm racing; mv racing.kept racing; done"
    )
    container.exec_run(["sh", "-c", sSwapper], user=S_USER, detach=True)
    dictOutcomes = {"landed": 0, "refused": 0, "failed": 0}
    fDeadline = time.monotonic() + 20
    iAttempt = 0
    while time.monotonic() < fDeadline and iAttempt < 150:
        iAttempt += 1
        try:
            connection.fnWriteFile(
                container.id, f"{S_PROJECT}/racing/note{iAttempt}.txt",
                b"payload", sAuthorizedRoot=S_PROJECT,
                tForbiddenNames=T_WRITE_DENYLISTED_NAMES)
            dictOutcomes["landed"] += 1
        except ContainerWriteRefusedError:
            dictOutcomes["refused"] += 1
        except OSError:
            dictOutcomes["failed"] += 1
    container.exec_run(["pkill", "-f", "mv racing"], user=S_USER)
    _fsRunAsUser(container, "pkill -f 'while true' ; true")
    iExit, sListing = _fsRunAsUser(container, f"ls -A {S_OUTSIDE}")
    assert sListing.strip() == "", (dictOutcomes, sListing)
    assert sum(dictOutcomes.values()) == iAttempt
