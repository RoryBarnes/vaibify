"""An image vaibify did not build is not run until the researcher chooses.

Driven in a real browser against the real hub, with the start route's
refusal supplied by the same backend module the command line reads.
Asserted here: the modal appears for an unbuilt image and never for a
built one; nothing is preselected and Continue waits for a choice; every
option explains itself in a native ``<details>``; the credentials box
is off and unavailable under Inspect only; the words on screen ARE the
backend's words; a hostile image description renders as text; the badge
shows the stored choice; and an answer is posted for the digest shown,
after which the start is retried.
"""

import json
import os

import pytest

from tests.browser.conftest import S_CONTAINER_NAME
from vaibify.config import imageTrust, registryManager

pytestmark = pytest.mark.browser

S_TILE = f'.container-tile[data-name="{S_CONTAINER_NAME}"]'
S_MODAL = "#modalImageTrust"
S_DIGEST = "sha256:" + "e" * 64
S_START_ROUTE = f"**/api/containers/{S_CONTAINER_NAME}/start"
S_ANSWER_ROUTE = f"**/api/registry/{S_CONTAINER_NAME}/image-trust"


def fdictPrompt(sUser="root", listEntrypoint=None, sDigest=S_DIGEST):
    dictPrompt = imageTrust.fdictBuildTrustPrompt(
        {}, {"sId": sDigest, "dictLabels": {}, "iSizeBytes": 52428800,
             "sUser": sUser, "listEntrypoint": listEntrypoint or ["/start"]},
        {"sObtainedFrom": "a published deposit", "sBaseImageId": sDigest})
    return dictPrompt


def fnRefuseStartWithThePrompt(pageDashboard, dictPrompt, listAttempts):
    def fnHandle(routeIntercepted):
        listAttempts.append(1)
        dictDetail = {
            "sMessage": "choose how this image may run",
            "sAction": "confirm-image-trust", "dictImageTrust": dictPrompt,
        }
        routeIntercepted.fulfill(
            status=409, content_type="application/json",
            body=json.dumps({"detail": dictDetail, **dictDetail}))

    pageDashboard.route(S_START_ROUTE, fnHandle)


def fnLoadAndPressStart(pageDashboard, serverHub):
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_selector(S_TILE, timeout=15000)
    pageDashboard.click(f"{S_TILE} .container-tile-actions")
    pageDashboard.wait_for_selector(
        f"{S_TILE} .container-tile-menu", state="visible", timeout=5000)
    pageDashboard.click(f'{S_TILE} [data-action="start"]')


@pytest.mark.falsification
def testTheModalAppearsForAnUnbuiltImageWithNothingPreselected(
    pageDashboard, serverHub,
):
    """Kills: the frontend ignoring the confirm-image-trust refusal (the
    start would end in a bare error and the question is never asked), or
    preselecting an option."""
    listAttempts = []
    fnRefuseStartWithThePrompt(pageDashboard, fdictPrompt(), listAttempts)
    fnLoadAndPressStart(pageDashboard, serverHub)
    pageDashboard.wait_for_selector(S_MODAL, timeout=10000)
    assert pageDashboard.locator(f"{S_MODAL} input[type=radio]").count() == 3
    assert pageDashboard.locator(
        f"{S_MODAL} input[type=radio]:checked").count() == 0
    assert pageDashboard.locator("#btnImageTrustContinue").is_disabled()
    assert not pageDashboard.locator("#imageTrustCredentials").is_checked()
    sText = pageDashboard.locator(S_MODAL).inner_text()
    assert S_DIGEST in sText and "50.0 MB" in sText
    assert "a published deposit" in sText


def testNoModalForAnImageVaibifyBuilt(pageDashboard, serverHub):
    def fnStart(routeIntercepted):
        routeIntercepted.fulfill(
            status=202, content_type="application/json",
            body=json.dumps({
                "sName": S_CONTAINER_NAME, "sReservationId": "r" * 32,
                "sStatusPath": "x", "bAlreadyStarting": False}))

    def fnStatus(routeIntercepted):
        routeIntercepted.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"sState": "SUCCEEDED", "sLeaseId": "lease"}))

    pageDashboard.route(S_START_ROUTE, fnStart)
    pageDashboard.route(
        f"**/api/containers/{S_CONTAINER_NAME}/start-status", fnStatus)
    fnLoadAndPressStart(pageDashboard, serverHub)
    pageDashboard.wait_for_selector(".toast.success", timeout=10000)
    assert pageDashboard.locator(S_MODAL).count() == 0


