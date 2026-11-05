"""A vaibify.yml that widens what the container reaches is not started until approved.

Driven in a real browser against the real hub, with the start route's
refusal supplied by the same backend module the command line reads
(ruling D2). Asserted here: the confirm-reach refusal opens a modal
whose words are the server's, Cancel records nothing and retries
nothing, and Approve posts the fingerprint that was shown and then
retries the start.
"""

import json
from types import SimpleNamespace

import pytest

from tests.browser.conftest import S_CONTAINER_NAME
from vaibify.config import reachApproval

pytestmark = pytest.mark.browser

S_TILE = f'.container-tile[data-name="{S_CONTAINER_NAME}"]'
S_MODAL = "#modalConfirm"
S_START_ROUTE = f"**/api/containers/{S_CONTAINER_NAME}/start"
S_APPROVAL_ROUTE = f"**/api/registry/{S_CONTAINER_NAME}/reach-approval"


def fdictPrompt():
    return reachApproval.fdictBuildReachPrompt(SimpleNamespace(
        sNetworkMode="open", bX11Forwarding=True, listBindMounts=[],
        listAllowedHosts=["data.example.org"]), None)


def fnRefuseStartWithThePrompt(page, dictPrompt, listAttempts):
    def fnHandle(routeIntercepted):
        listAttempts.append(1)
        dictDetail = {
            "sMessage": "approve what this container may reach",
            "sAction": "confirm-reach", "dictReachPrompt": dictPrompt,
        }
        routeIntercepted.fulfill(
            status=409, content_type="application/json",
            body=json.dumps({"detail": dictDetail, **dictDetail}))

    page.route(S_START_ROUTE, fnHandle)


def fnLoadAndPressStart(page, serverHub):
    page.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    page.wait_for_selector(S_TILE, timeout=15000)
    page.click(f"{S_TILE} .container-tile-actions")
    page.wait_for_selector(
        f"{S_TILE} .container-tile-menu", state="visible", timeout=5000)
    page.click(f'{S_TILE} [data-action="start"]')


@pytest.mark.falsification
def testTheModalShowsTheServersWordsAndApproveRetriesTheStart(
    pageDashboard, serverHub,
):
    """Kills: the frontend ignoring the confirm-reach refusal (the start
    would end in a bare error and the question is never asked)."""
    listAttempts = []
    listPosted = []
    dictPrompt = fdictPrompt()
    fnRefuseStartWithThePrompt(pageDashboard, dictPrompt, listAttempts)

    def fnRecord(routeIntercepted):
        listPosted.append(json.loads(routeIntercepted.request.post_data))
        routeIntercepted.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"bSuccess": True, "dictReachApproval": {
                "sReachFingerprint": dictPrompt["sReachFingerprint"]}}))

    pageDashboard.route(S_APPROVAL_ROUTE, fnRecord)
    fnLoadAndPressStart(pageDashboard, serverHub)
    pageDashboard.wait_for_selector(S_MODAL, timeout=10000)
    sText = pageDashboard.locator(S_MODAL).inner_text()
    assert dictPrompt["sTitle"] in sText
    for sBullet in dictPrompt["listBullets"]:
        assert sBullet in sText
    assert pageDashboard.locator("#btnConfirmOk").inner_text() == "Approve and start"
    pageDashboard.click("#btnConfirmOk")
    for _iPoll in range(50):
        if len(listAttempts) == 2:
            break
        pageDashboard.wait_for_timeout(200)
    assert listPosted == [{"sReachFingerprint": dictPrompt["sReachFingerprint"]}]
    assert len(listAttempts) == 2
    assert pageDashboard.listPageErrors == []


def testCancelRecordsNothingAndRetriesNothing(pageDashboard, serverHub):
    listAttempts = []
    listPosted = []
    fnRefuseStartWithThePrompt(pageDashboard, fdictPrompt(), listAttempts)
    pageDashboard.route(
        S_APPROVAL_ROUTE, lambda routeIntercepted: listPosted.append(1))
    fnLoadAndPressStart(pageDashboard, serverHub)
    pageDashboard.wait_for_selector(S_MODAL, timeout=10000)
    pageDashboard.click("#btnConfirmCancel")
    pageDashboard.wait_for_selector(S_MODAL, state="detached", timeout=5000)
    pageDashboard.wait_for_selector(".toast", timeout=5000)
    assert "was not started" in pageDashboard.locator(".toast").first.inner_text()
    pageDashboard.wait_for_timeout(500)
    assert listPosted == [] and len(listAttempts) == 1
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testAHostilePromptRendersAsText(pageDashboard, serverHub):
    """Kills: interpolating the prompt's text into markup unescaped."""
    sHostile = "\"><img src=x onerror=window.__reachPwned=1>"
    dictPrompt = fdictPrompt()
    dictPrompt["sText"] = sHostile
    dictPrompt["sTitle"] = sHostile
    fnRefuseStartWithThePrompt(pageDashboard, dictPrompt, [])
    fnLoadAndPressStart(pageDashboard, serverHub)
    pageDashboard.wait_for_selector(S_MODAL, timeout=10000)
    pageDashboard.wait_for_timeout(500)
    assert pageDashboard.evaluate("window.__reachPwned") is None
    assert pageDashboard.locator(f"{S_MODAL} img").count() == 0
    assert sHostile in pageDashboard.locator(S_MODAL).inner_text()
