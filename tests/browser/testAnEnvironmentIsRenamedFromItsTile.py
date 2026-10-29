"""Renaming an environment from its tile, in a real browser.

Drives the real tile, ⋮ menu, input modal and confirmation. The hub's
listing is rewritten in flight to give tiles the states under test --
running, and a rename in progress -- and the rename route is answered
by a stub, because a real rename copies volumes on a daemon this lane
must never reach. What is asserted:

- a running environment offers Rename only disabled, saying why
  (ruling D2);
- the flow asks for the name, shows the server's dry run -- every move,
  with the copy's size -- and only then sends the apply;
- a tile the hub reports mid-rename says "Renaming to <new>… (copying
  N)" and then "Finishing rename…", from the hub's own words (D3).

WHAT THIS DOES NOT COVER: the route and the transaction, which are
``tests/testEnvironmentRename.py`` and the live
``tests/testEnvironmentRenameLive.py``.
"""

import json

import pytest

from tests.browser.conftest import S_CONTAINER_NAME, S_HOST_PROJECT_READY


pytestmark = pytest.mark.browser

S_NEW_NAME = "renamedHostProject"
S_REGISTRY_GLOB = "**/api/registry"
S_RENAME_GLOB = "**/api/registry/*/rename"
DICT_DRY_RUN = {
    "listMoves": [
        {"sKind": "registry entry", "sFrom": S_HOST_PROJECT_READY,
         "sTo": S_NEW_NAME},
        {"sKind": "volume (copied, then verified)",
         "sFrom": "old-workspace", "sTo": "new-workspace"},
    ],
    "dictVolumeSizes": {"old-workspace": "4.7GB"},
    "sTimeNote": "Each volume is copied and then verified file by file.",
}


@pytest.fixture(autouse=True)
def fixtureStopIntercepting(pageDashboard):
    yield
    pageDashboard.unroute_all(behavior="ignoreErrors")


def _fnRewriteTheListing(page, fnAlter):
    def fnHandle(routeListing):
        responseListing = routeListing.fetch()
        dictBody = responseListing.json()
        for dictTile in dictBody.get("listContainers", []):
            fnAlter(dictTile)
        routeListing.fulfill(
            response=responseListing, body=json.dumps(dictBody),
            headers={"content-type": "application/json"})
    page.route(S_REGISTRY_GLOB, fnHandle)


def _fsOpenTheKebab(page, serverHub, sName):
    page.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    sTile = f'.container-tile[data-name="{sName}"]'
    page.wait_for_selector(sTile, timeout=15000)
    page.click(f"{sTile} .container-tile-actions")
    page.wait_for_selector(f"{sTile} .container-tile-menu", state="visible",
                           timeout=5000)
    return sTile


def test_a_running_environment_offers_rename_only_disabled(
    pageDashboard, serverHub,
):
    _fnRewriteTheListing(pageDashboard, lambda dictTile: (
        dictTile.update(sStatus="running")
        if dictTile.get("sName") == S_CONTAINER_NAME else None))
    sTile = _fsOpenTheKebab(pageDashboard, serverHub, S_CONTAINER_NAME)
    elItem = pageDashboard.locator(
        f'{sTile} .container-menu-item[data-action="rename-disabled"]')
    assert elItem.count() == 1
    assert "Stop the environment" in (elItem.get_attribute("title") or "")
    assert pageDashboard.locator(
        f'{sTile} .container-menu-item[data-action="rename"]').count() == 0
    assert pageDashboard.listPageErrors == []


def test_the_rename_shows_the_dry_run_before_it_sends_the_apply(
    pageDashboard, serverHub,
):
    listBodies = []

    def fnAnswer(routeRename):
        dictBody = routeRename.request.post_data_json
        listBodies.append(dictBody)
        if dictBody.get("bDryRun"):
            routeRename.fulfill(status=200, content_type="application/json",
                                body=json.dumps(DICT_DRY_RUN))
            return
        routeRename.fulfill(status=202, content_type="application/json",
                            body=json.dumps({"bLive": True}))
    pageDashboard.route(S_RENAME_GLOB, fnAnswer)
    sTile = _fsOpenTheKebab(pageDashboard, serverHub, S_HOST_PROJECT_READY)
    pageDashboard.click(
        f'{sTile} .container-menu-item[data-action="rename"]')
    pageDashboard.wait_for_selector("#modalInput", timeout=5000)
    pageDashboard.fill("#modalInput .input-modal-field", S_NEW_NAME)
    pageDashboard.click("#btnInputConfirm")
    pageDashboard.wait_for_selector("#modalConfirm", timeout=5000)
    sConfirm = pageDashboard.text_content("#modalConfirm")
    assert "old-workspace → new-workspace (4.7GB)" in sConfirm
    assert "next time the environment starts" in sConfirm
    assert listBodies == [{"sNewName": S_NEW_NAME, "bDryRun": True}]
    pageDashboard.click("#btnConfirmOk")
    pageDashboard.wait_for_function(
        "() => !document.getElementById('modalConfirm')", timeout=5000)
    pageDashboard.wait_for_timeout(300)
    assert listBodies[-1] == {"sNewName": S_NEW_NAME, "bDryRun": False}
    assert pageDashboard.listPageErrors == []


@pytest.mark.parametrize("sStage,sExpected", [
    ("copying", f"Renaming to {S_NEW_NAME}… (copying 4.7GB)"),
    ("finishing", "Finishing rename…"),
])
def test_a_tile_mid_rename_says_what_the_hub_says(
    pageDashboard, serverHub, sStage, sExpected,
):
    _fnRewriteTheListing(pageDashboard, lambda dictTile: (
        dictTile.update(dictPendingRename={
            "sOldName": S_HOST_PROJECT_READY, "sNewName": S_NEW_NAME,
            "sStage": sStage, "bLive": True, "listCopySizes": ["4.7GB"]})
        if dictTile.get("sName") == S_HOST_PROJECT_READY else None))
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    sTile = f'.container-tile[data-name="{S_HOST_PROJECT_READY}"]'
    pageDashboard.wait_for_selector(f"{sTile} .container-tile-progress",
                                    timeout=15000)
    assert pageDashboard.text_content(
        f"{sTile} .container-tile-progress") == sExpected
    assert pageDashboard.get_attribute(sTile, "data-renaming") == "true"
    assert pageDashboard.listPageErrors == []
