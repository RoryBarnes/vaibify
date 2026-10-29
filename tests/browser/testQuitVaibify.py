"""The hub's Quit button asks, sends one real Quit, and reports the truth.

The page must show what the server's preflight says (what keeps running,
what refuses), send the D6 choice as given, and say "vaibify has quit"
only once the server has stopped answering -- never while it answers,
and without raising the connection-lost alarm over a shutdown the
researcher asked for.

The Quit POST and the server's going-away are played by route handlers:
this lane's hub runs inside the test process, so a real Quit would
SIGTERM the test runner itself. The preflight is the real hub's.
"""

import json

import pytest

from tests.browser.conftest import fnLoadThePickerUntilItsButtonsListen


pytestmark = pytest.mark.browser


@pytest.fixture(autouse=True)
def fixtureStopIntercepting(pageDashboard):
    yield
    pageDashboard.unroute_all(behavior="ignoreErrors")


def _fnPlayAnAcceptedQuit(pageDashboard, listQuitBodies):
    """Answer the Quit 202, then let every later request find no server."""
    dictState = {"bQuit": False}

    def fnHandleQuit(routeQuit):
        listQuitBodies.append(json.loads(routeQuit.request.post_data or "{}"))
        dictState["bQuit"] = True
        routeQuit.fulfill(
            status=202, content_type="application/json",
            body=json.dumps({"bQuitting": True,
                             "listStoppedEnvironments": [],
                             "iChildWindows": 0}),
        )

    def fnHandleAfterQuit(routeAny):
        if dictState["bQuit"]:
            routeAny.abort("connectionrefused")
        else:
            routeAny.continue_()

    pageDashboard.route("**/api/session/lifetime", fnHandleAfterQuit)
    pageDashboard.route("**/api/session/quit", fnHandleQuit)


def testTheEnvironmentsHubQuitsAndSaysSoOnlyWhenTheServerIsGone(
    pageDashboard, serverHub,
):
    listQuitBodies = []
    fnLoadThePickerUntilItsButtonsListen(pageDashboard, serverHub)
    _fnPlayAnAcceptedQuit(pageDashboard, listQuitBodies)
    pageDashboard.click("#btnQuitVaibifyEnvironments")
    pageDashboard.wait_for_selector("#modalQuit", timeout=10000)
    elConfirm = pageDashboard.locator("#btnQuitConfirm")
    assert elConfirm.is_enabled()
    elConfirm.click()
    pageDashboard.wait_for_function(
        "() => (document.getElementById('quitShutdownStatus') || "
        "{textContent: ''}).textContent.includes('vaibify has quit')",
        timeout=15000,
    )
    assert listQuitBodies == [{"bStopEnvironments": False}]
    assert pageDashboard.locator(".toast.error").count() == 0
    assert pageDashboard.listPageErrors == []


def testAPreflightWithReasonsDisablesQuitAndNamesThem(
    pageDashboard, serverHub,
):
    fnLoadThePickerUntilItsButtonsListen(pageDashboard, serverHub)
    pageDashboard.route("**/api/session/quit-preflight", lambda routePre: (
        routePre.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({
                "listReasons": [{"sWindow": "this window",
                                 "sReason": "a pipeline run in 'demo'"}],
                "bCanQuit": False,
                "listRunningEnvironments": ["demo"],
                "bEnvironmentsUnknown": False,
                "listChildWindows": [{"iPort": 8051, "bResponding": True}],
                "iOpenTerminals": 1,
                "sTerminalWarning": "1 terminal will disconnect.",
            }),
        )
    ))
    pageDashboard.click("#btnQuitVaibifyEnvironments")
    pageDashboard.wait_for_selector("#modalQuit", timeout=10000)
    sModal = pageDashboard.locator("#modalQuit").inner_text()
    assert "this window: a pipeline run in 'demo'" in sModal
    assert "demo" in sModal
    assert "The vaibify window on port 8051" in sModal
    assert "1 terminal will disconnect." in sModal
    assert not pageDashboard.locator("#btnQuitConfirm").is_enabled()
    assert pageDashboard.listPageErrors == []


def testTheStopChoiceIsSentAsGiven(pageDashboard, serverHub):
    listQuitBodies = []
    fnLoadThePickerUntilItsButtonsListen(pageDashboard, serverHub)
    pageDashboard.route("**/api/session/quit-preflight", lambda routePre: (
        routePre.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({
                "listReasons": [], "bCanQuit": True,
                "listRunningEnvironments": ["first", "second"],
                "bEnvironmentsUnknown": False, "listChildWindows": [],
                "iOpenTerminals": 0, "sTerminalWarning": "",
            }),
        )
    ))
    _fnPlayAnAcceptedQuit(pageDashboard, listQuitBodies)
    pageDashboard.click("#btnQuitVaibifyEnvironments")
    pageDashboard.wait_for_selector("#checkboxQuitStopEnvironments",
                                    timeout=10000)
    assert "Also stop 2 running environments" in pageDashboard.locator(
        "#modalQuit").text_content()
    pageDashboard.check("#checkboxQuitStopEnvironments")
    pageDashboard.click("#btnQuitConfirm")
    pageDashboard.wait_for_selector("#quitShutdownStatus", timeout=10000)
    assert listQuitBodies == [{"bStopEnvironments": True}]
