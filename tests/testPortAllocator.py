"""Tests for vaibify.cli.portAllocator."""

import dataclasses
import socket
from unittest.mock import patch

import pytest


def _sockBindOn(iPort):
    """Bind a socket on 127.0.0.1:iPort and return it (caller closes)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", iPort))
    return sock


@dataclasses.dataclass
class _StubProjectConfig:
    """Minimal stand-in for ProjectConfig used by fiResolveProjectPort."""

    sProjectName: str = "demo"
    iDashboardPort: int = 0


def _ftReservedPort():
    """Return (iPort, sockHolder); caller closes sockHolder."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    return sock.getsockname()[1], sock


def test_fbIsPortFree_true_when_unbound():
    from vaibify.cli.portAllocator import fbIsPortFree
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    iPort = sock.getsockname()[1]
    sock.close()
    assert fbIsPortFree(iPort) is True


def test_fbIsPortFree_false_when_bound():
    from vaibify.cli.portAllocator import fbIsPortFree
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    iPort = sock.getsockname()[1]
    try:
        assert fbIsPortFree(iPort) is False
    finally:
        sock.close()


def test_fbIsPortFree_sets_reuseaddr_so_time_wait_looks_free():
    """A TIME_WAIT socket on macOS would block a bind without SO_REUSEADDR.

    We can't easily synthesise TIME_WAIT in a unit test, but we can
    assert the helper sets SO_REUSEADDR on the probe socket — without
    that flag the per-project-stable-port survival contract collapses
    every time a server is restarted within the TIME_WAIT window.
    """
    from unittest.mock import MagicMock, patch
    import socket as socketModule
    from vaibify.cli.portAllocator import fbIsPortFree
    mockSocket = MagicMock()
    with patch.object(
        socketModule, "socket", return_value=mockSocket,
    ):
        fbIsPortFree(54321)
    mockSocket.setsockopt.assert_any_call(
        socket.SOL_SOCKET, socket.SO_REUSEADDR, 1,
    )


def test_fiPickFreePort_returns_preferred_when_free():
    from vaibify.cli.portAllocator import fiPickFreePort
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    iPreferred = sock.getsockname()[1]
    sock.close()
    assert fiPickFreePort(iPreferred=iPreferred, iMaxAttempts=5) == iPreferred


def test_fiPickFreePort_shifts_when_preferred_bound():
    from vaibify.cli.portAllocator import fiPickFreePort
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    iBound = sock.getsockname()[1]
    try:
        iResolved = fiPickFreePort(iPreferred=iBound, iMaxAttempts=20)
        assert iResolved != iBound
        assert iBound < iResolved <= iBound + 19
    finally:
        sock.close()


def test_fiPickFreePort_raises_when_no_port_free():
    from vaibify.cli.portAllocator import fiPickFreePort
    sockets = []
    try:
        sockFirst = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sockFirst.bind(("127.0.0.1", 0))
        iStart = sockFirst.getsockname()[1]
        sockets.append(sockFirst)
        for iOffset in range(1, 3):
            sockExtra = _sockBindOn(iStart + iOffset)
            sockets.append(sockExtra)
        with pytest.raises(RuntimeError, match="No free TCP port"):
            fiPickFreePort(iPreferred=iStart, iMaxAttempts=3)
    except OSError:
        pytest.skip("Could not bind contiguous ports for this test.")
    finally:
        for sock in sockets:
            sock.close()


def test_fiResolvePort_returns_explicit_unchanged():
    from vaibify.cli.portAllocator import fiResolvePort
    assert fiResolvePort(9999) == 9999


def test_fiResolvePort_autopicks_when_none(capsys):
    from vaibify.cli.portAllocator import fiResolvePort
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    iPreferred = sock.getsockname()[1]
    sock.close()
    assert fiResolvePort(None, iPreferred=iPreferred) == iPreferred


def test_fiResolvePort_announces_fallback_on_stderr(capsys):
    from vaibify.cli.portAllocator import fiResolvePort
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    iBound = sock.getsockname()[1]
    try:
        iResolved = fiResolvePort(None, iPreferred=iBound)
        assert iResolved != iBound
        sErr = capsys.readouterr().err
        assert f"Port {iBound} in use" in sErr
    finally:
        sock.close()


# ---------------------------------------------------------------------------
# fiResolveProjectPort
# ---------------------------------------------------------------------------


def test_fiResolveProjectPort_explicit_port_wins_over_persisted():
    """--port overrides whatever vaibify.yml has so users can rescue."""
    from vaibify.cli.portAllocator import fiResolveProjectPort
    config = _StubProjectConfig(iDashboardPort=8050)
    assert fiResolveProjectPort(
        config, iExplicitPort=9999, sConfigPath=None,
    ) == 9999


def test_fiResolveProjectPort_returns_persisted_when_port_free():
    """Persisted port is honoured verbatim when nothing holds it."""
    from vaibify.cli.portAllocator import fiResolveProjectPort
    iPort, sock = _ftReservedPort()
    sock.close()
    config = _StubProjectConfig(iDashboardPort=iPort)
    assert fiResolveProjectPort(
        config, iExplicitPort=None, sConfigPath=None,
    ) == iPort


