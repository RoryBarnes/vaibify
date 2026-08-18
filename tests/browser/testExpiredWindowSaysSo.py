"""An expired hold window must not be reported as a server restart.

Three different things can end a dashboard session, and they have
three different recoveries: the server went away, the server refused
this tab, or the server held the session for as long as it promised
and nobody came back. The third was reported as the first — "Vaibify
server has been restarted (session expired)" — while the server was
healthy and the researcher's run was still going.

The message is the whole deliverable here, so it is asserted in a real
browser through the real monitor, the real message builder, and the
real toast renderer. A Python source scan can prove the string is in
the file; only this can prove the researcher is shown it.
"""

import pytest


pytestmark = pytest.mark.browser


def _fsSurfacedToastText(pageDashboard, dictLossEvent):
    """Drive a WebSocket loss through the real monitor, return the toast."""
    pageDashboard.evaluate(
        """(dictEvent) => {
            VaibifyConnectionMonitor.fnReset();
            VaibifyConnectionMonitor.fnReportWsLoss(dictEvent);
        }""",
        dictLossEvent,
    )
    pageDashboard.wait_for_selector(".toast", timeout=5000)
    return pageDashboard.eval_on_selector(".toast", "el => el.textContent")


def test_an_exhausted_window_says_the_session_expired_on_schedule(
    pageDashboard, serverHub,
):
    """The honest message names the window and keeps the run alive."""
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_selector(".container-tile", timeout=10000)
    sToast = _fsSurfacedToastText(pageDashboard, {
        "iCode": 1006,
        "bWindowExhausted": True,
        "fWindowSeconds": 900,
    })
    assert "15 minutes" in sToast, (
        f"the window must be named in the unit lived through: {sToast}"
    )
    assert "still going on the server" in sToast, (
        "the researcher must be told the run survived — that is the "
        f"fact the server-restarted message denied: {sToast}"
    )
    assert "restarted" not in sToast.lower(), (
        f"a healthy server must not be described as restarted: {sToast}"
    )


def test_an_ordinary_drop_is_still_reported_as_a_drop(pageDashboard, serverHub):
    """The symmetric half: the new branch must not swallow the old one.

    Asserting only the expired-window text would pass just as well if
    every loss were relabelled, which would trade one wrong message
    for another.
    """
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_selector(".container-tile", timeout=10000)
    sToast = _fsSurfacedToastText(pageDashboard, {
        "iCode": 1006,
        "bWindowExhausted": False,
    })
    assert "Cannot reach Vaibify server" in sToast, sToast
    assert "still going on the server" not in sToast, (
        f"an unreachable server must not claim the run is fine: {sToast}"
    )
