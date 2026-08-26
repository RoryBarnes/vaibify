"""A shut credential gate must SAY so when the researcher clicks.

The live report that produced this file: the button did nothing at all.
The explanation code existed — ``fnHandleToolbarClick`` has always
toasted the refusal — but ``_fnRenderToolbarButton`` set
``elButton.disabled`` for the same condition, and a disabled button
swallows its own click, so the handler was unreachable. The reason was
reachable only as a hover title, which is not where anyone looks after
clicking.

These tests drive the REAL frontend against the REAL capabilities route
with the gate genuinely shut (no evidence record patched in), because
the defect was precisely that a code path existed and could not run.
Asserting the toast TEXT is what makes them fail if the click goes
silent again.
"""

import pytest

from .fakeDockerAdapter import (
    S_CONTAINER_ID,
    S_CONTAINER_NAME,
    S_WORKFLOW_PATH,
)
from .testBrowserJourneys import _fnReleaseBrowserLaneOwnership


pytestmark = pytest.mark.browser


@pytest.fixture(autouse=True)
def _fnDropTheClaimBetweenTests(serverHub):
    """Release the container lease each test takes.

    Every test here claims the same container, and a lease left behind
    makes the NEXT claim fail — which showed up as the click producing
    no toast, a symptom indistinguishable from the bug under test. Two
    of these tests passed alone and failed in sequence before this
    existed.
    """
    yield
    _fnReleaseBrowserLaneOwnership(serverHub.app.state)


@pytest.fixture(autouse=True)
def _fnShutTheCredentialGate(monkeypatch):
    """Leave the runner backend disabled, the way a fresh machine is.

    Patched explicitly rather than relying on the absence of a record,
    so the test states its own premise and cannot be quietly enabled by
    a developer's real ~/.vaibify evidence file.
    """
    from vaibify.gui import agentCouncilCredentialGate
    monkeypatch.setattr(
        agentCouncilCredentialGate, "fdictEvaluateCredentialEnablement",
        lambda sProvider, sImageIdentity=None: {
            "bEnabled": False,
            "sReason": ("the runner backend is disabled: no "
                        "credential-verification evidence record exists "
                        "on this machine."),
            "dictRecord": None,
        })


def _fdictActivateCouncilToolbar(page, serverHub):
    """Claim, open the workflow, and report the toolbar button's state."""
    page.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    page.wait_for_selector(".container-tile", timeout=10000)
    return page.evaluate(
        """async ([sContainerId, sName, sWorkflowPath]) => {
            const dictClaim = await VaibifyApi.fdictPost(
                '/api/registry/' + encodeURIComponent(sName) + '/claim', {});
            VaibifyApp.fnRecordClaimedLease(sName, dictClaim.sLeaseId);
            await VaibifyApp.fnEnterNoWorkflow(sContainerId);
            await VaibifyApi.fdictPostRaw(
                '/api/connect/' + sContainerId +
                '?sWorkflowPath=' + encodeURIComponent(sWorkflowPath));
            VaibifyAgentCouncil.fnActivate(sContainerId);
            await VaibifyAgentCouncil.fnRefreshCapabilities();
            const elButton = document.getElementById('btnAgentCouncil');
            return {
                bDisabled: elButton.disabled,
                bBlockedClass: elButton.classList.contains(
                    'council-blocked'),
                sTitle: elButton.title,
            };
        }""",
        [S_CONTAINER_ID, S_CONTAINER_NAME, S_WORKFLOW_PATH],
    )


def testAShutGateLeavesTheButtonClickableNotDead(pageDashboard, serverHub):
    """The regression itself: disabled would swallow the click again."""
    dictState = _fdictActivateCouncilToolbar(pageDashboard, serverHub)

    assert dictState["bDisabled"] is False, (
        "the council button is disabled while the credential gate is "
        "shut, so clicking it does nothing and the researcher is told "
        "nothing — this is the reported defect"
    )
    assert dictState["bBlockedClass"] is True, (
        "a clickable-but-unusable button must still LOOK unusable"
    )
    assert "credential" in dictState["sTitle"].lower()


def testClickingTheBlockedButtonNamesTheFixOnScreen(
    pageDashboard, serverHub,
):
    """Not merely 'a toast appeared' — the remediation must be IN it.

    The whole point of the report was that the researcher could not
    tell what to do next, so asserting the presence of a toast would
    re-pass while saying nothing useful.
    """
    _fdictActivateCouncilToolbar(pageDashboard, serverHub)
    pageDashboard.click("#btnAgentCouncil")
    pageDashboard.wait_for_selector(".toast", timeout=8000)
    sToast = pageDashboard.inner_text(".toast")

    assert "credentialEvidence.json" in sToast, (
        f"the toast never names where to record the result: {sToast!r}")
    assert "sha256" in sToast, (
        f"the toast never says a tag is refused: {sToast!r}")
    assert "paid account" in sToast, (
        f"the toast never names the check that opens the gate: {sToast!r}")


