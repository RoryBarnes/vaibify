"""The "Set up Docker" wizard in a real browser.

The hub's answers are staged with route intercepts: the lane's Docker
is a fail-closed fake, and the setup routes read the machine running
the lane, so a live answer would depend on where the lane runs. The
server half (the plan, the agent refusal, the settings bounds) is
driven over real HTTP in ``tests/testRuntimeSetupRoutes.py``; what is
exercised here is the page: it shows the hub's words and commands,
offers Close and Continue for a step only the researcher can do, reads
the machine again on Continue, and sends back only an action's NAME.
"""

import json

import pytest

from tests.browser.testRemnantsGlyphAndPanel import _fnSeedScan


pytestmark = pytest.mark.browser

S_HOMEBREW_COMMAND = (
    '/bin/bash -c "$(curl -fsSL '
    'https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"')

DICT_NEEDS_A_PACKAGE_MANAGER = {"bReady": False, "listSteps": [{
    "sStepName": "install-package-manager",
    "sTitle": "Install a package manager", "sKind": "needs-you",
    "sExplanation": "Install Homebrew or MacPorts, then press Continue.",
    "listActions": [], "listCommandsToShow": [S_HOMEBREW_COMMAND],
}]}
DICT_INSTALL_NEXT = {"bReady": False, "listSteps": [{
    "sStepName": "install-runtime", "sTitle": "Install Colima and Docker",
    "sKind": "run", "sExplanation": "Homebrew will install colima.",
    "listActions": [{"sActionName": "install-with-homebrew",
                     "sLabel": "Install"}],
    "listCommandsToShow": [],
}, {
    "sStepName": "start-colima", "sTitle": "Start Colima", "sKind": "run",
    "sExplanation": "", "listActions": [], "listCommandsToShow": [],
}]}
DICT_START_NEXT = {"bReady": False, "listSteps": [{
    "sStepName": "start-colima", "sTitle": "Start Colima", "sKind": "run",
    "sExplanation": "The first start downloads a small virtual machine.",
    "listActions": [{"sActionName": "start-new-colima", "sLabel": "Start"}],
    "listCommandsToShow": [],
    "dictSettings": {"iCpus": 9, "iMemoryGigabytes": 8},
}]}
DICT_READY = {"bReady": True, "listSteps": []}


def _fnFulfillJson(routeIntercepted, dictBody, iStatus=200):
    routeIntercepted.fulfill(status=iStatus, content_type="application/json",
                             body=json.dumps(dictBody))


def _fnOpenTheWizard(page, serverHub, listPlans):
    """Show the hub with Docker unavailable and open the wizard on it.

    ``listPlans`` answers each read of the plan in turn; the last one
    repeats.
    """
    listQueue = list(listPlans)
    page.route("**/api/system/docker-status", lambda routeIntercepted: (
        _fnFulfillJson(routeIntercepted, {
            "bAvailable": False, "sError": "no daemon",
            "sHint": "The Colima virtual machine is not running.",
            "sCommand": "colima start", "sEndpoint": ""})))
    page.route("**/api/system/docker-status/retry", lambda routeIntercepted: (
        _fnFulfillJson(routeIntercepted, {"bAvailable": True, "sError": "",
                                          "sHint": "", "sCommand": "",
                                          "sEndpoint": ""})))
    page.route("**/api/runtime/setup", lambda routeIntercepted: (
        _fnFulfillJson(routeIntercepted,
                       listQueue.pop(0) if len(listQueue) > 1
                       else listQueue[0])))
    page.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    page.click("#btnSetupDocker", timeout=15000)
    page.wait_for_selector(
        "#modalRuntimeSetup .runtime-setup-step, "
        "#modalRuntimeSetup .runtime-setup-ready", timeout=10000)


def _flistButtonLabels(page):
    return page.locator(
        "#modalRuntimeSetup .runtime-setup-buttons button").all_text_contents()


