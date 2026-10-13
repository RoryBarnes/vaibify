"""A pinned image's settings note is the server's, under its own heading.

A container running the author's pinned image used to meet the
configuration-drift banner -- "an older version of your environment",
with a rebuild as the remedy -- although that image is not out of date
and a rebuild would replace it. The server now sends those containers
their own sentences on their own key, and the dashboard shows them in
their own banner, verbatim, never beside the rebuild banner.

WHAT THIS DOES NOT COVER: which containers get which sentences
(``testPinnedEnvironmentDrift.py``).
"""

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser

LIST_SENTENCES = [
    "A first sentence the server wrote.",
    "A second sentence the server wrote.",
]

_S_SURFACE = """(listPinned) => {
    VaibifyContainerManager.fnSurfaceReadinessOutcome({
        sStatus: "ready", bReady: true, saWarnings: [],
        listConfigurationDrift: [], listPinnedEnvironmentDrift: listPinned,
        listResourceLimitDrift: [], listResourceAdvisories: [],
    });
}"""

_S_BANNER_STATE = """() => {
    var elBanner = document.getElementById("pinnedEnvironmentDriftBanner");
    return {
        sDisplay: elBanner.style.display,
        sText: elBanner.textContent,
        listItems: Array.from(elBanner.querySelectorAll("li")).map(
            function (elItem) { return elItem.textContent; }),
        sDriftDisplay: document.getElementById(
            "configurationDriftBanner").style.display,
    };
}"""


@pytest.mark.falsification
def testThePinnedNoteShowsTheServersSentencesUnderItsOwnHeading(
    pageDashboard, serverHub,
):
    """Kills: dropping the pinned sentences instead of rendering them."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    pageDashboard.evaluate(_S_SURFACE, LIST_SENTENCES)
    dictShown = pageDashboard.evaluate(_S_BANNER_STATE)
    assert dictShown["sDisplay"] == "block"
    assert dictShown["listItems"] == LIST_SENTENCES
    assert "pinned" in dictShown["sText"]
    assert "older version" not in dictShown["sText"]
    assert dictShown["sDriftDisplay"] == "none", (
        "the rebuild banner must not appear beside the pinned note"
    )
    pageDashboard.click("#btnDismissPinnedEnvironmentDrift")
    assert pageDashboard.evaluate(_S_BANNER_STATE)["sDisplay"] == "none"
    pageDashboard.evaluate(_S_SURFACE, [])
    assert pageDashboard.evaluate(_S_BANNER_STATE)["sDisplay"] == "none"
    assert pageDashboard.listPageErrors == []
