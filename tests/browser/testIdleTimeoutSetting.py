"""The gear-menu idle-shutdown control reads and applies the timeout live.

Drives the REAL control in a real browser: the panel renders through
``fnRenderGlobalSettings``, the select is populated from a GET on open,
a change PUTs the chosen value, and the control reflects the server's
answer. Only the two idle-timeout endpoints are stubbed, so the frontend
path exercised here -- render, GET, change handler, PUT, reflect -- is
the production one.

WHAT THIS DOES NOT COVER, stated so silence is not read as proof: the
backend route itself (that is testPreferencesRoutes over real HTTP) and
whether the watchdog actually retires on the applied value (that is
testHubIdleWatchdog). This lane proves the control reaches the screen
and the wire, nothing about the reaper's clock.
"""

import json

import pytest


pytestmark = pytest.mark.browser

S_IDLE_ROUTE_GLOB = "**/api/preferences/idle-timeout"


def _fnRouteIdleTimeout(page, listPutBodies, dictGet, dictPut):
    """Stub the idle-timeout GET/PUT, capturing every PUT body."""

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

    page.route(S_IDLE_ROUTE_GLOB, fnHandle)


def _fnOpenSettingsWithWorkflow(page, serverHub):
    """Load the dashboard, seed a minimal workflow, open the gear panel.

    The panel is rendered through the real ``fnRenderGlobalSettings`` and
    marked ``expanded`` the way the gear toggle marks it, so the idle
    control's own load path (GET on render, change handler, PUT) is the
    production one. The change is delivered as a real ``change`` event on
    the real select, which is what the bound handler listens for.
    """
    page.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    page.wait_for_selector(".container-tile", timeout=10000)
    page.evaluate(
        """() => {
            VaibifyApp.fnRefreshWorkflowData({
                dictWorkflow: {listSteps: [], sPlotDirectory: 'Plot',
                    sFigureType: 'pdf'},
                sWorkflowPath: 'x',
            });
            document.getElementById('globalSettingsPanel')
                .classList.add('expanded');
            VaibifyApp.fnRenderGlobalSettings();
        }"""
    )
    page.wait_for_function(
        "() => !!document.getElementById('gsIdleTimeout')", timeout=10000)


def testIdleTimeoutControlReflectsAndAppliesLive(pageDashboard, serverHub):
    """The select shows the server's value, and a change PUTs the new one."""
    listPutBodies = []
    _fnRouteIdleTimeout(
        pageDashboard, listPutBodies,
        dictGet={"bNever": True, "fSeconds": None,
                 "sStoredPreference": None, "bEnvOverride": False},
        dictPut={"bNever": False, "fSeconds": 1800.0,
                 "sStoredPreference": "1800", "bEnvOverride": False},
    )
    _fnOpenSettingsWithWorkflow(pageDashboard, serverHub)
    # GET on open populated the select from the server's "never".
    pageDashboard.wait_for_function(
        "() => document.getElementById('gsIdleTimeout').value === 'never'",
        timeout=10000)
    # Choosing 30 minutes fires the change handler, which PUTs and
    # reflects the server's answer back into the control.
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
    assert listPutBodies, "the change handler never issued a PUT"
    assert listPutBodies[-1]["sValue"] == "1800"
    assert pageDashboard.listPageErrors == []


def testIdleTimeoutControlShowsEnvOverride(pageDashboard, serverHub):
    """An env-pinned timeout disables the control and names the override."""
    listPutBodies = []
    _fnRouteIdleTimeout(
        pageDashboard, listPutBodies,
        dictGet={"bNever": False, "fSeconds": 60.0,
                 "sStoredPreference": None, "bEnvOverride": True},
        dictPut={},
    )
    _fnOpenSettingsWithWorkflow(pageDashboard, serverHub)
    pageDashboard.wait_for_function(
        "() => document.getElementById('gsIdleTimeout').disabled === true",
        timeout=10000)
    sNote = pageDashboard.text_content("#gsIdleTimeoutNote")
    assert "VAIBIFY_HUB_IDLE_TIMEOUT_SECONDS" in sNote
    # A one-off "60 seconds" option shows the exact env-pinned value the
    # presets do not carry.
    sValue = pageDashboard.eval_on_selector(
        "#gsIdleTimeout", "el => el.value")
    assert sValue == "60"
    assert listPutBodies == [], "a disabled control must not PUT"
    assert pageDashboard.listPageErrors == []
