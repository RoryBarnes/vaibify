"""A dropped terminal socket must not leave the pane dead forever.

The pane wrote "[Connection closed]" and stopped. There was no retry,
no timer, and no re-arm, so a brief network interruption cost the
researcher the tab until they noticed and recreated it. Through an SSH
tunnel that is not an edge case.

What comes back is a NEW shell, and the pane says so. The old one is
genuinely gone: closing the socket terminates the recorded session and
proves it dead, which is what lets vaibify say anything honest about a
project being quiet. Claiming the session resumed would be worse than
dying silently.

HONEST SCOPE. These are source assertions. They prove the redial
exists, is bounded by the session's window, and refuses to retry a
deliberate refusal. They do NOT prove the pane comes back on a real
drop -- that needs a browser cutting a live socket, and the browser
lane exercises the dial and the banner but never a drop. Treat the
recovery itself as unverified until a lane drives it.
"""

import pytest

from tests.testNetworkEfficiencyFrontendContract import _fsReadStaticFile


@pytest.fixture(scope="module")
def sTerminalSource():
    return _fsReadStaticFile("scriptTerminal.js")


def test_an_unexpected_close_schedules_a_redial(sTerminalSource):
    """The close handler must reach the scheduler at all."""
    assert "_fnScheduleShellRedial" in sTerminalSource
    assert "ws.onclose = function (event) {" in sTerminalSource
    iClose = sTerminalSource.index("ws.onclose = function (event) {")
    sHandler = sTerminalSource[iClose:iClose + 600]
    assert "_fnScheduleShellRedial" in sHandler, (
        "the close handler must schedule a redial; without it the "
        "pane is dead until the tab is recreated"
    )
    assert "dictTab.websocket = null" in sHandler, (
        "the dial guard refuses while a socket is recorded, so a "
        "close that does not clear it can never redial"
    )


def test_the_redial_says_the_shell_is_new(sTerminalSource):
    """Honesty: a resumed session would be a lie about containment."""
    assert "NEW shell" in sTerminalSource, (
        "the pane must say the shell is new -- the previous one was "
        "terminated and proven dead when the socket closed"
    )


def test_a_deliberate_refusal_is_not_retried(sTerminalSource):
    """4xxx is the server's final answer; retrying is noise."""
    iScheduler = sTerminalSource.index(
        "function _fnScheduleShellRedial",
    )
    sScheduler = sTerminalSource[iScheduler:iScheduler + 1400]
    assert "iCode >= 4000 && iCode < 5000" in sScheduler, (
        "a deliberate refusal must not be retried"
    )
    assert "iCode === 1000 || iCode === 1001" in sScheduler, (
        "a normal close is the researcher leaving, not a fault"
    )


def test_the_redial_is_bounded_by_the_sessions_window(sTerminalSource):
    """The terminal must not outlive the credential it presents.

    A terminal retrying past the session window presents a revoked
    credential and is refused -- the same mismatch, one socket over.
    """
    assert "ffGetReconnectWindowSeconds" in sTerminalSource, (
        "the redial budget must come from the session's window, not "
        "from a constant beside it"
    )
    assert "_F_REDIAL_MARGIN_SECONDS" in sTerminalSource
    sSocketSource = _fsReadStaticFile("scriptWebSocket.js")
    assert "ffGetReconnectWindowSeconds: ffGetReconnectWindowSeconds" in (
        sSocketSource
    ), "the window getter must actually be exported"


def test_disposing_a_tab_cancels_a_pending_redial(sTerminalSource):
    """A closed tab that redials would resurrect a dead pane."""
    iDispose = sTerminalSource.index("function fnDisposeTab")
    sDispose = sTerminalSource[iDispose:iDispose + 400]
    assert "_fnCancelShellRedial" in sDispose, (
        "a disposed tab with a pending timer dials a shell into a "
        "pane that no longer exists"
    )
