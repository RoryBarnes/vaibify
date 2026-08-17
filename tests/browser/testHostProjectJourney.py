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
    # The run LOG, which nothing asserted until a researcher's
    # successful host runs each produced none. The log is what a
    # researcher opens to see why a step behaved as it did, and its
    # absence is invisible from the dashboard, which shows the live
    # output it already has in memory.
    sLogsDirectory = os.path.join(
        serverHub.sHome, S_HOST_PROJECT_READY, ".vaibify", "logs",
    )
    _fnWaitForAnyLog(sLogsDirectory)
    # Wait for the run to actually END, not merely for its output to
    # exist. The file appears while the server is still FINALIZING
    # (state merge, acknowledged terminal flush), and a test that
    # returns inside that window leaves a bRunning=true state behind
    # for the NEXT test's recovery poll to find — which is exactly how
    # the stop test intermittently saw a phantom "running" light under
    # full-suite load. The terminal event that clears the light is
    # emitted only after the terminal state is durably flushed, so
    # this wait is also the proof the run's state settled.
    pageDashboard.wait_for_function(
        "() => !Array.from(document.querySelectorAll('.step-status'))"
        ".some(el => el.classList.contains('running'))",
        timeout=30000,
    )
    # The host run measures CPU at the ``os.wait4`` reap and the
    # recorded stats carry the reading. PRESENCE is the claim — a
    # fast step legitimately rounds to 0.0 — because before the reap
    # existed the key was omitted for every host run. Registered as a
    # falsification: restoring the CPU-less host branch kills this.
    #
    # The wait is load-bearing, not politeness: the running-lights
    # check above clears at stepPass, which PRECEDES the finalize
    # (log flush → state merge → acknowledged terminal flush), so a
    # test that read state.json immediately raced the merge — and the
    # module's claim-release teardown then cleared the owner map
    # while the merge was still in flight, so its commit-time
    # revalidation refused the write and the stats never landed.
    sStatePath = os.path.join(
        serverHub.sHome, S_HOST_PROJECT_READY, ".vaibify", "state.json",
    )
    listRunStats = _flistWaitForRunStats(sStatePath)
    assert listRunStats, "the settled run left no dictRunStats behind"
    assert any(
        "fCpuTime" in dictRunStats for dictRunStats in listRunStats
    ), f"no recorded host run carries fCpuTime: {listRunStats}"


def _flistWaitForRunStats(sStatePath, fTimeoutSeconds=20.0):
    """Poll state.json until the run's dictRunStats merge lands."""
    import time
    fDeadline = time.time() + fTimeoutSeconds
    listRunStats = []
    while time.time() < fDeadline:
        try:
            with open(sStatePath) as fileState:
                listRunStats = _flistCollectRunStats(json.load(fileState))
        except (OSError, ValueError):
            listRunStats = []
        if listRunStats:
            return listRunStats
        time.sleep(0.2)
    return listRunStats


def _flistCollectRunStats(jsonNode):
    """Return every dictRunStats value anywhere in the state document."""
    listFound = []
    if isinstance(jsonNode, dict):
        for sKey, jsonValue in jsonNode.items():
            if sKey == "dictRunStats" and isinstance(jsonValue, dict):
                listFound.append(jsonValue)
            else:
                listFound.extend(_flistCollectRunStats(jsonValue))
    elif isinstance(jsonNode, list):
        for jsonItem in jsonNode:
            listFound.extend(_flistCollectRunStats(jsonItem))
    return listFound


