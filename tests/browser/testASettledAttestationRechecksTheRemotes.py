"""A verification that settles re-checks the published copies at once.

A settled Level 3 verification writes the attestation and the
reproduced manifest, so every published copy is behind it the moment
it lands -- yet the GitHub badge kept its pre-run answer until someone
pressed Verify, beside an Attestation row that read as passing
(researcher-reported, 2026-09-29).

Driven through the real file-status poll: the hub's own response is
fetched and only the running flag is rewritten, so the dashboard walks
the same path a real verification's end would take it down.
"""

import json

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser


@pytest.fixture(autouse=True)
def fixtureDropClaimsBetweenTests(serverHub):
    """Give every claim back after each test."""
    yield
    from vaibify.config.containerLock import fnReleaseContainerLock
    dictContainerOwners = serverHub.app.state.dictContainerOwners
    for _sName, recordOwner in list(dictContainerOwners.items()):
        fileHandle = getattr(recordOwner, "fileHandleLock", None)
        if fileHandle is not None:
            try:
                fnReleaseContainerLock(fileHandle)
            except OSError:
                pass
    dictContainerOwners.clear()
    serverHub.app.state.dictSessionOwner.clear()


def _fnWaitUntil(pageDashboard, fbCondition, iTimeoutSeconds=30):
    """Wait on the page's clock until a condition holds, or give up."""
    for _ in range(iTimeoutSeconds * 4):
        if fbCondition():
            return
        pageDashboard.wait_for_timeout(250)


@pytest.mark.falsification
def test_a_settled_verification_rechecks_the_remotes_once(
    pageDashboard, serverHub,
):
    """Running then settled: exactly one new re-check of the remotes.

    Kills: adopting the new envelope detail without comparing it with
    the running flag it replaces.
    """
    dictRunning = {"bValue": True}
    listRefreshes = []
    listServed = []

    def fnFileStatus(route):
        response = route.fetch()
        dictStatus = json.loads(response.text())
        dictDetail = dictStatus.get("dictWorkflowEnvelopeDetail") or {}
        dictDetail["bRebuildAttestationRunning"] = dictRunning["bValue"]
        dictStatus["dictWorkflowEnvelopeDetail"] = dictDetail
        listServed.append(dictRunning["bValue"])
        route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps(dictStatus),
        )

    def fnRefresh(route):
        listRefreshes.append(route.request.method)
        route.fulfill(status=200, content_type="application/json",
                      body='{"listStarted": []}')

    pageDashboard.route("**/api/pipeline/*/file-status*", fnFileStatus)
    pageDashboard.route("**/api/workflow/*/remotes/refresh", fnRefresh)
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    # Opening a project re-checks the remotes too; that one must have
    # landed before the count is taken, or it reads as the settle's.
    _fnWaitUntil(
        pageDashboard, lambda: True in listServed and listRefreshes,
    )
    pageDashboard.wait_for_timeout(1000)
    iBeforeSettle = len(listRefreshes)
    dictRunning["bValue"] = False
    _fnWaitUntil(pageDashboard, lambda: len(listRefreshes) > iBeforeSettle)
    iAfterSettle = len(listRefreshes)
    iServedAtSettle = len(listServed)
    _fnWaitUntil(pageDashboard, lambda: len(listServed) >= iServedAtSettle + 2)

    assert iAfterSettle == iBeforeSettle + 1, (
        f"expected one re-check when the verification settled; saw "
        f"{iAfterSettle - iBeforeSettle}"
    )
    assert len(listRefreshes) == iAfterSettle, (
        "the re-check repeated on later polls of a settled verification"
    )
