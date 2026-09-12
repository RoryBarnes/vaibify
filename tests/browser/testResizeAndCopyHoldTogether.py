"""One pane, two guarantees that have historically traded off.

The terminal pane has oscillated between two defects, each fix
verified against its own symptom and neither pinned against the
other's:

  - ``d4978e6c fix(terminal): stop scrollback duplication from reflow
    churn`` -- a fit at the wrong width, corrected as the layout
    settled, reflowed already-wrapped lines and DUPLICATED them in the
    scrollback. It guarded ``fnRefitTabPreservingSelection`` so a
    reflow happens only on a genuine dimension change.
  - ``93d06af6 feat(terminal): reliable text copy under mouse-capturing
    programs`` -- a fit landing mid-drag destroyed the selection a
    researcher was copying. It guarded ``fnFlushDeferredFit`` so a
    held selection defers the fit instead of taking it.

Those two guards pull in opposite directions: one wants the fit to
land, the other wants it held. Nothing in any lane asserted both at
once, so each change looked complete in isolation and the pair kept
trading off. This file is that missing assertion.

The three properties, in one test because one browser session holds a
project at a time (the one-session-per-container model answers a
second test with "In use in another browser session" rather than a
shell):

  1. A width change WHILE OUTPUT IS STREAMING leaves exactly one copy
     of every streamed line. That is d4978e6c's guarantee, driven
     against the case that produced it -- long lines that wrap, so a
     reflow has something to duplicate.
  2. A selection held across a resize is not taken from the
     researcher, and what reaches the clipboard carries no line twice.
     That is 93d06af6's guarantee.
  3. The held fit LANDS once the selection clears. Deferring is not
     dropping, and without this a future change could satisfy (2) by
     never fitting again at all.

The preconditions are asserted rather than assumed. A resize that did
not actually change the pane's geometry would let (1) pass against a
pane nothing reflowed, and a selection that never formed would let (2)
pass against an empty clipboard. Both are checked before the
assertions that depend on them.

The pane here is a host project's REAL PTY on this machine, so the
streamed bytes are written by a real program through a real terminal,
not modelled by the browser lane's Docker fake.

Kills (confirmed -- each mutation was applied to scriptTerminal.js,
this test run, and the named assertion observed to fail):
  - the ``hasSelection()`` deferral removed from
    ``fnRefitTabPreservingSelection``, so a fit takes a held selection
    -> "the fit landed while a selection was held".
  - ``fnFlushDeferredFit`` no longer fitting, so deferring becomes
    dropping -> "the deferred fit never landed after the selection
    cleared".

NOT confirmed, and said plainly rather than left to be assumed:
property (1) has NO kill. Reflow churn was driven deliberately -- an
interposed ``terminal.resize(40, rows)`` before every refit -- and the
assertion still passed, because xterm's reflow re-wraps losslessly and
cannot duplicate output that was merely appended. So buffer-level
reflow is NOT the mechanism behind duplicated text in this pane. An
in-place REPAINT is: a program that moves the cursor up N rows and
reprints will under-erase once the rows its last frame occupies change
underneath it, and that was reproduced here -- eight frames surviving
where one should. Property (1) therefore stands as a regression guard
on d4978e6c's own symptom, not as a proof about the repaint case.
"""

import re
import time

import pytest

from tests.browser.conftest import (
    S_HOST_PROJECT_READY,
    S_HOST_STEP_NAME,
    S_HOST_WORKFLOW_NAME,
)


pytestmark = pytest.mark.browser

F_SHELL_DEADLINE_SECONDS = 45.0
F_STREAM_DEADLINE_SECONDS = 60.0

# Lines long enough to wrap at either viewport width, so a reflow has
# wrapped rows to re-join and duplicate. Short lines would leave (1)
# passing against a bug it cannot see.
I_STREAMED_LINE_COUNT = 40
I_PADDING_CHARACTERS = 150
S_TOKEN_PATTERN = re.compile(r"mark(\d{4})")

