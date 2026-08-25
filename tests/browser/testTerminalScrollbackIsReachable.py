"""The scrollback stays reachable while a program is holding the mouse.

This closes a gap that blocked real work and that no test could have
caught, because nothing in any lane had ever driven a wheel or a
selection in the terminal pane.

What went wrong: a full-screen program (an agent, vim, htop) turns on
mouse reporting, and xterm then forwards the WHEEL to that program.
Option+drag overrides the same capture for SELECTION, but there is no
built-in override for the wheel, and xterm's drag-scroll engages only
once the pointer leaves the pane -- so a researcher could neither
scroll back to earlier output nor grow a selection past the visible
screen. Everything above the current screenful was unreachable, and
copy appeared broken when it was reach that was broken.

The precondition is asserted, not assumed. A plain wheel is driven
first and must NOT scroll: that is what proves the program in the pane
is genuinely capturing the mouse. Without it this file would pass just
as happily against a pane nothing was capturing, and would prove
nothing about the case it exists for.

Both remedies are then driven end to end: Shift+wheel reaches the
scrollback through the capture, and "Copy all" reaches it with no
gesture at all. The second matters because it cannot be defeated by
any program -- it never touches the mouse.

The capture here is real, not modelled. A host project's pane is a
real PTY on this machine, and the mouse-reporting escape sequences are
written by a real program running in it.

Kills (confirmed, not assumed -- each mutation was applied and the
named assertion observed to fail):
  - ``fbHandleScrollbackWheelEvent`` returning true unconditionally,
    i.e. the Shift+wheel override removed -> the Shift+wheel assertion
    fails with the pane still pinned at the newest line.
  - ``flistReadBufferLines`` starting at ``buffer.viewportY`` instead
    of row 0, i.e. "Copy all" copying the visible screen -> the
    clipboard assertion fails on the earliest seeded line.
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
I_SEEDED_LINE_COUNT = 300
S_LINE_PATTERN = re.compile(r"line(\d{4})")

# Enough pixels to cross several screenfuls, so the assertion does not
# depend on how many rows the pane happens to have on this runner.
I_WHEEL_DELTA_PIXELS = -900


def _fsPaneText(pageDashboard):
    """Return the terminal pane's VISIBLE rows, whitespace collapsed."""
    pageDashboard.wait_for_selector(".xterm-rows", timeout=20000)
    return " ".join(pageDashboard.text_content(".xterm-rows").split())


def _fiHighestVisibleLineNumber(pageDashboard):
    """Return the largest seeded line number currently on screen.

    The pane renders only its viewport, so this number IS the scroll
    position, expressed in something a researcher can see. Comparing it
    avoids pinning the assertion to a row count that differs between
    runners.
    """
    listMatches = S_LINE_PATTERN.findall(_fsPaneText(pageDashboard))
    return max((int(sMatch) for sMatch in listMatches), default=-1)


def _fnWaitForPaneText(pageDashboard, sExpected, sWhat):
    """Block until sExpected is visible in the pane, or fail saying why."""
    fStarted = time.monotonic()
    sPane = _fsPaneText(pageDashboard)
    while sExpected not in sPane:
        assert time.monotonic() - fStarted < F_SHELL_DEADLINE_SECONDS, (
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
    )


def _fnTypeInTheShell(pageDashboard, sCommand):
    pageDashboard.click(".xterm")
    pageDashboard.keyboard.type(sCommand)
    pageDashboard.keyboard.press("Enter")


def _fnSeedScrollbackAndCaptureTheMouse(pageDashboard):
    """Fill the scrollback, then start a program that owns the mouse.

    ``stty -echo`` before the capture keeps the pane readable: with it
    on, every mouse escape sequence the wheel sends would be echoed
    back as garbage at the prompt. ``cat`` then holds the foreground
    and swallows them, which is what a real full-screen program does
    with the reports it asked for.
    """
    _fnTypeInTheShell(
        pageDashboard,
        f"seq -f 'line%04g payload' 1 {I_SEEDED_LINE_COUNT}",
    )
    _fnWaitForPaneText(
        pageDashboard,
        f"line{I_SEEDED_LINE_COUNT:04d}",
        "the seeded scrollback",
    )
    _fnTypeInTheShell(
        pageDashboard,
        "printf '\\033[?1000h\\033[?1002h\\033[?1006h'; "
        "stty -echo; cat > /dev/null",
    )
    pageDashboard.wait_for_timeout(1000)


