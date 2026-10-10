"""SIGHUP and a second Ctrl-C both run the hub's full shutdown.

uvicorn handles only SIGINT and SIGTERM, so closing the terminal window
of a foreground hub killed it with every lifespan shutdown hook
skipped, and the terminals it owned ran on; a second Ctrl-C became
uvicorn's ``force_exit``, which skips the same hooks. The contract
tests here never INVOKE the detach: ``dup2`` is process-wide and would
blind pytest's output capture for the rest of the session. The one
test that exercises it runs a hub in a subprocess under a
pseudo-terminal and closes the terminal.
"""

import http.client
import os
import pty
import signal
import socket
import sys
import textwrap
import time

import pytest

from vaibify.cli import serverLaunch

# A SIGHUP whose default action reached pytest would end the suite.
# Every test here installs a harmless handler as the "previous" one
# BEFORE the server's capture runs, so a restore or a re-raise can only
# ever land on it.
_fnHarmless = lambda iSignal, frame: None  # noqa: E731


@pytest.fixture
def serverUvicorn(monkeypatch):
    fnPrevious = signal.signal(signal.SIGHUP, _fnHarmless)
    try:
        yield serverLaunch.ServerLoggingExitSignals(
            serverLaunch.uvicorn.Config(object(), port=8050))
    finally:
        signal.signal(signal.SIGHUP, fnPrevious)


def test_a_hangup_handler_is_installed_during_capture_and_restored_after(
    serverUvicorn,
):
    with serverUvicorn.capture_signals():
        fnDuringCapture = signal.getsignal(signal.SIGHUP)
        assert fnDuringCapture is not _fnHarmless, (
            "no hang-up handler was installed while uvicorn captures")
        assert callable(fnDuringCapture)
    assert signal.getsignal(signal.SIGHUP) is _fnHarmless, (
        "the previous SIGHUP handler was not restored after capture")


def test_a_second_sigint_leaves_force_exit_false(serverUvicorn, caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="vaibify"):
        serverUvicorn.handle_exit(signal.SIGINT, None)
        serverUvicorn.handle_exit(signal.SIGINT, None)
    assert serverUvicorn.should_exit is True
    assert serverUvicorn.force_exit is False, (
        "a second Ctrl-C must not skip the lifespan shutdown")
    assert any("second SIGINT" in recordLog.getMessage()
               for recordLog in caplog.records)


def test_the_hangup_handler_detaches_then_forwards_and_tolerates_a_repeat(
    serverUvicorn, monkeypatch,
):
    """uvicorn re-raises every captured signal when its capture ends,
    while the hang-up handler is still installed, so the handler runs
    again; the recorder sees that third call without any raise."""
    listDetached = []
    monkeypatch.setattr(
        serverLaunch, "fnDetachFromClosedTerminal",
        lambda: listDetached.append("detached"))
    with serverUvicorn.capture_signals():
        fnHangup = signal.getsignal(signal.SIGHUP)
        fnHangup(signal.SIGHUP, None)
        fnHangup(signal.SIGHUP, None)
        assert serverUvicorn.should_exit is True
        assert serverUvicorn.force_exit is False
    assert len(listDetached) >= 2
    assert signal.getsignal(signal.SIGHUP) is _fnHarmless


S_HUB_UNDER_PTY_SCRIPT = textwrap.dedent('''
    import os
    import sys
    from fastapi import FastAPI
    from vaibify.cli import serverLaunch
    from vaibify.gui import serverLifespan

    sMarkerDirectory = sys.argv[2]
    app = FastAPI(lifespan=serverLifespan._fcontextLifespanShared)
    app.state.listLifespanStartup = []

    async def fnWriteShutdownMarker(app):
        with open(os.path.join(sMarkerDirectory, "shutdown"), "w") as f:
            f.write("lifespan shutdown ran")

    app.state.listLifespanShutdown = [fnWriteShutdownMarker]

    @app.get("/")
    async def fdictAnswer():
        return {"sState": "up"}

    try:
        serverLaunch.fnRunServer(
            app, int(sys.argv[1]), bServeContainerAgents=False)
    except BaseException as error:
        with open(os.path.join(sMarkerDirectory, "error"), "w") as f:
            f.write(repr(error))
        raise
    with open(os.path.join(sMarkerDirectory, "exited"), "w") as f:
        f.write("clean")
''')


def _fiFreePort():
    with socket.socket() as socketProbe:
        socketProbe.bind(("127.0.0.1", 0))
        return socketProbe.getsockname()[1]


def _fbHubAnswers(iPort):
    connectionHttp = http.client.HTTPConnection("127.0.0.1", iPort, timeout=1)
    try:
        connectionHttp.request("GET", "/")
        return connectionHttp.getresponse().status == 200
    except OSError:
        return False
    finally:
        connectionHttp.close()


def _fiAwaitExit(iPid, fTimeoutSeconds):
    fDeadline = time.monotonic() + fTimeoutSeconds
    while time.monotonic() < fDeadline:
        iWaited, iStatus = os.waitpid(iPid, os.WNOHANG)
        if iWaited == iPid:
            return iStatus
        time.sleep(0.1)
    os.kill(iPid, signal.SIGKILL)
    os.waitpid(iPid, 0)
    pytest.fail("the hub did not exit within the window after its terminal closed")


@pytest.mark.exclusive
def test_closing_the_hubs_terminal_runs_the_lifespan_shutdown(tmp_path):
    """A real hub under a pseudo-terminal, whose master is closed.

    The child is the session leader with the pty as its controlling
    terminal, so closing the master delivers SIGHUP exactly as a
    closed terminal window does.
    """
    iPort = _fiFreePort()
    iPid, iMasterFd = pty.fork()
    if iPid == 0:  # pragma: no cover - the child execs the hub
        os.execv(sys.executable, [
            sys.executable, "-c", S_HUB_UNDER_PTY_SCRIPT,
            str(iPort), str(tmp_path)])
    try:
        fDeadline = time.monotonic() + 20.0
        while time.monotonic() < fDeadline and not _fbHubAnswers(iPort):
            time.sleep(0.1)
        assert _fbHubAnswers(iPort), "the hub never came up under the pty"
    except BaseException:
        os.kill(iPid, signal.SIGKILL)
        os.waitpid(iPid, 0)
        raise
    os.close(iMasterFd)
    iStatus = _fiAwaitExit(iPid, 20.0)
    assert os.WIFEXITED(iStatus) and os.WEXITSTATUS(iStatus) == 0, (
        f"the hub did not exit cleanly after the hang-up: status {iStatus}")
    assert (tmp_path / "shutdown").exists(), (
        "the lifespan shutdown hooks did not run after the hang-up")
    assert (tmp_path / "exited").exists()
    assert not (tmp_path / "error").exists(), (tmp_path / "error").read_text()
