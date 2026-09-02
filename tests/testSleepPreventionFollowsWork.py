"""Sleep prevention must follow work in the container, not a browser tab.

The falsification pair this file exists for (2026-08-29):

* a container with work still running keeps the machine awake after the
  browser has gone and the ownership record has been reaped, and
* a container with nothing running has its keep-alive withdrawn, so the
  fix cannot be "hold a caffeinate forever and call it correctness".

Both are driven through :func:`fnSweepWorkLaneKeepAlives` against the
REAL ``keepAliveManager`` registry (redirected into a temporary home by
the autouse fixture in ``conftest``), so the assertion is about pid
files that exist on disk, not about a call somebody remembered to make.
The caffeinate spawn itself is stubbed — the test must not fight the
researcher's power management — but every decision above it is real.
"""

import pytest

from vaibify.config import keepAliveManager
from vaibify.gui import sleepPrevention


class ConnectionDockerFake:
    """A daemon that answers only what this lane models, and fails loud.

    Fail-closed on purpose (the browser-lane contract, applied here):
    a permissive double would let a sweep that consulted the wrong
    container, or invented one, still pass.
    """

    def __init__(self, dictRunningIdByName, dictRunningExecsById=None):
        self._dictRunningIdByName = dictRunningIdByName
        self._dictRunningExecsById = dictRunningExecsById or {}
        self.listExecQueries = []

    def flistGetRunningContainers(self):
        return [
            {"sName": sName, "sContainerId": sContainerId}
            for sName, sContainerId in self._dictRunningIdByName.items()
        ]

    def flistRunningExecIdentifiers(self, sContainerId):
        if sContainerId not in self._dictRunningIdByName.values():
            raise AssertionError(
                f"the sweep asked about container id {sContainerId!r}, "
                "which this daemon is not running"
            )
        self.listExecQueries.append(sContainerId)
        return list(self._dictRunningExecsById.get(sContainerId, []))


class ConnectionDockerUnreadable:
    """A daemon that lists containers but cannot answer about execs."""

    def flistGetRunningContainers(self):
        return [{"sName": "frozen", "sContainerId": "id-frozen"}]

    def flistRunningExecIdentifiers(self, sContainerId):
        raise RuntimeError("daemon said no")


class StateAppFake:
    """Just enough app state for the sweep: the owner-of-record map."""

    def __init__(self, dictContainerOwners=None):
        self.dictContainerOwners = dictContainerOwners or {}


@pytest.fixture(autouse=True)
def fnStubCaffeinateSpawn(monkeypatch):
    """Record a pid without launching caffeinate.

    Everything else in ``keepAliveManager`` stays real: the pid file is
    written, read back, and unlinked in the redirected registry, and
    ``fbKeepAliveIsLive`` is answered from that file — so a sweep that
    forgets to write or to remove one is visible.
    """
    monkeypatch.setattr(
        keepAliveManager, "_fiSpawnCaffeinate", lambda: 4242,
    )
    monkeypatch.setattr(
        keepAliveManager, "_fnKillIfRunning",
        lambda iPid, sStartedIso: None,
    )
    monkeypatch.setattr(
        keepAliveManager, "fbIsProcessAliveSince",
        lambda iPid, sStartedIso: True,
    )
    # The sweep declines outright where caffeinate does not exist, so a
    # Linux runner must be told the platform supports it or every
    # assertion below would pass vacuously on macOS and be skipped in
    # CI — which is the same "a lane that reports success for having
    # run nothing" failure the docker guards exist to prevent.
    monkeypatch.setattr(
        keepAliveManager, "fbPlatformSupportsKeepAlive", lambda: True,
    )


def _fbWorkLaneIsUp(sName):
    """Return True when this container's work-lane keep-alive is recorded."""
    return keepAliveManager.fbKeepAliveIsLive(
        sleepPrevention.fsWorkLaneKeepAliveName(sName),
    )


def testWorkStillRunningKeepsTheMachineAwakeAfterTheBrowserIsGone():
    """The defect this fix exists for: a reaped record must not sleep the host.

    The record is GONE — the browser left, the reaper released it, and
    the session-lane keep-alive died with it. The daemon still reports
    a running exec, which is what a job backgrounded in a terminal, or
    any exec at all after a hub restart, looks like. The machine must
    stay awake.
    """
    connectionDocker = ConnectionDockerFake(
        {"proj": "id-proj"}, {"id-proj": ["exec-still-running"]},
    )
    stateApp = StateAppFake(dictContainerOwners={})
    assert not _fbWorkLaneIsUp("proj")

    sleepPrevention.fnSweepWorkLaneKeepAlives(
        stateApp, {"docker": connectionDocker},
    )

    assert _fbWorkLaneIsUp("proj"), (
        "a container the daemon reports a running exec in must hold a "
        "work-lane keep-alive even though nobody owns it"
    )


def testAnIdleContainerHasItsKeepAliveWithdrawn():
    """The other half: no visible work, no keep-alive.

    Without this the "fix" degenerates into never letting the machine
    sleep again, which would pass the first test and be worse than the
    defect.
    """
    connectionDocker = ConnectionDockerFake(
        {"proj": "id-proj"}, {"id-proj": ["exec-still-running"]},
    )
    stateApp = StateAppFake(dictContainerOwners={})
    sleepPrevention.fnSweepWorkLaneKeepAlives(
        stateApp, {"docker": connectionDocker},
    )
    assert _fbWorkLaneIsUp("proj")

    connectionDockerIdle = ConnectionDockerFake({"proj": "id-proj"}, {})
    sleepPrevention.fnSweepWorkLaneKeepAlives(
        stateApp, {"docker": connectionDockerIdle},
    )

    assert not _fbWorkLaneIsUp("proj"), (
        "a container with no running exec must release its work-lane "
        "keep-alive so the machine can sleep"
    )


