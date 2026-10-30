"""Planted programs on the agent's PATH never run inside a hub exec.

Real-daemon half of ``tests/testExecLane.py``. A throwaway container
gets the image's PATH shape -- the container user's ``~/.local/bin``
first -- and that user plants a ``cat``, a ``python3`` and a
``usercustomize.py``, each of which records that it ran and then
behaves correctly, so a hub that resolved through them would get the
right answer and never notice. The hub's own operations are then
driven: terminal group discovery and the process-group probe (both
``/bin/sh`` scripts run as root), a typed read, a batched hash, and a
batched existence probe. None of the markers may appear, and every
answer must still be correct.

The negative control runs the same programs through the
researcher-command lane, where they MUST run: that is what proves the
shims were live, so their silence under the hub lane means something.

Live-daemon convention (``testDockerConnectionLive``): skipped without
a daemon unless ``VAIBIFY_REQUIRE_DOCKER_DAEMON`` demands one.
"""

import secrets

import pytest

from tests.liveContainerLabels import fdictLabels
from tests.testDockerConnectionLive import fnRequireDaemonReachable
from vaibify.docker.execLane import S_EXEC_LANE_RESEARCHER_COMMAND

pytestmark = pytest.mark.docker_live

S_IMAGE = "python:3.10-slim"
S_USER = "researcher"
S_HOME = "/home/" + S_USER
S_LOCAL_BIN = S_HOME + "/.local/bin"
S_MARKERS = "/tmp/plantedShimRan"
S_IMAGE_PATH = (
    S_LOCAL_BIN + ":/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:"
    "/sbin:/bin"
)
S_DATA_FILE = S_HOME + "/project/data.txt"
S_DATA_CONTENT = "forty-two\n"

_S_PLANT_SCRIPT = f"""
set -e
mkdir -p {S_LOCAL_BIN} {S_HOME}/project
printf '%s\\n' '#!/bin/sh' 'echo cat >> {S_MARKERS}' 'exec /bin/cat "$@"' \
    > {S_LOCAL_BIN}/cat
printf '%s\\n' '#!/bin/sh' 'echo python3 >> {S_MARKERS}' \
    'exec /usr/local/bin/python3 "$@"' > {S_LOCAL_BIN}/python3
chmod 755 {S_LOCAL_BIN}/cat {S_LOCAL_BIN}/python3
sUserSite=$(/usr/local/bin/python3 -c 'import site; print(site.getusersitepackages())')
mkdir -p "$sUserSite"
printf '%s\\n' "open('{S_MARKERS}', 'a').write('usercustomize\\n')" \
    > "$sUserSite/usercustomize.py"
printf '%s' '{S_DATA_CONTENT}' > {S_DATA_FILE}
"""


@pytest.fixture(scope="module")
def tLiveContainer():
    """Yield (container, connection) with the shims planted by the user."""
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
        name=f"vaibifyExecLane{secrets.token_hex(4)}",
        environment={"PATH": S_IMAGE_PATH},
    )
    try:
        for sSetup, sAsUser in (
            # Ubuntu creates a 750 home (login.defs HOME_MODE); Debian's
            # slim image gives 700, so the fixture sets what the real
            # image has and the read-only user must get through.
            (f"useradd -m -u 1000 {S_USER} && chmod 750 {S_HOME}", "root"),
            (f"chmod 1777 /tmp", "root"),
            (_S_PLANT_SCRIPT, S_USER),
        ):
            iExit, baOutput = container.exec_run(
                ["sh", "-c", sSetup], user=sAsUser,
            )
            assert iExit == 0, (sSetup, baOutput)
        yield container, DockerConnection()
    finally:
        container.remove(force=True)


def _fsReadMarkers(container):
    """Return the shim marker file's text, or ``""`` when nothing ran."""
    iExit, baOutput = container.exec_run(
        ["/bin/cat", S_MARKERS], user="root",
    )
    return baOutput.decode("utf-8", errors="replace") if iExit == 0 else ""


def _fnClearMarkers(container):
    container.exec_run(["/bin/rm", "-f", S_MARKERS], user="root")