def test_fiResolveProjectPort_raises_on_foreign_holder():
    """A bind conflict from an unrelated process must fail loudly."""
    from vaibify.cli.portAllocator import (
        PortInUseError, fiResolveProjectPort,
    )
    iPort, sock = _ftReservedPort()
    try:
        config = _StubProjectConfig(
            sProjectName="demo", iDashboardPort=iPort,
        )
        with patch(
            "vaibify.cli.portAllocator._fdictReadContainerLockHolder",
            return_value={"iPid": 9999, "sProjectName": "other"},
        ):
            with pytest.raises(PortInUseError) as eExc:
                fiResolveProjectPort(
                    config, iExplicitPort=None, sConfigPath=None,
                )
        assert eExc.value.iPort == iPort
        assert "demo" in str(eExc.value)
    finally:
        sock.close()


def test_fiResolveProjectPort_waits_for_self_zombie_then_binds():
    """A same-project holder that releases mid-wait should succeed."""
    from vaibify.cli import portAllocator
    iPort, sock = _ftReservedPort()
    listSocks = [sock]

    def _fbStubIsFree(iPortArg):
        if iPortArg != iPort:
            return True
        return not listSocks

    def _fnReleaseAfterFirstCheck(_fSeconds):
        if listSocks:
            listSocks.pop().close()

    with patch.object(
        portAllocator, "fbIsPortFree", side_effect=_fbStubIsFree,
    ), patch.object(
        portAllocator, "_fdictReadContainerLockHolder",
        return_value={"sProjectName": "demo", "iPid": 4242},
    ), patch.object(
        portAllocator.time, "sleep",
        side_effect=_fnReleaseAfterFirstCheck,
    ):
        config = _StubProjectConfig(
            sProjectName="demo", iDashboardPort=iPort,
        )
        assert portAllocator.fiResolveProjectPort(
            config, iExplicitPort=None, sConfigPath=None,
        ) == iPort

    for sockExtra in listSocks:
        sockExtra.close()


def test_fiResolveProjectPort_raises_when_self_zombie_never_releases():
    """The 3s wait is bounded; a stuck zombie still produces a clear error."""
    from vaibify.cli import portAllocator
    iPort, sock = _ftReservedPort()
    try:
        config = _StubProjectConfig(
            sProjectName="demo", iDashboardPort=iPort,
        )
        with patch.object(
            portAllocator, "_fdictReadContainerLockHolder",
            return_value={"sProjectName": "demo", "iPid": 4242},
        ), patch.object(
            portAllocator.time, "sleep",
        ), patch.object(
            portAllocator.time, "monotonic",
            side_effect=[0.0, 100.0, 100.1],
        ):
            with pytest.raises(portAllocator.PortInUseError):
                portAllocator.fiResolveProjectPort(
                    config, iExplicitPort=None, sConfigPath=None,
                )
    finally:
        sock.close()


def test_fiResolveProjectPort_assigns_and_persists_when_zero(capsys):
    """First launch writes the chosen port back to vaibify.yml."""
    from vaibify.cli.portAllocator import fiResolveProjectPort
    config = _StubProjectConfig(
        sProjectName="demo", iDashboardPort=0,
    )
    listPersisted = []

    def _fnRecordSave(configArg, sPath):
        listPersisted.append((configArg.iDashboardPort, sPath))

    iResolved = fiResolveProjectPort(
        config, iExplicitPort=None,
        sConfigPath="/tmp/vaibify.yml",
        fnSaveConfig=_fnRecordSave,
    )
    assert iResolved > 0
    assert config.iDashboardPort == iResolved
    assert listPersisted == [(iResolved, "/tmp/vaibify.yml")]
    sErr = capsys.readouterr().err
    assert "Assigned dashboard port" in sErr


def test_fiResolveProjectPort_warns_when_save_fails(capsys):
    """Save failures degrade to a warning, not a hard error."""
    from vaibify.cli.portAllocator import fiResolveProjectPort
    config = _StubProjectConfig(
        sProjectName="demo", iDashboardPort=0,
    )

    def _fnRaisingSave(_config, _sPath):
        raise OSError("disk full")

    iResolved = fiResolveProjectPort(
        config, iExplicitPort=None,
        sConfigPath="/tmp/vaibify.yml",
        fnSaveConfig=_fnRaisingSave,
    )
    assert iResolved > 0
    sErr = capsys.readouterr().err
    assert "could not persist" in sErr


def test_port_in_use_error_message_names_holder():
    """The error message must give the user something to act on."""
    from vaibify.cli.portAllocator import PortInUseError
    error = PortInUseError(
        8050, "demo",
        {"iPid": 1234, "sProjectName": "other"},
    )
    sMessage = str(error)
    assert "8050" in sMessage
    assert "demo" in sMessage
    assert "1234" in sMessage
    assert "other" in sMessage
    assert "--port" in sMessage


# ---------------------------------------------------------------------------
# fiResolveHubPort
# ---------------------------------------------------------------------------


