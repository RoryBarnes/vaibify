"""A remote dashboard must say which machine it is driving.

Every sentence vaibify writes about "this machine" had an unambiguous
subject until a browser could be somewhere else. Through a tunnel the
files, the commands and the shells are all on the far machine, and a
researcher who believes otherwise reasons about blast radius wrongly.

Two affordances are hidden rather than left to fail, because both hand
the BROWSER an address and through a tunnel the browser's loopback is
the laptop: New Window spawns a hub on a port chosen after the tunnel
was built, so nothing forwards it, and the VS Code deep link carries a
container id that exists only on the remote daemon. A dead tab reads as
a vaibify bug; an absent button reads as a boundary.

Driven through the real applier in a real browser. A source scan can
prove the string exists; only this proves the researcher is shown it.
"""

import pytest


pytestmark = pytest.mark.browser

S_APP_READY = "typeof VaibifyApp !== 'undefined'"


def _fdictApplyLane(pageDashboard, bRemote, sHostname):
    """Apply a lane through the real entry point; report what shows."""
    return pageDashboard.evaluate(
        """([bRemote, sHostname]) => {
            VaibifyApp.fnApplyRemoteSession(bRemote, sHostname);
            const elBadge =
                document.getElementById('remoteSessionBadge');
            const fsVisible = (sId) => {
                const el = document.getElementById(sId);
                return el ? el.style.display !== 'none' : null;
            };
            return {
                sBadgeText: elBadge ? elBadge.textContent : null,
                bBadgeShown:
                    elBadge ? elBadge.style.display !== 'none' : null,
                bNewWindow: fsVisible('btnNewVaibifyWindow'),
                bNewWindowWorkflows:
                    fsVisible('btnNewVaibifyWindowWorkflows'),
                bReportedRemote: VaibifyApp.fbIsRemoteSession(),
            };
        }""",
        [bRemote, sHostname],
    )


def test_a_remote_session_names_the_machine_it_drives(
    pageDashboard, serverHub,
):
    """The badge appears and carries the hostname, not a euphemism."""
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_selector(".container-tile", timeout=15000)
    pageDashboard.wait_for_function(S_APP_READY, timeout=10000)
    dictShown = _fdictApplyLane(
        pageDashboard, True, "compute-server-3",
    )
    assert dictShown["bBadgeShown"], dictShown
    assert "compute-server-3" in dictShown["sBadgeText"], (
        "the badge must name the machine; 'somewhere else' is not "
        f"something a researcher can act on: {dictShown}"
    )
    assert dictShown["bReportedRemote"] is True
    assert pageDashboard.listPageErrors == []


def test_a_remote_session_hides_what_cannot_work_remotely(
    pageDashboard, serverHub,
):
    """Both loopback-address affordances go away."""
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_selector(".container-tile", timeout=15000)
    pageDashboard.wait_for_function(S_APP_READY, timeout=10000)
    dictShown = _fdictApplyLane(pageDashboard, True, "compute-server-3")
    for sKey in ("bNewWindow", "bNewWindowWorkflows"):
        assert dictShown[sKey] in (False, None), (
            f"{sKey} is still offered in a remote session, where it "
            f"would open an address on the laptop: {dictShown}"
        )


def test_a_local_session_shows_no_badge_and_keeps_its_buttons(
    pageDashboard, serverHub,
):
    """The symmetric half, and it is doing real work.

    Hiding the buttons unconditionally would satisfy the test above
    and remove a working feature from every local dashboard; showing
    the badge unconditionally would put a hostname in front of
    researchers for whom "here" was never ambiguous.
    """
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_selector(".container-tile", timeout=15000)
    pageDashboard.wait_for_function(S_APP_READY, timeout=10000)
    dictShown = _fdictApplyLane(pageDashboard, False, "")
    assert dictShown["bBadgeShown"] is False, dictShown
    assert dictShown["bReportedRemote"] is False
    for sKey in ("bNewWindow", "bNewWindowWorkflows"):
        assert dictShown[sKey] in (True, None), (
            f"{sKey} was hidden in a LOCAL session, where it works: "
            f"{dictShown}"
        )