# Narrow before, wide after: the shape the researcher reported, where
# a narrow rendering stayed on screen above the wide one. Height moves
# too, so the refit is observable as a row-count change rather than
# inferred from the container's pixels.
T_VIEWPORT_NARROW = (900, 620)
T_VIEWPORT_WIDE = (1400, 780)
T_VIEWPORT_SHORTER = (1400, 600)

F_RESIZE_SETTLE_SECONDS = 1.0
I_COPY_ON_SELECT_SETTLE_MILLISECONDS = 700


def _fsPaneText(pageDashboard):
    """Return the terminal pane's VISIBLE rows, whitespace collapsed."""
    pageDashboard.wait_for_selector(".xterm-rows", timeout=20000)
    return " ".join(pageDashboard.text_content(".xterm-rows").split())


def _fiVisibleRowCount(pageDashboard):
    """Return the pane's row count, as the DOM renderer reports it.

    xterm's DOM renderer keeps one div per terminal row, so this IS
    ``terminal.rows`` -- observable without reaching inside the IIFE,
    and it moves if and only if a fit actually landed.
    """
    return pageDashboard.evaluate(
        "() => document.querySelectorAll('.xterm-rows > div').length",
    )


def _fbSelectionIsHeld(pageDashboard):
    """Return whether xterm is painting a selection right now.

    Measured by the selection layer's painted AREA, not by its child
    count. xterm keeps its selection divs in the DOM and collapses
    them to zero size when nothing is selected -- measured: three divs
    covering 77440 square pixels while a selection is held, two divs
    covering 0 once it is cleared. Counting divs would therefore
    report a selection that does not exist, and this assertion would
    pass against a pane a resize had just wiped.
    """
    return pageDashboard.evaluate(
        """() => Array.from(
            document.querySelectorAll('.xterm-selection div')
        ).reduce((fArea, el) => {
            const dictRect = el.getBoundingClientRect();
            return fArea + dictRect.width * dictRect.height;
        }, 0) > 0""",
    )


def _fnWaitForPaneText(pageDashboard, sExpected, sWhat, fDeadlineSeconds):
    """Block until sExpected is visible in the pane, or fail saying why."""
    fStarted = time.monotonic()
    sPane = _fsPaneText(pageDashboard)
    while sExpected not in sPane:
        assert time.monotonic() - fStarted < fDeadlineSeconds, (
            f"{sWhat} never appeared in the terminal pane. "
            f"Pane read: {sPane[-400:]}"
        )
        pageDashboard.wait_for_timeout(250)
        sPane = _fsPaneText(pageDashboard)
    return sPane


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
        F_SHELL_DEADLINE_SECONDS,
    )


def _fnTypeInTheShell(pageDashboard, sCommand):
    pageDashboard.click(".xterm")
    pageDashboard.keyboard.type(sCommand)
    pageDashboard.keyboard.press("Enter")


def _fnStartStreamingWrappedLines(pageDashboard):
    """Start a slow stream of uniquely-tagged lines that each wrap.

    The sleep is what makes the resize land MID-stream: an instant
    dump would finish before the viewport moved, and the test would
    assert about a reflow that happened over settled output rather
    than over output still arriving.
    """
    sPad = (
        f"sPad=$(head -c {I_PADDING_CHARACTERS} < /dev/zero "
        "| tr '\\0' 'x')"
    )
    sLoop = (
        f"for i in $(seq 1 {I_STREAMED_LINE_COUNT}); do "
        "printf 'mark%04d %s\\n' \"$i\" \"$sPad\"; sleep 0.12; done"
    )
    _fnTypeInTheShell(pageDashboard, f"{sPad}; {sLoop}")


def _fsCopyWholeBuffer(pageDashboard):
    """Return the entire scrollback, wrapped rows re-joined.

    "Copy all" is used rather than a drag because it touches no mouse
    and reads the whole buffer -- so a line duplicated ABOVE the
    visible screen is still caught.
    """
    pageDashboard.click(".terminal-pane-copy")
    pageDashboard.wait_for_timeout(500)
    return pageDashboard.evaluate(
        "() => navigator.clipboard.readText()",
    )


