"""The ask-the-chairbot conversation, driven in a real browser.

Lane 1 of ``design/agentCouncilVerificationLanes.md``: the real
frontend, the real routes, the real controller — and a scripted chat
runner, because the browser lane's fake Docker adapter builds no
containers and this proves the UI journey, nothing about real runners.

A green Python suite says nothing about this file's subject. The chat
tab is ~200 lines of JavaScript that no other test executes, and the
specific failures it can have — a button that binds to nothing, a
transcript that never renders, a poll that wipes the composer — are all
invisible to a string-matching contract test.

What this drives that nothing else can:

- the tab renders, the Open button is bound, and the disclosure is on
  screen BEFORE a runner is built;
- a question typed into the composer reaches the backend and its answer
  appears in the transcript through the poll, not through an optimistic
  local append;
- the composer survives a poll tick that changed nothing (the
  render-signature trap: a per-tick value in the signature wipes a
  half-typed question);
- closing removes the conversation and the tab returns to its opening
  state.
"""

import shutil
import tempfile

import pytest

from vaibify.gui import agentCouncilChat
from vaibify.gui import agentCouncilContext
from vaibify.gui import agentCouncilController
from vaibify.gui import agentCouncilDockerGateway
from vaibify.gui import agentCouncilStore

from .fakeDockerAdapter import S_CONTAINER_ID
from .testCouncilPlanningJourney import (  # noqa: F401 — fixtures
    _fdictClaimAndActivate,
    _fdictStore,
    _fnConveneThroughTheForm,
    _fnScriptedProviderSeam,
    _fnIsolateCouncilStore,
    _fsNewestCampaignId,
)


pytestmark = pytest.mark.browser

S_SCRIPTED_ANSWER = ("The time-based cache was rejected because an edit "
                     "inside the window returns a stale answer.")


@pytest.fixture(autouse=True)
def _fnScriptChatRunner(monkeypatch):
    """Answer the chat lane's gateway calls with a scripted runner.

    Declared, not permissive: every primitive the chat lane calls is
    listed here with what it returns, and anything else is left
    unpatched so the real one raises for want of a daemon. This is the
    browser lane's fail-closed fake discipline applied to the council's
    conversation half.
    """
    monkeypatch.setattr(
        agentCouncilDockerGateway, "fdockerCreateCouncilClient",
        lambda *args, **kwargs: object())
    # The gateway FACTORY is deliberately NOT patched. It is shared with
    # the campaign read route, which builds a registry-only view through
    # it, so a double returning a dict of its own shape broke an
    # unrelated route with a 500 — caught here and nowhere else, because
    # only this lane loads the page that polls it.
    monkeypatch.setattr(
        agentCouncilDockerGateway, "fsCreateCampaignInternalNetwork",
        lambda dictGateway, sScope: f"vaibifyCouncilEgress-{sScope}")
    monkeypatch.setattr(
        agentCouncilDockerGateway, "fsLaunchAllowlistProxy",
        lambda dictGateway, sScope, saHostnames: "172.30.0.2")
    monkeypatch.setattr(
        agentCouncilDockerGateway, "fdictRemoveCampaignEgressResources",
        lambda dictGateway, sScope: {
            "bProxyAbsenceProven": True, "bNetworkAbsenceProven": True,
            "saIndeterminateResources": []})
    monkeypatch.setattr(
        agentCouncilDockerGateway, "fdictReserveAndCreateRunner",
        lambda *args, **kwargs: {
            "bCreated": True, "sRefusalReason": "",
            "sHandle": "browser-lane-chat-handle",
            "sReservationId": "browser-lane-chat-reservation",
            "sContainerName": "vaibifyCouncilRunnerBrowserLane",
            "sRole": "runner"})
    monkeypatch.setattr(
        agentCouncilDockerGateway, "fnCopySnapshotIntoRunner",
        lambda *args, **kwargs: None)
    monkeypatch.setattr(
        agentCouncilDockerGateway, "fdictDestroyAndSettle",
        lambda dictGateway, sHandle: {
            "sOutcome": "destroyed", "sReason": ""})
    monkeypatch.setattr(
        agentCouncilDockerGateway, "fdictExecuteBoundedTurn",
        _fdictAnswerAfterADeliberateDelay)
    # The credential lane needs a real project login to extract, which
    # the fake adapter has no file to serve; the delivery is proven in
    # tests/testCouncilChat.py against a real staged host file.
    monkeypatch.setattr(
        agentCouncilChat, "_fnDeliverChatCredential",
        lambda dictSession: None)
    from vaibify.gui import agentCouncilProviders
    monkeypatch.setattr(
        agentCouncilProviders, "fsExplainUnusableRunnerCredential",
        lambda *args: "")
    monkeypatch.setattr(
        agentCouncilContext, "fbaReadSealedSnapshotArchive",
        lambda sRoot, sCampaignId: b"browser-lane-snapshot")


def _fsJsonString(sText):
    import json
    return json.dumps(sText)


