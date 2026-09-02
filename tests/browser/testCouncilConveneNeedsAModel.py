"""Convening without choosing a model refuses in words, not a number.

The model lists open UNCHOSEN by ruling (2026-08-28): pre-selecting one
would tell every researcher which model vaibify thinks is best, and
this product ranks no models. The cost is that the form can be
submitted incomplete — so the refusal is what has to carry the
meaning. It reached a researcher as "Could not convene: Request failed
(422)".

Only a real browser proves this: the refusal is composed from live
draft state, and a source-level contract test cannot show that the
form actually stops rather than posting.
"""

import pytest

from .testBrowserJourneys import _fnReleaseBrowserLaneOwnership  # noqa: F401
from .testCouncilPlanningJourney import (  # noqa: F401 — fixture wiring
    _fdictClaimAndActivate,
    _fnIsolateCouncilStore,
    _fnScriptedProviderSeam,
)

pytestmark = pytest.mark.browser


def testConveningWithoutAModelNamesTheAgentAndPostsNothing(
        pageDashboard, serverHub):
    """The form refuses, names the agent, and issues no request.

    Kills: submitting an incomplete participant (the server's 422 then
    reads as a bare status), and a refusal that says "invalid" without
    saying which agent.
    """
    _fdictClaimAndActivate(pageDashboard, serverHub)
    pageDashboard.click("#btnAgentCouncil")
    pageDashboard.wait_for_selector("#btnCouncilPlanChange", timeout=8000)
    pageDashboard.click("#btnCouncilPlanChange")
    pageDashboard.wait_for_selector("#btnCouncilConvene", timeout=8000)

    # A question, but the model lists left as they open — unchosen.
    pageDashboard.fill("#councilQuestion", "Replace the integrator")
    pageDashboard.evaluate("""
        () => {
            window.__vaibifyPosted = [];
            const fnOriginal = VaibifyApi.fdictPost;
            VaibifyApi.fdictPost = function (sUrl, dictBody) {
                window.__vaibifyPosted.push(sUrl);
                return fnOriginal(sUrl, dictBody);
            };
        }
    """)
    pageDashboard.click("#btnCouncilConvene")

    sError = pageDashboard.inner_text("#councilFormError")
    assert "Choose a model" in sError
    assert "Agent 1" in sError and "Agent 2" in sError
    assert "422" not in sError
    # Refused BEFORE the request: nothing was posted at all.
    listPosted = pageDashboard.evaluate("window.__vaibifyPosted")
    assert listPosted == [], listPosted

    assert pageDashboard.listPageErrors == []
    assert pageDashboard.listConsoleErrors == []