def _fdictCountTokens(sText):
    """Return each streamed line's tag mapped to how often it appears."""
    dictCounts = {}
    for sToken in S_TOKEN_PATTERN.findall(sText):
        dictCounts[sToken] = dictCounts.get(sToken, 0) + 1
    return dictCounts


def _flistDescribeDuplicates(dictCounts):
    """Return a readable list of every tag seen more than once."""
    return sorted(
        f"mark{sToken} x{iCount}"
        for sToken, iCount in dictCounts.items() if iCount > 1
    )


def _fnWaitForBufferText(pageDashboard, sExpected, sWhat, fDeadlineSeconds):
    """Block until sExpected is anywhere in the whole scrollback.

    The VIEWPORT is the wrong thing to poll here. A reflow can leave
    the pane parked mid-buffer -- measured: ``ydisp 14 -> 2`` on a
    widening refit -- and nothing scrolls it back down, so visible
    rows can go on showing old output while the stream runs to
    completion below. Reading the whole buffer asks the question the
    test actually means: did these bytes arrive?
    """
    fStarted = time.monotonic()
    sBuffer = _fsCopyWholeBuffer(pageDashboard)
    while sExpected not in sBuffer:
        assert time.monotonic() - fStarted < fDeadlineSeconds, (
            f"{sWhat} never reached the terminal buffer. "
            f"Buffer tail: {sBuffer[-400:]}"
        )
        pageDashboard.wait_for_timeout(500)
        sBuffer = _fsCopyWholeBuffer(pageDashboard)
    return sBuffer


def _fnDragASelectionAcrossThePane(pageDashboard):
    """Drag a selection from the left edge across several rows.

    It starts at the left edge because every streamed line carries its
    tag in the first columns; a band starting mid-pane could select
    only padding and leave the clipboard assertion vacuous.
    """
    dictBox = pageDashboard.locator(".xterm-screen").bounding_box()
    fLeft = dictBox["x"] + 2
    fRight = dictBox["x"] + dictBox["width"] - 4
    fTop = dictBox["y"] + dictBox["height"] * 0.25
    fBottom = dictBox["y"] + dictBox["height"] * 0.70
    pageDashboard.mouse.move(fLeft, fTop)
    pageDashboard.mouse.down()
    # Intermediate moves: xterm grows a selection from mousemove
    # events, and a single jump to the end point can be delivered as
    # one move that it never sees as a drag.
    pageDashboard.mouse.move(fRight * 0.6, (fTop + fBottom) / 2)
    pageDashboard.mouse.move(fRight, fBottom)
    pageDashboard.mouse.up()
    pageDashboard.wait_for_timeout(
        I_COPY_ON_SELECT_SETTLE_MILLISECONDS,
    )


