"""A resize must not strand old frames above a repainting program.

This is the researcher-visible defect: resize the pane while an agent
is answering, and its earlier output stays on screen at the old
wrapping, above the new. The reported artifact was the same passage
twice at two different widths.

The mechanism. xterm re-wraps its buffer the instant it is resized;
the program learns its new width only when SIGWINCH arrives. A program
that repaints in place -- cursor up N rows, erase to end of screen,
reprint -- therefore composes a frame for one width and has it painted
at another. Its cursor-up lands short, the erase misses, and the old
frame survives. The hub closes the window by draining the pty and
resizing it BEFORE telling the browser it may reflow, so text composed
at the old width is painted into the old-width buffer and text
composed after it into the new one.

The program driven here is a faithful proxy for an Ink renderer (which
is what Claude Code is): it repaints in place fifty times a second and
re-reads its width from a real SIGWINCH handler, which is what Node
does. A shell loop is NOT a usable proxy -- bash did not run its WINCH
trap inside a tight loop at all, so it never learned the new width and
would have reported this broken however well it worked.

ONE stale frame is tolerated, and the number is not slack. The program
tracks the rows it PRINTED; the reflow changes how many rows that text
occupies; so its first erase after a resize is short by construction,
and no ordering on this side can reach a program's model of its own
output. Measured on a real pane: three stale frames before the drain
existed, one after. The assertion is therefore "at most one", and it
is what fails if the drain is removed.

Kills (confirmed -- the mutation was applied, this test run, and the
named assertion observed to fail):
  - the drain removed from
    ``_fnApplyPendingResizeAndAcknowledge``, so the hub acknowledges
    before forwarding what the program already wrote -> the residue
    assertion fails with three stale frames.
"""

import re
import time

import pytest

from tests.browser.testResizeAndCopyHoldTogether import (
    _fnOpenAHostShell,
    _fnTypeInTheShell,
    _fsPaneText,
)


pytestmark = pytest.mark.browser

S_FRAME_PATTERN = re.compile(r"F(\d{3})L\dC(\d{3})")

T_VIEWPORT_WIDE = (1400, 780)
T_VIEWPORT_NARROW = (820, 620)

F_SETTLE_BEFORE_RESIZE_SECONDS = 2.5
F_SETTLE_AFTER_RESIZE_SECONDS = 4.0

# One stale frame is the floor, not slack -- see the module docstring.
I_TOLERATED_STALE_FRAMES = 1

S_REPAINTER = '''\
import fcntl, signal, struct, sys, termios, time

I_LINE_LENGTH = 100
I_LINES_PER_FRAME = 3


def fiReadColumns():
    baPacked = fcntl.ioctl(
        sys.stdout.fileno(), termios.TIOCGWINSZ, b"\\0" * 8)
    return struct.unpack("HHHH", baPacked)[1]


def fiRowsPerFrame(iColumns):
    return -(-I_LINE_LENGTH // max(1, iColumns)) * I_LINES_PER_FRAME


dictState = {"iColumns": fiReadColumns()}
signal.signal(
    signal.SIGWINCH,
    lambda iSignal, frame: dictState.update(iColumns=fiReadColumns()))

sys.stdout.write("\\033[2J\\033[H")
iPreviousRows = 0
for iFrame in range(1, 401):
    if iPreviousRows:
        sys.stdout.write("\\033[%dA\\033[0J" % iPreviousRows)
    iColumns = dictState["iColumns"]
    sPad = "x" * (I_LINE_LENGTH - 12)
    for iLine in range(1, I_LINES_PER_FRAME + 1):
        sys.stdout.write(
            "F%03dL%dC%03d %s\\n" % (iFrame, iLine, iColumns, sPad))
    sys.stdout.flush()
    iPreviousRows = fiRowsPerFrame(iColumns)
    time.sleep(0.02)
'''


def _fsetReadVisibleWidths(pageDashboard):
    """Return the set of widths the VISIBLE frames were composed for.

    The pane's own text is the evidence: every line carries the width
    the program believed when it wrote it, so a stale frame announces
    itself rather than being inferred from geometry.
    """
    return {sWidth for _, sWidth
            in S_FRAME_PATTERN.findall(_fsPaneText(pageDashboard))}


def _fiCountVisibleFramesAtWidth(pageDashboard, sWidth):
    """Return how many distinct frames on screen carry that width."""
    return len({sFrame for sFrame, sSeen
                in S_FRAME_PATTERN.findall(_fsPaneText(pageDashboard))
                if sSeen == sWidth})


@pytest.mark.falsification
def test_a_resize_does_not_strand_old_frames(
    pageDashboard, serverHub, tmp_path,
):
    """Narrow the pane under a repainting program; count what stays.
    Kills: the drain removed from
    `_fnApplyPendingResizeAndAcknowledge` -> three stale frames
    instead of one (observed).
    """
    pathProgram = tmp_path / "paneRepainter.py"
    pathProgram.write_text(S_REPAINTER)

    pageDashboard.set_viewport_size(
        {"width": T_VIEWPORT_WIDE[0], "height": T_VIEWPORT_WIDE[1]},
    )
    _fnOpenAHostShell(pageDashboard, serverHub)
    _fnTypeInTheShell(pageDashboard, f"python3 {pathProgram}")
    time.sleep(F_SETTLE_BEFORE_RESIZE_SECONDS)

    setWidthsBefore = _fsetReadVisibleWidths(pageDashboard)
    assert len(setWidthsBefore) == 1, (
        "the pane was not showing a single repainting program before "
        f"the resize, so nothing below is measurable: {setWidthsBefore}"
    )
    sWidthBefore = setWidthsBefore.pop()

    pageDashboard.set_viewport_size(
        {"width": T_VIEWPORT_NARROW[0], "height": T_VIEWPORT_NARROW[1]},
    )
    time.sleep(F_SETTLE_AFTER_RESIZE_SECONDS)

    # The precondition: the PROGRAM reacted. Without this the residue
    # count below would pass against a pane whose pty was never
    # resized at all -- which is a different bug wearing this one's
    # clothes.
    setWidthsAfter = _fsetReadVisibleWidths(pageDashboard)
    assert setWidthsAfter - {sWidthBefore}, (
        "no frame was composed at a new width, so the program never "
        "learned the pane had been resized and this test is measuring "
        f"nothing; widths on screen: {setWidthsAfter}"
    )

    iStale = _fiCountVisibleFramesAtWidth(pageDashboard, sWidthBefore)
    assert iStale <= I_TOLERATED_STALE_FRAMES, (
        f"{iStale} frames composed at the old width ({sWidthBefore} "
        "columns) are still on screen after the resize. That is the "
        "researcher-visible defect: old output stranded above the new "
        "at the old wrapping. At most "
        f"{I_TOLERATED_STALE_FRAMES} is expected, because the program "
        "tracks the rows it printed and the reflow changes them"
    )
