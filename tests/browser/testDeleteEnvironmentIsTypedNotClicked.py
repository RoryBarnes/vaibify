"""Deleting an environment costs a sentence, not a click, in a real browser.

The kebab's two removals sit one line apart and one of them cannot be
undone, so the gate has to be something a reflex cannot supply. This
drives the real tile, the real menu and the real modal: only the
deletion POST is stubbed, so the path exercised -- render, open, type,
enable, send -- is the production one.

Both halves of the guard are asserted, because either alone would pass
against a broken UI. A test that only checked "the button is disabled
at first" would pass against a button that never enables; one that only
checked "the right phrase sends the POST" would pass against a button
that was never disabled at all.

The host tile is checked in the same test as the container tile. It is
the same menu renderer, and a Delete offered on a host project could
only mean deleting the researcher's own directory -- so its ABSENCE
there is a guarantee, not a detail.

WHAT THIS DOES NOT COVER: the route (that is
testDeleteEnvironmentRoute, over real HTTP) and whether anything is
actually removed from a daemon (nothing in either lane runs a real
docker rm). This proves the dialog reaches the wire with the phrase.
"""

import json

import pytest

from tests.browser.conftest import S_CONTAINER_NAME, S_HOST_PROJECT_READY


pytestmark = pytest.mark.browser

S_DELETE_ROUTE_GLOB = "**/api/registry/*/delete-environment"
S_PHRASE = f"permanently delete {S_CONTAINER_NAME}"


def _fnStubDeletion(page, listPostBodies):
    """Answer the deletion POST, capturing every body it is sent."""

    def fnHandle(routeIntercepted):
        listPostBodies.append(routeIntercepted.request.post_data_json)
        routeIntercepted.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({
                "sName": S_CONTAINER_NAME,
                "bContainerRemoved": True,
                "listVolumesRemoved": [f"{S_CONTAINER_NAME}-workspace"],
                "listImagesRemoved": [f"{S_CONTAINER_NAME}:latest"],
                "bRegistryEntryRemoved": True,
                "listFailures": [],
            }))

    page.route(S_DELETE_ROUTE_GLOB, fnHandle)


def _fnOpenTheKebab(page, serverHub, sName, bReload=True):
    """Open one tile's actions menu, loading the hub first by default.

    An already-open menu overlays the tiles beside it, so a second
    kebab cannot be clicked until the first is closed. The hub closes
    every menu on any document click, which is what the neutral click
    here is for -- not a workaround, the production behaviour.
    """
    if bReload:
        page.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    else:
        page.mouse.click(2, 2)
    sTile = f'.container-tile[data-name="{sName}"]'
    page.wait_for_selector(sTile, timeout=15000)
    page.click(f"{sTile} .container-tile-actions")
    page.wait_for_selector(f"{sTile} .container-tile-menu", state="visible",
                           timeout=5000)
    return sTile


def testDeleteIsOfferedOnAContainerTileAndNeverOnAHostTile(
    pageDashboard, serverHub,
):
    """The action exists where it has a meaning, and nowhere else."""
    sTile = _fnOpenTheKebab(pageDashboard, serverHub, S_CONTAINER_NAME)
    assert pageDashboard.locator(
        f'{sTile} [data-action="delete-environment"]').count() == 1
    # The same tile still offers the non-destructive removal, so the
    # two are a pair rather than a replacement.
    assert pageDashboard.locator(
        f'{sTile} [data-action="remove"]').count() == 1
    # The two removals sit one line apart and only one cannot be
    # undone, so they must not be painted the same. BOTH halves are
    # asserted: a test that only checked Delete's marking would pass
    # against a menu where every item was marked, which destroys the
    # distinction the marking exists to draw.
    assert pageDashboard.eval_on_selector(
        f'{sTile} [data-action="delete-environment"]',
        "el => el.classList.contains('danger')"
        " && el.classList.contains('container-menu-item--irreversible')",
    ) is True
    assert pageDashboard.eval_on_selector(
        f'{sTile} [data-action="remove"]',
        "el => el.classList.contains('danger')"
        " || el.classList.contains('container-menu-item--irreversible')",
    ) is False
    sHostTile = _fnOpenTheKebab(
        pageDashboard, serverHub, S_HOST_PROJECT_READY, bReload=False)
    assert pageDashboard.locator(
        f'{sHostTile} [data-action="delete-environment"]').count() == 0
    assert pageDashboard.locator(
        f'{sHostTile} [data-action="remove"]').count() == 1
    assert pageDashboard.listPageErrors == []