def testContinueWaitsForAChoiceAndInspectOnlyDisablesCredentials(
    pageDashboard, serverHub,
):
    fnRefuseStartWithThePrompt(pageDashboard, fdictPrompt(), [])
    fnLoadAndPressStart(pageDashboard, serverHub)
    pageDashboard.wait_for_selector(S_MODAL, timeout=10000)
    pageDashboard.check("#imageTrustCredentials")
    pageDashboard.check(f"{S_MODAL} input[value=restricted]")
    assert pageDashboard.locator("#btnImageTrustContinue").is_enabled()
    assert pageDashboard.locator("#imageTrustCredentials").is_enabled()
    pageDashboard.check(f"{S_MODAL} input[value=inspect]")
    assert pageDashboard.locator("#imageTrustCredentials").is_disabled()
    assert not pageDashboard.locator("#imageTrustCredentials").is_checked()


def testEveryDetailsSectionExpands(pageDashboard, serverHub):
    fnRefuseStartWithThePrompt(pageDashboard, fdictPrompt(), [])
    fnLoadAndPressStart(pageDashboard, serverHub)
    pageDashboard.wait_for_selector(S_MODAL, timeout=10000)
    locatorSections = pageDashboard.locator(f"{S_MODAL} details")
    assert locatorSections.count() == 4
    for iIndex in range(4):
        locatorSection = locatorSections.nth(iIndex)
        assert not locatorSection.evaluate("el => el.open")
        locatorSection.locator("summary").click()
        assert locatorSection.evaluate("el => el.open")
        assert locatorSection.locator("li").first.is_visible()


@pytest.mark.falsification
def testTheWordsOnScreenAreTheBackendsWords(pageDashboard, serverHub):
    """Kills: option or credentials text living in the frontend, where it
    could drift from the command line's.

    Every label, summary and detail line the backend module holds must
    appear in the modal, in order.
    """
    fnRefuseStartWithThePrompt(pageDashboard, fdictPrompt(), [])
    fnLoadAndPressStart(pageDashboard, serverHub)
    pageDashboard.wait_for_selector(S_MODAL, timeout=10000)
    listRendered = pageDashboard.locator(S_MODAL).evaluate(
        "el => Array.from(el.querySelectorAll("
        "'.image-trust-label, .image-trust-summary, "
        ".image-trust-credentials-label, .image-trust-details li'))"
        ".map(e => e.textContent)")
    listExpected = []
    for dictOption in imageTrust.LIST_TRUST_OPTIONS:
        listExpected.append(dictOption["sLabel"])
        listExpected.append(dictOption["sSummary"])
        listExpected.extend(dictOption["listDetailLines"])
    listExpected.append(imageTrust.DICT_CREDENTIAL_OPTION["sLabel"])
    listExpected.extend(imageTrust.DICT_CREDENTIAL_OPTION["listDetailLines"])
    assert sorted(listRendered) == sorted(listExpected)


@pytest.mark.falsification
def testAHostileImageDescriptionRendersAsText(pageDashboard, serverHub):
    """Kills: interpolating the image's declared user, entrypoint or
    digest into markup unescaped."""
    sHostile = "\"><img src=x onerror=window.__imageTrustPwned=1>"
    dictPrompt = fdictPrompt(
        sUser=sHostile, listEntrypoint=[sHostile, "'><b>bold</b>"],
        sDigest="sha256:" + sHostile)
    fnRefuseStartWithThePrompt(pageDashboard, dictPrompt, [])
    fnLoadAndPressStart(pageDashboard, serverHub)
    pageDashboard.wait_for_selector(S_MODAL, timeout=10000)
    pageDashboard.wait_for_timeout(500)
    assert pageDashboard.evaluate("window.__imageTrustPwned") is None
    assert pageDashboard.locator(f"{S_MODAL} img").count() == 0
    assert pageDashboard.locator(f"{S_MODAL} b").count() == 0
    assert sHostile in pageDashboard.locator(S_MODAL).inner_text()


def testAnAnswerIsPostedForTheDigestShownThenTheStartIsRetried(
    pageDashboard, serverHub,
):
    listAttempts = []
    listPosted = []
    fnRefuseStartWithThePrompt(pageDashboard, fdictPrompt(), listAttempts)

    def fnRecord(routeIntercepted):
        listPosted.append(json.loads(routeIntercepted.request.post_data))
        routeIntercepted.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"bSuccess": True, "dictImageTrust": {
                "sImageDigest": S_DIGEST, "sChoice": "as-built",
                "bWithCredentials": True}}))

    pageDashboard.route(S_ANSWER_ROUTE, fnRecord)
    fnLoadAndPressStart(pageDashboard, serverHub)
    pageDashboard.wait_for_selector(S_MODAL, timeout=10000)
    pageDashboard.check(f"{S_MODAL} input[value=as-built]")
    pageDashboard.check("#imageTrustCredentials")
    pageDashboard.click("#btnImageTrustContinue")
    for _ in range(50):
        if len(listAttempts) == 2:
            break
        pageDashboard.wait_for_timeout(200)
    assert listPosted == [{
        "sImageDigest": S_DIGEST, "sChoice": "as-built",
        "bWithCredentials": True}]
    assert len(listAttempts) == 2


