"""A dashboard opens only once its container has finished starting.

The waiting screen used to give up after two minutes and open a
dashboard whose terminal could not start yet, so a researcher who
entered a large project waited half a minute for a prompt with nothing
to say why (researcher-reported, 2026-11-09). Now the screen waits for
as long as the start takes, says which step it is on, offers a way back,
and calls a start stuck only after a long silence, without letting the
researcher in.

The readiness answers, and the listing's word that the container runs,
are scripted at the network edge, so each test chooses what the
container says; the claim and its release are the hub's own.
"""

import json

import pytest

from tests.browser.conftest import S_CONTAINER_NAME

pytestmark = pytest.mark.browser

S_TILE = f'.container-tile[data-name="{S_CONTAINER_NAME}"]'


@pytest.fixture(autouse=True)
def fixtureDropTheOwnerRecord(serverHub):
    """Each test opens the tile from scratch: a claim one leaves would lock it."""
    serverHub.app.state.dictContainerOwners.pop(S_CONTAINER_NAME, None)
    yield
    serverHub.app.state.dictContainerOwners.pop(S_CONTAINER_NAME, None)


def _fdictBooting(sStep, iSinceStart, iSinceProgress):
    return {
        "bReady": False, "sStatus": "booting", "sReason": "",
        "saWarnings": [], "iWarningCount": 0, "sStep": sStep,
        "iSecondsSinceStart": iSinceStart,
        "iSecondsSinceProgress": iSinceProgress,
    }


_DICT_READY = {
    "bReady": True, "sStatus": "ok", "sReason": "", "saWarnings": [],
    "iWarningCount": 0, "listStartupObservations": [],
}


def _fnScriptReadiness(pageDashboard, listAnswers):
    """Answer each readiness request with the next scripted answer, then the last."""
    listAsked = []

    def fnAnswer(route):
        listAsked.append(1)
        dictAnswer = listAnswers[min(len(listAsked), len(listAnswers)) - 1]
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(dictAnswer))
    pageDashboard.route("**/api/containers/*/ready", fnAnswer)
    return listAsked


def _fnReportTheContainerRunning(pageDashboard):
    """The lane's daemon holds no image for the project; say it runs."""
    def fnAnswer(route):
        responseReal = route.fetch()
        dictListing = responseReal.json()
        for dictContainer in dictListing.get("listContainers", []):
            if dictContainer.get("sName") == S_CONTAINER_NAME:
                dictContainer["sStatus"] = "running"
        route.fulfill(response=responseReal, body=json.dumps(dictListing))
    pageDashboard.route("**/api/registry", fnAnswer)


def _fnOpenTheTile(pageDashboard, serverHub):
    _fnReportTheContainerRunning(pageDashboard)
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_selector(
        S_TILE + ":not(.container-tile--locked) .status-running",
        timeout=30000)
    pageDashboard.click(S_TILE + " .container-tile-main")
    pageDashboard.wait_for_selector("#modalInitializing", state="visible",
                                    timeout=10000)


def _fbPickerShown(pageDashboard):
    return pageDashboard.evaluate(
        "() => document.getElementById('workflowPicker').style.display === 'flex'")


@pytest.mark.falsification
def testTheDashboardStaysClosedUntilTheStartIsDoneAndSaysWhy(
    pageDashboard, serverHub,
):
    """Kills: entering on any readiness answer, ready or not."""
    listAsked = _fnScriptReadiness(pageDashboard, [
        _fdictBooting("Installing vplot (3 of 9 repositories)", 134, 12),
    ] * 4 + [_DICT_READY])
    _fnOpenTheTile(pageDashboard, serverHub)
    pageDashboard.wait_for_function(
        "() => document.getElementById('initializingStep').textContent"
        " === 'Installing vplot (3 of 9 repositories)'", timeout=10000)
    assert pageDashboard.text_content("#initializingElapsed") == (
        "2 min 14 s since the container started")
    assert not _fbPickerShown(pageDashboard)
    assert pageDashboard.is_hidden("#btnInitializingRestart")
    pageDashboard.wait_for_selector("#modalInitializing", state="hidden",
                                    timeout=30000)
    assert len(listAsked) >= 5
    assert _fbPickerShown(pageDashboard)
    assert pageDashboard.listPageErrors == []


def testBackLeavesTheStartRunningAndLetsTheClaimGo(pageDashboard, serverHub):
    _fnScriptReadiness(pageDashboard, [_fdictBooting("Checking the network", 5, 5)])
    _fnOpenTheTile(pageDashboard, serverHub)
    assert S_CONTAINER_NAME in serverHub.app.state.dictContainerOwners
    pageDashboard.click("#btnInitializingBack")
    pageDashboard.wait_for_selector("#modalInitializing", state="hidden",
                                    timeout=10000)
    pageDashboard.wait_for_function(
        "() => document.getElementById('containerLanding').style.display"
        " !== 'none'", timeout=10000)
    assert not _fbPickerShown(pageDashboard)
    assert S_CONTAINER_NAME not in serverHub.app.state.dictContainerOwners


def testALongSilenceIsCalledStuckAndStillKeepsTheResearcherOut(
    pageDashboard, serverHub,
):
    _fnScriptReadiness(pageDashboard, [
        _fdictBooting("Compiling vplanet", 1500, 660)])
    _fnOpenTheTile(pageDashboard, serverHub)
    pageDashboard.wait_for_selector("#btnInitializingRestart", state="visible",
                                    timeout=10000)
    assert pageDashboard.text_content("#initializingStuck").startswith(
        "Nothing new for 11 minutes, so the start may be stuck.")
    pageDashboard.wait_for_timeout(3000)
    assert pageDashboard.is_visible("#modalInitializing")
    assert not _fbPickerShown(pageDashboard)


def testAContainerThatDoesNotAnswerIsNotEntered(pageDashboard, serverHub):
    _fnScriptReadiness(pageDashboard, [{
        "bReady": False, "sStatus": "stalled", "sReason": "",
        "saWarnings": [], "iWarningCount": 0,
    }])
    _fnOpenTheTile(pageDashboard, serverHub)
    pageDashboard.wait_for_selector("#btnInitializingRestart", state="visible",
                                    timeout=10000)
    assert "does not answer" in pageDashboard.text_content("#initializingStuck")
    pageDashboard.wait_for_timeout(3000)
    assert not _fbPickerShown(pageDashboard)
