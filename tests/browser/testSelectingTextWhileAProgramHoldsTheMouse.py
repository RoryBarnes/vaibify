"""Text a program is sitting on top of is still the researcher's.

A full-screen program -- every agent, vim, htop -- turns on mouse
reporting, and xterm then hands it the drag. Measured in this pane
before the mode existed, with reporting on:

  - a plain drag past the bottom edge selected NOTHING (the painted
    selection area stayed at zero), and
  - it threw the pane from row 3024 to row 4704, discarding the
    scroll-back position the researcher had dragged from.

xterm's only override is a modifier that differs by platform and
appears nowhere in the interface. The pane's "Select text" mode is
the visible form of it.

The second property here is the one a modifier alone would not have
fixed. xterm ramps its drag autoscroll from 1 to 15 lines per 50ms
over 50 pixels of overshoot, and this pane leaves roughly 30 pixels
between its last row and the bottom of the window -- so crossing the
edge at all measured the length of the scrollback in under a second
(3040 -> 4720, the buffer's end, in 0.3s). There is no speed in that
at which a researcher can stop on the line they want, which is the
whole purpose of dragging past the edge.

The pane here is a host project's REAL PTY on this machine, and
mouse reporting is turned on by the shell itself rather than
modelled, because what is being asserted is precisely what xterm
does when a program owns the mouse.

Kills (confirmed -- each mutation was applied to scriptTerminal.js,
this test run, and the named assertion observed to fail):
  - ``fbSelectTextModeApplies`` returning false always, so the
    modifier is never injected -> "the drag selected nothing".
  - ``F_AUTOSCROLL_OVERSHOOT_COMPRESSION`` set to 1, restoring
    xterm's own ramp -> "the autoscroll ran away".
"""

import time

import pytest

from tests.browser.conftest import (
    S_HOST_PROJECT_READY,
    S_HOST_STEP_NAME,
    S_HOST_WORKFLOW_NAME,
)


pytestmark = [pytest.mark.browser]

F_SHELL_DEADLINE_SECONDS = 45.0

# Enough lines that the pane has somewhere to scroll to, and a marker
# the wait can key on rather than sleeping.
S_FILL_COMMAND = "for i in $(seq 1 400); do echo selectline $i; done"
S_FILL_MARKER = "selectline 400"

# The shell turns on mouse reporting itself (X10 + button-event +
# SGR), which is what an agent's TUI does on startup.
S_MOUSE_REPORTING_ON = "printf '\\033[?1000h\\033[?1002h\\033[?1006h'"

F_DRAG_SECONDS = 1.0

# The rate is asserted just past the edge, because that is the zone
# the claim is about: a researcher reaching a few lines further down
# wants to STOP on one of them. Thirty pixels past, the compressed
# ramp yields 2 lines per 50ms (~640 pixels a second) and xterm's own
# yields 9 (~2880). The ceiling sits between them, so restoring
# xterm's ramp fails this assertion; the floor is what stops it
# passing against a pane that never scrolled at all. Both bounds are
# needed -- a one-sided assertion here is satisfied by doing nothing.
F_OVERSHOOT_PIXELS = 30.0
F_MINIMUM_SCROLL_PIXELS = 20.0
F_MAXIMUM_SCROLL_PIXELS = 1200.0


def _fnWaitForPaneText(pageDashboard, sExpected, sWhat):
    fStarted = time.monotonic()
    while sExpected not in pageDashboard.text_content(".xterm-rows"):
        assert time.monotonic() - fStarted < F_SHELL_DEADLINE_SECONDS, (
            f"{sWhat} never appeared in the terminal pane."
        )
        pageDashboard.wait_for_timeout(250)


def _fnOpenAHostShell(pageDashboard, serverHub):
    """Enter the host project and dial a real shell in its pane."""
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_selector(
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"]', timeout=15000,
    )
    pageDashboard.click(
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"] '
        '.container-tile-main',
    )
    pageDashboard.wait_for_selector("#modalConfirm", timeout=10000)
    pageDashboard.click("#btnConfirmOk")
    pageDashboard.wait_for_selector(f"text={S_HOST_WORKFLOW_NAME}",
                                    timeout=20000)
    pageDashboard.click(f"text={S_HOST_WORKFLOW_NAME}")
    pageDashboard.wait_for_selector(f"text={S_HOST_STEP_NAME}", timeout=20000)
    pageDashboard.wait_for_selector(".xterm", timeout=20000)
    pageDashboard.click(".xterm")
    _fnWaitForPaneText(pageDashboard, "YOUR OWN machine",
                       "the host shell banner")


def _fnRunInTheShell(pageDashboard, sCommand):
    pageDashboard.click(".xterm")
    pageDashboard.keyboard.type(sCommand)
    pageDashboard.keyboard.press("Enter")


def _fdictPaneGeometry(pageDashboard):
    """Where the pane's last rendered row sits, in page coordinates."""
    return pageDashboard.evaluate(
        """() => {
            const el = document.querySelector('.xterm-screen');
            const dictRect = el.getBoundingClientRect();
            return {fTop: dictRect.top, fBottom: dictRect.bottom,
                    fLeft: dictRect.left, fRight: dictRect.right,
                    fWindowHeight: window.innerHeight};
        }""",
    )


def _ffSelectionArea(pageDashboard):
    """The painted selection's area, which is zero when none is held.

    xterm keeps its selection divs in the DOM and collapses them to
    zero size rather than removing them, so counting divs would
    report a selection that does not exist.
    """
    return pageDashboard.evaluate(
        """() => Array.from(
            document.querySelectorAll('.xterm-selection div')
        ).reduce((fArea, el) => {
            const dictRect = el.getBoundingClientRect();
            return fArea + dictRect.width * dictRect.height;
        }, 0)""",
    )