@pytest.mark.falsification
def testAStepOnlyTheResearcherCanDoOffersCloseAndContinue(
        pageDashboard, serverHub):
    """Continue reads the machine again and the wizard carries on.

    Kills: a Continue button that does not read the machine again.
    """
    _fnOpenTheWizard(pageDashboard, serverHub,
                     [DICT_NEEDS_A_PACKAGE_MANAGER, DICT_INSTALL_NEXT])
    assert _flistButtonLabels(pageDashboard) == ["Close", "Continue"]
    assert pageDashboard.locator(
        "#modalRuntimeSetup .runtime-setup-command code").first.text_content(
        ) == S_HOMEBREW_COMMAND
    pageDashboard.click("#btnRuntimeSetupContinue")
    pageDashboard.wait_for_selector(
        '#modalRuntimeSetup .runtime-setup-step[data-step-name="install-runtime"]',
        timeout=10000)
    assert _flistButtonLabels(pageDashboard) == ["Close", "Install"]
    assert pageDashboard.locator(
        "#modalRuntimeSetup .runtime-setup-later").text_content() == (
        "Then: Start Colima.")
    assert pageDashboard.listPageErrors == []


def testContinueWithTheStepStillNeededSaysSo(pageDashboard, serverHub):
    _fnOpenTheWizard(pageDashboard, serverHub, [DICT_NEEDS_A_PACKAGE_MANAGER])
    pageDashboard.click("#btnRuntimeSetupContinue")
    pageDashboard.wait_for_selector(
        "#modalRuntimeSetup .runtime-setup-message.warning", timeout=10000)
    assert "still finds this step needed" in pageDashboard.locator(
        "#modalRuntimeSetup .runtime-setup-message").text_content()


def testRunningAStepSendsOnlyItsNameAndTheEditedSettings(
        pageDashboard, serverHub):
    listBodies = []

    def fnAnswerRun(routeIntercepted):
        listBodies.append(json.loads(routeIntercepted.request.post_data))
        _fnFulfillJson(routeIntercepted, DICT_READY)

    pageDashboard.route("**/api/runtime/setup/run", fnAnswerRun)
    pageDashboard.route("**/api/runtime/setup/progress", lambda r: (
        _fnFulfillJson(r, {"bLive": False, "sTitle": "Start Colima",
                           "sOutcome": "succeeded",
                           "listTail": ["$ colima start", "INFO done"]})))
    _fnOpenTheWizard(pageDashboard, serverHub, [DICT_START_NEXT])
    pageDashboard.fill("#inputRuntimeSetupCpus", "3")
    pageDashboard.click(
        '#modalRuntimeSetup button[data-action-name="start-new-colima"]')
    pageDashboard.wait_for_selector("#modalRuntimeSetup .runtime-setup-ready",
                                    timeout=10000)
    assert listBodies == [{"sActionName": "start-new-colima", "iCpus": 3,
                           "iMemoryGigabytes": 8}]
    assert pageDashboard.locator(
        "#modalRuntimeSetup .runtime-setup-ready").text_content() == (
        "Docker is ready for vaibify.")
    assert "INFO done" in pageDashboard.locator(
        "#modalRuntimeSetup .runtime-setup-tail").text_content()
    assert _flistButtonLabels(pageDashboard) == ["Close"]
    assert pageDashboard.listPageErrors == []


def testAStepTheHubRefusesShowsTheHubsSentence(pageDashboard, serverHub):
    sRefusal = ("Another Docker setup is already running on this machine "
                "(in another tab or a terminal). Wait for it to finish.")
    pageDashboard.route("**/api/runtime/setup/run", lambda r: _fnFulfillJson(
        r, {"detail": {"sMessage": sRefusal}}, iStatus=409))
    pageDashboard.route("**/api/runtime/setup/progress", lambda r: (
        _fnFulfillJson(r, {"bLive": False, "sTitle": "", "sOutcome": "",
                           "listTail": []})))
    _fnOpenTheWizard(pageDashboard, serverHub, [DICT_INSTALL_NEXT])
    pageDashboard.click(
        '#modalRuntimeSetup button[data-action-name="install-with-homebrew"]')
    pageDashboard.wait_for_selector(
        "#modalRuntimeSetup .runtime-setup-message.error", timeout=10000)
    assert sRefusal in pageDashboard.locator(
        "#modalRuntimeSetup .runtime-setup-message").text_content()


def testClosingTheWizardAsksAboutDockerAgain(pageDashboard, serverHub):
    listRetries = []
    _fnOpenTheWizard(pageDashboard, serverHub, [DICT_READY])
    pageDashboard.route("**/api/system/docker-status/retry", lambda r: (
        listRetries.append(r.request.url), _fnFulfillJson(r, {
            "bAvailable": True, "sError": "", "sHint": "", "sCommand": "",
            "sEndpoint": ""})))
    pageDashboard.click("#btnRuntimeSetupClose")
    pageDashboard.wait_for_function(
        "() => document.getElementById('modalRuntimeSetup').style.display "
        "=== 'none'", timeout=5000)
    pageDashboard.wait_for_timeout(300)
    assert len(listRetries) == 1


