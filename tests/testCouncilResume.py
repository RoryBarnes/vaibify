"""Falsification tests for explicit resume (continuation plan section 4).

Two lanes. The controller lane plants a REAL mid-walk checkpoint —
produced by driving the actual engine and capturing the version a
crashed hub would have left on disk — into a fresh durable store, and
resumes it with fake provider connections that RECORD what they are
asked to run: the proof is that settled phases are never re-run and
the walk continues to its real end. The route lane drives the original
live failure end-to-end: a hub restart under a needsHuman gate,
answered through a SECOND app over the same durable store (the E-gap:
no test drove a hub restart across a live campaign before this file).

Every refusal asserts the text that names ITS cause — the repository's
two-guards-one-outcome lesson — and each carries its resuming twin.
"""

import asyncio
import copy
import hashlib
import io
import os
import tarfile

import pytest

from tests.agentCouncilHarness import (
    VersionRecordingCheckpoint,
    fdictDecideCompleted,
    fdictMakeTurnResult,
    ffnDecideAllAccept,
    fixtureBuildCouncil,
)
from tests.testCouncilRoutes import (  # noqa: F401  (fixture re-export)
    DICT_START_BODY,
    MockDockerCouncil,
    S_CONTAINER_ID,
    S_CONTAINER_NAME,
    S_IMAGE_IDENTITY,
    _fnBuildAppWithTmpStore,
    _fnWaitForCampaignState,
    _sStartOneCampaign,
    _tEstablishOwnership,
    eventTurnGate,
)
from vaibify.gui import agentCouncilController
from vaibify.gui import agentCouncilRegistry
from vaibify.gui import agentCouncilStore

LIST_TWO_SPECS = [
    {"sHandle": "alpha", "sProvider": "fake", "sRequestedModel": "model-a"},
    {"sHandle": "beta", "sProvider": "fake", "sRequestedModel": "model-b"},
]


class _RecordingAcceptConnection:
    """A provider fake that records the phases it is asked to run."""

    def __init__(self, listPhaseLog):
        self.listPhaseLog = listPhaseLog

    async def fdictPrepareImmutableContext(self, dictTurnRequest):
        return {"sContextIdentity": "resume-context"}

    async def fnStartTurn(self, dictTurnRequest):
        self.listPhaseLog.append(dictTurnRequest["sPhase"])

    async def fiterStreamNormalizedEvents(self):
        return
        yield  # pragma: no cover

    async def fdictCollectStructuredResult(self):
        return fdictMakeTurnResult(sVerdict="accept")

    async def fsReportCompletion(self):
        return "terminal"


def _fdictCaptureMidWalkVersion():
    """Drive a real council; return the checkpoint a crash would leave.

    The chosen version is the LAST one whose attempt settled
    ``advancedToNextPhase`` with cross-review complete and synthesis
    not yet begun — state planning, walk mid-round: exactly what a hub
    killed between phases checkpoints.
    """
    checkpointRecorder = VersionRecordingCheckpoint()
    fixtureCouncil = fixtureBuildCouncil(
        LIST_TWO_SPECS, ffnDecideAllAccept, sChairbotHandle="alpha",
        checkpoint=checkpointRecorder)
    fixtureCouncil.fdictDrive()
    listCandidates = [
        dictVersion for dictVersion in checkpointRecorder.listVersions
        if dictVersion["sState"] == "planning"
        and dictVersion.get("listRounds")
        and (dictVersion["listRounds"][-1].get("dictPhaseAttempt") or {}
             ).get("sOutcome") == "advancedToNextPhase"
        and (dictVersion["listRounds"][-1].get("dictPhaseAttempt") or {}
             ).get("sPhase") == "crossReview"
        and "synthesis" not in dictVersion["listRounds"][-1][
            "dictTurnsByPhase"]]
    assert listCandidates, "no mid-walk boundary checkpoint was captured"
    return copy.deepcopy(listCandidates[-1])


