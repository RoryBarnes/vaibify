"""A promotion releases the open project's claim; the page must not undo it.

Promoting from inside the open project makes the hub release this tab's
claim and rename the project, and the page re-enters under the new name
once the hub has answered. A poll made in the gap is refused for want of
a claim the researcher just gave up, and a page that recovered it would
claim a project that no longer has that name. The answer is held here
so the gap is not a matter of timing.
"""

import pytest

from tests.browser.conftest import S_HOST_PROJECT_READY
from tests.browser.testALostClaimRecoversWhereverItIsMet import (
    _fdictAwaitProbe,
    _flistClaimPosts,
    _flistRecordRequests,
    _fnProbeWithoutWaiting,
)
from tests.browser.testLostClaimIsRecoverable import (  # noqa: F401
    fixtureDropClaimsBetweenJourneys,
)
from tests.browser.testPromoteFromInsideJourney import (
    S_NEW_PROJECT_NAME,
    _fnDrivePromoteWizardToSubmit,
    _fnOpenTheSandboxWorkflow,
)


pytestmark = pytest.mark.browser


@pytest.mark.falsification
def test_a_poll_refused_while_a_promotion_is_answered_does_not_take_the_claim_back(
    pageDashboard, serverHub,
):
    """The hub promotes, its answer is held, and a poll is refused meanwhile.

    Kills: letting the claim recovery run while a promotion releases the
    claim, which re-claims the project under the name it no longer has.
    """
    listHeld = []
    listRequests = _flistRecordRequests(pageDashboard)
    _fnOpenTheSandboxWorkflow(pageDashboard, serverHub)
    iClaimsBefore = len(_flistClaimPosts(listRequests))
    pageDashboard.route(
        "**/api/registry/*/promote-to-host-project",
        lambda routePromote: listHeld.append(
            (routePromote, routePromote.fetch())))
    _fnDrivePromoteWizardToSubmit(pageDashboard)
    for _ in range(100):
        if listHeld:
            break
        pageDashboard.wait_for_timeout(100)
    assert listHeld, "the promotion never reached the hub"
    _fnProbeWithoutWaiting(
        pageDashboard,
        f"/api/pipeline/{S_HOST_PROJECT_READY}/state?probe=promoting",
        "promoting")
    dictOutcome = _fdictAwaitProbe(pageDashboard, "promoting")
    assert dictOutcome["bOk"] is False and dictOutcome["bHandled"] is True
    assert len(_flistClaimPosts(listRequests)) == iClaimsBefore
    routePromote, responseHub = listHeld[0]
    routePromote.fulfill(response=responseHub)
    pageDashboard.wait_for_selector("#mainLayout.active", timeout=20000)
    pageDashboard.unroute_all(behavior="ignoreErrors")
    assert S_HOST_PROJECT_READY not in serverHub.app.state.dictContainerOwners
    assert S_NEW_PROJECT_NAME in serverHub.app.state.dictContainerOwners