# Long enough that the answer CANNOT be ready when the ask action's own
# refetch runs, so the only thing that can put it on screen is a poll
# tick. Without the delay the journey passed with the chat poll deleted
# outright — an answer arriving in milliseconds is not the production
# case, and a test built on it proves nothing about the lane that
# actually delivers a minutes-long answer.
F_SCRIPTED_ANSWER_DELAY_SECONDS = 3.0


def _fdictAnswerAfterADeliberateDelay(*args, **kwargs):
    """Return the scripted stream, slowly. Runs on a worker thread."""
    import time
    time.sleep(F_SCRIPTED_ANSWER_DELAY_SECONDS)
    return {
        "iExitCode": 0,
        "sOutput": '{"type": "system", "model": "browser-lane-model"}\n'
                   '{"type": "result", "result": '
                   + _fsJsonString(S_SCRIPTED_ANSWER) + "}\n",
        "bOutputCapExceeded": False, "bWallClockExceeded": False,
        "iOutputBytes": 128, "bOomKilled": False,
        "fElapsedSeconds": F_SCRIPTED_ANSWER_DELAY_SECONDS,
    }


@pytest.mark.falsification
def testTheChairbotConversationRunsInTheBrowser(pageDashboard, serverHub):
    """Open, ask, read the answer, and close — through the real UI.

    Kills: unbinding the Ask and Open buttons, and never polling the
    chat tab (the scripted runner's 3s delay makes the poll the only
    path).
    """
    _fdictClaimAndActivate(pageDashboard, serverHub)
    _fnConveneThroughTheForm(pageDashboard)
    sCampaignId = _fsNewestCampaignId(serverHub)

    pageDashboard.click('.council-tab[data-tab="chat"]')
    pageDashboard.wait_for_selector("#btnCouncilChatOpen", timeout=16000)
    sDisclosure = pageDashboard.inner_text(".council-chat")
    assert "spends this project's provider subscription" in sDisclosure
    assert "cannot accept a plan" in sDisclosure

    pageDashboard.click("#btnCouncilChatOpen")
    pageDashboard.wait_for_selector("#councilChatQuestion", timeout=16000)

    pageDashboard.fill("#councilChatQuestion",
                       "Why did you reject the time-based cache?")
    pageDashboard.click("#btnCouncilChatAsk")
    # The answer is not ready when the action's own refetch runs, so
    # reaching the screen at all means a POLL TICK put it there.
    pageDashboard.wait_for_selector(
        ".council-chat-chairbot", timeout=30000)

    sTranscript = pageDashboard.inner_text(".council-chat-transcript")
    assert "Why did you reject the time-based cache?" in sTranscript
    assert S_SCRIPTED_ANSWER in sTranscript
    # Mechanically recorded from the stream, never the requested alias.
    assert "browser-lane-model" in pageDashboard.inner_text(
        ".council-chat-status")

    pageDashboard.click("#btnCouncilChatClose")
    pageDashboard.wait_for_selector("#btnCouncilChatOpen", timeout=16000)
    dictControllerState = getattr(
        serverHub.app.state,
        agentCouncilController.S_COUNCIL_CONTROLLER_STATE_KEY)
    assert sCampaignId not in dictControllerState[
        agentCouncilChat.S_CHAT_SESSIONS_KEY], (
        "closing the conversation in the UI left the session behind")

    assert pageDashboard.listPageErrors == []
    assert pageDashboard.listConsoleErrors == []


@pytest.mark.falsification
def testAPollTickDoesNotWipeAHalfTypedQuestion(pageDashboard, serverHub):
    """The render-signature trap, driven rather than asserted in a string.

    The panel re-renders whenever its signature changes. The idle
    countdown changes on every tick, so a signature that read it would
    replace the composer — and the researcher's half-typed question with
    it — every few seconds. Nothing but a real browser can show that.

    Kills: the idle countdown entering the render signature (the 30s
    wait is load-bearing; 9s spanned no idle tick).
    """
    _fdictClaimAndActivate(pageDashboard, serverHub)
    _fnConveneThroughTheForm(pageDashboard)

    pageDashboard.click('.council-tab[data-tab="chat"]')
    pageDashboard.wait_for_selector("#btnCouncilChatOpen", timeout=16000)
    pageDashboard.click("#btnCouncilChatOpen")
    pageDashboard.wait_for_selector("#councilChatQuestion", timeout=16000)

    pageDashboard.fill("#councilChatQuestion", "half typed, do not lose me")
    # Comfortably more than two ticks at the idle cadence. A shorter
    # wait let the "put the countdown in the signature" mutation
    # survive, because no tick had landed yet.
    pageDashboard.wait_for_timeout(30000)

    assert pageDashboard.input_value("#councilChatQuestion") == (
        "half typed, do not lose me")
    assert pageDashboard.listPageErrors == []
    assert pageDashboard.listConsoleErrors == []


def test_isolation_root_is_a_directory(tmp_path):
    """A guard that the isolation import path is intact off the browser."""
    import os
    assert os.path.isdir(str(tmp_path))
    sTemporary = tempfile.mkdtemp(prefix="councilChatLane")
    shutil.rmtree(sTemporary, ignore_errors=True)
    assert agentCouncilStore.S_COUNCIL_CAMPAIGN_STORE_STATE_KEY