def _tPlantCrashedCampaign(tmp_path, dictVersion,
                           sImageIdentity=S_IMAGE_IDENTITY):
    """Write a crashed campaign into a fresh durable store, as disk has it.

    Returns (dictStore, dictRegistry, dictControllerState, sCampaignId).
    The sealed archive is written for real and its BYTE digest pinned
    into the identity, exactly as launch pins it.
    """
    sCampaignId = dictVersion["sCampaignId"]
    sRoot = str(tmp_path / "councils")
    sSnapshotDirectory = os.path.join(sRoot, sCampaignId, "snapshot")
    os.makedirs(sSnapshotDirectory, exist_ok=True)
    with tarfile.open(os.path.join(
            sSnapshotDirectory, "snapshot.tar"), "w") as fileTar:
        baProject = b'{"name": "resume-test-fixture"}'
        infoProject = tarfile.TarInfo(name="project.json")
        infoProject.size = len(baProject)
        fileTar.addfile(infoProject, io.BytesIO(baProject))
    with open(os.path.join(sSnapshotDirectory, "snapshot.tar"),
              "rb") as fileTar:
        sArchiveDigest = hashlib.sha256(fileTar.read()).hexdigest()
    dictVersion["dictProjectIdentity"] = {
        "sResourceName": S_CONTAINER_NAME,
        "sProjectRepoPath": "/workspace/project-repo",
        "sSnapshotIdentity": "sealed-content-identity-0001",
        "sSnapshotScopeNote": "",
        "sImageIdentity": sImageIdentity,
        "sSnapshotArchiveSha256": sArchiveDigest,
    }
    dictStore = agentCouncilStore.fdictCreateCampaignStore(
        sDurableStoreRoot=sRoot)
    agentCouncilStore.fdictRegisterStartedCampaign(dictStore, dictVersion)
    return (dictStore, agentCouncilRegistry.fdictCreateCouncilRegistry(),
            agentCouncilController.fdictCreateCouncilControllerState(),
            sCampaignId)


def _fdictResumeToCompletion(dictStore, dictRegistry, dictControllerState,
                             sCampaignId, monkeypatch, listPhaseLog,
                             bClearStopRequest=False):
    """Run resume and await the continued drive task to settlement."""
    monkeypatch.setattr(
        agentCouncilController, "fconnectionBuildParticipantConnection",
        lambda dictRuntime, dictParticipant:
            _RecordingAcceptConnection(listPhaseLog))

    async def _fdictRun():
        dictResumed = (
            await agentCouncilController.fdictResumeCampaignDeliberation(
                dictControllerState, dictStore, dictRegistry, sCampaignId,
                S_IMAGE_IDENTITY, bClearStopRequest=bClearStopRequest))
        dictRuntime = dictControllerState["dictCampaignRuntime"].get(
            sCampaignId)
        if dictRuntime is not None and dictRuntime.get(
                "taskDrive") is not None:
            await dictRuntime["taskDrive"]
        return dictResumed

    return asyncio.run(_fdictRun())


@pytest.mark.falsification
def testResumeContinuesTheWalkWithoutRerunningSettledPhases(
        tmp_path, monkeypatch):
    """The researcher's ruling made executable: continue, never re-run.

    A real mid-walk checkpoint (cross-review settled, synthesis not
    begun) resumes into a drive that runs ONLY synthesis and veto —
    the recorded connections prove no settled phase was re-run and no
    provider work was silently re-spent — and the campaign reaches the
    same planReady a crash-free run reaches.

    Kills: resume dropping the settled boundary and walking the round
    from its first phase again.
    """
    dictStore, dictRegistry, dictControllerState, sCampaignId = (
        _tPlantCrashedCampaign(tmp_path, _fdictCaptureMidWalkVersion()))
    listPhaseLog = []

    dictResumed = _fdictResumeToCompletion(
        dictStore, dictRegistry, dictControllerState, sCampaignId,
        monkeypatch, listPhaseLog)

    assert dictResumed["bResumed"] is True
    dictRecord = agentCouncilStore.fjsonGetCampaignRecord(
        dictStore, sCampaignId)
    assert dictRecord["sState"] == "planReady"
    assert set(listPhaseLog) == {"synthesis", "veto"}, listPhaseLog


