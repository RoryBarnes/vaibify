"""A spawned vaibify window can sign in, and a failed one leaves no hub.

``POST /api/session/spawn`` launched a child hub told not to open a
browser, so the child armed no sign-in capability, and the route handed
back the bare address: a tab that answered 401 to every call, and one
more running hub per click that nobody could use. These drive the route
against a REAL child hub: the capability in the returned URL must redeem
there, which a stubbed child cannot show.
"""

import json
import os
import shutil
import socket
import subprocess
import tempfile
import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from vaibify.gui.routes import sessionRoutes

# Outside anything vaibify picks by default and outside the other
# real-hub tests' ports, so a developer's own hub is never disturbed.
I_TEST_PORT = 18483
S_FRAGMENT_PREFIX = "#bootstrap="


def _fbPortIsFree(iPort):
    connectionProbe = socket.socket()
    connectionProbe.settimeout(0.5)
    try:
        return connectionProbe.connect_ex(("127.0.0.1", iPort)) != 0
    finally:
        connectionProbe.close()


def _fclientBuildSpawnClient():
    app = FastAPI()
    sessionRoutes.fnRegisterAll(app, {})
    return app, TestClient(app)


def _fnRedeem(iPort, sCapability):
    requestBootstrap = urllib.request.Request(
        f"http://127.0.0.1:{iPort}/api/bootstrap", method="POST",
        data=json.dumps({"sCapability": sCapability}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(requestBootstrap, timeout=15) as response:
        return json.loads(response.read())


@pytest.fixture
def realSpawnedHub(monkeypatch):
    """Spawn through the route against a real child; always stop the child.

    The child gets its own home so it never registers in a developer's
    real one. The control-socket directory is fixed when the module is
    imported, so the parent is pointed at the child's, and the home is
    short because a Unix socket path is limited to about a hundred bytes.
    """
    from vaibify.gui import hostControlChannel
    if not _fbPortIsFree(I_TEST_PORT):
        pytest.skip(f"port {I_TEST_PORT} is already in use")
    sHome = tempfile.mkdtemp(prefix="vb", dir="/tmp")
    monkeypatch.setenv("HOME", sHome)
    monkeypatch.setattr(
        hostControlChannel, "_S_CONTROL_DIRECTORY",
        os.path.join(sHome, ".vaibify", "control"))
    app, client = _fclientBuildSpawnClient()
    try:
        with patch(
            "vaibify.cli.portAllocator.fiPickFreePort",
            return_value=I_TEST_PORT,
        ):
            response = client.post("/api/session/spawn")
        yield response
    finally:
        for child in app.state.listSpawnedChildren:
            child.terminate()
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                child.kill()
        subprocess.run(
            ["pkill", "-f", f"vaibify --port {I_TEST_PORT}"],
            capture_output=True)
        shutil.rmtree(sHome, ignore_errors=True)


@pytest.mark.falsification
def testTheSpawnedWindowsUrlSignsInToTheRealChildHub(realSpawnedHub):
    """Kills: returning the bare address of a hub that armed no capability."""
    assert realSpawnedHub.status_code == 200, realSpawnedHub.text
    sUrl = realSpawnedHub.json()["sUrl"]
    sBase, sFragment = sUrl.split(S_FRAGMENT_PREFIX)
    assert sBase == f"http://127.0.0.1:{I_TEST_PORT}/"
    assert _fnRedeem(I_TEST_PORT, sFragment).get("sCredential"), (
        "the capability in the spawned window's URL did not sign in")


def testTheBareAddressOfTheSpawnedHubCannotSignIn(realSpawnedHub):
    assert realSpawnedHub.status_code == 200, realSpawnedHub.text
    with pytest.raises(urllib.error.HTTPError) as errorRaised:
        _fnRedeem(I_TEST_PORT, "not-a-capability-anybody-minted")
    assert errorRaised.value.code in (401, 403)


def _fchildAlive():
    child = MagicMock()
    child.poll.return_value = None
    return child


def _fresponseSpawnWithAChildThatFails(monkeypatch, fbReady, sMintError=""):
    app, client = _fclientBuildSpawnClient()
    child = _fchildAlive()

    async def fbAwaitReady(iPort, fTimeoutSeconds, childWatched=None):
        return fbReady

    def fsMintOrRefuse(iPort, bRemoteSession=False):
        from vaibify.cli.hubSession import HubSessionError
        raise HubSessionError(sMintError)

    monkeypatch.setattr(sessionRoutes, "_fbAwaitChildReady", fbAwaitReady)
    monkeypatch.setattr(
        "vaibify.cli.hubSession.fsRequestBootstrapCapability",
        fsMintOrRefuse)
    with patch(
        "vaibify.cli.portAllocator.fiPickFreePort", return_value=8055,
    ), patch.object(
        sessionRoutes, "_fprocessLaunchDetachedHub", return_value=child,
    ):
        response = client.post("/api/session/spawn")
    return response, child, app


@pytest.mark.falsification
def testAChildThatNeverBecomesReadyIsStoppedAndRefused(monkeypatch):
    """Kills: leaving an unusable hub running after a failed click."""
    response, child, app = _fresponseSpawnWithAChildThatFails(
        monkeypatch, fbReady=False)
    assert response.status_code == 502
    child.terminate.assert_called_once()
    assert app.state.listSpawnedChildren == []
    assert response.json()["detail"] == (
        "The new vaibify window was not ready after 240 seconds, so it "
        "was stopped. What it printed is in "
        f"{sessionRoutes._fsSpawnedHubLogPath(8055)}.")


def testAChildThatWillNotMintACapabilityIsStoppedAndRefused(monkeypatch):
    response, child, app = _fresponseSpawnWithAChildThatFails(
        monkeypatch, fbReady=True, sMintError="refused")
    assert response.status_code == 502
    child.terminate.assert_called_once()
    assert app.state.listSpawnedChildren == []
    assert "issued no sign-in link" in response.json()["detail"]


@pytest.mark.falsification
def testARefusalSaysTheChildExitedOnItsOwnAndWhereItsOutputIs():
    """Kills: reporting a child that exited as one that was stopped.

    The status is read before the stop is sent, so an exit of the
    child's own is told apart from this hub's terminate.
    """
    childExited = MagicMock()
    childExited.poll.return_value = 3
    assert sessionRoutes._fsDescribeSpawnFailure(childExited, 8055, False) == (
        "The new vaibify window exited with status 3 before it was "
        "ready. What it printed is in "
        f"{sessionRoutes._fsSpawnedHubLogPath(8055)}.")


@pytest.mark.falsification
def testTheSpawnedHubImportsItsParentsVaibifyNotTheWorkingDirectorys(
    monkeypatch, tmp_path,
):
    """Kills: launching the child from the parent's working directory.

    ``-m`` puts the working directory first on the child's import path,
    so a parent started inside a checkout of another version spawned a
    child running THAT version. Here the working directory holds a
    shadowing ``vaibify`` package, the launch's real arguments run a
    probe that prints which ``vaibify`` it imported, and the answer is
    read back from the log a refusal names.
    """
    import sys
    import vaibify
    pathShadow = tmp_path / "vaibify"
    pathShadow.mkdir()
    (pathShadow / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    fprocessRealPopen = subprocess.Popen

    def fprocessRunTheProbe(listArgv, **dictKeywords):
        assert listArgv[1:3] == ["-m", "vaibify"]
        return fprocessRealPopen(
            [sys.executable, "-c", "import vaibify; print(vaibify.__file__)"],
            **dictKeywords)

    with patch.object(subprocess, "Popen", fprocessRunTheProbe):
        processProbe = sessionRoutes._fprocessLaunchDetachedHub(I_TEST_PORT)
    assert processProbe.wait(timeout=60) == 0
    with open(sessionRoutes._fsSpawnedHubLogPath(I_TEST_PORT),
              encoding="utf-8") as fileLog:
        sImported = fileLog.read().split()[-1]
    assert os.path.realpath(sImported) == os.path.realpath(vaibify.__file__)


def testTheReadinessWaitStopsWhenTheChildHasAlreadyExited(monkeypatch):
    import asyncio
    childDead = MagicMock()
    childDead.poll.return_value = 1
    monkeypatch.setattr(
        sessionRoutes, "_fbIsPortAcceptingConnections", lambda iPort: False)
    bReady = asyncio.run(
        sessionRoutes._fbAwaitChildReady(8055, 30.0, childDead))
    assert bReady is False
