"""Holding the mouse outside the window must keep text coming.

Reported from a real Firefox: press Option, drag below the bottom of
the window, and the pane does not scroll, so text below the last row
cannot be selected at all.

The mechanism is not browser-specific even though the report is.
xterm recomputes its autoscroll speed ONLY when a mousemove arrives
and keeps applying the last speed it computed, so the speed stays
zero -- permanently, however long the mouse is held out there --
unless some mousemove landed below the pane's last row. Two things
conspire against that. Browsers disagree about whether a pointer
outside the window reports at all, and the strip between the pane's
last row and the window edge is about thirty pixels: narrower than
one frame of a brisk drag at 60Hz, so it can be crossed with no
sample inside it.

Then it gets worse, and the lane's own trace is where that was
learned. With the pointer dragged 80 pixels below a window 720 tall
and a pane ending at 690, the CI Firefox lane reported:

    mouseout@682 mouseleave@682 mousemove@682
    mouseout@-86 mouseleave@-86 x11 mousemove@-86 mousemove@-86

Firefox fires the boundary event correctly at 682 -- so the recovery
above does fire there -- and then reports the HELD pointer at
clientY -86, above the top of the page, a 768-pixel jump between two
frames for a pointer nobody moved. Chromium reports the same gesture
as 800. Believing Firefox ran the pane to the top of its buffer:
3791 pixels the wrong way, which a researcher sees as the selection
leaping away from the text they were reaching for.

WHAT THIS TEST DRIVES, SAID PLAINLY. Playwright's Chromium is the
engine; no automation framework will move a pointer outside the
viewport, and this machine could not launch a Firefox. Both
pathologies are therefore delivered as events rather than produced
by a pointer -- but neither is invented: the boundary event is the
one every engine sends on leaving the window, and -86 is copied from
the Firefox lane's own trace. What is proven here is that vaibify
recovers from both states. That Firefox produces them is measured,
in CI, by the assertion message this file prints on failure.

Kills (confirmed -- mutation applied to scriptTerminal.js, this test
run, the named assertion observed to fail):
  - the ``mouseout`` listener removed from
    ``fnBindSelectionInterception`` -> "the pane never scrolled".
  - ``ffResolveDragCoordinate`` believing a contradicted coordinate
    -> "a coordinate claiming the pointer had jumped above the page
    reversed the drag".
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
S_FILL_COMMAND = "for i in $(seq 1 400); do echo exitline $i; done"
S_FILL_MARKER = "exitline 400"

# Held long enough that a pane scrolling at the parked rate (about
# forty lines a second) moves unmistakably, and a pane that is not
# scrolling cannot be mistaken for a slow one.
F_HOLD_SECONDS = 1.0
F_MINIMUM_SCROLL_PIXELS = 100.0


def _fnWaitForPaneText(pageDashboard, sExpected, sWhat):
    fStarted = time.monotonic()
    while sExpected not in pageDashboard.text_content(".xterm-rows"):
        assert time.monotonic() - fStarted < F_SHELL_DEADLINE_SECONDS, (
            f"{sWhat} never appeared in the terminal pane."
        )
        pageDashboard.wait_for_timeout(250)


def _fnOpenAHostShell(pageDashboard, serverHub):
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


def _fdictPaneGeometry(pageDashboard):
    return pageDashboard.evaluate(
        """() => {
            const dictRect = document.querySelector('.xterm-screen')
                .getBoundingClientRect();
            return {fTop: dictRect.top, fBottom: dictRect.bottom,
                    fLeft: dictRect.left, fRight: dictRect.right,
                    fWindowHeight: window.innerHeight};
        }""",
    )


def _ffScrollPosition(pageDashboard):
    return pageDashboard.evaluate(
        "() => document.querySelector('.xterm-viewport').scrollTop",
    )


# What Firefox reports for a pointer dragged 80 pixels BELOW a
# window 720 tall, measured in CI: a coordinate above the top of the
# page. Chromium reports the same gesture as 800. The number is
# copied from the lane's own trace rather than invented, because a
# made-up impossible coordinate would not prove anything about a real
# engine's behaviour.
F_FIREFOX_REPORTS_A_DOWNWARD_DRAG_AT = -86.0


def _fnDeliverAContradictoryCoordinate(pageDashboard, fClientY):
    """Send a mousemove claiming the pointer jumped past the far edge."""
    pageDashboard.evaluate(
        """(fClientY) => {
            document.body.dispatchEvent(new MouseEvent('mousemove', {
                bubbles: true, cancelable: true, view: window,
                clientX: 400, clientY: fClientY, buttons: 1,
            }));
        }""",
        fClientY,
    )


def _fnDeliverThePointerLeavingTheWindow(pageDashboard, dictGeometry):
    """Send the boundary event a browser fires on leaving the window.

    A null relatedTarget is what marks it as leaving the WINDOW
    rather than crossing between two elements inside it.
    """
    pageDashboard.evaluate(
        """(fClientY) => {
            document.body.dispatchEvent(new MouseEvent('mouseout', {
                bubbles: true, cancelable: true, view: window,
                clientX: 400, clientY: fClientY,
                buttons: 1, relatedTarget: null,
            }));
        }""",
        dictGeometry["fWindowHeight"] - 1,
    )


@pytest.mark.falsification
def testAPointerHeldOutsideTheWindowKeepsThePaneScrolling(
    pageDashboard, serverHub,
):
    _fnOpenAHostShell(pageDashboard, serverHub)
    pageDashboard.click(".xterm")
    pageDashboard.keyboard.type(S_FILL_COMMAND)
    pageDashboard.keyboard.press("Enter")
    _fnWaitForPaneText(pageDashboard, S_FILL_MARKER, "the filled scrollback")

    dictGeometry = _fdictPaneGeometry(pageDashboard)
    fMidX = (dictGeometry["fLeft"] + dictGeometry["fRight"]) / 2
    fMidY = (dictGeometry["fTop"] + dictGeometry["fBottom"]) / 2

    # Park well above the newest line, so there is somewhere to go.
    pageDashboard.mouse.move(fMidX, fMidY)
    for _ in range(15):
        pageDashboard.mouse.wheel(0, -120)
    pageDashboard.wait_for_timeout(400)

    # The drag stops INSIDE the pane. This is the strip-skipping
    # flick: xterm's remembered autoscroll speed is zero, and no
    # further mouse event is sent for the rest of the test.
    pageDashboard.mouse.move(dictGeometry["fLeft"] + 60, fMidY)
    pageDashboard.mouse.down()
    pageDashboard.mouse.move(fMidX, dictGeometry["fBottom"] - 4, steps=4)
    pageDashboard.wait_for_timeout(300)

    fStranded = _ffScrollPosition(pageDashboard)
    pageDashboard.wait_for_timeout(400)
    assert _ffScrollPosition(pageDashboard) == fStranded, (
        "the pane was already scrolling before the pointer left the "
        "window, so this test would pass without the recovery it "
        "exists to assert"
    )

    _fnDeliverThePointerLeavingTheWindow(pageDashboard, dictGeometry)
    time.sleep(F_HOLD_SECONDS)
    fMoved = _ffScrollPosition(pageDashboard) - fStranded

    assert fMoved >= F_MINIMUM_SCROLL_PIXELS, (
        f"the pane never scrolled ({fMoved} pixels in "
        f"{F_HOLD_SECONDS}s): a drag held outside the window leaves "
        "text below the last row unreachable"
    )

    # Firefox then reports the held pointer ABOVE the top of the page
    # -- a 768-pixel jump between two frames, for a pointer that has
    # not moved. Believing it ran the pane to the top of its buffer,
    # 3791 pixels the wrong way, which is what a researcher sees as
    # the selection leaping away from the text they were reaching for.
    fBeforeContradiction = _ffScrollPosition(pageDashboard)
    for _ in range(4):
        _fnDeliverAContradictoryCoordinate(
            pageDashboard, F_FIREFOX_REPORTS_A_DOWNWARD_DRAG_AT)
    time.sleep(F_HOLD_SECONDS)
    fAfterContradiction = _ffScrollPosition(pageDashboard)
    pageDashboard.mouse.up()

    assert fAfterContradiction >= fBeforeContradiction, (
        "a coordinate claiming the pointer had jumped above the page "
        f"reversed the drag: the pane went from {fBeforeContradiction} "
        f"to {fAfterContradiction}, scrolling away from the text the "
        "drag was reaching for"
    )
