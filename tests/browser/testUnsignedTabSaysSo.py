"""A tab with no credential says so, instead of spinning forever.

The dashboard signs in only by redeeming a one-time capability from
the URL fragment. Land on the bare address — a restored tab, a
bookmark, an address typed from the terminal — and there is no
capability to redeem.

What that used to look like: ``fnInitialize`` called
``fnFetchSessionToken``, got nothing, and carried on; the first
authenticated call threw on its 401; the rest of initialization never
ran; and what stayed on screen was the STATIC "Loading
environments..."
from ``index.html``. So a refused dashboard was pixel-identical to a
slow one, the Add button was dead because its binding never happened,
and the honest diagnosis took a maintainer and a researcher the better
part of an hour.

This lane can reproduce it exactly, because the bare address is
precisely what ``serverHub.sBaseUrl`` is — every other journey here
goes through ``fsBootstrapUrl()``.
"""

import pytest


pytestmark = pytest.mark.browser


def testAnUnsignedTabNamesTheProblemAndTheFix(pageDashboard, serverHub):
    """The refusal is visible, and it says what to do about it.

    Kills: continuing initialization with no credential, which leaves
    the static placeholder on screen and reports a refused dashboard
    as a loading one.
    """
    pageDashboard.goto(serverHub.sBaseUrl, wait_until="load")
    pageDashboard.wait_for_selector(
        "#listContainers", state="visible", timeout=10000,
    )
    pageDashboard.wait_for_function(
        """() => !document.getElementById('listContainers')
            .innerText.includes('Loading environments')""",
        timeout=10000,
    )
    sText = pageDashboard.text_content("#listContainers")
    assert "not signed in" in sText, sText
    assert "one-time link" in sText, (
        f"the message does not say why, so it is not actionable: {sText}"
    )
    assert "vaibify" in sText, (
        f"the message does not say what to run: {sText}"
    )


def testASignedTabStillLoadsItsContainers(pageDashboard, serverHub):
    """The other direction: the guard must not refuse a real session.

    Kills: treating every tab as unsigned — which would replace the
    dashboard with an error for everybody, and would pass a test that
    only asserted the refusal.
    """
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_selector(".container-tile", timeout=15000)
    sText = pageDashboard.text_content("#listContainers")
    assert "not signed in" not in sText, sText
    assert pageDashboard.listPageErrors == []
