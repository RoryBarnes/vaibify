"""Falsification tests for the deliberate pause (2026-08-29).

The researcher who has to close the laptop. Before this, the only
control was "Stop council", which ARCHIVES — so stepping away meant
abandoning the run or leaving it going overnight.

Three lanes, because the guarantee spans three layers and each can be
broken without the others noticing:

- the ENGINE lane proves where a pause is honoured (a settled phase
  boundary, never mid-phase) and that a pause the walk outruns is
  retired rather than left to stall the next thing the researcher
  clicks;
- the CONTROLLER lane proves a pause claims nothing about runners —
  it tears nothing down, so it cannot strand one nobody proved gone —
  and that the resume already built for the crash case continues a
  paused campaign without re-running settled work;
- the ROUTE lane drives real HTTP end to end: pause, stand-down,
  resume, and the expired-login pre-flight a council paused overnight
  actually meets in the morning.
"""

import asyncio
import copy

import pytest

from tests.agentCouncilHarness import (
    fdictDecideCompleted,
    fdictMakeTurnResult,
    ffnDecideAllAccept,
    fixtureBuildCouncil,
)
from tests.testCouncilResume import (
    LIST_TWO_SPECS,
    _RecordingAcceptConnection,
    _tPlantCrashedCampaign,
)
from tests.testCouncilRoutes import (  # noqa: F401  (fixture re-export)
    S_CONTAINER_ID,
    S_IMAGE_IDENTITY,
    _fnWaitForCampaignState,
    _sStartOneCampaign,
    eventTurnGate,
    tOwnerClient,
)
from vaibify.gui import agentCouncilController
from vaibify.gui import agentCouncilStore

LIST_SEQUENTIAL_TURN_SETTINGS = {"iMaximumConcurrentTurns": 1}


def _ffnDecidePausingDuring(sPauseHandle, sPausePhase, dictEngineHolder):
    """Build a decide callback that pauses from inside one live turn.

    The pause arrives while a turn is running, which is the only
    moment it can arrive in production, and with one turn per wave the
    NEXT participant of the same phase has not been launched yet — so
    a pause honoured anywhere but the phase boundary is visible as a
    turn that never ran.
    """

    def _fdictDecide(sHandle, dictTurnRequest):
        if (sHandle == sPauseHandle
                and dictTurnRequest["sPhase"] == sPausePhase):
            dictEngineHolder["engine"].fnRequestPauseAfterCurrentPhase()
        return fdictDecideCompleted(fdictMakeTurnResult(sVerdict="accept"))

    return _fdictDecide


@pytest.mark.falsification
def testAPauseStandsDownAtAPhaseBoundaryNeverMidPhase():
    """Every participant of the running phase still answers.

    A pause requested during alpha's cross-review turn must not cost
    beta its turn: a killed or unlaunched turn is a failure nothing can
    attribute, which is the defect class the stop lane already carries
    (it records ``notStarted`` turns, acceptable for a campaign about
    to be archived and wrong for one meant to continue). The walk then
    stops with the round mid-flight, still ``planning``, and with the
    durable attempt record proving a boundary the resume admission
    recognises.

    Kills: honouring the pause where the stop is honoured — between
    turn waves, inside the phase.
    """
    dictEngineHolder = {}
    fixtureCouncil = fixtureBuildCouncil(
        LIST_TWO_SPECS,
        _ffnDecidePausingDuring(
            "alpha", "crossReview", dictEngineHolder),
        dictSettings=dict(LIST_SEQUENTIAL_TURN_SETTINGS),
        sChairbotHandle="alpha")
    dictEngineHolder["engine"] = fixtureCouncil.engine

    dictSettled = fixtureCouncil.fdictDrive()

    dictRound = dictSettled["listRounds"][-1]
    listCrossReview = dictRound["dictTurnsByPhase"]["crossReview"]
    assert len(listCrossReview) == 2, listCrossReview
    assert [dictTurn["sStatus"] for dictTurn in listCrossReview] == [
        "completed", "completed"], listCrossReview
    # The phase after the paused one never began.
    assert "synthesis" not in dictRound["dictTurnsByPhase"]
    assert dictSettled["sState"] == "planning"
    assert dictSettled["bPauseRequested"] is True
    # Nothing is left half-written underneath the record: the same
    # predicate the startup classifier and the resume route consult.
    assert agentCouncilController._fbCampaignStoppedAtAProvenBoundary(
        dictSettled) is True