def _ffScrollPosition(pageDashboard):
    return pageDashboard.evaluate(
        "() => document.querySelector('.xterm-viewport').scrollTop",
    )


def _fnScrollBackFromTheNewestLine(pageDashboard, dictGeometry):
    """Park the pane well above its newest line.

    Shift is held because the program owns the plain wheel too; this
    is the existing unconditional override, and using it here keeps
    the parking independent of the mode under test.
    """
    pageDashboard.mouse.move(
        (dictGeometry["fLeft"] + dictGeometry["fRight"]) / 2,
        (dictGeometry["fTop"] + dictGeometry["fBottom"]) / 2,
    )
    pageDashboard.keyboard.down("Shift")
    for _ in range(15):
        pageDashboard.mouse.wheel(0, -120)
    pageDashboard.keyboard.up("Shift")
    pageDashboard.wait_for_timeout(400)


def _ffDragPastTheBottomEdge(pageDashboard, dictGeometry, fTargetY):
    """Drag from mid-pane down to fTargetY; return pixels scrolled.

    The button is left DOWN: the selection and the distance travelled
    are both read while the drag is still live, because releasing is
    what a program that reads the release can act on.
    """
    fStartY = (dictGeometry["fTop"] + dictGeometry["fBottom"]) / 2
    fMidX = (dictGeometry["fLeft"] + dictGeometry["fRight"]) / 2
    fBefore = _ffScrollPosition(pageDashboard)
    pageDashboard.mouse.move(dictGeometry["fLeft"] + 60, fStartY)
    pageDashboard.mouse.down()
    pageDashboard.mouse.move(fMidX, fStartY + 20, steps=4)
    pageDashboard.mouse.move(fMidX, fTargetY, steps=4)
    time.sleep(F_DRAG_SECONDS)
    return _ffScrollPosition(pageDashboard) - fBefore


def testSelectTextModeGivesTheMouseBackToTheResearcher(
    pageDashboard, serverHub,
):
    _fnOpenAHostShell(pageDashboard, serverHub)
    _fnRunInTheShell(pageDashboard, S_FILL_COMMAND)
    _fnWaitForPaneText(pageDashboard, S_FILL_MARKER, "the filled scrollback")
    _fnRunInTheShell(pageDashboard, S_MOUSE_REPORTING_ON)
    pageDashboard.wait_for_timeout(600)

    dictGeometry = _fdictPaneGeometry(pageDashboard)

    # The precondition, asserted rather than assumed: with the mode
    # off, this is the gesture that fails. Without this the whole
    # test could pass against a pane nothing was holding.
    _fnScrollBackFromTheNewestLine(pageDashboard, dictGeometry)
    _ffDragPastTheBottomEdge(
        pageDashboard, dictGeometry,
        dictGeometry["fBottom"] + F_OVERSHOOT_PIXELS)
    pageDashboard.mouse.up()
    assert _ffSelectionArea(pageDashboard) == 0, (
        "a program was expected to be holding the mouse, but a plain "
        "drag selected text anyway -- the precondition for this test "
        "did not hold, so what follows would prove nothing"
    )

    pageDashboard.click(".terminal-pane-select")
    assert pageDashboard.get_attribute(
        ".terminal-pane-select", "aria-pressed") == "true", (
        "the mode is on but the button does not say so"
    )

    _fnScrollBackFromTheNewestLine(pageDashboard, dictGeometry)
    fMoved = _ffDragPastTheBottomEdge(
        pageDashboard, dictGeometry,
        dictGeometry["fBottom"] + F_OVERSHOOT_PIXELS)
    fArea = _ffSelectionArea(pageDashboard)
    pageDashboard.mouse.up()

    assert fArea > 0, (
        "the drag selected nothing: the mode is on, but the mouse is "
        "still reaching the program instead of the pane"
    )
    assert fMoved >= F_MINIMUM_SCROLL_PIXELS, (
        f"the drag past the bottom edge scrolled {fMoved} pixels, so "
        "text below the pane stayed out of reach"
    )
    assert fMoved <= F_MAXIMUM_SCROLL_PIXELS, (
        f"the autoscroll ran away: {fMoved} pixels in "
        f"{F_DRAG_SECONDS}s, which is a speed nobody can stop on a "
        "chosen line at"
    )

    # Reaching text off the bottom means the pointer leaves the
    # window, not merely the pane -- this pane's last row sits about
    # thirty pixels from the bottom of the browser. A researcher who
    # keeps dragging must not fall off the end of the mechanism.
    _fnScrollBackFromTheNewestLine(pageDashboard, dictGeometry)
    fMovedOutside = _ffDragPastTheBottomEdge(
        pageDashboard, dictGeometry, dictGeometry["fWindowHeight"] + 80)
    pageDashboard.mouse.up()
    assert fMovedOutside > 0, (
        "the pane stopped scrolling once the pointer left the browser "
        "window, which is where a drag for off-screen text ends up"
    )

    # The mode must not cost the pane its keyboard: the press whose
    # default action it suppresses is the one that focuses the pane.
    # The line is discarded first: the mode-OFF drag above forwarded
    # mouse reports to the shell, which is exactly the behaviour this
    # mode exists to stop, and they are sitting on bash's input line.
    pageDashboard.click(".xterm")
    pageDashboard.keyboard.press("Control+c")
    pageDashboard.wait_for_timeout(300)
    _fnRunInTheShell(pageDashboard, "echo keyboardstillworks")
    _fnWaitForPaneText(pageDashboard, "keyboardstillworks",
                       "a command typed after selecting")
