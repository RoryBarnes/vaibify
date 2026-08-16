"""WebSocket-to-PTY bridge for terminal sessions, both legs.

The Docker leg bridges to an exec instance's socket; the host leg
(2026-08-15) bridges to a real PTY on the researcher's own machine.
Both are constructed ONLY by the gated terminal route, and both run
their start as the journaled create → journal → start split — this
module is the single seam that prepares terminal execution records,
which is what the quiescence claim rests on.
"""

__all__ = [
    "fsGenerateSessionId",
    "TerminalSession",
    "HostTerminalSession",
]

import fcntl
import os
import select
import struct
import termios
import uuid

from . import terminalContainment


def fsGenerateSessionId():
    """Return a unique session identifier string."""
    return str(uuid.uuid4())


class TerminalSession:
    """Manages a single interactive exec session in a container.

    When ``dictContainment`` is provided (the production route always
    provides it), the start is the journaled create → journal → start
    split of design v13 §6.1: the exec id lands in the container's
    operation journal BEFORE ``exec_start``, the shell runs behind the
    group-reporting wrapper, and the discovered session/process group
    becomes a durable ``TerminalExecutionRecord`` that every
    authority-ending path terminates-and-proves. Without it (direct
    library use, unit tests) the legacy unjournaled start is kept — the
    same deliberate unwired-lane remainder the pipeline exec split has.
    """

    def __init__(
        self, connectionDocker, sContainerId, sUser=None,
        sShellCommand="/bin/bash", dictContainment=None,
    ):
        self._connectionDocker = connectionDocker
        self._sContainerId = sContainerId
        self._sUser = sUser
        self._sShellCommand = sShellCommand
        self._dictContainment = dictContainment
        self._sSessionId = fsGenerateSessionId()
        self._sExecId = None
        self._socketExec = None
        self._bRunning = False
        self._bInputFenced = False
        self.recordContainment = None

    @property
    def sSessionId(self):
        return self._sSessionId

    def fnStart(self):
        """Create and start the docker exec instance."""
        if self._dictContainment is None:
            self._sExecId = self._connectionDocker.fsExecCreate(
                self._sContainerId, sUser=self._sUser
            )
            self._socketExec = (
                self._connectionDocker.fsocketExecStart(self._sExecId)
            )
            self._bRunning = True
            return
        self._fnStartContained()

    def _fnStartContained(self):
        """Run the journaled create → journal → start → discover split."""
        sContainerName = self._dictContainment["sContainerName"]
        sOperationId = terminalContainment.fsPrepareTerminalOperation(
            sContainerName, self._sContainerId,
        )
        sMarkerPath = terminalContainment.fsMintGroupMarkerPath()
        sWrapperScript = terminalContainment.fsBuildGroupReportingCommand(
            self._sShellCommand, sMarkerPath,
        )
        self._sExecId = self._connectionDocker.fsExecCreate(
            self._sContainerId, sUser=self._sUser,
            listCommand=["/bin/sh", "-c", sWrapperScript],
        )
        terminalContainment.fnPromoteTerminalOperation(
            sContainerName, sOperationId, self._sExecId,
            self._sContainerId, self._dictContainment["iOwnerGeneration"],
        )
        self._socketExec = (
            self._connectionDocker.fsocketExecStart(self._sExecId)
        )
        self._bRunning = True
        self._fnRegisterAndDiscoverGroup(
            sContainerName, sOperationId, sMarkerPath,
        )

    def _fnRegisterAndDiscoverGroup(
        self, sContainerName, sOperationId, sMarkerPath,
    ):
        """Register the durable record, then bind its discovered group.

        Registration precedes discovery so a crash mid-discovery still
        leaves the record covered by the release, reaper, and shutdown
        drains. A failed discovery fails closed: the record is drained
        (settled only if the exec provably never reached the shell,
        quarantined otherwise), the socket is closed, and the terminal
        is refused with the containment error.
        """
        recordTerminal = terminalContainment.TerminalExecutionRecord(
            sOperationId=sOperationId,
            sContainerName=sContainerName,
            sContainerId=self._sContainerId,
            sDockerExecId=self._sExecId,
            iOwnerGeneration=self._dictContainment["iOwnerGeneration"],
            connectionDocker=self._connectionDocker,
            dictRegistry=None,
            session=self,
        )
        self.recordContainment = recordTerminal
        terminalContainment.fnRegisterTerminalRecord(
            self._dictContainment["appState"], recordTerminal,
        )
        try:
            iProcessGroup = (
                terminalContainment.fiDiscoverTerminalProcessGroup(
                    self._connectionDocker, self._sContainerId, sMarkerPath,
                )
            )
            terminalContainment.fnRecordTerminalProcessGroup(
                sContainerName, sOperationId, iProcessGroup,
            )
            recordTerminal.iProcessGroup = iProcessGroup
        except Exception:
            terminalContainment.fdictDrainSessionRecord(self)
            self.fnClose()
            raise

    def fnSendInput(self, baData):
        """Write bytes to the exec session stdin unless fenced."""
        if not self._bRunning or self._bInputFenced:
            return
        self._socketExec._sock.sendall(baData)

    def fnFenceInput(self):
        """Refuse further input — the reversible first step of a drain."""
        self._bInputFenced = True

    def fnLiftInputFence(self):
        """Reverse the fence (a drain that never happened, design §6.1)."""
        self._bInputFenced = False

    def fbaReadOutput(self):
        """Read available bytes from the exec session."""
        if not self._bRunning:
            return b""
        self._socketExec._sock.setblocking(False)
        try:
            return self._socketExec._sock.recv(4096)
        except BlockingIOError:
            return b""
        finally:
            self._socketExec._sock.setblocking(True)

    def fnResize(self, iRows, iColumns):
        """Resize the PTY to match browser terminal dimensions."""
        if self._sExecId:
            self._connectionDocker.fnExecResize(
                self._sExecId, iRows, iColumns
            )

    def fnKillForeground(self):
        """Kill the foreground process by sending SIGINT then SIGQUIT."""
        if not self._bRunning:
            return
        try:
            self._socketExec._sock.sendall(b"\x03")
            self._socketExec._sock.sendall(b"\x1c")
        except Exception:
            pass

    def fnClose(self):
        """Close the exec socket after asking the shell to exit.

        Closing the socket is NOT proof the terminal died — a
        signal-trapping shell survives all three keystrokes below.
        Every production close path therefore also drains the
        containment record (``terminalContainment.fdictDrainSessionRecord``),
        which terminates the recorded process group and PROVES it
        empty or quarantines.
        """
        self._bRunning = False
        if self._socketExec:
            try:
                self._socketExec._sock.sendall(b"\x03")
                self._socketExec._sock.sendall(b"\x04")
                self._socketExec._sock.sendall(b"exit\n")
            except Exception:
                pass
            try:
                self._socketExec.close()
            except Exception:
                pass


