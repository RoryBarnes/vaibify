"""A deterministic Agent Council planning journey in a real browser.

Section 15.5: create a planning council, watch normalized events, answer
a blocking question, accept a plan, reload and reopen the campaign, and
show a stale-baseline warning after the project state changes — all
through the real backend routes, the REAL controller and engine, and
the in-process campaign store, with NO provider SDK and NO permissive
Docker fallback (lane 1 of the R12 verification lanes: this proves the
UI journey, nothing about real runners).

Since R1b the blocking-question gate and the candidate plan are
produced by the REAL engine driven over scripted fake provider
connections (needsHuman at the first veto, accept everywhere after),
and acceptance goes through the engine's planReady gate over the
server-held candidate. The only remaining record patch is the
stale-baseline flag, whose real producer is R12.
"""

import io
import json
import os
import shutil
import tarfile
import tempfile
import time

import pytest

from vaibify.gui import agentCouncilContext
from vaibify.gui import agentCouncilController
from vaibify.gui import agentCouncilRegistry
from vaibify.gui import agentCouncilStore

from tests.agentCouncilHarness import (
    CouncilRecorder,
    FakeCouncilConnection,
    fdictDecideCompleted,
    fdictMakeTurnResult,
)
from .fakeDockerAdapter import (
    S_CONTAINER_ID,
    S_CONTAINER_NAME,
    S_WORKFLOW_PATH,
)
from .testBrowserJourneys import _fnReleaseBrowserLaneOwnership


pytestmark = pytest.mark.browser


@pytest.fixture(autouse=True)
def _fnIsolateCouncilStore(serverHub):
    """Redirect the council's durable store to a throwaway directory.

    The hub builds its store rooted at ``~/.vaibify/agentCouncils``; the
    browser lane's registry isolation does not cover it, so without this
    a planning journey would write campaign records into the developer's
    real home. Swapping the app-state store for a temp-rooted one keeps
    the routes reading it fresh on every request.
    """
    sTempRoot = tempfile.mkdtemp(prefix="councilLane")
    dictStore = agentCouncilStore.fdictCreateCampaignStore(
        sDurableStoreRoot=sTempRoot)
    setattr(serverHub.app.state,
            agentCouncilStore.S_COUNCIL_CAMPAIGN_STORE_STATE_KEY, dictStore)
    yield
    _fnReleaseBrowserLaneOwnership(serverHub.app.state)
    shutil.rmtree(sTempRoot, ignore_errors=True)


def _fdictWriteLaneSnapshot(connectionDocker, sContainerId,
                            sProjectRepoPath, sCampaignId,
                            sSnapshotStoreRoot=None):
    """Write a minimal sealed snapshot the way the real capture would.

    The browser lane's fake Docker adapter cannot serve get_archive or
    the git identity reads, and lane 1 deliberately proves nothing
    about real containers — the real capture has its own live lane.
    """
    sDirectory = os.path.join(sSnapshotStoreRoot, sCampaignId, "snapshot")
    os.makedirs(sDirectory, exist_ok=True)
    with tarfile.open(
            os.path.join(sDirectory, "snapshot.tar"), "w") as fileTar:
        baProject = b'{"name": "browser-lane-fixture"}'
        infoProject = tarfile.TarInfo(name="project.json")
        infoProject.size = len(baProject)
        fileTar.addfile(infoProject, io.BytesIO(baProject))
    with open(os.path.join(sDirectory, "manifest.json"),
              "w") as fileManifest:
        fileManifest.write(json.dumps({
            "sSnapshotSha256": "browser-lane-snapshot-hash",
            "sCommitSha": "fixturecommit0001",
            "sDirtyStateDigest": "fixturedigest0001",
            "sBaselineHeadSha": "fixturecommit0001",
            "sBaselinePorcelainDigest": "fixtureporcelain0001"}))
    return {"sSnapshotSha256": "browser-lane-snapshot-hash"}


