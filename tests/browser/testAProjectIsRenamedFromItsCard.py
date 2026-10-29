"""A project is renamed from its card, and an open menu outlives a poll.

The Project hub repaints its cards every three seconds. A menu opened
on a card lives inside that card, so a repaint closes it -- and a click
the researcher was already making lands on a replacement element and
does nothing (the PR #187 trap). The poll therefore HOLDS a changed
listing while a card's menu is open and draws it when the menu closes.

To make the hold observable, every poll answer is rewritten so its
listing differs from the last one (the size field grows each tick):
without the hold, the first tick after the menu opens repaints the
list and the menu is gone.

Then the rename itself is driven end to end against the seeded host
project's real file: the dry run's confirmation is read, Rename is
clicked, and the card and the file both carry the new name.

One session, because one is all there is: a second test in this module
would wait on the claim this one still holds.

Kills: dropping the open-menu hold from ``fnRenderWorkflowList``, so a
changed listing repaints the cards under the open menu.
"""

import json
import os

import pytest

from tests.browser.conftest import (
    S_HOST_PROJECT_READY,
    S_HOST_WORKFLOW_NAME,
)


pytestmark = pytest.mark.browser

I_SPAN_SEVERAL_POLL_TICKS_MILLISECONDS = 8000
S_NEW_NAME = "hostLaneRenamed"
S_CARD = "#listWorkflows .container-card"


def _fnMakeEveryListingDiffer(pageDashboard):
    """Grow each listed project's size by one per answer."""
    dictTick = {"iCount": 0}

    def fnHandle(routeList):
        if routeList.request.method != "GET":
            routeList.continue_()
            return
        responseList = routeList.fetch()
        listProjects = responseList.json()
        dictTick["iCount"] += 1
        for dictProject in listProjects:
            dictProject["iSizeBytes"] = (
                (dictProject.get("iSizeBytes") or 0) + dictTick["iCount"])
        routeList.fulfill(
            response=responseList, body=json.dumps(listProjects),
            headers={"content-type": "application/json"})

    pageDashboard.route("**/api/workflows/*", fnHandle)
    return dictTick


def _fnOpenTheProjectHub(pageDashboard, serverHub):
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_selector(
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"]',
        timeout=15000,
    )
    pageDashboard.click(
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"] '
        '.container-tile-main',
    )
    pageDashboard.wait_for_selector("#modalConfirm", timeout=10000)
    pageDashboard.click("#btnConfirmOk")
    pageDashboard.wait_for_selector(
        f"text={S_HOST_WORKFLOW_NAME}", timeout=20000,
    )


def _fsSeededProjectFile(pageDashboard):
    """Return the host path of the seeded project file the card names."""
    return pageDashboard.locator(S_CARD).first.get_attribute("data-path")


@pytest.mark.falsification
def test_a_project_is_renamed_from_its_card_menu(pageDashboard, serverHub):
    """Kills: dropping the open-menu hold from fnRenderWorkflowList."""
    dictTick = _fnMakeEveryListingDiffer(pageDashboard)
    _fnOpenTheProjectHub(pageDashboard, serverHub)
    pageDashboard.click(S_CARD + " .project-card-actions")
    pageDashboard.wait_for_selector(
        S_CARD + " .project-card-menu.is-open", timeout=5000)
    pageDashboard.evaluate(
        "() => { document.querySelector('" + S_CARD + "')"
        ".dataset.markedWithItsMenuOpen = 'yes'; }")
    iTicksBefore = dictTick["iCount"]
    pageDashboard.wait_for_timeout(I_SPAN_SEVERAL_POLL_TICKS_MILLISECONDS)
    assert dictTick["iCount"] > iTicksBefore, (
        "no poll answered during the wait, so nothing tested the hold")
    assert pageDashboard.evaluate(
        "() => { const el = document.querySelector('" + S_CARD + "');"
        " return Boolean(el && el.dataset.markedWithItsMenuOpen === 'yes'"
        " && el.querySelector('.project-card-menu.is-open')); }"), (
        "a poll repainted the project cards under an open menu, closing "
        "it and replacing the card the researcher was clicking")

    sProjectFile = _fsSeededProjectFile(pageDashboard)
    pageDashboard.click(
        S_CARD + ' .project-card-menu [data-action="rename"]')
    pageDashboard.wait_for_selector("#modalInput", timeout=5000)
    pageDashboard.fill("#modalInput .input-modal-field", S_NEW_NAME)
    pageDashboard.click("#btnInputConfirm")
    pageDashboard.wait_for_selector("#modalConfirm", timeout=20000)
    sConfirmText = pageDashboard.text_content("#modalConfirm")
    assert S_HOST_WORKFLOW_NAME in sConfirmText
    assert S_NEW_NAME in sConfirmText
    assert "PROOF" in sConfirmText or "invalidates" in sConfirmText, (
        f"the confirmation says nothing about invalidation: {sConfirmText}")
    pageDashboard.unroute_all(behavior="ignoreErrors")
    pageDashboard.click("#btnConfirmOk")
    pageDashboard.wait_for_selector(
        f"{S_CARD} .name:text-is('{S_NEW_NAME}')", timeout=20000)
    with open(sProjectFile, encoding="utf-8") as fileProject:
        assert json.load(fileProject)["sWorkflowName"] == S_NEW_NAME
    assert os.path.isfile(sProjectFile)
    assert pageDashboard.listPageErrors == []
