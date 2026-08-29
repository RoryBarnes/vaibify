"""Pause and Stop must not look like the same button.

The researcher's question: "I have to close the laptop and head home.
How do I pause it?" Before this, the only control was "Stop council",
which archives the campaign permanently — so the answer was to abandon
the run or leave it going.

Pause is the non-destructive sibling, and everything that makes it
usable is on the screen: the two controls state opposite consequences,
a pause that has not landed yet says the phase is still finishing, one
that has says the council is standing still, and a PAUSED council must
never be described to the researcher as a hub that crashed. None of
that is visible to the Python suite, which executes no JavaScript.
"""

import json

import pytest

from .testBrowserJourneys import _fnReleaseBrowserLaneOwnership  # noqa: F401
from .testCouncilBlockedButtonExplainsItself import (  # noqa: F401
    _fdictActivateCouncilToolbar,
    _fnOpenCouncilWorkspace,
)
from .testCouncilPlanningJourney import (  # noqa: F401 — fixture wiring
    _fdictClaimAndActivate,
    _fnIsolateCouncilStore,
    _fnScriptedProviderSeam,
)

pytestmark = pytest.mark.browser


def _fdictCampaign(dictOverrides):
    """Return one planning campaign record with the overrides applied."""
    dictCampaign = {
        "sCampaignId": "campaign-pause",
        "sState": "planning",
        "sQuestion": "Which sampler settings converge fastest?",
        "sChairbotParticipantId": "p-one",
        "bDeliberationLive": True,
        "listParticipants": [
            {"sParticipantId": "p-one", "sProvider": "claude",
             "sRequestedModel": "opus"},
            {"sParticipantId": "p-two", "sProvider": "claude",
             "sRequestedModel": "sonnet"}],
        "listRounds": [{"iRoundNumber": 1, "dictTurnsByPhase": {}}],
        "dictStoppingPoint": {
            "bResumable": True, "sAction": "resume",
            "sNextPhase": "synthesis", "iRoundNumber": 1},
    }
    dictCampaign.update(dictOverrides)
    return dictCampaign


def _fsCampaignScript(dictOverrides):
    """Render one planning campaign into the panel's own test seam."""
    return ("VaibifyAgentCouncil.fnSetCampaignForTest("
            + json.dumps(_fdictCampaign(dictOverrides)) + ");")


def _fsRender(page, dictOverrides):
    """Render a campaign and return the workspace's visible text."""
    return page.evaluate(
        "() => {" + _fsCampaignScript(dictOverrides) +
        "return document.getElementById("
        "'agentCouncilWorkspaceBody').innerText; }")


@pytest.mark.falsification
def testPauseAndStopStateOppositeConsequencesSideBySide(
        pageDashboard, serverHub):
    """Both controls, and the difference in words the researcher reads.

    Kills: shipping Pause as a second neutral button beside Stop, where
    the only thing distinguishing "carry on later" from "end this
    council for good" is the verb.
    """
    _fnOpenCouncilWorkspace(pageDashboard, serverHub)
    sBody = _fsRender(pageDashboard, {})

    assert pageDashboard.locator("#btnCouncilPause").count() == 1, sBody
    assert pageDashboard.locator("#btnCouncilStop").count() == 1, sBody
    assert "Pause after this phase" in sBody, sBody
    assert "for good" in sBody, (
        "nothing on screen said that stopping is the irreversible one")
    # The destructive one is styled as destructive; the difference must
    # survive a researcher who reads only the buttons.
    assert pageDashboard.locator(
        "#btnCouncilStop.btn-danger").count() == 1, (
        "Stop is styled exactly like Pause")
    assert pageDashboard.locator(
        "#btnCouncilPause.btn-danger").count() == 0


