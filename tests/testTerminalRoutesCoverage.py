"""Tests for vaibify.gui.routes.terminalRoutes — the terminal is back.

The interactive terminal was withdrawn on 2026-08-02 because its
containment could not be PROVEN: a descendant that calls ``setsid``
leaves the recorded process group, so release, hand-over and shutdown
could not honestly report the container quiet. It is back for
containers on 2026-08-11 by the other resolution — vaibify no longer
makes the claim the terminal could falsify. A container in which a
terminal ran reports quiescence UNPROVEN and routes to ``vaibify
reconcile``; it does not report quiet.

The tests in this file therefore changed shape twice, and neither
change was a test being deleted to make a failure go away. The
withdrawal replaced serving assertions with refusal assertions because
the serving paths were gone; this restoration replaces the
refuses-everything assertions because refusing everything is no longer
the contract. What is pinned now:

* the ownership gate decides — an unauthorized caller gets an
  AUTHORIZATION code and is never served;
* a HOST project is SERVED (2026-08-15 ruling) — by the PTY twin,
  never the Docker session class, and without the daemon requirement a
  host-only machine cannot meet;
* every refusal still accepts before closing, so a browser reads the
  deliberate code rather than an opaque 1006.

One property the withdrawal added is deliberately NOT preserved: the
handler resolves the container name before the gate, so a caller that
can reach the socket can still distinguish a real id from a fabricated
one. That is not a terminal regression — ``/ws/pipeline`` has had the
identical ordering throughout, so the oracle is a property of the
WebSocket gates in general, and fixing it in one lane while leaving the
live one open would be theatre.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vaibify.gui import webSocketAuthorization
from vaibify.gui.routes import terminalRoutes


def _flistRegisterAndCaptureHandlers(dictCtx):
    """Register the terminal route on a stub app; return its handlers."""
    from vaibify.gui.routes import terminalRoutes

    app = MagicMock()
    listRegistered = []

    def fnCaptureRoute(sPath):
        def fnDecorator(fnHandler):
            listRegistered.append(fnHandler)
            return fnHandler
        return fnDecorator

    app.websocket = fnCaptureRoute
    terminalRoutes._fnRegisterTerminalWs(app, dictCtx)
    return listRegistered


def _fmockWebSocket():
    """Build a mock WebSocket that records accept/close."""
    mockWs = AsyncMock()
    mockWs.headers = {"origin": "http://localhost:8000"}
    mockWs.query_params = {"sToken": "any-token", "sLeaseId": "any-lease"}
    mockWs.close = AsyncMock()
    mockWs.accept = AsyncMock()
    return mockWs


class TestTerminalWsIsGatedNotWithdrawn:
    """The ownership gate decides; the route no longer refuses everyone."""

    @pytest.mark.asyncio
    async def test_an_unauthorized_caller_is_refused_by_the_gate(self):
        """Standing is checked before anything is built.

        The refusal code must come from the AUTHORIZATION vocabulary,
        so a client can tell a bad credential from a feature that is
        not available.
        """
        listHandlers = _flistRegisterAndCaptureHandlers({})
        assert len(listHandlers) == 1, (
            "exactly one handler may answer the terminal path"
        )
        mockWs = _fmockWebSocket()

        await listHandlers[0](mockWs, "container-1")

        mockWs.accept.assert_awaited_once()
        iCode = mockWs.close.await_args.kwargs["code"]
        assert iCode in (
            webSocketAuthorization.I_REJECT_BAD_ORIGIN,
            webSocketAuthorization.I_REJECT_BAD_TOKEN,
            webSocketAuthorization.I_REJECT_FOREIGN_LEASE,
        ), iCode

    @pytest.mark.asyncio
    async def test_an_unauthorized_caller_builds_no_session(self):
        """A refused dial-in must not create a containment record.

        A TerminalSession built before the gate would put a
        quarantine-bearing operation on a container for a caller with
        no standing in it.
        """
        listBuilt = []
        with patch.object(
            terminalRoutes, "TerminalSession",
            lambda *tArgs, **dictKeywords: listBuilt.append(tArgs),
        ):
            await _flistRegisterAndCaptureHandlers({})[0](
                _fmockWebSocket(), "container-1",
            )
        assert listBuilt == []

    @pytest.mark.asyncio
    @pytest.mark.falsification
    async def test_a_host_project_is_served_by_the_pty_twin(self):
        """A host project gets the host session, never the Docker one.

        This test refused with its own code until 2026-08-15, when the
        ruling landed that the host terminal exists: a real PTY on the
        researcher's own machine, same gate, same seam, same
        quiescence cost.

        Kills: the host branch never firing — a host project falls
        through to the daemon requirement and the Docker session
        class, and the researcher is told to install Docker for a
        project that never wanted one.
        """
        listServed = []

        async def fnRecordServe(
            app, websocket, dictCtx, sContainerId, sName,
            bHostProject=False,
        ):
            listServed.append(bHostProject)

        with patch.object(
            terminalRoutes, "fiContainerSessionRejectionCode",
            lambda *tArgs, **dictKeywords: 0,
        ), patch(
            "vaibify.config.registryManager.fbIsHostProject",
            lambda sName: True,
        ), patch.object(
            terminalRoutes, "_fnTrackAndServeTerminal", fnRecordServe,
        ):
            await _flistRegisterAndCaptureHandlers({})[0](
                _fmockWebSocket(), "a-host-project",
            )
        assert listServed == [True], (
            "a host project must be served through the host branch"
        )

    @pytest.mark.asyncio
    @pytest.mark.falsification
    async def test_a_container_project_is_served_by_the_docker_leg(self):
        """The symmetric direction: containers keep the exec session.

        Kills: branching every project onto the host PTY — a container
        project's shell would then fork on the HUB's machine instead
        of exec-ing into the container, which is a sandbox escape
        wearing a terminal's face.
        """
        listServed = []

        async def fnRecordServe(
            app, websocket, dictCtx, sContainerId, sName,
            bHostProject=False,
        ):
            listServed.append(bHostProject)

        listRequired = []
        dictCtx = {"require": lambda *aArgs: listRequired.append(aArgs)}
        with patch.object(
            terminalRoutes, "fiContainerSessionRejectionCode",
            lambda *tArgs, **dictKeywords: 0,
        ), patch(
            "vaibify.config.registryManager.fbIsHostProject",
            lambda sName: False,
        ), patch.object(
            terminalRoutes, "_fnTrackAndServeTerminal", fnRecordServe,
        ):
            await _flistRegisterAndCaptureHandlers(dictCtx)[0](
                _fmockWebSocket(), "container-1",
            )
        assert listServed == [False], (
            "a container project must be served through the Docker "
            "branch"
        )
        assert len(listRequired) == 1, (
            "the container branch must still require the daemon"
        )

    @pytest.mark.asyncio
    async def test_the_host_branch_precedes_the_daemon_requirement(self):
        """A host-only machine has no daemon to require.

        Asking ``require`` first would answer "install Docker" about a
        project that never wanted one -- the same ordering the
        container-only HTTP routes fixed. The branch now SERVES
        instead of refusing, and the daemon skip is unchanged.
        """
        listRequired = []
        dictCtx = {"require": lambda *aArgs: listRequired.append(aArgs)}

        async def fnSwallowServe(*tArgs, **dictKeywords):
            return None

        with patch.object(
            terminalRoutes, "fiContainerSessionRejectionCode",
            lambda *tArgs, **dictKeywords: 0,
        ), patch(
            "vaibify.config.registryManager.fbIsHostProject",
            lambda sName: True,
        ), patch.object(
            terminalRoutes, "_fnTrackAndServeTerminal", fnSwallowServe,
        ):
            await _flistRegisterAndCaptureHandlers(dictCtx)[0](
                _fmockWebSocket(), "a-host-project",
            )
        assert listRequired == []

    @pytest.mark.asyncio
    async def test_accepts_before_closing_so_the_browser_sees_the_code(self):
        """A close before accept reads to a browser as an opaque 1006.

        The researcher would be told "cannot reach server" for a
        deliberate refusal -- it has to be legible or it becomes a
        support question about the network.
        """
        listCalls = []
        mockWs = _fmockWebSocket()
        mockWs.accept = AsyncMock(
            side_effect=lambda *a, **k: listCalls.append("accept"),
        )
        mockWs.close = AsyncMock(
            side_effect=lambda *a, **k: listCalls.append("close"),
        )

        await _flistRegisterAndCaptureHandlers({})[0](mockWs, "container-1")

        assert listCalls == ["accept", "close"]


class TestWithdrawalCodeIsNotAnAuthorizationCode:
    """Each non-authorization refusal must be distinguishable."""

    def test_the_host_code_is_distinct_from_every_other_refusal(self):
        """Three different things, three different codes.

        "not built for this kind of project", "the feature is gone",
        and "your credential is bad" send a researcher to three
        different places. A client that conflates them tells them to
        re-claim a project that is already theirs, or to wait for a
        feature that is not coming.
        """
        iNotOnHost = webSocketAuthorization.I_REJECT_TERMINAL_NOT_ON_HOST
        tOtherCodes = (
            webSocketAuthorization.I_REJECT_AUTHORIZED,
            webSocketAuthorization.I_REJECT_BAD_ORIGIN,
            webSocketAuthorization.I_REJECT_BAD_TOKEN,
            webSocketAuthorization.I_REJECT_FOREIGN_LEASE,
            webSocketAuthorization.I_REJECT_DUPLICATE_SESSION,
            webSocketAuthorization.I_REJECT_TERMINAL_DISABLED,
            webSocketAuthorization.I_REJECT_POISONED,
        )
        assert iNotOnHost not in tOtherCodes

    def test_code_is_distinct_from_every_authorization_refusal(self):
        iWithdrawn = webSocketAuthorization.I_REJECT_TERMINAL_DISABLED
        tAuthorizationCodes = (
            webSocketAuthorization.I_REJECT_AUTHORIZED,
            webSocketAuthorization.I_REJECT_BAD_ORIGIN,
            webSocketAuthorization.I_REJECT_BAD_TOKEN,
            webSocketAuthorization.I_REJECT_FOREIGN_LEASE,
            webSocketAuthorization.I_REJECT_DUPLICATE_SESSION,
        )
        assert iWithdrawn not in tAuthorizationCodes, (
            "a client that cannot tell a withdrawn feature from a "
            "rejected credential would tell the researcher to re-claim "
            "a container that is already theirs"
        )