DICT_RESIZE_STEP = {
    "sStepName": "resize-colima", "sTitle": "Give Colima more memory",
    "sKind": "run", "bOptional": True, "bAnnounceOnHub": True,
    "sExplanation": "Your Colima machine 'default' has 2.0 GB of memory.",
    "listActions": [{"sActionName": "resize-colima",
                     "sLabel": "Restart with more memory"}],
    "listCommandsToShow": [],
}
DICT_READY_WITH_A_SMALL_MACHINE = {
    "bReady": True, "listSteps": [DICT_RESIZE_STEP],
    "dictHubBannerStep": DICT_RESIZE_STEP,
}
DICT_ONLY_BUILDX_MISSING = {"bReady": False, "listSteps": [{
    "sStepName": "install-runtime", "sTitle": "Install docker buildx",
    "sKind": "run", "sExplanation": "Homebrew will install docker-buildx.",
    "listActions": [{"sActionName": "install-with-homebrew",
                     "sLabel": "Install"}],
    "listCommandsToShow": [],
}], "dictHubBannerStep": None}


def _fnOpenTheHubWithDockerAnswering(page, serverHub, dictPlan, listUrls,
                                     listStatusReads=None):
    def fnAnswerStatus(routeIntercepted):
        if listStatusReads is not None:
            listStatusReads.append(routeIntercepted.request.url)
        _fnFulfillJson(routeIntercepted, {
            "bAvailable": True, "sError": "", "sHint": "", "sCommand": "",
            "sEndpoint": ""})

    page.route("**/api/system/docker-status", fnAnswerStatus)

    def fnAnswerPlan(routeIntercepted):
        listUrls.append(routeIntercepted.request.url)
        _fnFulfillJson(routeIntercepted, dictPlan)

    page.route("**/api/runtime/setup*", fnAnswerPlan)
    page.goto(serverHub.fsBootstrapUrl(), wait_until="load")


@pytest.mark.falsification
def testAnUndersizedMachineIsReachableWhileDockerAnswers(
        pageDashboard, serverHub):
    """Kills: a wizard reachable only while Docker does not answer."""
    listUrls = []
    _fnOpenTheHubWithDockerAnswering(
        pageDashboard, serverHub, DICT_READY_WITH_A_SMALL_MACHINE, listUrls)
    pageDashboard.wait_for_selector(
        "#dockerStatusBanner.docker-status-banner-advice #btnSetupDocker",
        timeout=15000)
    assert "Give Colima more memory" in pageDashboard.locator(
        "#dockerStatusBanner").text_content()
    assert any("bAdvisory=true" in sUrl for sUrl in listUrls), listUrls
    pageDashboard.click("#btnSetupDocker")
    pageDashboard.wait_for_selector(
        '#modalRuntimeSetup .runtime-setup-step[data-step-name="resize-colima"]',
        timeout=10000)
    assert pageDashboard.listPageErrors == []


def testAReadyMachineShowsNoBanner(pageDashboard, serverHub):
    listUrls = []
    _fnOpenTheHubWithDockerAnswering(
        pageDashboard, serverHub, DICT_READY, listUrls)
    pageDashboard.wait_for_timeout(1500)
    assert listUrls, "the hub never asked for the plan"
    assert not pageDashboard.locator("#dockerStatusBanner").is_visible()


S_RECORD_BANNER_HIDES = """() => {
    const elBanner = document.getElementById('dockerStatusBanner');
    window.listBannerHides = [];
    new MutationObserver(() => {
        if (elBanner.style.display !== 'flex' || !elBanner.textContent) {
            window.listBannerHides.push(Date.now());
        }
    }).observe(elBanner, {attributes: true, childList: true,
                          subtree: true, characterData: true});
}"""


