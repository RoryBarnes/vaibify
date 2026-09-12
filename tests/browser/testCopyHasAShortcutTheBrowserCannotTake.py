"""Copy must not depend on which machine the researcher sat down at.

Ctrl+Shift+C is the conventional terminal copy on Linux and is also
Firefox's Inspector shortcut. Measured on two Linux distributions
running the SAME Firefox against the SAME page: on one the pane
copies, on the other the developer tools open over the researcher's
work and the keystroke never reaches the handler at all. Nothing in
vaibify can arbitrate that -- a reserved shortcut is decided above the
page -- so the pane also accepts Ctrl+Insert, which no browser claims.

The second property is the one that made the same key do two
different things on one machine. The handler used to return early on
an empty selection WITHOUT calling preventDefault, so Ctrl+Shift+C
copied when xterm believed it held a selection and fell through to
the browser when it did not. "Believed" is narrower than "looks
highlighted": the selection can be cleared by a click the researcher
reads as harmless. A terminal that does nothing is the right answer
to "copy with nothing selected"; opening a developer tool is not.

The clipboard is deliberately poisoned with a sentinel before each
copy. Copy-on-select has ALREADY put the selection on the clipboard
by then, so without that step every assertion here would pass against
a handler that does nothing at all -- the shortcut would be untested
and would look proven.

The two properties are driven differently on purpose. The copy is a
real keypress, because that is the thing a researcher performs. The
fall-through is a constructed event, because `defaultPrevented` is
the property under test and no synthesized keypress reports it back.

Kills (confirmed -- the mutation was applied, this test run, and the
named assertion observed to fail):
  - `bInsertCopy` removed from the accepted combinations, i.e. the
    fallback gone -> the clipboard still holds the sentinel.
"""

import time

import pytest

from tests.browser.testResizeAndCopyHoldTogether import (
    _fnOpenAHostShell,
    _fnTypeInTheShell,
    _fnWaitForPaneText,
)


pytestmark = pytest.mark.browser

S_SENTINEL = "SENTINEL-CLIPBOARD-NOT-OVERWRITTEN"
S_COPY_TOKEN = "copyme0042token"
F_SHELL_DEADLINE_SECONDS = 45.0
I_COPY_ON_SELECT_SETTLE_MILLISECONDS = 700


def _fnPoisonTheClipboard(pageDashboard):
    """Put a sentinel on the clipboard so a no-op cannot pass.

    Copy-on-select has already placed the selection there, so an
    assertion made without this step is satisfied by a shortcut that
    does nothing.
    """
    pageDashboard.evaluate(
        "(sText) => navigator.clipboard.writeText(sText)", S_SENTINEL,
    )
    pageDashboard.wait_for_timeout(200)


def _fsReadClipboard(pageDashboard):
    return pageDashboard.evaluate("() => navigator.clipboard.readText()")


def _fnSelectSomeOutput(pageDashboard):
    """Drag a selection across the pane and let copy-on-select settle."""
    dictBox = pageDashboard.locator(".xterm-screen").bounding_box()
    pageDashboard.mouse.move(dictBox["x"] + 2, dictBox["y"] + 4)
    pageDashboard.mouse.down()
    pageDashboard.mouse.move(
        dictBox["x"] + dictBox["width"] * 0.6,
        dictBox["y"] + dictBox["height"] * 0.5,
    )
    pageDashboard.mouse.move(
        dictBox["x"] + dictBox["width"] - 4,
        dictBox["y"] + dictBox["height"] - 6,
    )
    pageDashboard.mouse.up()
    pageDashboard.wait_for_timeout(I_COPY_ON_SELECT_SETTLE_MILLISECONDS)


def _fbCopyKeyIsSwallowed(pageDashboard, sKey, bShift, bControl):
    """Dispatch a copy combination and report whether it was consumed.

    Constructed rather than pressed because `defaultPrevented` is the
    property: a keystroke the page does not consume is one the browser
    acts on, and no synthesized press reports that back.
    """
    return pageDashboard.evaluate(
        """(dictKey) => {
            const elTextarea = document.querySelector('.xterm-helper-textarea')
                || document.querySelector('.xterm textarea');
            const eventKey = new KeyboardEvent('keydown', {
                key: dictKey.sKey,
                ctrlKey: dictKey.bControl,
                shiftKey: dictKey.bShift,
                bubbles: true,
                cancelable: true,
            });
            elTextarea.dispatchEvent(eventKey);
            return eventKey.defaultPrevented;
        }""",
        {"sKey": sKey, "bShift": bShift, "bControl": bControl},
    )


@pytest.mark.falsification
def test_copy_has_a_shortcut_the_browser_cannot_take(
    pageDashboard, serverHub,
):
    """Ctrl+Insert copies, and a copy key is never handed to the browser.

    Kills: `bInsertCopy` removed from the accepted combinations, i.e.
    the unreserved fallback gone -> the clipboard still holds the
    sentinel after Ctrl+Insert.
    """
    pageDashboard.context.grant_permissions(
        ["clipboard-read", "clipboard-write"],
    )
    _fnOpenAHostShell(pageDashboard, serverHub)
    _fnTypeInTheShell(pageDashboard, f"echo {S_COPY_TOKEN}")
    _fnWaitForPaneText(
        pageDashboard, S_COPY_TOKEN, "the text to copy",
        F_SHELL_DEADLINE_SECONDS,
    )

    # --- Ctrl+Insert copies the selection -------------------------
    _fnSelectSomeOutput(pageDashboard)
    _fnPoisonTheClipboard(pageDashboard)
    assert _fsReadClipboard(pageDashboard) == S_SENTINEL, (
        "the clipboard was not poisoned, so the assertion below would "
        "be satisfied by copy-on-select rather than by the shortcut"
    )

    pageDashboard.keyboard.press("Control+Insert")
    time.sleep(0.6)
    sClipboard = _fsReadClipboard(pageDashboard)
    assert sClipboard != S_SENTINEL, (
        "Ctrl+Insert did not copy. It is the fallback that exists "
        "precisely because Ctrl+Shift+C is reserved by the browser on "
        "some machines and not others, so without it a researcher on "
        "the wrong machine has no copy shortcut at all"
    )
    assert S_COPY_TOKEN in sClipboard, (
        "Ctrl+Insert put something on the clipboard that is not the "
        f"selected output: {sClipboard[:120]!r}"
    )

    # --- a copy key is never handed to the browser ----------------
    # Cleared by clicking, which is how a researcher loses a selection
    # without meaning to -- the state that made this key ambiguous.
    dictBox = pageDashboard.locator(".xterm-screen").bounding_box()
    pageDashboard.mouse.click(dictBox["x"] + 20, dictBox["y"] + 4)
    pageDashboard.wait_for_timeout(300)
    _fnPoisonTheClipboard(pageDashboard)

    for sKey, bShift in (("C", True), ("Insert", False)):
        assert _fbCopyKeyIsSwallowed(
            pageDashboard, sKey, bShift, True,
        ), (
            f"a copy combination ({sKey}) with nothing selected was "
            "left for the browser to act on. That is how the SAME key "
            "comes to copy on one press and open a developer tool on "
            "the next, from state the researcher cannot see"
        )
    assert _fsReadClipboard(pageDashboard) == S_SENTINEL, (
        "a copy shortcut with nothing selected overwrote the "
        "clipboard; doing nothing is the right answer"
    )
