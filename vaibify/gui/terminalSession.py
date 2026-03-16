"""WebSocket-to-Docker PTY bridge for terminal sessions."""

import uuid


def fsGenerateSessionId():
    """Return a unique session identifier string."""
    return str(uuid.uuid4())


class TerminalSession:
    """Manages a single interactive exec session in a container."""

    def __init__(self, connectionDocker, sContainerId, sUser=None):
        self._connectionDocker = connectionDocker
        self._sContainerId = sContainerId
        self._sUser = sUser
        self._sSessionId = fsGenerateSessionId()
        self._sExecId = None
        self._socketExec = None
        self._bRunning = False

    @property
    def sSessionId(self):
        return self._sSessionId

    def fnStart(self):
        """Create and start the docker exec instance."""
        self._sExecId = self._connectionDocker.fsExecCreate(
            self._sContainerId, sUser=self._sUser
        )
        self._socketExec = (
            self._connectionDocker.fsocketExecStart(self._sExecId)
        )
        self._bRunning = True

    def fnSendInput(self, baData):
        """Write bytes to the exec session stdin."""
        if not self._bRunning:
            return
        self._socketExec._sock.sendall(baData)

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

    def fnClose(self):
        """Clean up the exec session."""
        self._bRunning = False
        if self._socketExec:
            try:
                self._socketExec.close()
            except Exception:
                pass
