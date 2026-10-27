"""A refused copy-on-select says what happened and how to try again.

A selection in the terminal is copied 200 ms after it settles, which is
after the release. A browser lets a page write the clipboard only for a
few seconds after the researcher's press, so a long drag can outlast
that permission and the copy is refused (measured once in real Firefox;
docs/developers.md has the numbers). The toast now says the copy was
refused, that a long selection can do that, and that right-clicking the
highlight -- a fresh press -- tries again. It never claims that a long
drag WAS the cause: one refusal does not establish why it happened.

Playwright's own Firefox build keeps a press's permission alive far
longer than the release browser (measured: writes accepted after a
nine-second hold), so no lane can produce the real refusal. The
refusal is therefore stood in for by a ``writeText`` that rejects,
which runs on every engine; what is under test is the page's answer
to a refusal, not the browser's reason for one.

Kills (recorded in tests/falsificationRegistry.py): the copy-on-select
path reporting the old, remedy-less sentence.
"""

import pytest

from tests.browser.testAProgramsCopyWaitsForTheResearchersClick import (
    _fnOpenAHostShell,
    _fnTypeInTheShell,
    _fnWaitForPaneText,
)


pytestmark = pytest.mark.browser

S_REFUSAL_SENTENCE = (
    "The browser refused the copy. Long selections can outlast its "
    "clipboard permission. Right-click the highlighted text to try again."
)
S_COPY_TOKEN = "refusedselection0042token"

# A clipboard that refuses until told otherwise, recording every write
# it was asked for. The textarea fallback refuses too, so a refusal is
# a refusal whichever road the page takes.
S_REFUSING_CLIPBOARD = """
window.listClipboardWrites = [];
window.bRefuseClipboardWrites = true;
(function () {
    const fnWrite = (sText) => {
        window.listClipboardWrites.push(String(sText));
        return window.bRefuseClipboardWrites
            ? Promise.reject(new DOMException('refused', 'NotAllowedError'))
            : Promise.resolve();
    };
    if (navigator.clipboard) {
        Object.defineProperty(navigator.clipboard, 'writeText', {
            configurable: true, value: fnWrite,
        });
    } else {
        Object.defineProperty(navigator, 'clipboard', {
            configurable: true, value: {writeText: fnWrite},
        });
    }
    document.execCommand = () => !window.bRefuseClipboardWrites;
})();
"""


def fdictSelectThePrintedToken(pageDashboard, serverHub):
    """Open a shell, print the token, and drag across the whole pane."""
    _fnOpenAHostShell(pageDashboard, serverHub, S_REFUSING_CLIPBOARD)
    _fnTypeInTheShell(pageDashboard, f"echo {S_COPY_TOKEN}")
    _fnWaitForPaneText(pageDashboard, S_COPY_TOKEN, "the text to copy")
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
    pageDashboard.wait_for_function(
        "() => window.listClipboardWrites.length > 0", timeout=5000)
    return dictBox


@pytest.mark.falsification
def test_a_refused_copy_on_select_says_how_to_try_again(
    pageDashboard, serverHub,
):
    """The refusal toast names the retry and does not claim a cause.

    Kills: the copy-on-select path reporting the old sentence.
    """
    fdictSelectThePrintedToken(pageDashboard, serverHub)
    assert S_COPY_TOKEN in pageDashboard.evaluate(
        "() => window.listClipboardWrites[0]"), (
        "the copy-on-select write did not carry the selected text"
    )
    elToast = pageDashboard.locator(".toast.error").first
    elToast.wait_for(timeout=5000)
    assert S_REFUSAL_SENTENCE in elToast.text_content(), (
        "a refused copy-on-select did not say how to retry: "
        f"{elToast.text_content()!r}"
    )