@pytest.mark.falsification
def test_a_resize_during_output_neither_duplicates_nor_breaks_copy(
    pageDashboard, serverHub,
):
    """A width change mid-stream, then a selection held across one.
    Kills: the `hasSelection()` deferral removed from
    `fnRefitTabPreservingSelection`, so a fit takes a held
    selection. (A second mutation -- `fnFlushDeferredFit` never
    fitting -- was also kill-confirmed against this test; the
    registry admits one entry per nodeid, so it is recorded here.)
    """
    pageDashboard.context.grant_permissions(
        ["clipboard-read", "clipboard-write"],
    )
    pageDashboard.set_viewport_size(
        {"width": T_VIEWPORT_NARROW[0], "height": T_VIEWPORT_NARROW[1]},
    )
    _fnOpenAHostShell(pageDashboard, serverHub)

    # --- (1) a resize while output is streaming -------------------
    iRowsBefore = _fiVisibleRowCount(pageDashboard)
    _fnStartStreamingWrappedLines(pageDashboard)
    _fnWaitForPaneText(
        pageDashboard, "mark0003", "the streamed output",
        F_STREAM_DEADLINE_SECONDS,
    )
    pageDashboard.set_viewport_size(
        {"width": T_VIEWPORT_WIDE[0], "height": T_VIEWPORT_WIDE[1]},
    )
    sWholeBuffer = _fnWaitForBufferText(
        pageDashboard,
        f"mark{I_STREAMED_LINE_COUNT:04d}",
        "the last streamed line",
        F_STREAM_DEADLINE_SECONDS,
    )

    # The precondition: the pane really did refit, so what follows is
    # a statement about a reflow rather than about a pane that never
    # moved.
    iRowsAfter = _fiVisibleRowCount(pageDashboard)
    assert iRowsAfter != iRowsBefore, (
        "the viewport changed but the pane's row count did not, so no "
        "fit landed and this test would prove nothing about reflow; "
        f"rows stayed at {iRowsBefore}"
    )

    dictCounts = _fdictCountTokens(sWholeBuffer)
    assert len(dictCounts) == I_STREAMED_LINE_COUNT, (
        "the buffer did not hold every streamed line, so the "
        "duplication check below had nothing to measure; found "
        f"{len(dictCounts)} of {I_STREAMED_LINE_COUNT}"
    )
    listDuplicated = _flistDescribeDuplicates(dictCounts)
    assert not listDuplicated, (
        "a resize during streaming duplicated output in the "
        "scrollback -- the reflow-churn defect d4978e6c fixed is "
        f"back: {listDuplicated}"
    )

    # --- (2) a selection held across a resize ---------------------
    _fnDragASelectionAcrossThePane(pageDashboard)
    assert _fbSelectionIsHeld(pageDashboard), (
        "no selection formed, so nothing below says anything about "
        "what a resize does to one"
    )
    sClipboardBefore = pageDashboard.evaluate(
        "() => navigator.clipboard.readText()",
    )
    dictSelected = _fdictCountTokens(sClipboardBefore)
    assert dictSelected, (
        "copy-on-select produced no tagged line, so the clipboard "
        "assertions would be vacuous; clipboard held "
        f"{len(sClipboardBefore)} characters"
    )

    iRowsHeld = _fiVisibleRowCount(pageDashboard)
    pageDashboard.set_viewport_size(
        {"width": T_VIEWPORT_SHORTER[0],
         "height": T_VIEWPORT_SHORTER[1]},
    )
    time.sleep(F_RESIZE_SETTLE_SECONDS)

    assert _fbSelectionIsHeld(pageDashboard), (
        "a resize took the selection away from under the researcher, "
        "which is the copy defect 93d06af6 fixed"
    )
    assert _fiVisibleRowCount(pageDashboard) == iRowsHeld, (
        "the fit landed while a selection was held, so the deferral "
        "that protects a drag is gone; rows moved "
        f"{iRowsHeld} -> {_fiVisibleRowCount(pageDashboard)}"
    )
    listSelectedDuplicated = _flistDescribeDuplicates(dictSelected)
    assert not listSelectedDuplicated, (
        "the copied selection carried a line more than once -- the "
        "shape a researcher sees as duplicated text in a paste: "
        f"{listSelectedDuplicated}"
    )

    # --- (3) the held fit lands once the selection clears ---------
    dictScreenBox = pageDashboard.locator(".xterm-screen").bounding_box()
    pageDashboard.mouse.click(
        dictScreenBox["x"] + 30, dictScreenBox["y"] + 6,
    )
    time.sleep(F_RESIZE_SETTLE_SECONDS)
    assert not _fbSelectionIsHeld(pageDashboard), (
        "the click did not clear the selection, so the release below "
        "was never actually exercised"
    )
    assert _fiVisibleRowCount(pageDashboard) != iRowsHeld, (
        "the deferred fit never landed after the selection cleared, "
        "so deferring has become dropping and the pane is now the "
        f"wrong size for its container; rows still {iRowsHeld}"
    )
