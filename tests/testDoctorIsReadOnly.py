"""``vaibify doctor`` observes; it must never mutate what it inspects.

The defect this file pins shipped: the start-scope pre-flight called
``fnRemoveStopped`` -- a ``docker rm`` -- so running the diagnostic
against a container that had stopped unexpectedly deleted that
container's writable layer and its ``HostConfig``. The researcher who
ran doctor to find out WHY a container stopped destroyed the evidence
by asking the question, and had to reproduce the failure to look at it.

Two lanes, because neither alone is enough. The unit lane proves the
check composes no removal call; the live lane proves it against a real
daemon by asking the daemon whether the container id is still there,
which is the only witness that cannot be satisfied by a stub.
"""

import subprocess
import uuid
from unittest.mock import patch

import pytest

from vaibify.cli.commandDoctor import flistRunDoctorChecks


def _fconfigStubbedProject():
    """Return a minimal config object the start-scope checks accept."""
    class _ConfigStub:
        sProjectName = "doctorReadOnlyProbe"
        listPorts = []
        listBindMounts = []
        listSecrets = []
        sWorkspaceRoot = "/workspace"
    return _ConfigStub()


@pytest.mark.falsification
def test_doctor_removes_nothing_when_the_container_is_stopped():
    """Every doctor check runs, and no removal is composed.

    Kills: In _fpreflightContainerName, restore the fnRemoveStopped
    call before returning the stale-container result, so the check
    deletes the container it was asked about.
    """
    with patch(
        "vaibify.docker.containerManager.fdictGetContainerStatus",
        return_value={
            "bExists": True, "bRunning": False, "sStatus": "exited",
        },
    ), patch(
        "vaibify.docker.containerManager.fnRemoveStopped",
    ) as mockRemove, patch(
        "vaibify.cli.commandDoctor._fdictHostProjectOrNone",
        return_value=None,
    ), patch(
        "vaibify.cli.commandDoctor._flistSharedChecks", return_value=[],
    ):
        listResults = flistRunDoctorChecks(
            _fconfigStubbedProject(), False, True,
        )
    mockRemove.assert_not_called()
    assert any(r.sName == "container-name" for r in listResults)


def test_no_doctor_check_composes_a_mutating_docker_command():
    """A container-name check never reaches a mutating docker verb.

    Asserted at the subprocess boundary rather than by reading the
    source, so a removal reintroduced through any spelling -- the SDK,
    a helper, a second module -- is still caught.
    """
    from vaibify.cli.commandStart import _fpreflightContainerName
    listCommands = []

    def _fnRecordCommand(saCommand, *args, **kwargs):
        listCommands.append(list(saCommand))
        raise AssertionError(
            f"doctor composed a docker command: {saCommand}"
        )

    with patch(
        "vaibify.docker.containerManager.fdictGetContainerStatus",
        return_value={
            "bExists": True, "bRunning": False, "sStatus": "exited",
        },
    ), patch(
        "vaibify.docker.containerManager.subprocess.run", _fnRecordCommand,
    ):
        _fpreflightContainerName(_fconfigStubbedProject())
    assert listCommands == []


@pytest.mark.docker_live
def test_a_stopped_container_survives_the_diagnostic():
    """The live witness: the container id still resolves afterwards."""
    from tests.testDockerConnectionLive import fnRequireDaemonReachable
    fnRequireDaemonReachable()
    sName = "vaibifyDoctorProbe" + uuid.uuid4().hex[:8]
    sCreated = subprocess.run(
        ["docker", "create", "--name", sName, "ubuntu:24.04", "true"],
        capture_output=True, text=True,
    )
    if sCreated.returncode != 0:
        pytest.skip(f"could not create a probe container: {sCreated.stderr}")
    sContainerId = sCreated.stdout.strip()
    try:
        class _ConfigStub:
            sProjectName = sName
            listPorts = []
            listBindMounts = []
            listSecrets = []
            sWorkspaceRoot = "/workspace"

        from vaibify.cli.commandStart import _fpreflightContainerName
        resultCheck = _fpreflightContainerName(_ConfigStub())
        assert resultCheck.sLevel == "warn"
        processInspect = subprocess.run(
            ["docker", "inspect", "-f", "{{.Id}}", sContainerId],
            capture_output=True, text=True,
        )
        assert processInspect.returncode == 0, (
            "the diagnostic destroyed the container it was asked about"
        )
        assert processInspect.stdout.strip() == sContainerId
    finally:
        subprocess.run(
            ["docker", "rm", "-f", sName],
            capture_output=True, text=True,
        )
