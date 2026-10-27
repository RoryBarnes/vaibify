"""Right-clicking the highlighted text copies it, after a refusal too.

The refused copy-on-select tells the researcher to right-click the
highlight to try again, so that advice has to be true: the right-click
must write exactly the highlighted text, and the highlight must still
be there to click. Whether the browser then ACCEPTS the write is its
own decision -- Playwright's Firefox cannot reproduce the real
browser's expiring permission (docs/developers.md) -- so the clipboard
is stood in for: it refuses the first write, then accepts.

Kills (recorded in tests/falsificationRegistry.py): a right-click on a
selection that no longer copies it.
"""

import pytest

from tests.browser.testARefusedCopyOnSelectSaysHowToRetry import (
    S_COPY_TOKEN,
    fdictSelectThePrintedToken,
)


pytestmark = pytest.mark.browser


@pytest.mark.falsification
def test_right_clicking_the_highlight_after_a_refusal_copies_it(
    pageDashboard, serverHub,
):
    """The retry the refusal toast names writes the highlighted text.

    Kills: a right-click on a selection that no longer copies it.
    """
    dictBox = fdictSelectThePrintedToken(pageDashboard, serverHub)
    pageDashboard.locator(".toast.error").first.wait_for(timeout=5000)
    sRefused = pageDashboard.evaluate("() => window.listClipboardWrites[0]")
    pageDashboard.evaluate("() => { window.bRefuseClipboardWrites = false; }")
    pageDashboard.mouse.click(
        dictBox["x"] + dictBox["width"] * 0.5,
        dictBox["y"] + dictBox["height"] * 0.5,
        button="right",
    )
    pageDashboard.wait_for_function(
        "() => window.listClipboardWrites.length > 1", timeout=5000)
    listWrites = pageDashboard.evaluate("() => window.listClipboardWrites")
    assert listWrites[-1] == sRefused and S_COPY_TOKEN in listWrites[-1], (
        "the right-click did not write the highlighted text the refused "
        f"copy had: {listWrites!r}"
    )
    pageDashboard.locator(
        ".toast", has_text="Copied to clipboard").first.wait_for(timeout=5000)
