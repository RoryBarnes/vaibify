"""The environment-drift banner can be hidden, and comes back while true.

It could not be closed at all: a researcher who had read it had to
stare at it for the rest of the visit (researcher-reported,
2026-09-28). It now closes like the build warnings beside it -- for
THIS visit only. The condition does not go away by being acknowledged,
so the next readiness answer that still carries the drift renders it
again. Driven through the real readiness surface and a real click.
"""

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser


_S_SURFACE_DRIFT = """() => {
    VaibifyContainerManager.fnSurfaceReadinessOutcome({
        sStatus: "ready", bReady: true, saWarnings: [],
        listConfigurationDrift: [
            "The environment's settings changed.", "Rebuild to apply them.",
        ],
    });
}"""

_S_BANNER_STATE = """() => {
    var elBanner = document.getElementById("configurationDriftBanner");
    return {sDisplay: elBanner.style.display, sHtml: elBanner.innerHTML};
}"""


@pytest.mark.falsification
def test_the_drift_banner_closes_for_this_visit_and_returns_while_true(
    pageDashboard, serverHub,
):
    """Kills: rendering the banner without its close button."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    pageDashboard.evaluate(_S_SURFACE_DRIFT)
    dictShown = pageDashboard.evaluate(_S_BANNER_STATE)
    assert dictShown["sDisplay"] == "block"
    assert "older version of your environment" in dictShown["sHtml"]
    assert "vaibify.yml" not in dictShown["sHtml"], (
        "the heading must not assume the reader knows the file"
    )
    pageDashboard.click("#btnDismissConfigurationDrift")
    assert pageDashboard.evaluate(_S_BANNER_STATE)["sDisplay"] == "none"
    pageDashboard.evaluate(_S_SURFACE_DRIFT)
    assert pageDashboard.evaluate(_S_BANNER_STATE)["sDisplay"] == "block", (
        "a drift that is still true renders again on the next answer"
    )