def testCancelCreatesAndRecordsNothing(pageDashboard, serverHub):
    listAttempts = []
    listPosted = []
    fnRefuseStartWithThePrompt(pageDashboard, fdictPrompt(), listAttempts)
    pageDashboard.route(
        S_ANSWER_ROUTE, lambda routeIntercepted: listPosted.append(1))
    fnLoadAndPressStart(pageDashboard, serverHub)
    pageDashboard.wait_for_selector(S_MODAL, timeout=10000)
    pageDashboard.click("#btnImageTrustCancel")
    pageDashboard.wait_for_selector(S_MODAL, state="detached", timeout=5000)
    pageDashboard.wait_for_timeout(500)
    assert listPosted == [] and len(listAttempts) == 1


def fnForgetTheStoredChoice():
    dictRegistry = registryManager.fdictLoadRegistry()
    for dictEntry in dictRegistry["listProjects"]:
        dictEntry.pop(registryManager.S_IMAGE_TRUST_KEY, None)
    registryManager.fnSaveRegistry(dictRegistry)


@pytest.mark.falsification
def testTheBadgeAndTheSettingsReflectTheStoredChoice(
    pageDashboard, serverHub,
):
    """Kills: a badge that does not read the stored record."""
    sConfigPath = registryManager.fdictGetProject(
        S_CONTAINER_NAME)["sConfigPath"]
    os.makedirs(os.path.dirname(sConfigPath), exist_ok=True)
    with open(sConfigPath, "w") as fileConfig:
        fileConfig.write(f"projectName: {S_CONTAINER_NAME}\n")
    registryManager.fnRecordImageTrust(
        S_CONTAINER_NAME, imageTrust.fdictBuildTrustRecord(
            S_DIGEST, "restricted", False))
    try:
        pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
        pageDashboard.wait_for_selector(S_TILE, timeout=15000)
        sBadge = pageDashboard.locator(
            f"{S_TILE} .containment-chip--imagetrust").inner_text()
        assert sBadge == "image trust: restricted"
        pageDashboard.click(f"{S_TILE} .container-tile-gear")
        pageDashboard.wait_for_selector("#modalSettings", timeout=10000)
        assert pageDashboard.locator(
            "#settingImageTrust").inner_text() == "image trust: restricted"
    finally:
        fnForgetTheStoredChoice()


def testAFailedRestrictedStartIsShownAndOffersTheOtherChoices(
    pageDashboard, serverHub,
):
    """The failure is on screen as it happened, never as a running state,
    and the way out is the same modal."""
    sFailure = (
        "Container 'x' was created and started, but it is not running -- "
        "its entrypoint exited immediately.")
    dictPrompt = fdictPrompt()
    dictPrompt["dictCurrentAnswer"] = {
        "sImageDigest": S_DIGEST, "sChoice": "restricted"}

    def fnStart(routeIntercepted):
        routeIntercepted.fulfill(
            status=202, content_type="application/json",
            body=json.dumps({
                "sName": S_CONTAINER_NAME, "sReservationId": "r" * 32,
                "sStatusPath": "x", "bAlreadyStarting": False}))

    pageDashboard.route(S_START_ROUTE, fnStart)
    pageDashboard.route(
        f"**/api/containers/{S_CONTAINER_NAME}/start-status",
        lambda routeIntercepted: routeIntercepted.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({
                "sState": "FAILED", "sError": sFailure,
                "sReservationId": "r" * 32})))
    pageDashboard.route(
        S_ANSWER_ROUTE, lambda routeIntercepted: routeIntercepted.fulfill(
            status=200, content_type="application/json",
            body=json.dumps(dictPrompt)))
    fnLoadAndPressStart(pageDashboard, serverHub)
    pageDashboard.wait_for_selector(".toast.error", timeout=10000)
    assert "not running" in pageDashboard.locator(
        ".toast.error").first.inner_text()
    pageDashboard.wait_for_selector(S_MODAL, timeout=10000)
    assert "did not start restricted" in pageDashboard.locator(
        f"{S_MODAL} .image-trust-notice").inner_text()
    assert pageDashboard.locator(
        f"{S_TILE} .status-dot.status-running").count() == 0