def _fnScrollOverThePane(pageDashboard, bShiftKey):
    """Put the pointer on the pane and turn the wheel."""
    dictBox = pageDashboard.locator(".xterm-screen").bounding_box()
    pageDashboard.mouse.move(
        dictBox["x"] + dictBox["width"] / 2,
        dictBox["y"] + dictBox["height"] / 2,
    )
    if bShiftKey:
        pageDashboard.keyboard.down("Shift")
    pageDashboard.mouse.wheel(0, I_WHEEL_DELTA_PIXELS)
    if bShiftKey:
        pageDashboard.keyboard.up("Shift")
    pageDashboard.wait_for_timeout(500)


def _fnShiftScrollAsTheBrowserSendsIt(pageDashboard):
    """Deliver a shifted wheel the way a real browser delivers one.

    A browser remaps a shifted wheel onto the HORIZONTAL axis: deltaY
    arrives as 0 and the magnitude rides deltaX. Playwright's
    ``mouse.wheel`` does not reproduce that -- it writes deltaY
    whatever the modifier state -- so the lane was driving a shape no
    browser sends, and passed against a handler that measured zero
    lines and then swallowed the event. Dispatching the event directly
    is the only way to put the real shape through the real listener.
    """
    pageDashboard.evaluate(
        """(iDelta) => {
            const el = document.querySelector('.xterm-screen');
            el.dispatchEvent(new WheelEvent('wheel', {
                deltaX: iDelta, deltaY: 0, deltaMode: 0,
                shiftKey: true, bubbles: true, cancelable: true,
            }));
        }""",
        I_WHEEL_DELTA_PIXELS,
    )
    pageDashboard.wait_for_timeout(500)


def test_the_scrollback_is_reachable_while_a_program_holds_the_mouse(
    pageDashboard, serverHub,
):
    """One captured pane, three properties, because all three are about
    that capture.

    They cannot be separate tests: one browser session holds the
    project at a time (the one-session-per-container model), so a
    second test would be answered with "In use in another browser
    session" rather than a shell.
    """
    pageDashboard.context.grant_permissions(
        ["clipboard-read", "clipboard-write"],
    )
    _fnOpenAHostShell(pageDashboard, serverHub)
    _fnSeedScrollbackAndCaptureTheMouse(pageDashboard)

    iAtBottom = _fiHighestVisibleLineNumber(pageDashboard)
    assert iAtBottom == I_SEEDED_LINE_COUNT, (
        "the pane should be sitting at the newest output before any "
        f"scrolling; the highest visible line was {iAtBottom}"
    )

    # The precondition, asserted rather than assumed: a plain wheel is
    # swallowed by the program, so this pane really is capturing.
    _fnScrollOverThePane(pageDashboard, bShiftKey=False)
    iAfterPlainWheel = _fiHighestVisibleLineNumber(pageDashboard)
    assert iAfterPlainWheel == I_SEEDED_LINE_COUNT, (
        "a plain wheel scrolled the pane, so nothing was capturing the "
        "mouse and the rest of this test would prove nothing about the "
        f"case it exists for; highest visible line {iAfterPlainWheel}"
    )

    # The remedy: Shift+wheel reaches the scrollback through the capture.
    _fnScrollOverThePane(pageDashboard, bShiftKey=True)
    iAfterShiftWheel = _fiHighestVisibleLineNumber(pageDashboard)
    assert iAfterShiftWheel < I_SEEDED_LINE_COUNT, (
        "Shift+wheel did not reach the scrollback while a program held "
        "the mouse, so everything above the visible screen is "
        f"unreachable again; highest visible line {iAfterShiftWheel}"
    )

    # ...and it must work on the axis a REAL browser puts it on. This
    # assertion is the one that would have caught the shipped bug: the
    # synthetic-deltaY case above passed while every actual Shift+
    # scroll in Firefox scrolled nothing at all.
    _fnShiftScrollAsTheBrowserSendsIt(pageDashboard)
    iAfterRealShift = _fiHighestVisibleLineNumber(pageDashboard)
    assert iAfterRealShift < iAfterShiftWheel, (
        "a shifted wheel carrying its magnitude on deltaX -- which is "
        "how a browser delivers Shift+scroll -- did not scroll the "
        f"pane; highest visible line went {iAfterShiftWheel} -> "
        f"{iAfterRealShift}"
    )

    # The remedy that needs no mouse at all, and so cannot be defeated
    # by any program: the whole buffer, not the visible screenful.
    pageDashboard.click(".terminal-pane-copy")
    pageDashboard.wait_for_timeout(500)
    sClipboard = pageDashboard.evaluate(
        "() => navigator.clipboard.readText()",
    )
    for iLine in (1, I_SEEDED_LINE_COUNT // 2, I_SEEDED_LINE_COUNT):
        assert f"line{iLine:04d}" in sClipboard, (
            f"'Copy all' missed line{iLine:04d}, so it copied the "
            "visible screen rather than the scrollback: "
            f"{len(sClipboard)} characters copied"
        )