@pytest.mark.falsification
def testResumeRefusesARunningAttemptAndNamesReconcile(
        tmp_path, monkeypatch):
    """running is permanently unresumable: launched runners, no proof.

    Kills: the resume route admitting an attempt whose turns never all
    settled.
    """
    dictVersion = _fdictCaptureMidWalkVersion()
    dictVersion["listRounds"][-1]["dictPhaseAttempt"]["sAttemptState"] = (
        "running")
    dictVersion["listRounds"][-1]["dictPhaseAttempt"]["sOutcome"] = ""
    dictStore, dictRegistry, dictControllerState, sCampaignId = (
        _tPlantCrashedCampaign(tmp_path, dictVersion))

    with pytest.raises(agentCouncilController.CouncilCommandError,
                       match="vaibify reconcile"):
        _fdictResumeToCompletion(
            dictStore, dictRegistry, dictControllerState, sCampaignId,
            monkeypatch, [])


@pytest.mark.falsification
def testResumeRefusesAPreAttemptRecord(tmp_path, monkeypatch):
    """No attempt record is never assumed settled.

    Kills: treating a pre-feature checkpoint as a resumable boundary.
    """
    dictVersion = _fdictCaptureMidWalkVersion()
    dictVersion["listRounds"][-1].pop("dictPhaseAttempt", None)
    dictStore, dictRegistry, dictControllerState, sCampaignId = (
        _tPlantCrashedCampaign(tmp_path, dictVersion))

    with pytest.raises(agentCouncilController.CouncilCommandError,
                       match="earlier hub version"):
        _fdictResumeToCompletion(
            dictStore, dictRegistry, dictControllerState, sCampaignId,
            monkeypatch, [])


@pytest.mark.falsification
def testResumeRefusesAChangedImageNamingBothIdentities(
        tmp_path, monkeypatch):
    """Ruling 3: a resume that would change the execution image refuses.

    No override flag, no recorded-decision escape hatch; the refusal
    names BOTH identities so the researcher can see what moved.

    Kills: resume comparing nothing and relaunching in whatever image
    the container now runs.
    """
    dictStore, dictRegistry, dictControllerState, sCampaignId = (
        _tPlantCrashedCampaign(
            tmp_path, _fdictCaptureMidWalkVersion(),
            sImageIdentity="sha256:" + "0dd0" * 16))

    with pytest.raises(agentCouncilController.CouncilCommandError) as error:
        _fdictResumeToCompletion(
            dictStore, dictRegistry, dictControllerState, sCampaignId,
            monkeypatch, [])
    assert "image changed" in str(error.value)
    assert "0dd0" in str(error.value)
    assert S_IMAGE_IDENTITY in str(error.value)


@pytest.mark.falsification
def testResumeRefusesACorruptSealedArchive(tmp_path, monkeypatch):
    """The archive is validated by BYTES, not by the manifest identity.

    sSnapshotSha256 is a content identity over sorted manifest rows —
    hashing the tar and comparing to IT validates nothing, which is
    why the byte digest is pinned separately at launch.

    Kills: skipping the byte-digest comparison.
    """
    dictStore, dictRegistry, dictControllerState, sCampaignId = (
        _tPlantCrashedCampaign(tmp_path, _fdictCaptureMidWalkVersion()))
    sArchivePath = os.path.join(
        dictStore["sDurableStoreRoot"], sCampaignId, "snapshot",
        "snapshot.tar")
    with open(sArchivePath, "ab") as fileTar:
        fileTar.write(b"corruption")

    with pytest.raises(agentCouncilController.CouncilCommandError,
                       match="does not match the digest"):
        _fdictResumeToCompletion(
            dictStore, dictRegistry, dictControllerState, sCampaignId,
            monkeypatch, [])


@pytest.mark.falsification
def testResumeRefusesOverAnUnsettledReservation(tmp_path, monkeypatch):
    """Any unsettled reservation refuses — not only quarantined ones.

    Kills: narrowing the reservation check to the quarantined status.
    """
    dictStore, dictRegistry, dictControllerState, sCampaignId = (
        _tPlantCrashedCampaign(tmp_path, _fdictCaptureMidWalkVersion()))
    dictRegistry["dictReservationsById"]["reservation-1"] = {
        "sCampaignId": sCampaignId,
        "sStatus": agentCouncilRegistry.S_RESERVATION_PENDING,
    }

    with pytest.raises(agentCouncilController.CouncilCommandError,
                       match="unsettled runner reservations"):
        _fdictResumeToCompletion(
            dictStore, dictRegistry, dictControllerState, sCampaignId,
            monkeypatch, [])


