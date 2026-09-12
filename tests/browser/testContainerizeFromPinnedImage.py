"""Containerizing from the author's pinned image, in a browser.

The wizard's Environment page is where a researcher chooses between
obtaining the image a clone's envelope pins and building from the
Dockerfile, and where a clone that pins nothing obtainable is told so.
These tests drive the real wizard against faked backend answers,
because what matters is ORDER and ROUTING, not wording: no convert
request and no acquisition before the page has been seen and Convert
clicked; the acquire route, never ``/build``, after a conversion that
says the image is obtained; and a not-built tile whose registry entry
says the image is obtained calling acquire-image, never ``/build``.
"""

import json

import pytest

from tests.browser.conftest import S_HOST_PROJECT_READY
from tests.browser.fakeDockerAdapter import S_CONTAINER_NAME


pytestmark = pytest.mark.browser

S_NEW_NAME = "pinned-clone-box"
S_PIN = "registry.example/project@sha256:" + "a" * 64


def _fdictPinnedAnswer(bObtainable=True, bMatches=True):
    return {
        "bObtainable": bObtainable,
        "sRefusal": "" if bObtainable else (
            "rule 2 (environment envelope): .vaibify/environment.json "
            "records no image architecture"
        ),
        "sPinnedImageReference": S_PIN if bObtainable else "",
        "sRequiredPlatform": "linux/amd64" if bObtainable else "",
        "bDepositOnRecord": True,
        "sDepositVersionDoi": "10.5072/zenodo.1",
        "sZenodoService": "sandbox",
        "dictDaemon": {
            "bReachable": True, "sArchitecture": "amd64" if bMatches else "arm64",
            "sRequiredPlatform": "linux/amd64", "bArchitectureMatches": bMatches,
        },
        "dictAuthorFeatures": {"claude": True, "latex": True, "codex": False},
        "listAgentOverlays": [
            "claude", "codex", "gemini", "antigravity", "opencode", "cline",
            "openhands", "pi",
        ],
        "listBaseFeatureKeys": [
            "jupyter", "rLanguage", "julia", "database", "dvc",
            "nestedSampling", "latex", "gpu",
        ],
    }


def _fnFakePinnedEnvironment(page, dictAnswer):
    page.route(
        "**/api/registry/**/pinned-environment",
        lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps(dictAnswer),
        ),
    )


def _flistRecordConvertAndHandOffs(page, bAcquireRequired):
    """Answer the convert POST and the two hand-offs; record every request."""
    listRequests = []

    def fnConvert(route):
        listRequests.append(("convert", json.loads(route.request.post_data)))
        route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({
                "sName": S_NEW_NAME, "sMode": "container",
                "bBuildRequired": not bAcquireRequired,
                "bAcquireRequired": bAcquireRequired,
                "sAcquirePath": f"/api/containers/{S_NEW_NAME}/acquire-image",
                "sBuildPath": f"/api/containers/{S_NEW_NAME}/build",
            }),
        )

    def fnAcquire(route):
        listRequests.append(("acquire", route.request.url))
        route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"bSuccess": True, "sMessage": "Image obtained",
                             "dictImageOrigin": {}}),
        )

    def fnBuild(route):
        listRequests.append(("build", route.request.url))
        route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"bSuccess": True, "sMessage": "Build complete"}),
        )

    def fnStart(route):
        listRequests.append(("start", route.request.url))
        route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"bSuccess": True}),
        )

    page.route("**/api/registry/**/convert-to-container", fnConvert)
    page.route("**/api/containers/**/acquire-image**", fnAcquire)
    page.route("**/api/containers/**/build", fnBuild)
    page.route("**/api/containers/**/build/progress", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"bKnown": False, "bLive": False, "saTailLines": [],
                         "iLineCount": 0, "sOutcome": ""}),
    ))
    page.route("**/api/containers/**/start", fnStart)
    return listRequests