class HostTerminalSession:
    """An interactive shell on the researcher's own machine, contained.

    The host twin of :class:`TerminalSession`, with the same duck
    surface the relay and the drains speak. The start is the journaled
    prepare → suspended spawn → promote → release → verify split
    (ruling 12): the ``terminal``-kind record carries the shell's
    recycle-proof pid BEFORE its first instruction runs, the stub then
    ``setsid``s — so the journaled pid IS the session id the
    containment probes sweep — and a child that never proves session
    leadership is drained and refused, never relayed.

    ``connectionHost`` is the ROUTER: the launch, the drain's signal
    and the drain's probe all dispatch to the host leg by the resource
    name, which is also why ``sContainerId`` on the containment record
    is the name.
    """

    def __init__(self, connectionHost, sResourceName, dictContainment):
        self._connectionHost = connectionHost
        self._sResourceName = sResourceName
        self._dictContainment = dictContainment
        self._sSessionId = fsGenerateSessionId()
        self._processChild = None
        self._iMasterFd = None
        self._bRunning = False
        self._bInputFenced = False
        self.recordContainment = None

    @property
    def sSessionId(self):
        return self._sSessionId

    def fnStart(self):
        """Run the journaled prepare → spawn → promote → verify split."""
        from vaibify.host.hostCancellation import fbAwaitSessionLeadership
        sOperationId = terminalContainment.fsPrepareTerminalOperation(
            self._sResourceName, self._sResourceName,
        )
        dictLaunch = self._connectionHost.fdictLaunchTerminalShellSuspended(
            self._sResourceName,
        )
        self._processChild = dictLaunch["processChild"]
        self._iMasterFd = dictLaunch["iMasterFd"]
        iPid = self._processChild.pid
        terminalContainment.fnPromoteHostTerminalOperation(
            self._sResourceName, sOperationId, iPid,
            self._dictContainment["iOwnerGeneration"],
        )
        dictLaunch["fnReleaseGate"]()
        self._bRunning = True
        self._fnRegisterAndVerifyLeadership(sOperationId, iPid,
                                            fbAwaitSessionLeadership)

    def _fnRegisterAndVerifyLeadership(
        self, sOperationId, iPid, fbAwaitSessionLeadership,
    ):
        """Register the durable record, then verify the setsid landed.

        Registration precedes verification so a crash mid-verify still
        leaves the record covered by the release, reaper and shutdown
        drains — the container leg's ordering, kept exactly. A child
        that never leads its own session fails closed: drained,
        closed, refused.
        """
        recordTerminal = terminalContainment.TerminalExecutionRecord(
            sOperationId=sOperationId,
            sContainerName=self._sResourceName,
            sContainerId=self._sResourceName,
            sDockerExecId="",
            iOwnerGeneration=self._dictContainment["iOwnerGeneration"],
            connectionDocker=self._connectionHost,
            dictRegistry=None,
            session=self,
            iHolderPid=iPid,
        )
        self.recordContainment = recordTerminal
        terminalContainment.fnRegisterTerminalRecord(
            self._dictContainment["appState"], recordTerminal,
        )
        if not fbAwaitSessionLeadership(iPid):
            terminalContainment.fdictDrainSessionRecord(self)
            self.fnClose()
            raise terminalContainment.TerminalContainmentError(
                "The host terminal shell never proved session "
                "leadership; an uncontainable shell is refused"
            )
        terminalContainment.fnRecordTerminalProcessGroup(
            self._sResourceName, sOperationId, iPid,
        )
        recordTerminal.iProcessGroup = iPid

    def fnSendInput(self, baData):
        """Write bytes to the PTY master unless fenced."""
        if not self._bRunning or self._bInputFenced:
            return
        os.write(self._iMasterFd, baData)

    def fnFenceInput(self):
        """Refuse further input — the reversible first step of a drain."""
        self._bInputFenced = True

    def fnLiftInputFence(self):
        """Reverse the fence (a drain that never happened)."""
        self._bInputFenced = False

    def fbaReadOutput(self):
        """Read available bytes from the PTY master without blocking.

        ``OSError`` here is the PTY's EOF: when the shell exits, the
        master read raises ``EIO`` (macOS and Linux both), which is
        this session's honest "the shell is gone" signal — running is
        cleared so the relay's read loop ends instead of spinning.
        """
        if not self._bRunning:
            return b""
        listReadable, _, _ = select.select([self._iMasterFd], [], [], 0)
        if not listReadable:
            return b""
        try:
            return os.read(self._iMasterFd, 4096)
        except OSError:
            self._bRunning = False
            return b""

    def fnResize(self, iRows, iColumns):
        """Resize the PTY to match the browser terminal's dimensions."""
        if self._iMasterFd is None:
            return
        fcntl.ioctl(
            self._iMasterFd, termios.TIOCSWINSZ,
            struct.pack("HHHH", iRows, iColumns, 0, 0),
        )

    def fnKillForeground(self):
        """Interrupt the foreground job: SIGINT then SIGQUIT keystrokes."""
        if not self._bRunning:
            return
        try:
            os.write(self._iMasterFd, b"\x03")
            os.write(self._iMasterFd, b"\x1c")
        except OSError:
            pass

    def fnClose(self):
        """Close the PTY master after asking the shell to exit.

        Closing the master is NOT proof the terminal died — the same
        contract as the Docker leg — so every production close path
        also drains the containment record, which terminates the
        recorded session and PROVES it empty or quarantines.
        """
        self._bRunning = False
        if self._iMasterFd is not None:
            try:
                os.write(self._iMasterFd, b"\x03")
                os.write(self._iMasterFd, b"\x04")
                os.write(self._iMasterFd, b"exit\n")
            except OSError:
                pass
            try:
                os.close(self._iMasterFd)
            except OSError:
                pass
            self._iMasterFd = None
        if self._processChild is not None:
            self._processChild.poll()
