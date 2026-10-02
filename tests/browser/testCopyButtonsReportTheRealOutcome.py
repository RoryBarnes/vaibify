"""A copy button says "Copied" only when the browser accepted the text.

Four buttons wrote to the clipboard and labelled themselves from the
click, not from the answer: the quarantine remedy, the Zenodo DOI on the
status card, the DOI in the post-push toast, and the council's
implementation brief. A refused write left every one of them claiming
success, and the researcher pasted something else. The write is a
promise, the engine decides it, and the label now follows the answer.

The engines are driven for real; only the clipboard is replaced, because
a headless browser's own answer depends on permissions and focus rather
than on this page. One stub refuses (the write rejects and so does the
textarea fallback) and one accepts and records what it was given.
"""

import json

import pytest

from .testBrowserJourneys import _fnReleaseBrowserLaneOwnership  # noqa: F401
from .testCouncilBlockedButtonExplainsItself import (  # noqa: F401
    _fdictActivateCouncilToolbar,
    _fnOpenCouncilWorkspace,
)
from .testCouncilFinishedSurface import _fsRenderFinished
from .testCouncilPlanningJourney import (  # noqa: F401 — fixture wiring
    _fdictClaimAndActivate,
    _fnIsolateCouncilStore,
    _fnScriptedProviderSeam,
)
from .testQuarantineChipExplains import _fnServeQuarantinedContainer
from tests.browser.conftest import fnOpenTheSeededHostWorkflow

pytestmark = pytest.mark.browser

_S_REFUSING_CLIPBOARD = """() => {
    Object.defineProperty(navigator.clipboard, 'writeText', {
        configurable: true,
        value: () => Promise.reject(
            new DOMException('refused', 'NotAllowedError')),
    });
    document.execCommand = () => false;
}"""

_S_ACCEPTING_CLIPBOARD = """() => {
    window.listCopiedTexts = [];
    Object.defineProperty(navigator.clipboard, 'writeText', {
        configurable: true,
        value: (sText) => {
            window.listCopiedTexts.push(sText);
            return Promise.resolve();
        },
    });
}"""

_S_FALLBACK_ONLY_CLIPBOARD = """() => {
    Object.defineProperty(navigator.clipboard, 'writeText', {
        configurable: true,
        value: () => Promise.reject(
            new DOMException('refused', 'NotAllowedError')),
    });
    document.execCommand = () => true;
}"""

S_DOI = "10.5281/zenodo.424242"


def _fnServeDepositCard(page):
    page.route(
        "**/api/zenodo/*/deposit",
        lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({
                "sDepositionId": "424242", "sDoi": S_DOI,
                "sConceptDoi": "", "sHtmlUrl": "",
            }),
        ),
    )


def _fnOpenDepositCard(page, serverHub):
    fnOpenTheSeededHostWorkflow(page, serverHub)
    _fnServeDepositCard(page)
    page.evaluate(
        "() => VaibifyZenodoDepositCard.fnOpen("
        "VaibifyApp.fsGetContainerId())"
    )
    page.wait_for_selector(".zdc-copy", timeout=10000)


def _fnOpenQuarantineModal(page, serverHub):
    _fnServeQuarantinedContainer(page)
    page.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    page.wait_for_selector(".containment-chip--quarantined", timeout=10000)
    page.click(".containment-chip--quarantined")
    page.wait_for_selector("#modalInfo .quarantine-copy-button", timeout=5000)


