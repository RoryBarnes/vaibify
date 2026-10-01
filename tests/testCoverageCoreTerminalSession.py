"""The terminal session's PTY surface and its fail-closed start.

The host leg runs a REAL shell through the real HostConnection launch,
with the journal, locks and scratch roots redirected into tmp_path, as
``testHostTerminal`` does. The Docker leg's exec socket is a recorder,
because the daemon is the external boundary.
"""

import fcntl
import os
import signal
import struct
import sys
import termios
import time

import pytest

from vaibify.config import containerLock, operationJournal
from vaibify.gui import terminalContainment
from vaibify.gui.terminalSession import HostTerminalSession, TerminalSession
from vaibify.host import hostCancellation, hostScratch
from vaibify.host.hostConnection import HostConnection


S_RESOURCE_NAME = "hostProjectAlpha"


@pytest.fixture(autouse=True)
def fixtureIsolateJournalAndScratch(tmp_path, monkeypatch):
    """Redirect the journal, locks, scratch roots and the shell's HOME.

    The host sessions start a real interactive bash, which would read the
    developer's rc files and append to their history; an empty HOME and
    a null history file keep it hermetic.
    """
    pathHome = tmp_path / "home"
    pathHome.mkdir()
    monkeypatch.setenv("HOME", str(pathHome))
    monkeypatch.setenv("HISTFILE", os.devnull)
    monkeypatch.delenv("BASH_ENV", raising=False)
    monkeypatch.setattr(
        operationJournal, "_S_JOURNAL_DIRECTORY", str(tmp_path / "journal"),
    )
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"),
    )
    monkeypatch.setattr(
        hostScratch, "_S_HOST_DIAGNOSTICS_ROOT",
        str(tmp_path / "host-diagnostics"),
    )


class AppStateStub:
    """The one object the terminal registry attaches its records to."""


@pytest.fixture()
def sessionHost(tmp_path):
    """A host terminal session over a real project root, not yet started."""
    sProjectRoot = str(tmp_path / "project")
    os.makedirs(sProjectRoot)
    connectionHost = HostConnection(
        fnResolveProjectRoot=lambda sResourceId: sProjectRoot,
    )
    sessionTerminal = HostTerminalSession(
        connectionHost, S_RESOURCE_NAME,
        {"appState": AppStateStub(), "iOwnerGeneration": 1},
    )
    yield sessionTerminal
    if sessionTerminal.recordContainment is not None:
        terminalContainment.fdictDrainSessionRecord(sessionTerminal)
    sessionTerminal.fnClose()


def fbaAwaitOutput(sessionTerminal, sNeedle, fTimeoutSeconds=20.0):
    """Collect output until sNeedle appears or the deadline passes."""
    baCollected = b""
    fDeadline = time.monotonic() + fTimeoutSeconds
    while time.monotonic() < fDeadline:
        baChunk = sessionTerminal.fbaReadOutput()
        if baChunk:
            baCollected += baChunk
            if sNeedle.encode() in baCollected:
                break
        else:
            time.sleep(0.05)
    return baCollected


def testFencedInputIsDroppedUntilTheFenceLifts(sessionHost):
    """Input sent while fenced never reaches the shell; lifted input does."""
    sessionHost.fnStart()
    sessionHost.fnFenceInput()
    sessionHost.fnSendInput(b"echo FENCED-$((3*3))\n")
    sessionHost.fnLiftInputFence()
    sessionHost.fnSendInput(b"echo LIFTED-$((4*4))\n")
    baOutput = fbaAwaitOutput(sessionHost, "LIFTED-16")
    assert b"LIFTED-16" in baOutput
    assert b"FENCED-9" not in baOutput


def fnAwaitForegroundLeavesShell(sessionTerminal, iShellProcessGroup, fTimeoutSeconds):
    """Wait until a process group other than the shell's owns the PTY."""
    fDeadline = time.monotonic() + fTimeoutSeconds
    while time.monotonic() < fDeadline:
        if os.tcgetpgrp(sessionTerminal._iMasterFd) != iShellProcessGroup:
            return
        time.sleep(0.02)
    raise AssertionError("the job never took the terminal's foreground")


def testKillForegroundInterruptsARunningCommand(sessionHost):
    """A long foreground job is interrupted so the shell answers again.

    The kill waits until the keystrokes can only reach the job. An
    interactive shell ignores SIGINT and SIGQUIT, so they are lost if
    they arrive while the shell still owns the terminal -- and bash's
    child can run (and print) before its parent hands the terminal to
    the job's process group. A timer raced both under load. So the test
    waits for the job's marker (it runs with default handlers) AND for
    the foreground group to leave the shell's. The marker is computed
    so the terminal's echo of the typed line cannot match it.
    """
    sessionHost.fnStart()
    sessionHost.fnSendInput(b"echo READY-$((5*5))\n")
    assert b"READY-25" in fbaAwaitOutput(sessionHost, "READY-25", 15.0)
    iShellProcessGroup = os.tcgetpgrp(sessionHost._iMasterFd)
    sessionHost.fnSendInput(b"sh -c 'echo JOB-$((6*7)); exec sleep 60'\n")
    assert b"JOB-42" in fbaAwaitOutput(sessionHost, "JOB-42", 15.0)
    fnAwaitForegroundLeavesShell(sessionHost, iShellProcessGroup, 15.0)
    sessionHost.fnKillForeground()
    sessionHost.fnSendInput(b"echo ALIVE-$((2*3))\n")
    assert b"ALIVE-6" in fbaAwaitOutput(sessionHost, "ALIVE-6", 15.0)