def testNoWorkflowOpenAlsoExplainsItselfRatherThanDyingSilently(
    pageDashboard, serverHub,
):
    """The SECOND silent dead click, from the same button.

    ``fnActivate`` runs from ``_fnActivateWorkflow`` — opening a
    WORKFLOW, not merely a container — so a researcher sitting in a
    project with no workflow open has a live-looking button and no
    container id behind it. The first fix left this case disabled, on
    the reasoning that there was "nothing to say yet"; there is, and it
    is the most actionable thing on the page.

    Enters the container WITHOUT opening a workflow — the state the
    live report was made from, and the one where the button first
    becomes visible. (On the landing page it is hidden, so no click is
    possible there; verified before writing this.)
    """
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_selector(".container-tile", timeout=10000)
    pageDashboard.evaluate(
        """async ([sContainerId, sName]) => {
            const dictClaim = await VaibifyApi.fdictPost(
                '/api/registry/' + encodeURIComponent(sName) + '/claim', {});
            VaibifyApp.fnRecordClaimedLease(sName, dictClaim.sLeaseId);
            await VaibifyApp.fnEnterNoWorkflow(sContainerId);
        }""",
        [S_CONTAINER_ID, S_CONTAINER_NAME],
    )
    pageDashboard.wait_for_selector("#btnAgentCouncil:visible", timeout=8000)

    assert pageDashboard.locator("#btnAgentCouncil").is_disabled() is False, (
        "in a container with no workflow open the button is visible but "
        "disabled, so the click is swallowed and the researcher is told "
        "nothing — this is the second report of the same silent click"
    )

    pageDashboard.click("#btnAgentCouncil")
    pageDashboard.wait_for_selector(".toast", timeout=8000)
    sToast = pageDashboard.inner_text(".toast")

    # NOT merely "a toast appeared". An earlier version of this asserted
    # only that the click SPEAKS, and that passed while the council
    # panel was never activated in this state at all — the toast said
    # "Open this project to convene a council" to a researcher who had
    # already opened it. Asserting the absence of that specific wrong
    # answer is what makes this test about the Blank Project.
    assert "open this project" not in sToast.lower(), (
        "the council panel was never activated in the Blank Project "
        f"state, so the click answered with the state we are in: {sToast!r}")
    assert sToast.strip().strip("×").strip(), (
        f"the click produced an empty toast: {sToast!r}")


def testABlankProjectCanActuallyConveneNotMerelyExplainItself(
    pageDashboard, serverHub, monkeypatch,
):
    """The positive form, and the one that proves the feature.

    Every other test in this file asserts a REFUSAL is explained. This
    one asserts there is no refusal at all: in the Blank Project state,
    with the credential gate open and the lane's single tracked
    directory, the button is live and unblocked.

    It exists because the negative tests could not see the real defect.
    The backend admitted Blank Projects while the frontend never
    activated the council panel without a workflow, and a test that
    only asked "does the click say something" was satisfied by the
    panel saying "open this project" to someone who already had.
    """
    from vaibify.gui import agentCouncilCredentialGate
    monkeypatch.setattr(
        agentCouncilCredentialGate, "fdictEvaluateCredentialEnablement",
        lambda sProvider, sImageIdentity=None: {
            "bEnabled": True, "sReason": "", "dictRecord": {}})

    _fdictActivateCouncilToolbar(pageDashboard, serverHub)
    sState = pageDashboard.evaluate(
        """() => {
            const el = document.getElementById('btnAgentCouncil');
            return JSON.stringify({
                bDisabled: el.disabled,
                bBlocked: el.classList.contains('council-blocked'),
                sTitle: el.title});
        }"""
    )

    assert '"bDisabled":false' in sState, sState
    assert '"bBlocked":false' in sState, (
        f"a Blank Project that can convene still looks blocked: {sState}")
    assert "convene" in sState.lower()


def testCancellingAComposedCouncilAsksBeforeDiscardingIt(
    pageDashboard, serverHub, monkeypatch,
):
    """A written question must survive a stray click on Cancel.

    Choosing participants and writing the question is the researcher's
    real investment in convening, and Cancel used to discard it with no
    prompt. Drives the REAL form: opens the modal with the gate
    permitting, types a question, clicks Cancel, and asserts the modal
    is still there behind a confirmation.
    """
    from vaibify.gui import agentCouncilCredentialGate
    monkeypatch.setattr(
        agentCouncilCredentialGate, "fdictEvaluateCredentialEnablement",
        lambda sProvider, sImageIdentity=None: {
            "bEnabled": True, "sReason": "", "dictRecord": {}})

    _fdictActivateCouncilToolbar(pageDashboard, serverHub)
    pageDashboard.click("#btnAgentCouncil")
    # The chooser stands between the toolbar and the convene form.
    pageDashboard.wait_for_selector("#btnCouncilPlanChange", timeout=8000)
    pageDashboard.click("#btnCouncilPlanChange")
    pageDashboard.wait_for_selector("#councilQuestion", timeout=8000)
    pageDashboard.fill("#councilQuestion", "Which sampler converges fastest?")

    pageDashboard.click("#btnCouncilCancel")

    pageDashboard.wait_for_selector("#modalConfirm", timeout=8000)
    assert pageDashboard.locator("#councilQuestion").count() == 1, (
        "the convene form was torn down before the researcher answered "
        "the confirmation"
    )
    assert "lost" in pageDashboard.inner_text("#modalConfirm").lower()


def testCancellingAnUntouchedCouncilDoesNotNagTheResearcher(
    pageDashboard, serverHub, monkeypatch,
):
    """The other half: no prompt when there is nothing to lose.

    A confirmation that fires on an empty form is one researchers learn
    to dismiss unread, which would cost the protection above its value.
    """
    from vaibify.gui import agentCouncilCredentialGate
    monkeypatch.setattr(
        agentCouncilCredentialGate, "fdictEvaluateCredentialEnablement",
        lambda sProvider, sImageIdentity=None: {
            "bEnabled": True, "sReason": "", "dictRecord": {}})

    _fdictActivateCouncilToolbar(pageDashboard, serverHub)
    pageDashboard.click("#btnAgentCouncil")
    # The chooser stands between the toolbar and the convene form.
    pageDashboard.wait_for_selector("#btnCouncilPlanChange", timeout=8000)
    pageDashboard.click("#btnCouncilPlanChange")
    pageDashboard.wait_for_selector("#councilQuestion", timeout=8000)

    pageDashboard.click("#btnCouncilCancel")

    pageDashboard.wait_for_timeout(500)
    assert pageDashboard.locator("#modalConfirm").count() == 0, (
        "an untouched convene form asked for confirmation it did not need"
    )
    assert pageDashboard.locator("#agentCouncilModal").is_visible() is False