def testTheCopyHelperAnswersFalseWhenEveryRouteIsRefused(
    pageDashboard, serverHub,
):
    """Kills: resolving true without consulting the engine's answer."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    pageDashboard.evaluate(_S_REFUSING_CLIPBOARD)
    bCopied = pageDashboard.evaluate(
        "() => VaibifyFileOps.fpromiseCopyText('abc')")
    assert bCopied is False


def testTheCopyHelperFallsBackToTheTextareaWhenTheWriteIsRefused(
    pageDashboard, serverHub,
):
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    pageDashboard.evaluate(_S_FALLBACK_ONLY_CLIPBOARD)
    bCopied = pageDashboard.evaluate(
        "() => VaibifyFileOps.fpromiseCopyText('abc')")
    assert bCopied is True


def testTheCopyHelperAnswersTrueWhenTheWriteIsAccepted(
    pageDashboard, serverHub,
):
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    pageDashboard.evaluate(_S_ACCEPTING_CLIPBOARD)
    bCopied = pageDashboard.evaluate(
        "() => VaibifyFileOps.fpromiseCopyText('abc')")
    assert bCopied is True
    assert pageDashboard.evaluate("() => window.listCopiedTexts") == ["abc"]


@pytest.mark.falsification
def testTheDoiCopyButtonOnTheStatusCardReportsAFailedCopy(
    pageDashboard, serverHub,
):
    """Kills: labelling the button "Copied" before the write has answered."""
    _fnOpenDepositCard(pageDashboard, serverHub)
    pageDashboard.evaluate(_S_REFUSING_CLIPBOARD)
    pageDashboard.click(".zdc-copy")
    pageDashboard.wait_for_function(
        "() => document.querySelector('.zdc-copy').textContent"
        " === 'Copy failed'", timeout=5000)
    assert pageDashboard.listPageErrors == []


def testTheDoiCopyButtonOnTheStatusCardCopiesTheDoi(
    pageDashboard, serverHub,
):
    _fnOpenDepositCard(pageDashboard, serverHub)
    pageDashboard.evaluate(_S_ACCEPTING_CLIPBOARD)
    pageDashboard.click(".zdc-copy")
    pageDashboard.wait_for_function(
        "() => document.querySelector('.zdc-copy').textContent"
        " === 'Copied'", timeout=5000)
    assert pageDashboard.evaluate("() => window.listCopiedTexts") == [S_DOI]


@pytest.mark.falsification
def testTheQuarantineRemedyCopyButtonReportsAFailedCopy(
    pageDashboard, serverHub,
):
    """Kills: the quarantine button labelling itself from the click."""
    _fnOpenQuarantineModal(pageDashboard, serverHub)
    pageDashboard.evaluate(_S_REFUSING_CLIPBOARD)
    pageDashboard.click("#modalInfo .quarantine-copy-button")
    pageDashboard.wait_for_function(
        "() => document.querySelector("
        "'#modalInfo .quarantine-copy-button').textContent"
        " === 'Copy failed'", timeout=5000)
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testTheCouncilBriefCopyReportsAFailedCopy(pageDashboard, serverHub):
    """Kills: toasting "copied" after an unawaited, refused write."""
    _fnOpenCouncilWorkspace(pageDashboard, serverHub)
    _fsRenderFinished(pageDashboard)
    pageDashboard.route(
        "**/plan.md*",
        lambda route: route.fulfill(
            status=200, content_type="text/markdown",
            body="# Plan\n\nStep 1\n"),
    )
    pageDashboard.click("#btnCouncilOpenPlanTab")
    pageDashboard.evaluate(_S_REFUSING_CLIPBOARD)
    pageDashboard.click("#btnCouncilCopyBrief")
    pageDashboard.wait_for_selector(".toast.error", timeout=5000)
    sToast = pageDashboard.locator(".toast.error").last.inner_text()
    assert "refused the copy" in sToast, sToast
    assert pageDashboard.locator(
        ".toast", has_text="Implementation brief copied").count() == 0


def testTheCouncilBriefCopyConfirmsAnAcceptedCopy(pageDashboard, serverHub):
    _fnOpenCouncilWorkspace(pageDashboard, serverHub)
    _fsRenderFinished(pageDashboard)
    pageDashboard.route(
        "**/plan.md*",
        lambda route: route.fulfill(
            status=200, content_type="text/markdown",
            body="# Plan\n\nStep 1\n"),
    )
    pageDashboard.click("#btnCouncilOpenPlanTab")
    pageDashboard.evaluate(_S_ACCEPTING_CLIPBOARD)
    pageDashboard.click("#btnCouncilCopyBrief")
    pageDashboard.wait_for_selector(
        ".toast:has-text('Implementation brief copied')", timeout=5000)
    assert pageDashboard.evaluate(
        "() => window.listCopiedTexts") == ["# Plan\n\nStep 1\n"]
