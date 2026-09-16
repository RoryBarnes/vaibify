"""A copy the researcher earned survives the selection being cleared.

Measured in a real session, against a real agent running in the pane:
the selection held for 2.7 seconds and ~170 repaints while the
researcher dragged, and was destroyed 48 MILLISECONDS after they
released the mouse. WHAT destroys it is not established -- see below;
the measurement is the release-to-clear interval, not the culprit.
Copy-on-select debounces by 200ms, so
the clearing arrived first, and it killed the copy twice over. The
clearing `onSelectionChange` cancelled the pending timer on its way
through, and the flush that would have run re-read `hasSelection()`
and found nothing. `writeText` was never called at all. The researcher
saw their selection highlight, released, and had an empty clipboard.

Repaint alone does NOT do this -- that was measured too, in both
Chromium and Firefox, with a program repainting ten times a second:
the selection survives every frame. It is the release specifically.
So the pane cannot rely on the selection still existing a moment after
the researcher finished making it, and the debounce must delay the
WRITE rather than the READ.

The clipboard is poisoned with a sentinel BEFORE the drag, so a
handler that copies nothing cannot pass by leaving an earlier copy in
place.

What this test canNOT do is reproduce the researcher's clearer. Four
synthetic attempts failed: a repainting program, a program that reads
the mouse-release byte and redraws, a full ESC[2J erase driven by that
byte, and a plain click. In every one the selection survived. Nor can
the DOM stand in for the answer -- the `.xterm-selection` rectangles
report what is being PAINTED, not whether xterm still holds a
selection, so a count of them is not evidence either way.

So the proof that this scenario reproduces the defect is not an
assertion inside the test. It is the registered mutation in
`tests/falsificationRegistry.py`: restore the guard order and this
test must FAIL. A scenario that cannot kill that mutation is not
exercising the defect, and `tools/reconfirmFalsification.py` is what
says so. The timing window is still asserted, because a clearing
gesture that lands after the debounce would let the copy happen for
the ordinary reason.

Kills (confirmed -- the mutation was applied, this test run, and the
named assertion observed to fail):
  - the empty-selection guard moved back below the timer cancellation,
    i.e. a cleared selection cancels the scheduled copy again -> the
    clipboard still holds the sentinel.
"""

import time

import pytest

from tests.browser.testResizeAndCopyHoldTogether import (
    _fnOpenAHostShell,
    _fnTypeInTheShell,
    _fnWaitForPaneText,
)


pytestmark = [
    pytest.mark.browser,
    # Chromium is the only engine whose Playwright build grants
    # clipboard permissions; see pytest.ini for what that costs.
    pytest.mark.clipboardPermissions,
]

S_SENTINEL = "SENTINEL-CLIPBOARD-NOT-OVERWRITTEN"
S_COPY_TOKEN = "clearedselection0042token"
F_SHELL_DEADLINE_SECONDS = 45.0
I_COPY_ON_SELECT_DELAY_MS = 200
I_SETTLE_MILLISECONDS = 800


def _fsReadClipboard(pageDashboard):
    return pageDashboard.evaluate("() => navigator.clipboard.readText()")


def _fnDragAcrossThePane(pageDashboard):
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
    return dictBox


@pytest.mark.falsification
def test_a_copy_survives_the_selection_being_cleared_after_release(
    pageDashboard, serverHub,
):
    """A selection cleared inside the debounce is still copied.

    Kills: the empty-selection guard moved back below the timer
    cancellation -> the clipboard still holds the sentinel.
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

    pageDashboard.evaluate(
        "(sText) => navigator.clipboard.writeText(sText)", S_SENTINEL,
    )
    pageDashboard.wait_for_timeout(200)
    assert _fsReadClipboard(pageDashboard) == S_SENTINEL, (
        "the clipboard was not poisoned, so a handler that copies "
        "nothing would pass on an earlier copy left in place"
    )

    dictBox = _fnDragAcrossThePane(pageDashboard)
    fReleasedAt = time.monotonic()

    # The program's own response to the release, stood in for by a
    # click: whatever clears it, the pane must not lose the copy.
    pageDashboard.mouse.click(
        dictBox["x"] + dictBox["width"] * 0.5,
        dictBox["y"] + dictBox["height"] * 0.5,
    )
    fElapsedMilliseconds = (time.monotonic() - fReleasedAt) * 1000.0
    assert fElapsedMilliseconds < I_COPY_ON_SELECT_DELAY_MS, (
        f"the clearing gesture landed {fElapsedMilliseconds:.0f}ms "
        f"after release, outside the {I_COPY_ON_SELECT_DELAY_MS}ms "
        "debounce. The copy would then have landed for the ordinary "
        "reason and this test would pass against the defect it exists "
        "to catch."
    )

    pageDashboard.wait_for_timeout(I_SETTLE_MILLISECONDS)
    sClipboard = _fsReadClipboard(pageDashboard)
    assert sClipboard != S_SENTINEL, (
        "the selection was cleared inside the debounce and the copy "
        "was silently dropped -- the researcher selected text, "
        "released, and got an empty clipboard"
    )
    assert S_COPY_TOKEN in sClipboard, (
        f"the clipboard holds {sClipboard[:120]!r}, which does not "
        f"contain the selected {S_COPY_TOKEN!r}"
    )
