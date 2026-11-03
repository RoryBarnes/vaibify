"""The Zenodo token banner renders the server's answer and offers the decision.

A token moved out of the container is pending until the researcher
confirms it, and the dashboard is where they do that. The banner shows
the slot's state, the host-side validation outcome and the deposit
titles Zenodo reported, with Confirm and Decline buttons; with nothing
awaiting and no rotation advice it is absent; and it never shows a
token value, because the server never sends one. Driven through the
module's real render path over the real page.

WHAT THIS DOES NOT COVER: which slots the server reports and what a
decision does to the keyring (``tests/testZenodoTokenMigration.py``,
``tests/testZenodoTokenRoutes.py``).
"""

import json

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser

_DICT_PENDING = {
    "bApplicable": True, "bAwaitingDecision": True, "bRotationAdvised": True,
    "dictActive": {"sandbox": False, "production": False},
    "dictTokenPages": {
        "sandbox": "https://sandbox.zenodo.org/account/settings/applications/",
        "production": "https://zenodo.org/account/settings/applications/",
    },
    "listSlots": [{
        "sSlot": "zenodo_token_sandbox", "sState": "removed", "sReason": "",
        "sInstance": "sandbox", "sProposedInstance": "sandbox",
        "bPending": True, "bAwaitingDecision": True, "sConflictChoice": "",
        "dictValidation": {"sOutcome": "accepted", "sDetail": "", "sAt": "now"},
        "listDepositTitles": ["A deposit the server fetched"],
    }],
}

_S_RENDER = """(dictDescribed) => {
    VaibifyZenodoTokens.fnRender(dictDescribed);
}"""

_S_BANNER_STATE = """() => {
    var elBanner = document.getElementById("zenodoTokenBanner");
    return {
        sDisplay: elBanner.style.display,
        sText: elBanner.textContent,
        listDecisions: Array.from(
            elBanner.querySelectorAll("button[data-decision]")).map(
            function (elButton) { return elButton.dataset.decision; }),
        listLinks: Array.from(elBanner.querySelectorAll("a")).map(
            function (elLink) { return elLink.href; }),
    };
}"""


