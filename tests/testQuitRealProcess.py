"""A real hub, started as `python -m vaibify`, quits when asked and only then.

The hub runs as its own process with its own home, so its session slot,
locks and control socket are real files a test can inspect after it has
gone. The Quit is posted over real HTTP with a credential redeemed over
the hub's real control socket, as the dashboard's own sign-in does.

The Docker leg (D6) has the hub hold a real running container and Quit
WITHOUT stopping environments: the container must still be running after
the hub has exited, and its lock must be free.
"""

import fcntl
import os
import pathlib
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time

import pytest

from vaibify.cli import hubSession
from vaibify.gui import hostControlChannel
from tests.liveContainerLabels import flistLabelArguments


# A real hub process: a bound port, a unix socket and a session slot.
pytestmark = pytest.mark.exclusive

PATH_REPOSITORY = pathlib.Path(__file__).resolve().parent.parent
F_HUB_START_SECONDS = 60.0
F_HUB_EXIT_SECONDS = 30.0
S_PROBE_IMAGE = "ubuntu:24.04"


def _fiFreePort():
    socketProbe = socket.socket()
    socketProbe.bind(("127.0.0.1", 0))
    iPort = socketProbe.getsockname()[1]
    socketProbe.close()
    return iPort


def _fsDockerHostForChild():
    """Return the daemon endpoint, which a fresh HOME would otherwise lose."""
    if os.environ.get("DOCKER_HOST"):
        return os.environ["DOCKER_HOST"]
    resultContext = subprocess.run(
        ["docker", "context", "inspect", "--format",
         "{{.Endpoints.docker.Host}}"],
        capture_output=True, text=True, timeout=30,
    )
    return resultContext.stdout.strip()


@pytest.fixture
def dictHub(monkeypatch):
    """Start a real hub in its own short home; yield its facts; stop it."""
    sHome = tempfile.mkdtemp(prefix="vqh", dir="/tmp")
    iPort = _fiFreePort()
    dictEnvironment = dict(
        os.environ, HOME=sHome, PYTHONPATH=str(PATH_REPOSITORY),
        VAIBIFY_SUPPRESS_BROWSER="1",
    )
    sDockerHost = _fsDockerHostForChild() if shutil.which("docker") else ""
    if sDockerHost:
        dictEnvironment["DOCKER_HOST"] = sDockerHost
    processHub = subprocess.Popen(
        [sys.executable, "-m", "vaibify", "--port", str(iPort)],
        env=dictEnvironment, stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE, start_new_session=True,
    )
    sControl = os.path.join(sHome, ".vaibify", "control")
    monkeypatch.setattr(hostControlChannel, "_S_CONTROL_DIRECTORY", sControl)
    _fnAwaitPath(processHub, hostControlChannel.fsControlSocketPathForPort(
        iPort))
    sBaseUrl = f"http://127.0.0.1:{iPort}"
    yield {
        "processHub": processHub, "sHome": sHome, "iPort": iPort,
        "sBaseUrl": sBaseUrl,
        "sCredential": hubSession.fsRedeemHostLaneCredential(iPort, sBaseUrl),
    }
    if processHub.poll() is None:
        processHub.kill()
        processHub.wait(timeout=10)
    shutil.rmtree(sHome, ignore_errors=True)


def _fnAwaitPath(processHub, sPath):
    fDeadline = time.monotonic() + F_HUB_START_SECONDS
    while time.monotonic() < fDeadline:
        assert processHub.poll() is None, (
            "the hub exited while starting: "
            + processHub.stderr.read().decode("utf-8", "replace")[-2000:])
        if os.path.exists(sPath):
            return
        time.sleep(0.1)
    raise AssertionError(f"the hub never served {sPath}")


def _fiPostQuit(dictHub):
    iStatus, jsonBody = hubSession.ftSendHttpRequest(
        dictHub["sBaseUrl"], dictHub["sCredential"], "POST",
        "/api/session/quit", {"bStopEnvironments": False}, 30.0,
    )
    return iStatus, jsonBody


def _fbLockIsHeld(sLockPath):
    """Return True when some process holds the flock on a lock file."""
    if not os.path.exists(sLockPath):
        return False
    with open(sLockPath, "r") as fileLock:
        try:
            fcntl.flock(fileLock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fileLock, fcntl.LOCK_UN)
    return False


def _fnAssertTheHubLeftCleanly(dictHub):
    """The hub exited 0 by its own SIGTERM, after every shutdown hook ran.

    Quit ends the hub with the SIGTERM the idle watchdog sends, so
    uvicorn's graceful shutdown runs the hooks. uvicorn 0.29 and later
    then re-raise the captured signal; under the default handler that
    killed the process (measured: -15) before the launcher released its
    session slot, so ``serverLaunch`` turns it into a clean exit. What
    proves the hooks ran is what they leave behind: no session slot and
    no held lock.
    """
    iExit = dictHub["processHub"].wait(timeout=F_HUB_EXIT_SECONDS)
    assert iExit == 0, (
        f"exit {iExit}: "
        + dictHub["processHub"].stderr.read().decode()[-2000:])
    sSessions = os.path.join(dictHub["sHome"], ".vaibify", "sessions")
    listSlots = os.listdir(sSessions) if os.path.isdir(sSessions) else []
    assert listSlots == [], f"the session slot outlived the hub: {listSlots}"
    sLocks = os.path.join(dictHub["sHome"], ".vaibify", "locks")
    for sEntry in (os.listdir(sLocks) if os.path.isdir(sLocks) else []):
        assert not _fbLockIsHeld(os.path.join(sLocks, sEntry)), sEntry


def testARealHubQuitsAndReleasesItsSlotAndLocks(dictHub):
    iStatus, jsonBody = _fiPostQuit(dictHub)
    assert iStatus == 202, jsonBody
    assert jsonBody["bQuitting"] is True
    _fnAssertTheHubLeftCleanly(dictHub)


def _fbContainerRunning(sName):
    resultInspect = subprocess.run(
        ["docker", "inspect", "--format", "{{.State.Running}}", sName],
        capture_output=True, text=True, timeout=30,
    )
    return resultInspect.stdout.strip() == "true"


@pytest.mark.docker
def testAHeldEnvironmentKeepsRunningAfterAPlainQuit(dictHub):
    """D6: Quit leaves environments running unless asked to stop them."""
    sName = "vaibifyQuitProbe" + secrets.token_hex(4)
    subprocess.run(
        ["docker", "run", "-d", "--name", sName,
         *flistLabelArguments(), S_PROBE_IMAGE, "sleep", "600"],
        check=True, capture_output=True, timeout=300,
    )
    try:
        iStatus, jsonBody = hubSession.ftSendHttpRequest(
            dictHub["sBaseUrl"], dictHub["sCredential"], "POST",
            f"/api/registry/{sName}/claim", {}, 30.0,
        )
        assert iStatus == 200, jsonBody
        iStatus, jsonPreflight = hubSession.ftSendHttpRequest(
            dictHub["sBaseUrl"], dictHub["sCredential"], "GET",
            "/api/session/quit-preflight", None, 30.0,
        )
        assert iStatus == 200 and sName in (
            jsonPreflight["listRunningEnvironments"]), jsonPreflight
        iStatus, jsonBody = _fiPostQuit(dictHub)
        assert iStatus == 202, jsonBody
        _fnAssertTheHubLeftCleanly(dictHub)
        assert _fbContainerRunning(sName)
    finally:
        subprocess.run(["docker", "rm", "-f", sName],
                       capture_output=True, timeout=60)