def testTheExplanationDoesNotSelfDestructBeforeItCanBeRead(
    pageDashboard, serverHub,
):
    """An info toast disappears after 4s; this one must not.

    A refusal carrying a filesystem path and an image-id rule is not
    readable, let alone actionable, in four seconds.
    """
    _fdictActivateCouncilToolbar(pageDashboard, serverHub)
    pageDashboard.click("#btnAgentCouncil")
    pageDashboard.wait_for_selector(".toast", timeout=8000)

    pageDashboard.wait_for_timeout(5000)

    assert pageDashboard.locator(".toast").count() == 1, (
        "the refusal vanished before it could be read or acted on"
    )


def testAnOversizedFileIsOfferedForExclusionNotJustRefused(
    pageDashboard, serverHub, monkeypatch,
):
    """The convene form must let the researcher ACT on the size bound.

    The live report: a repository with one 85 MB data file was refused
    at "Convene council", after the participants were chosen and the
    question written. The pre-flight now catches it earlier, but
    catching it earlier is only half — a researcher told "this file is
    too big" and given no way past it is still stuck.

    Drives the REAL form against the REAL pre-flight with the lane's
    repository reporting one oversized member, then asserts the file is
    named on screen, pre-ticked for exclusion, and that convening sends
    it. A Python-side assertion cannot see any of that: the checkbox,
    its default, and the request body are all frontend behaviour.
    """
    from vaibify.gui import agentCouncilCredentialGate
    monkeypatch.setattr(
        agentCouncilCredentialGate, "fdictEvaluateCredentialEnablement",
        lambda sProvider, sImageIdentity=None: {
            "bEnabled": True, "sReason": "", "dictRecord": {}})
    serverHub.adapterDocker.dictRepositoryWeight = {
        "iFileCount": 120,
        "iTotalBytes": 200 * 1024 * 1024,
        "bTruncated": False,
        "bLargestFilesTruncated": False,
        "listLargestFiles": [
            {"sPath": "data/marshnb/4.inv", "iSizeBytes": 85912419},
            {"sPath": "README.md", "iSizeBytes": 2048},
        ],
    }

    dictState = _fdictActivateCouncilToolbar(pageDashboard, serverHub)
    assert dictState["bDisabled"] is False, (
        "an oversized FILE blocked the button, which hides the only "
        "place the exclusion can be offered")

    pageDashboard.click("#btnAgentCouncil")
    pageDashboard.wait_for_selector("#btnCouncilPlanChange", timeout=8000)
    pageDashboard.click("#btnCouncilPlanChange")
    pageDashboard.wait_for_selector(
        ".council-snapshot-scope", timeout=8000)

    sScope = pageDashboard.inner_text(".council-snapshot-scope")
    assert "4.inv" in sScope, (
        f"the offending file is not named on screen: {sScope!r}")
    assert "81 MB" in sScope, (
        f"the size the researcher must judge is missing: {sScope!r}")
    assert pageDashboard.locator(
        "[data-oversized-path='data/marshnb/4.inv']").is_checked(), (
        "the exclusion is not ticked by default, so a researcher who "
        "writes a question and convenes is refused anyway")
    assert pageDashboard.locator(
        "[data-oversized-path='README.md']").count() == 0, (
        "a file well inside the bound was offered for exclusion; the "
        "list would become a general curation switch")

    listSentBodies = pageDashboard.evaluate(
        """() => {
            window._listCouncilStartBodies = [];
            const fnRealPost = VaibifyApi.fdictPost;
            VaibifyApi.fdictPost = function (sPath, dictBody) {
                if (sPath.indexOf('/start') !== -1) {
                    window._listCouncilStartBodies.push(dictBody);
                    return Promise.reject(new Error('intercepted'));
                }
                return fnRealPost(sPath, dictBody);
            };
            return window._listCouncilStartBodies;
        }"""
    )
    assert listSentBodies == []
    pageDashboard.fill("#councilQuestion", "Which sampler converges fastest?")
    pageDashboard.click("#btnCouncilConvene")
    pageDashboard.wait_for_function(
        "() => window._listCouncilStartBodies.length > 0", timeout=8000)
    listExcluded = pageDashboard.evaluate(
        "() => window._listCouncilStartBodies[0].listExcludedPaths")

    assert listExcluded == ["data/marshnb/4.inv"], (
        "the convene request did not carry the researcher's exclusion, "
        f"so the capture would refuse exactly as before: {listExcluded!r}")


def _fnOpenCouncilWorkspace(page, serverHub):
    """Open the council panel from a KNOWN state, not an inherited one.

    The page is shared across this module's tests, so the panel arrives
    carrying whatever the previous test left: display:none, a bound
    handle, and a persisted height in localStorage. Two tests passed
    alone and failed in file order on exactly that. Clearing the stored
    height also keeps the drag assertion meaningful — a panel restored
    near its 85vh ceiling cannot grow by the amount the test drags.
    """
    _fdictActivateCouncilToolbar(page, serverHub)
    page.evaluate(
        "() => window.localStorage.removeItem("
        "'vaibifyCouncilWorkspaceHeight')")
    # Opened inside the poll, not once before it. Entering a workflow
    # kicks off async work that re-hides the workspace, so a single
    # call races it — and the race resolves differently depending on
    # what ran before, which is why these two tests passed alone and
    # failed in file order.
    #
    # The condition is a real, grabbable BOX rather than Playwright's
    # visibility heuristic, which classes this 6px strip as hidden and
    # would fail a handle that works: width and height are exactly what
    # a pointer drag needs.
    page.wait_for_function(
        """() => {
            VaibifyAgentCouncil.fnShowWorkspace();
            const el = document.getElementById('councilResizeHandle');
            if (!el) return false;
            const r = el.getBoundingClientRect();
            return r.width > 0 && r.height > 0;
        }""", timeout=10000)


