"""The terminal help names the key THIS machine uses, not a list.

When a program in the pane has taken the mouse, one modifier forces
a selection through anyway, and which one it is depends on the
platform: the vendored xterm honours Option on a Mac and Shift on
Windows and Linux. The help used to print both and leave the
researcher to filter -- reported from a Mac, where the line read as
naming Shift, which does nothing there.

Naming the wrong key is worse than naming none, so the platform
answer has ONE home (``fsNameSelectionModifierKey`` in
scriptUtilities.js) shared with the code that injects the modifier.
This drives it from both sides of that answer by overriding
``navigator.platform`` -- the same frozen string xterm itself reads
-- before any page script runs.

Windows is asserted as well as macOS. It is the platform neither the
author nor the reporter is sitting at, so it is the one an untested
assumption would quietly get wrong.

Kills (confirmed -- mutation applied, this test run, the named
assertion observed to fail):
  - ``fsNameSelectionModifierKey`` returning "Shift" unconditionally
    -> "the Macintosh help named Shift".
"""

import pytest


pytestmark = [pytest.mark.browser]

# The frozen navigator.platform strings xterm's own macOS test reads.
S_MACINTOSH_PLATFORM = "MacIntel"
S_WINDOWS_PLATFORM = "Win32"


def _fsReadTerminalHelp(pageDashboard, serverHub, sPlatform):
    """Open the legend under a faked platform and return its text."""
    pageDashboard.add_init_script(
        "Object.defineProperty(navigator, 'platform', "
        f"{{get: () => {sPlatform!r}}});"
    )
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_function(
        "() => window.VaibifyLegendPanel !== undefined", timeout=20000)
    # Asserted rather than assumed: a page whose platform override did
    # not take would answer this test with the author's own machine.
    assert pageDashboard.evaluate("() => navigator.platform") == sPlatform
    return pageDashboard.evaluate(
        """() => {
            VaibifyLegendPanel.fnOpen();
            const listSections = Array.from(
                document.querySelectorAll('.proof-help-details'));
            const el = listSections.find(
                s => (s.textContent || '').includes('Terminal usage'));
            return el ? el.textContent : '';
        }""",
    )


@pytest.mark.falsification
def testTheHelpNamesOptionOnAMacintosh(pageDashboard, serverHub):
    sHelp = _fsReadTerminalHelp(
        pageDashboard, serverHub, S_MACINTOSH_PLATFORM)
    assert "Terminal usage" in sHelp, (
        "the terminal help section did not render, so nothing below "
        "says anything about which key it names"
    )
    assert "Option" in sHelp, (
        "the Macintosh help named no Option key, so a researcher on a "
        "Mac is not told the modifier their xterm actually honours"
    )
    assert "Shift while dragging" not in sHelp, (
        "the Macintosh help named Shift as the selection modifier, "
        "which does nothing on a Mac"
    )
    assert "Cmd+C" in sHelp, (
        "the Macintosh help did not name Cmd+C as the copy shortcut"
    )


def testTheHelpNamesShiftOnWindows(pageDashboard, serverHub):
    sHelp = _fsReadTerminalHelp(pageDashboard, serverHub, S_WINDOWS_PLATFORM)
    assert "Shift" in sHelp, (
        "the Windows help named no Shift key, so a researcher on "
        "Windows is not told the modifier their xterm honours"
    )
    assert "Option" not in sHelp, (
        "the Windows help named Option, which is a Macintosh key"
    )
    assert "Ctrl+Shift+C" in sHelp, (
        "the Windows help did not name Ctrl+Shift+C as the copy "
        "shortcut"
    )