@pytest.mark.falsification
def testAPauseTheWalkOutrunsIsRetiredNotLeftToStall():
    """A pause overtaken by a human gate must not survive it.

    The council stopped for its own reason — a blocking question — so
    nothing was paused. A flag left standing would make the
    researcher's answer a no-op: the continuation spawns a drive whose
    first act is to stand down again, and the panel shows a council
    that ignores every click.

    Kills: the walk leaving a pause request set on a record it carried
    out of the planning state.
    """
    dictEngineHolder = {}

    def _fdictDecideAsking(sHandle, dictTurnRequest):
        if dictTurnRequest["sPhase"] == "synthesis":
            dictEngineHolder["engine"].fnRequestPauseAfterCurrentPhase()
            return fdictDecideCompleted(fdictMakeTurnResult(
                sVerdict="needsHuman",
                listOpenQuestions=["Which tolerance is authoritative?"]))
        return fdictDecideCompleted(fdictMakeTurnResult(sVerdict="accept"))

    fixtureCouncil = fixtureBuildCouncil(
        LIST_TWO_SPECS, _fdictDecideAsking, sChairbotHandle="alpha")
    dictEngineHolder["engine"] = fixtureCouncil.engine

    dictSettled = fixtureCouncil.fdictDrive()

    assert dictSettled["sState"] == "needsHuman", dictSettled["sState"]
    assert dictSettled["bPauseRequested"] is False, (
        "a pause the walk outran was left standing, so the researcher's "
        "answer would spawn a drive that stands down at once")


@pytest.mark.falsification
def testPauseIsRefusedWhenNothingIsDeliberating(tmp_path):
    """A pause nobody can honour must be refused, not recorded.

    The flag is acted on by a live walk and by nothing else, so setting
    it on a record no drive is walking advertises a stand-down that
    will never happen — the researcher comes back to a council that
    has been "pausing" all night and to a resume surface derived from
    a lie.

    Kills: admitting the pause command against a campaign with no live
    drive.
    """
    from tests.testCouncilResume import _fdictCaptureMidWalkVersion

    dictStore, _, dictControllerState, sCampaignId = _tPlantCrashedCampaign(
        tmp_path, _fdictCaptureMidWalkVersion())

    with pytest.raises(agentCouncilController.CouncilCommandError) as errorInfo:
        asyncio.run(agentCouncilController.fdictRequestCampaignPause(
            dictControllerState, dictStore, sCampaignId))

    assert "not deliberating" in str(errorInfo.value), str(errorInfo.value)
    assert agentCouncilStore.fjsonGetCampaignRecord(
        dictStore, sCampaignId).get("bPauseRequested") is not True


@pytest.mark.falsification
def testPauseProvesNothingAboutRunnersBecauseItTearsNothingDown(
        tmp_path, monkeypatch):
    """The honesty property, asserted as the absence of a teardown.

    "Pause must not become a way to strand a quarantined runner" is
    guaranteed structurally rather than by a probe: the command
    releases nothing, so there is no absence for it to fail to prove.
    A paused campaign holds its egress boundary exactly as a campaign
    sitting at a human gate does, and the paths that release a gate's
    — a lease release, a delete, shutdown, the startup sweep — release
    this one.

    Kills: a pause that reaches for the egress teardown, whose
    indeterminate answer would quarantine a working council over a
    researcher going home.
    """
    listReleaseCalls = []
    monkeypatch.setattr(
        agentCouncilController, "_fbReleaseRunnerAccessResources",
        lambda dictRuntime: listReleaseCalls.append(dictRuntime) or True)

    dictStore = agentCouncilStore.fdictCreateCampaignStore(
        sDurableStoreRoot=str(tmp_path / "councils"))
    fixtureCouncil = fixtureBuildCouncil(
        LIST_TWO_SPECS, ffnDecideAllAccept, sChairbotHandle="alpha")
    agentCouncilStore.fdictRegisterStartedCampaign(
        dictStore, copy.deepcopy(fixtureCouncil.dictCampaign))
    sCampaignId = fixtureCouncil.dictCampaign["sCampaignId"]
    dictControllerState = (
        agentCouncilController.fdictCreateCouncilControllerState())

    async def _fdictPauseALiveDrive():
        eventHeld = asyncio.Event()

        async def _fnHeldDrive():
            await eventHeld.wait()

        dictRuntime = {
            "sCampaignId": sCampaignId,
            "dictCampaign": fixtureCouncil.dictCampaign,
            "dictStore": dictStore,
            "engineCouncil": fixtureCouncil.engine,
            "dictRunnerAccess": {"dictEgress": {"sNetworkName": "net-x"}},
            "bLaunchInProgress": False,
            "taskDrive": asyncio.ensure_future(_fnHeldDrive()),
            "sTurnId": "turn-1",
        }
        dictControllerState["dictCampaignRuntime"][sCampaignId] = dictRuntime
        dictPaused = await agentCouncilController.fdictRequestCampaignPause(
            dictControllerState, dictStore, sCampaignId)
        eventHeld.set()
        await dictRuntime["taskDrive"]
        return dictPaused, dictRuntime

    dictPaused, dictRuntime = asyncio.run(_fdictPauseALiveDrive())

    assert dictPaused["bPauseRequested"] is True
    assert dictPaused["bSettled"] is False
    assert listReleaseCalls == [], (
        "the pause tore down runner access, so an indeterminate answer "
        "could quarantine a council whose researcher merely went home")
    assert dictRuntime["dictRunnerAccess"] is not None
    assert sCampaignId in dictControllerState["dictCampaignRuntime"]