@pytest.mark.falsification
def testAPauseStillLandingSaysThePhaseWillFinishFirst(
        pageDashboard, serverHub):
    """A requested pause is not a landed one, and must not claim to be.

    Kills: rendering "Paused" the moment the request is accepted, which
    tells a researcher nothing is running while a turn is mid-answer.
    """
    _fnOpenCouncilWorkspace(pageDashboard, serverHub)
    sBody = _fsRender(pageDashboard, {
        "bPauseRequested": True,
        "dictPhaseInFlight": {
            "sPhase": "crossReview", "iRoundNumber": 1,
            "listRunningParticipantIds": ["p-one"]}})

    assert "Pausing" in sBody, sBody
    assert "crossReview" in sBody, sBody
    assert "will finish first" in sBody, sBody
    # Nothing to resume yet: the council has not stood down.
    assert pageDashboard.locator("#btnCouncilResume").count() == 0, sBody
    assert pageDashboard.locator("#btnCouncilPause").count() == 0, (
        "a council already pausing offered to pause again")


@pytest.mark.falsification
def testALandedPauseSaysNothingIsWorkingAndOffersResume(
        pageDashboard, serverHub):
    """The stand-down, with the way back on the screen.

    Kills: leaving a paused council on the deliberating composer, which
    claims agents are working over a council that has stopped.
    """
    _fnOpenCouncilWorkspace(pageDashboard, serverHub)
    sBody = _fsRender(pageDashboard, {
        "bPauseRequested": True, "dictPhaseInFlight": None})

    assert "Paused." in sBody, sBody
    assert "No agent is working" in sBody, sBody
    assert "The council is deliberating" not in sBody, sBody
    assert pageDashboard.locator("#btnCouncilResume").count() == 1, sBody


@pytest.mark.falsification
def testAPausedCouncilIsNotDescribedAsACrashedHub(pageDashboard, serverHub):
    """The resume surface must say WHY the council is not running.

    A researcher who paused it and came back must not read that the hub
    restarted, and one whose hub really did crash must not read that
    they paused it. Both directions are asserted, because a branch that
    always takes one side passes the half nobody checks.

    Kills: the resume surface describing every idle planning campaign
    as a hub restart.
    """
    _fnOpenCouncilWorkspace(pageDashboard, serverHub)

    sPaused = _fsRender(pageDashboard, {
        "bDeliberationLive": False, "bPauseRequested": True})
    assert "is paused" in sPaused, sPaused
    assert "hub restarted" not in sPaused, sPaused

    sCrashed = _fsRender(pageDashboard, {
        "bDeliberationLive": False, "bPauseRequested": False})
    assert "hub restarted" in sCrashed, sCrashed
    assert "is paused" not in sCrashed, sCrashed


@pytest.mark.falsification
def testThePauseButtonPostsToThePauseRoute(pageDashboard, serverHub):
    """The click must reach the pause route, not the stop route.

    Two endpoints with opposite consequences sit one line apart in the
    source; a mis-wired handler archives a council the researcher meant
    to keep. The request is observed and then answered, so the assertion
    is about what the browser actually sent.

    Kills: binding Pause to the stop action (or to nothing at all).
    """
    _fnOpenCouncilWorkspace(pageDashboard, serverHub)
    listPosted = []

    def _fnRecordAndAnswer(routeIntercepted):
        listPosted.append((routeIntercepted.request.method,
                           routeIntercepted.request.url))
        routeIntercepted.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"bPauseRequested": True, "bSettled": False}))

    # The action refetches backend truth after posting — never
    # optimistic — so the refresh is answered too, with the record a
    # real pause produces. Registered FIRST so the pause glob added
    # below wins for the POST.
    pageDashboard.route(
        "**/api/agent-councils/*/campaign-pause*",
        lambda routeIntercepted: routeIntercepted.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"dictCampaign": _fdictCampaign({
                "bPauseRequested": True, "dictPhaseInFlight": None})})))
    pageDashboard.route("**/api/agent-councils/**/pause", _fnRecordAndAnswer)
    _fsRender(pageDashboard, {})
    pageDashboard.click("#btnCouncilPause")
    pageDashboard.wait_for_function(
        "() => document.getElementById('agentCouncilWorkspaceBody')"
        ".innerText.indexOf('Paused.') !== -1", timeout=10000)

    assert listPosted, "the Pause button sent no request at all"
    sMethod, sUrl = listPosted[0]
    assert sMethod == "POST", listPosted
    assert sUrl.endswith("/campaign-pause/pause"), listPosted
    assert "request-stop" not in sUrl, listPosted

    assert pageDashboard.listPageErrors == []
    assert pageDashboard.listConsoleErrors == []
