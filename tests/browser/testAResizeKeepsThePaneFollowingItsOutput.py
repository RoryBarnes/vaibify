"""A resize must not strand the viewport away from the newest output.

The defect, measured on a real pane: resize the window while output is
streaming and the pane stops following it, permanently. The viewport
sat 16 pixels down a buffer 1475 pixels tall, showing the fourth line
of forty, and no amount of further output brought it back -- because
xterm resumes auto-scrolling only for a pane that is exactly at the
bottom, so once a reflow leaves it one row short it never follows
again. From the chair the terminal simply stops, which is
indistinguishable from a hung program and was diagnosed as one.

Restoring it is easy to get wrong in the other direction, so both
directions are asserted here:

  1. A pane that WAS following still shows the newest line after a
     resize.
  2. A pane the researcher had scrolled back is left where they put
     it. Yanking a reader to the bottom because the window changed
     size is its own defect, and a fix that always scrolls would pass
     (1) while committing it.

Why the follow state is REMEMBERED rather than measured when the
resize arrives: by the time any of our code runs, the browser has
already re-laid-out the pane, so the element reports its new height
against a buffer that has not reflowed. Measured, that combination
read "128 pixels from the bottom" -- a scroll-back that never
happened -- for a pane nobody had touched, and the restore correctly
declined to fire. The tracking, and its rule that a scroll arriving
with a changed clientHeight is the browser re-clamping rather than a
person moving, is what property (1) actually exercises.

Kills (confirmed -- each mutation was applied, this test run, and the
named assertion observed to fail):
  - the restore never firing (`bWasFollowingOutput` forced false) ->
    property (1) fails with the newest line off screen.
  - the restore always firing (`bWasFollowingOutput` forced true) ->
    property (2) fails, the reader yanked to the bottom.
"""

import time

import pytest

from tests.browser.testResizeAndCopyHoldTogether import (
    F_STREAM_DEADLINE_SECONDS,
    I_STREAMED_LINE_COUNT,
    _fnOpenAHostShell,
    _fnStartStreamingWrappedLines,
    _fnWaitForBufferText,
    _fnWaitForPaneText,
    _fsPaneText,
)


pytestmark = pytest.mark.browser

T_VIEWPORT_NARROW = (900, 620)
T_VIEWPORT_WIDE = (1400, 780)

F_SETTLE_SECONDS = 1.5
I_SCROLL_BACK_PIXELS = -900


def _fdictReadViewportGeometry(pageDashboard):
    """Return the pane's scrolling geometry, as the browser reports it."""
    return pageDashboard.evaluate(
        """() => {
            const el = document.querySelector('.xterm-viewport');
            return el ? {
                fTop: el.scrollTop,
                fHeight: el.scrollHeight,
                fClient: el.clientHeight,
            } : null;
        }""",
    )


def _fbPaneSitsAtTheBottom(pageDashboard):
    """Return whether the pane is showing its newest output.

    Read from the scrolling element rather than from a rendered line,
    so "at the bottom" means the same thing before and after a reflow
    re-wraps every row.
    """
    dictGeometry = _fdictReadViewportGeometry(pageDashboard)
    assert dictGeometry, "the terminal pane has no viewport element"
    fRemaining = (dictGeometry["fHeight"] - dictGeometry["fTop"]
                  - dictGeometry["fClient"])
    return fRemaining <= max(dictGeometry["fClient"] / 40.0, 1)


def _fnScrollTheePaneBack(pageDashboard):
    """Turn the wheel over the pane to scroll away from the newest line."""
    dictBox = pageDashboard.locator(".xterm-screen").bounding_box()
    pageDashboard.mouse.move(
        dictBox["x"] + dictBox["width"] / 2,
        dictBox["y"] + dictBox["height"] / 2,
    )
    pageDashboard.mouse.wheel(0, I_SCROLL_BACK_PIXELS)
    pageDashboard.wait_for_timeout(500)


@pytest.mark.falsification
def test_a_resize_keeps_the_pane_following_its_output(
    pageDashboard, serverHub,
):
    """Resize under a live stream, then resize under a reader.
    Kills: the restore never firing (`bWasFollowingOutput` forced
    false) -> the pane stops following its output. (The opposite
    mutation, forced true, was also kill-confirmed against
    property (2); the registry admits one entry per nodeid.)
    """
    pageDashboard.context.grant_permissions(
        ["clipboard-read", "clipboard-write"],
    )
    pageDashboard.set_viewport_size(
        {"width": T_VIEWPORT_NARROW[0], "height": T_VIEWPORT_NARROW[1]},
    )
    _fnOpenAHostShell(pageDashboard, serverHub)

    # --- (1) resized while following ------------------------------
    _fnStartStreamingWrappedLines(pageDashboard)
    _fnWaitForPaneText(
        pageDashboard, "mark0004", "early output",
        F_STREAM_DEADLINE_SECONDS,
    )
    assert _fbPaneSitsAtTheBottom(pageDashboard), (
        "the pane was not following its output before the resize, so "
        "property (1) would prove nothing: "
        f"{_fdictReadViewportGeometry(pageDashboard)}"
    )

    pageDashboard.set_viewport_size(
        {"width": T_VIEWPORT_WIDE[0], "height": T_VIEWPORT_WIDE[1]},
    )
    _fnWaitForBufferText(
        pageDashboard,
        f"mark{I_STREAMED_LINE_COUNT:04d}",
        "the last streamed line",
        F_STREAM_DEADLINE_SECONDS,
    )
    time.sleep(F_SETTLE_SECONDS)

    assert _fbPaneSitsAtTheBottom(pageDashboard), (
        "after a resize during streaming the pane stopped following "
        "its output; every later line lands below a viewport that "
        "never moves again, which reads as the terminal having hung: "
        f"{_fdictReadViewportGeometry(pageDashboard)}"
    )
    assert f"mark{I_STREAMED_LINE_COUNT:04d}" in _fsPaneText(pageDashboard), (
        "the newest line is not on screen after the resize, so the "
        "researcher cannot see the output that is still arriving"
    )

    # --- (2) resized while reading --------------------------------
    _fnScrollTheePaneBack(pageDashboard)
    assert not _fbPaneSitsAtTheBottom(pageDashboard), (
        "the wheel did not move the pane off the newest line, so "
        "property (2) would pass against any behaviour at all: "
        f"{_fdictReadViewportGeometry(pageDashboard)}"
    )
    dictBeforeSecondResize = _fdictReadViewportGeometry(pageDashboard)

    pageDashboard.set_viewport_size(
        {"width": T_VIEWPORT_NARROW[0], "height": T_VIEWPORT_NARROW[1]},
    )
    time.sleep(F_SETTLE_SECONDS)

    assert not _fbPaneSitsAtTheBottom(pageDashboard), (
        "a resize yanked the researcher to the newest line while they "
        "were reading further back. Scrolling someone away from what "
        "they are reading because the window changed size is not a "
        "fix for the parked viewport, it is the opposite defect; "
        f"they were at {dictBeforeSecondResize}, now at "
        f"{_fdictReadViewportGeometry(pageDashboard)}"
    )
