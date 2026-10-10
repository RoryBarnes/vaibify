"""The pane's Reset button switches off the input modes a program left on.

A full-screen program that is SIGKILLed never switches mouse reporting
off, and every mouse move then prints garbage at the prompt. A container
shell's prompt hook now resets it on its own; Reset is the by-hand
fallback, and it must not cost the researcher the scrollback they may be
about to copy -- so it writes the reset sequence as program output
rather than calling ``terminal.reset()``.

The pane is a host project's REAL pty, and mouse reporting is switched
on by the shell itself, which is what a program does. A host shell has
no prompt hook (host mode is unchanged), so the mode stays on until the
button is pressed. Whether xterm holds the mouse is read from the class
it keeps on its own element (``enable-mouse-events``), the same state
``fbProgramHoldsTheMouse`` reads.

WHAT THIS DOES NOT COVER: the container prompt hook, which
``testTerminalPromptResetLive.py`` runs in a real container.
"""

import time

import pytest

from tests.browser.testSelectingTextWhileAProgramHoldsTheMouse import (
    _fnOpenAHostShell,
    _fnRunInTheShell,
    _fnWaitForPaneText,
)


pytestmark = [pytest.mark.browser]

S_FILL_COMMAND = "for i in $(seq 1 30); do echo resetline $i; done"
S_FILL_MARKER = "resetline 30"
S_MOUSE_REPORTING_ON = "printf '\\033[?1000h\\033[?1003h\\033[?1006h'"
F_DEADLINE_SECONDS = 20.0


def _fbXtermHoldsTheMouse(page):
    return page.evaluate(
        "() => document.querySelector('.xterm')"
        ".classList.contains('enable-mouse-events')")


def _fnWaitForMouseState(page, bHeld):
    fStarted = time.monotonic()
    while _fbXtermHoldsTheMouse(page) != bHeld:
        assert time.monotonic() - fStarted < F_DEADLINE_SECONDS, (
            f"xterm never reached mouse-held={bHeld}")
        page.wait_for_timeout(200)


@pytest.mark.falsification
def testResetGivesTheMouseBackAndKeepsTheScrollback(pageDashboard, serverHub):
    """Kills: Reset calling terminal.reset(), which erases the scrollback."""
    _fnOpenAHostShell(pageDashboard, serverHub)
    _fnRunInTheShell(pageDashboard, S_FILL_COMMAND)
    _fnWaitForPaneText(pageDashboard, S_FILL_MARKER, "the fill output")
    _fnRunInTheShell(pageDashboard, S_MOUSE_REPORTING_ON)
    _fnWaitForMouseState(pageDashboard, True)
    pageDashboard.click(".terminal-pane-reset")
    _fnWaitForMouseState(pageDashboard, False)
    pageDashboard.wait_for_timeout(300)
    assert S_FILL_MARKER in pageDashboard.text_content(".xterm-rows"), (
        "Reset threw away what the pane was showing")
    assert pageDashboard.listPageErrors == []
