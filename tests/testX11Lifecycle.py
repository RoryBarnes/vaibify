"""The hub's X11 lifecycle: nothing while the server is down, everything else on its cadence."""

import asyncio
from types import SimpleNamespace

import pytest

from vaibify.docker import x11Authorizations, x11Forwarding
from vaibify.gui import x11Lifecycle


@pytest.fixture
def fixtureX11Home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(x11Lifecycle.platform, "system", lambda: "Linux")
    return tmp_path


def _fnRecordOne(monkeypatch, sName="demo"):
    monkeypatch.setattr(
        x11Authorizations, "fdictRunHelper",
        lambda listArguments, fTimeoutSeconds=15.0: {
            "sOutcome": "generated", "iAuthorizationId": 1,
        },
    )
    x11Authorizations.fdictPrepareAuthorization(sName, "a" * 32, ":0", ":0", "0")


def testNothingIsProbedWhileNoServerCouldAnswer(fixtureX11Home, monkeypatch):
    _fnRecordOne(monkeypatch)
    monkeypatch.delenv("DISPLAY")
    listCalls = []
    monkeypatch.setattr(x11Authorizations, "fdictReconcile", lambda fbExists: listCalls.append(1))
    assert x11Lifecycle.fdictReconcileAuthorizations() == {}
    assert listCalls == []


@pytest.mark.falsification
def testAMacWithItsServerQuitIsLeftAlone(fixtureX11Home, monkeypatch):
    """Connecting to the launchd socket would start the server; the hub never does that.

    Kills: probing the display while the Mac's X server is not running.
    """
    _fnRecordOne(monkeypatch)
    monkeypatch.setattr(x11Lifecycle.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(x11Forwarding, "fbMacXServerIsRunning", lambda: False)
    monkeypatch.setattr(
        x11Authorizations, "fdictReconcile",
        lambda fbExists: pytest.fail("probed a display whose server is not running"),
    )
    assert x11Lifecycle.fdictReconcileAuthorizations() == {}
    assert x11Lifecycle.fbXServerAvailable() is False


def testTheReconcileAsksTheDaemonWhetherEachContainerStillExists(fixtureX11Home, monkeypatch):
    """Kills: probing the display while the Mac's X server is not running."""
    _fnRecordOne(monkeypatch)
    dictSeen = {}

    def fdictReconcile(fbContainerExists):
        dictSeen["answered-absent"] = fbContainerExists("absent")
        dictSeen["answered-present"] = fbContainerExists("present")
        dictSeen["unanswered"] = fbContainerExists("unknown")
        return {"demo": "accepted"}

    monkeypatch.setattr(x11Authorizations, "fdictReconcile", fdictReconcile)
    monkeypatch.setattr(
        x11Lifecycle.containerManager, "fdictProbeContainerPresence",
        lambda sName: {"bAnswered": sName != "unknown", "bPresent": sName == "present"},
    )
    assert x11Lifecycle.fdictReconcileAuthorizations() == {"demo": "accepted"}
    assert dictSeen == {
        "answered-absent": False, "answered-present": True, "unanswered": True,
    }


def testAnEmptyRecordCostsNoProbe(fixtureX11Home, monkeypatch):
    monkeypatch.setattr(
        x11Authorizations, "fdictReconcile",
        lambda fbExists: pytest.fail("probed with nothing recorded"),
    )
    assert x11Lifecycle.fdictReconcileAuthorizations() == {}


@pytest.mark.falsification
def testTheHubWithdrawsTheLegacyGrantsAtStartup(fixtureX11Home, monkeypatch):
    """Earlier versions admitted every local client; a hub that forgot would leave that open.

    Kills: dropping the xhost revocation from the hub's start.
    """
    listRevoked = []
    monkeypatch.setattr(
        x11Forwarding, "fnRevokeLegacyXhostGrants", lambda: listRevoked.append(1),
    )
    monkeypatch.setattr(x11Lifecycle, "fdictReconcileAuthorizations", lambda: {})
    app = SimpleNamespace(
        state=SimpleNamespace(listLifespanStartup=[], listLifespanShutdown=[]),
    )
    x11Lifecycle.fnRegisterX11Lifecycle(app, fInterval=3600.0)
    assert len(app.state.listLifespanStartup) == 1

    async def fnRunStartThenStop():
        await app.state.listLifespanStartup[0](app)
        assert not app.state.taskX11Probe.done()
        await app.state.listLifespanShutdown[0](app)
        assert app.state.taskX11Probe.done()

    asyncio.run(fnRunStartThenStop())
    assert listRevoked == [1]


def testTheProbeLoopSurvivesAFailedReconcile(fixtureX11Home, monkeypatch):
    listAttempts = []

    def fdictFailOnce():
        listAttempts.append(1)
        if len(listAttempts) == 1:
            raise RuntimeError("the daemon hiccuped")
        return {}

    monkeypatch.setattr(x11Lifecycle, "fdictReconcileAuthorizations", fdictFailOnce)

    async def fnRunTwoTicks():
        taskLoop = asyncio.create_task(x11Lifecycle._fnProbeLoop(0.01))
        while len(listAttempts) < 2:
            await asyncio.sleep(0.01)
        taskLoop.cancel()
        try:
            await taskLoop
        except asyncio.CancelledError:
            pass

    asyncio.run(fnRunTwoTicks())
    assert len(listAttempts) >= 2
