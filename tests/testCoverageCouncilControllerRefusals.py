"""Refusals, settlements and plan composition of the council controller.

Every campaign here is a real record from ``fdictCreateCampaign`` held in
a real durable store under ``tmp_path``; the registry is the real one.
The Docker daemon is the only thing faked, and only where the controller
reaches it (the quarantine re-proof), at the gateway's client and
docker-module seams. Each refusal asserts the words that name ITS cause,
because two refusals that read alike are what sent researchers to the
wrong remedy before.
"""

import asyncio
import os
import types

import pytest

from vaibify.gui import agentCouncilCampaign
from vaibify.gui import agentCouncilController as controller
from vaibify.gui import agentCouncilDockerGateway
from vaibify.gui import agentCouncilRegistry
from vaibify.gui import agentCouncilRunner
from vaibify.gui import agentCouncilStore


S_QUESTION = "How should stepAlpha read dataFile.csv?"
S_RESOURCE_NAME = "projectContainerAlpha"
S_OTHER_RESOURCE_NAME = "projectContainerBeta"


def fdictBuildCampaign(sState=agentCouncilCampaign.S_STATE_PLANNING,
                       sResourceName=""):
    """A real two-model campaign record in the requested state."""
    dictCampaign = agentCouncilCampaign.fdictCreateCampaign(
        S_QUESTION,
        [agentCouncilCampaign.fdictCreateParticipant("claude", "modelAlpha"),
         agentCouncilCampaign.fdictCreateParticipant("codex", "modelBeta")],
        dictProjectIdentity={"sResourceName": sResourceName})
    if sState != agentCouncilCampaign.S_STATE_DRAFT:
        agentCouncilCampaign.fnTransitionCampaignState(
            dictCampaign, sState, "test fixture")
    return dictCampaign


def fdictBuildStore(tmp_path):
    return agentCouncilStore.fdictCreateCampaignStore(
        sDurableStoreRoot=str(tmp_path / "councils"))


def fsRegisterCampaign(dictStore, dictCampaign):
    agentCouncilStore.fdictRegisterStartedCampaign(dictStore, dictCampaign)
    return dictCampaign["sCampaignId"]


def fdictBuildParkedRuntime(dictStore, dictCampaign):
    """A registered runtime with no live drive: a campaign at rest."""
    return {
        "sCampaignId": dictCampaign["sCampaignId"],
        "dictCampaign": dictCampaign,
        "dictStore": dictStore,
        "dictRegistry": agentCouncilRegistry.fdictCreateCouncilRegistry(),
        "taskDrive": None,
        "bLaunchInProgress": False,
        "engineCouncil": None,
        "dictRunnerAccess": None,
        "dictRunnerAccessByProvider": {},
        "sTurnId": "",
    }


def fdictRoundWithAttempt(dictAttempt, bWithTurns=False):
    dictRound = {"iRoundNumber": 1, "sResolution": "",
                 "dictTurnsByPhase": {}, "dictPhaseAttempt": dictAttempt}
    if bWithTurns:
        dictRound["dictTurnsByPhase"] = {
            "independentProposals": [{"sStatus": "completed"}]}
    return dictRound


# ----- the plan document -------------------------------------------------


def testAnUnconvergedCouncilsPlanSaysSoAndCarriesItsSummary():
    """A plan reached by override must say the council never agreed."""
    dictCampaign = fdictBuildCampaign()
    dictCampaign["dictDeliberationSummary"] = {"dictResult": {
        "sSummary": "The council split on the file format.",
        "listPositionsProposed": ["read with a streaming parser"],
        "listPointsOfDisagreement": ["whether headers are optional"],
        "listEvidenceBehindEachPosition": [],
    }}
    sMarkdown = controller.fsComposePlanMarkdown(
        dictCampaign, {"dictResult": {"sSummary": "candidate summary"}})
    assert "## Deliberation summary — the council did NOT converge" in (
        sMarkdown)
    assert "The council split on the file format." in sMarkdown
    assert "### Positions proposed\n- read with a streaming parser" in (
        sMarkdown)
    assert "### Where the council divided\n- whether headers are optional" in (
        sMarkdown)
    assert "What each side had to stand on" not in sMarkdown
    assert "**DRAFT — this candidate was never accepted.**" in sMarkdown


