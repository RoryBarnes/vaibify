"""Settings offers BOTH host-global timeouts, and they stay independent.

The bug this lane exists for: "Idle shutdown" was the only timeout on
the Settings panel, and it governs the hub PROCESS retiring itself --
which an open dashboard vetoes. What ends a session somebody is sitting
in front of is the absolute session cap, twelve hours by default, whose
preference, route and backend tests all existed with no control
anywhere. A researcher set Idle shutdown to Never, was disconnected at
twelve hours anyway, and had been told by the expiry message to "raise
the session cap in Settings" -- where no such control was.

The two controls now share one implementation, which is exactly why
INDEPENDENCE is the property worth driving in a browser: a shared
render path that sent both changes to one endpoint, or showed one
endpoint's answer in both selects, would look right on screen and be
the same bug again. So every assertion here keeps the two apart --
different endpoints, different values, different env-override names --
and the PUT bodies are captured per endpoint.

WHAT THIS DOES NOT COVER: the routes themselves (testPreferencesRoutes,
over real HTTP), whether the watchdog retires on the applied idle value
(testHubIdleWatchdog), and whether the evaluator expires a session on
the applied cap (testSessionCapSetting). This lane proves both controls
reach the screen and the right wire.
"""

import json

import pytest


pytestmark = pytest.mark.browser

S_IDLE_GLOB = "**/api/preferences/idle-timeout"
S_CAP_GLOB = "**/api/preferences/session-cap"


def _fnRoutePreference(page, sGlob, listPutBodies, dictGet, dictPut):
    """Stub one preference endpoint's GET/PUT, capturing every PUT body."""

    def fnHandle(routeIntercepted):
        requestIntercepted = routeIntercepted.request
        if requestIntercepted.method == "PUT":
            listPutBodies.append(requestIntercepted.post_data_json)
            routeIntercepted.fulfill(
                status=200, content_type="application/json",
                body=json.dumps(dictPut))
        else:
            routeIntercepted.fulfill(
                status=200, content_type="application/json",
                body=json.dumps(dictGet))

    page.route(sGlob, fnHandle)


def _fdictAnswer(bNever, fSeconds, bEnvOverride=False):
    return {
        "bNever": bNever, "fSeconds": fSeconds,
        "sStoredPreference": None, "bEnvOverride": bEnvOverride,
    }


def _fnOpenTheHostSettingsPanel(page, serverHub):
    """Load the dashboard and open the TOOLBAR gear's host panel.

    The panel renders through the real ``fnRenderHostSettings`` and is
    marked expanded the way the gear toggle marks it, so each control's
    own load path (GET on render, change handler, PUT) is the
    production one.

    NO WORKFLOW IS SEEDED. Both timeouts are properties of this
    computer, and they used to be rendered by a panel that returns
    early without an open project, inside a tab Blank Project mode does
    not draw -- so the researcher whose blank-project session timed out
    was the one researcher who could not reach them.
    """
    page.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    page.wait_for_selector(".container-tile", timeout=10000)
    page.evaluate(
        """() => {
            document.getElementById('hostSettingsPanel')
                .classList.add('expanded');
            VaibifyApp.fnRenderHostSettings();
        }"""
    )
    page.wait_for_function(
        "() => !!document.getElementById('gsSessionCap')", timeout=10000)


def testBothTimeoutsAreOfferedAndShowTheirOwnServerValue(
    pageDashboard, serverHub,
):
    """Two controls, two endpoints, two different answers on screen."""
    listIdlePuts, listCapPuts = [], []
    _fnRoutePreference(
        pageDashboard, S_IDLE_GLOB, listIdlePuts,
        _fdictAnswer(True, None), {})
    _fnRoutePreference(
        pageDashboard, S_CAP_GLOB, listCapPuts,
        _fdictAnswer(False, 43200.0), {})
    _fnOpenTheHostSettingsPanel(pageDashboard, serverHub)
    pageDashboard.wait_for_function(
        "() => document.getElementById('gsIdleTimeout').value === 'never'"
        " && document.getElementById('gsSessionCap').value === '43200'",
        timeout=10000)
    assert listIdlePuts == [] and listCapPuts == []
    assert pageDashboard.listPageErrors == []


