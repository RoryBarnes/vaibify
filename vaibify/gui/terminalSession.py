"""WebSocket-to-Docker PTY bridge for terminal sessions."""

__all__ = [
    "fsGenerateSessionId",
    "TerminalSession",
]

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