def testAConvergedCouncilsPlanCarriesNoDeliberationSummary():
    dictCampaign = fdictBuildCampaign(
        agentCouncilCampaign.S_STATE_PLAN_ACCEPTED)
    dictCampaign["dictDeliberationSummary"] = {"dictResult": {}}
    sMarkdown = controller.fsComposePlanMarkdown(
        dictCampaign, {"dictResult": {"sSummary": "agreed"}})
    assert "did NOT converge" not in sMarkdown
    assert "DRAFT" not in sMarkdown


def testEveryObjectionProvenanceIsWrittenUnderItsOwnHeading():
    """An override is stated as an override, never folded into a clear."""
    dictCandidate = {
        "dictResult": {"sSummary": "s"},
        "listCouncilClearedObjections": [{"sObjectionText": "clearedOne"}],
        "listResearcherResolvedObjections": [],
        "listResearcherOverriddenObjections": [
            {"sObjectionText": "overriddenOne"}, {}],
    }
    sMarkdown = controller.fsComposePlanMarkdown(
        fdictBuildCampaign(), dictCandidate)
    assert "## Objections cleared in review\n- clearedOne" in sMarkdown
    assert "Objections resolved by the researcher" not in sMarkdown
    assert ("## Objections OVERRIDDEN by the researcher\n"
            "- overriddenOne\n- \n") in sMarkdown


# ----- the rebuild admission pins ----------------------------------------


def testARebuildIsRefusedWhenNoImageIdentityWasPinned():
    dictCampaign = fdictBuildCampaign()
    with pytest.raises(controller.CouncilCommandError,
                       match="recorded no execution image identity"):
        controller._fbaAdmitRuntimeRebuild(
            None, dictCampaign, dictCampaign["sCampaignId"], "imageAlpha",
            "resume")


def testARebuildIsRefusedWhenNoArchiveDigestWasPinned():
    dictCampaign = fdictBuildCampaign()
    dictCampaign["dictProjectIdentity"]["sImageIdentity"] = "imageAlpha"
    with pytest.raises(controller.CouncilCommandError,
                       match="cannot retry: this campaign recorded no "
                             "snapshot archive digest"):
        controller._fbaAdmitRuntimeRebuild(
            None, dictCampaign, dictCampaign["sCampaignId"], "imageAlpha",
            "retry")


# ----- commands against a campaign nobody stored ----------------------------


@pytest.mark.parametrize("sCommand, sExpected", [
    ("resume", "no stored campaign 'campaign-missing' to resume"),
    ("retry", "no stored campaign 'campaign-missing' to retry"),
    ("launch", "no stored campaign 'campaign-missing' to deliberate"),
    ("pause", "no stored campaign 'campaign-missing'"),
    ("stop", "no stored campaign 'campaign-missing'"),
])
def testEveryCommandAgainstAnUnknownCampaignRefusesByName(
        tmp_path, sCommand, sExpected):
    dictStore = fdictBuildStore(tmp_path)
    dictState = controller.fdictCreateCouncilControllerState()
    dictRegistry = agentCouncilRegistry.fdictCreateCouncilRegistry()
    listDrained = []

    async def fdictCapture():
        return {}

    dictCommands = {
        "resume": lambda: controller.fdictResumeCampaignDeliberation(
            dictState, dictStore, dictRegistry, "campaign-missing",
            "imageAlpha"),
        "retry": lambda: controller.fdictRetryCampaignFailedPhase(
            dictState, dictStore, dictRegistry, "campaign-missing",
            "imageAlpha"),
        "launch": lambda: controller.fdictLaunchCampaignDeliberation(
            dictState, dictStore, dictRegistry, "campaign-missing",
            fdictCapture, "imageAlpha"),
        "pause": lambda: controller.fdictRequestCampaignPause(
            dictState, dictStore, "campaign-missing"),
        "stop": lambda: controller.fdictRequestCampaignStop(
            dictState, dictStore, dictRegistry, "campaign-missing",
            lambda: listDrained.append(True)),
    }
    with pytest.raises(controller.CouncilCommandError, match=sExpected):
        asyncio.run(dictCommands[sCommand]())
    assert listDrained == []


# ----- continuations that need a gate -------------------------------------


def testAContinuationWithNoRuntimeAndNoMaterialsNamesTheRestart():
    dictState = controller.fdictCreateCouncilControllerState()
    with pytest.raises(controller.CouncilCommandError,
                       match="the hub restarted since it ran"):
        asyncio.run(controller.fdictGrantCampaignResolutionRound(
            dictState, None, None, "campaign-gone", 1))