def testTheCouncilPanelResizesByDragAndReservesItsSpace(
    pageDashboard, serverHub, monkeypatch,
):
    """Drag the top edge; the panel resizes and stops covering the page.

    Two reported symptoms, one cause: the panel is a fixed-position
    overlay with no height, so it sized to its content (re-fitting on
    every tab switch) and covered whatever sat beneath it — the
    terminal included.

    Both halves are asserted because fixing one alone is worse than
    useless: a panel that resizes but still overlays just covers MORE
    of the terminal. Drives a real pointer drag rather than calling the
    handler, because "can the researcher grab that 6px strip" is the
    part a unit test cannot answer.
    """
    from vaibify.gui import agentCouncilCredentialGate
    monkeypatch.setattr(
        agentCouncilCredentialGate, "fdictEvaluateCredentialEnablement",
        lambda sProvider, sImageIdentity=None: {
            "bEnabled": True, "sReason": "", "dictRecord": {}})

    _fnOpenCouncilWorkspace(pageDashboard, serverHub)

    dictBefore = pageDashboard.evaluate(
        """() => {
            const el = document.getElementById('agentCouncilWorkspace');
            return {
                iHeight: el.offsetHeight,
                bReserved: document.body.classList.contains(
                    'council-workspace-open'),
                sReservedPx: getComputedStyle(
                    document.documentElement).getPropertyValue(
                        '--council-workspace-height').trim(),
            };
        }"""
    )
    assert dictBefore["bReserved"] is True, (
        "the layout reserves no space for the panel, so the panel is "
        "covering whatever is under it — the terminal complaint")
    assert dictBefore["sReservedPx"].endswith("px"), dictBefore
    assert abs(int(dictBefore["sReservedPx"][:-2])
               - dictBefore["iHeight"]) <= 2, (
        f"the reserved space and the panel disagree: {dictBefore}")

    dictHandle = pageDashboard.locator("#councilResizeHandle").bounding_box()
    pageDashboard.mouse.move(
        dictHandle["x"] + dictHandle["width"] / 2,
        dictHandle["y"] + dictHandle["height"] / 2)
    pageDashboard.mouse.down()
    pageDashboard.mouse.move(
        dictHandle["x"] + dictHandle["width"] / 2,
        dictHandle["y"] - 120, steps=8)
    pageDashboard.mouse.up()

    dictAfter = pageDashboard.evaluate(
        """() => {
            const el = document.getElementById('agentCouncilWorkspace');
            return {
                iHeight: el.offsetHeight,
                sReservedPx: getComputedStyle(
                    document.documentElement).getPropertyValue(
                        '--council-workspace-height').trim(),
                sStored: window.localStorage.getItem(
                    'vaibifyCouncilWorkspaceHeight'),
            };
        }"""
    )
    assert dictAfter["iHeight"] > dictBefore["iHeight"] + 50, (
        "dragging the top edge upward did not grow the panel: "
        f"{dictBefore['iHeight']} -> {dictAfter['iHeight']}")
    assert abs(int(dictAfter["sReservedPx"][:-2])
               - dictAfter["iHeight"]) <= 2, (
        "the reserved space did not follow the resize, so a taller "
        f"panel covers more of the page: {dictAfter}")
    assert dictAfter["sStored"], (
        "the chosen height was not persisted, so it is lost on reload")


def testClosingTheCouncilPanelGivesTheSpaceBack(
    pageDashboard, serverHub, monkeypatch,
):
    """The other half of reserving space: releasing it.

    A reservation that outlives the panel strands a band of dead
    padding at the bottom of the layout — the same occlusion bug
    wearing the opposite sign.
    """
    from vaibify.gui import agentCouncilCredentialGate
    monkeypatch.setattr(
        agentCouncilCredentialGate, "fdictEvaluateCredentialEnablement",
        lambda sProvider, sImageIdentity=None: {
            "bEnabled": True, "sReason": "", "dictRecord": {}})

    _fnOpenCouncilWorkspace(pageDashboard, serverHub)
    assert pageDashboard.evaluate(
        "() => document.body.classList.contains('council-workspace-open')")

    pageDashboard.click("#btnAgentCouncilWorkspaceClose")

    assert pageDashboard.evaluate(
        "() => !document.body.classList.contains("
        "'council-workspace-open')"), (
        "the layout still reserves space for a closed panel")


def testConveningShowsItIsWorkingAndCannotBeDoubleSubmitted(
    pageDashboard, serverHub, monkeypatch,
):
    """The click must visibly land during the 5-10s convene.

    A convene resolves the image, checks the gate and the login,
    captures the repository snapshot, builds one runner per
    participant, copies the snapshot into each and spawns the drive
    task — all inside one HTTP request that says nothing until it
    answers. The form used to sit unchanged throughout, which reads as
    a click that missed (live report, 2026-08-24).

    Holds the backend request open so the busy state can be observed
    while it is genuinely in flight, rather than asserting against a
    state that has already been torn down. Also asserts the button
    cannot be pressed twice: a second convene during the wait is a
    second campaign.
    """
    import threading

    from vaibify.gui import agentCouncilCredentialGate
    monkeypatch.setattr(
        agentCouncilCredentialGate, "fdictEvaluateCredentialEnablement",
        lambda sProvider, sImageIdentity=None: {
            "bEnabled": True, "sReason": "", "dictRecord": {}})

    eventRelease = threading.Event()
    from vaibify.gui.routes import councilRoutes
    fnRealProbe = councilRoutes.fnRefuseStartWithoutAProjectLogin

    def _fnSlowLoginProbe(*tArguments, **dictKeywords):
        """Hold the START request open, and only that request.

        The login probe runs on the start path alone, inside
        asyncio.to_thread — so blocking here holds the convene without
        stalling the event loop. An earlier version blocked
        _ftResolveCouncilPrincipal, which the campaign-LIST route also
        calls: that hung the page itself and the modal never opened.
        """
        eventRelease.wait(timeout=20)
        return fnRealProbe(*tArguments, **dictKeywords)

    monkeypatch.setattr(
        councilRoutes, "fnRefuseStartWithoutAProjectLogin",
        _fnSlowLoginProbe)

    _fdictActivateCouncilToolbar(pageDashboard, serverHub)
    pageDashboard.click("#btnAgentCouncil")
    pageDashboard.wait_for_selector("#btnCouncilPlanChange", timeout=8000)
    pageDashboard.click("#btnCouncilPlanChange")
    pageDashboard.wait_for_selector("#councilQuestion", timeout=8000)
    pageDashboard.fill("#councilQuestion", "Which sampler converges fastest?")
    # Models must be chosen or the body is rejected 422 before the
    # request reaches anything slow — and a convene that fails instantly
    # has no busy state to observe, which is a green test asserting
    # nothing.
    listOptions = pageDashboard.eval_on_selector(
        '.council-model[data-index="0"]',
        "el => Array.from(el.options).map(o => o.value).filter(v => v)")
    assert listOptions, "the model picker was never populated"
    pageDashboard.select_option(
        '.council-model[data-index="0"]', listOptions[0])
    # DISTINCT models: the campaign validator refuses a one-model
    # council, and that refusal is instant — so a duplicate here would
    # leave nothing slow to observe.
    pageDashboard.select_option(
        '.council-model[data-index="1"]', listOptions[1])
    pageDashboard.click("#btnCouncilConvene")

    try:
        pageDashboard.wait_for_function(
            "() => document.getElementById('councilConveneStatus')"
            " && document.getElementById('councilConveneStatus')"
            ".textContent.trim().length > 0", timeout=8000)
        sStatus = pageDashboard.inner_text("#councilConveneStatus")
        assert "Convening" in sStatus, sStatus
        assert pageDashboard.locator("#btnCouncilConvene").is_disabled(), (
            "the Convene button stayed live during the convene, so a "
            "second click starts a second campaign")
    finally:
        eventRelease.set()

    # The busy state must be LEAVABLE. A refused convene that stranded
    # the form disabled would trap the researcher's question inside it.
    pageDashboard.wait_for_function(
        "() => { const el = document.getElementById('btnCouncilConvene');"
        " return !el || !el.disabled; }", timeout=15000)


