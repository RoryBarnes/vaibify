"""A failed save says which failure it was.

A researcher toggled a step's run checkbox and got "Save failed —
reloaded to match the server so the dashboard doesn't show an unsaved
change." Twice, because the reload restored the checkbox and they
clicked again. That sentence is the same for a refused lane, a server
exception, and a hub that is not answering — three different problems
with three different fixes — so neither they nor the maintainer could
tell which had happened, and the diagnosis went nowhere until the
server log was read.

The message now carries the status and the server's own detail, which
is the string that names the operation that failed. Both directions are
driven here through a real browser against the real handler, with the
route intercepted: a message that never names a reason is the defect,
and a message that invents one where the server gave none would be
worse.
"""

import pytest

from tests.browser.fakeDockerAdapter import S_CONTAINER_ID, S_CONTAINER_NAME


pytestmark = pytest.mark.browser


@pytest.fixture(autouse=True)
def fixtureDropClaimsBetweenJourneys(serverHub):
    """Give every claim back; the hub outlives the page."""
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


def _fsToastAfterAFailedStepEdit(page, serverHub, iStatus, sDetail):
    """Drive a real step edit whose PUT is answered with a failure."""
    page.goto(serverHub.fsBootstrapUrl(), wait_until="networkidle")
    page.route(
        "**/api/steps/**",
        lambda route: route.fulfill(
            status=iStatus,
            content_type="application/json",
            body='{"detail": {"sMessage": "%s"}}' % sDetail,
        ),
    )
    page.evaluate(
        """async ([sContainerId, sName]) => {
            const dictClaim = await VaibifyApi.fdictPost(
                '/api/registry/' + encodeURIComponent(sName) + '/claim',
                {});
            VaibifyApp.fnRecordClaimedLease(sName, dictClaim.sLeaseId);
            await VaibifyApp.fnEnterNoWorkflow(sContainerId);
            await VaibifyApp.fnToggleStepEnabled(0, false);
        }""",
        [S_CONTAINER_ID, S_CONTAINER_NAME],
    )
    page.wait_for_function(
        """() => document.getElementById('toastContainer')
            .innerText.trim().length > 0""",
        timeout=10000,
    )
    return " ".join(page.text_content("#toastContainer").split())


@pytest.mark.falsification
def testAFailedSaveNamesTheStatusAndTheReason(pageDashboard, serverHub):
    """The message must distinguish this failure from the others.

    Kills: one fixed sentence for every non-conflict failure, which is
    what shipped and what made a real report undiagnosable.
    """
    sToast = _fsToastAfterAFailedStepEdit(
        pageDashboard, serverHub, 500, "the step update failed",
    )
    assert "500" in sToast, sToast
    assert "the step update failed" in sToast, sToast


@pytest.mark.falsification
def testAConflictKeepsItsOwnMessage(pageDashboard, serverHub):
    """The other direction: a 409 is not a failure, it is a race.

    A stale fingerprint means somebody else changed the project, and
    the researcher's next move is to re-apply the edit -- not to read a
    status code. Folding it into the generic message would lose that.

    Kills: describing every failure with the status sentence.
    """
    sToast = _fsToastAfterAFailedStepEdit(
        pageDashboard, serverHub, 409, "fingerprint mismatch",
    )
    assert "changed since you loaded it" in sToast, sToast
    assert "409" not in sToast, sToast