def testAStoppedContainerReleasesItsKeepAliveWithoutBeingAsked():
    """A container that is gone cannot be running work.

    The daemon is never consulted about it — there is nothing to
    consult — so the lane must be withdrawn on the container's absence
    from the running list alone.
    """
    stateApp = StateAppFake(dictContainerOwners={})
    sleepPrevention.fnSweepWorkLaneKeepAlives(
        StateAppFake(),
        {"docker": ConnectionDockerFake(
            {"proj": "id-proj"}, {"id-proj": ["exec-one"]},
        )},
    )
    assert _fbWorkLaneIsUp("proj")

    connectionDockerEmpty = ConnectionDockerFake({}, {})
    sleepPrevention.fnSweepWorkLaneKeepAlives(
        stateApp, {"docker": connectionDockerEmpty},
    )

    assert not _fbWorkLaneIsUp("proj")


def testAnOwnedContainerIsNotPolledButAnEstablishedLaneIsMaintained():
    """Owned containers stay out on the way IN, and stay in once established.

    Skipping owned containers is what stops the dashboard's own steady
    polling from churning a caffeinate every tick; keeping an
    ESTABLISHED lane is what stops a later claim from withdrawing the
    only protection a running job has, since claiming a container that
    is already up starts no session-lane keep-alive at all.
    """
    connectionDocker = ConnectionDockerFake(
        {"owned": "id-owned"}, {"id-owned": ["exec-one"]},
    )
    stateApp = StateAppFake(dictContainerOwners={"owned": object()})

    sleepPrevention.fnSweepWorkLaneKeepAlives(
        stateApp, {"docker": connectionDocker},
    )
    assert connectionDocker.listExecQueries == [], (
        "an owned container must not be polled for exec evidence"
    )
    assert not _fbWorkLaneIsUp("owned")

    # The record is reaped; the lane is established from evidence.
    sleepPrevention.fnSweepWorkLaneKeepAlives(
        StateAppFake(), {"docker": connectionDocker},
    )
    assert _fbWorkLaneIsUp("owned")

    # A new browser claims it. The lane must survive the claim.
    connectionDocker.listExecQueries.clear()
    sleepPrevention.fnSweepWorkLaneKeepAlives(
        StateAppFake(dictContainerOwners={"owned": object()}),
        {"docker": connectionDocker},
    )
    assert connectionDocker.listExecQueries == ["id-owned"], (
        "an ESTABLISHED lane must keep being re-decided after a claim"
    )
    assert _fbWorkLaneIsUp("owned")


def testAnUnreadableDaemonKeepsTheMachineAwake():
    """The two errors are not symmetric, so the tie is broken toward awake.

    Withdrawing a keep-alive under a running multi-day job costs the
    job; holding one nothing needs costs some battery.
    """
    sleepPrevention.fnSweepWorkLaneKeepAlives(
        StateAppFake(), {"docker": ConnectionDockerUnreadable()},
    )
    assert _fbWorkLaneIsUp("frozen")


def testAnUnlistableDaemonChangesNothing():
    """A daemon that cannot be listed must not be read as "nothing runs".

    Answering an unreachable daemon as an empty running set would stop
    every work lane on the host the first time Docker hiccuped.
    """
    sleepPrevention.fnSweepWorkLaneKeepAlives(
        StateAppFake(),
        {"docker": ConnectionDockerFake(
            {"proj": "id-proj"}, {"id-proj": ["exec-one"]},
        )},
    )
    assert _fbWorkLaneIsUp("proj")

    class ConnectionDockerDown:
        def flistGetRunningContainers(self):
            raise RuntimeError("daemon unreachable")

    sleepPrevention.fnSweepWorkLaneKeepAlives(
        StateAppFake(), {"docker": ConnectionDockerDown()},
    )
    assert _fbWorkLaneIsUp("proj")


def testTheTwoLanesCannotStopEachOther():
    """A work-lane registry name is unreachable by a container name.

    Docker container names match ``[a-zA-Z0-9][a-zA-Z0-9_.-]*``, so no
    container can be called anything that collides with a work-lane
    name. Without that the session lane's stop — which every release
    and reap performs — would take the work lane down with it, which is
    the coupling this module exists to break.
    """
    sLaneName = sleepPrevention.fsWorkLaneKeepAliveName("proj")
    assert sleepPrevention.S_WORK_LANE_SEPARATOR in sLaneName
    assert sLaneName != "proj"

    sleepPrevention.fnSweepWorkLaneKeepAlives(
        StateAppFake(),
        {"docker": ConnectionDockerFake(
            {"proj": "id-proj"}, {"id-proj": ["exec-one"]},
        )},
    )
    assert _fbWorkLaneIsUp("proj")

    keepAliveManager.fnStopKeepAlive("proj")

    assert _fbWorkLaneIsUp("proj"), (
        "stopping the SESSION lane must leave the work lane standing"
    )