def testChangingTheSessionCapPutsToItsOwnEndpointOnly(
    pageDashboard, serverHub,
):
    """The whole reported bug in one assertion.

    Setting a session lifetime must not be delivered to the idle
    endpoint, and must not leave the idle control changed: those are
    the two ways one control could silently stand in for the other.
    """
    listIdlePuts, listCapPuts = [], []
    _fnRoutePreference(
        pageDashboard, S_IDLE_GLOB, listIdlePuts,
        _fdictAnswer(True, None), _fdictAnswer(True, None))
    _fnRoutePreference(
        pageDashboard, S_CAP_GLOB, listCapPuts,
        _fdictAnswer(False, 43200.0), _fdictAnswer(True, None))
    _fnOpenTheHostSettingsPanel(pageDashboard, serverHub)
    pageDashboard.wait_for_function(
        "() => document.getElementById('gsSessionCap').value === '43200'",
        timeout=10000)
    pageDashboard.evaluate(
        """() => {
            const elSelect = document.getElementById('gsSessionCap');
            elSelect.value = 'never';
            elSelect.dispatchEvent(new Event('change', {bubbles: true}));
        }"""
    )
    pageDashboard.wait_for_function(
        "() => document.getElementById('gsSessionCap').value === 'never'",
        timeout=10000)
    assert listCapPuts and listCapPuts[-1]["sValue"] == "never"
    assert listIdlePuts == [], "the session cap was PUT to the idle route"
    assert pageDashboard.eval_on_selector(
        "#gsIdleTimeout", "el => el.value") == "never"
    assert pageDashboard.listPageErrors == []


def testChangingIdleShutdownStillPutsToItsOwnEndpointOnly(
    pageDashboard, serverHub,
):
    """The same independence in the other direction."""
    listIdlePuts, listCapPuts = [], []
    _fnRoutePreference(
        pageDashboard, S_IDLE_GLOB, listIdlePuts,
        _fdictAnswer(True, None), _fdictAnswer(False, 1800.0))
    _fnRoutePreference(
        pageDashboard, S_CAP_GLOB, listCapPuts,
        _fdictAnswer(False, 43200.0), {})
    _fnOpenTheHostSettingsPanel(pageDashboard, serverHub)
    pageDashboard.evaluate(
        """() => {
            const elSelect = document.getElementById('gsIdleTimeout');
            elSelect.value = '1800';
            elSelect.dispatchEvent(new Event('change', {bubbles: true}));
        }"""
    )
    pageDashboard.wait_for_function(
        "() => document.getElementById('gsIdleTimeout').value === '1800'",
        timeout=10000)
    assert listIdlePuts and listIdlePuts[-1]["sValue"] == "1800"
    assert listCapPuts == [], "idle shutdown was PUT to the cap route"
    assert pageDashboard.eval_on_selector(
        "#gsSessionCap", "el => el.value") == "43200"
    assert pageDashboard.listPageErrors == []


def testEachControlNamesItsOwnEnvironmentOverride(pageDashboard, serverHub):
    """A pinned cap must not be blamed on the idle timeout's variable."""
    listIdlePuts, listCapPuts = [], []
    _fnRoutePreference(
        pageDashboard, S_IDLE_GLOB, listIdlePuts,
        _fdictAnswer(True, None), {})
    _fnRoutePreference(
        pageDashboard, S_CAP_GLOB, listCapPuts,
        _fdictAnswer(False, 90.0, bEnvOverride=True), {})
    _fnOpenTheHostSettingsPanel(pageDashboard, serverHub)
    pageDashboard.wait_for_function(
        "() => document.getElementById('gsSessionCap').disabled === true",
        timeout=10000)
    sNote = pageDashboard.text_content("#gsSessionCapNote")
    assert "VAIBIFY_ABSOLUTE_SESSION_CAP_SECONDS" in sNote
    assert "VAIBIFY_HUB_IDLE_TIMEOUT_SECONDS" not in sNote
    # A one-off option shows the exact pinned value the presets lack.
    assert pageDashboard.eval_on_selector(
        "#gsSessionCap", "el => el.value") == "90"
    # The idle control beside it is untouched and still usable.
    assert pageDashboard.eval_on_selector(
        "#gsIdleTimeout", "el => el.disabled") is False
    assert pageDashboard.text_content("#gsIdleTimeoutNote") == ""
    assert listCapPuts == [], "a disabled control must not PUT"
    assert pageDashboard.listPageErrors == []