def testResizeSetsTheWindowSizeOfThePty(sessionHost):
    """The PTY reports the rows and columns the browser asked for."""
    sessionHost.fnStart()
    sessionHost.fnResize(33, 101)
    baSize = fcntl.ioctl(
        sessionHost._iMasterFd, termios.TIOCGWINSZ, b"\x00" * 8,
    )
    iRows, iColumns, _, _ = struct.unpack("HHHH", baSize)
    assert (iRows, iColumns) == (33, 101)


@pytest.mark.xfail(
    sys.platform == "darwin", strict=True, raises=AssertionError,
    reason=(
        "BUG (macOS): terminalSession.HostTerminalSession.fbaReadOutput "
        "expects EIO when the shell exits, but a Darwin PTY master "
        "answers readable-then-empty (b'') instead, so _bRunning never "
        "clears and pipelineServer.fnTerminalReadLoop polls forever."
    ),
)
def testShellExitEndsTheReadLoop(sessionHost):
    """Once the shell exits, reading clears running so the relay stops."""
    sessionHost.fnStart()
    sessionHost.fnSendInput(b"exit\n")
    fDeadline = time.monotonic() + 10.0
    while sessionHost._bRunning and time.monotonic() < fDeadline:
        sessionHost.fbaReadOutput()
        time.sleep(0.05)
    assert sessionHost._bRunning is False
    assert sessionHost.fbaReadOutput() == b""


def testClosedSessionIsInert(sessionHost):
    """After close the master is released and every call is a no-op."""
    sessionHost.fnStart()
    sessionHost.fnClose()
    assert sessionHost._iMasterFd is None
    sessionHost.fnResize(10, 10)
    sessionHost.fnKillForeground()
    sessionHost.fnSendInput(b"ignored\n")
    assert sessionHost.fbaReadOutput() == b""


def fnKillLeftoverShell(iPid):
    """Kill the quarantined shell this test deliberately left uncontained.

    A pid of 0 (the record's default) or below would make ``os.kill``
    signal a whole process group -- this pytest run included -- so only
    a positive pid is ever signalled.
    """
    if not isinstance(iPid, int) or iPid <= 0:
        return
    try:
        os.kill(iPid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def testShellThatNeverLeadsItsSessionIsRefused(sessionHost, monkeypatch):
    """A failed leadership proof drains, closes, and raises by name."""
    monkeypatch.setattr(
        hostCancellation, "fbAwaitSessionLeadership", lambda iPid: False,
    )
    with pytest.raises(
        terminalContainment.TerminalContainmentError,
        match="never proved session leadership",
    ):
        sessionHost.fnStart()
    recordTerminal = sessionHost.recordContainment
    try:
        assert recordTerminal is not None
        assert recordTerminal.iProcessGroup == 0
        assert recordTerminal.sState == "quarantined"
        assert sessionHost._iMasterFd is None
        assert sessionHost.fbaReadOutput() == b""
    finally:
        fnKillLeftoverShell(recordTerminal.iHolderPid)
        sessionHost.recordContainment = None


def testHostSessionIdsAreDistinctPerSession(tmp_path):
    """Two sessions over one resource never share a session id."""
    connectionHost = HostConnection(fnResolveProjectRoot=lambda s: str(tmp_path))
    dictContainment = {"appState": AppStateStub(), "iOwnerGeneration": 1}
    sessionFirst = HostTerminalSession(
        connectionHost, S_RESOURCE_NAME, dictContainment)
    sessionSecond = HostTerminalSession(
        connectionHost, S_RESOURCE_NAME, dictContainment)
    assert sessionFirst.sSessionId != sessionSecond.sSessionId


class SocketRefusingWrites:
    """A raw socket whose writes fail, as a half-closed exec socket does."""

    def sendall(self, baData):
        raise BrokenPipeError("peer closed")


class ExecSocketRecorder:
    """The exec socket wrapper: a raw ``_sock`` and a recorded close."""

    def __init__(self):
        self._sock = SocketRefusingWrites()
        self.bClosed = False

    def close(self):
        self.bClosed = True


class DockerConnectionRecorder:
    """Records exec creation and hands back the recorder socket."""

    def __init__(self):
        self.socketExec = ExecSocketRecorder()
        self.listCreated = []

    def fsExecCreate(self, sContainerId, sUser=None):
        self.listCreated.append((sContainerId, sUser))
        return "execIdentifierAlpha"

    def fsocketExecStart(self, sExecId):
        return self.socketExec


def testDockerCloseStillClosesSocketWhenShellWritesFail():
    """A broken write path must not keep the exec socket open."""
    connectionDocker = DockerConnectionRecorder()
    sessionDocker = TerminalSession(
        connectionDocker, "containerIdentifierAlpha", sUser="researcher",
    )
    sessionDocker.fnStart()
    assert connectionDocker.listCreated == [
        ("containerIdentifierAlpha", "researcher"),
    ]
    sessionDocker.fnKillForeground()
    sessionDocker.fnClose()
    assert connectionDocker.socketExec.bClosed is True
    assert sessionDocker.fbaReadOutput() == b""
