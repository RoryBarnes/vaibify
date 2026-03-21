"""Extended tests for vaibify.gui.terminalSession TerminalSession class."""

from unittest.mock import MagicMock, PropertyMock

import pytest

from vaibify.gui.terminalSession import (
    TerminalSession,
    fsGenerateSessionId,
)


def _fmockDockerConnection():
    """Build a mock Docker connection with exec support."""
    mockConnection = MagicMock()
    mockConnection.fsExecCreate.return_value = "exec_id_123"
    mockSocket = MagicMock()
    mockConnection.fsocketExecStart.return_value = mockSocket
    return mockConnection


# -----------------------------------------------------------------------
# TerminalSession.__init__
# -----------------------------------------------------------------------


def test_init_sets_container_id():
    mockConn = _fmockDockerConnection()
    session = TerminalSession(mockConn, "container_abc")
    assert session._sContainerId == "container_abc"


def test_init_sets_user():
    mockConn = _fmockDockerConnection()
    session = TerminalSession(
        mockConn, "cid", sUser="researcher",
    )
    assert session._sUser == "researcher"


def test_init_generates_session_id():
    mockConn = _fmockDockerConnection()
    session = TerminalSession(mockConn, "cid")
    assert len(session.sSessionId) == 36


def test_init_not_running():
    mockConn = _fmockDockerConnection()
    session = TerminalSession(mockConn, "cid")
    assert session._bRunning is False


# -----------------------------------------------------------------------
# fnStart
# -----------------------------------------------------------------------


def test_fnStart_creates_exec():
    mockConn = _fmockDockerConnection()
    session = TerminalSession(mockConn, "cid", sUser="user")
    session.fnStart()
    mockConn.fsExecCreate.assert_called_once_with(
        "cid", sUser="user",
    )
    assert session._bRunning is True
    assert session._sExecId == "exec_id_123"


def test_fnStart_starts_socket():
    mockConn = _fmockDockerConnection()
    session = TerminalSession(mockConn, "cid")
    session.fnStart()
    mockConn.fsocketExecStart.assert_called_once_with(
        "exec_id_123",
    )


# -----------------------------------------------------------------------
# fnSendInput
# -----------------------------------------------------------------------


def test_fnSendInput_sends_data():
    mockConn = _fmockDockerConnection()
    session = TerminalSession(mockConn, "cid")
    session.fnStart()
    session.fnSendInput(b"ls\n")
    mockSocket = session._socketExec
    mockSocket._sock.sendall.assert_called_with(b"ls\n")


def test_fnSendInput_does_nothing_when_stopped():
    mockConn = _fmockDockerConnection()
    session = TerminalSession(mockConn, "cid")
    session.fnSendInput(b"ls\n")


# -----------------------------------------------------------------------
# fbaReadOutput
# -----------------------------------------------------------------------


def test_fbaReadOutput_returns_data():
    mockConn = _fmockDockerConnection()
    session = TerminalSession(mockConn, "cid")
    session.fnStart()
    session._socketExec._sock.recv.return_value = b"output"
    baResult = session.fbaReadOutput()
    assert baResult == b"output"


def test_fbaReadOutput_returns_empty_when_stopped():
    mockConn = _fmockDockerConnection()
    session = TerminalSession(mockConn, "cid")
    baResult = session.fbaReadOutput()
    assert baResult == b""


def test_fbaReadOutput_handles_blocking_error():
    mockConn = _fmockDockerConnection()
    session = TerminalSession(mockConn, "cid")
    session.fnStart()
    session._socketExec._sock.recv.side_effect = (
        BlockingIOError
    )
    baResult = session.fbaReadOutput()
    assert baResult == b""


# -----------------------------------------------------------------------
# fnResize
# -----------------------------------------------------------------------


def test_fnResize_calls_exec_resize():
    mockConn = _fmockDockerConnection()
    session = TerminalSession(mockConn, "cid")
    session.fnStart()
    session.fnResize(24, 80)
    mockConn.fnExecResize.assert_called_once_with(
        "exec_id_123", 24, 80,
    )


def test_fnResize_no_exec_id_skips():
    mockConn = _fmockDockerConnection()
    session = TerminalSession(mockConn, "cid")
    session.fnResize(24, 80)
    mockConn.fnExecResize.assert_not_called()


# -----------------------------------------------------------------------
# fnKillForeground
# -----------------------------------------------------------------------


def test_fnKillForeground_sends_signals():
    mockConn = _fmockDockerConnection()
    session = TerminalSession(mockConn, "cid")
    session.fnStart()
    session.fnKillForeground()
    listCalls = session._socketExec._sock.sendall.call_args_list
    baFirstArg = listCalls[-2][0][0]
    baSecondArg = listCalls[-1][0][0]
    assert baFirstArg == b"\x03"
    assert baSecondArg == b"\x1c"


def test_fnKillForeground_not_running_skips():
    mockConn = _fmockDockerConnection()
    session = TerminalSession(mockConn, "cid")
    session.fnKillForeground()


def test_fnKillForeground_exception_handled():
    mockConn = _fmockDockerConnection()
    session = TerminalSession(mockConn, "cid")
    session.fnStart()
    session._socketExec._sock.sendall.side_effect = (
        ConnectionError("broken")
    )
    session.fnKillForeground()


# -----------------------------------------------------------------------
# fnClose
# -----------------------------------------------------------------------


def test_fnClose_sets_not_running():
    mockConn = _fmockDockerConnection()
    session = TerminalSession(mockConn, "cid")
    session.fnStart()
    session.fnClose()
    assert session._bRunning is False


def test_fnClose_closes_socket():
    mockConn = _fmockDockerConnection()
    session = TerminalSession(mockConn, "cid")
    session.fnStart()
    session.fnClose()
    session._socketExec.close.assert_called_once()


def test_fnClose_handles_socket_error():
    mockConn = _fmockDockerConnection()
    session = TerminalSession(mockConn, "cid")
    session.fnStart()
    session._socketExec.close.side_effect = OSError
    session.fnClose()
    assert session._bRunning is False


def test_fnClose_no_socket():
    mockConn = _fmockDockerConnection()
    session = TerminalSession(mockConn, "cid")
    session.fnClose()
    assert session._bRunning is False