def testTheBrowserLaneNeverTouchesTheResearchersCouncilStore(serverHub):
    """Running this suite must not disturb a real, live council.

    The council's durable store root is derived from $HOME and this
    lane does not override $HOME, so the test hub used to share the
    researcher's ~/.vaibify/agentCouncils. Its startup reconcile
    classifies any campaign still in `planning` as `interrupted` — so
    merely STARTING this suite killed a researcher's in-flight council
    mid-turn, and did it with a reason string that reads like their own
    hub restarting (2026-08-24).

    A no-side-effects property cannot be asserted by observing that
    nothing broke, so this asserts the mechanism instead: the store
    root is somewhere else.
    """
    import os

    sStoreRoot = serverHub.app.state.dictCouncilCampaignStore[
        "sDurableStoreRoot"]
    sRealRoot = os.path.join(os.path.expanduser("~"), ".vaibify",
                             "agentCouncils")
    assert os.path.realpath(sStoreRoot) != os.path.realpath(sRealRoot), (
        f"the browser lane's council store IS the researcher's: "
        f"{sStoreRoot}")
    assert not os.path.realpath(sStoreRoot).startswith(
        os.path.realpath(sRealRoot) + os.sep), sStoreRoot


def testSettledTurnsAppearInThePanelWithoutAReload(pageDashboard, serverHub):
    """A finished turn must be visible, and must arrive on its own.

    The panel rendered campaign state and runner lifecycle only, so a
    participant that had produced a 20,000-token proposal still read
    "deliberating" and a researcher watching saw nothing happen for
    minutes (live report, 2026-08-24). Two independent defects had to
    be fixed for that: listRounds was rendered nowhere, and the
    re-render signature ignored turn state, so even a correct renderer
    would not have been called.

    Drives the REAL renderer against a REAL campaign payload, then
    mutates only the turn state and re-renders — which is what a poll
    does. Asserting the first render alone would pass with the
    signature bug fully intact.
    """
    _fnOpenCouncilWorkspace(pageDashboard, serverHub)

    sBefore = pageDashboard.evaluate(
        """() => {
            VaibifyAgentCouncil.fnSetCampaignForTest({
                sCampaignId: 'campaign-x', sState: 'planning',
                sQuestion: 'Refactor the integrator?',
                sChairbotParticipantId: 'p-opus',
                listParticipants: [
                    {sParticipantId: 'p-opus', sProvider: 'claude',
                     sRequestedModel: 'opus'},
                    {sParticipantId: 'p-sonn', sProvider: 'claude',
                     sRequestedModel: 'sonnet'}],
                listRounds: [{iRoundNumber: 1, dictTurnsByPhase: {
                    independentProposals: [
                        {sParticipantId: 'p-sonn', sStatus: 'inFlight'}]}}],
            });
            return document.getElementById(
                'agentCouncilWorkspaceBody').innerText;
        }"""
    )
    assert "sonnet" in sBefore

    sAfter = pageDashboard.evaluate(
        """() => {
            VaibifyAgentCouncil.fnSetCampaignForTest({
                sCampaignId: 'campaign-x', sState: 'planning',
                sQuestion: 'Refactor the integrator?',
                sChairbotParticipantId: 'p-opus',
                listParticipants: [
                    {sParticipantId: 'p-opus', sProvider: 'claude',
                     sRequestedModel: 'opus'},
                    {sParticipantId: 'p-sonn', sProvider: 'claude',
                     sRequestedModel: 'sonnet'}],
                listRounds: [{iRoundNumber: 1, dictTurnsByPhase: {
                    independentProposals: [
                        {sParticipantId: 'p-sonn', sStatus: 'completed',
                         dictModelIdentity: {dictUsage: {
                             output_tokens: 20478}}},
                        {sParticipantId: 'p-opus', sStatus: 'failed',
                         sFailureReason: 'noResultEvent'}]}}],
            });
            return document.getElementById(
                'agentCouncilWorkspaceBody').innerText;
        }"""
    )
    assert "20,478" in sAfter, (
        f"a settled turn's output never reached the panel: {sAfter!r}")
    assert "✓" in sAfter and "✗" in sAfter, sAfter
    assert "failed" in sAfter
    assert "independent proposals" in sAfter, (
        "the phase is unlabelled, so the researcher cannot tell a "
        f"proposal from a veto: {sAfter!r}")