def _fnWaitForAnyLog(sLogsDirectory, fTimeoutSeconds=20.0):
    """Wait for the run to leave a log file behind."""
    import time
    fDeadline = time.time() + fTimeoutSeconds
    while time.time() < fDeadline:
        if os.path.isdir(sLogsDirectory) and os.listdir(sLogsDirectory):
            return
        time.sleep(0.5)
    raise AssertionError(
        f"the run wrote no log into {sLogsDirectory}; a researcher has "
        "no record of what the step printed"
    )


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
def testAHostTerminalOpensWithTheBannerAndEchoes(
    pageDashboard, serverHub,
):
    """A real shell, on this machine, in the researcher's tab.

    Until 2026-08-15 this test pinned the opposite: the pane showed a
    notice pointing at the researcher's own shell and never dialed.
    The ruling that replaced it — the terminal is how people will
    first try vaibify, so host mode must have it — comes with two
    honesty devices this test asserts: the per-session BANNER saying
    the shell runs on their own machine and that processes can
    outlive the tab, and (elsewhere) the quiescence-unproven journal
    record. The echo proves the PTY is real: a marker computed by
    bash on this machine comes back through the hub's WebSocket into
    xterm.

    Kills: the banner never reaching the host session's first bytes.
    """
    _fnOpenTheHostWorkflow(pageDashboard, serverHub)
    # The shell dials on the researcher's first gesture, never on
    # entry (the lazy dial, 2026-08-17) — and a HOST shell is the
    # highest-stakes case of the quarantine-bearing operation that
    # rule exists for. The click that used to precede typing is the
    # opt-in now, so it comes before the banner is expected.
    pageDashboard.wait_for_selector(".xterm", timeout=20000)
    pageDashboard.click(".xterm")
    sPane = _fsTerminalNoticeText(pageDashboard)
    fDeadline = 20.0
    import time as moduleTime
    fStarted = moduleTime.monotonic()
    while "YOUR OWN machine" not in sPane:
        assert moduleTime.monotonic() - fStarted < fDeadline, (
            f"the host banner never rendered: {sPane[:400]}"
        )
        pageDashboard.wait_for_timeout(250)
        sPane = _fsTerminalNoticeText(pageDashboard)
    assert "keep running" in sPane, sPane
    pageDashboard.keyboard.type("echo BROWSER-$((6*7))")
    pageDashboard.keyboard.press("Enter")
    fStarted = moduleTime.monotonic()
    while "BROWSER-42" not in sPane:
        assert moduleTime.monotonic() - fStarted < fDeadline, (
            f"the shell never echoed through the PTY: {sPane[:400]}"
        )
        pageDashboard.wait_for_timeout(250)
        sPane = _fsTerminalNoticeText(pageDashboard)
    assert pageDashboard.listPageErrors == []


def _fsTerminalNoticeText(page):
    """Return the terminal pane's rendered text, whitespace collapsed."""
    page.wait_for_selector(".xterm-rows", timeout=20000)
    return " ".join(page.text_content(".xterm-rows").split())


def _fsStepStatusClass(page):
    """Return the class list of the seeded step's run-status dot."""
    return page.get_attribute(
        f'.step-item:has-text("{S_HOST_STEP_NAME}") .step-status',
        "class",
    )