def testARebuildForACampaignNobodyStoredRefusesByName(tmp_path):
    dictState = controller.fdictCreateCouncilControllerState()
    with pytest.raises(controller.CouncilCommandError,
                       match="no stored campaign 'campaign-gone' to "
                             "continue"):
        asyncio.run(controller.fdictGrantCampaignResolutionRound(
            dictState, fdictBuildStore(tmp_path), None, "campaign-gone", 1,
            dictRebuildMaterials={"sImageReference": "imageAlpha"}))


def fdictStateWithParkedCampaign(tmp_path, dictCampaign):
    dictStore = fdictBuildStore(tmp_path)
    sCampaignId = fsRegisterCampaign(dictStore, dictCampaign)
    dictState = controller.fdictCreateCouncilControllerState()
    dictState["dictCampaignRuntime"][sCampaignId] = fdictBuildParkedRuntime(
        dictStore, dictCampaign)
    return dictState, dictStore, sCampaignId


def fnGrantRounds(dictState, dictStore, sCampaignId, iRounds):
    asyncio.run(controller.fdictGrantCampaignResolutionRound(
        dictState, dictStore, None, sCampaignId, iRounds))


def testAGrantOnACampaignNotWaitingOnTheResearcherIsRefused(tmp_path):
    dictState, dictStore, sCampaignId = fdictStateWithParkedCampaign(
        tmp_path, fdictBuildCampaign())
    with pytest.raises(controller.CouncilCommandError,
                       match="not waiting on the researcher"):
        fnGrantRounds(dictState, dictStore, sCampaignId, 1)


def fdictCampaignAtGate(sGateKind):
    dictCampaign = fdictBuildCampaign(agentCouncilCampaign.S_STATE_NEEDS_HUMAN)
    dictCampaign["dictPendingHumanGate"] = {"sGateKind": sGateKind}
    return dictCampaign


def testAGrantCannotAnswerADifferentKindOfGate(tmp_path):
    dictState, dictStore, sCampaignId = fdictStateWithParkedCampaign(
        tmp_path, fdictCampaignAtGate(
            agentCouncilCampaign.S_GATE_BLOCKING_QUESTION))
    with pytest.raises(controller.CouncilCommandError,
                       match="answers a exhaustedRounds gate, not "
                             "blockingQuestion"):
        fnGrantRounds(dictState, dictStore, sCampaignId, 1)


def testAGrantOfFewerThanOneRoundIsRefusedAndSpawnsNothing(tmp_path):
    dictState, dictStore, sCampaignId = fdictStateWithParkedCampaign(
        tmp_path, fdictCampaignAtGate(
            agentCouncilCampaign.S_GATE_EXHAUSTED_ROUNDS))
    with pytest.raises(controller.CouncilCommandError,
                       match="at least one round"):
        fnGrantRounds(dictState, dictStore, sCampaignId, 0)
    assert dictState["dictCampaignRuntime"][sCampaignId]["taskDrive"] is None


def testAContinuationForAReleasedProjectIsRefused(tmp_path):
    dictState, dictStore, sCampaignId = fdictStateWithParkedCampaign(
        tmp_path, fdictCampaignAtGate(
            agentCouncilCampaign.S_GATE_EXHAUSTED_ROUNDS))
    dictState["dictCampaignRuntime"][sCampaignId]["dictCampaign"][
        "dictProjectIdentity"]["sResourceName"] = S_RESOURCE_NAME
    controller.fbCloseResourceAdmission(dictState, S_RESOURCE_NAME)
    with pytest.raises(controller.CouncilCommandError,
                       match="the project lease was released"):
        fnGrantRounds(dictState, dictStore, sCampaignId, 1)


# ----- resume and retry admission -----------------------------------------


def fnResume(dictState, dictStore, sCampaignId, dictRegistry=None):
    asyncio.run(controller.fdictResumeCampaignDeliberation(
        dictState, dictStore,
        dictRegistry or agentCouncilRegistry.fdictCreateCouncilRegistry(),
        sCampaignId, "imageAlpha"))


def fnRetry(dictState, dictStore, sCampaignId):
    asyncio.run(controller.fdictRetryCampaignFailedPhase(
        dictState, dictStore,
        agentCouncilRegistry.fdictCreateCouncilRegistry(),
        sCampaignId, "imageAlpha"))


def testOnlyAPlanningCampaignCanBeResumed(tmp_path):
    dictStore = fdictBuildStore(tmp_path)
    sCampaignId = fsRegisterCampaign(
        dictStore, fdictBuildCampaign(agentCouncilCampaign.S_STATE_FAILED))
    with pytest.raises(controller.CouncilCommandError,
                       match="only a campaign stopped mid-deliberation"):
        fnResume(controller.fdictCreateCouncilControllerState(), dictStore,
                 sCampaignId)