def _fnWaitForPicker(page, serverHub):
    page.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    page.wait_for_selector(
        f'.container-tile[data-name="{S_CONTAINER_NAME}"]', timeout=10000,
    )


def _fnOpenConvertWizard(page):
    page.click(
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"] '
        '.container-tile-actions',
    )
    page.click(
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"] '
        '.container-menu-item[data-action="convert"]',
    )
    page.wait_for_selector("#modalCreateWizard", timeout=5000)
    page.fill("#inputWizardProjectName", S_NEW_NAME)


def _fnNextTo(page, sTitle):
    page.click("#btnWizardNext")
    page.wait_for_function(
        "sTitle => document.querySelector('#wizardStepTitle')"
        ".textContent.trim() === sTitle",
        arg=sTitle, timeout=5000,
    )


def _fsTitle(page):
    return page.text_content("#wizardStepTitle").strip()


def _fnClickNextPastAnyAgentWarning(page):
    page.click("#btnWizardNext")
    page.wait_for_timeout(200)
    elModal = page.query_selector("#modalConfirm")
    if elModal and elModal.is_visible() and "coding agent" in (
        page.text_content("#modalConfirm") or ""
    ):
        page.click("#btnConfirmOk")
        page.wait_for_timeout(200)


def testTheEnvironmentPageFollowsNameAndShowsTheRefusal(
    pageDashboard, serverHub,
):
    """The page is ALWAYS there, and an unobtainable clone reads why."""
    _fnFakePinnedEnvironment(pageDashboard, _fdictPinnedAnswer(bObtainable=False))
    _fnWaitForPicker(pageDashboard, serverHub)
    _fnOpenConvertWizard(pageDashboard)
    assert _fsTitle(pageDashboard) == "Project Name"
    _fnNextTo(pageDashboard, "Environment")
    pageDashboard.wait_for_selector(".wizard-environment-refusal", timeout=5000)
    elPinned = pageDashboard.query_selector(
        '.wizard-environment-source[value="archive"]',
    )
    assert elPinned is not None and elPinned.is_disabled()
    assert "records no image architecture" in pageDashboard.text_content(
        ".wizard-environment-refusal",
    )
    assert pageDashboard.query_selector("#wizardAllowEmulation") is None
    elBuild = pageDashboard.query_selector(
        '.wizard-environment-source[value="build"]',
    )
    assert elBuild.is_checked()
    _fnNextTo(pageDashboard, "Python Version")
    assert pageDashboard.listPageErrors == []


def testChoosingThePinnedImageDropsTheBuildPagesAndPinsTheAuthorsAgents(
    pageDashboard, serverHub,
):
    """Python, Repositories and Packages vanish; the author's agents are fixed."""
    _fnFakePinnedEnvironment(
        pageDashboard, _fdictPinnedAnswer(bObtainable=True, bMatches=False),
    )
    _fnWaitForPicker(pageDashboard, serverHub)
    _fnOpenConvertWizard(pageDashboard)
    _fnNextTo(pageDashboard, "Environment")
    pageDashboard.wait_for_selector(".wizard-environment-facts", timeout=5000)
    assert S_PIN in pageDashboard.text_content(".wizard-environment-facts")
    # Emulation is offered only when the architectures differ.
    assert pageDashboard.query_selector("#wizardAllowEmulation") is not None
    pageDashboard.check('.wizard-environment-source[value="archive"]')
    pageDashboard.wait_for_timeout(200)
    _fnNextTo(pageDashboard, "Features & Authentication")
    pageDashboard.wait_for_selector(
        '.wizard-feature-input[data-feature="claude"]', timeout=5000,
    )
    elClaude = pageDashboard.query_selector(
        '.wizard-feature-input[data-feature="claude"]',
    )
    assert elClaude is not None, pageDashboard.inner_html("#wizardStepContent")
    assert elClaude.is_checked() and elClaude.is_disabled()
    assert "installed by the author" in pageDashboard.text_content(
        '.wizard-feature-row:has(.wizard-feature-input[data-feature="claude"])',
    )
    elGemini = pageDashboard.query_selector(
        '.wizard-feature-input[data-feature="codex"]',
    )
    assert not elGemini.is_checked() and not elGemini.is_disabled()
    elLatex = pageDashboard.query_selector(
        '.wizard-feature-input[data-feature="latex"]',
    )
    assert elLatex.is_checked() and elLatex.is_disabled()
    _fnClickNextPastAnyAgentWarning(pageDashboard)
    assert _fsTitle(pageDashboard) == "Files to Copy"
    _fnClickNextPastAnyAgentWarning(pageDashboard)
    assert _fsTitle(pageDashboard) == "Summary"
    assert "pinned image" in pageDashboard.text_content("#wizardStepContent")
    assert pageDashboard.listPageErrors == []