def testTheDeleteButtonStaysInertUntilThePhraseIsTypedExactly(
    pageDashboard, serverHub,
):
    """Disabled at rest, disabled for a near miss, enabled only exactly."""
    listPostBodies = []
    _fnStubDeletion(pageDashboard, listPostBodies)
    sTile = _fnOpenTheKebab(pageDashboard, serverHub, S_CONTAINER_NAME)
    pageDashboard.click(f'{sTile} [data-action="delete-environment"]')
    pageDashboard.wait_for_selector("#modalTypedConfirm", timeout=10000)
    assert pageDashboard.eval_on_selector(
        "#btnTypedConfirmOk", "el => el.disabled") is True
    # The modal names the environment, so a menu opened on the wrong
    # tile cannot be confirmed from memory.
    assert S_CONTAINER_NAME in pageDashboard.text_content(
        "#modalTypedConfirm")
    for sNearMiss in ("delete", "permanently delete",
                      f"permanently delete {S_CONTAINER_NAME}x",
                      "PERMANENTLY DELETE " + S_CONTAINER_NAME):
        pageDashboard.fill("#typedConfirmField", sNearMiss)
        assert pageDashboard.eval_on_selector(
            "#btnTypedConfirmOk", "el => el.disabled") is True, sNearMiss
    assert listPostBodies == [], "a near miss reached the wire"
    pageDashboard.fill("#typedConfirmField", S_PHRASE)
    pageDashboard.wait_for_function(
        "() => document.getElementById('btnTypedConfirmOk')"
        ".disabled === false", timeout=5000)
    assert pageDashboard.listPageErrors == []


def testConfirmingSendsThePhraseTheServerWillValidate(
    pageDashboard, serverHub,
):
    """The dialog is the courtesy; the phrase on the wire is the gate."""
    listPostBodies = []
    _fnStubDeletion(pageDashboard, listPostBodies)
    sTile = _fnOpenTheKebab(pageDashboard, serverHub, S_CONTAINER_NAME)
    pageDashboard.click(f'{sTile} [data-action="delete-environment"]')
    pageDashboard.wait_for_selector("#modalTypedConfirm", timeout=10000)
    pageDashboard.fill("#typedConfirmField", S_PHRASE)
    pageDashboard.wait_for_function(
        "() => document.getElementById('btnTypedConfirmOk')"
        ".disabled === false", timeout=5000)
    pageDashboard.click("#btnTypedConfirmOk")
    pageDashboard.wait_for_function(
        "() => !document.getElementById('modalTypedConfirm')",
        timeout=10000)
    # Wait on the outcome the researcher sees, not on a clock: the
    # success toast is written only after the POST resolves.
    pageDashboard.wait_for_selector(
        f"text=/'{S_CONTAINER_NAME}' was deleted/", timeout=10000)
    assert listPostBodies, "confirming never issued the POST"
    assert listPostBodies[-1]["sConfirmation"] == S_PHRASE
    assert pageDashboard.listPageErrors == []


def testARefusedDeletionKeepsTheOverleafMirrorRecord(
    pageDashboard, serverHub,
):
    """The mirror is forgotten only after the server agrees.

    This route refuses routinely -- a busy container, another session,
    an unsettled journal -- so a client that forgot the Overleaf
    association first would destroy it for an environment that is
    still there. Driven with a 409, which is the common refusal.
    """
    listForgotten = []
    pageDashboard.route(
        S_DELETE_ROUTE_GLOB,
        lambda routeIntercepted: routeIntercepted.fulfill(
            status=409, content_type="application/json",
            body=json.dumps({"detail": {"sMessage": (
                "open in a browser session right now")}})),
    )
    pageDashboard.route(
        "**/api/overleaf/**",
        lambda routeIntercepted: (
            listForgotten.append(routeIntercepted.request.url),
            routeIntercepted.fulfill(
                status=200, content_type="application/json", body="{}"),
        )[-1],
    )
    sTile = _fnOpenTheKebab(pageDashboard, serverHub, S_CONTAINER_NAME)
    pageDashboard.click(f'{sTile} [data-action="delete-environment"]')
    pageDashboard.wait_for_selector("#modalTypedConfirm", timeout=10000)
    pageDashboard.fill("#typedConfirmField", S_PHRASE)
    pageDashboard.wait_for_function(
        "() => document.getElementById('btnTypedConfirmOk')"
        ".disabled === false", timeout=5000)
    pageDashboard.click("#btnTypedConfirmOk")
    pageDashboard.wait_for_selector(
        "text=/open in a browser session right now/", timeout=10000)
    assert listForgotten == [], (
        "a refused deletion still forgot the Overleaf mirror"
    )


def testEscapeAbandonsTheDialogWithoutDeleting(pageDashboard, serverHub):
    """The way out must not be the way through."""
    listPostBodies = []
    _fnStubDeletion(pageDashboard, listPostBodies)
    sTile = _fnOpenTheKebab(pageDashboard, serverHub, S_CONTAINER_NAME)
    pageDashboard.click(f'{sTile} [data-action="delete-environment"]')
    pageDashboard.wait_for_selector("#modalTypedConfirm", timeout=10000)
    # Even with the phrase already typed, Return must not submit: on a
    # dialog whose purpose is to interrupt a reflex, a keyboard
    # shortcut to finish is the reflex.
    pageDashboard.fill("#typedConfirmField", S_PHRASE)
    pageDashboard.press("#typedConfirmField", "Enter")
    pageDashboard.wait_for_timeout(300)
    assert listPostBodies == [], "Return submitted the deletion"
    pageDashboard.press("#typedConfirmField", "Escape")
    pageDashboard.wait_for_function(
        "() => !document.getElementById('modalTypedConfirm')",
        timeout=5000)
    assert listPostBodies == []
    assert pageDashboard.listPageErrors == []
