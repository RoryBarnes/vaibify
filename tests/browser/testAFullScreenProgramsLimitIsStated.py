"""A program on the whole screen leaves no scrollback, and the pane says so.

Every earlier terminal test modelled a mouse-capturing program in the
NORMAL buffer, which keeps scrollback, so the suite stayed green while
Claude Code's fullscreen renderer -- the ALTERNATE screen plus mouse
capture -- took both scrollback and selection away. The fixture here is
that shape: it enters ``?1049h``, turns on mouse reporting, and holds
the foreground. The test first establishes, on screen, that the pane
really has no scrollback in that state, and only then checks that the
"Select text" button and the terminal help say so, so the wording
cannot drift back into promising scrollback that is not there.

Kills (each recorded in tests/falsificationRegistry.py):
  - the "Select text" title losing its full-screen sentence;
  - the terminal help losing its full-screen paragraph.
"""

import pytest

from tests.browser.testAProgramsCopyWaitsForTheResearchersClick import (
    _fnOpenAHostShell,
    _fnTypeInTheShell,
    _fnWaitForPaneText,
    _fsVisiblePaneText,
)


pytestmark = pytest.mark.browser

I_SEEDED_LINE_COUNT = 200
S_FULL_SCREEN_PROGRAM = (
    "printf '\\033[?1049h\\033[?1000h\\033[?1006h'; "
    "stty -echo; cat > /dev/null"
)


def _fdictMeasureTheVisiblePane(pageDashboard):
    """Return the mouse mode and how far the visible pane can scroll."""
    return pageDashboard.evaluate(
        """() => {
            const el = Array.from(document.querySelectorAll('.xterm'))
                .find((e) => e.style.display !== 'none');
            const elViewport = el.querySelector('.xterm-viewport');
            return {
                bMouseCaptured: el.classList.contains('enable-mouse-events'),
                iScrollableRows: Math.round(
                    (elViewport.scrollHeight - elViewport.clientHeight)
                    / (el.querySelector('.xterm-rows > div')
                        .getBoundingClientRect().height || 1)),
            };
        }""",
    )


@pytest.mark.falsification
def test_select_text_states_the_full_screen_limit(pageDashboard, serverHub):
    """With a full-screen program up, the title says only the screen exists.

    Kills: the "Select text" title losing its full-screen sentence.
    """
    _fnOpenAHostShell(pageDashboard, serverHub)
    _fnTypeInTheShell(
        pageDashboard, f"seq -f 'line%04g' 1 {I_SEEDED_LINE_COUNT}")
    _fnWaitForPaneText(
        pageDashboard, f"line{I_SEEDED_LINE_COUNT:04d}", "the scrollback")
    assert _fdictMeasureTheVisiblePane(pageDashboard)["iScrollableRows"] > 0
    _fnTypeInTheShell(pageDashboard, S_FULL_SCREEN_PROGRAM)
    pageDashboard.wait_for_timeout(1000)
    dictPane = _fdictMeasureTheVisiblePane(pageDashboard)
    assert dictPane["bMouseCaptured"], (
        "the fixture did not capture the mouse, so this is not the shape "
        f"of a full-screen agent: {dictPane}"
    )
    assert dictPane["iScrollableRows"] == 0, (
        "the pane still scrolls with a program on the alternate screen, "
        f"so the premise of the wording below is wrong: {dictPane}"
    )
    assert "line0001" not in _fsVisiblePaneText(pageDashboard)
    sTitle = pageDashboard.get_attribute(".terminal-pane-select", "title")
    assert "keeps no scrollback here" in sTitle, (
        "the Select text button promises scrollback a full-screen "
        f"program does not have: {sTitle!r}"
    )


@pytest.mark.falsification
def test_the_terminal_help_states_the_full_screen_limit(
    pageDashboard, serverHub,
):
    """The one terminal help says a full-screen program keeps no scrollback.

    Kills: the terminal help losing its full-screen paragraph.
    """
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_function(
        "() => window.VaibifyLegendPanel !== undefined", timeout=20000)
    sHelp = pageDashboard.evaluate(
        """() => {
            VaibifyLegendPanel.fnOpen();
            const el = Array.from(
                document.querySelectorAll('.proof-help-details'))
                .find((e) => (e.textContent || '')
                    .includes('Terminal usage'));
            return el ? el.textContent : '';
        }""",
    )
    assert "Terminal usage" in sHelp, "the terminal help did not render"
    assert "keeps no scrollback in the pane" in sHelp, (
        "the terminal help no longer says that a full-screen program "
        "leaves only the screen to select and copy"
    )
