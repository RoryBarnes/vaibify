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
    _fnOpenAHostShell,
    _fnTypeInTheShell,
    _fnWaitForBufferText,
    _fnWaitForPaneText,
    _fsPaneText,
)


pytestmark = pytest.mark.browser

T_VIEWPORT_NARROW = (900, 620)
T_VIEWPORT_WIDE = (1400, 780)
# Property (2) changes only the HEIGHT. A width change re-wraps the
# buffer, and un-wrapping shortens it -- measured, 4499px to 3027px
# -- so a reader parked 900px from the bottom is CLAMPED there by
# the browser with nowhere else to be. That reads as the pane
# yanking them when nothing did. Height alone moves no text.
T_VIEWPORT_NARROW_TALL = (900, 780)

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


def _fnRequireTheBufferOverflowsThePane(pageDashboard, sWhen):
    """Fail unless the pane has more content than it can show.

    Without this, "the pane is at the bottom" is VACUOUS: a buffer
    that fits entirely has scrollHeight == clientHeight, so it reads
    as flush against the bottom no matter what the code does, and
    every assertion below passes against any behaviour at all. The
    pane's geometry follows the runner's font metrics, so a fixture
    that overflows generously here can fit exactly there -- which is
    how this file came to pass on one machine while its mutation
    survived on another.
    """
    dictGeometry = _fdictReadViewportGeometry(pageDashboard)
    assert dictGeometry, "the terminal pane has no viewport element"
    fScrollable = dictGeometry["fHeight"] - dictGeometry["fClient"]
    assert fScrollable > dictGeometry["fClient"], (
        f"{sWhen}: the pane holds barely more than one screenful "
        f"({dictGeometry}), so 'is it at the bottom' answers yes "
        "whatever the code does and nothing below is a real assertion"
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


I_TAGGED_LINE_COUNT = 44

# Resize EARLY, while the pane is not yet scrolling. That is the
# condition the defect needs: a reflow strands the viewport before
# there is scrollback to speak of, and everything streamed
# afterwards then lands below a viewport that never moves again.
# Deferring the resize until the buffer was several screenfuls deep
# made the mutation SURVIVE -- resizing late simply does not
# reproduce it, so the test measured a bug it had arranged not to
# trigger. The anti-vacuity check therefore belongs AFTER the
# stream, where the real assertion is and the buffer is deep.
I_RESIZE_AFTER_LINE = 4


def _fnStreamTaggedLines(pageDashboard, sTag):
    """Stream uniquely-tagged wrapping lines, slowly enough to resize.

    Its own tag per pass, so the second pass cannot be satisfied by
    output the first one left on screen.
    """
    _fnTypeInTheShell(
        pageDashboard,
        "sPad=$(head -c 150 < /dev/zero | tr '\\0' x); "
        f"for i in $(seq 1 {I_TAGGED_LINE_COUNT}); do "
        f"printf '{sTag}%04d %s\\n' \"$i\" \"$sPad\"; "
        "sleep 0.12; done",
    )


def _fnDriveOneResizeWhileFollowing(
    pageDashboard, sTag, tFrom, tTo,
):
    """Stream, resize mid-stream, and require the pane still follows."""
    pageDashboard.set_viewport_size(
        {"width": tFrom[0], "height": tFrom[1]},
    )
    _fnStreamTaggedLines(pageDashboard, sTag)
    _fnWaitForPaneText(
        pageDashboard, f"{sTag}{I_RESIZE_AFTER_LINE:04d}",
        f"{sTag} output", F_STREAM_DEADLINE_SECONDS,
    )
    assert _fbPaneSitsAtTheBottom(pageDashboard), (
        f"the pane was not following before the {sTag} resize, so "
        "what follows would prove nothing: "
        f"{_fdictReadViewportGeometry(pageDashboard)}"
    )

    pageDashboard.set_viewport_size({"width": tTo[0], "height": tTo[1]})
    sLast = f"{sTag}{I_TAGGED_LINE_COUNT:04d}"
    _fnWaitForBufferText(
        pageDashboard, sLast, f"the last {sTag} line",
        F_STREAM_DEADLINE_SECONDS,
    )
    time.sleep(F_SETTLE_SECONDS)

    _fnRequireTheBufferOverflowsThePane(
        pageDashboard, f"after the {sTag} resize",
    )
    assert _fbPaneSitsAtTheBottom(pageDashboard), (
        f"after resizing {tFrom[0]}x{tFrom[1]} -> {tTo[0]}x{tTo[1]} "
        "during streaming the pane stopped following its output. "
        "Every later line lands below a viewport that never moves "
        "again, which reads as the terminal having hung: "
        f"{_fdictReadViewportGeometry(pageDashboard)}"
    )
    assert sLast in _fsPaneText(pageDashboard), (
        f"the newest line ({sLast}) is not on screen after the "
        f"{tFrom[0]}x{tFrom[1]} -> {tTo[0]}x{tTo[1]} resize"
    )


@pytest.mark.falsification
def test_a_resize_keeps_the_pane_following_its_output(
    pageDashboard, serverHub,
):
    """Resize under a live stream both ways, then under a reader.

    BOTH directions are driven because only one of them strands the
    viewport, and which one depends on the pane's rows and columns --
    which follow the runner's font metrics. Driving only the widening
    direction passed on the author's machine while the mutation
    SURVIVED on CI: the guard was untested there and the report read
    as an undefended guard rather than an unobservable mutation.

    Kills: the restore ALWAYS firing (`bWasFollowingOutput` forced
    true) -> property (2) fails, the reader yanked to the bottom.

    The opposite mutation -- the restore never firing, which is the
    defect this guard was written for -- kills here on a stranding
    geometry and SURVIVED on both Ubuntu CI legs, twice, with both
    directions driven and the buffer confirmed to overflow. Whether a
    reflow strands the viewport depends on the pane's rows and
    columns, and those follow the runner's font metrics; where it does
    not strand, removing the restore changes nothing any test can
    observe. So the portable direction is what the registry pins, and
    this note is the record that the other one is unobservable rather
    than undefended.
    """
    pageDashboard.context.grant_permissions(
        ["clipboard-read", "clipboard-write"],
    )
    pageDashboard.set_viewport_size(
        {"width": T_VIEWPORT_NARROW[0], "height": T_VIEWPORT_NARROW[1]},
    )
    _fnOpenAHostShell(pageDashboard, serverHub)

    # --- (1) resized while following, in both directions ----------
    _fnDriveOneResizeWhileFollowing(
        pageDashboard, "alpha", T_VIEWPORT_NARROW, T_VIEWPORT_WIDE,
    )
    _fnDriveOneResizeWhileFollowing(
        pageDashboard, "beta", T_VIEWPORT_WIDE, T_VIEWPORT_NARROW,
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
        {"width": T_VIEWPORT_NARROW_TALL[0],
         "height": T_VIEWPORT_NARROW_TALL[1]},
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