def test_fiResolveHubPort_explicit_port_wins_over_persisted():
    """--port overrides whatever ~/.vaibify/hub-port.json holds."""
    from vaibify.cli.portAllocator import fiResolveHubPort
    with patch(
        "vaibify.cli.portAllocator._fiReadPersistedHubPort",
        return_value=8050,
    ):
        assert fiResolveHubPort(iExplicitPort=9999) == 9999


def test_fiResolveHubPort_returns_persisted_when_port_free():
    """The previously-bound port is honoured verbatim when free."""
    from vaibify.cli.portAllocator import fiResolveHubPort
    iPort, sock = _ftReservedPort()
    sock.close()
    with patch(
        "vaibify.cli.portAllocator._fiReadPersistedHubPort",
        return_value=iPort,
    ):
        assert fiResolveHubPort(iExplicitPort=None) == iPort


def test_fiResolveHubPort_first_run_assigns_and_persists():
    """With no persisted port, allocator picks one and writes it back."""
    from vaibify.cli.portAllocator import fiResolveHubPort
    listPersisted = []

    def _fnRecordPersist(iPort):
        listPersisted.append(iPort)

    with patch(
        "vaibify.cli.portAllocator._fiReadPersistedHubPort",
        return_value=0,
    ), patch(
        "vaibify.cli.portAllocator._fnPersistHubPortSafely",
        side_effect=_fnRecordPersist,
    ):
        iResolved = fiResolveHubPort(iExplicitPort=None)
    assert iResolved > 0
    assert listPersisted == [iResolved]


def test_fiResolveHubPort_scans_and_warns_on_foreign_holder(capsys):
    """When the persisted port is held by something else, shift + warn."""
    from vaibify.cli.portAllocator import fiResolveHubPort
    iHeld, sockHolder = _ftReservedPort()
    listPersisted = []
    try:
        with patch(
            "vaibify.cli.portAllocator._fiReadPersistedHubPort",
            return_value=iHeld,
        ), patch(
            "vaibify.cli.portAllocator._fdictReadHubSlot",
            return_value={},
        ), patch(
            "vaibify.cli.portAllocator._fnPersistHubPortSafely",
            side_effect=lambda iPort: listPersisted.append(iPort),
        ):
            iResolved = fiResolveHubPort(iExplicitPort=None)
        assert iResolved != iHeld
        assert listPersisted == [iResolved]
        sErr = capsys.readouterr().err
        assert f"{iHeld} is held by another process" in sErr
        assert "old URL will need to be reopened" in sErr
    finally:
        sockHolder.close()


def test_fiResolveHubPort_waits_for_self_zombie_then_binds():
    """A same-role hub zombie that releases mid-wait wins the persisted port."""
    from vaibify.cli import portAllocator
    iPort, sock = _ftReservedPort()
    listSocks = [sock]

    def _fbStubIsFree(iPortArg):
        if iPortArg != iPort:
            return True
        return not listSocks

    def _fnReleaseAfterFirstCheck(_fSeconds):
        if listSocks:
            listSocks.pop().close()

    with patch.object(
        portAllocator, "fbIsPortFree", side_effect=_fbStubIsFree,
    ), patch.object(
        portAllocator, "_fiReadPersistedHubPort", return_value=iPort,
    ), patch.object(
        portAllocator, "_fdictReadHubSlot",
        return_value={"sRole": "hub", "iPort": iPort, "iPid": 1234},
    ), patch.object(
        portAllocator.time, "sleep",
        side_effect=_fnReleaseAfterFirstCheck,
    ):
        assert portAllocator.fiResolveHubPort(
            iExplicitPort=None,
        ) == iPort

    for sockExtra in listSocks:
        sockExtra.close()


def test_fiResolveHubPort_falls_back_when_self_zombie_never_releases():
    """A stuck hub-role holder yields gracefully to a scanned port."""
    from vaibify.cli import portAllocator
    iPort, sock = _ftReservedPort()
    try:
        with patch.object(
            portAllocator, "_fiReadPersistedHubPort",
            return_value=iPort,
        ), patch.object(
            portAllocator, "_fdictReadHubSlot",
            return_value={"sRole": "hub", "iPort": iPort, "iPid": 1234},
        ), patch.object(
            portAllocator, "_fnPersistHubPortSafely",
        ), patch.object(
            portAllocator.time, "sleep",
        ), patch.object(
            portAllocator.time, "monotonic",
            side_effect=[0.0, 100.0, 100.1],
        ):
            iResolved = portAllocator.fiResolveHubPort(
                iExplicitPort=None,
            )
        assert iResolved != iPort
    finally:
        sock.close()


def test_fiResolveHubPort_first_run_announces_on_stderr(capsys):
    """First-run path should tell the user the assigned port + persistence."""
    from vaibify.cli.portAllocator import fiResolveHubPort
    with patch(
        "vaibify.cli.portAllocator._fiReadPersistedHubPort",
        return_value=0,
    ), patch(
        "vaibify.cli.portAllocator._fnPersistHubPortSafely",
    ):
        fiResolveHubPort(iExplicitPort=None)
    sErr = capsys.readouterr().err
    assert "Assigned hub port" in sErr
    assert "persisted" in sErr