@pytest.mark.falsification
def testResumeRefusesAPeerHeldCampaign(tmp_path, monkeypatch):
    """Another live hub's campaign is not this hub's to drive.

    The predicate itself carries its own live-flock falsification in
    testCouncilPeerHubIsolation; what this pins is that resume
    CONSULTS it.

    Kills: dropping the peer check from the unsettled-work refusal.
    """
    dictStore, dictRegistry, dictControllerState, sCampaignId = (
        _tPlantCrashedCampaign(tmp_path, _fdictCaptureMidWalkVersion()))
    monkeypatch.setattr(
        agentCouncilRegistry, "fbCampaignBelongsToALivePeerHub",
        lambda dictCampaign: True)

    with pytest.raises(agentCouncilController.CouncilCommandError,
                       match="another live hub"):
        _fdictResumeToCompletion(
            dictStore, dictRegistry, dictControllerState, sCampaignId,
            monkeypatch, [])


@pytest.mark.falsification
def testAStandingStopRequestSurfacesTheChoice(tmp_path, monkeypatch):
    """A kept flag would archive the resumed campaign instantly.

    The engine loop archives on a set bStopRequested before running
    anything, so resuming a record that kept a pre-crash stop request
    silently destroys the campaign the researcher just asked to
    continue. The choice is surfaced: refuse without the explicit
    clear; with it, the clear lands as a RECORDED researcher decision
    and the walk continues.

    Kills: resume silently clearing (or silently keeping) the flag.
    """
    dictVersion = _fdictCaptureMidWalkVersion()
    dictVersion["bStopRequested"] = True
    dictStore, dictRegistry, dictControllerState, sCampaignId = (
        _tPlantCrashedCampaign(tmp_path, dictVersion))

    with pytest.raises(agentCouncilController.CouncilCommandError,
                       match="stop was requested"):
        _fdictResumeToCompletion(
            dictStore, dictRegistry, dictControllerState, sCampaignId,
            monkeypatch, [])

    listPhaseLog = []
    dictResumed = _fdictResumeToCompletion(
        dictStore, dictRegistry, dictControllerState, sCampaignId,
        monkeypatch, listPhaseLog, bClearStopRequest=True)
    assert dictResumed["bResumed"] is True
    dictRecord = agentCouncilStore.fjsonGetCampaignRecord(
        dictStore, sCampaignId)
    assert dictRecord["sState"] == "planReady"
    assert dictRecord["bStopRequested"] is False
    assert any(
        dictDecision.get("sDecisionKind") == "stopRequestClearedOnResume"
        for dictDecision in dictRecord.get("listResearcherDecisions") or [])


@pytest.mark.falsification
def testAFailedRebuildLeavesTheRecordByteIdentical(tmp_path, monkeypatch):
    """4.3: a resume succeeds and transitions, or fails and touches nothing.

    The launch path is transactional the other way (build fault ->
    failed, checkpointed). Reusing that handler here would destroy the
    exact record — a 13-question gate among them — this feature exists
    to rescue.

    Kills: the rebuild failure path transitioning or checkpointing the
    record.
    """
    dictStore, dictRegistry, dictControllerState, sCampaignId = (
        _tPlantCrashedCampaign(tmp_path, _fdictCaptureMidWalkVersion()))
    sRecordPath = os.path.join(
        dictStore["sDurableStoreRoot"], sCampaignId, "campaign.json")
    with open(sRecordPath, "rb") as fileRecord:
        baBefore = fileRecord.read()

    def _fnExplodingFactory(dictRuntime, dictParticipant):
        raise RuntimeError("scripted rebuild failure")

    monkeypatch.setattr(
        agentCouncilController, "fconnectionBuildParticipantConnection",
        _fnExplodingFactory)

    async def _fdictRun():
        return await agentCouncilController.fdictResumeCampaignDeliberation(
            dictControllerState, dictStore, dictRegistry, sCampaignId,
            S_IMAGE_IDENTITY)

    with pytest.raises(RuntimeError, match="scripted rebuild failure"):
        asyncio.run(_fdictRun())

    with open(sRecordPath, "rb") as fileRecord:
        baAfter = fileRecord.read()
    assert baAfter == baBefore


