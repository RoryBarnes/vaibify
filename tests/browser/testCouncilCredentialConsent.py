"""The council button opens the step each readiness state names.

Contracts A2, A5 and C of the credential-consent plan, driven in a real
browser against the real capabilities, consent, job and panel routes.
The credential gate is NOT patched: consent and outcomes land in a real
(temporary) credential document and the gate reads them back, so a
button that opens the right modal for the wrong reason fails here.

The one thing replaced is the job body, because the browser lane has
no daemon to run a runner in: the replacement writes the job record and
publishes its outcome through the same store functions the real job
uses. Nothing here proves anything about a real subscription token.
"""

import json
import os
import tempfile

import pytest

from .testBrowserJourneys import _fnReleaseBrowserLaneOwnership
from .testCouncilPlanningJourney import (  # noqa: F401 — fixture wiring
    _fdictClaimAndActivate,
    _fnIsolateCouncilStore,
)

pytestmark = pytest.mark.browser


@pytest.fixture(autouse=True)
def sEvidencePath(monkeypatch, serverHub):
    """A throwaway credential document, never the developer's real one."""
    from vaibify.gui import agentCouncilCredentialGate
    sDirectory = tempfile.mkdtemp(prefix="councilConsentLane")
    sPath = os.path.join(sDirectory, "agentCouncils",
                         "credentialEvidence.json")
    monkeypatch.setattr(
        agentCouncilCredentialGate, "fsResolveCredentialEvidencePath",
        lambda: sPath)
    yield sPath
    _fnReleaseBrowserLaneOwnership(serverHub.app.state)


def _fnScriptJobOutcome(monkeypatch, sOutcome, sFailedCheck=""):
    """Replace the job body with one that ends at once in ``sOutcome``."""
    from vaibify.gui import (
        agentCouncilCredentialTest,
        agentCouncilDockerGateway,
    )
    monkeypatch.setattr(agentCouncilDockerGateway,
                        "fdockerCreateCouncilClient", lambda: object())

    def _fnScriptedJob(dictJob, dictRuntime):
        for dictCheck in dictJob["listChecks"]:
            dictCheck["sStatus"] = "passed"
            if dictCheck["sCheckId"] == sFailedCheck:
                dictCheck["sStatus"] = sOutcome
                break
        try:
            agentCouncilCredentialTest.fnPublishJobOutcome(
                dictJob, sOutcome, sFailedCheck,
                "scripted in the browser lane" if sFailedCheck else "")
        finally:
            dictRuntime.pop("fileJobLock").close()

    monkeypatch.setattr(agentCouncilCredentialTest, "fnRunCredentialTestJob",
                        _fnScriptedJob)


def _fnOpenConsentModal(page, serverHub):
    _fdictClaimAndActivate(page, serverHub)
    page.click("#btnAgentCouncil")
    page.wait_for_selector("#btnCouncilConsentRun", timeout=8000)


@pytest.mark.falsification
def testANeedsTestButtonIsEnabledAndOpensTheConsentModal(
        pageDashboard, serverHub):
    """Readiness ``needsCredentialTest``: enabled, and the click consents.

    Kills: the toolbar treating a consent the researcher can give as a
    wall (greyed, a toast) instead of opening the modal.
    """
    _fdictClaimAndActivate(pageDashboard, serverHub)
    dictButton = pageDashboard.evaluate("""() => {
        const el = document.getElementById('btnAgentCouncil');
        return {bDisabled: el.disabled,
                bBlocked: el.classList.contains('council-blocked'),
                sTitle: el.title};
    }""")
    assert dictButton["bDisabled"] is False
    assert dictButton["bBlocked"] is False, dictButton
    assert "credential test" in dictButton["sTitle"]
    pageDashboard.click("#btnAgentCouncil")
    pageDashboard.wait_for_selector("#btnCouncilConsentRun", timeout=8000)
    sModal = pageDashboard.inner_text("#councilConsentModalBody")
    assert "First council with this project's image on this computer" in (
        sModal)
    assert pageDashboard.locator("#btnCouncilConsentCancel").count() == 1
    assert pageDashboard.is_checked("#councilConsentProvider0")
    assert pageDashboard.listPageErrors == []
    assert pageDashboard.listConsoleErrors == []


def testTheConsentModalStatesEveryCheckAndTheResidualRisk(
        pageDashboard, serverHub):
    _fnOpenConsentModal(pageDashboard, serverHub)
    pageDashboard.click(".council-consent-details summary")
    sModal = pageDashboard.inner_text("#councilConsentModalBody")
    assert pageDashboard.locator(
        ".council-consent-details ol li").count() == 7
    assert "does not make that risk zero" in sModal
    assert "every project on this computer that uses this image" in sModal
    assert "about two paid requests" in sModal
    assert "does not spend a request to prove the login still works" in (
        sModal)


def testCancellingTheConsentWritesNothing(pageDashboard, serverHub,
                                          sEvidencePath):
    _fnOpenConsentModal(pageDashboard, serverHub)
    pageDashboard.click("#btnCouncilConsentCancel")
    assert not pageDashboard.is_visible("#councilConsentModal")
    assert not os.path.exists(sEvidencePath), (
        "cancelling the consent modal recorded a consent")