def _tPlantPausedCampaign(tmp_path):
    """Drive a real council to a PAUSE, then plant what disk would hold.

    The version is produced by the engine standing down, never by hand:
    a planted flag would prove the resume works on a record shape
    production may not write.
    """
    from tests.agentCouncilHarness import VersionRecordingCheckpoint

    dictEngineHolder = {}
    checkpointRecorder = VersionRecordingCheckpoint()
    fixtureCouncil = fixtureBuildCouncil(
        LIST_TWO_SPECS,
        _ffnDecidePausingDuring("alpha", "crossReview", dictEngineHolder),
        dictSettings=dict(LIST_SEQUENTIAL_TURN_SETTINGS),
        sChairbotHandle="alpha", checkpoint=checkpointRecorder)
    dictEngineHolder["engine"] = fixtureCouncil.engine
    dictSettled = fixtureCouncil.fdictDrive()
    assert dictSettled["bPauseRequested"] is True
    return _tPlantCrashedCampaign(tmp_path, copy.deepcopy(dictSettled))


@pytest.mark.falsification
def testResumingAPausedCouncilClearsThePauseAndContinuesTheWalk(
        tmp_path, monkeypatch):
    """Resume is the un-pause, and it uses the crash-resume machinery.

    A paused campaign is planning at a proven boundary — exactly the
    shape a hub killed between phases leaves — so it continues through
    the one resume path rather than a second one written beside it.
    The flag must be cleared as part of that: kept, the rebuilt drive
    stands down before running anything and the researcher's click
    does nothing at all.

    Kills: resume leaving ``bPauseRequested`` set, which turns the
    button into a no-op that looks like a hang.
    """
    dictStore, dictRegistry, dictControllerState, sCampaignId = (
        _tPlantPausedCampaign(tmp_path))
    listPhaseLog = []
    monkeypatch.setattr(
        agentCouncilController, "fconnectionBuildParticipantConnection",
        lambda dictRuntime, dictParticipant:
            _RecordingAcceptConnection(listPhaseLog))

    async def _fdictRun():
        dictResumed = (
            await agentCouncilController.fdictResumeCampaignDeliberation(
                dictControllerState, dictStore, dictRegistry, sCampaignId,
                S_IMAGE_IDENTITY))
        dictRuntime = dictControllerState["dictCampaignRuntime"].get(
            sCampaignId)
        if dictRuntime is not None and dictRuntime.get(
                "taskDrive") is not None:
            await dictRuntime["taskDrive"]
        return dictResumed

    dictResumed = asyncio.run(_fdictRun())

    assert dictResumed["bResumed"] is True
    dictRecord = agentCouncilStore.fjsonGetCampaignRecord(
        dictStore, sCampaignId)
    assert dictRecord["bPauseRequested"] is False
    assert dictRecord["sState"] == "planReady", dictRecord["sState"]
    # Settled work is never re-run: cross-review had both turns.
    assert set(listPhaseLog) == {"synthesis", "veto"}, listPhaseLog