class _NeedsHumanOnSynthesisConnection:
    """Accept everywhere; raise a blocking question at synthesis.

    The route harness's gated fake always accepts, and an all-accept
    council never opens a gate — so the restart-under-a-gate journey
    scripts its own provider: the synthesis author asks one question,
    which is the smallest real path to needsHuman.
    """

    async def fdictPrepareImmutableContext(self, dictTurnRequest):
        return {"sContextIdentity": "restart-context"}

    async def fnStartTurn(self, dictTurnRequest):
        self._sPhase = dictTurnRequest["sPhase"]

    async def fiterStreamNormalizedEvents(self):
        return
        yield  # pragma: no cover

    async def fdictCollectStructuredResult(self):
        if self._sPhase == "synthesis":
            return fdictMakeTurnResult(
                sVerdict="needsHuman",
                listOpenQuestions=["Which cache policy should hold?"])
        return fdictMakeTurnResult(sVerdict="accept")

    async def fsReportCompletion(self):
        return "terminal"


@pytest.mark.falsification
def testAGateAnswerSurvivesTheHubRestart(tmp_path, eventTurnGate,
                                         monkeypatch):
    """The live failure that motivated all of this, driven end-to-end.

    App A drives a real campaign to a needsHuman gate and dies (its
    process state is discarded). App B — a second hub over the SAME
    durable store — reloads the campaign, shows the gate, and the
    researcher's answer REBUILDS the runtime and continues the
    deliberation to planReady. Before this worked, the panel showed
    the gate and an answer box, then refused the answer.

    Kills: the respond path refusing whenever the in-process runtime
    died with the hub.
    """
    from fastapi.testclient import TestClient
    from vaibify.gui import agentCouncilCampaign

    monkeypatch.setattr(
        agentCouncilController, "fconnectionBuildParticipantConnection",
        lambda dictRuntime, dictParticipant:
            _NeedsHumanOnSynthesisConnection())
    appFirst = _fnBuildAppWithTmpStore(tmp_path)
    sCredential, sLease = _tEstablishOwnership(
        appFirst, S_CONTAINER_NAME, S_CONTAINER_ID)
    dictNeedsHumanBody = dict(DICT_START_BODY)
    dictNeedsHumanBody["dictSettings"] = {"iMinimumRounds": 1}
    with TestClient(appFirst, headers={
        "X-Session-Token": sCredential, "X-Vaibify-Lease": sLease,
    }) as clientFirst:
        response = clientFirst.post(
            f"/api/agent-councils/{S_CONTAINER_ID}/start",
            json=dictNeedsHumanBody)
        assert response.status_code == 200, response.text
        sCampaignId = response.json()["sCampaignId"]
        _fnWaitForCampaignState(
            appFirst, sCampaignId,
            agentCouncilCampaign.S_STATE_NEEDS_HUMAN)

    # The "restart": a second app, fresh process state, same store root.
    appSecond = _fnBuildAppWithTmpStore(tmp_path)
    agentCouncilStore.fdictReloadDurableCampaigns(
        appSecond.state.dictCouncilCampaignStore)
    sCredential, sLease = _tEstablishOwnership(
        appSecond, S_CONTAINER_NAME, S_CONTAINER_ID)
    with TestClient(appSecond, headers={
        "X-Session-Token": sCredential, "X-Vaibify-Lease": sLease,
    }) as clientSecond:
        dictCampaign = clientSecond.get(
            f"/api/agent-councils/{S_CONTAINER_ID}/{sCampaignId}"
        ).json()["dictCampaign"]
        assert dictCampaign["sState"] == "needsHuman"
        assert dictCampaign["bDeliberationLive"] is False
        listAnswers = [
            {"sDecisionId": dictDecision["sDecisionId"],
             "listQuestionIds": [
                 dictQuestion["sQuestionId"]
                 for dictQuestion in dictDecision.get("listQuestions") or []],
             "sAnswerText": "Use the content-hash policy."}
            for dictDecision in dictCampaign.get("listGateDecisions") or []]
        response = clientSecond.post(
            f"/api/agent-councils/{S_CONTAINER_ID}/{sCampaignId}/respond",
            json={"sResponseText": "Use the content-hash policy.",
                  "listDecisionAnswers": listAnswers})
        assert response.status_code == 200, response.text
        _fnWaitForCampaignState(
            appSecond, sCampaignId,
            agentCouncilCampaign.S_STATE_PLAN_READY)