@pytest.mark.falsification
def testStoppingTasksDoesNotUnRunAFinishedStep(
    pageDashboard, serverHub,
):
    """A stop ends work in progress; it does not erase work that ended.

    Kills: clearing EVERY step light on a successful kill. Stopping
    took a finished step's pale-blue dot back to a hollow never-run
    circle, so the dashboard forgot -- and told the researcher it had
    forgotten -- that the step had succeeded. The running light beside
    it MUST go, which is the half that stops "clear nothing" from
    passing this test.

    The kill POST is answered here rather than served: what is under
    test is what the dashboard does with a success, and the route that
    produces one is driven against real processes in
    ``tests/testHostCancel.py``.

    The finished step's light is BACKED BY (stubbed) SERVER STATE, not
    injected: this page keeps reconciling itself against the server —
    that is the ground-truth contract — so a fixture-injected light
    with no server backing is erased by whichever reconciliation lands
    first, and this test failed three different ways on slow runners
    before the modelling was fixed. With the state poll answering "a
    completed run in which step 1 passed", every reconciliation
    RE-DERIVES the pale-blue dot instead of erasing it. Only the
    second step's "running" light is injected, faithfully: an
    in-flight light is set by live events and backed by nothing
    durable, which is exactly what a stop interrupts.
    """
    pageDashboard.route(
        "**/api/pipeline/*/state",
        lambda routeIntercepted: routeIntercepted.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "bRunning": False,
                "iExitCode": 0,
                "sLogPath": "/journey/.vaibify/logs/stop-demo.log",
                "iStepCount": 2,
                "dictStepResults": {
                    "1": {"sStatus": "passed", "iExitCode": 0},
                },
            }),
        ),
    )
    _fnOpenTheHostWorkflow(pageDashboard, serverHub)
    pageDashboard.route(
        "**/api/pipeline/*/kill",
        lambda routeIntercepted: routeIntercepted.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "bSuccess": True,
                "iProcessesKilled": 1,
                "bTaskCancelled": True,
                "listCancellationRefusals": [],
            }),
        ),
    )
    pageDashboard.wait_for_selector(
        f'.step-item:has-text("{S_HOST_STEP_NAME}") .step-status.pass',
        timeout=15000,
    )
    pageDashboard.evaluate(
        "() => { VaibifyApp.fnSetStepStatus(1, 'running');"
        " VaibifyApp.fnRenderStepList(); }",
    )
    assert "pass" in _fsStepStatusClass(pageDashboard)

    pageDashboard.evaluate("() => VaibifyPipelineRunner.fnKillPipeline()")
    pageDashboard.wait_for_selector("#modalConfirm", timeout=5000)
    pageDashboard.click("#btnConfirmOk")
    pageDashboard.wait_for_selector("text=Killed 1 process", timeout=5000)

    assert "pass" in _fsStepStatusClass(pageDashboard), (
        "stopping tasks erased a completed step's result; the "
        "dashboard now reports the step as never run"
    )
    assert pageDashboard.evaluate(
        "() => Array.from(document.querySelectorAll('.step-status'))"
        ".some(el => el.classList.contains('running'))",
    ) is False, "the stop left a running light on"
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testAKillResumesFilePollingItself(pageDashboard, serverHub):
    """Kills: resuming file polling only on the run's terminal event.

    A kill races the runner: when the task-cancellation side wins, the
    run emits NO terminal event (live: exit 130, 2026-08-14), and the
    terminal event was the only thing that restarted file polling. An
    edit made mid-run was then never announced — the reload detector
    sat blind until a tab reload. The kill's own success handler must
    resume polling, whichever side of the race won.

    Asserted on the poller's OWN state, not on counted network ticks:
    the counting version was timing-coupled and its mutation SURVIVED
    on one CI runner, twice, which is how a falsification stops being
    evidence.
    """
    _fnOpenTheHostWorkflow(pageDashboard, serverHub)
    pageDashboard.route(
        "**/api/pipeline/*/kill",
        lambda routeIntercepted: routeIntercepted.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "bSuccess": True,
                "iProcessesKilled": 1,
                "bTaskCancelled": True,
                "listCancellationRefusals": [],
            }),
        ),
    )
    pageDashboard.evaluate(
        "() => VaibifyPipelineRunner.fnHandlePipelineEvent("
        "{ sType: 'started', sCommand: 'runSelected' })",
    )
    assert pageDashboard.evaluate(
        "() => VaibifyPolling.fbFilePollingActive()",
    ) is False, "the run's started event did not stop file polling"

    pageDashboard.evaluate("() => VaibifyPipelineRunner.fnKillPipeline()")
    pageDashboard.wait_for_selector("#modalConfirm", timeout=5000)
    pageDashboard.click("#btnConfirmOk")
    pageDashboard.wait_for_function(
        "() => VaibifyPolling.fbFilePollingActive()", timeout=7000,
    )
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testAStaleRecoveryAnswerDoesNotResumePollingMidRun(
        pageDashboard, serverHub):
    """Kills: ignoring a recovery answer that a live run has outdated.

    Pipeline-state recovery is fired-and-forgotten at workflow
    activation. When its response lands AFTER a run has started, the
    server's "not running" answer predates the run — acting on it
    restarted file polling in the middle of a live run. That late
    landing is how the kill falsification above SURVIVED on a loaded
    CI runner (2026-08-14): the stale response satisfied its polling
    wait with the kill handler's resume mutated away.

    Deterministic by construction: the recovery call is awaited
    directly, so the "response arrives after the run started"
    ordering is forced, not raced.
    """
    _fnOpenTheHostWorkflow(pageDashboard, serverHub)
    pageDashboard.evaluate(
        "() => VaibifyPipelineRunner.fnHandlePipelineEvent("
        "{ sType: 'started', sCommand: 'runSelected' })",
    )
    assert pageDashboard.evaluate(
        "() => VaibifyPolling.fbFilePollingActive()",
    ) is False, "the run's started event did not stop file polling"

    pageDashboard.evaluate(
        "() => VaibifyPipelineRunner.fnRecoverPipelineState("
        "VaibifyApp.fsGetContainerId())",
    )
    assert pageDashboard.evaluate(
        "() => VaibifyPolling.fbFilePollingActive()",
    ) is False, (
        "a recovery answer fetched before the run started resumed "
        "file polling mid-run"
    )
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testADegradedRunNeverToastsACleanCompletion(
        pageDashboard, serverHub):
    """Kills: the §4.6 terminal report in the researcher's own tab.

    A run whose pull records did not all commit must say "with
    degraded provenance", never plain "completed" — the clean toast
    claims documentation the disk does not have.
    """
    _fnOpenTheHostWorkflow(pageDashboard, serverHub)
    pageDashboard.evaluate(
        "() => VaibifyPipelineRunner.fnHandlePipelineEvent("
        "{ sType: 'completed', sCommand: 'runSelected', iExitCode: 0,"
        " bProvenanceDegraded: true, bRunMetadataPersisted: true,"
        " sLogPath: '/journey/.vaibify/logs/degraded.log' })",
    )
    pageDashboard.wait_for_selector(
        "text=degraded provenance", timeout=5000,
    )
    assert pageDashboard.evaluate(
        "() => Array.from("
        "document.querySelectorAll('.toast.success'))"
        ".some(el => el.textContent.includes('Step completed'))",
    ) is False, (
        "a degraded run also painted the clean success toast"
    )
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testAStepDownstreamOfDegradedProvenanceWearsTheGlyph(
    pageDashboard, serverHub,
):
    """Kills: the live result event's taint never reaching the store —
    the run degrades, dependents execute, and the researcher's tab
    shows two ordinary lights with no visible connection to the
    undocumented data the second step may have consumed (ruling R6).
    """
    _fnOpenTheHostWorkflow(pageDashboard, serverHub)
    pageDashboard.evaluate(
        "() => { VaibifyPipelineRunner.fnHandlePipelineEvent("
        "{ sType: 'stepPass', iStepNumber: 1, iExitCode: 0 });"
        "VaibifyPipelineRunner.fnHandlePipelineEvent("
        "{ sType: 'stepPass', iStepNumber: 2, iExitCode: 0,"
        " bDownstreamOfDegradedProvenance: true }); }",
    )
    pageDashboard.wait_for_selector(
        '.step-item:has-text("Second Stage") .step-taint-glyph',
        timeout=5000,
    )
    assert pageDashboard.locator(
        f'.step-item:has-text("{S_HOST_STEP_NAME}")',
    ).count() == 1
    iFirstStepGlyphs = pageDashboard.locator(
        f'.step-item:has-text("{S_HOST_STEP_NAME}") .step-taint-glyph',
    ).count()
    assert iFirstStepGlyphs == 0, (
        "the degrading step marked itself; the glyph's tooltip is a "
        "false statement about that step"
    )
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testACleanRunsResultsWearNoTaintGlyph(pageDashboard, serverHub):
    """Kills: the glyph rendering unconditionally — a mark that
    appears on every step says nothing, and a researcher learns to
    ignore exactly the warning ruling R6 exists to make visible."""
    _fnOpenTheHostWorkflow(pageDashboard, serverHub)
    pageDashboard.evaluate(
        "() => { VaibifyPipelineRunner.fnHandlePipelineEvent("
        "{ sType: 'stepPass', iStepNumber: 1, iExitCode: 0 });"
        "VaibifyPipelineRunner.fnHandlePipelineEvent("
        "{ sType: 'stepPass', iStepNumber: 2, iExitCode: 0 }); }",
    )
    pageDashboard.wait_for_selector(
        f'.step-item:has-text("{S_HOST_STEP_NAME}") .step-status.pass',
        timeout=5000,
    )
    assert pageDashboard.locator(".step-taint-glyph").count() == 0, (
        "a clean run's results wear the downstream-of-degraded mark"
    )
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testATaintMarkSurvivesAReconnect(pageDashboard, serverHub):
    """Kills: the recovery lanes dropping the persisted flag — the
    mark then exists only for the tab that watched the run live, and
    reopening the dashboard silently launders the tainted results."""
    pageDashboard.route(
        "**/api/pipeline/*/state",
        lambda routeIntercepted: routeIntercepted.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "bRunning": False,
                "iExitCode": 0,
                "sLogPath": "/journey/.vaibify/logs/tainted.log",
                "iStepCount": 2,
                "dictStepResults": {
                    "1": {"sStatus": "passed", "iExitCode": 0},
                    "2": {"sStatus": "passed", "iExitCode": 0,
                          "bDownstreamOfDegradedProvenance": True},
                },
            }),
        ),
    )
    _fnOpenTheHostWorkflow(pageDashboard, serverHub)
    pageDashboard.wait_for_selector(
        '.step-item:has-text("Second Stage") .step-taint-glyph',
        timeout=10000,
    )
    iFirstStepGlyphs = pageDashboard.locator(
        f'.step-item:has-text("{S_HOST_STEP_NAME}") .step-taint-glyph',
    ).count()
    assert iFirstStepGlyphs == 0
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testAStoppedRunsLightsSurviveAReconnect(pageDashboard, serverHub):
    """Kills: restoring lights only for runs that exited cleanly.

    A Stop ends the in-flight step by signal, so the run's exit code
    is negative (-15 live). The reconnect recovery guarded its light
    restoration with ``iExitCode >= 0`` — meant to skip the -1
    never-completed sentinel — so reopening the dashboard after a
    stop showed every step as never-run while the durable state knew
    the first step had passed. Found live in Rory's 2026-08-14
    walkthrough: a hub restart after a stopped run turned both lights
    into open circles.
    """
    pageDashboard.route(
        "**/api/pipeline/*/state",
        lambda routeIntercepted: routeIntercepted.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "bRunning": False,
                "iExitCode": -15,
                "sLogPath": "/journey/.vaibify/logs/stopped.log",
                "iStepCount": 2,
                "dictStepResults": {
                    "1": {"sStatus": "passed", "iExitCode": 0},
                    "2": {"sStatus": "stopped", "iExitCode": 130},
                },
            }),
        ),
    )
    _fnOpenTheHostWorkflow(pageDashboard, serverHub)
    pageDashboard.wait_for_selector(
        f'.step-item:has-text("{S_HOST_STEP_NAME}") .step-status.pass',
        timeout=15000,
    )
    assert "stopped" in pageDashboard.get_attribute(
        '.step-item:has-text("Second Stage") .step-status', "class",
    ), "the stopped step's purple light was not restored"
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testAKillPaintsTheStoppedLight(pageDashboard, serverHub):
    """Kills: leaving the interrupted step as a hollow never-ran circle.

    When the kill's task-cancellation side wins, the run emits no
    result for the step it interrupted, so a step that ran for
    minutes and was deliberately stopped displayed identically to one
    that never started (live, twice, 2026-08-14). The kill response
    now names the interrupted step and the dashboard paints it
    PURPLE 'stopped' — never failure-red (the researcher's stop is
    not the step failing), never hollow (it did run).
    """
    _fnOpenTheHostWorkflow(pageDashboard, serverHub)
    pageDashboard.route(
        "**/api/pipeline/*/kill",
        lambda routeIntercepted: routeIntercepted.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "bSuccess": True,
                "iProcessesKilled": 1,
                "bTaskCancelled": True,
                "iStoppedStepNumber": 2,
                "listCancellationRefusals": [],
            }),
        ),
    )
    pageDashboard.evaluate(
        "() => {"
        " VaibifyPipelineRunner.fnHandlePipelineEvent("
        "   { sType: 'started', sCommand: 'runSelected' });"
        " VaibifyPipelineRunner.fnHandlePipelineEvent("
        "   { sType: 'stepStarted', iStepNumber: 2,"
        "     fWallClockBudgetSeconds: 0.0 }); }",
    )
    pageDashboard.wait_for_selector(".step-status.running", timeout=5000)

    pageDashboard.evaluate("() => VaibifyPipelineRunner.fnKillPipeline()")
    pageDashboard.wait_for_selector("#modalConfirm", timeout=5000)
    pageDashboard.click("#btnConfirmOk")
    pageDashboard.wait_for_selector(
        '.step-item:has-text("Second Stage") .step-status.stopped',
        timeout=7000,
    )
    sClasses = pageDashboard.get_attribute(
        '.step-item:has-text("Second Stage") .step-status', "class",
    )
    assert "fail" not in sClasses, (
        "the researcher's stop was painted as a failure"
    )
    assert pageDashboard.evaluate(
        "() => !Array.from(document.querySelectorAll('.step-status'))"
        ".some(el => el.classList.contains('running'))",
    ), "the running light survived the stop"
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testARunClickAcknowledgesTheAppliedRevision(
    pageDashboard, serverHub,
):
    """Kills: sending run frames with no freshness acknowledgment.

    The dispatch freshness gate proves three-way agreement — what
    this dashboard APPLIED, the server's record, and the file's
    bytes. A frame without the acknowledgment fields drops the
    client to the legacy two-way check, so the browser must attach
    what it has applied; and a ``workflowSuperseded`` refusal must
    say the project changed, never "already running" — the generic
    text is actively false and sends the researcher to the Kill
    button, which cannot help.
    """
    _fnOpenTheHostWorkflow(pageDashboard, serverHub)
    dictSent = pageDashboard.evaluate(
        "() => {"
        " const fnRealSend = VaibifyWebSocket.fnSend;"
        " let dictCaptured = null;"
        " VaibifyWebSocket.fnSend = function (dictAction) {"
        "   dictCaptured = dictAction; };"
        " try {"
        "   VaibifyPipelineRunner.fnSendPipelineAction("
        "     { sAction: 'runSelected', listStepIndices: [0] });"
        " } finally { VaibifyWebSocket.fnSend = fnRealSend; }"
        " return dictCaptured; }",
    )
    assert dictSent is not None, "the action never reached the socket"
    assert dictSent.get("sAcknowledgedSourceFingerprint"), (
        "the run frame carries no acknowledged fingerprint; the "
        "dispatch gate cannot prove what this dashboard displayed"
    )
    assert dictSent.get("sAcknowledgedWorkflowPath"), (
        "the run frame names no workflow; byte-identical projects in "
        "one repo would be indistinguishable"
    )
    pageDashboard.evaluate(
        "() => VaibifyPipelineRunner.fnHandlePipelineEvent({"
        " sType: 'runRefused', sReason: 'workflowSuperseded',"
        " sAction: 'runSelected', listStepIndices: [0],"
        " sMessage: \"Refused 'runSelected': project.json changed on"
        " disk after this dashboard loaded it; the dashboard has been"
        " refreshed. Review the refreshed project and run again —"
        " nothing was started.\" })",
    )
    sToasts = pageDashboard.evaluate(
        "() => Array.from(document.querySelectorAll('.toast'))"
        ".map(el => el.textContent).join(' | ')",
    )
    assert "changed on disk" in sToasts, sToasts
    assert "already running" not in sToasts, (
        "a superseded-workflow refusal was reported as a busy "
        "container; the researcher is sent to the Kill button"
    )
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testADegradedCompletionIsNotReportedClean(pageDashboard, serverHub):
    """Kills: swallowing ``bRunMetadataPersisted: false`` on completion.

    Completion is state-only, and the backend reports honestly when
    recording the run's results FAILED — the run itself finished, but
    a reload would show stale statistics. A dashboard that shows only
    the success toast suppresses a degraded state, which the
    ground-truth rule forbids. The clean completion is asserted
    beside it, because a warning that fires on every completion would
    train the researcher to ignore it.
    """
    _fnOpenTheHostWorkflow(pageDashboard, serverHub)
    pageDashboard.evaluate(
        "() => VaibifyPipelineRunner.fnHandlePipelineEvent({"
        " sType: 'completed', iExitCode: 0, sLogPath: '',"
        " bRunMetadataPersisted: false,"
        " sRunMetadataDetail: 'no project repo' })",
    )
    sToasts = pageDashboard.evaluate(
        "() => Array.from(document.querySelectorAll('.toast'))"
        ".map(el => el.textContent).join(' | ')",
    )
    assert "recording its results failed" in sToasts, (
        "the backend said the run's results were not recorded and the "
        "dashboard reported a clean completion"
    )
    pageDashboard.evaluate(
        "() => document.querySelectorAll('.toast')"
        ".forEach(el => el.remove())",
    )
    pageDashboard.evaluate(
        "() => VaibifyPipelineRunner.fnHandlePipelineEvent({"
        " sType: 'completed', iExitCode: 0, sLogPath: '',"
        " bRunMetadataPersisted: true })",
    )
    sToastsClean = pageDashboard.evaluate(
        "() => Array.from(document.querySelectorAll('.toast'))"
        ".map(el => el.textContent).join(' | ')",
    )
    assert "recording its results failed" not in sToastsClean, (
        "a clean completion warned anyway; a warning that always fires "
        "is one the researcher learns to ignore"
    )
    assert pageDashboard.listPageErrors == []