def testTheStaleBannerRendersWhenPollFailuresAreRecorded(
    pageDashboard, serverHub,
):
    """RENDERER-ONLY, and labelled as such.

    Drives the banner from injected counters, so it proves the panel
    SHOWS a broken poll — not that a broken poll is detected. The
    detection half is deliberately unproven: a version of this test
    that drove real ticks observed nothing at all, because the loop
    reaches neither its success nor its failure branch under
    conditions I have not yet reproduced. That open thread is recorded
    in the commit rather than papered over with a green test.
    """
    _fnOpenCouncilWorkspace(pageDashboard, serverHub)
    sText = pageDashboard.evaluate(
        """() => {
            VaibifyAgentCouncil.fnSetPollHealthForTest(4, 'HTTP 404');
            VaibifyAgentCouncil.fnSetCampaignForTest({
                sCampaignId: 'c', sState: 'planning',
                sQuestion: 'Q', listParticipants: [], listRounds: []});
            return document.getElementById(
                'agentCouncilWorkspaceBody').innerText;
        }"""
    )
    assert "NOT updating" in sText, sText
    assert "4 consecutive" in sText, sText
    assert "HTTP 404" in sText, (
        f"the reason is hidden, so the researcher cannot act: {sText!r}")


def testAHealthyPollDoesNotCryWolf(pageDashboard, serverHub):
    """The other half: no warning when refreshing works.

    A banner that showed unconditionally would be ignored within a day
    and would cost the warning above all of its value.
    """
    _fnOpenCouncilWorkspace(pageDashboard, serverHub)
    sText = pageDashboard.evaluate(
        """() => {
            VaibifyAgentCouncil.fnSetPollHealthForTest(0, '');
            VaibifyAgentCouncil.fnSetCampaignForTest({
                sCampaignId: 'c', sState: 'planning',
                sQuestion: 'Q', listParticipants: [], listRounds: []});
            return document.getElementById(
                'agentCouncilWorkspaceBody').innerText;
        }"""
    )
    assert "NOT updating" not in sText, sText


def testAFailingEventsPollCannotFreezeTheCampaignRefresh(
    pageDashboard, serverHub,
):
    """The defect that froze a live council's panel for a whole run.

    Kills: awaiting the events poll and the campaign refresh in one try.

    Evidence from the researcher's own frozen session: requests fired
    every few seconds and EVERY one was `events?iAfter=0` — no campaign
    request at all, and iAfter never advanced. The events poll was
    throwing, the shared catch swallowed it, and the campaign refresh
    below it never executed. The panel rendered convene-time state
    while the backend reached needsHuman (2026-08-24).

    Stubs the TRANSPORT — not the logic under test — so the events
    route fails and the campaign route succeeds, then runs the real
    tick. The campaign must still arrive.
    """
    _fnOpenCouncilWorkspace(pageDashboard, serverHub)
    sText = pageDashboard.evaluate(
        """async () => {
            VaibifyAgentCouncil.fnSetCampaignForTest({
                sCampaignId: 'campaign-x', sState: 'planning',
                sQuestion: 'Original question',
                listParticipants: [], listRounds: []});
            const fnRealGet = VaibifyApi.fdictGet;
            VaibifyApi.fdictGet = function (sPath) {
                if (sPath.indexOf('/events') !== -1) {
                    return Promise.reject(new Error('events route broken'));
                }
                return Promise.resolve({dictCampaign: {
                    sCampaignId: 'campaign-x', sState: 'needsHuman',
                    sQuestion: 'Original question',
                    dictPendingHumanGate: {
                        sGateKind: 'blockingQuestion',
                        listQuestions: [{
                            sQuestionText: 'DECISION-THE-COUNCIL-NEEDS',
                            sRaisedByParticipantId: 'p1'}]},
                    listParticipants: [], listRounds: []}});
            };
            try {
                await VaibifyAgentCouncil.fnPollOnceForTest();
            } finally {
                VaibifyApi.fdictGet = fnRealGet;
            }
            return document.getElementById(
                'agentCouncilWorkspaceBody').innerText;
        }"""
    )
    assert "DECISION-THE-COUNCIL-NEEDS" in sText, (
        "a broken events poll starved the campaign refresh, so the "
        f"council's blocking question never reached the panel: {sText!r}")


def testARendererFaultIsNamedInsteadOfFreezingThePanel(
    pageDashboard, serverHub,
):
    """A dashboard bug must not masquerade as a quiet council.

    Kills: letting a render exception escape the poll.

    _fnIngestEvents ends by rendering, so a throw there escapes the
    events poll — and with both refreshes sharing one try, that also
    killed the campaign refresh. One bad render froze the whole panel
    with nothing on the console, which is indistinguishable from a
    council doing nothing.
    """
    _fnOpenCouncilWorkspace(pageDashboard, serverHub)
    sText = pageDashboard.evaluate(
        """() => {
            VaibifyAgentCouncil.fnSetCampaignForTest({
                sCampaignId: 'campaign-y', sState: 'planning',
                sQuestion: 'Q', listParticipants: [],
                /* A shape neither the signature nor the renderer can
                   walk — both call .map on it. */
                listRounds: 'not-an-array'});
            return document.getElementById(
                'agentCouncilWorkspaceBody').innerText;
        }"""
    )
    assert "could not draw" in sText, (
        f"a renderer fault left the panel silent: {sText!r}")
    assert "dashboard fault" in sText, sText