@pytest.mark.falsification
def testAPausedCouncilSurvivesTheHubExitingAndComesBackResumable(
        tmp_path, monkeypatch):
    """Close the laptop, the hub exits, come back in the morning.

    The whole scenario, through the REAL shutdown lifecycle rather than
    a planted record — the composition failure the 2026-08-27 review
    found for resume, which the tests missed by never running the
    lifecycle. Shutdown asks every registered runtime to stop, and a
    paused campaign has one: if that request reached disk the record
    would carry a stop the researcher never asked for, and the morning
    resume would refuse with "resuming would archive it immediately".

    The startup classifier must then leave it in planning, and the
    stopping point must still offer resume.

    Kills: shutdown's cooperative stop being checkpointed onto a
    campaign whose drive had already settled.
    """
    dictStore, _, dictControllerState, sCampaignId = _tPlantPausedCampaign(
        tmp_path)
    listReleased = []
    monkeypatch.setattr(
        agentCouncilController, "_fbReleaseRunnerAccessResources",
        lambda dictRuntime: listReleased.append(dictRuntime) or True)
    # ONE record object behind both, exactly as the production runtime
    # builds it: the engine drives the same dict the runtime holds, so
    # a checkpoint of either would carry the other's writes.
    dictLiveRecord = agentCouncilStore.fjsonGetCampaignRecord(
        dictStore, sCampaignId)
    dictControllerState["dictCampaignRuntime"][sCampaignId] = {
        "sCampaignId": sCampaignId,
        "dictCampaign": dictLiveRecord,
        "dictStore": dictStore,
        "engineCouncil": _fdictBuildInertEngine(
            dictStore, sCampaignId, dictLiveRecord),
        "dictRunnerAccess": {"dictEgress": {"sNetworkName": "net-x"}},
        "bLaunchInProgress": False,
        "taskDrive": None,
        "sTurnId": "",
    }

    agentCouncilController.fnDrainControllerOnShutdown(dictControllerState)
    asyncio.run(agentCouncilController.fnAwaitControllerSettleOnShutdown(
        dictControllerState))

    # A NEW store over the same durable root: what the morning's hub
    # actually reads off disk.
    dictReloaded = agentCouncilStore.fdictCreateCampaignStore(
        sDurableStoreRoot=dictStore["sDurableStoreRoot"])
    agentCouncilStore.fdictReloadDurableCampaigns(dictReloaded)
    iClassified = (
        agentCouncilController.fiClassifyInterruptedCampaignsOnStartup(
            dictReloaded))

    assert listReleased, "shutdown left the egress boundary provisioned"
    assert iClassified == 0
    dictRecord = agentCouncilStore.fjsonGetCampaignRecord(
        dictReloaded, sCampaignId)
    assert dictRecord["sState"] == "planning"
    assert dictRecord["bPauseRequested"] is True
    assert dictRecord["bStopRequested"] is False, (
        "the hub's own shutdown wrote a stop onto a paused council, so "
        "the morning resume refuses over a decision nobody made")
    assert agentCouncilStore.fdictDescribeStoredStoppingPoint(
        dictReloaded, sCampaignId)["sAction"] == "resume"


def _fdictBuildInertEngine(dictStore, sCampaignId, dictCampaign):
    """A real engine over the runtime's record, driving no connections.

    Shutdown's quiet stop calls a REAL engine method, so a double would
    make the test prove nothing about what production does with it.
    """
    from vaibify.gui.agentCouncil import CouncilEngine
    from vaibify.gui import agentCouncilCampaign

    def _fdictRefuseBaseline(dictRequest):
        raise AssertionError("shutdown drives no baseline evidence")

    return CouncilEngine(
        dictCampaign,
        {dictParticipant["sParticipantId"]:
            agentCouncilCampaign.CouncilProviderConnection()
         for dictParticipant in dictCampaign["listParticipants"]},
        lambda dictEvent: agentCouncilStore.fdictAppendCampaignEvent(
            dictStore, sCampaignId, dictEvent),
        lambda dictEntry: dictEntry,
        lambda dictSettled: agentCouncilStore.fnCheckpointStoredCampaign(
            dictStore, sCampaignId, dictSettled),
        _fdictRefuseBaseline)


# ── the route lane: pause, stand down, resume, over real HTTP ──────

def _fnPauseAndWaitForStandDown(client, app, sCampaignId, eventTurnGate):
    """Pause a live campaign, open the gate, and wait for the boundary."""
    responsePause = client.post(
        f"/api/agent-councils/{S_CONTAINER_ID}/{sCampaignId}/pause")
    assert responsePause.status_code == 200, responsePause.text
    eventTurnGate.set()
    from tests.testCouncilRoutes import _fnWaitForNoLiveCouncilWork
    _fnWaitForNoLiveCouncilWork(app)
    return responsePause.json()


