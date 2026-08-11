"""A host project, opened and run in a real browser, with no adapter.

Every other browser test in this lane drives the fail-closed Docker
adapter, which answers a modelled command with a modelled reply. This
one drives **nothing of the sort**. A host project routes to the real
``HostConnection``, so what runs here is the actual gated and
journaled launch, against an actual git repository on the actual
filesystem, and the step's output file is a real file this test then
reads off disk.

That is the whole point. The lane's own contract file says a green
browser run says nothing about container launch, file ownership, or
real transport — because for a container project it cannot. For a host
project there is no substrate to fake: the researcher's machine IS the
substrate, and this lane runs on a machine. So this is the one journey
in the suite where "the dashboard ran a pipeline step and the output
appeared" is a claim about production code all the way down.

WHAT IT COVERS, from the plan's item 15: create is covered by the
wizard tests beside this one; here it is warn, claim, open, run, and
read the output back. It dwells through a full polling interval and
enters the Repositories tab, because the failure this catches is not a
broken button — it is an activation surface that fires a dozen
requests the moment a workflow opens and quietly 500s on the one path
nobody wired for host mode.

The assertion that carries the most weight is the boring one at the
end of each test: **no failed request, no console error, no uncaught
rejection**. A host project reaching a container-only route answers
with an exception the dashboard swallows into a grey badge, and only
the network log shows it happened.
"""

import json
import os

import pytest

from tests.browser.conftest import (
    S_HOST_PROJECT_READY,
    S_HOST_STEP_NAME,
    S_HOST_STEP_OUTPUT,
    S_HOST_WORKFLOW_NAME,
)


pytestmark = pytest.mark.browser

F_POLLING_DWELL_SECONDS = 11.0


@pytest.fixture(autouse=True)
def fixtureDropClaimsBetweenJourneys(serverHub):
    """Give every claim back after each journey.

    The hub is module-scoped and the page is not, so a journey that
    claims and stops would leave the project owned by a lease nobody
    holds, and the next journey's claim is refused 409 by a session
    that no longer exists.
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


def _flistRecordFailedRequests(page):
    """Attach a failed-request recorder and return its list."""
    listFailures = []
    page.on("requestfailed", lambda request: listFailures.append(
        f"{request.method} {request.url} ({request.failure})",
    ))
    page.on("response", lambda response: (
        listFailures.append(f"{response.status} {response.url}")
        if response.status >= 400 else None
    ))
    return listFailures


def _fnOpenTheHostWorkflow(page, serverHub):
    """Warn, continue, and open the seeded workflow. Returns nothing."""
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
    page.wait_for_selector(
        f'text={S_HOST_WORKFLOW_NAME}', timeout=20000,
    )
    page.click(f'text={S_HOST_WORKFLOW_NAME}')
    page.wait_for_selector(
        f'text={S_HOST_STEP_NAME}', timeout=20000,
    )


def _fnEnterTheHostProjectWithoutAWorkflow(page, serverHub):
    """Claim the host project and enter its no-workflow view.

    The Repositories panel lives ONLY in that view — the workflow view
    shows steps, proof, files and logs — so a journey that opened a
    workflow first could never reach the tab it is about.
    """
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
    page.wait_for_selector("#btnNoWorkflow", timeout=20000)
    page.click("#btnNoWorkflow")
    page.wait_for_selector(
        '.left-tab[data-panel="repos"]', state="visible", timeout=20000,
    )


def _fsStepOutputPath(serverHub):
    """Return the on-disk path of the step's output file."""
    return os.path.join(
        serverHub.sHome, S_HOST_PROJECT_READY, S_HOST_STEP_NAME,
        S_HOST_STEP_OUTPUT,
    )


def testAHostProjectOpensItsWorkflowWithoutAFailedRequest(
    pageDashboard, serverHub,
):
    """The activation surface, driven for real on the host leg.

    Opening a workflow fires the repositories init, the git badges
    fetch, the pipeline-state recovery, the settings load and the file
    poll — a dozen requests the researcher never asked for. Any one of
    them reaching a path nobody wired for host mode answers with an
    exception the dashboard swallows into a grey badge, so the network
    log is the only place it shows.
    """
    listFailures = _flistRecordFailedRequests(pageDashboard)
    _fnOpenTheHostWorkflow(pageDashboard, serverHub)
    assert pageDashboard.is_visible("#hostModeBadge"), (
        "the uncontained badge is missing from an open host workflow"
    )
    pageDashboard.wait_for_timeout(F_POLLING_DWELL_SECONDS * 1000)
    assert listFailures == [], (
        "the host activation surface produced failed requests:\n  "
        + "\n  ".join(listFailures)
    )
    assert pageDashboard.listPageErrors == [], pageDashboard.listPageErrors
    assert pageDashboard.listConsoleErrors == [], (
        pageDashboard.listConsoleErrors
    )