def testPollsCarryTheCampaignsDirectorySoAToolkitContainerCanRefresh(
    pageDashboard, serverHub,
):
    """The 409-on-every-poll that froze a live panel.

    Kills: dropping the directory from the poll URLs.

    A toolkit container tracks several repositories, so with no
    workflow open the server cannot resolve which one a bare request
    means and answers 409. Only `start` sent a chosen directory, so
    every read route refused from the moment the council convened and
    the panel showed convene-time state for the whole deliberation
    (live evidence, 2026-08-24).
    """
    _fnOpenCouncilWorkspace(pageDashboard, serverHub)
    listUrls = pageDashboard.evaluate(
        """async () => {
            VaibifyAgentCouncil.fnSetCampaignForTest({
                sCampaignId: 'campaign-z', sState: 'planning',
                sQuestion: 'Q', listParticipants: [], listRounds: [],
                dictProjectIdentity: {
                    sProjectRepoPath: '/workspace/vplanet-private'}});
            const listSeen = [];
            const fnRealGet = VaibifyApi.fdictGet;
            VaibifyApi.fdictGet = function (sPath) {
                listSeen.push(sPath);
                return Promise.resolve({dictCampaign: null, listEvents: []});
            };
            try {
                await VaibifyAgentCouncil.fnPollOnceForTest();
            } finally {
                VaibifyApi.fdictGet = fnRealGet;
            }
            return listSeen;
        }"""
    )
    assert listUrls, "the poll issued no requests at all"
    for sUrl in listUrls:
        assert "sProjectDirectory=vplanet-private" in sUrl, (
            f"a read went out without the campaign's directory: {sUrl}")


def testTheStateLineIsASentenceAndAgentsAreCalledAgents(
    pageDashboard, serverHub,
):
    """Researcher-facing wording, not protocol vocabulary.

    "Phase: needsHuman" told a researcher nothing about the five
    questions waiting for them, and "Participant 1" is the protocol's
    word for what they think of as an agent (2026-08-24).
    """
    _fnOpenCouncilWorkspace(pageDashboard, serverHub)
    sText = pageDashboard.evaluate(
        """() => {
            VaibifyAgentCouncil.fnSetCampaignForTest({
                sCampaignId: 'c', sState: 'needsHuman', sQuestion: 'Q',
                sChairbotParticipantId: 'p1',
                dictPendingHumanGate: {sGateKind: 'blockingQuestion',
                    listQuestions: [{sQuestionText: 'A REAL QUESTION',
                                     sRaisedByParticipantId: 'p1'}]},
                listParticipants: [{sParticipantId: 'p1',
                    sProvider: 'claude', sRequestedModel: 'opus'}],
                listRounds: []});
            return document.getElementById(
                'agentCouncilWorkspaceBody').innerText;
        }"""
    )
    assert "needs your opinion" in sText, sText
    assert "Phase: needsHuman" not in sText, sText
    assert "Agent 1" in sText, sText
    assert "Participant 1" not in sText, sText
    assert "A REAL QUESTION" in sText, (
        f"the blocking question never reached the panel: {sText!r}")


def testATimeBudgetKillOffersToRaiseTheBudget(pageDashboard, serverHub):
    """The one failure a researcher can fix, offered as a fix.

    Kills: leaving a wall-clock kill as red text among other red text.

    A turn destroyed at its time budget is not a model problem — it is
    a number that was too small, and the researcher cannot raise a
    number they do not know exists. Asserts the modal names the current
    budget, offers a larger one, and records the choice so the next
    convene actually sends it.
    """
    _fnOpenCouncilWorkspace(pageDashboard, serverHub)
    sModalText = pageDashboard.evaluate(
        """() => {
            window.localStorage.removeItem(
                'vaibifyCouncilTurnWallClockSeconds');
            VaibifyAgentCouncil.fnSetCampaignForTest({
                sCampaignId: 'c', sState: 'planning', sQuestion: 'Q',
                dictSettings: {iTurnWallClockSeconds: 3600},
                listParticipants: [], listRounds: [{
                    iRoundNumber: 1, dictTurnsByPhase: {
                        independentProposals: [{
                            sTurnId: 't1', sParticipantId: 'p1',
                            sStatus: 'failed',
                            sFailureReason: 'emptyTurn: ...',
                            sRejectedPayload:
                                '{"sEmptyResultReason": '
                                + '"killedAtTurnWallClockBudget"}'}]}}]});
            const el = document.getElementById('modalConfirm');
            return el ? el.innerText : '(no modal)';
        }"""
    )
    assert "ran out of time" in sModalText, sModalText
    assert "60 minutes" in sModalText, (
        f"the modal does not name the current budget: {sModalText!r}")
    assert "120 minutes" in sModalText, (
        f"the modal offers no larger budget: {sModalText!r}")

    # Accepting must change what the NEXT convene sends, or the modal
    # promised something it does not deliver.
    iSent = pageDashboard.evaluate(
        """() => {
            /* By ID. A comma selector returns the first match in
               DOCUMENT order, which is Cancel — so the first version of
               this clicked "Leave it" and asserted the confirm path. */
            document.getElementById('btnConfirmOk').click();
            return parseInt(window.localStorage.getItem(
                'vaibifyCouncilTurnWallClockSeconds'), 10);
        }"""
    )
    assert iSent == 7200, iSent



