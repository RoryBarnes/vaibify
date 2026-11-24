"""Every notice banner says why it is showing, and the x hides only what was read.

A researcher met a Network notice they could neither dismiss nor ask
about, beside a remote-binding notice with no x either
(researcher-reported, 2026-11-24). Every banner above the steps now
carries a "?" that opens its own explanation, the two that had no x
have one, and that x holds only while the server's answer and the view
are the ones it hid: a different answer, or the next open, shows the
banner again. View > Network Status and View > Remote Bindings show
those rows on demand, asked of the server at the click, even when all
is well.

Driven through each module's own render path over the real page, with
the routes the chips ask intercepted.

WHAT THIS DOES NOT COVER: what the server decides for any banner (the
readiness, binding and token routes have their own tests), the help
text's wording (drafts for the researcher to rewrite), and a real
proxy's refusals.
"""

import json
import re

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser

S_SURFACE_NETWORK = """(dictNetwork) => {
    VaibifyContainerManager.fnSurfaceReadinessOutcome(
        {sStatus: "ready", bReady: true, dictNetwork: dictNetwork},
        VaibifyApp.fsGetContainerId());
}"""
S_DISPLAY_OF = "(sId) => document.getElementById(sId).style.display"
S_RENDER_NOTICES = (
    "(dictDescribed) => VaibifyRemoteBindings.fnRenderNotices(dictDescribed)")
_RE_READY = re.compile(r".*/api/containers/[^/]+/ready$")
_RE_BINDINGS_READ = re.compile(r".*/api/bindings/[^/?]+(\?.*)?$")

LIST_ONE_REFUSAL = [{"sHost": "data.example.org", "iPort": 443, "iCount": 3,
                     "sReason": "not in the allowlist"}]
LIST_TWO_REFUSALS = LIST_ONE_REFUSAL + [
    {"sHost": "mirror.example.net", "iPort": 443, "iCount": 1,
     "sReason": "not in the allowlist"}]


def fdictNetwork(sMode="allowlist", sProxyState="running", listRefused=None):
    return {
        "sMode": sMode, "sName": "", "bSealed": True,
        "sProxyState": sProxyState, "listRefused": listRefused or [],
        "sContainerState": "sealed", "sEvidence": "", "bFileAgrees": True,
    }


def fdictBindingEntry(sNotice, sState="differs"):
    return {
        "sService": "zenodo", "sRecordKind": "project", "sInstance": "sandbox",
        "sLabel": "this workflow's project deposits on the Zenodo sandbox",
        "bApplies": True, "dictProjectTarget": None, "dictConfirmed": None,
        "sState": sState, "sNotice": sNotice,
        "dictReview": {"sService": "zenodo", "sRecordKind": "project",
                       "sWorkflowKey": "projects/study.json::study",
                       "dictTarget": {"sInstance": "sandbox"}},
    }


def fdictDescribed(listEntries):
    return {
        "sProjectUuid": "f" * 32, "sWorkflowKey": "projects/study.json::study",
        "sWorkflowName": "study", "dictRecordState": {"bValid": True},
        "listEntries": listEntries,
    }


S_NOTICE_ONE = "project.json names deposition 11, but you confirmed 10."
S_NOTICE_TWO = "project.json names deposition 12, but you confirmed 10."


def fnAnswerJson(routeIntercepted, iStatus, dictBody):
    routeIntercepted.fulfill(
        status=iStatus, content_type="application/json",
        body=json.dumps(dictBody))


def fsDisplay(pageDashboard, sBannerId):
    return pageDashboard.evaluate(S_DISPLAY_OF, sBannerId)


# ---------------------------------------------------------------------------
# The x hides what was read
# ---------------------------------------------------------------------------


@pytest.mark.falsification
def testTheNetworkRowReturnsWhenTheServerSaysSomethingNew(pageDashboard, serverHub):
    """Kills: a dismissal that holds whatever the server says next."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    pageDashboard.evaluate(S_SURFACE_NETWORK, fdictNetwork(listRefused=LIST_ONE_REFUSAL))
    assert fsDisplay(pageDashboard, "networkBanner") == "block"
    pageDashboard.click("#btnDismissNetwork")
    assert fsDisplay(pageDashboard, "networkBanner") == "none"
    pageDashboard.evaluate(S_SURFACE_NETWORK, fdictNetwork(listRefused=LIST_ONE_REFUSAL))
    assert fsDisplay(pageDashboard, "networkBanner") == "none", (
        "the same answer, in the same view, must stay hidden")
    pageDashboard.evaluate(S_SURFACE_NETWORK, fdictNetwork(listRefused=LIST_TWO_REFUSALS))
    assert fsDisplay(pageDashboard, "networkBanner") == "block", (
        "a new refused host is something the researcher has not read")
    assert "mirror.example.net" in pageDashboard.inner_text("#networkBanner")
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testADismissedRowReturnsOnTheNextOpen(pageDashboard, serverHub):
    """Kills: a dismissal that outlives the view it was made in."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    pageDashboard.evaluate(S_SURFACE_NETWORK, fdictNetwork(sProxyState="down"))
    pageDashboard.click("#btnDismissNetwork")
    assert fsDisplay(pageDashboard, "networkBanner") == "none"
    pageDashboard.evaluate("() => VaibifyApp.fnAdvanceViewGeneration()")
    pageDashboard.evaluate(S_SURFACE_NETWORK, fdictNetwork(sProxyState="down"))
    assert fsDisplay(pageDashboard, "networkBanner") == "block"
    assert "network proxy down" in pageDashboard.inner_text("#networkBanner")