def testAResumeFromANonWalkingOutcomeIsRefusedNamingTheOutcome(tmp_path):
    dictCampaign = fdictBuildCampaign()
    dictCampaign["listRounds"] = [fdictRoundWithAttempt({
        "sAttemptState": "outcomeSettled", "sOutcome": "quorumShortfall"})]
    dictStore = fdictBuildStore(tmp_path)
    sCampaignId = fsRegisterCampaign(dictStore, dictCampaign)
    with pytest.raises(controller.CouncilCommandError,
                       match=r"\(quorumShortfall\) is not a resumable "
                             "boundary"):
        fnResume(controller.fdictCreateCouncilControllerState(), dictStore,
                 sCampaignId)


def fdictReloadWithLostProvenance(tmp_path, dictCampaign):
    """Register a campaign that ran, drop its sidecar, reload the store."""
    dictStore = fdictBuildStore(tmp_path)
    sCampaignId = fsRegisterCampaign(dictStore, dictCampaign)
    sSidecar = os.path.join(
        str(tmp_path / "councils"), sCampaignId,
        agentCouncilStore.S_PROVENANCE_SIDECAR_BASENAME)
    if os.path.exists(sSidecar):
        os.remove(sSidecar)
    dictReloaded = fdictBuildStore(tmp_path)
    agentCouncilStore.fdictReloadDurableCampaigns(dictReloaded)
    assert agentCouncilStore.fbCampaignProvenanceUnavailable(
        dictReloaded, sCampaignId)
    return dictReloaded, sCampaignId


def testAResumeOverLostProvenanceIsRefusedBeforeAnyRebuild(tmp_path):
    dictCampaign = fdictBuildCampaign()
    dictCampaign["listRounds"] = [fdictRoundWithAttempt(
        {"sAttemptState": "turnsSettled"}, bWithTurns=True)]
    dictStore, sCampaignId = fdictReloadWithLostProvenance(
        tmp_path, dictCampaign)
    dictState = controller.fdictCreateCouncilControllerState()
    with pytest.raises(controller.CouncilCommandError,
                       match="cannot resume: this campaign's provenance "
                             "sidecar is missing"):
        fnResume(dictState, dictStore, sCampaignId)
    assert dictState["dictCampaignRuntime"] == {}
    assert agentCouncilStore.fjsonGetCampaignRecord(
        dictStore, sCampaignId)["sState"] == (
            agentCouncilCampaign.S_STATE_PLANNING)


def testARetryOverLostProvenanceIsRefused(tmp_path):
    dictCampaign = fdictBuildCampaign(agentCouncilCampaign.S_STATE_FAILED)
    dictCampaign["listRounds"] = [fdictRoundWithAttempt(
        {"sAttemptState": "outcomeSettled",
         "sOutcome": "transitioned:failed"}, bWithTurns=True)]
    dictStore, sCampaignId = fdictReloadWithLostProvenance(
        tmp_path, dictCampaign)
    with pytest.raises(controller.CouncilCommandError,
                       match="cannot retry: this campaign's provenance "
                             "sidecar"):
        fnRetry(controller.fdictCreateCouncilControllerState(), dictStore,
                sCampaignId)


def testARuntimeStillBeingBuiltRefusesADiscard():
    """Defense in depth: the build window is never mistaken for spent."""
    dictState = controller.fdictCreateCouncilControllerState()
    dictState["dictCampaignRuntime"]["campaignAlpha"] = {
        "bLaunchInProgress": True}
    with pytest.raises(controller.CouncilCommandError,
                       match="runtime is still being built"):
        asyncio.run(controller._fnDiscardSettledRuntimeOrRefuse(
            dictState, "campaignAlpha", "retry"))
    assert "campaignAlpha" in dictState["dictCampaignRuntime"]


# ----- the quarantine re-proof --------------------------------------------


class _FakeNotFoundError(Exception):
    pass


class _ProbeAnsweringApi:
    """Answers each container id with a scripted daemon reply."""

    def __init__(self, dictAnswers):
        self.dictAnswers = dictAnswers
        self.listInspected = []

    def inspect_container(self, sContainerId):
        self.listInspected.append(sContainerId)
        jsonAnswer = self.dictAnswers[sContainerId]
        if isinstance(jsonAnswer, Exception):
            raise jsonAnswer
        return jsonAnswer


