"""A failure shown inside a panel or card ends the same way a toast does.

The environment hub's toasts learned to end in "Click to run a
diagnosis"; the Files panel still wrote "Error loading directory" over
whatever the server had said, and the reproduce-a-published-project
card wrote the server's sentence with nowhere to go next. Both are
asserted here in a real browser: the server's sentence is what the
researcher reads, and the diagnosis is one click away from where they
are looking.
"""

import json

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow

pytestmark = pytest.mark.browser

S_CLAIM_MESSAGE = (
    "This project is no longer claimed by this session. Select it again "
    "on the project list to claim it."
)
S_EVIDENCE_MESSAGE = (
    "The exec could not be started: the daemon answered 'connection "
    "refused'. Start the environment again from the project list."
)
S_STAGE_REFUSAL = (
    "Refused: the source names no vaibify project; there is no "
    ".vaibify/projects directory at the resolved commit."
)


def _fnInterceptTheDoctor(pageDashboard):
    pageDashboard.route(
        "**/api/system/doctor",
        lambda routeIntercepted: routeIntercepted.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"listFindings": [{
                "sName": "docker-daemon", "sLevel": "fail",
                "sScope": "host",
                "sMessage": "Docker daemon not reachable.",
                "sRemediation": "Start the daemon.",
                "sCommand": "colima start",
            }]}),
        ),
    )


@pytest.mark.falsification
def testTheFilesPanelShowsTheRefusalAndOffersTheDiagnosis(
    pageDashboard, serverHub,
):
    """The panel shows the server's sentence and the diagnosis link.

    Kills: writing "Error loading directory" over the sentence again,
    which hid the one instruction the researcher could act on.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    _fnInterceptTheDoctor(pageDashboard)
    pageDashboard.route(
        "**/api/files/**",
        lambda routeIntercepted: routeIntercepted.fulfill(
            status=409, content_type="application/json",
            body=json.dumps({"detail": {
                "sMessage": S_CLAIM_MESSAGE, "sRefusal": "claim-required",
            }}),
        ),
    )
    pageDashboard.click('.left-tab[data-panel="files"]')
    pageDashboard.wait_for_selector(
        "#listFiles .files-panel-failure", timeout=10000,
    )
    sPanel = pageDashboard.locator("#listFiles").inner_text()
    assert "Select it again on the project list" in sPanel
    assert "Error loading directory" not in sPanel
    pageDashboard.click("#listFiles .diagnosis-link")
    pageDashboard.wait_for_selector(
        "#modalInfo .diagnosis-finding--fail", timeout=10000,
    )
    assert "colima start" in pageDashboard.locator("#modalInfo").inner_text()


@pytest.mark.falsification
def testTheReproduceCardShowsTheServersSentenceAndOffersTheDiagnosis(
    pageDashboard, serverHub,
):
    """The card's source stage shows the refusal with a next step.

    Kills: both card stages writing the raw message as text again,
    with no diagnosis to click.
    """
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_selector("#btnAddContainer", timeout=15000)
    _fnInterceptTheDoctor(pageDashboard)
    pageDashboard.route(
        "**/api/reproductions/stage",
        lambda routeIntercepted: routeIntercepted.fulfill(
            status=422, content_type="application/json",
            body=json.dumps({"detail": S_STAGE_REFUSAL}),
        ),
    )
    pageDashboard.click("#btnAddContainer")
    pageDashboard.wait_for_selector("#modalAddChoice", timeout=5000)
    pageDashboard.click("#btnChoiceKindReproduce")
    pageDashboard.wait_for_selector(
        "#modalReproducePublished", state="visible", timeout=5000,
    )
    pageDashboard.fill("#reproduceSourceInput", "https://host.example/x.git")
    pageDashboard.click("#btnReproduceStage")
    pageDashboard.wait_for_selector(
        "#reproduceSourceError .diagnosis-link", timeout=10000,
    )
    sError = pageDashboard.locator("#reproduceSourceError").inner_text()
    assert "no .vaibify/projects directory" in sError
    assert pageDashboard.is_hidden("#reproduceStageConfirm")
    pageDashboard.click("#reproduceSourceError .diagnosis-link")
    pageDashboard.wait_for_selector(
        "#modalInfo .diagnosis-finding--fail", timeout=10000,
    )
    assert "colima start" in pageDashboard.locator("#modalInfo").inner_text()


@pytest.mark.falsification
def testAServerSentenceQuotingDaemonEvidenceIsNotRewritten(
    pageDashboard, serverHub,
):
    """The sanitizer matches on phrases like "connection refused" to
    translate raw daemon text. A sentence the SERVER wrote for the
    researcher may quote that phrase as evidence, and the rewrite then
    replaced the remedy with "Cannot connect to Docker" -- a remedy for
    a failure that had not happened.

    Kills: routing every sentence through the phrase sanitizer, so the
    quoted evidence is read as the daemon's own failure.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    pageDashboard.route(
        "**/api/files/**",
        lambda routeIntercepted: routeIntercepted.fulfill(
            status=409, content_type="application/json",
            body=json.dumps({"detail": {"sMessage": S_EVIDENCE_MESSAGE}}),
        ),
    )
    pageDashboard.click('.left-tab[data-panel="files"]')
    pageDashboard.wait_for_selector(
        "#listFiles .files-panel-failure", timeout=10000,
    )
    sPanel = pageDashboard.locator("#listFiles").inner_text()
    assert "Start the environment again from the project list" in sPanel
    assert "Cannot connect to Docker" not in sPanel
