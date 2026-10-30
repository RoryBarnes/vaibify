"""A manuscript pull hands no token to any container program.

Real-daemon half of ``tests/testOverleafTokenStaysOffTheCommandLine.py``.
Until 2026-10-29 the pull ran ``overleafSync.py`` inside the container
with the Overleaf token on its standard input. It now clones on the
host and lands the sources through the confined tree write. A throwaway
container gets the image's PATH shape, and the container user plants a
``python3`` shim that records every invocation with its standard input
and argument vector. The pull then runs against the real adapter, with
the host-side clone stood in for (no network); the files must land in
the manuscript directory owned by the container user, and the shim must
record nothing, because the hub's own programs run on the fixed PATH
and no program is handed the token.

Live-daemon convention (``testDockerConnectionLive``): skipped without
a daemon unless ``VAIBIFY_REQUIRE_DOCKER_DAEMON`` demands one.
"""

import os
import secrets

import pytest

from tests.liveContainerLabels import fdictLabels
from tests.testDockerConnectionLive import fnRequireDaemonReachable
from vaibify.gui import syncDispatcher
from vaibify.reproducibility import overleafSync

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
S_REPO = S_HOME + "/project"
S_TOKEN = "overleaf-token-SENTINEL-" + secrets.token_hex(4)

_S_PLANT_SCRIPT = f"""
set -e
mkdir -p {S_LOCAL_BIN} {S_REPO}
printf '%s\\n' '#!/bin/sh' 'echo "python3 $*" >> {S_MARKERS}' \
    'cat >> {S_MARKERS}' 'exec /usr/local/bin/python3 "$@"' \
    > {S_LOCAL_BIN}/python3
chmod 755 {S_LOCAL_BIN}/python3
"""


@pytest.fixture(scope="module")
def tLiveContainer():
    """Yield (container, connection) with the shim planted by the user."""
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
        name=f"vaibifyOverleafPull{secrets.token_hex(4)}",
        environment={"PATH": S_IMAGE_PATH},
    )
    try:
        for sSetup, sAsUser in (
            (f"useradd -m -u 1000 {S_USER} && chmod 750 {S_HOME}", "root"),
            ("chmod 1777 /tmp", "root"),
            (_S_PLANT_SCRIPT, S_USER),
        ):
            iExit, baOutput = container.exec_run(
                ["sh", "-c", sSetup], user=sAsUser,
            )
            assert iExit == 0, (sSetup, baOutput)
        yield container, DockerConnection()
    finally:
        container.remove(force=True)


def _fsExecAsRoot(container, listCommand):
    iExit, baOutput = container.exec_run(listCommand, user="root")
    return baOutput.decode("utf-8", errors="replace") if iExit == 0 else ""


def testThePullLandsTheSourcesAndNoContainerProgramSeesTheToken(
    tLiveContainer, monkeypatch,
):
    container, connection = tLiveContainer
    monkeypatch.setattr(syncDispatcher, "_fsFetchOverleafToken", lambda: S_TOKEN)

    def fnCloneOnTheHost(sProjectId, listPullPaths, sTargetDirectory, sToken):
        assert sToken == S_TOKEN
        for sRelativePath in listPullPaths:
            with open(os.path.join(sTargetDirectory, sRelativePath), "w") as fileTex:
                fileTex.write("\\documentclass{article}\n")

    monkeypatch.setattr(overleafSync, "fnPullTexFromOverleaf", fnCloneOnTheHost)
    sTarget = S_REPO + "/.vaibify/manuscript"
    iExit, sOutput = syncDispatcher.ftResultPullFromOverleaf(
        connection, container.id, "abcdef012345", ["main.tex", "refs.bib"],
        sTarget, sProjectRepoPath=S_REPO,
    )
    assert iExit == 0, sOutput
    sListing = _fsExecAsRoot(
        container, ["sh", "-c", f"ls -ln {sTarget}"],
    )
    assert "main.tex" in sListing and "refs.bib" in sListing
    for sLine in sListing.splitlines()[1:]:
        assert " 1000 " in sLine, (
            f"a pulled file is not owned by the container user: {sLine}"
        )
    assert _fsExecAsRoot(container, ["cat", S_MARKERS]) == "", (
        "a program on the agent's PATH ran during the pull"
    )
    # Every place a program the hub ran could have written: the user's
    # home, the scratch directories and the root user's home. /proc and
    # /sys are not files and a scan of them never ends.
    sEverything = _fsExecAsRoot(
        container, ["sh", "-c",
                    f"grep -rl {S_TOKEN} /home /tmp /var/tmp /root 2>/dev/null || true"],
    )
    assert sEverything.strip() == "", "the token reached the container"