def _fdictDecideJourneyTurn(sHandle, dictTurnRequest):
    """needsHuman at the first veto; accept with a plan everywhere else."""
    if (dictTurnRequest["sPhase"] == "veto"
            and dictTurnRequest["iRoundNumber"] == 1):
        return fdictDecideCompleted(fdictMakeTurnResult(
            sVerdict="needsHuman",
            listOpenQuestions=["Choose the cache invalidation policy."]))
    return fdictDecideCompleted(fdictMakeTurnResult(
        sVerdict="accept",
        listPlanItems=["add a content-hash cache to the slow step"],
        sSummary="Cache the slow step keyed on content hashes."))


@pytest.fixture(autouse=True)
def _fnScriptedProviderSeam(monkeypatch):
    """Route the controller's provider seam onto the scripted fakes."""
    recorder = CouncilRecorder()
    monkeypatch.setattr(
        agentCouncilController, "fconnectionBuildParticipantConnection",
        lambda dictRuntime, dictParticipant: FakeCouncilConnection(
            dictParticipant["sParticipantId"], _fdictDecideJourneyTurn,
            recorder))
    monkeypatch.setattr(
        agentCouncilContext, "fdictCaptureProjectContextSnapshot",
        _fdictWriteLaneSnapshot)
    from vaibify.gui import agentCouncilCredentialGate
    monkeypatch.setattr(
        agentCouncilCredentialGate, "fdictEvaluateCredentialEnablement",
        lambda sProvider, sImageIdentity=None: {
            "bEnabled": True, "sReason": "", "dictRecord": {}})


def _fdictStore(serverHub):
    return getattr(serverHub.app.state,
                   agentCouncilStore.S_COUNCIL_CAMPAIGN_STORE_STATE_KEY)


def _fsNewestCampaignId(serverHub):
    listSummaries = agentCouncilStore.flistSummariseCampaigns(
        _fdictStore(serverHub))
    assert listSummaries, "no campaign was created"
    return listSummaries[-1]["sCampaignId"]


def _fnPatchCampaign(serverHub, sCampaignId, dictPatch):
    """Merge a patch into a stored campaign and re-checkpoint it.

    This is the deterministic stand-in for what the engine would settle
    from a real turn — a blocking gate, a candidate plan, a stale
    baseline flag — so the UI can be driven to render backend truth.
    """
    dictStore = _fdictStore(serverHub)
    dictCampaign = agentCouncilStore.fjsonGetCampaignRecord(
        dictStore, sCampaignId)
    dictCampaign.update(dictPatch)
    agentCouncilStore.fnCheckpointStoredCampaign(
        dictStore, sCampaignId, dictCampaign)


def _fnRetireLiveTurns(serverHub, sCampaignId):
    """Retire the campaign's in-flight turn so the next launch is admitted.

    Phase 3 mints a turn record on start and never retires it (the paid
    turn is driven by the registry at integration). A human answer
    launches a fresh turn, which the registry refuses while one is still
    live — so this stands in for the turn the engine would have settled
    before the campaign reached the human gate.
    """
    dictRegistry = getattr(
        serverHub.app.state,
        agentCouncilRegistry.S_COUNCIL_REGISTRY_STATE_KEY)
    for tTurnKey in list(dictRegistry["setTurnsInFlight"]):
        if tTurnKey[0] == sCampaignId:
            agentCouncilRegistry.fnRetireTurnInFlight(
                dictRegistry, sCampaignId, tTurnKey[1])