def testNothingIsRequestedBeforeConvertAndThenAcquireNotBuild(
    pageDashboard, serverHub,
):
    """ORDER: no convert or acquire request until Convert; then acquire, never /build."""
    _fnFakePinnedEnvironment(pageDashboard, _fdictPinnedAnswer(bObtainable=True))
    listRequests = _flistRecordConvertAndHandOffs(pageDashboard, True)
    _fnWaitForPicker(pageDashboard, serverHub)
    _fnOpenConvertWizard(pageDashboard)
    _fnNextTo(pageDashboard, "Environment")
    pageDashboard.wait_for_selector(".wizard-environment-facts", timeout=5000)
    pageDashboard.check('.wizard-environment-source[value="archive"]')
    pageDashboard.wait_for_timeout(200)
    _fnNextTo(pageDashboard, "Features & Authentication")
    pageDashboard.check('.wizard-feature-input[data-feature="codex"]')
    _fnClickNextPastAnyAgentWarning(pageDashboard)
    _fnClickNextPastAnyAgentWarning(pageDashboard)
    assert _fsTitle(pageDashboard) == "Summary"
    assert listRequests == [], (
        "a request left the page before the researcher clicked Convert"
    )
    assert pageDashboard.text_content("#btnWizardNext").strip() == "Convert"
    pageDashboard.click("#btnWizardNext")
    # The confirm modal sits between Convert and the request, and it
    # names what starts next -- an acquisition, not a build.
    pageDashboard.wait_for_selector("#modalConfirm", timeout=5000)
    assert listRequests == [], "a request left before the confirm"
    sConfirmBody = pageDashboard.text_content("#modalConfirm")
    assert "pinned image" in sConfirmBody
    pageDashboard.click("#btnConfirmOk")
    pageDashboard.wait_for_timeout(1500)
    listKinds = [tRequest[0] for tRequest in listRequests]
    assert listKinds[0] == "convert"
    dictBody = listRequests[0][1]
    assert dictBody["sEnvironmentSource"] == "archive"
    assert dictBody["bAllowEmulation"] is False
    assert "claude" in dictBody["listFeatures"]
    assert "codex" in dictBody["listFeatures"]
    assert "acquire" in listKinds
    assert "build" not in listKinds
    sAcquireUrl = [t[1] for t in listRequests if t[0] == "acquire"][0]
    assert "bAllowEmulation=false" in sAcquireUrl


