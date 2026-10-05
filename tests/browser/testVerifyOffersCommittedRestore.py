"""A clone whose files moved is offered the committed versions, not Regenerate.

The Level 3 readiness gate refuses when the manifest misdescribes the
files it names. On a clone a reader re-ran outside the author's
environment, the checklist's usual remedy -- regenerate the envelope --
would replace the AUTHOR's manifest with the reader's own bytes, and
the verification would then compare the reader with themselves. When
the manifest is someone else's and the project runs in a container,
Verify offers to restore the committed versions instead, then asks the
readiness question again and carries on to the ordinary copy warning.

The readiness, the difference list and the restore are intercepted:
the lane has no container, and what is under test is which remedy the
page names and in what order it acts.
"""

import json

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow
from tests.browser.testVerifyRefusesBeforeItWarns import (
    fixtureDropClaimsBetweenTests,  # noqa: F401 -- autouse fixture
)


pytestmark = pytest.mark.browser

S_CONFIRM_MODAL = "#modalConfirm"
S_INFO_MODAL = "#modalInfo"
S_PINNED_OUTPUT = "Step/result.json"


def _fnAnswerReadiness(page, dictState):
    """Serve the route's shape, the manifest contradicted until restored."""
    from vaibify.reproducibility.levelGates import fdictL3ReadinessGaps

    def fnAnswer(route):
        dictGaps = {
            sKey: (True if isinstance(objValue, bool) else objValue)
            for sKey, objValue in fdictL3ReadinessGaps(
                {}, "/nonexistent-repo-for-shape").items()
        }
        for sContainerFact in (
            "bImageMatchesDeclaredPackages",
            "bDockerfileDescribesPinnedImage",
            "bLockDoesNotBlockVerification",
        ):
            dictGaps[sContainerFact] = True
        dictGaps["bManifestMatchesTheFiles"] = dictState["bRestored"]
        dictGaps["bL3ReadinessOK"] = dictState["bRestored"]
        route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({
                "iProofLevel": 2, "dictL3ReadinessGaps": dictGaps,
            }),
        )

    page.route("**/api/workflow/**/level3/readiness", fnAnswer)


def _fnAnswerDifferences(page, sOwnership):
    page.route(
        "**/api/workflow/**/committed-file-differences",
        lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({
                "listDifferingPaths": [S_PINNED_OUTPUT],
                "sManifestOwnership": sOwnership,
                "bRestoreRunsInContainer": True,
            }),
        ),
    )


def _flistRecordRestoresAndVerifies(page, dictState):
    listRequests = []

    def fnRestore(route):
        listRequests.append("restore")
        dictState["bRestored"] = True
        route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"listRestoredPaths": [S_PINNED_OUTPUT]}),
        )

    def fnVerify(route):
        listRequests.append("verify")
        route.fulfill(
            status=200, content_type="application/json",
            body='{"bAccepted": true, "sPhase": "starting"}',
        )

    page.route("**/api/workflow/**/restore-committed-files", fnRestore)
    page.route("**/api/workflow/**/level3/verify", fnVerify)
    return listRequests


@pytest.mark.falsification
def test_a_foreign_manifest_is_offered_the_committed_files_then_the_copy(
    pageDashboard, serverHub,
):
    """Restore first, readiness again, then the ordinary copy warning.

    Kills: dropping the offer from the Verify pre-flight, which sends
    the reader back to the checklist whose remedy regenerates the
    author's manifest.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    dictState = {"bRestored": False}
    _fnAnswerReadiness(pageDashboard, dictState)
    _fnAnswerDifferences(pageDashboard, "foreign")
    listRequests = _flistRecordRestoresAndVerifies(pageDashboard, dictState)

    pageDashboard.evaluate(
        "() => VaibifyApp.fnRunProjectAction('verify-l3', '', null)")
    pageDashboard.wait_for_selector(
        S_CONFIRM_MODAL, state="visible", timeout=5000)
    sOffer = pageDashboard.inner_text(S_CONFIRM_MODAL)
    assert "Restore the committed files first" in sOffer, sOffer
    assert "Nothing outside the container is touched" in sOffer, sOffer
    assert listRequests == []

    pageDashboard.click("#btnConfirmOk")
    pageDashboard.wait_for_function(
        "() => { const el = document.querySelector('#modalConfirm');"
        " return el && el.innerText.includes('Copy and verify'); }",
        timeout=10000,
    )
    assert listRequests == ["restore"], (
        "the verification started before the reader agreed to the copy, "
        f"or the restore never ran: {listRequests}"
    )
    assert pageDashboard.listPageErrors == []


def test_the_authors_own_manifest_keeps_the_ordinary_checklist(
    pageDashboard, serverHub,
):
    """Only someone else's manifest changes the remedy."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    dictState = {"bRestored": False}
    _fnAnswerReadiness(pageDashboard, dictState)
    _fnAnswerDifferences(pageDashboard, "own")
    listRequests = _flistRecordRestoresAndVerifies(pageDashboard, dictState)

    pageDashboard.evaluate(
        "() => VaibifyApp.fnRunProjectAction('verify-l3', '', null)")
    pageDashboard.wait_for_selector(
        S_INFO_MODAL, state="visible", timeout=5000)
    sText = pageDashboard.inner_text(S_INFO_MODAL)
    assert "Regenerate the envelope" in sText, sText
    assert listRequests == []
    assert pageDashboard.listPageErrors == []