def fdictRegistryWithReservations(listReservations):
    """A registry whose reservations were created then settled as given."""
    dictRegistry = agentCouncilRegistry.fdictCreateCouncilRegistry({
        "iMaxConcurrentRunners": 10, "iPerProviderMaxConcurrent": 10,
        "iPerCampaignMaxConcurrentRunners": 10})
    for sCampaignId, sReservationId, sContainerId, bQuarantine in (
            listReservations):
        assert agentCouncilRegistry.fdictReserveRunner(
            dictRegistry, sCampaignId, sReservationId, "claude",
            {"iMemoryBytes": 1, "fCpuCount": 0.1})["bReserved"] is True
        agentCouncilRegistry.fnMarkRunnerCreated(
            dictRegistry, sReservationId, sContainerId)
        if bQuarantine:
            agentCouncilRegistry.fdictSettleReservation(
                dictRegistry, sReservationId,
                agentCouncilRunner.S_OUTCOME_QUARANTINED)
    return dictRegistry


def fnWireProbeDaemon(monkeypatch, dictAnswers):
    apiFake = _ProbeAnsweringApi(dictAnswers)
    monkeypatch.setattr(
        agentCouncilDockerGateway, "fdockerCreateCouncilClient",
        lambda: types.SimpleNamespace(api=apiFake))
    monkeypatch.setattr(
        agentCouncilDockerGateway, "_fmoduleGetDocker",
        lambda: types.SimpleNamespace(errors=types.SimpleNamespace(
            NotFound=_FakeNotFoundError)))
    return apiFake


def fsStatusOf(dictRegistry, sReservationId):
    dictReservation = dictRegistry["dictReservationsById"].get(sReservationId)
    return dictReservation["sStatus"] if dictReservation else "settled"


def testOnlyAQuarantineTheDaemonPositivelyForgotIsSettled(monkeypatch):
    """Present and indeterminate answers leave the quarantine standing."""
    dictRegistry = fdictRegistryWithReservations([
        ("campaignAlpha", "resGone", "ctrGone", True),
        ("campaignAlpha", "resPresent", "ctrPresent", True),
        ("campaignAlpha", "resUnknown", "ctrUnknown", True),
        ("campaignAlpha", "resLive", "ctrLive", False),
        ("campaignBeta", "resOther", "ctrOther", True),
    ])
    apiFake = fnWireProbeDaemon(monkeypatch, {
        "ctrGone": _FakeNotFoundError("gone"),
        "ctrPresent": {"Config": {"Labels": {}}},
        "ctrUnknown": RuntimeError("daemon timed out"),
    })
    controller._fnReproveQuarantinedReservationsBlocking(
        dictRegistry, "campaignAlpha")
    assert sorted(apiFake.listInspected) == [
        "ctrGone", "ctrPresent", "ctrUnknown"]
    assert fsStatusOf(dictRegistry, "resGone") == "settled"
    assert fsStatusOf(dictRegistry, "resPresent") == "quarantined"
    assert fsStatusOf(dictRegistry, "resUnknown") == "quarantined"
    assert fsStatusOf(dictRegistry, "resLive") == "live"
    assert fsStatusOf(dictRegistry, "resOther") == "quarantined"


def testAnUnreachableDaemonLeavesEveryQuarantineStanding(monkeypatch):
    dictRegistry = fdictRegistryWithReservations([
        ("campaignAlpha", "resGone", "ctrGone", True)])

    def fdockerRefuse():
        raise ConnectionError("no daemon socket")

    monkeypatch.setattr(
        agentCouncilDockerGateway, "fdockerCreateCouncilClient",
        fdockerRefuse)
    controller._fnReproveQuarantinedReservationsBlocking(
        dictRegistry, "campaignAlpha")
    assert fsStatusOf(dictRegistry, "resGone") == "quarantined"


def testAProbeThatCannotEvenLoadTheSdkSettlesNothing(monkeypatch):
    dictRegistry = fdictRegistryWithReservations([
        ("campaignAlpha", "resGone", "ctrGone", True)])
    fnWireProbeDaemon(monkeypatch, {"ctrGone": _FakeNotFoundError("gone")})

    def fmoduleRefuse():
        raise ImportError("docker SDK unavailable")

    monkeypatch.setattr(
        agentCouncilDockerGateway, "_fmoduleGetDocker", fmoduleRefuse)
    controller._fnReproveQuarantinedReservationsBlocking(
        dictRegistry, "campaignAlpha")
    assert fsStatusOf(dictRegistry, "resGone") == "quarantined"