def testANotBuiltTileWhoseEntrySaysArchiveCallsAcquireNeverBuild(
    pageDashboard, serverHub,
):
    """The tile branches on the registry entry, not on a remembered answer."""
    listRequests = _flistRecordConvertAndHandOffs(pageDashboard, True)

    def fnListing(route):
        response = route.fetch()
        dictListing = json.loads(response.text())
        for dictProject in dictListing.get("listContainers") or []:
            if dictProject.get("sName") == S_CONTAINER_NAME:
                dictProject["sStatus"] = "not built"
                dictProject["bImageExists"] = False
                dictProject["dictImageSource"] = {
                    "sSource": "archive", "bAllowEmulation": True,
                    "sPinnedImageReference": S_PIN,
                    "sRequiredPlatform": "linux/amd64",
                    "listAuthorOverlays": [], "listAdditionalAgents": [],
                    "listResolvedOverlays": None,
                }
        route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps(dictListing),
        )
    pageDashboard.route("**/api/registry", fnListing)
    _fnWaitForPicker(pageDashboard, serverHub)
    pageDashboard.wait_for_selector(
        f'.container-tile[data-name="{S_CONTAINER_NAME}"]'
        '[data-image-source="archive"]', timeout=10000,
    )
    elMenu = pageDashboard.query_selector(
        f'.container-tile[data-name="{S_CONTAINER_NAME}"] '
        '.container-menu-item[data-action="rebuild"]',
    )
    assert elMenu is None, "an obtained image was offered a plain Rebuild"
    assert pageDashboard.query_selector(
        f'.container-tile[data-name="{S_CONTAINER_NAME}"] '
        '.container-menu-item[data-action="switch-to-building"]',
    ) is not None
    pageDashboard.click(
        f'.container-tile[data-name="{S_CONTAINER_NAME}"] .container-tile-main',
    )
    pageDashboard.wait_for_timeout(1500)
    listKinds = [tRequest[0] for tRequest in listRequests]
    assert "acquire" in listKinds
    assert "build" not in listKinds
    sAcquireUrl = [t[1] for t in listRequests if t[0] == "acquire"][0]
    assert "bAllowEmulation=true" in sAcquireUrl



# ---------------------------------------------------------------------
# A transition goes no further than a stop that failed, and a refusal
# offers its recovery (review findings, 2026-09-12)
# ---------------------------------------------------------------------


def _fnListTheTileAsObtained(page, sStatus, listAdditionalAgents=None):
    """Make the fake tile an obtained project in the given state."""
    def fnListing(route):
        response = route.fetch()
        dictListing = json.loads(response.text())
        for dictProject in dictListing.get("listContainers") or []:
            if dictProject.get("sName") == S_CONTAINER_NAME:
                dictProject["sStatus"] = sStatus
                dictProject["bImageExists"] = sStatus != "not built"
                dictProject["dictImageSource"] = {
                    "sSource": "archive", "bAllowEmulation": False,
                    "sPinnedImageReference": S_PIN,
                    "sRequiredPlatform": "linux/amd64",
                    "listAuthorOverlays": ["claude"],
                    "listAdditionalAgents": list(listAdditionalAgents or []),
                    "listResolvedOverlays": None,
                }
        route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps(dictListing),
        )
    page.route("**/api/registry", fnListing)


def _flistRecordLifecycle(page, bStopSucceeds):
    """Record stop, switch, acquire, build and start requests in order."""
    listRequests = _flistRecordConvertAndHandOffs(page, True)

    def fnStop(route):
        listRequests.append(("stop", route.request.url))
        if bStopSucceeds:
            route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps({"bSuccess": True}),
            )
        else:
            route.fulfill(
                status=500, content_type="application/json",
                body=json.dumps({"detail": "Stop failed: the daemon refused"}),
            )

    def fnSwitch(route):
        listRequests.append(("switch", route.request.url))
        route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"bSwitched": True, "sBuildPath": "/x"}),
        )
    page.route("**/api/containers/**/stop", fnStop)
    page.route("**/api/containers/**/switch-to-building", fnSwitch)
    return listRequests


def _fnClickTileMenuAction(page, sAction):
    page.wait_for_selector(
        f'.container-tile[data-name="{S_CONTAINER_NAME}"]'
        '[data-image-source="archive"]', timeout=10000,
    )
    page.click(
        f'.container-tile[data-name="{S_CONTAINER_NAME}"] '
        '.container-tile-actions',
    )
    page.click(
        f'.container-tile[data-name="{S_CONTAINER_NAME}"] '
        f'.container-menu-item[data-action="{sAction}"]',
    )
    page.wait_for_selector("#modalConfirm", timeout=5000)
    page.click("#btnConfirmOk")
    page.wait_for_timeout(1500)