@pytest.mark.falsification
def testAPendingTokenIsShownWithItsEvidenceAndTheDecisionButtons(
    pageDashboard, serverHub,
):
    """Kills: rendering the row without its Confirm and Decline buttons."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    pageDashboard.evaluate(_S_RENDER, _DICT_PENDING)
    dictShown = pageDashboard.evaluate(_S_BANNER_STATE)
    assert dictShown["sDisplay"] == "block"
    assert "await your decision" in dictShown["sText"]
    assert "accepted by sandbox" in dictShown["sText"]
    assert "A deposit the server fetched" in dictShown["sText"]
    assert dictShown["listDecisions"] == ["confirm", "decline"]
    assert "Regenerate your Zenodo tokens" in dictShown["sText"]
    assert dictShown["listLinks"] == [
        "https://zenodo.org/account/settings/applications/",
        "https://sandbox.zenodo.org/account/settings/applications/",
    ]


def testAConflictOffersTheTwoResolutionsAndNothingAwaitingHidesTheBanner(
    pageDashboard, serverHub,
):
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    dictConflict = dict(_DICT_PENDING, bRotationAdvised=False, listSlots=[dict(
        _DICT_PENDING["listSlots"][0], sState="conflict", bPending=False,
        sReason="the host already holds a different active sandbox token",
        dictValidation={}, listDepositTitles=[],
    )])
    pageDashboard.evaluate(_S_RENDER, dictConflict)
    dictShown = pageDashboard.evaluate(_S_BANNER_STATE)
    assert dictShown["sDisplay"] == "block"
    assert dictShown["listDecisions"] == ["keep-host", "stage-pending"]
    assert "differs from the one stored on this computer" in dictShown["sText"]
    pageDashboard.evaluate(_S_RENDER, {
        "bApplicable": True, "bAwaitingDecision": False,
        "bRotationAdvised": False, "listSlots": [], "dictActive": {},
    })
    assert pageDashboard.evaluate(_S_BANNER_STATE)["sDisplay"] == "none"
    pageDashboard.evaluate(_S_RENDER, _DICT_PENDING)
    pageDashboard.click("#btnDismissZenodoTokens")
    assert pageDashboard.evaluate(_S_BANNER_STATE)["sDisplay"] == "none"


# ---------------------------------------------------------------------------
# The rotation notice is answered, and goes only when the server says so
# ---------------------------------------------------------------------------

_S_ROTATION_STATE = """() => {
    var elBanner = document.getElementById("zenodoTokenBanner");
    return {
        sDisplay: elBanner.style.display,
        listAnswers: Array.from(
            elBanner.querySelectorAll("button[data-rotation]")).map(
            function (elButton) { return elButton.dataset.rotation; }),
        listLinkTexts: Array.from(elBanner.querySelectorAll("a")).map(
            function (elLink) { return elLink.textContent; }),
    };
}"""

_DICT_ROTATION_ONLY = dict(
    _DICT_PENDING, bAwaitingDecision=False, listSlots=[],
    dictActive={"sandbox": True, "production": False},
)

_DICT_ANSWERED = dict(
    _DICT_ROTATION_ONLY, bRotationAdvised=False, sRotationDecision="kept",
    sRotationDecidedAt="2026-11-02T12:00:00+00:00",
)


def _flistHoldRotationAnswers(pageDashboard):
    """Serve the notice to a refresh, and hold every answer the page posts."""
    pageDashboard.route(
        "**/api/zenodo-tokens/*",
        lambda routeIntercepted: routeIntercepted.fulfill(
            status=200, content_type="application/json",
            body=json.dumps(_DICT_ROTATION_ONLY),
        ),
    )
    listHeld = []
    pageDashboard.route(
        "**/api/zenodo-tokens/*/rotation-decision",
        lambda routeIntercepted: listHeld.append(routeIntercepted),
    )
    pageDashboard.evaluate(
        "() => VaibifyZenodoTokens.fnRefresh('rotationNoticeContainer')")
    pageDashboard.wait_for_selector(
        "#zenodoTokenBanner button[data-rotation]", timeout=10000)
    return listHeld


def _froutePosted(pageDashboard, listHeld):
    for _ in range(50):
        if listHeld:
            return listHeld[0]
        pageDashboard.wait_for_timeout(100)
    raise AssertionError("the answer was never posted")


def testTheRotationNoticeOffersTwoAnswersInTheDashboardsWords(
    pageDashboard, serverHub,
):
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    pageDashboard.evaluate(_S_RENDER, _DICT_ROTATION_ONLY)
    dictShown = pageDashboard.evaluate(_S_ROTATION_STATE)
    assert dictShown["sDisplay"] == "block"
    assert dictShown["listAnswers"] == ["regenerated", "kept"]
    assert dictShown["listLinkTexts"] == ["zenodo.org", "the Zenodo sandbox"]
    pageDashboard.evaluate(_S_RENDER, _DICT_ANSWERED)
    assert pageDashboard.evaluate(_S_ROTATION_STATE)["sDisplay"] == "none"


def testAnAnswerIsPostedAndTheNoticeGoesOnlyOnTheReply(
    pageDashboard, serverHub,
):
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    listHeld = _flistHoldRotationAnswers(pageDashboard)
    pageDashboard.click('#zenodoTokenBanner button[data-rotation="kept"]')
    routePosted = _froutePosted(pageDashboard, listHeld)
    assert json.loads(routePosted.request.post_data) == {"sDecision": "kept"}
    assert pageDashboard.evaluate(_S_ROTATION_STATE)["sDisplay"] == "block"
    routePosted.fulfill(
        status=200, content_type="application/json",
        body=json.dumps(_DICT_ANSWERED),
    )
    pageDashboard.wait_for_function(
        "() => document.getElementById('zenodoTokenBanner').style.display"
        " === 'none'", timeout=10000)


def testARefusedAnswerLeavesTheNoticeUpAndSaysWhy(pageDashboard, serverHub):
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    listHeld = _flistHoldRotationAnswers(pageDashboard)
    pageDashboard.click(
        '#zenodoTokenBanner button[data-rotation="regenerated"]')
    _froutePosted(pageDashboard, listHeld).fulfill(
        status=409, content_type="application/json",
        body=json.dumps({"detail": "The rotation answer could not be recorded."}),
    )
    pageDashboard.wait_for_selector(".toast.error", timeout=10000)
    assert "could not be recorded" in pageDashboard.locator(
        ".toast.error").first.inner_text()
    dictShown = pageDashboard.evaluate(_S_ROTATION_STATE)
    assert dictShown["sDisplay"] == "block"
    assert pageDashboard.locator(
        '#zenodoTokenBanner button[data-rotation="kept"]').is_enabled()


def testRegeneratedAsksForEachActiveTokenAgain(pageDashboard, serverHub):
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    listHeld = _flistHoldRotationAnswers(pageDashboard)
    pageDashboard.click(
        '#zenodoTokenBanner button[data-rotation="regenerated"]')
    _froutePosted(pageDashboard, listHeld).fulfill(
        status=200, content_type="application/json",
        body=json.dumps(dict(_DICT_ANSWERED, sRotationDecision="regenerated")),
    )
    pageDashboard.wait_for_selector(
        "#modalConnectionSetup", state="visible", timeout=10000)
    assert pageDashboard.locator(
        'input[name="zenodoInstance"][value="sandbox"]').is_checked()