def testAResumeReprovesAStaleQuarantineThenStillRefusesALiveRunner(
        monkeypatch, tmp_path):
    """The stale quarantine clears; the live reservation still refuses."""
    dictCampaign = fdictBuildCampaign()
    dictStore = fdictBuildStore(tmp_path)
    sCampaignId = fsRegisterCampaign(dictStore, dictCampaign)
    dictRegistry = fdictRegistryWithReservations([
        (sCampaignId, "resStale", "ctrStale", True),
        (sCampaignId, "resLive", "ctrLive", False)])
    fnWireProbeDaemon(monkeypatch, {"ctrStale": _FakeNotFoundError("gone")})
    with pytest.raises(controller.CouncilCommandError,
                       match="unsettled runner reservations"):
        fnResume(controller.fdictCreateCouncilControllerState(), dictStore,
                 sCampaignId, dictRegistry=dictRegistry)
    assert fsStatusOf(dictRegistry, "resStale") == "settled"
    assert fsStatusOf(dictRegistry, "resLive") == "live"


# ----- stop and pause against a live, engine-less drive ---------------------


async def ftaskStartIdleDrive(eventRelease):
    async def fnIdle():
        await eventRelease.wait()
    return asyncio.create_task(fnIdle())


def testAPauseBeforeTheEngineExistsSaysTheCouncilIsStillStarting(tmp_path):
    dictState, dictStore, sCampaignId = fdictStateWithParkedCampaign(
        tmp_path, fdictBuildCampaign())

    async def fnPauseDuringStartup():
        eventRelease = asyncio.Event()
        dictRuntime = dictState["dictCampaignRuntime"][sCampaignId]
        dictRuntime["taskDrive"] = await ftaskStartIdleDrive(eventRelease)
        try:
            await controller.fdictRequestCampaignPause(
                dictState, dictStore, sCampaignId)
        finally:
            eventRelease.set()
            await dictRuntime["taskDrive"]

    with pytest.raises(controller.CouncilCommandError,
                       match="still starting up"):
        asyncio.run(fnPauseDuringStartup())


def testAStopAgainstAnEngineLessDriveCancelsItAndSettlesInterrupted(
        tmp_path):
    dictState, dictStore, sCampaignId = fdictStateWithParkedCampaign(
        tmp_path, fdictBuildCampaign())
    dictRuntime = dictState["dictCampaignRuntime"][sCampaignId]
    dictRegistry = dictRuntime["dictRegistry"]
    agentCouncilRegistry.fbRegisterTurnInFlight(
        dictRegistry, sCampaignId, "turn-7")
    dictRuntime["sTurnId"] = "turn-7"
    listDrained = []

    async def fdictStopLiveDrive():
        eventRelease = asyncio.Event()
        dictRuntime["taskDrive"] = await ftaskStartIdleDrive(eventRelease)
        dictStopped = await controller.fdictRequestCampaignStop(
            dictState, dictStore, dictRegistry, sCampaignId,
            lambda: listDrained.append(True))
        await asyncio.sleep(0)
        return dictStopped, dictRuntime["taskDrive"].cancelled()

    dictStopped, bCancelled = asyncio.run(fdictStopLiveDrive())
    assert bCancelled is True
    assert listDrained == [True]
    assert dictRegistry["setTurnsInFlight"] == set()
    assert dictStopped["bSettled"] is True
    assert dictStopped["dictCampaign"]["sState"] == (
        agentCouncilCampaign.S_STATE_INTERRUPTED)
    assert dictStopped["dictCampaign"]["bStopRequested"] is True


# ----- the release drain --------------------------------------------------