def testTheBindingNoticesHideUntilTheNoticesChange(pageDashboard, serverHub):
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    dictFirst = fdictDescribed([fdictBindingEntry(S_NOTICE_ONE)])
    pageDashboard.evaluate(S_RENDER_NOTICES, dictFirst)
    assert fsDisplay(pageDashboard, "remoteBindingBanner") == "block"
    pageDashboard.click("#btnDismissRemoteBindings")
    assert fsDisplay(pageDashboard, "remoteBindingBanner") == "none"
    pageDashboard.evaluate(S_RENDER_NOTICES, dictFirst)
    assert fsDisplay(pageDashboard, "remoteBindingBanner") == "none"
    pageDashboard.evaluate(
        S_RENDER_NOTICES, fdictDescribed([fdictBindingEntry(S_NOTICE_TWO)]))
    assert fsDisplay(pageDashboard, "remoteBindingBanner") == "block"
    assert "deposition 12" in pageDashboard.inner_text("#remoteBindingBanner")
    assert pageDashboard.listPageErrors == []


# ---------------------------------------------------------------------------
# Hiding a binding notice authorizes nothing
# ---------------------------------------------------------------------------


S_PROMOTION = "promotionUnderReview"
_RE_RECONCILE = re.compile(rf".*/promotions/{S_PROMOTION}/reconcile$")
_RE_CONFIRM = re.compile(r".*/api/bindings/[^/?]+/confirm$")


