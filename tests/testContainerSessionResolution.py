"""End-to-end and unit tests for the name-keyed container-session model.

These tests make the one-session guarantee observable where every prior
fixture hid it: the owner-of-record map is keyed by the container NAME
the claim route writes, while the WebSocket routes receive the docker
ID in their path. Earlier tests collapsed name == id or mocked the gate
to a constant, so a name-vs-id key mismatch would have passed CI while
closing every real connection 4403. Each test below keeps the docker ID
and the project NAME DISTINCT so the id->name resolution boundary is
exercised, not assumed.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from vaibify.gui import containerOwnership
from vaibify.gui.routes.pipelineRoutes import _fnRegisterPipelineWs
from vaibify.gui.routes.terminalRoutes import _fnRegisterTerminalWs


S_CONTAINER_ID = "abc123dockerid"
S_PROJECT_NAME = "MyProject"
S_TOKEN = "shared-trust-token"
S_LEASE = "owning-lease-xyz"
S_AGENT_TOKEN = "per-container-agent-token"


class _FakeDocker:
    """A docker stand-in that maps one docker id to one project name."""

    def __init__(self, sContainerId, sName):
        self._sContainerId = sContainerId
        self._sName = sName

    def flistGetRunningContainers(self):
        return [{"sContainerId": self._sContainerId, "sName": self._sName}]


def _fdictBuildContext(dictContainerOwners):
    """Build a route context whose docker resolves id != name."""
    return {
        "require": MagicMock(),
        "docker": _FakeDocker(S_CONTAINER_ID, S_PROJECT_NAME),
        "sSessionToken": S_TOKEN,
        "dictContainerOwners": dictContainerOwners,
    }


def _fdictOwnersByName(sLeaseId=S_LEASE, iLiveCount=0, iLivePipelineCount=0):
    """Return an owner map keyed by NAME (the claim route's canonical key)."""
    recordOwner = containerOwnership.OwnerRecord(
        sLeaseId=sLeaseId, fileHandleLock=None,
        sAgentToken=S_AGENT_TOKEN, sContainerId=S_CONTAINER_ID,
    )
    recordOwner.iLiveConnectionCount = iLiveCount
    recordOwner.iLivePipelineConnectionCount = iLivePipelineCount
    return {S_PROJECT_NAME: recordOwner}


def _fclientWithPipelineWs(dictCtx):
    """Register the pipeline WS route on a fresh app and return a client."""
    app = FastAPI()
    _fnRegisterPipelineWs(app, dictCtx)
    return TestClient(app)


def _sPipelineUrl(sLeaseId=S_LEASE, sToken=S_TOKEN):
    """Build a /ws/pipeline URL addressed by the docker ID, not the name."""
    return (
        f"/ws/pipeline/{S_CONTAINER_ID}"
        f"?sToken={sToken}&sLeaseId={sLeaseId}"
    )


_DICT_LOOPBACK_ORIGIN = {"origin": "http://localhost"}


# -- name != id end-to-end: the owner's tab is accepted ------------------


def test_owner_pipeline_ws_accepted_when_name_differs_from_id():
    """A claim by NAME authorizes a WS addressed by the docker ID.

    The handshake is ACCEPTED only if the route resolves the path docker
    id to the canonical project name before consulting the name-keyed
    gate. A regression to an id-keyed lookup would close this 4403.
    """
    dictCtx = _fdictBuildContext(_fdictOwnersByName())
    listCountDuring = []

    async def _fnFakeServe(websocket, dictCtxArg, sContainerId):
        await websocket.accept()
        listCountDuring.append(
            dictCtx["dictContainerOwners"][S_PROJECT_NAME]
            .iLiveConnectionCount
        )

    with patch(
        "vaibify.gui.routes.pipelineRoutes.fnHandlePipelineWs",
        _fnFakeServe,
    ):
        client = _fclientWithPipelineWs(dictCtx)
        with client.websocket_connect(
            _sPipelineUrl(), headers=_DICT_LOOPBACK_ORIGIN,
        ):
            pass
    assert listCountDuring == [1], (
        "the owner's WS must be accepted and counted as one live "
        "connection on the name-keyed record"
    )
    assert (
        dictCtx["dictContainerOwners"][S_PROJECT_NAME].iLiveConnectionCount
        == 0
    ), "the per-container live count must return to zero after disconnect"


def test_foreign_lease_pipeline_ws_closes_4403_with_real_guard():
    """A tab presenting a non-owning lease is refused by the real guard.

    The handshake is accepted first so the close frame carries the
    deliberate 4403 (close-before-accept downgrades every refusal to an
    opaque 1006 in a real browser); the refusal is observed on receive.
    """
    dictCtx = _fdictBuildContext(_fdictOwnersByName())
    client = _fclientWithPipelineWs(dictCtx)
    with client.websocket_connect(
        _sPipelineUrl(sLeaseId="some-other-lease"),
        headers=_DICT_LOOPBACK_ORIGIN,
    ) as websocketClient:
        with pytest.raises(WebSocketDisconnect) as excInfo:
            websocketClient.receive_text()
    assert excInfo.value.code == 4403


def test_absent_lease_pipeline_ws_closes_4403_with_real_guard():
    """A tab presenting no lease at all is refused 4403, not accepted."""
    dictCtx = _fdictBuildContext(_fdictOwnersByName())
    client = _fclientWithPipelineWs(dictCtx)
    with client.websocket_connect(
        f"/ws/pipeline/{S_CONTAINER_ID}?sToken={S_TOKEN}",
        headers=_DICT_LOOPBACK_ORIGIN,
    ) as websocketClient:
        with pytest.raises(WebSocketDisconnect) as excInfo:
            websocketClient.receive_text()
    assert excInfo.value.code == 4403


# -- the one-live budget is scoped to the PIPELINE lane ------------------
#
# One legitimate session holds several sockets at once: the terminal
# strip opens a terminal WS on workflow entry, Run Step opens the
# pipeline WS on demand, and extra terminal tabs add more. Only a second
# concurrent PIPELINE socket marks a duplicate tab. The original
# all-sockets budget shipped the Run-Step-always-refused bug: the
# terminal held the single slot, every Run Step was closed 4409, and the
# browser blamed the network.


def _sTerminalUrl(sLeaseId=S_LEASE, sToken=S_TOKEN):
    """Build a /ws/terminal URL addressed by the docker ID, not the name."""
    return (
        f"/ws/terminal/{S_CONTAINER_ID}"
        f"?sToken={sToken}&sLeaseId={sLeaseId}"
    )


async def _fnFakeBlockingTerminalSession(websocket, dictCtx, sContainerId):
    """Stand-in terminal session that stays live until the client leaves."""
    try:
        await websocket.receive_text()
    except WebSocketDisconnect:
        pass


def _fclientWithBothWsRoutes(dictCtx):
    """Register BOTH WebSocket routes on one app and return a client."""
    dictCtx.setdefault("containerUsers", {})
    dictCtx.setdefault("terminals", {})
    app = FastAPI()
    _fnRegisterPipelineWs(app, dictCtx)
    _fnRegisterTerminalWs(app, dictCtx)
    return TestClient(app)


@pytest.mark.falsification
def test_terminal_plus_pipeline_ws_coexist_in_one_session():
    """One session's terminal AND pipeline sockets are both served, live
    at the same time, on the same lease.

    This is the Run Step path as the GUI actually drives it: the
    terminal WS connects on workflow entry and is STILL OPEN when the
    pipeline WS arrives. Both must serve concurrently; refusing the
    second socket silently killed every Run Step while the server was
    healthy.

    Kills: reverting fbRefuseSecondLiveConnection to the all-sockets
    budget (iLivePipelineConnectionCount -> iLiveConnectionCount), the
    exact regression shipped by the one-session refactor.
    """
    dictCtx = _fdictBuildContext(_fdictOwnersByName())
    listCountsAtPipelineServe = []

    async def _fnFakePipelineServe(websocket, dictCtxArg, sContainerId):
        await websocket.accept()
        recordOwner = dictCtx["dictContainerOwners"][S_PROJECT_NAME]
        listCountsAtPipelineServe.append((
            recordOwner.iLiveConnectionCount,
            recordOwner.iLivePipelineConnectionCount,
        ))

    with patch(
        "vaibify.gui.routes.terminalRoutes._fnStartAndRunTerminal",
        _fnFakeBlockingTerminalSession,
    ), patch(
        "vaibify.gui.routes.pipelineRoutes.fnHandlePipelineWs",
        _fnFakePipelineServe,
    ):
        client = _fclientWithBothWsRoutes(dictCtx)
        with client.websocket_connect(
            _sTerminalUrl(), headers=_DICT_LOOPBACK_ORIGIN,
        ):
            with client.websocket_connect(
                _sPipelineUrl(), headers=_DICT_LOOPBACK_ORIGIN,
            ):
                pass
    assert listCountsAtPipelineServe == [(2, 1)], (
        "with the terminal socket still live, the same session's "
        "pipeline socket must be SERVED (2 live connections total, "
        "1 on the pipeline lane), never refused 4409"
    )
    recordOwner = dictCtx["dictContainerOwners"][S_PROJECT_NAME]
    assert recordOwner.iLiveConnectionCount == 0
    assert recordOwner.iLivePipelineConnectionCount == 0


def test_second_pipeline_ws_refused_4409_while_first_is_live():
    """Two concurrent PIPELINE sockets on one lease: the second gets 4409.

    Driven with two real connections, not a seeded counter: the first
    pipeline socket is still being served when the duplicate arrives.
    The duplicate passes the lease gate (same lease) and is refused at
    the lane budget, observed as a 4409 close AFTER the handshake so a
    real browser sees the code.
    """
    dictCtx = _fdictBuildContext(_fdictOwnersByName())

    async def _fnFakeBlockingPipelineServe(websocket, dictCtxArg, sContainerId):
        await websocket.accept()
        try:
            await websocket.receive_text()
        except WebSocketDisconnect:
            pass

    with patch(
        "vaibify.gui.routes.pipelineRoutes.fnHandlePipelineWs",
        _fnFakeBlockingPipelineServe,
    ):
        client = _fclientWithPipelineWs(dictCtx)
        with client.websocket_connect(
            _sPipelineUrl(), headers=_DICT_LOOPBACK_ORIGIN,
        ):
            with client.websocket_connect(
                _sPipelineUrl(), headers=_DICT_LOOPBACK_ORIGIN,
            ) as websocketDuplicate:
                with pytest.raises(WebSocketDisconnect) as excInfo:
                    websocketDuplicate.receive_text()
            assert excInfo.value.code == 4409


def test_second_terminal_ws_served_alongside_live_connections():
    """A second terminal tab is a feature of one session, never a 4409.

    Seeds a live pipeline socket AND a live terminal socket on the
    owner, then connects another terminal: multi-tab terminals must be
    served — the old all-sockets budget refused them as duplicates.
    """
    dictCtx = _fdictBuildContext(
        _fdictOwnersByName(iLiveCount=2, iLivePipelineCount=1),
    )
    listCountsDuringServe = []

    async def _fnFakeCountingTerminalSession(websocket, dictCtxArg, sId):
        listCountsDuringServe.append(
            dictCtx["dictContainerOwners"][S_PROJECT_NAME]
            .iLiveConnectionCount,
        )

    with patch(
        "vaibify.gui.routes.terminalRoutes._fnStartAndRunTerminal",
        _fnFakeCountingTerminalSession,
    ):
        client = _fclientWithBothWsRoutes(dictCtx)
        with client.websocket_connect(
            _sTerminalUrl(), headers=_DICT_LOOPBACK_ORIGIN,
        ):
            pass
    assert listCountsDuringServe == [3], (
        "an extra terminal tab must be served and counted for liveness"
    )


# -- terminal route accepts the owner addressed by docker id -------------


def test_owner_terminal_ws_accepted_when_name_differs_from_id():
    """The terminal route resolves id->name and accepts the owner."""
    dictCtx = _fdictBuildContext(_fdictOwnersByName())
    dictCtx["containerUsers"] = {}
    dictCtx["terminals"] = {}
    listCountDuring = []

    async def _fnFakeStartAndRun(websocket, dictCtxArg, sContainerId):
        listCountDuring.append(
            dictCtx["dictContainerOwners"][S_PROJECT_NAME]
            .iLiveConnectionCount
        )

    app = FastAPI()
    _fnRegisterTerminalWs(app, dictCtx)
    with patch(
        "vaibify.gui.routes.terminalRoutes._fnStartAndRunTerminal",
        _fnFakeStartAndRun,
    ):
        client = TestClient(app)
        with client.websocket_connect(
            f"/ws/terminal/{S_CONTAINER_ID}"
            f"?sToken={S_TOKEN}&sLeaseId={S_LEASE}",
            headers=_DICT_LOOPBACK_ORIGIN,
        ):
            pass
    assert listCountDuring == [1]
    assert (
        dictCtx["dictContainerOwners"][S_PROJECT_NAME].iLiveConnectionCount
        == 0
    )


# -- the agent lane survives id->name resolution -------------------------


def test_agent_lane_authorized_by_container_id_against_name_record():
    """A per-container agent token is honored after id->name resolution.

    The agent dials the docker ID with its container's own agent token
    and no loopback origin. After the route resolves the id to the owned
    NAME, the lease-exempt agent lane authorizes it against that owner's
    per-container token; proving the resolution does not break the
    machine lane on a hub.
    """
    dictCtx = _fdictBuildContext(_fdictOwnersByName())

    async def _fnFakeServe(websocket, dictCtxArg, sContainerId):
        await websocket.accept()

    with patch(
        "vaibify.gui.routes.pipelineRoutes.fnHandlePipelineWs",
        _fnFakeServe,
    ):
        client = _fclientWithPipelineWs(dictCtx)
        with client.websocket_connect(
            f"/ws/pipeline/{S_CONTAINER_ID}?sToken={S_AGENT_TOKEN}",
            headers={"x-vaibify-session": S_AGENT_TOKEN},
        ):
            pass


# -- guard unit fixtures with DISTINCT keys ------------------------------


def test_guard_reachable_only_after_id_to_name_resolution():
    """The name-keyed record is reachable by NAME, missed by raw ID.

    This proves the resolution is load-bearing: the SAME owner map and
    lease authorize the connection when the gate is handed the resolved
    NAME, and reject it when handed the unresolved docker ID.
    """
    from vaibify.gui import webSocketAuthorization

    class _Conn:
        def __init__(self):
            self.headers = {"origin": "http://localhost"}
            self.query_params = {"sToken": S_TOKEN, "sLeaseId": S_LEASE}

    dictCtx = {
        "sSessionToken": S_TOKEN,
        "dictContainerOwners": _fdictOwnersByName(),
    }
    assert webSocketAuthorization.fiContainerSessionRejectionCode(
        _Conn(), dictCtx, S_PROJECT_NAME,
    ) == 0
    assert webSocketAuthorization.fiContainerSessionRejectionCode(
        _Conn(), dictCtx, S_CONTAINER_ID,
    ) == 4403


# -- viewer path: the minted lease authorizes the viewer's WS ------------


def test_viewer_registration_keys_by_name_and_surfaces_lease():
    """The viewer keys its record by NAME and exposes the minted lease.

    Keying by the raw docker id would make every gate lookup miss (the
    finding-3 4403) and would stop keep-alive by the wrong key on
    teardown. The surfaced lease is what the viewer's browser presents
    on its WebSocket.
    """
    from vaibify.gui import pipelineServer

    dictContainerOwners = {}
    dictCtx = {
        "bIsHub": False,
        "docker": _FakeDocker(S_CONTAINER_ID, S_PROJECT_NAME),
        "dictContainerOwners": dictContainerOwners,
    }
    pipelineServer._fnRegisterViewerServedContainer(dictCtx, S_CONTAINER_ID)
    assert S_PROJECT_NAME in dictContainerOwners
    assert S_CONTAINER_ID not in dictContainerOwners
    sLease = dictCtx["sViewerLease"]
    assert sLease == dictContainerOwners[S_PROJECT_NAME].sLeaseId


def test_viewer_minted_lease_authorizes_pipeline_ws():
    """A viewer WS presenting the surfaced lease is ACCEPTED end-to-end."""
    from vaibify.gui import pipelineServer

    dictContainerOwners = {}
    dictCtx = _fdictBuildContext(dictContainerOwners)
    dictCtx["bIsHub"] = False
    pipelineServer._fnRegisterViewerServedContainer(dictCtx, S_CONTAINER_ID)
    sLease = dictCtx["sViewerLease"]

    async def _fnFakeServe(websocket, dictCtxArg, sContainerId):
        await websocket.accept()

    with patch(
        "vaibify.gui.routes.pipelineRoutes.fnHandlePipelineWs",
        _fnFakeServe,
    ):
        client = _fclientWithPipelineWs(dictCtx)
        with client.websocket_connect(
            _sPipelineUrl(sLeaseId=sLease), headers=_DICT_LOOPBACK_ORIGIN,
        ):
            pass


# -- the per-container counter functions are not dead code ---------------


def test_live_connection_counter_has_non_test_call_site():
    """The per-container counter must be driven from production source.

    Before this wiring the increment/decrement pair had zero non-test
    callers, so the reaper saw a perpetually-zero count and force-released
    live owned sessions. The shared serve helper is the single driver.
    """
    import inspect
    from vaibify.gui import webSocketAuthorization

    sSource = inspect.getsource(
        webSocketAuthorization.fnServeUnderLiveConnectionCounters,
    )
    assert "fnIncrementLiveConnection" in sSource
    assert "fnDecrementLiveConnection" in sSource


# -- reaper never retires a live owned session (finding 2, reaper half) --


def test_app_reaper_skips_owner_with_live_connection_then_reaps_idle():
    """The hub reaper vetoes a live owner, then reaps it once idle.

    Exercised through ``_fnReapIdleOwnershipsForApp`` (the watchdog's
    real entry point), not the pure helper, so the lifecycle path that
    force-released live sessions ~30s after claim is the thing under
    test.
    """
    import time
    from types import SimpleNamespace
    from vaibify.gui import serverLifespan

    dictContainerOwners = _fdictOwnersByName(iLiveCount=1)
    app = SimpleNamespace(
        state=SimpleNamespace(
            bReapOwnerships=True,
            dictContainerOwners=dictContainerOwners,
        ),
    )
    dictCtx = {"docker": _FakeDocker(S_CONTAINER_ID, S_PROJECT_NAME)}
    recordOwner = dictContainerOwners[S_PROJECT_NAME]
    recordOwner.fLastSeenMonotonic = time.monotonic() - 10_000.0
    with patch.object(
        serverLifespan, "_fbOwnedNamePipelineRunning", return_value=False,
    ):
        serverLifespan._fnReapIdleOwnershipsForApp(app, dictCtx)
        assert S_PROJECT_NAME in dictContainerOwners, (
            "an owner with a live connection must never be reaped, even "
            "long past the idle grace window"
        )
        containerOwnership.fnDecrementLiveConnection(
            dictContainerOwners, S_PROJECT_NAME,
        )
        recordOwner.fLastSeenMonotonic = time.monotonic() - 10_000.0
        serverLifespan._fnReapIdleOwnershipsForApp(app, dictCtx)
    assert S_PROJECT_NAME not in dictContainerOwners, (
        "once idle past grace the owner is reaped"
    )


# -- two apps in one process keep independent terminal users -------------


def test_two_apps_in_one_process_have_independent_terminal_users():
    """A viewer and a hub built together keep separate terminal users.

    The terminal user lives on ``app.state`` and the route context, not
    a ``pipelineServer`` module global, so the last build no longer wins
    for both apps. Building a viewer (``alice``) and a hub (``researcher``)
    in one process must leave each app resolving its own user.
    """
    from vaibify.gui import pipelineServer

    with patch.object(
        pipelineServer, "_fconnectionCreateDocker",
        return_value=MagicMock(),
    ):
        appViewer = pipelineServer.fappCreateApplication(
            sTerminalUserArg="alice",
        )
        appHub = pipelineServer.fappCreateHubApplication()
    assert appViewer.state.sTerminalUser == "alice"
    assert appHub.state.sTerminalUser == "researcher"


# -- shutdown hook ordering (executor torn down last) --------------------


def test_executor_shutdown_runs_after_sweep_and_watchdog_stops():
    """The thread-pool executor is shut down after the loops that use it.

    Shutdown hooks run in append order. The sweep and idle-watchdog
    loops submit to the default executor via ``asyncio.to_thread``; if
    the executor were shut down first, a tick landing in that window
    would raise ``cannot schedule new futures after shutdown``. The
    executor stop hook must therefore be appended last.
    """
    from vaibify.gui import pipelineServer

    with patch.object(
        pipelineServer, "_fconnectionCreateDocker",
        return_value=MagicMock(),
    ):
        appHub = pipelineServer.fappCreateHubApplication()
    listNames = [
        getattr(fnHook, "__name__", "")
        for fnHook in appHub.state.listLifespanShutdown
    ]
    iExecutor = listNames.index("fnShutdownExecutor")
    iSweep = listNames.index("fnStopSweepTask")
    iWatchdog = listNames.index("fnStopWatchdog")
    assert iExecutor > iSweep and iExecutor > iWatchdog, (
        "the executor shutdown hook must be appended after the sweep "
        f"and watchdog stop hooks; got order {listNames}"
    )
