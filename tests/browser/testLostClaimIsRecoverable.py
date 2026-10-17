"""A refusal that names an action leaves you somewhere you can do it.

When a claim is genuinely gone — the browser was away long enough, or
another session took the project — selecting a workflow is refused with
"claim this before connecting to it". The researcher who met that
message was standing on the workflow picker, which has no claim control
of any kind: the project TILE is the claim control, and it is one
screen back. There was no kebab, no button, nothing. The dashboard was
not wedged, but it was indistinguishable from wedged.

So the refusal carries a machine-readable code, and the dashboard now
performs the named recovery itself: it re-claims the project and
retries the selection, so a reaped claim costs one silent round trip
instead of a three-click toast dance (2026-08-20). Only a reclaim that
FAILS — another vaibify process holds the flock — walks back to the
project list, where the tile is the claim control. Keying on the code
rather than the prose is deliberate: the sibling 409 on this route
("in use in another browser session") has no recovery to offer, and a
recovery keyed on the word "claim" would fire for it too and send a
researcher to re-click a tile that will refuse them again.

The claim is dropped here the way the reaper drops one — the owner
record is removed from the live hub — rather than by waiting out a
window, so the test drives the real refusal on the real route without
being a timing test.
"""

import time

import pytest

from tests.browser.conftest import (
    S_HOST_PROJECT_READY,
    S_HOST_WORKFLOW_NAME,
)


pytestmark = pytest.mark.browser

@pytest.fixture(autouse=True)
def fixtureDropClaimsBetweenJourneys(serverHub):
    """Give every claim back after each journey.

    The page's teardown, which runs first, has already waited for the
    journey's work to drain (``fnWaitForTheHubToDrain``).
    """
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


def _fnReachTheWorkflowPicker(page, serverHub):
    """Claim the host project and stop on its workflow list."""
    page.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    page.wait_for_selector(
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"]',
        timeout=15000,
    )
    page.click(
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"] '
        '.container-tile-main',
    )
    page.wait_for_selector("#modalConfirm", timeout=10000)
    page.click("#btnConfirmOk")
    page.wait_for_selector(f'text={S_HOST_WORKFLOW_NAME}', timeout=20000)


F_QUIET_HUB_TIMEOUT_SECONDS = 20.0


def _fbProjectJournalIsBusy():
    """Return True while a journaled operation runs on the ready project.

    The same resolution the claim route consults before it refuses a
    project as in use.
    """
    from vaibify.config import operationJournal
    return operationJournal.fdictResolveContainerJournal(
        S_HOST_PROJECT_READY, None, bPersistResolution=False,
    )["sResolution"] == operationJournal.S_RESOLUTION_BUSY


def _ftDropTheClaimWhenTheHubIsQuiet(serverHub):
    """Pop the owner record only at a moment nothing is running.

    Return ``(bDropped, recordOwner)``. Runs ON the hub's loop, so no
    request can be admitted between the check and the pop.
    """
    from vaibify.gui import commitCarrier
    appState = serverHub.app.state
    if (
        serverHub.fiCountInFlightRequests() > 0
        or _fbProjectJournalIsBusy()
        or commitCarrier.fbContainerHasLiveMutationWork(
            appState, S_HOST_PROJECT_READY)
    ):
        return False, None
    return True, appState.dictContainerOwners.pop(S_HOST_PROJECT_READY, None)


def _fnTakeTheClaimAway(serverHub):
    """Drop the owner record, exactly as the idle reaper drops one.

    The lapse modelled here is the one the recovery exists for: a page
    the browser froze, whose claim then outlived its presence window.
    Nothing that page started is still running when its claim goes.
    Dropped at an arbitrary instant instead, the claim could go while
    one of the page's own polls was mid-read; the recovery's claim was
    then refused as in use by that read, and the journey failed. On an
    idle machine the reads finished in milliseconds and the race was
    rarely lost; under parallel workers it was lost often. So the drop
    waits for a quiet hub -- no request in flight, no journaled
    operation, no guarded work (the reaper's own veto) -- and checks
    and drops in one tick of the hub's loop.
    """
    from vaibify.config.containerLock import fnReleaseContainerLock
    fDeadline = time.monotonic() + F_QUIET_HUB_TIMEOUT_SECONDS
    while True:
        bDropped, recordOwner = serverHub.fgenericCallOnTheHubLoop(
            lambda: _ftDropTheClaimWhenTheHubIsQuiet(serverHub))
        if bDropped:
            break
        if time.monotonic() > fDeadline:
            pytest.fail(
                f"the hub never fell quiet within "
                f"{F_QUIET_HUB_TIMEOUT_SECONDS:g} s, so the claim on "
                f"{S_HOST_PROJECT_READY} was never dropped")
        time.sleep(0.02)
    if recordOwner is not None and getattr(
        recordOwner, "fileHandleLock", None,
    ) is not None:
        try:
            fnReleaseContainerLock(recordOwner.fileHandleLock)
        except OSError:
            pass


