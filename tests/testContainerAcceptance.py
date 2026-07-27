"""Lane 2: the same questions Lane 1 asks a fake, asked of a container.

Lane 1's Docker adapter models three commands. This file is what makes
that contract real: each assertion named in
``fakeDockerAdapter.LIST_MODELLED_COMMANDS`` exists here and puts the
same question to a live container, so a fake that drifts from the
daemon is caught rather than believed.

These are the only tests that speak for the host-to-Docker boundary:
container launch, the real transport, and file ownership on write. Lane
1 says nothing about any of it.

Running them needs a real container:

    export VAIBIFY_ACCEPTANCE_CONTAINER=<name or id>
    python -m pytest tests/testContainerAcceptance.py -m docker

Unset, they skip -- unless ``VAIBIFY_REQUIRE_DOCKER_DAEMON`` is set,
which turns the skip into a failure so the nightly job cannot report
success for having run nothing.
"""

import os

import pytest

from tests.testDockerConnectionLive import S_REQUIRE_DAEMON_ENV


pytestmark = pytest.mark.docker

S_ACCEPTANCE_CONTAINER_ENV = "VAIBIFY_ACCEPTANCE_CONTAINER"


def _fsRequireAcceptanceContainer():
    """Return the container to drive, or skip/fail per the run's demand."""
    sContainer = os.environ.get(S_ACCEPTANCE_CONTAINER_ENV, "")
    if sContainer:
        return sContainer
    if os.environ.get(S_REQUIRE_DAEMON_ENV):
        pytest.fail(
            f"{S_ACCEPTANCE_CONTAINER_ENV} is unset while "
            f"{S_REQUIRE_DAEMON_ENV} demands live coverage. A lane "
            "that skips itself green is the failure this exists to "
            "prevent."
        )
    pytest.skip(f"{S_ACCEPTANCE_CONTAINER_ENV} not set")


def _fconnectionOpen():
    """Return a real DockerConnection, or skip when no daemon answers."""
    from tests.testDockerConnectionLive import fnRequireDaemonReachable
    fnRequireDaemonReachable()
    from vaibify.docker.dockerConnection import DockerConnection
    return DockerConnection()


def testRealContainerDetectsProjectRepo():
    """`git rev-parse --show-toplevel` answers with a repo path.

    Lane 1's fake returns a fixed path for this command. If a real
    container answers differently -- non-zero, empty, or an error
    string -- every Lane 1 journey that depends on project-repo
    detection is testing a fiction.
    """
    sContainer = _fsRequireAcceptanceContainer()
    connection = _fconnectionOpen()
    iCode, sOutput = connection.ftResultExecuteCommand(
        sContainer,
        "cd /workspace && git rev-parse --show-toplevel",
    )
    assert iCode == 0, f"exit {iCode}: {sOutput!r}"
    assert sOutput.strip().startswith("/"), (
        f"Not an absolute repo path: {sOutput!r}"
    )


def testRealContainerListsWorkflows():
    """The workflow-discovery find must run and exit cleanly.

    An empty result is acceptable -- a container need not host a
    workflow -- but the command must execute, which is what the fake
    asserts by returning exit 0.
    """
    sContainer = _fsRequireAcceptanceContainer()
    connection = _fconnectionOpen()
    iCode, sOutput = connection.ftResultExecuteCommand(
        sContainer,
        "find /workspace -maxdepth 4 -path '*/.vaibify/workflows/*' "
        "-name '*.json' 2>/dev/null || true",
    )
    assert iCode == 0, f"exit {iCode}: {sOutput!r}"


def testRealContainerHasNoPipelineStateYet():
    """Reading an absent pipeline_state must fail, not fabricate.

    Lane 1's fake returns exit 1 with empty output for this. The
    property that matters is that a missing file is reported as
    missing -- a container that answered 0 with empty output would
    make "no state" indistinguishable from "empty state".
    """
    sContainer = _fsRequireAcceptanceContainer()
    connection = _fconnectionOpen()
    iCode, _sOutput = connection.ftResultExecuteCommand(
        sContainer,
        "cat /workspace/.vaibify/pipeline_state_does_not_exist.json",
    )
    assert iCode != 0, (
        "A missing pipeline-state file was reported as readable."
    )