@pytest.mark.falsification
def testTheBannerStaysOnScreenWhileTheHubPolls(pageDashboard, serverHub):
    """Kills: every poll blanking the banner while it asks the hub again.

    A MutationObserver records every hide, however brief, so the check
    does not depend on catching the gap with a screenshot. The control
    is that the hub really polled Docker's status several times.
    """
    listUrls, listStatusReads = [], []
    _fnOpenTheHubWithDockerAnswering(
        pageDashboard, serverHub, DICT_READY_WITH_A_SMALL_MACHINE, listUrls,
        listStatusReads)
    pageDashboard.wait_for_selector(
        "#dockerStatusBanner.docker-status-banner-advice #btnSetupDocker",
        timeout=15000)
    iReadsBefore = len(listStatusReads)
    pageDashboard.evaluate(S_RECORD_BANNER_HIDES)
    pageDashboard.wait_for_timeout(10000)
    assert len(listStatusReads) - iReadsBefore >= 3, listStatusReads
    assert pageDashboard.evaluate("window.listBannerHides") == []
    assert len([s for s in listUrls if "bAdvisory=true" in s]) == 1, listUrls
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testAMissingBuildxAloneRaisesNoBanner(pageDashboard, serverHub):
    """Kills: a banner for a step the hub did not choose to announce."""
    listUrls = []
    _fnOpenTheHubWithDockerAnswering(
        pageDashboard, serverHub, DICT_ONLY_BUILDX_MISSING, listUrls)
    pageDashboard.wait_for_timeout(4000)
    assert any("bAdvisory=true" in sUrl for sUrl in listUrls), listUrls
    assert not pageDashboard.locator("#dockerStatusBanner").is_visible()
    assert pageDashboard.listPageErrors == []


S_BLOCKED_SENTENCE = (
    "Containerizing 'hostProjectAlpha' is stopped. "
    "Dockerfile.claudeArmOnIntel needs docker buildx, which is not "
    "installed on this machine.")
DICT_BLOCKED_ON_BUILDX = dict(DICT_ONLY_BUILDX_MISSING, listAttentionItems=[{
    "sItemId": "buildx-missing:hostProjectAlpha", "sSeverity": "blocked",
    "sSentence": S_BLOCKED_SENTENCE,
}])
S_GLYPH_IS_RED = """() => {
    const elProbe = document.createElement('span');
    elProbe.style.color = 'var(--color-red)';
    document.body.appendChild(elProbe);
    const sRed = getComputedStyle(elProbe).color;
    elProbe.remove();
    return getComputedStyle(document.getElementById('btnRemnants')).color === sRed;
}"""


@pytest.mark.falsification
def testABlockedDockerStepTurnsTheSignRedAndLeadsToTheWizard(
        pageDashboard, serverHub):
    """Kills: a verified block drawn in the orange of "worth a look".

    The colour is read off the live element against the theme's own
    red, so a class with no rule behind it cannot pass.
    """
    _fnSeedScan(serverHub, [])
    _fnOpenTheHubWithDockerAnswering(
        pageDashboard, serverHub, DICT_BLOCKED_ON_BUILDX, [])
    pageDashboard.wait_for_selector("#btnRemnants", state="visible",
                                    timeout=15000)
    elGlyph = pageDashboard.query_selector("#btnRemnants")
    assert elGlyph.inner_text().strip() == "\u26a0 1"
    assert S_BLOCKED_SENTENCE in elGlyph.get_attribute("title")
    assert pageDashboard.evaluate(S_GLYPH_IS_RED)
    assert not pageDashboard.locator("#dockerStatusBanner").is_visible()
    pageDashboard.click("#btnRemnants")
    pageDashboard.wait_for_selector("#modalInfo .attention-item--blocked",
                                    timeout=10000)
    sPanel = pageDashboard.text_content("#modalInfo")
    assert "Needs your attention" in sPanel
    assert S_BLOCKED_SENTENCE in sPanel
    assert "Leftover processes and files" in sPanel
    pageDashboard.click("#modalInfo .attention-set-up-docker")
    pageDashboard.wait_for_selector(
        '#modalRuntimeSetup .runtime-setup-step[data-step-name="install-runtime"]',
        timeout=10000)
    assert pageDashboard.query_selector("#modalInfo") is None
    assert pageDashboard.listPageErrors == []


def testNothingBlockedLeavesTheSignHidden(pageDashboard, serverHub):
    _fnSeedScan(serverHub, [])
    listUrls = []
    _fnOpenTheHubWithDockerAnswering(
        pageDashboard, serverHub, dict(DICT_ONLY_BUILDX_MISSING,
                                       listAttentionItems=[]), listUrls)
    pageDashboard.wait_for_timeout(4000)
    assert any("bAdvisory=true" in sUrl for sUrl in listUrls), listUrls
    assert not pageDashboard.locator("#btnRemnants").is_visible()