def testTheReleaseDrainStopsOnlyThisResourcesLiveDriveAndReportsIt(
        tmp_path):
    """Another project's runtime is untouched; a live drive is unsettled."""
    dictStore = fdictBuildStore(tmp_path)
    dictOurs = fdictBuildCampaign(sResourceName=S_RESOURCE_NAME)
    dictTheirs = fdictBuildCampaign(sResourceName=S_OTHER_RESOURCE_NAME)
    for dictCampaign in (dictOurs, dictTheirs):
        fsRegisterCampaign(dictStore, dictCampaign)
    dictState = controller.fdictCreateCouncilControllerState()
    for dictCampaign in (dictOurs, dictTheirs):
        dictState["dictCampaignRuntime"][dictCampaign["sCampaignId"]] = (
            fdictBuildParkedRuntime(dictStore, dictCampaign))

    async def fdictDrainWithLiveDrive():
        eventRelease = asyncio.Event()
        dictRuntime = dictState["dictCampaignRuntime"][dictOurs["sCampaignId"]]
        dictRuntime["taskDrive"] = await ftaskStartIdleDrive(eventRelease)
        dictDrained = await controller.fdictDrainControllerForResource(
            dictState, S_RESOURCE_NAME)
        await asyncio.sleep(0)
        return dictDrained, dictRuntime["taskDrive"].cancelled()

    dictDrained, bCancelled = asyncio.run(fdictDrainWithLiveDrive())
    assert dictDrained == {"bAllSettled": False,
                           "listUnsettledCampaignIds": [
                               dictOurs["sCampaignId"]]}
    assert bCancelled is True
    assert dictTheirs["sState"] == agentCouncilCampaign.S_STATE_PLANNING
    assert dictTheirs["sCampaignId"] in dictState["dictCampaignRuntime"]


# ----- startup classification ---------------------------------------------


def testStartupClassifiesOnlyCampaignsWithNoProvenBoundary(tmp_path):
    dictStore = fdictBuildStore(tmp_path)
    dictNoRounds = fdictBuildCampaign()
    dictNoAttempt = fdictBuildCampaign()
    dictNoAttempt["listRounds"] = [fdictRoundWithAttempt(None)]
    dictSettled = fdictBuildCampaign()
    dictSettled["listRounds"] = [fdictRoundWithAttempt(
        {"sAttemptState": "turnsSettled"})]
    dictFailed = fdictBuildCampaign(agentCouncilCampaign.S_STATE_FAILED)
    for dictCampaign in (dictNoRounds, dictNoAttempt, dictSettled,
                         dictFailed):
        fsRegisterCampaign(dictStore, dictCampaign)
    assert controller.fiClassifyInterruptedCampaignsOnStartup(
        dictStore) == 2
    dictStates = {
        dictCampaign["sCampaignId"]: agentCouncilStore.fjsonGetCampaignRecord(
            dictStore, dictCampaign["sCampaignId"])["sState"]
        for dictCampaign in (dictNoRounds, dictNoAttempt, dictSettled,
                             dictFailed)}
    assert dictStates == {
        dictNoRounds["sCampaignId"]: "interrupted",
        dictNoAttempt["sCampaignId"]: "interrupted",
        dictSettled["sCampaignId"]: "planning",
        dictFailed["sCampaignId"]: "failed",
    }


# ----- connection building and the lazy baseline executor ------------------


def testAParticipantWhoseProviderHasNoStagerIsRefusedByName():
    dictRuntime = {
        "dictRunnerAccessByProvider": {"codex": {"dictEgress": {}}},
        "dictStageRunnerCredentials": {"claude": lambda: ("/unused", 0)},
    }
    with pytest.raises(controller.CouncilCommandError,
                       match="no credential stager was supplied for "
                             "provider 'codex'"):
        controller.fconnectionBuildParticipantConnection(
            dictRuntime, {"sProvider": "codex",
                          "sRequestedModel": "modelBeta"})


def testTheBaselineExecutorIsBuiltOnceAndRunsThroughTheRuntimeGateway():
    """Built on first use over the runtime's gateway, then reused.

    The runtime's registry is draining, so the REAL executor refuses at
    admission — proof it ran against this runtime's gateway — without a
    daemon ever being reached.
    """
    dictRegistry = agentCouncilRegistry.fdictCreateCouncilRegistry()
    dictRegistry["bAdmittingNewTurns"] = False
    dictRuntime = {
        "sCampaignId": "campaignAlpha",
        "sImageReference": "imageAlpha",
        "sSnapshotIdentity": "snapshotAlpha",
        "baSnapshotTar": b"",
        "fdictExecuteBaselineEvidence": None,
        "dictGateway": agentCouncilDockerGateway.fdictCreateCouncilDockerGateway(
            None, dictRegistry),
    }
    for _ in range(2):
        with pytest.raises(agentCouncilDockerGateway.CouncilGatewayError,
                           match="baseline sandbox admission refused"):
            controller._fdictExecuteBaselineEvidenceLazily(
                dictRuntime, {"sCommandText": "true"})
    fdictFirstExecutor = dictRuntime["fdictExecuteBaselineEvidence"]
    assert callable(fdictFirstExecutor)
    with pytest.raises(agentCouncilDockerGateway.CouncilGatewayError):
        controller._fdictExecuteBaselineEvidenceLazily(
            dictRuntime, {"sCommandText": "true"})
    assert dictRuntime["fdictExecuteBaselineEvidence"] is fdictFirstExecutor


