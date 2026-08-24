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
    fnRealProbe = councilRoutes._fnRefuseStartWithoutAProjectLogin

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
        councilRoutes, "_fnRefuseStartWithoutAProjectLogin",
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
