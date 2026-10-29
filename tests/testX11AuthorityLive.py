"""The X11 helper against a real X server with the SECURITY extension.

The helper is copied flat into a throwaway Linux container running Xvfb
(started with ``-noreset``, because an X server resets, and forgets
every generated authorization, when its last client disconnects, and in
this container the helper IS the last client each time; on a desktop a
window manager keeps the server up). Generated cookies are then driven
through the whole life the hub gives them: made, accepted, untrusted
(no XTEST), copied, revoked, refused, revoked again (``gone``), and
probed against a dead server.

Live-daemon convention (``testDockerConnectionLive``): skipped without
a daemon unless ``VAIBIFY_REQUIRE_DOCKER_DAEMON`` demands one. The
fixture image is built once from the pinned Ubuntu base and kept.
"""

import io
import json
import os
import secrets
import tarfile

import pytest

from tests.liveContainerLabels import fdictLabels
from tests.testDockerConnectionLive import fnRequireDaemonReachable
from vaibify.host import x11Authority

pytestmark = pytest.mark.docker_live

S_IMAGE = "vaibify-test-xvfb:ubuntu24"
S_DOCKERFILE = """
FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends \\
    xvfb x11-utils xauth python3 libx11-6 libxext6 \\
    && rm -rf /var/lib/apt/lists/*
"""
S_HELPER_PATH = "/x11Authority.py"
S_START_SERVER = """
set -e
touch /root/.Xauthority
xauth -f /root/.Xauthority add :99 MIT-MAGIC-COOKIE-1 \
    $(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \\n')
Xvfb :99 -noreset -nolisten tcp -auth /root/.Xauthority >/tmp/xvfb.log 2>&1 &
for i in $(seq 1 50); do xdpyinfo -display :99 >/dev/null 2>&1 && exit 0; sleep 0.1; done
exit 1
"""


def _fnBuildFixtureImage(clientDocker):
    import docker
    try:
        clientDocker.images.get(S_IMAGE)
        return
    except docker.errors.ImageNotFound:
        pass
    clientDocker.images.build(
        fileobj=io.BytesIO(S_DOCKERFILE.encode("utf-8")), tag=S_IMAGE, rm=True,
    )


def _fbaHelperTar():
    bufferTar = io.BytesIO()
    with tarfile.open(fileobj=bufferTar, mode="w") as fileTar:
        fileTar.add(x11Authority.__file__, arcname=os.path.basename(S_HELPER_PATH))
    return bufferTar.getvalue()


@pytest.fixture(scope="module")
def container():
    fnRequireDaemonReachable()
    import docker
    from vaibify.docker.dockerConnection import _fnEnsureDockerHost
    _fnEnsureDockerHost()
    clientDocker = docker.from_env()
    _fnBuildFixtureImage(clientDocker)
    containerLive = clientDocker.containers.run(
        S_IMAGE, ["sleep", "600"], detach=True, labels=fdictLabels(),
        name=f"vaibifyX11Live{secrets.token_hex(4)}",
        environment={"DISPLAY": ":99", "XAUTHORITY": "/root/.Xauthority"},
    )
    try:
        containerLive.put_archive("/", _fbaHelperTar())
        iExit, baOutput = containerLive.exec_run(["sh", "-c", S_START_SERVER])
        assert iExit == 0, baOutput
        if b"SECURITY" not in containerLive.exec_run(["xdpyinfo", "-display", ":99"])[1]:
            pytest.skip("this Xvfb has no SECURITY extension")
        yield containerLive
    finally:
        containerLive.remove(force=True)


def _fdictHelper(container, listArguments):
    """Run one helper operation; parse its stdout alone (Xlib chatters on stderr)."""
    iExit, tStreams = container.exec_run(
        ["python3", "-I", S_HELPER_PATH] + listArguments, demux=True,
        environment={"DISPLAY": ":99", "XAUTHORITY": "/root/.Xauthority"},
    )
    baStdout, baStderr = tStreams
    assert iExit == 0, (baStdout, baStderr)
    return json.loads((baStdout or b"").decode("utf-8").strip().splitlines()[-1])


def _fiXtestCount(container, sAuthority):
    _iExit, baOutput = container.exec_run(
        ["xdpyinfo", "-display", ":99"], environment={"XAUTHORITY": sAuthority},
    )
    return baOutput.count(b"XTEST")


def testAGeneratedCookieAdmitsAnUntrustedClientUntilItIsRevoked(container):
    container.exec_run(["mkdir", "-p", "/c1", "/c2"])
    dictGenerated = _fdictHelper(container, ["generate", "--directory", "/c1", "--number", "99"])
    assert dictGenerated["sOutcome"] == "generated"
    iAuthorizationId = dictGenerated["iAuthorizationId"]
    assert _fdictHelper(container, ["probe", "--directory", "/c1"])["sOutcome"] == "accepted"
    # Untrusted: the server hides XTEST (synthetic input) from this client, and
    # shows it to the trusted one.
    assert _fiXtestCount(container, "/c1/Xauthority") == 0
    assert _fiXtestCount(container, "/root/.Xauthority") == 1
    # A copy of the cookie is the same authorization: accepted until revoked.
    container.exec_run(["cp", "/c1/Xauthority", "/c2/Xauthority"])
    assert _fdictHelper(container, ["probe", "--directory", "/c2"])["sOutcome"] == "accepted"
    dictRevoked = _fdictHelper(container, [
        "revoke", "--directory", "/c1", "--authorization-id", str(iAuthorizationId),
    ])
    assert dictRevoked["sOutcome"] == "revoked"
    assert _fdictHelper(container, ["probe", "--directory", "/c1"])["sOutcome"] == "refused"
    assert _fdictHelper(container, ["probe", "--directory", "/c2"])["sOutcome"] == "refused"
    dictStale = _fdictHelper(container, [
        "revoke", "--directory", "/c1", "--authorization-id", str(iAuthorizationId),
    ])
    assert dictStale["sOutcome"] == "gone"


def testASecondContainersCookieIsItsOwn(container):
    container.exec_run(["mkdir", "-p", "/c3", "/c4"])
    dictThird = _fdictHelper(container, ["generate", "--directory", "/c3", "--number", "99"])
    dictFourth = _fdictHelper(container, ["generate", "--directory", "/c4", "--number", "99"])
    assert dictThird["iAuthorizationId"] != dictFourth["iAuthorizationId"]
    _fdictHelper(container, [
        "revoke", "--directory", "/c3", "--authorization-id", str(dictThird["iAuthorizationId"]),
    ])
    assert _fdictHelper(container, ["probe", "--directory", "/c3"])["sOutcome"] == "refused"
    assert _fdictHelper(container, ["probe", "--directory", "/c4"])["sOutcome"] == "accepted"


def testADeadServerIsUnreachableNotRefused(container):
    """Last: it ends the server the module's other tests share."""
    container.exec_run(["pkill", "Xvfb"])
    container.exec_run(["sh", "-c", "for i in 1 2 3 4 5 6 7 8 9 10; do pgrep Xvfb >/dev/null || exit 0; sleep 0.2; done"])
    assert _fdictHelper(container, ["probe", "--directory", "/c4"])["sOutcome"] == "serverUnreachable"
    assert _fdictHelper(container, [
        "revoke", "--directory", "/c4", "--authorization-id", "1",
    ])["sOutcome"] == "serverUnreachable"