@pytest.mark.falsification
def testALostClaimIsReclaimedAndTheWorkflowOpens(
    pageDashboard, serverHub,
):
    """A reaped claim is re-taken silently and the click just works.

    The reaper collects a claim after thirty socket-less seconds, and
    the workflow picker holds no socket — so a researcher who paused
    to read the list lost their claim by their very next click and was
    walked through a three-click toast dance to get back in (live
    report, 2026-08-20). The refusal's named recovery is a claim plus
    a retry, which the dashboard now performs itself: the record is
    unowned, so arbitration grants it back to this session and the
    workflow opens.

    Kills: dropping the reclaim-and-retry from the claim-required
    branch, which restores the bounce to the Environment hub.
    """
    _fnReachTheWorkflowPicker(pageDashboard, serverHub)
    _fnTakeTheClaimAway(serverHub)
    pageDashboard.click(f'text={S_HOST_WORKFLOW_NAME}')
    pageDashboard.wait_for_selector("#hostModeBadge", timeout=20000)
    assert S_HOST_PROJECT_READY in (
        serverHub.app.state.dictContainerOwners
    ), "the workflow opened without re-claiming the project"
    assert pageDashboard.listPageErrors == [], pageDashboard.listPageErrors


def _fnHandTheClaimToAnotherSession(serverHub):
    """Leave the record owned, by somebody else.

    This is the sibling 409 — "in use in another browser session" —
    which is the refusal the recovery must NOT act on.
    """
    recordOwner = serverHub.app.state.dictContainerOwners[
        S_HOST_PROJECT_READY
    ]
    recordOwner.sBrowserSessionId = "someOtherBrowserSession"
    recordOwner.sLeaseId = "someOtherLease"


@pytest.mark.falsification
def testAnInUseRefusalDoesNotBounceYouBackToTheTile(
    pageDashboard, serverHub,
):
    """The other direction, and it has to be the OTHER REFUSAL.

    Recovering unconditionally cannot be caught by a successful
    selection — the recovery lives in the catch block, so a request
    that does not fail never reaches it, and a test built that way
    reports a pass for a mutation it cannot see. (It did: this test
    was written that way first and its mutant survived.) The
    observable case is a workflow selection refused for a reason the
    researcher CANNOT fix by re-clicking the tile. Sending them there
    to be refused a second time is the failure.

    The claim request is the deterministic witness. A recovery sends
    its claim BEFORE it says anything, so by the time the toast is up
    a wrongful recovery has always been sent; the bounce back to the
    tile comes a round trip later still, so asserting only that the
    picker is on screen raced it, and the mutant survived a CI run.

    Kills: recovering on any error instead of on the refusal code.
    """
    _fnReachTheWorkflowPicker(pageDashboard, serverHub)
    _fnHandTheClaimToAnotherSession(serverHub)
    listClaimUrls = []
    pageDashboard.on("request", lambda request: (
        listClaimUrls.append(request.url)
        if request.url.endswith("/claim") else None
    ))
    pageDashboard.click(f'text={S_HOST_WORKFLOW_NAME}')
    pageDashboard.wait_for_selector("#toastContainer", timeout=20000)
    pageDashboard.wait_for_function(
        """() => document.getElementById('toastContainer')
            .innerText.trim().length > 0""",
        timeout=20000,
    )
    sToastText = pageDashboard.text_content("#toastContainer")
    assert "in use" in sToastText.lower(), sToastText
    assert listClaimUrls == [], (
        "an in-use refusal was answered with a reclaim: " +
        str(listClaimUrls)
    )
    assert pageDashboard.is_visible("#btnNoWorkflow"), (
        "an unfixable refusal sent the researcher back to the project "
        "list, where re-selecting the project refuses them again"
    )
    assert pageDashboard.listPageErrors == [], pageDashboard.listPageErrors