def _fdictClaimAndActivate(page, serverHub):
    """Claim the container and activate the council, returning capabilities.

    The council routes authorize through the container owner lease, so a
    real claim is what lets the planning POSTs land. Activation fetches
    the capabilities the toolbar button reads.
    """
    page.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    page.wait_for_selector(".container-tile", timeout=10000)
    return page.evaluate(
        """async ([sContainerId, sName, sWorkflowPath]) => {
            const dictClaim = await VaibifyApi.fdictPost(
                '/api/registry/' + encodeURIComponent(sName) + '/claim', {});
            VaibifyApp.fnRecordClaimedLease(sName, dictClaim.sLeaseId);
            await VaibifyApp.fnEnterNoWorkflow(sContainerId);
            /* A campaign is bound to the open workflow's project repo
             * (remediation R2), so the journey opens the workflow the
             * way the dashboard does before convening a council. */
            await VaibifyApi.fdictPostRaw(
                '/api/connect/' + sContainerId +
                '?sWorkflowPath=' + encodeURIComponent(sWorkflowPath));
            VaibifyAgentCouncil.fnActivate(sContainerId);
            await VaibifyAgentCouncil.fnRefreshCapabilities();
            const elButton = document.getElementById('btnAgentCouncil');
            return {bDisabled: elButton.disabled};
        }""",
        [S_CONTAINER_ID, S_CONTAINER_NAME, S_WORKFLOW_PATH],
    )


def _fnConveneThroughTheForm(page):
    page.click("#btnAgentCouncil")
    page.wait_for_selector("#btnCouncilPlanChange", timeout=5000)
    page.click("#btnCouncilPlanChange")
    page.wait_for_selector("#councilQuestion", timeout=5000)
    page.fill("#councilQuestion",
              "Should the slow pipeline step gain a caching layer?")
    page.fill('.council-model[data-index="0"]', "planner-one")
    page.fill('.council-model[data-index="1"]', "planner-two")
    page.click("#btnCouncilConvene")
    page.wait_for_selector("#agentCouncilWorkspaceBody .council-summary",
                           timeout=8000)


def testCouncilPlanningJourney(pageDashboard, serverHub, monkeypatch):
    """The whole planning arc, driven through the real backend store."""
    dictActivation = _fdictClaimAndActivate(pageDashboard, serverHub)
    assert dictActivation["bDisabled"] is False, (
        "a container project with two supported participants must enable "
        "the Agent Council button"
    )

    _fnConveneThroughTheForm(pageDashboard)
    sCampaignId = _fsNewestCampaignId(serverHub)

    # The start event reaches the read-only console through the poll.
    pageDashboard.click('.council-tab[data-tab^="participant:"]')
    pageDashboard.wait_for_selector(".council-event", timeout=8000)

    _fnAnswerABlockingQuestion(pageDashboard, serverHub, sCampaignId)
    _fnAcceptTheCandidatePlan(pageDashboard, serverHub, sCampaignId)
    _fnReloadAndReopen(pageDashboard, serverHub, sCampaignId)
    _fnShowStaleBaselineWarning(
        pageDashboard, serverHub, sCampaignId, monkeypatch)

    assert pageDashboard.listPageErrors == []
    assert pageDashboard.listConsoleErrors == []


def _fnAnswerABlockingQuestion(page, serverHub, sCampaignId):
    """The REAL gate: the engine's first veto raised needsHuman."""
    _fnWaitForState(page, serverHub, sCampaignId, "needsHuman")
    page.click('.council-tab[data-tab="council"]')
    page.wait_for_selector(".council-needs-human", timeout=16000)
    page.fill("#councilAnswer", "Use the content-hash policy.")
    page.click("#btnCouncilAnswer")
    _fnWaitForResponseRecorded(page, serverHub, sCampaignId)


def _fnWaitForResponseRecorded(page, serverHub, sCampaignId):
    """Poll the store until the answer lands, asserting backend truth."""
    for _ in range(50):
        dictRecord = agentCouncilStore.fjsonGetCampaignRecord(
            _fdictStore(serverHub), sCampaignId)
        if dictRecord["listResearcherResponses"]:
            return
        page.wait_for_timeout(200)
    raise AssertionError("the answer never reached the campaign record")


