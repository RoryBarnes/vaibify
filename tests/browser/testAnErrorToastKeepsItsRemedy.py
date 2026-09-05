"""A long error toast must keep the half that says what to do.

A researcher whose Docker socket was missing was shown this, and
nothing more (researcher-reported, 2026-09-05):

    Docker support is not available. No Docker socket exists at the
    endpoint vaibify resolved. Either the daemon is not running, or it
    listens on a socket your shell reaches and vaibify did not (a
    rootles...

The cut is at exactly 200 characters -- ``fsSanitizeErrorForUser``
substring'd every message longer than that and appended an ellipsis.
The 264 characters it removed held the remedy (``Try: docker context
ls``) and the verbatim cause. A diagnosis without its remedy is the
failure mode the whole diagnosis catalog exists to prevent, arriving
one layer further out.

``.toast.error`` then clipped what survived a second time, at 120px
with ``overflow: hidden`` -- no scrollbar, no indication anything was
missing.

Both halves are asserted through the real toast renderer in a real
browser, because both are invisible to the Python suite: one is a
string operation in a file it never executes, the other is a CSS
rule it never applies. The overflow assertion reads the COMPUTED
style off a live element rather than the class name, which would
pass against a stylesheet containing no such rule at all.
"""

import pytest


pytestmark = pytest.mark.browser


S_DOCKER_DETAIL = (
    "Docker support is not available. No Docker socket exists at the "
    "endpoint vaibify resolved. Either the daemon is not running, or "
    "it listens on a socket your shell reaches and vaibify did not (a "
    "rootless or Docker Desktop context). Compare the endpoint below "
    "with the one vaibify used; if they agree, start the daemon. Try: "
    "docker context ls (cause: Error while fetching server API "
    "version: ('Connection aborted.', FileNotFoundError(2, 'No such "
    "file or directory')))"
)


def _felShowErrorToast(pageDashboard, sMessage):
    """Render one error toast through the real renderer, return its text."""
    pageDashboard.evaluate(
        """(sText) => {
            document.getElementById("toastContainer").innerHTML = "";
            VaibifyApp.fnShowToast(sText, "error");
        }""",
        sMessage,
    )
    pageDashboard.wait_for_selector(".toast.error", timeout=5000)


def test_a_long_error_toast_still_carries_its_remedy(
    pageDashboard, serverHub,
):
    """The tail of the message must reach the DOM, not an ellipsis."""
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_selector(".container-tile", timeout=10000)
    _felShowErrorToast(pageDashboard, S_DOCKER_DETAIL)
    sToast = pageDashboard.eval_on_selector(
        ".toast.error", "el => el.textContent",
    )
    assert "docker context ls" in sToast, (
        "the remedy was cut off the end of the message the researcher "
        f"is shown: {sToast}"
    )
    assert "FileNotFoundError" in sToast, (
        f"the verbatim cause was cut off: {sToast}"
    )
    assert "rootles..." not in sToast, (
        f"the message was truncated mid-word at 200 characters: {sToast}"
    )


def test_a_long_error_toast_is_scrollable_rather_than_clipped(
    pageDashboard, serverHub,
):
    """Text in the DOM that no scrollbar reaches is still hidden.

    The first assertion above passes against ``overflow: hidden`` --
    the text is present, it simply cannot be read. This one reads the
    computed style off the live element, so it fails on the clip that
    shipped and on any later stylesheet that reintroduces one.
    """
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_selector(".container-tile", timeout=10000)
    _felShowErrorToast(pageDashboard, S_DOCKER_DETAIL)
    sOverflowY = pageDashboard.eval_on_selector(
        ".toast.error", "el => getComputedStyle(el).overflowY",
    )
    assert sOverflowY in ("auto", "scroll"), (
        "a bounded error toast must scroll; computed overflow-y was "
        f"{sOverflowY!r}"
    )


def test_the_sanitizer_returns_a_long_message_unchanged(
    pageDashboard, serverHub,
):
    """The cut is gone at its source, not merely papered over by CSS.

    Asserted against the real function in the loaded page: a scrollable
    box cannot restore characters a substring already discarded.
    """
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_selector(".container-tile", timeout=10000)
    sReturned = pageDashboard.evaluate(
        "(sText) => VaibifyUtilities.fsSanitizeErrorForUser(sText)",
        S_DOCKER_DETAIL,
    )
    assert sReturned == S_DOCKER_DETAIL, (
        "fsSanitizeErrorForUser altered a message it should pass "
        f"through: {sReturned}"
    )