def testAClaimThatStillHoldsOpensTheWorkflow(pageDashboard, serverHub):
    """The ordinary path still works, which nothing above asserts."""
    _fnReachTheWorkflowPicker(pageDashboard, serverHub)
    pageDashboard.click(f'text={S_HOST_WORKFLOW_NAME}')
    pageDashboard.wait_for_selector("#hostModeBadge", timeout=20000)
    assert pageDashboard.listPageErrors == [], pageDashboard.listPageErrors


@pytest.mark.falsification
def testALostClaimIsReclaimedWhenABlankProjectOpens(
    pageDashboard, serverHub,
):
    """The recovery belongs to the REFUSAL, not to the workflow picker.

    ``claim-required`` is raised on ``/api/connect``, and the picker is
    only one of its callers: opening a Blank Project posts the same
    route and met the same reaped claim. It reported the refusal's own
    sentence -- "Select it again on the project list to claim it" --
    from a screen that is not the project list, naming a control the
    researcher could not reach. Recovering here is the same claim plus
    the same retry, so the click just works.

    Kills: recovering only inside the workflow picker, leaving every
    other ``/api/connect`` caller to print an instruction its
    researcher cannot act on.
    """
    _fnReachTheWorkflowPicker(pageDashboard, serverHub)
    _fnTakeTheClaimAway(serverHub)
    pageDashboard.click("#btnNoWorkflow")
    pageDashboard.wait_for_selector("#mainLayout.active", timeout=20000)
    assert S_HOST_PROJECT_READY in (
        serverHub.app.state.dictContainerOwners
    ), "the Blank Project opened without re-claiming the project"
    assert pageDashboard.listPageErrors == [], pageDashboard.listPageErrors


def testABusyProjectSaysAnOperationIsFinishingNotAnotherSession(
    pageDashboard, serverHub,
):
    """A BUSY journal names its cause, not a session that does not exist.

    The listing locks a project whose journal holds a live in-flight
    operation -- usually this very hub's own, a request a closed tab
    started. The tile said "In use by another vaibify session" and sent
    the researcher to close a window or run ``vaibify sessions``, where
    nothing would be found (measured, 2026-10-11). Driven with a real
    live process recorded as a host exec, the shape that was measured.

    Kills: rendering a BUSY journal with the cross-hub lock message.
    """
    import subprocess
    from vaibify.config import operationJournal
    processHolder = subprocess.Popen(["sleep", "60"], start_new_session=True)
    sOperationId = operationJournal.fsPrepareOperation(
        S_HOST_PROJECT_READY, "host-exec", "exec")
    try:
        operationJournal.fnPromoteOperationToInFlight(
            S_HOST_PROJECT_READY, sOperationId,
            {"iHolderPid": processHolder.pid,
             "iHolderProcessGroup": processHolder.pid},
        )
        sTile = f'.container-tile[data-name="{S_HOST_PROJECT_READY}"]'
        pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
        pageDashboard.wait_for_selector(
            f'{sTile}[data-locked="true"]', timeout=15000)
        pageDashboard.click(f"{sTile} .container-tile-main")
        pageDashboard.wait_for_function(
            """() => document.getElementById('toastContainer')
                .innerText.trim().length > 0""",
            timeout=10000,
        )
        sToastText = pageDashboard.text_content("#toastContainer")
        assert "still finishing" in sToastText, sToastText
        assert "another vaibify session" not in sToastText, sToastText
        assert "vaibify sessions" not in sToastText, sToastText
        assert pageDashboard.listPageErrors == [], pageDashboard.listPageErrors
    finally:
        processHolder.kill()
        processHolder.wait()
        operationJournal.fnSettleOperation(S_HOST_PROJECT_READY, sOperationId)
