"""A dropped terminal socket, cut for real, and the pane that comes back.

This closes the one gap the redial shipped with. The pane's recovery
was pinned by source assertions -- that a timer exists, that a refusal
is not retried -- and by nothing that had ever dropped a socket. The
browser lane drove the DIAL and a real PTY echo, so the code was
exercised right up to the moment it mattered and no further.

So this cuts the TRANSPORT under a live shell. The terminal socket is
routed through the test, which passes it straight to the real hub --
the server, the PTY and the shell are all genuinely there -- and then
severs it. No export was added to make this reachable, and the pane
under test learns of the drop exactly as it would from a dead tunnel:
an onclose that is neither a normal close nor a deliberate refusal.

(A real network drop delivers 1006, which no endpoint may send
explicitly. 1011 takes the identical branch -- not 1000/1001, not a
4xxx -- and is the closest a test can get to a severed link. Browser
offline emulation was tried first and does not tear down an
established loopback socket, which is why the transport is routed
instead.)

What comes back is a NEW shell, deliberately, and the pane says so.
The old one is genuinely gone: closing the socket terminates the
recorded session and proves it dead, which is what lets vaibify say
anything honest about a project being quiet. A pane that silently
reconnected to a "resumed" session would be claiming the one thing
this design refuses to claim.
"""

import time

import pytest

from tests.browser.conftest import (
    S_HOST_PROJECT_READY,
    S_HOST_STEP_NAME,
    S_HOST_WORKFLOW_NAME,
)


pytestmark = pytest.mark.browser

F_RECOVERY_DEADLINE_SECONDS = 45.0


def _fsPaneText(pageDashboard):
    """Return the terminal pane's rendered text, whitespace collapsed."""
    pageDashboard.wait_for_selector(".xterm-rows", timeout=20000)
    return " ".join(pageDashboard.text_content(".xterm-rows").split())


def _fnWaitForPaneText(pageDashboard, sExpected, sWhat):
    """Block until sExpected is visible in the pane, or fail saying why."""
    fStarted = time.monotonic()
    sPane = _fsPaneText(pageDashboard)
    while sExpected not in sPane:
        assert time.monotonic() - fStarted < F_RECOVERY_DEADLINE_SECONDS, (
            f"{sWhat} never appeared in the terminal pane. "
            f"Pane read: {sPane[-400:]}"
        )
        pageDashboard.wait_for_timeout(250)
        sPane = _fsPaneText(pageDashboard)
    return sPane


def _fsRunUntilItEchoes(pageDashboard, sCommand, sMarker, sWhat):
    """Type sCommand until sMarker comes back, and return the pane.

    Retried deliberately rather than waited-for. There is no reliable
    "the replacement shell is ready" signal to watch: the pane renders
    only its visible viewport, so the previous shell's banner scrolls
    away rather than accumulating, and keystrokes sent a moment early
    are simply lost. Retrying an idempotent echo is what a researcher
    does, and it asserts the thing that actually matters -- the new
    shell accepts input and runs it -- instead of a proxy for it.
    """
    fStarted = time.monotonic()
    while True:
        pageDashboard.click(".xterm")
        pageDashboard.keyboard.type(sCommand)
        pageDashboard.keyboard.press("Enter")
        pageDashboard.wait_for_timeout(1500)
        sPane = _fsPaneText(pageDashboard)
        if sMarker in sPane:
            return sPane
        assert time.monotonic() - fStarted < F_RECOVERY_DEADLINE_SECONDS, (
            f"{sWhat} never ran in the terminal pane. "
            f"Pane read: {sPane[-400:]}"
        )


def _fnRouteTheTerminalSocket(pageDashboard, listRoutes):
    """Pass the terminal socket through to the real hub, but hold it.

    ``connect_to_server`` is what keeps this honest: every byte still
    reaches the real server and a real PTY. The test holds the handle
    only so it can sever the link later, which is the one thing a page
    cannot be asked to do to itself.
    """
    def _fnHandle(routeSocket):
        routeSocket.connect_to_server()
        listRoutes.append(routeSocket)

    pageDashboard.route_web_socket("**/ws/terminal/**", _fnHandle)


def _fnSeverTheLiveSocket(pageDashboard, listRoutes):
    """Cut the live terminal socket, then get out of the way.

    The routing exists only to reach the sever. Removing it before the
    pane redials means the REPLACEMENT socket is an ordinary direct
    connection to the hub -- so what the recovery is judged on is the
    product's own path, with no test machinery in it at all.
    """
    assert listRoutes, "the terminal socket was never routed"
    listRoutes[-1].close(code=1011)
    pageDashboard.unroute_all()


def _fnOpenAHostShell(pageDashboard, serverHub):
    """Enter the host project and dial a real shell in its pane."""
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_selector(
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"]',
        timeout=15000,
    )
    pageDashboard.click(
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"] '
        '.container-tile-main',
    )
    pageDashboard.wait_for_selector("#modalConfirm", timeout=10000)
    pageDashboard.click("#btnConfirmOk")
    # The pane lives inside a workflow, so entering one is part of
    # reaching a shell -- the same route the journey test takes.
    pageDashboard.wait_for_selector(
        f"text={S_HOST_WORKFLOW_NAME}", timeout=20000,
    )
    pageDashboard.click(f"text={S_HOST_WORKFLOW_NAME}")
    pageDashboard.wait_for_selector(
        f"text={S_HOST_STEP_NAME}", timeout=20000,
    )
    # The shell dials on the researcher's first gesture, never on entry.
    pageDashboard.wait_for_selector(".xterm", timeout=20000)
    pageDashboard.click(".xterm")
    _fnWaitForPaneText(
        pageDashboard, "YOUR OWN machine", "the host shell banner",
    )


def test_a_cut_connection_brings_back_a_new_and_empty_shell(
    pageDashboard, serverHub,
):
    """One cut, both properties, because both are about that cut.

    They were two tests until the second could not run: the first
    still held the project, and the tile answered "In use in another
    browser session." That is the one-session-per-container model
    working exactly as designed, so the tests merged rather than the
    model being worked around.

    What is proven here: the pane recovers at all (it used to write
    "[Connection closed]" and stop forever), the replacement is a
    USABLE shell rather than a prompt-shaped picture, and it is a NEW
    one -- the previous shell's state is gone, which is what makes
    "terminated and proven dead" a true statement rather than a hope.
    """
    listRoutes = []
    _fnRouteTheTerminalSocket(pageDashboard, listRoutes)
    _fnOpenAHostShell(pageDashboard, serverHub)

    pageDashboard.click(".xterm")
    pageDashboard.keyboard.type("MARKER=beforeTheCut")
    pageDashboard.keyboard.press("Enter")
    _fsRunUntilItEchoes(
        pageDashboard, "echo SET-$MARKER", "SET-beforeTheCut",
        "the marker in the first shell",
    )

    # The cut, under a live PTY.
    _fnSeverTheLiveSocket(pageDashboard, listRoutes)
    sPane = _fnWaitForPaneText(
        pageDashboard, "Reconnecting", "the reconnect notice",
    )
    assert "NEW shell" in sPane, (
        "the pane must say the shell is new. Claiming the session "
        f"resumed would be the one dishonest option: {sPane[-400:]}"
    )

    sPane = _fsRunUntilItEchoes(
        pageDashboard, "echo AFTER-[$MARKER]", "AFTER-[",
        "the marker probe in the replacement shell",
    )
    assert "AFTER-[beforeTheCut]" not in sPane, (
        "the replacement shell inherited the previous shell's state, "
        "so the old process is still alive and the terminated-and-"
        f"proven claim is false: {sPane[-400:]}"
    )