# ----- the drive task's own fault handling ---------------------------------


def fdictRunFaultingDrive(tmp_path, dictCampaign):
    """Spawn a drive whose engine raises; return (store, id, registry)."""
    dictState, dictStore, sCampaignId = fdictStateWithParkedCampaign(
        tmp_path, dictCampaign)
    dictRuntime = dictState["dictCampaignRuntime"][sCampaignId]

    async def fnAdvanceEngineThatFaults():
        raise RuntimeError("provider transport exploded")

    async def fnSpawnAndAwait():
        controller._fsSpawnDriveTask(dictRuntime, fnAdvanceEngineThatFaults)
        assert dictRuntime["dictRegistry"]["setTurnsInFlight"]
        await dictRuntime["taskDrive"]

    asyncio.run(fnSpawnAndAwait())
    return dictStore, sCampaignId, dictRuntime["dictRegistry"]


def testADeliberationFaultFailsAPlanningCampaignAndNamesTheFault(tmp_path):
    dictStore, sCampaignId, dictRegistry = fdictRunFaultingDrive(
        tmp_path, fdictBuildCampaign())
    dictStored = agentCouncilStore.fjsonGetCampaignRecord(
        dictStore, sCampaignId)
    assert dictStored["sState"] == agentCouncilCampaign.S_STATE_FAILED
    assert dictStored["listStateTransitions"][-1]["sReason"] == (
        "deliberationFaulted: RuntimeError: provider transport exploded")
    assert dictRegistry["setTurnsInFlight"] == set()


def testADeliberationFaultNeverOverwritesAStateTheEngineAlreadyReached(
        tmp_path):
    dictStore, sCampaignId, dictRegistry = fdictRunFaultingDrive(
        tmp_path, fdictCampaignAtGate(
            agentCouncilCampaign.S_GATE_BLOCKING_QUESTION))
    dictStored = agentCouncilStore.fjsonGetCampaignRecord(
        dictStore, sCampaignId)
    assert dictStored["sState"] == agentCouncilCampaign.S_STATE_NEEDS_HUMAN
    assert dictRegistry["setTurnsInFlight"] == set()


# ----- participant reinstatement on a continuation --------------------------


def testAContinuationReinstatesOnlyTransientlyFailedParticipants(tmp_path):
    """A rate limit comes back; an authentication failure stays retired."""
    dictCampaign = fdictCampaignAtGate(
        agentCouncilCampaign.S_GATE_EXHAUSTED_ROUNDS)
    dictTransient, dictPermanent = dictCampaign["listParticipants"]
    for dictParticipant in (dictTransient, dictPermanent):
        dictParticipant["bFailed"] = True
        dictParticipant["sFailureReason"] = "failed earlier"
    dictCampaign["listRounds"] = [{
        "iRoundNumber": 1, "sResolution": "", "dictTurnsByPhase": {
            "independentProposals": [
                {"sParticipantId": dictTransient["sParticipantId"],
                 "sStatus": "failed", "sFailureClass": "rateLimit"},
                {"sParticipantId": dictPermanent["sParticipantId"],
                 "sStatus": "failed",
                 "sFailureReason": "authenticationFailed: logged out"},
            ]}}]
    dictState, dictStore, sCampaignId = fdictStateWithParkedCampaign(
        tmp_path, dictCampaign)
    with pytest.raises(controller.CouncilCommandError, match="at least one"):
        fnGrantRounds(dictState, dictStore, sCampaignId, 0)
    dictStored = agentCouncilStore.fjsonGetCampaignRecord(
        dictStore, sCampaignId)
    dictFailedById = {
        dictParticipant["sParticipantId"]: dictParticipant["bFailed"]
        for dictParticipant in dictStored["listParticipants"]}
    assert dictFailedById == {dictTransient["sParticipantId"]: False,
                              dictPermanent["sParticipantId"]: True}
    listEvents = agentCouncilStore.fdictCollectCampaignEvents(
        dictStore, sCampaignId, 0)["listEvents"]
    assert [(dictEvent["sEventKind"], dictEvent["sDetail"])
            for dictEvent in listEvents] == [
                ("participantReinstated", dictTransient["sParticipantId"])]