def testARaisedTimeBudgetIsSentOnTheNextConvene(
    pageDashboard, serverHub, monkeypatch,
):
    """Remembering the choice is not acting on it.

    Kills: dropping iTurnWallClockSeconds from the convene body.

    The modal offers a longer budget for the NEXT council. If the
    convene request keeps sending the default, the modal promised
    something that never happens — and a mutation deleting the field
    survived a test that only asserted the stored value.
    """
    from vaibify.gui import agentCouncilCredentialGate
    monkeypatch.setattr(
        agentCouncilCredentialGate, "fdictEvaluateCredentialEnablement",
        lambda sProvider, sImageIdentity=None: {
            "bEnabled": True, "sReason": "", "dictRecord": {}})

    _fdictActivateCouncilToolbar(pageDashboard, serverHub)
    pageDashboard.evaluate(
        """() => window.localStorage.setItem(
            'vaibifyCouncilTurnWallClockSeconds', '7200')""")
    pageDashboard.click("#btnAgentCouncil")
    pageDashboard.wait_for_selector("#btnCouncilPlanChange", timeout=8000)
    pageDashboard.click("#btnCouncilPlanChange")
    pageDashboard.wait_for_selector("#councilQuestion", timeout=8000)
    pageDashboard.fill("#councilQuestion", "Q")
    listOptions = pageDashboard.eval_on_selector(
        '.council-model[data-index="0"]',
        "el => Array.from(el.options).map(o => o.value).filter(v => v)")
    pageDashboard.select_option(
        '.council-model[data-index="0"]', listOptions[0])
    pageDashboard.select_option(
        '.council-model[data-index="1"]', listOptions[1])

    dictBody = pageDashboard.evaluate(
        """async () => {
            let dictSent = null;
            const fnRealPost = VaibifyApi.fdictPost;
            VaibifyApi.fdictPost = function (sPath, dictPayload) {
                dictSent = dictPayload;
                return Promise.reject(new Error('intercepted'));
            };
            try {
                document.getElementById('btnCouncilConvene').click();
                await new Promise(r => setTimeout(r, 400));
            } finally {
                VaibifyApi.fdictPost = fnRealPost;
            }
            return dictSent;
        }"""
    )
    assert dictBody, "the convene never issued a request"
    assert dictBody["dictSettings"]["iTurnWallClockSeconds"] == 7200, (
        "the raised budget was remembered but not sent, so the modal "
        f"promised something the next council will not do: {dictBody}")


def testAnAgentTabShowsThatAgentsWorkNotABareEventDump(
    pageDashboard, serverHub,
):
    """A console must show what happened, and whose.

    Kills: rendering the event envelope, or ignoring sParticipantId.

    Every agent tab showed the SAME global stream as bare names — "#4
    providerEvent", "#5 providerEvent" — while the payload sat unread
    in sParticipantId and dictProviderEvent. A row that names its
    envelope and hides its content is not a console, and two tabs
    showing identical content are not two agents (2026-08-25).
    """
    _fnOpenCouncilWorkspace(pageDashboard, serverHub)
    dictText = pageDashboard.evaluate(
        """() => {
            VaibifyAgentCouncil.fnSetEventsForTest([
                {iSequence: 1, sEventKind: 'roundOpened'},
                {iSequence: 2, sEventKind: 'providerEvent',
                 sParticipantId: 'p-opus',
                 dictProviderEvent: {type: 'assistant', message: {content: [
                     {type: 'text', text: 'OPUS-IS-READING-EVOLVE-C'},
                     {type: 'tool_use', name: 'Grep'}]}}},
                {iSequence: 3, sEventKind: 'providerEvent',
                 sParticipantId: 'p-sonn',
                 dictProviderEvent: {type: 'assistant', message: {content: [
                     {type: 'text', text: 'SONNET-IS-READING-UPDATE-C'}]}}},
            ]);
            VaibifyAgentCouncil.fnSetCampaignForTest({
                sCampaignId: 'c', sState: 'planning', sQuestion: 'Q',
                sChairbotParticipantId: 'p-opus', listRounds: [],
                listParticipants: [
                    {sParticipantId: 'p-opus', sProvider: 'claude',
                     sRequestedModel: 'opus'},
                    {sParticipantId: 'p-sonn', sProvider: 'claude',
                     sRequestedModel: 'sonnet'}]});
            const fnTab = (sId) => {
                VaibifyAgentCouncil.fnSelectTabForTest('participant:' + sId);
                return document.getElementById(
                    'agentCouncilWorkspaceBody').innerText;
            };
            return {sOpus: fnTab('p-opus'), sSonnet: fnTab('p-sonn')};
        }"""
    )
    assert "OPUS-IS-READING-EVOLVE-C" in dictText["sOpus"], dictText["sOpus"]
    assert "Grep" in dictText["sOpus"], (
        "the tool the agent invoked — the most informative thing in the "
        f"stream — is not shown: {dictText['sOpus']!r}")
    assert "SONNET-IS-READING-UPDATE-C" not in dictText["sOpus"], (
        "one agent's tab shows another agent's work; the tabs are not "
        "filtered by participant")
    assert "SONNET-IS-READING-UPDATE-C" in dictText["sSonnet"]
    assert "round opened" in dictText["sOpus"], (
        "council-level events belong in every agent's timeline")


def testTheCouncilsQuestionsAreNumbered(pageDashboard, serverHub):
    """A dozen questions in one box need numbers to refer to.

    Kills: rendering the blocking questions as an unordered list.

    A live council raised twelve at once, answered through a single
    text area — a researcher has to be able to write "on 3" and be
    understood (2026-08-25).
    """
    _fnOpenCouncilWorkspace(pageDashboard, serverHub)
    dictResult = pageDashboard.evaluate(
        """() => {
            VaibifyAgentCouncil.fnSetCampaignForTest({
                sCampaignId: 'c', sState: 'needsHuman', sQuestion: 'Q',
                listParticipants: [], listRounds: [],
                dictPendingHumanGate: {sGateKind: 'blockingQuestion',
                    listQuestions: [
                        {sQuestionText: 'FIRST', sRaisedByParticipantId: 'p'},
                        {sQuestionText: 'SECOND', sRaisedByParticipantId: 'p'},
                        {sQuestionText: 'THIRD', sRaisedByParticipantId: 'p'}]}});
            const el = document.querySelector('.council-questions');
            return {
                sTag: el ? el.tagName : '(missing)',
                sStyle: el ? getComputedStyle(el).listStyleType : '',
                iItems: el ? el.querySelectorAll('li').length : 0,
            };
        }"""
    )
    assert dictResult["sTag"] == "OL", dictResult
    assert dictResult["iItems"] == 3, dictResult
    assert dictResult["sStyle"] == "decimal", (
        f"the numbers are not rendered: {dictResult}")