def testTheShimsAreLiveOnTheResearcherLane(tLiveContainer):
    """Negative control: through the image PATH every planted program runs."""
    container, connection = tLiveContainer
    _fnClearMarkers(container)
    tExecResult = connection.ftRunInContainerStreamed(
        container.id, "cat /dev/null && python3 -c pass",
        sLane=S_EXEC_LANE_RESEARCHER_COMMAND,
    )
    assert tExecResult.iExitCode == 0, tExecResult
    sMarkers = _fsReadMarkers(container)
    assert "cat" in sMarkers
    assert "python3" in sMarkers
    assert "usercustomize" in sMarkers


def testTheRootProbesResolveNothingTheAgentPlanted(tLiveContainer):
    from vaibify.gui import terminalContainment
    container, connection = tLiveContainer
    _fnClearMarkers(container)
    sMarkerPath = f"/tmp/group-{secrets.token_hex(4)}"
    iExit, _ = container.exec_run(
        ["sh", "-c", f"printf '4242 4242 4242\\n' > {sMarkerPath}"],
        user=S_USER,
    )
    assert iExit == 0
    iLeader = terminalContainment.fiDiscoverTerminalProcessGroup(
        connection, container.id, sMarkerPath, fTimeoutSeconds=5.0,
    )
    assert iLeader == 4242
    dictProbe = connection.fdictProbeProcessGroupMembers(container.id, 1)
    assert dictProbe["bConclusive"]
    assert dictProbe["iMemberCount"] >= 1
    assert _fsReadMarkers(container) == ""


def testTheTypedReadsAndBatchedProbesRunTheRealInterpreter(tLiveContainer):
    from vaibify.gui.containerGit import fdictComputeBlobShasInContainer
    container, connection = tLiveContainer
    _fnClearMarkers(container)
    baContent = connection.fbaFetchFile(container.id, S_DATA_FILE)
    assert baContent == S_DATA_CONTENT.encode("utf-8")
    listExists = connection.flistContainerPathsExist(
        container.id, [S_DATA_FILE, S_HOME + "/project/absent.txt"],
    )
    assert listExists == [True, False]
    dictShas = fdictComputeBlobShasInContainer(
        connection, container.id, ["data.txt"], sWorkspace=S_HOME + "/project",
    )
    assert set(dictShas) == {"data.txt"}
    assert len(dictShas["data.txt"]) == 40
    assert _fsReadMarkers(container) == ""


def testAConfinedWriteRunsTheRealInterpreter(tLiveContainer):
    container, connection = tLiveContainer
    _fnClearMarkers(container)
    sTarget = S_HOME + "/project/written.txt"
    connection.fnWriteFile(container.id, sTarget, b"hub bytes\n")
    iExit, baOutput = container.exec_run(["/bin/cat", sTarget], user=S_USER)
    assert (iExit, baOutput) == (0, b"hub bytes\n")
    assert _fsReadMarkers(container) == ""


# ---------------------------------------------------------------------------
# The integrity-read lane against a real daemon
# ---------------------------------------------------------------------------

from vaibify.docker.dockerConnection import UnreadableSmallFile  # noqa: E402
from vaibify.docker.execLane import (  # noqa: E402
    S_EXEC_LANE_HUB_INTERNAL, S_EXEC_LANE_INTEGRITY_READ,
)

S_PROJECT = S_HOME + "/project"
S_PRIVATE_FILE = S_HOME + "/private.txt"


def _fnRunAsUser(container, sCommand):
    iExit, baOutput = container.exec_run(["sh", "-c", sCommand], user=S_USER)
    assert iExit == 0, (sCommand, baOutput)


def _fdictSnapshotHashes(connection, container):
    tExecResult = connection.ftReadRepoSnapshot(
        container.id, S_PROJECT, [], [], ["data.txt"], [],
    )
    assert tExecResult.iExitCode == 0, tExecResult
    import json
    sPayload = tExecResult.sStdout.strip()
    dictParsed = json.loads(sPayload[sPayload.index("{"):])
    return dictParsed["dictHashes"]