@pytest.mark.falsification
def testAPassingTestContinuesToTheCouncil(pageDashboard, serverHub,
                                          monkeypatch, sEvidencePath):
    """The whole journey: consent, a passing test, then the chooser.

    Kills: the continue path not re-reading capabilities, which would
    leave the button on its old verdict and loop back into consent.
    """
    _fnScriptJobOutcome(monkeypatch, "passed")
    _fnOpenConsentModal(pageDashboard, serverHub)
    pageDashboard.click("#btnCouncilConsentRun")
    pageDashboard.wait_for_selector("#btnCouncilConsentContinue",
                                    timeout=15000)
    assert "claude: passed" in pageDashboard.inner_text(
        "#councilConsentModalBody")
    dictDocument = json.load(open(sEvidencePath))
    assert dictDocument["listOutcomes"][-1]["sOutcome"] == "passed"
    pageDashboard.click("#btnCouncilConsentContinue")
    pageDashboard.wait_for_selector("#btnCouncilPlanChange", timeout=8000)
    assert not pageDashboard.is_visible("#councilConsentModal")
    assert pageDashboard.listPageErrors == []


def testAFailingTestSaysWhichCheckAndOffersNoWayForward(
        pageDashboard, serverHub, monkeypatch):
    _fnScriptJobOutcome(monkeypatch, "failed", "trivialTurn")
    _fnOpenConsentModal(pageDashboard, serverHub)
    pageDashboard.click("#btnCouncilConsentRun")
    pageDashboard.wait_for_selector("#btnCouncilConsentDone", timeout=15000)
    sModal = pageDashboard.inner_text("#councilConsentModalBody")
    assert "failed at 'trivialTurn'" in sModal
    assert "No provider passed" in sModal
    assert pageDashboard.locator("#btnCouncilConsentContinue").count() == 0


def testThePanelShowsTheOutcomeAndWithdrawTurnsTheProviderOff(
        pageDashboard, serverHub, monkeypatch):
    """Panel (A5): consent, the latest test, and a working Withdraw."""
    _fnScriptJobOutcome(monkeypatch, "passed")
    _fnOpenConsentModal(pageDashboard, serverHub)
    pageDashboard.click("#btnCouncilConsentRun")
    pageDashboard.wait_for_selector("#btnCouncilConsentContinue",
                                    timeout=15000)
    pageDashboard.click("#btnCouncilConsentContinue")
    pageDashboard.wait_for_selector("#btnCouncilCredentialTests",
                                    timeout=8000)
    pageDashboard.click("#btnCouncilCredentialTests")
    pageDashboard.wait_for_selector(".council-credential-panel",
                                    timeout=8000)
    sRow = pageDashboard.inner_text("tr[data-provider='claude']")
    assert "active" in sRow and "passed" in sRow
    assert "suspends the current pass" in pageDashboard.inner_text(
        "#councilConsentModalBody")
    pageDashboard.click("tr[data-provider='claude'] .council-panel-withdraw")
    pageDashboard.click("#btnConfirmOk")
    pageDashboard.wait_for_selector(
        "tr[data-provider='claude'][data-state='withdrawn']", timeout=8000)
    sReadiness = pageDashboard.evaluate("""async () => {
        await VaibifyAgentCouncil.fnRefreshCapabilities();
        return document.getElementById('btnAgentCouncil').title;
    }""")
    assert "credential test" in sReadiness


@pytest.mark.falsification
def testNoLoginIsAWallWithTheLoginRemedy(pageDashboard, serverHub,
                                         monkeypatch):
    """Readiness ``blocked``: greyed, and the click says what to do.

    Kills: offering a consent modal with nothing in it to consent to.
    """
    from vaibify.gui.routes import councilRoutes
    monkeypatch.setattr(
        councilRoutes, "fdictReadProjectLoginState",
        lambda dictCtx, sContainerId, sProvider="claude": {
            "bHasLogin": False, "iExpiresAtEpochMilliseconds": 0,
            "sLoginProblem": "no persisted login was found"})
    _fdictClaimAndActivate(pageDashboard, serverHub)
    assert pageDashboard.evaluate(
        "() => document.getElementById('btnAgentCouncil')"
        ".classList.contains('council-blocked')")
    pageDashboard.click("#btnAgentCouncil")
    pageDashboard.wait_for_selector(".toast", timeout=8000)
    assert "Log in from the project's terminal" in pageDashboard.inner_text(
        ".toast")
    assert not pageDashboard.is_visible("#councilConsentModal")


def testTheConveneDisclosureRepeatsTheResidualRisk(
        pageDashboard, serverHub, monkeypatch):
    from vaibify.gui import agentCouncilCredentialGate
    monkeypatch.setattr(
        agentCouncilCredentialGate, "fdictEvaluateCredentialEnablement",
        lambda sProvider, sImageIdentity=None: {
            "bEnabled": True, "sReason": "", "dictRecord": {},
            "sState": "authorized"})
    _fdictClaimAndActivate(pageDashboard, serverHub)
    pageDashboard.click("#btnAgentCouncil")
    pageDashboard.wait_for_selector("#btnCouncilPlanChange", timeout=8000)
    pageDashboard.click("#btnCouncilPlanChange")
    pageDashboard.wait_for_selector(".council-disclosure", timeout=8000)
    assert "does not make that risk zero" in pageDashboard.inner_text(
        ".council-disclosure")