@pytest.mark.falsification
def testPauseLeavesTheCouncilResumableWhereStopArchivesIt(
        tOwnerClient, eventTurnGate):
    """The whole point, over real HTTP: pause is not stop.

    The sibling test for the stop drives the identical shape and ends
    at ``archived``; this one must end at ``planning`` with the
    stopping point offering resume. If the two ever converge on one
    outcome the researcher has no way to step away from a council
    without abandoning it.

    Kills: routing the pause into the stop's archive transition.
    """
    client, app, _ = tOwnerClient
    sCampaignId = _sStartOneCampaign(client)

    dictPaused = _fnPauseAndWaitForStandDown(
        client, app, sCampaignId, eventTurnGate)
    assert dictPaused["bPauseRequested"] is True
    assert dictPaused["bSettled"] is False
    assert dictPaused["dictCampaign"]["sState"] == "planning"

    responseRead = client.get(
        f"/api/agent-councils/{S_CONTAINER_ID}/{sCampaignId}")
    assert responseRead.status_code == 200, responseRead.text
    dictCampaign = responseRead.json()["dictCampaign"]
    assert dictCampaign["sState"] == "planning"
    assert dictCampaign["bPauseRequested"] is True
    assert dictCampaign["dictStoppingPoint"]["sAction"] == "resume", (
        dictCampaign["dictStoppingPoint"])
    # The panel's own liveness statement: nothing is deliberating.
    assert dictCampaign["dictPhaseInFlight"] is None


@pytest.mark.falsification
def testResumingAPausedCouncilRefusesAnExpiredLoginInsteadOf500(
        tOwnerClient, eventTurnGate, monkeypatch):
    """The morning after: the login pre-flight must actually run.

    A runner is handed the access token WITHOUT the refresh token, so a
    council paused overnight and resumed at breakfast can face a login
    it cannot renew — the one failure this pre-flight exists to catch
    before a turn is spent discovering it. The guard was unreachable:
    the resume handler read a free ``dictCampaign`` and raised
    NameError first, so every resume answered 500 and no test had ever
    driven the route (2026-08-29).

    Kills: the login pre-flight reading a name the handler never bound,
    which turns every resume — expired login or not — into a 500.
    """
    import json
    import time

    client, app, _ = tOwnerClient
    sCampaignId = _sStartOneCampaign(client)
    _fnPauseAndWaitForStandDown(client, app, sCampaignId, eventTurnGate)

    def _fbaExpiredLogin(sContainerId, sPath):
        return json.dumps({"claudeAiOauth": {
            "accessToken": "fixture-access-token",
            "expiresAt": int((time.time() - 7200) * 1000)}}).encode("utf-8")

    monkeypatch.setattr(
        app.state.dictRouteContext["docker"], "fbaFetchCredentialFile",
        _fbaExpiredLogin, raising=False)

    responseResume = client.post(
        f"/api/agent-councils/{S_CONTAINER_ID}/{sCampaignId}/resume",
        json={"bClearStopRequest": False})

    assert responseResume.status_code == 409, (
        f"{responseResume.status_code}: {responseResume.text}")
    assert "expired" in responseResume.text, responseResume.text
    assert "refresh token" in responseResume.text, responseResume.text


def testAPausedCampaignReadsAsPausedInTheListing(
        tOwnerClient, eventTurnGate):
    """The listing must not show a paused council as merely planning.

    A row cannot distinguish a paused campaign from a crashed one —
    both are planning at a proven boundary — so the durable flag is
    reported in its own words. "Pause requested", not "paused": the
    listing has no liveness and cannot know whether the phase that was
    running has finished.
    """
    client, app, _ = tOwnerClient
    sCampaignId = _sStartOneCampaign(client)
    _fnPauseAndWaitForStandDown(client, app, sCampaignId, eventTurnGate)

    responseList = client.get(f"/api/agent-councils/{S_CONTAINER_ID}")
    assert responseList.status_code == 200, responseList.text
    listMatching = [
        dictSummary for dictSummary
        in responseList.json()["listCampaigns"]
        if dictSummary["sCampaignId"] == sCampaignId]
    assert listMatching, responseList.text
    assert listMatching[0]["bPauseRequested"] is True


def testAPauseIsRefusedOverHttpWhenNothingIsDeliberating(
        tOwnerClient, eventTurnGate):
    """The route surfaces the controller's refusal as a 409, not a 500."""
    client, app, _ = tOwnerClient
    eventTurnGate.set()
    sCampaignId = _sStartOneCampaign(client)
    from vaibify.gui import agentCouncilCampaign
    _fnWaitForCampaignState(
        app, sCampaignId, agentCouncilCampaign.S_STATE_PLAN_READY)

    response = client.post(
        f"/api/agent-councils/{S_CONTAINER_ID}/{sCampaignId}/pause")

    assert response.status_code == 409, response.text
    assert "not deliberating" in response.text, response.text