def testTheIntegrityLaneIsAnotherUserInTheContainerUsersGroup(tLiveContainer):
    """The reader is uid 65532, gid 1000, and can enter a 750 home."""
    container, connection = tLiveContainer
    _fnRunAsUser(container, f"chmod 750 {S_HOME}")
    tExecResult = connection.ftRunInContainerStreamed(
        container.id, "id -u; id -g; echo $HOME",
        sLane=S_EXEC_LANE_INTEGRITY_READ,
    )
    assert tExecResult.sStdout.split() == ["65532", "1000", "/"]
    baContent = connection.fbaFetchFile(container.id, S_DATA_FILE)
    assert baContent == S_DATA_CONTENT.encode("utf-8")


def testAFileTheReaderCannotOpenIsUnknownNotMissingNotUnchanged(tLiveContainer):
    container, connection = tLiveContainer
    _fnRunAsUser(container, f"chmod 750 {S_HOME}; chmod 644 {S_DATA_FILE}")
    dictBefore = _fdictSnapshotHashes(connection, container)
    assert dictBefore["data.txt"]["sSha256"]
    _fnRunAsUser(container, f"chmod 600 {S_DATA_FILE}")
    dictLocked = _fdictSnapshotHashes(connection, container)
    assert dictLocked["data.txt"]["sSha256"] is None
    assert not dictLocked["data.txt"].get("bMissing")
    dictSmall = connection.fdictFetchSmallFiles(
        container.id, [S_DATA_FILE, S_PROJECT + "/absent.txt"],
    )
    assert isinstance(dictSmall[S_DATA_FILE], UnreadableSmallFile)
    assert dictSmall[S_PROJECT + "/absent.txt"] is None
    with pytest.raises(OSError) as errorInfo:
        connection.fbaFetchFile(container.id, S_DATA_FILE)
    assert not isinstance(errorInfo.value, FileNotFoundError)
    _fnRunAsUser(container, f"chmod 644 {S_DATA_FILE}; echo changed >> {S_DATA_FILE}")
    dictAfter = _fdictSnapshotHashes(connection, container)
    assert dictAfter["data.txt"]["sSha256"]
    assert dictAfter["data.txt"]["sSha256"] != dictBefore["data.txt"]["sSha256"]


def testTheContainerUserLaneStillReadsThatUsersPrivateFile(tLiveContainer):
    """The keyring-class reads keep the container user's identity."""
    container, connection = tLiveContainer
    _fnRunAsUser(
        container, f"printf secret > {S_PRIVATE_FILE}; chmod 600 {S_PRIVATE_FILE}",
    )
    with pytest.raises(OSError):
        connection.fbaFetchFile(container.id, S_PRIVATE_FILE)
    assert connection.fbaFetchFile(
        container.id, S_PRIVATE_FILE, sLane=S_EXEC_LANE_HUB_INTERNAL,
    ) == b"secret"


@pytest.fixture(scope="module")
def tLiveGitContainer(tLiveContainer):
    """The same container with git installed and a repository owned by the user."""
    container, connection = tLiveContainer
    iExit, baOutput = container.exec_run(
        ["sh", "-c", "apt-get update -qq && apt-get install -y -qq git >/dev/null"],
        user="root",
    )
    assert iExit == 0, baOutput[-400:]
    _fnRunAsUser(
        container,
        f"cd {S_PROJECT} && git init -q -b main . && git config user.email a@b && "
        "git config user.name a && git add data.txt && git commit -qm init",
    )
    return container, connection


def testGitIsReadAsTheReadOnlyUserWithoutTheAgentsConfiguration(tLiveGitContainer):
    """A repository owned by uid 1000 is readable by uid 65532.

    The agent's ``~/.gitconfig`` is never consulted: HOME is the root
    directory, so a ``core.fsmonitor`` or alias planted there cannot
    run inside the hub's git.
    """
    container, connection = tLiveGitContainer
    _fnRunAsUser(
        container,
        f"printf '[alias]\\n\\trev-parse = !touch {S_MARKERS}\\n' > {S_HOME}/.gitconfig",
    )
    _fnClearMarkers(container)
    listStatuses = connection.flistReadGitRepoStatuses(container.id, [S_PROJECT])
    assert listStatuses[0]["bMissing"] is False
    assert listStatuses[0]["sBranch"].strip() == "main"
    assert _fsReadMarkers(container) == ""
