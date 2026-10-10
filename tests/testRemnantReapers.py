"""The reaper registry runs every reaper, records each outcome, and never
blocks the event loop.

The registry exists because a reaper once failed silently: the secret
sweep raised inside a swallowed ``except`` for weeks while the suite
stayed green. These tests pin the properties that make such a failure
visible: a raising reaper is recorded as ``failed`` with a remedy and
does not stop the others; a declining reaper carries its reason; a slow
reaper lets the loop progress; and the stale-lock reap still completes
before the first request.
"""

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vaibify.gui import (
    appFactory, remnantReapers, serverLifespan, sessionLifecycle,
)


def _fappBare():
    app = FastAPI(lifespan=serverLifespan._fcontextLifespanShared)
    app.state.listLifespanStartup = []
    app.state.listLifespanShutdown = []
    return app


def _fdictRan(iRemoved):
    return remnantReapers.fdictBuildReaperOutcome(
        remnantReapers.S_OUTCOME_RAN, iRemoved=iRemoved)


def test_every_registered_reaper_runs_and_is_recorded():
    app = _fappBare()
    listSeen = []
    for sName in ("first", "second", "third"):
        remnantReapers.fnRegisterReaper(
            app, sName,
            lambda dictCtx, sName=sName: listSeen.append(sName) or _fdictRan(1))
    asyncio.run(remnantReapers.fnRunReaperPass(app, {"docker": None}))
    assert listSeen == ["first", "second", "third"]
    dictHealth = app.state.dictReaperHealth
    assert sorted(dictHealth) == ["first", "second", "third"]
    for dictRecord in dictHealth.values():
        assert dictRecord["sOutcome"] == "ran"
        assert dictRecord["iRemoved"] == 1
        assert dictRecord["sLastRunIso"]


def test_a_raising_reaper_records_failed_with_a_remedy_and_the_rest_still_run():
    app = _fappBare()
    listSeen = []

    def fdictRaise(dictCtx):
        raise RuntimeError("the daemon vanished")

    remnantReapers.fnRegisterReaper(app, "breaks", fdictRaise)
    remnantReapers.fnRegisterReaper(
        app, "survives", lambda dictCtx: listSeen.append("ran") or _fdictRan(0))
    asyncio.run(remnantReapers.fnRunReaperPass(app, {}))
    dictBroken = app.state.dictReaperHealth["breaks"]
    assert dictBroken["sOutcome"] == "failed"
    assert "RuntimeError: the daemon vanished" == dictBroken["sReason"]
    assert dictBroken["sRemedy"], "a failure names its remedy"
    assert listSeen == ["ran"]
    assert app.state.dictReaperHealth["survives"]["sOutcome"] == "ran"


def test_a_reaper_returning_no_outcome_is_recorded_as_failed():
    app = _fappBare()
    remnantReapers.fnRegisterReaper(app, "silent", lambda dictCtx: None)
    asyncio.run(remnantReapers.fnRunReaperPass(app, {}))
    dictRecord = app.state.dictReaperHealth["silent"]
    assert dictRecord["sOutcome"] == "failed"
    assert "no outcome" in dictRecord["sReason"]


def test_a_forbidden_reaper_carries_its_reason_and_remedy():
    app = _fappBare()
    remnantReapers.fnRegisterReaper(
        app, "declines",
        lambda dictCtx: remnantReapers.fdictBuildReaperOutcome(
            remnantReapers.S_OUTCOME_FORBIDDEN,
            sReason="the daemon could not list mounts",
            sRemedy="Start Docker, then rescan."))
    asyncio.run(remnantReapers.fnRunReaperPass(app, {}))
    dictRecord = app.state.dictReaperHealth["declines"]
    assert dictRecord["sOutcome"] == "forbidden"
    assert dictRecord["sReason"] == "the daemon could not list mounts"
    assert dictRecord["sRemedy"] == "Start Docker, then rescan."
    assert dictRecord["iRemoved"] == 0


