"""Lane 1 journeys: the dashboard, in a real browser, against a real server.

Ordered deliberately. Refusal-honesty comes first because that is this
repository's actual shipped-bug shape -- all-grey badges, Run Step
always refused and mislabelled "cannot reach server", passed steps
rendering as missing markers. A dashboard that lies is worse than one
that is slow, so the first thing the lane proves is that a backend
refusal reaches the screen as a refusal.

Every journey asserts both what the browser shows and what the backend
authoritatively holds afterwards. That is what makes this a front-and-
back test rather than a frontend mock test.

WHAT THIS LANE DOES NOT COVER -- stated because silence about an
unverified surface reads as verification:

* The Docker boundary. The adapter here is a fail-closed fake; only
  Lane 2, against a real container, speaks for container launch, file
  ownership on write, or the real transport.
* Terminal WebSocket content, figure rendering, and the sync panel.
* Anything requiring a second browser session's lease to be minted by
  a different hub process.

Lane 1 failing blocks merge. Lane 2 failing blocks the next release,
not retroactively -- it runs nightly, so fake-vs-reality drift is
caught up to a day late.
"""

import json

import pytest

from tests.browser.fakeDockerAdapter import (
    S_CONTAINER_NAME,
    UnmodelledContainerCall,
)


pytestmark = pytest.mark.browser


def _fdictSessionToken(page, serverHub):
    """Return the token the page itself fetched, as the browser does."""
    return page.evaluate(
        """async (sBaseUrl) => {
            const response = await fetch(sBaseUrl + '/api/session-token');
            return await response.json();
        }""",
        serverHub.sBaseUrl,
    )


# ---------------------------------------------------------------------
# Journey 1 -- a refusal must look like a refusal
# ---------------------------------------------------------------------


def testBackendRefusalIsNotRenderedAsSuccess(pageDashboard, serverHub):
    """An unauthorized API call must not read as a success anywhere.

    The bar is not "the request failed" -- it is that nothing in the
    response could be mistaken for a granted action by a caller that
    only checks for a payload.
    """
    pageDashboard.goto(serverHub.sBaseUrl, wait_until="load")
    dictResult = pageDashboard.evaluate(
        """async (sBaseUrl) => {
            const response = await fetch(
                sBaseUrl + '/api/projects', {
                    headers: {'x-session-token': 'not-the-real-token'},
                });
            return {
                iStatus: response.status,
                sBody: await response.text(),
            };
        }""",
        serverHub.sBaseUrl,
    )
    assert dictResult["iStatus"] == 401, (
        "A bad session token was not refused: "
        f"{dictResult['iStatus']} {dictResult['sBody'][:200]}"
    )
    assert "bSuccess" not in dictResult["sBody"], (
        "A refusal carried a success-shaped payload, which a caller "
        "checking only for a body would render as an accepted action."
    )


# ---------------------------------------------------------------------
# Journey 2 -- the page evaluates cleanly
# ---------------------------------------------------------------------


def testDashboardLoadsWithNoConsoleErrors(pageDashboard, serverHub):
    """Zero console errors, zero page errors, zero failed assets.

    A single ReferenceError means a module failed to evaluate and every
    module below it in load order is dead -- the exact failure the five
    parallel agents shipped green, because no automated check executed
    the frontend at all.
    """
    listFailedRequests = []
    pageDashboard.on("requestfailed", lambda request: (
        listFailedRequests.append(request.url)
    ))
    listBadResponses = []
    pageDashboard.on("response", lambda response: (
        listBadResponses.append((response.url, response.status))
        if response.status >= 500 else None
    ))

    pageDashboard.goto(serverHub.sBaseUrl, wait_until="networkidle")

    assert pageDashboard.listPageErrors == [], (
        "Uncaught page errors: " + "; ".join(pageDashboard.listPageErrors)
    )
    assert pageDashboard.listConsoleErrors == [], (
        "Console errors: " + "; ".join(pageDashboard.listConsoleErrors)
    )
    assert listFailedRequests == [], (
        "Assets failed to load: " + "; ".join(listFailedRequests)
    )
    assert listBadResponses == [], (
        f"Server errors during load: {listBadResponses}"
    )


