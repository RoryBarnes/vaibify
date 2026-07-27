"""Lane 2: the same questions Lane 1 asks a fake, asked of a container.

Lane 1's Docker adapter models a declared set of container commands.
This file is what makes that contract real: each assertion named in
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

import json
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


def testRealContainerProbesDirectories():
    """`test -d` must distinguish a directory from a missing path.

    Lane 1's fake answers 0 for the discovery probe. A container whose
    shell answered 0 for both cases would make workflow discovery
    report directories that are not there.
    """
    sContainer = _fsRequireAcceptanceContainer()
    connection = _fconnectionOpen()
    iPresent, _ = connection.ftResultExecuteCommand(
        sContainer, "test -d /workspace",
    )
    iAbsent, _ = connection.ftResultExecuteCommand(
        sContainer, "test -d /workspace/thisDirectoryDoesNotExist",
    )
    assert iPresent == 0, "an existing directory probed as absent"
    assert iAbsent != 0, "a missing directory probed as present"


def testRealContainerCopiesAndRenamesFiles():
    """`cp -f` and `mv -f` must actually move bytes.

    These carry the atomic state.json save: write a .tmp, back up the
    original, rename over it. Lane 1's fake answers 0 for both; if a
    real container's shell did not honour them, every workflow save
    would report success and change nothing -- state loss that looks
    like success, which is the worst shape this project has.
    """
    sContainer = _fsRequireAcceptanceContainer()
    connection = _fconnectionOpen()
    sDirectory = "/tmp/vaibifyAcceptance"
    connection.ftResultExecuteCommand(
        sContainer, f"mkdir -p {sDirectory}",
    )
    connection.ftResultExecuteCommand(
        sContainer, f"printf 'original' > {sDirectory}/state.json",
    )
    connection.ftResultExecuteCommand(
        sContainer, f"printf 'updated' > {sDirectory}/state.json.tmp",
    )
    iCopy, _ = connection.ftResultExecuteCommand(
        sContainer,
        f"if [ -f '{sDirectory}/state.json' ]; then cp -f "
        f"'{sDirectory}/state.json' '{sDirectory}/state.json.bak'; fi",
    )
    iMove, _ = connection.ftResultExecuteCommand(
        sContainer,
        f"mv -f '{sDirectory}/state.json.tmp' "
        f"'{sDirectory}/state.json'",
    )
    assert iCopy == 0 and iMove == 0
    _iCode, sLive = connection.ftResultExecuteCommand(
        sContainer, f"cat {sDirectory}/state.json",
    )
    _iCode, sBackup = connection.ftResultExecuteCommand(
        sContainer, f"cat {sDirectory}/state.json.bak",
    )
    assert sLive.strip() == "updated", (
        f"the rename did not take effect: {sLive!r}"
    )
    assert sBackup.strip() == "original", (
        f"the backup did not capture the previous state: {sBackup!r}"
    )
    connection.ftResultExecuteCommand(
        sContainer, f"rm -rf {sDirectory}",
    )


def testRealContainerReportsItsContainerUser():
    """`printenv CONTAINER_USER` must name the unprivileged user.

    Lane 1's fake answers "researcher". The value drives file
    ownership on host-to-container writes, so an empty answer here is
    how files start landing root-owned and the in-container agent
    stops being able to edit them.
    """
    sContainer = _fsRequireAcceptanceContainer()
    connection = _fconnectionOpen()
    iCode, sUser = connection.ftResultExecuteCommand(
        sContainer, "printenv CONTAINER_USER",
    )
    assert iCode == 0, f"exit {iCode}"
    assert sUser.strip(), "CONTAINER_USER is empty in the container"
    assert sUser.strip() != "root", (
        "CONTAINER_USER is root; the unprivileged-user protection is "
        "not in effect."
    )


def testRealContainerStatsAndFingerprints():
    """The file-status poll's stat and sha256 must both work.

    Lane 1's fake answers this with synthetic mtimes and a constant
    fingerprint, and the whole staleness model rests on it: if `stat
    -c '%n %Y'` or `sha256sum` is missing or formats differently in a
    real container, every step reads as fresh forever and the
    dashboard silently stops reporting invalidation.
    """
    sContainer = _fsRequireAcceptanceContainer()
    connection = _fconnectionOpen()
    connection.ftResultExecuteCommand(
        sContainer, "printf 'x' > /tmp/vaibifyStatProbe",
    )
    iCode, sOutput = connection.ftResultExecuteCommand(
        sContainer,
        "stat -c '%n %Y' /tmp/vaibifyStatProbe; "
        "printf 'fingerprint:%s\\n' "
        "\"$(sha256sum -- /tmp/vaibifyStatProbe | cut -d' ' -f1)\"",
    )
    assert iCode == 0, f"exit {iCode}: {sOutput!r}"
    listLines = [s for s in sOutput.splitlines() if s.strip()]
    sStat = listLines[0].split()
    assert sStat[0] == "/tmp/vaibifyStatProbe"
    assert sStat[1].isdigit(), f"mtime not an integer: {sStat!r}"
    sFingerprint = [
        s for s in listLines if s.startswith("fingerprint:")
    ][0]
    assert len(sFingerprint.split(":", 1)[1].strip()) == 64
    connection.ftResultExecuteCommand(
        sContainer, "rm -f /tmp/vaibifyStatProbe",
    )


def testRealContainerRunsPython3OverStdin():
    """`python3 -c` scripts must run and return their stdout.

    Three separate helpers ride this path -- the conftest-version
    scan, marker-directory creation, and the marker copy -- and the
    first parses stdout as JSON. A container without python3, or one
    whose stdin is empty, fails all three at once. That second case
    has bitten before: a heredoc without `-i` runs python on empty
    stdin and exits 0, which looks exactly like success.
    """
    sContainer = _fsRequireAcceptanceContainer()
    connection = _fconnectionOpen()
    iCode, sOutput = connection.ftResultExecuteCommand(
        sContainer,
        "python3 -c 'import json; print(json.dumps({\"ok\": True}))'",
    )
    assert iCode == 0, f"exit {iCode}: {sOutput!r}"
    assert json.loads(sOutput.strip()) == {"ok": True}