def test_a_slow_reaper_does_not_block_the_event_loop():
    app = _fappBare()
    eventRelease = threading.Event()

    def fdictBlockUntilReleased(dictCtx):
        eventRelease.wait(5.0)
        return _fdictRan(0)

    remnantReapers.fnRegisterReaper(app, "slow", fdictBlockUntilReleased)

    async def fiCountTicksWhileThePassRuns():
        taskPass = asyncio.create_task(remnantReapers.fnRunReaperPass(app, {}))
        iTicks = 0
        fDeadline = time.monotonic() + 1.0
        while time.monotonic() < fDeadline:
            await asyncio.sleep(0.01)
            iTicks += 1
        assert not taskPass.done(), "the reaper is still blocked in its thread"
        assert remnantReapers.fbReaperPassInFlight(app.state)
        eventRelease.set()
        await taskPass
        assert not remnantReapers.fbReaperPassInFlight(app.state)
        return iTicks

    assert asyncio.run(fiCountTicksWhileThePassRuns()) > 10


def test_the_wrapped_sweep_adapter_records_a_count_or_zero():
    assert remnantReapers.ffnWrapSweepAsReaper(lambda: 3)(None)["iRemoved"] == 3
    assert remnantReapers.ffnWrapSweepAsReaper(lambda: None)(None)["iRemoved"] == 0


def test_the_loop_runs_a_first_pass_at_once_and_again_on_rescan():
    app = _fappBare()
    listPasses = []
    remnantReapers.fnRegisterReaper(
        app, "counts", lambda dictCtx: listPasses.append(1) or _fdictRan(0))
    remnantReapers.fnRegisterReaperLoop(app, {}, fInterval=3600.0)
    with TestClient(app):
        fDeadline = time.monotonic() + 5.0
        while len(listPasses) < 1 and time.monotonic() < fDeadline:
            time.sleep(0.02)
        assert len(listPasses) == 1, "the first pass ran without a tick"
        # The request is made on the hub's own loop, as a route would;
        # the test runs on another thread.
        app.state.taskRemnantReapers.get_loop().call_soon_threadsafe(
            remnantReapers.fnRequestRescan, app)
        fDeadline = time.monotonic() + 5.0
        while len(listPasses) < 2 and time.monotonic() < fDeadline:
            time.sleep(0.02)
        assert len(listPasses) == 2, "a rescan request starts a pass at once"
    assert app.state.taskRemnantReapers.done(), "shutdown cancels the loop"


def test_the_stale_lock_reap_completes_before_the_hub_serves(monkeypatch):
    """The reap is awaited as a startup hook; the thread does not detach it."""
    from vaibify.config import containerLock
    listCalls = []
    monkeypatch.setattr(
        containerLock, "fnReapStaleContainerLocks",
        lambda: listCalls.append(threading.current_thread().name))
    app = _fappBare()
    appFactory._fnRegisterHubStartupReapStaleClaims(app)
    assert len(app.state.listLifespanStartup) == 1
    asyncio.run(app.state.listLifespanStartup[0](app))
    assert len(listCalls) == 1, "the reap has finished when the hook returns"
    assert listCalls[0] != threading.main_thread().name, "it ran off the loop"
    assert app.state.dictReaperHealth["staleContainerLocks"]["sOutcome"] == "ran"


@pytest.mark.parametrize("bInFlight, bBusy, bExpectSentence", [
    (True, True, True),
    (False, True, False),
    (True, False, False),
])
def test_a_busy_refusal_during_a_pass_says_the_pass_may_be_ending_it(
    bInFlight, bBusy, bExpectSentence,
):
    appState = SimpleNamespace(bReaperPassInFlight=bInFlight)
    dictPayload = {"bClaimed": False, "sMessage": "Container is busy.",
                   "bBusy": bBusy}
    iStatus, dictOut = sessionLifecycle._ftAnnotateBusyRefusalDuringReaperPass(
        appState, (409, dictPayload))
    assert iStatus == 409
    bHasSentence = (
        remnantReapers.S_REAPER_PASS_IN_FLIGHT_SENTENCE in dictOut["sMessage"])
    assert bHasSentence is bExpectSentence
    assert dictOut["sMessage"].startswith("Container is busy.")


def test_a_granted_claim_is_returned_unchanged_during_a_pass():
    appState = SimpleNamespace(bReaperPassInFlight=True)
    tVerdict = (200, {"bClaimed": True, "sMessage": "granted"})
    assert sessionLifecycle._ftAnnotateBusyRefusalDuringReaperPass(
        appState, tVerdict) == tVerdict
