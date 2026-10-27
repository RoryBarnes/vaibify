"""The Docker banner says what failed: Docker, or only the check.

When vaibify's own server stopped, the banner read "Docker is
unavailable" over a Docker that was answering, because every failed
check was rendered under that headline (researcher-reported,
2026-10-26). A check that never reached the server is reported as the
server not answering, with Reload, the one action that does not ask the
dead server; a check the server answered with an error is reported as
not checked. Only the server's own answer that Docker does not respond
earns "Docker is unavailable".
"""

import pytest


pytestmark = pytest.mark.browser


def _fnOpenTheHubWithTheCheck(page, serverHub, fnAnswer):
    page.route("**/api/system/docker-status", fnAnswer)
    page.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    page.wait_for_selector("#dockerStatusBanner", state="visible",
                           timeout=15000)


@pytest.mark.falsification
def testAServerThatDoesNotAnswerIsNeverReportedAsDockerUnavailable(
        pageDashboard, serverHub):
    """Kills: "Docker is unavailable" over a check that never got an answer.

    The request is refused at the network layer, as it is when the hub
    has been shut down; Docker itself was never asked.
    """
    _fnOpenTheHubWithTheCheck(
        pageDashboard, serverHub,
        lambda routeIntercepted: routeIntercepted.abort("connectionrefused"))
    pageDashboard.wait_for_selector("#btnReloadDashboard", timeout=15000)
    sBanner = pageDashboard.text_content("#dockerStatusBanner")
    assert "vaibify's server is not answering." in sBanner
    assert "Docker was not checked." in sBanner
    assert "Docker is unavailable" not in sBanner
    assert pageDashboard.query_selector("#btnRetryDockerStatus") is None
    assert pageDashboard.query_selector("#btnSetupDocker") is None


def testACheckTheServerAnsweredWithAnErrorSaysDockerWasNotChecked(
        pageDashboard, serverHub):
    _fnOpenTheHubWithTheCheck(
        pageDashboard, serverHub,
        lambda routeIntercepted: routeIntercepted.fulfill(
            status=500, content_type="application/json",
            body='{"detail": "probe crashed"}'))
    pageDashboard.wait_for_selector("#btnRetryDockerStatus", timeout=15000)
    sBanner = pageDashboard.text_content("#dockerStatusBanner")
    assert sBanner.startswith("Docker was not checked.")
    assert "Docker is unavailable" not in sBanner
    assert pageDashboard.query_selector("#btnReloadDashboard") is None
