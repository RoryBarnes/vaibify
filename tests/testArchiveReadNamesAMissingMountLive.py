"""A deleted managed secret, and the archive reads out of its container.

On 2026-10-30 a Level 3 verification failed with "Cannot read directory
from container ... Could not find the file" over an intact directory.
The host file behind the container's ``--mount`` secret had been
deleted, and on Colima 0.10.3 (virtiofs) with Docker Engine 28.4.0
every ``get_archive`` out of such a container then fails while ``exec``
keeps working. This lane reproduces the setup against the real daemon.

Two things are required on every runtime: a read succeeds before the
unlink, and the connection detects the missing managed secret after it,
independently of any archive read. What archive reads do after the
unlink depends on the runtime, so the branch reached is recorded on
every run (``sArchiveBranch``) and REQUIRED only for the measured
configuration: a later Colima or Engine release that fixes the behavior
must not read as a vaibify regression.
"""

import json
import os
import subprocess
import time

import pytest

from tests.liveContainerLabels import fdictLabels
from tests.testDockerConnectionLive import fnRequireDaemonReachable
from vaibify.config import secretManager
from vaibify.docker import dockerContext

pytestmark = pytest.mark.docker_live

S_IMAGE = "alpine:3.20"
S_SECRET_TARGET = "/run/secrets/archiveProbe"
S_PROBE_PATH = "/etc/hostname"
I_ARCHIVE_BYTE_CAP = 1024 * 1024
F_POLL_SECONDS = 30.0
S_BRANCH_EXPLAINED = "reads failed and were explained"
S_BRANCH_UNEXPLAINED = "reads failed without the explanation"
S_BRANCH_SUCCEEDED = "reads still succeed"

# (Colima version, Colima mount type, Docker Engine version) under which
# every archive read failed once a --mount source was deleted. Measured
# on macOS on 2026-10-30. Add a row only where the failure is observed.
T_MEASURED_ARCHIVE_FAILURE = ("0.10.3", "virtiofs", "28.4.0")


def _fnEnsureImage(clientDocker):
    import docker
    try:
        clientDocker.images.get(S_IMAGE)
    except docker.errors.ImageNotFound:
        clientDocker.images.pull(S_IMAGE)


@pytest.fixture
def tContainerWithManagedSecret():
    """Yield (connection, container id, secret path, client); always clean up."""
    fnRequireDaemonReachable()
    import docker
    from docker.types import Mount
    from vaibify.docker.dockerConnection import (
        DockerConnection, _fnEnsureDockerHost,
    )
    _fnEnsureDockerHost()
    clientDocker = docker.from_env()
    _fnEnsureImage(clientDocker)
    sSecret = secretManager.fsMaterializeSecretValue(
        "archiveProbe", "probe-value")
    container = clientDocker.containers.run(
        S_IMAGE, ["sleep", "300"], detach=True, labels=fdictLabels(),
        mounts=[Mount(S_SECRET_TARGET, sSecret, type="bind", read_only=True)],
    )
    try:
        yield DockerConnection(), container.id, sSecret, clientDocker
    finally:
        container.remove(force=True)
        if os.path.lexists(sSecret):
            os.unlink(sSecret)


def _fsColimaMountType():
    """Return the active Colima profile's mount type, or '' when unknown."""
    sProfile = dockerContext.fsColimaProfileName()
    if not sProfile:
        return ""
    try:
        resultStatus = subprocess.run(
            ["colima", "status", "--profile", sProfile, "--json"],
            capture_output=True, text=True, timeout=30,
        )
        return str(json.loads(resultStatus.stdout).get("mount_type", ""))
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""


def _ftReadConfiguration(clientDocker):
    """Return (Colima version, mount type, Engine version); '' where unknown."""
    sColimaVersion = ".".join(str(i) for i in dockerContext.ftColimaVersion())
    sEngineVersion = str(clientDocker.version().get("Version", ""))
    return (sColimaVersion, _fsColimaMountType(), sEngineVersion)


def _ftPollTheArchiveBranch(connection, sContainerId, sSecret):
    """Read once a second for up to 30 s; return (branch, failure text)."""
    fDeadline = time.monotonic() + F_POLL_SECONDS
    while True:
        try:
            connection.fbaFetchDirectoryArchive(
                sContainerId, S_PROBE_PATH, I_ARCHIVE_BYTE_CAP)
        except FileNotFoundError as error:
            sMessage = str(error)
            bExplained = (f"is missing ({S_SECRET_TARGET})," in sMessage
                          and sSecret not in sMessage)
            return (S_BRANCH_EXPLAINED if bExplained
                    else S_BRANCH_UNEXPLAINED), sMessage
        if time.monotonic() >= fDeadline:
            return S_BRANCH_SUCCEEDED, ""
        time.sleep(1.0)


def testADeletedManagedSecretIsDetectedAndItsArchiveBranchRecorded(
    tContainerWithManagedSecret, record_property,
):
    """Baseline and detection always; the failure branch where measured."""
    connection, sContainerId, sSecret, clientDocker = (
        tContainerWithManagedSecret)
    connection.fbaFetchDirectoryArchive(
        sContainerId, S_SECRET_TARGET, I_ARCHIVE_BYTE_CAP)
    os.unlink(sSecret)
    dictMounts = connection.fdictInspectBindMountSources(sContainerId)
    assert dictMounts["listMissingSecretTargets"] == [S_SECRET_TARGET]
    sBranch, sMessage = _ftPollTheArchiveBranch(
        connection, sContainerId, sSecret)
    tConfiguration = _ftReadConfiguration(clientDocker)
    record_property("sArchiveBranch", sBranch)
    record_property("tConfiguration", repr(tConfiguration))
    print(f"sArchiveBranch: {sBranch} under {tConfiguration}")
    assert sBranch != S_BRANCH_UNEXPLAINED, sMessage
    if tConfiguration == T_MEASURED_ARCHIVE_FAILURE:
        assert sBranch == S_BRANCH_EXPLAINED, (
            f"under the measured configuration {tConfiguration} every "
            "archive read failed once the mount source was deleted; here "
            "reads still succeed, so re-measure before trusting the "
            "diagnosis on this runtime")