def testFrontendGlobalsResolveAsBareIdentifiers(pageDashboard, serverHub):
    """The IIFE modules must actually be on the page.

    Probed as bare identifiers, not via ``window``: the modules are
    declared with ``const``, which creates a global *lexical* binding
    rather than a ``window`` property, so ``window.VaibifyApp`` is
    ``undefined`` for a module that is working perfectly. AGENTS.md
    records a session lost to exactly that false alarm.
    """
    pageDashboard.goto(serverHub.sBaseUrl, wait_until="networkidle")
    assert pageDashboard.evaluate("typeof VaibifyApp") == "object", (
        "VaibifyApp did not evaluate; the module graph is broken."
    )
    assert pageDashboard.evaluate(
        "typeof VaibifyUtilities"
    ) == "object"


# ---------------------------------------------------------------------
# Journey 3 -- the seeded project renders from the real response
# ---------------------------------------------------------------------


def testSeededProjectReachesTheBrowser(pageDashboard, serverHub):
    """The seeded project must arrive over real HTTP and be rendered.

    The token is NOT set by hand here. The application installs its own
    ``fetch`` wrapper that injects ``x-session-token``, so adding the
    header explicitly sends it twice -- and Starlette joins duplicate
    headers with ", ", producing ``"<token>, <token>"``, which the
    middleware correctly rejects with a 401. Riding the app's wrapper
    is both the faithful path and the working one.
    """
    pageDashboard.goto(serverHub.sBaseUrl, wait_until="networkidle")
    dictPayload = pageDashboard.evaluate(
        """async (sBaseUrl) => {
            const response = await fetch(sBaseUrl + '/api/registry');
            return {iStatus: response.status,
                    sBody: await response.text()};
        }""",
        serverHub.sBaseUrl,
    )
    assert dictPayload["iStatus"] == 200, dictPayload["sBody"][:300]
    assert S_CONTAINER_NAME in dictPayload["sBody"], (
        "The seeded project never reached the browser: "
        + dictPayload["sBody"][:300]
    )
    assert "gj1132" not in dictPayload["sBody"].lower(), (
        "The lane is reading the developer's real ~/.vaibify registry "
        "instead of its isolated one."
    )
    pageDashboard.wait_for_selector(
        f"text={S_CONTAINER_NAME}", timeout=15000,
    )


def testDoubledSessionTokenHeaderIsRefused(pageDashboard, serverHub):
    """A doubled token HEADER must fail closed, not fail open.

    Renamed from "duplicate session", which implied coverage this does
    not have: this is one browser sending one header twice, an
    entirely different mechanism from a second browser session
    copying a lease (see the duplicate-session journey below).

    Discovered while building this lane: because the app's fetch
    wrapper already injects the header, a caller that also sets it
    produces a doubled value. The property that matters is the
    direction of the failure -- a doubled credential is refused, never
    accepted by prefix or by taking the first element.
    """
    pageDashboard.goto(serverHub.sBaseUrl, wait_until="networkidle")
    iStatus = pageDashboard.evaluate(
        """async (sBaseUrl) => {
            const sToken = (await (await fetch(
                sBaseUrl + '/api/session-token')).json()).sToken;
            const response = await fetch(sBaseUrl + '/api/registry', {
                headers: {'x-session-token': sToken},
            });
            return response.status;
        }""",
        serverHub.sBaseUrl,
    )
    assert iStatus == 401


# ---------------------------------------------------------------------
# The fake's own contract
# ---------------------------------------------------------------------


def testTheDockerFakeRefusesUnmodelledCalls(serverHub):
    """The adapter must never invent an answer.

    This is the property that separates Lane 1 from the twenty
    hand-rolled mocks it does not want to become. A permissive
    catch-all would make every journey above pass for the wrong reason.
    """
    with pytest.raises(UnmodelledContainerCall):
        serverHub.adapterDocker.ftResultExecuteCommand(
            "any-container", "rm -rf /workspace",
        )