def testTheRepositoriesTabAnswersForAHostProject(
    pageDashboard, serverHub,
):
    """The panel that polls every five seconds, on the host leg.

    Its status route is the one that just stopped assembling a shell
    script, and the host leg answers it through the typed-read grant
    point — a real ``git status`` in a real repository, launched
    through the gated primitive.

    Entered through Blank Project rather than through the workflow,
    because the Repositories tab exists only in the no-workflow view.
    """
    listFailures = _flistRecordFailedRequests(pageDashboard)
    _fnEnterTheHostProjectWithoutAWorkflow(pageDashboard, serverHub)
    pageDashboard.click('.left-tab[data-panel="repos"]')
    pageDashboard.wait_for_selector(
        "#tabContentRepos", state="visible", timeout=10000,
    )
    pageDashboard.wait_for_timeout(F_POLLING_DWELL_SECONDS * 1000)
    assert listFailures == [], (
        "the Repositories panel produced failed requests:\n  "
        + "\n  ".join(listFailures)
    )
    assert pageDashboard.listPageErrors == []


def testRunningAStepWritesARealFileAndTheDashboardSeesIt(
    pageDashboard, serverHub,
):
    """The end of the journey, and the only claim that matters.

    A researcher clicks Run and a file appears. Everything between the
    click and the file is production code: the pipeline WebSocket, the
    durable-task carrier, the host connection's gated launch, a real
    ``python3`` on this machine, and the file poll noticing the result.
    """
    sOutputPath = _fsStepOutputPath(serverHub)
    if os.path.exists(sOutputPath):
        os.remove(sOutputPath)
    listFailures = _flistRecordFailedRequests(pageDashboard)
    _fnOpenTheHostWorkflow(pageDashboard, serverHub)

    # Expand the step, then use its Run button. The button lives in
    # the step's detail block, so a collapsed row has none.
    pageDashboard.click(f'.step-item:has-text("{S_HOST_STEP_NAME}")')
    pageDashboard.wait_for_selector(
        ".btn-run-step", state="visible", timeout=15000,
    )
    _fnClickRunUntilItIsNotRefused(pageDashboard)
    _fnWaitForFile(sOutputPath)
    with open(sOutputPath) as fileOutput:
        assert json.load(fileOutput)["listValues"] == [1, 2, 3]
    assert listFailures == [], (
        "running a host step produced failed requests:\n  "
        + "\n  ".join(listFailures)
    )
    assert pageDashboard.listPageErrors == []


def _fnClickRunUntilItIsNotRefused(page, fTimeoutSeconds=45.0):
    """Click Run, retrying while the project is still busy opening.

    Opening a workflow fires the repositories status, the git badges
    and a project-repo fetch, and on the host leg each git command is
    its own gated, journaled launch — measured at roughly five seconds
    of held drain in total. A Run arriving inside that window is
    REFUSED by the run-dispatch gate, correctly and with a toast that
    says so, which is exactly what a researcher meets if they click
    quickly. Retrying is what they do.

    The bound is what makes this a test rather than a shrug: a drain
    held indefinitely — a badges carrier that never settles — fails
    here with the refusal text rather than hanging or passing.
    """
    import time
    fDeadline = time.monotonic() + fTimeoutSeconds
    sLastRefusal = ""
    while time.monotonic() < fDeadline:
        page.click(".btn-run-step")
        page.wait_for_timeout(1500)
        listRefusals = [
            sText for sText in page.evaluate(
                "() => Array.from(document.querySelectorAll('.toast'))"
                ".map(el => el.innerText)",
            )
            if "Refused" in sText
        ]
        if not listRefusals:
            return
        sLastRefusal = listRefusals[-1]
        # Clear the toasts before retrying: they stack over the button
        # and intercept the next click, which would fail the retry for
        # a reason that has nothing to do with the refusal.
        page.evaluate(
            "() => document.querySelectorAll('.toast')"
            ".forEach(el => el.remove())",
        )
        page.wait_for_timeout(2000)
    raise AssertionError(
        "Run Step was refused for the whole window; the drain is held "
        f"by something that never finishes. Last refusal: {sLastRefusal}"
    )


def _fnWaitForFile(sPath, fTimeoutSeconds=60.0):
    """Block until the step's output exists, or fail naming the path."""
    import time
    fDeadline = time.monotonic() + fTimeoutSeconds
    while time.monotonic() < fDeadline:
        if os.path.exists(sPath):
            return
        time.sleep(0.2)
    raise AssertionError(
        f"the step never wrote {sPath}; the run did not reach the "
        "host connection, or it failed silently"
    )


@pytest.mark.falsification
def testTheTerminalNoticeSpeaksAboutTheRightThing(
    pageDashboard, serverHub,
):
    """A host project has no container to talk about.

    The withdrawn-terminal notice is container copy: it said releasing
    "this container" could not report it quiet, and then offered a
    `docker exec -it <container-name> bash` line naming a container
    that does not exist for this project. A researcher who followed it
    would be told no such container is running, about work that is
    running fine on their own machine.

    Kills: one notice for both modes.
    """
    _fnOpenTheHostWorkflow(pageDashboard, serverHub)
    sNotice = _fsTerminalNoticeText(pageDashboard)
    assert "docker exec" not in sNotice, sNotice
    assert "this container" not in sNotice, sNotice
    assert "your own machine" in sNotice, sNotice
    assert serverHub.sHome.split(os.sep)[-1] in sNotice or (
        S_HOST_PROJECT_READY in sNotice
    ), (
        "the notice does not name the directory the researcher should "
        f"open a shell in: {sNotice}"
    )


def _fsTerminalNoticeText(page):
    """Return the terminal pane's rendered text, whitespace collapsed."""
    page.wait_for_selector(".xterm-rows", timeout=20000)
    return " ".join(page.text_content(".xterm-rows").split())
