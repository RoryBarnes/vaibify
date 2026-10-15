"""A click survives a panel rebuilt between its mousedown and its mouseup.

The browser fires ``click`` only when the mousedown and the mouseup
land on the same element. Two panels rebuild themselves through
``innerHTML`` when a fetch or a poll answers, and on 2026-10-14 both
answered inside a click: the AI Declaration's file-state answer
replaced the sign-off row (Firefox trace: fetches answered +9 ms and
+25 ms after mousedown, mouseup at +29 ms) and a council poll replaced
the chat's Ask button. No handler ran and nothing was sent, and a
human click is about as wide as a Playwright one.

Each test presses the pointer, forces the rebuild that raced through
the module's REAL path while the pointer is down, releases, and asks
the server whether the click arrived. The fix holds every such render
until the pointer lifts; a render that goes through at once replaces
the element and these tests see no request.
"""

import os

import pytest

from tests.browser.conftest import (
    S_HOST_DECLARATION_STEP_NAME,
    S_HOST_PROJECT_READY,
    fnOpenTheSeededHostWorkflow,
)
from .testCouncilChairbotChat import (  # noqa: F401 -- fixtures
    _fnScriptChatRunner,
)
from .testCouncilPlanningJourney import (  # noqa: F401 -- fixtures
    _fdictClaimAndActivate,
    _fnConveneThroughTheForm,
    _fnScriptedProviderSeam,
    _fnIsolateCouncilStore,
)


pytestmark = pytest.mark.browser

S_DECLARATION_BODY = (
    f'.step-wrapper:has-text("{S_HOST_DECLARATION_STEP_NAME}")'
)
S_SIGN_OFF_ROW = (
    S_DECLARATION_BODY + ' .ai-declaration-attestation '
    '.verification-row[data-approver="user"]'
)


def _fnPressTheCenterOf(pageDashboard, sSelector):
    """Press the pointer on the element and stamp that very element.

    The stamp is what makes the hold observable in every browser: a
    rebuild under the press replaces the element, and the replacement
    carries no stamp. Chromium delivers a click to the common ancestor
    of the press and the release, so the click alone can look as if it
    survived a rebuild it did not.
    """
    dictBox = pageDashboard.locator(sSelector).first.bounding_box()
    assert dictBox is not None, f"{sSelector} has no box"
    pageDashboard.mouse.move(
        dictBox["x"] + dictBox["width"] / 2, dictBox["y"] + dictBox["height"] / 2,
    )
    pageDashboard.mouse.down()
    assert pageDashboard.evaluate("VaibifyUtilities.fbPointerIsDownForTest()")
    pageDashboard.locator(sSelector).first.evaluate(
        "element => { element.dataset.pressStamp = 'under the pointer'; }")


def _fnWaitForTheRebuildToBeRequested(pageDashboard, sSelector):
    """Wait until the forced rebuild has reached the render path.

    A fixed pause is not enough on a loaded runner: when the fetch
    answers only after the pointer is released, a build that renders at
    once and a build that holds look the same, and the mutation
    survives. So the release waits for one of two facts: a render is
    being HELD (the fix), or the stamped element is already gone (the
    render went through at once). Either way the rebuild was requested
    while the pointer was down.
    """
    pageDashboard.wait_for_function(
        "sSelector => VaibifyUtilities.fiHeldRendersForTest() > 0"
        " || !(document.querySelector('[data-press-stamp]'))",
        arg=sSelector, timeout=15000,
    )


def _fnReleaseOnTheStampedElement(pageDashboard, sSelector):
    """Release the pointer, having checked the pressed element is still there.

    The locator resolves afresh, so a replacement element answers with
    no stamp.
    """
    sStamp = pageDashboard.locator(sSelector).first.evaluate(
        "element => element.dataset.pressStamp || ''")
    assert sStamp == "under the pointer", (
        "the element under the pointer was replaced while the pointer was down")
    pageDashboard.mouse.up()


def _fnSignOffThrough(pageDashboard, listLabels):
    for sLabel in listLabels:
        pageDashboard.click(S_SIGN_OFF_ROW)
        pageDashboard.wait_for_selector(
            S_SIGN_OFF_ROW + f' .verification-badge:has-text("{sLabel}")',
            timeout=15000)


@pytest.mark.falsification
def test_a_sign_off_click_survives_the_file_state_answer_landing_inside_it(
    pageDashboard, serverHub,
):
    """Kills: rendering the declaration row's fetched state at once,
    under a pressed pointer, so the row under the click is replaced.
    """
    sProject = os.path.join(serverHub.sHome, S_HOST_PROJECT_READY)
    sDeclaration = os.path.join(sProject, "AI_USAGE.md")
    with open(sDeclaration, "w") as fileDeclaration:
        fileDeclaration.write("# AI Usage Declaration\n\nNone.\n")
    try:
        fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
        pageDashboard.click(
            f'.step-item:has-text("{S_HOST_DECLARATION_STEP_NAME}")')
        pageDashboard.wait_for_selector(
            S_DECLARATION_BODY + " .ai-declaration-preview", timeout=15000)
        pageDashboard.wait_for_timeout(500)
        _fnPressTheCenterOf(pageDashboard, S_SIGN_OFF_ROW)
        # The fetch that raced, re-asked through the module's own seam;
        # its answer rebuilds the slot that holds the pressed row.
        pageDashboard.evaluate(
            "VaibifyStepRenderer.fnRefreshAiDeclarationFileSlots()")
        _fnWaitForTheRebuildToBeRequested(pageDashboard, S_SIGN_OFF_ROW)
        _fnReleaseOnTheStampedElement(pageDashboard, S_SIGN_OFF_ROW)
        pageDashboard.wait_for_selector(
            S_SIGN_OFF_ROW + ' .verification-badge:has-text("Passed")',
            timeout=15000)
        _fnSignOffThrough(pageDashboard, ["Failed", "Error", "Untested"])
        assert pageDashboard.listPageErrors == []
    finally:
        if os.path.exists(sDeclaration):
            os.remove(sDeclaration)


@pytest.mark.falsification
def test_an_ask_click_survives_a_council_render_landing_inside_it(
    pageDashboard, serverHub,
):
    """Kills: the council workspace rendering at once under a pressed
    pointer, so the Ask button under the click is replaced.
    """
    _fdictClaimAndActivate(pageDashboard, serverHub)
    _fnConveneThroughTheForm(pageDashboard)
    pageDashboard.click('.council-tab[data-tab="chat"]')
    pageDashboard.wait_for_selector("#btnCouncilChatOpen", timeout=16000)
    pageDashboard.click("#btnCouncilChatOpen")
    pageDashboard.wait_for_selector("#councilChatQuestion", timeout=16000)
    pageDashboard.fill("#councilChatQuestion", "did this click arrive?")
    _fnPressTheCenterOf(pageDashboard, "#btnCouncilChatAsk")
    # The real refresh-and-render path the poll takes, forced while
    # the pointer is down.
    pageDashboard.evaluate("VaibifyAgentCouncil.fnShowWorkspace()")
    _fnWaitForTheRebuildToBeRequested(pageDashboard, "#btnCouncilChatAsk")
    _fnReleaseOnTheStampedElement(pageDashboard, "#btnCouncilChatAsk")
    pageDashboard.wait_for_selector(".council-chat-chairbot", timeout=30000)
    assert pageDashboard.listPageErrors == []