@pytest.mark.falsification
def testHidingABindingNoticeSendsNothingAndTheActionIsStillRefused(
    pageDashboard, serverHub,
):
    """Kills: a dismissal that reaches the server."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    dictEntry = dict(fdictBindingEntry(S_NOTICE_ONE), dictEvidence={
        "bSucceeded": True, "sState": "token-accepted", "listDepositTitles": []})
    pageDashboard.evaluate(S_RENDER_NOTICES, fdictDescribed([dictEntry]))
    listBindingRequests, listConfirms, listReconciles = [], [], []
    pageDashboard.on("request", lambda requestSeen: (
        listBindingRequests.append(requestSeen.url)
        if "/api/bindings/" in requestSeen.url else None))
    pageDashboard.click("#btnDismissRemoteBindings")
    pageDashboard.wait_for_timeout(800)
    assert listBindingRequests == [], "hiding a notice must send nothing"

    def fnReconcile(routeIntercepted):
        listReconciles.append(routeIntercepted.request.url)
        fnAnswerJson(routeIntercepted, 409, {"detail": {
            "sError": "BINDING-REQUIRED", "sService": "zenodo",
            "sRecordKind": "project", "sInstance": "sandbox",
            "sMessage": "Nothing is confirmed yet for this workflow's "
                        "project deposits on the Zenodo sandbox.",
        }})

    def fnRead(routeIntercepted):
        if "sService=" not in routeIntercepted.request.url:
            routeIntercepted.fallback()
            return
        fnAnswerJson(routeIntercepted, 200, fdictDescribed([dictEntry]))

    def fnConfirm(routeIntercepted):
        listConfirms.append(routeIntercepted.request.url)
        fnAnswerJson(routeIntercepted, 200, {"bConfirmed": True})

    pageDashboard.route(_RE_RECONCILE, fnReconcile)
    pageDashboard.route(_RE_BINDINGS_READ, fnRead)
    pageDashboard.route(_RE_CONFIRM, fnConfirm)
    pageDashboard.evaluate(
        "(sPromotion) => { VaibifyApp.fnRunProjectAction("
        "'reconcile-promotion', sPromotion, null); }", S_PROMOTION)
    pageDashboard.wait_for_selector(
        "#btnRemoteBindingConfirm:not([disabled])", timeout=10000)
    assert len(listReconciles) == 1, "the action ran and was refused"
    assert listConfirms == [], "nothing was confirmed without the researcher"
    pageDashboard.click("#btnRemoteBindingCancel")
    assert pageDashboard.listPageErrors == []


# ---------------------------------------------------------------------------
# Every banner says why it is showing
# ---------------------------------------------------------------------------


S_SURFACE_EVERY_READINESS_BANNER = """() => {
    VaibifyContainerManager.fnSurfaceReadinessOutcome({
        sStatus: "ready", bReady: true,
        saWarnings: ["A repository could not be cloned."],
        listConfigurationDrift: ["The environment's settings changed."],
        listPinnedEnvironmentDrift: ["A port differs from the pinned image."],
        listResourceLimitDrift: ["The memory limit differs."],
        listResourceAdvisories: ["The memory limit may be small."],
        dictNetwork: {sMode: "web", sName: "", bSealed: true,
                      sProxyState: "down", listRefused: [],
                      sContainerState: "sealed", sEvidence: "",
                      bFileAgrees: true},
    }, VaibifyApp.fsGetContainerId());
}"""

DICT_ZENODO_PENDING = {
    "bApplicable": True, "bAwaitingDecision": True, "bRotationAdvised": False,
    "dictActive": {"sandbox": False, "production": False},
    "dictTokenPages": {},
    "listSlots": [{
        "sSlot": "zenodo_token_sandbox", "sState": "removed", "sReason": "",
        "sInstance": "sandbox", "sProposedInstance": "sandbox",
        "bPending": True, "bAwaitingDecision": True, "sConflictChoice": "",
        "dictValidation": {"sOutcome": "accepted", "sDetail": "", "sAt": "now"},
        "listDepositTitles": [],
    }],
}

LIST_BANNERS_WITH_HELP = [
    "driftBanner", "configurationDriftBanner", "pinnedEnvironmentDriftBanner",
    "buildWarningsBanner", "networkBanner", "zenodoTokenBanner",
    "remoteBindingBanner", "resourceLimitDriftBanner", "resourceAdvisoryBanner",
]


def testEveryBannerSaysWhyItIsShowing(pageDashboard, serverHub):
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    pageDashboard.evaluate(S_SURFACE_EVERY_READINESS_BANNER)
    pageDashboard.evaluate(
        "(dictDescribed) => VaibifyZenodoTokens.fnRender(dictDescribed)",
        DICT_ZENODO_PENDING)
    pageDashboard.evaluate(
        S_RENDER_NOTICES, fdictDescribed([fdictBindingEntry(S_NOTICE_ONE)]))
    pageDashboard.evaluate(
        "() => VaibifyWorkflowManager._fnRenderDriftBannerForTest("
        "{sRefusal: 'diverged-branches', iAhead: 1, iBehind: 2, sBranch: 'main'})")
    for sBannerId in LIST_BANNERS_WITH_HELP:
        elHelp = pageDashboard.locator(f"#{sBannerId} .notice-banner-help")
        assert elHelp.count() == 1, f"{sBannerId} has no '?'"
        assert len(elHelp.get_attribute("title") or "") > 80, sBannerId
        assert elHelp.is_visible(), f"{sBannerId}'s '?' is not visible"
    pageDashboard.click("#networkBanner .notice-banner-help")
    pageDashboard.wait_for_selector("#popupHelp", state="visible", timeout=5000)
    assert "This row describes the container's network" in (
        pageDashboard.inner_text("#popupHelp"))
    assert pageDashboard.listPageErrors == []


# ---------------------------------------------------------------------------
# The chips ask the server and show the row whatever it says
# ---------------------------------------------------------------------------


def _fnChooseFromTheViewMenu(pageDashboard, sItemSelector):
    pageDashboard.click(".toolbar-menu-trigger:text-is('View')")
    pageDashboard.click(sItemSelector)


def testViewNetworkStatusShowsTheRowWhenAllIsWell(pageDashboard, serverHub):
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    listReadinessAsks = []
    dictHealthy = fdictNetwork(sMode="web")

    def fnReady(routeIntercepted):
        listReadinessAsks.append(routeIntercepted.request.url)
        fnAnswerJson(routeIntercepted, 200, {
            "bReady": True, "sStatus": "ready", "dictNetwork": dictHealthy})

    pageDashboard.route(_RE_READY, fnReady)
    pageDashboard.evaluate(S_SURFACE_NETWORK, dictHealthy)
    assert fsDisplay(pageDashboard, "networkBanner") == "none"
    _fnChooseFromTheViewMenu(pageDashboard, "#btnViewNetworkStatus")
    pageDashboard.wait_for_function(
        "() => document.getElementById('networkBanner').style.display === 'block'",
        timeout=10000)
    assert len(listReadinessAsks) == 1, "the menu item asks the server at the click"
    sText = pageDashboard.inner_text("#networkBanner")
    assert "Network mode: web (network proxy running)" in sText
    assert "Nothing refused recently." in sText
    pageDashboard.click("#btnDismissNetwork")
    assert fsDisplay(pageDashboard, "networkBanner") == "none"
    assert pageDashboard.listPageErrors == []


def testViewRemoteBindingsSaysWhenNothingNeedsReview(pageDashboard, serverHub):
    def fnRead(routeIntercepted):
        fnAnswerJson(routeIntercepted, 200, fdictDescribed([
            fdictBindingEntry("", sState="agrees")]))

    pageDashboard.route(_RE_BINDINGS_READ, fnRead)
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    assert fsDisplay(pageDashboard, "remoteBindingBanner") == "none"
    _fnChooseFromTheViewMenu(pageDashboard, "#btnViewRemoteBindings")
    pageDashboard.wait_for_function(
        "() => document.getElementById('remoteBindingBanner').style.display === 'block'",
        timeout=10000)
    assert "Nothing needs your review now." in (
        pageDashboard.inner_text("#remoteBindingBanner"))
    assert pageDashboard.listPageErrors == []