def _fnAcceptTheCandidatePlan(page, serverHub, sCampaignId):
    """Acceptance over the REAL planReady candidate the engine adopted."""
    _fnWaitForState(page, serverHub, sCampaignId, "planReady")
    page.wait_for_selector('.council-tab[data-tab="plan"]', timeout=16000)
    page.click('.council-tab[data-tab="plan"]')
    page.wait_for_selector("#btnCouncilAcceptPlan", timeout=16000)
    page.click("#btnCouncilAcceptPlan")
    _fnWaitForState(page, serverHub, sCampaignId, "awaitingImplementation")
    page.wait_for_function(
        """() => document.querySelector('.council-plan-accepted') !== null""",
        timeout=16000,
    )


def _fnWaitForState(page, serverHub, sCampaignId, sState):
    for _ in range(50):
        dictRecord = agentCouncilStore.fjsonGetCampaignRecord(
            _fdictStore(serverHub), sCampaignId)
        if dictRecord["sState"] == sState:
            return
        page.wait_for_timeout(200)
    raise AssertionError(
        "campaign never reached %s (stuck at %s)"
        % (sState, dictRecord["sState"]))


def _fnReloadAndReopen(page, serverHub, sCampaignId):
    page.reload(wait_until="load")
    page.wait_for_selector(".container-tile", timeout=10000)
    page.evaluate(
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
        }""",
        [S_CONTAINER_ID, S_CONTAINER_NAME, S_WORKFLOW_PATH],
    )
    page.click("#btnAgentCouncil")
    page.wait_for_selector("#btnCouncilOpenExisting", timeout=5000)
    page.click(".council-open-row")
    page.wait_for_selector("#agentCouncilWorkspaceBody .council-summary",
                           timeout=8000)


def _fnShowStaleBaselineWarning(page, serverHub, sCampaignId, monkeypatch):
    """The UI renders the backend's staleness verdict (lane 1 scope).

    The producer itself is REAL since R12 — computed per read from the
    sealed manifest against the live repository — and its computation
    is proven in tests/testCouncilControllerIntegration.py. Lane 1's
    fake Docker adapter has no repository to move, so this patches the
    route-level producer, not the record: the record carries no
    staleness field at all any more.
    """
    from vaibify.gui.routes import councilRoutes
    monkeypatch.setattr(
        councilRoutes, "_fdictComputeBaselineStaleness",
        lambda *listArguments: {
            "bPlanningBaselineStale": True,
            "sPlanningBaselineSummary":
                "3 files changed since the council ran"})
    page.click('.council-tab[data-tab="council"]')
    page.wait_for_selector(
        ".council-verdict-blockedForWantOfEvidence", timeout=16000)
    sText = page.text_content(".council-verdict-blockedForWantOfEvidence")
    assert "baseline" in sText.lower(), (
        "the stale-baseline warning did not name the baseline"
    )


def testMissingProviderSdkDoesNotBlockTheDashboard(pageDashboard, serverHub):
    """The council is offered even with no provider SDK installed.

    The browser lane carries no Anthropic or OpenAI SDK, and the
    dashboard must load and report council capabilities honestly rather
    than crash. A missing SDK makes only that provider unavailable; it
    never prevents the dashboard from starting (section 8.1).
    """
    dictActivation = _fdictClaimAndActivate(pageDashboard, serverHub)
    assert dictActivation["bDisabled"] is False
    dictCapabilities = pageDashboard.evaluate(
        """async ([sContainerId]) => {
            return await VaibifyApi.fdictGet(
                '/api/agent-councils/' + encodeURIComponent(sContainerId)
                + '/capabilities');
        }""",
        [S_CONTAINER_ID],
    )
    assert dictCapabilities["bAvailable"] is True
    assert dictCapabilities["listProviders"], "no providers were offered"
    assert pageDashboard.listPageErrors == []
    assert pageDashboard.listConsoleErrors == []
    _fnReleaseBrowserLaneOwnership(serverHub.app.state)


def test_isolation_root_is_a_directory(tmp_path):
    """A guard that the isolation import path is intact off the browser."""
    assert os.path.isdir(str(tmp_path))