@pytest.mark.falsification
def testAFailedStopEndsTheReobtainBeforeAnyAcquire(pageDashboard, serverHub):
    """Kills: proceeding to acquire after fnStopContainer reported failure."""
    _fnListTheTileAsObtained(pageDashboard, "running")
    listRequests = _flistRecordLifecycle(pageDashboard, False)
    _fnWaitForPicker(pageDashboard, serverHub)
    _fnClickTileMenuAction(pageDashboard, "reobtain")
    listKinds = [tRequest[0] for tRequest in listRequests]
    assert listKinds == ["stop"], listKinds


@pytest.mark.falsification
def testTheSwitchStopsFirstAndAFailedStopPostsNothing(pageDashboard, serverHub):
    """The stop precedes the switch; a failed stop leaves the entry untouched.

    Kills: posting the switch before the stop, which clears the origin
    record and the registry's image source under a container still
    running the author's image.
    """
    _fnListTheTileAsObtained(pageDashboard, "running")
    listRequests = _flistRecordLifecycle(pageDashboard, False)
    _fnWaitForPicker(pageDashboard, serverHub)
    _fnClickTileMenuAction(pageDashboard, "switch-to-building")
    assert [tRequest[0] for tRequest in listRequests] == ["stop"]
    pageDashboard.unroute("**/api/containers/**/stop")
    pageDashboard.unroute("**/api/containers/**/switch-to-building")
    listOrdered = _flistRecordLifecycle(pageDashboard, True)
    _fnClickTileMenuAction(pageDashboard, "switch-to-building")
    listKinds = [tRequest[0] for tRequest in listOrdered]
    assert listKinds[:3] == ["stop", "switch", "build"], listKinds


@pytest.mark.falsification
def testAnUnprovenBaselineOffersTheRetryWithoutAdditions(pageDashboard, serverHub):
    """The refusal names its recovery and the page OFFERS it.

    Kills: reporting the refusal as an ordinary failure, which leaves
    the researcher with a sentence and no lane to act on it.
    """
    _fnListTheTileAsObtained(pageDashboard, "not built", ["gemini"])
    listRequests = _flistRecordConvertAndHandOffs(pageDashboard, True)
    pageDashboard.unroute("**/api/containers/**/acquire-image**")
    listAcquireUrls = []

    def fnAcquire(route):
        listAcquireUrls.append(route.request.url)
        if len(listAcquireUrls) == 1:
            route.fulfill(
                status=500, content_type="application/json",
                body=json.dumps({"detail": {
                    "sMessage": "Build failed",
                    "sError": "the obtained image carries no overlays label",
                    "sStderrTail": "",
                    "sAction": "reobtain-without-additions",
                }}),
            )
            return
        route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"bSuccess": True, "dictImageOrigin": {}}),
        )
    pageDashboard.route("**/api/containers/**/acquire-image**", fnAcquire)
    _fnWaitForPicker(pageDashboard, serverHub)
    pageDashboard.wait_for_selector(
        f'.container-tile[data-name="{S_CONTAINER_NAME}"]'
        '[data-image-source="archive"]', timeout=10000,
    )
    pageDashboard.click(
        f'.container-tile[data-name="{S_CONTAINER_NAME}"] .container-tile-main',
    )
    pageDashboard.wait_for_selector("#modalConfirm", timeout=5000)
    sOffer = pageDashboard.text_content("#modalConfirm")
    assert "without the added agents" in sOffer
    assert len(listAcquireUrls) == 1
    pageDashboard.click("#btnConfirmOk")
    pageDashboard.wait_for_timeout(1500)
    assert len(listAcquireUrls) == 2
    assert "bWithoutAdditions=true" in listAcquireUrls[1]
    assert "bWithoutAdditions" not in listAcquireUrls[0]
    assert "build" not in [tRequest[0] for tRequest in listRequests]
