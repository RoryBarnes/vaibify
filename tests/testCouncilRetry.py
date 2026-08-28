"""Falsification tests for retry and retirement (continuation plan 2.5/2.6).

The researcher's ruling made executable: a failure re-runs the phase
that failed, and the failed attempt is retired into the record — never
erased. Each test plants a REAL terminal record (driven through the
actual engine, captured as a crashed hub's checkpoint) into a durable
store and retries it through the controller, with recording fake
connections proving which phases actually re-ran.
"""

import asyncio
import copy

import pytest

from tests.agentCouncilHarness import (
    VersionRecordingCheckpoint,
    fdictDecideCompleted,
    fdictMakeTurnResult,
    ffnDecideAllAccept,
    fixtureBuildCouncil,
)
from tests.testCouncilResume import (  # noqa: F401
    LIST_TWO_SPECS,
    S_IMAGE_IDENTITY,
    _RecordingAcceptConnection,
    _tPlantCrashedCampaign,
)
from vaibify.gui import agentCouncilController
from vaibify.gui import agentCouncilProviders
from vaibify.gui import agentCouncilStore
from vaibify.gui.agentCouncilCampaign import (
    SET_RETRYABLE_TURN_FAILURE_REASONS,
)


def _fdictDriveToFailedSynthesis(sEmptyReason):
    """Drive a real council whose synthesis authors all fail.

    Returns the final checkpointed record: state failed, attempt
    outcome transitioned:failed, with the machine failure class the
    retry whitelist reads.
    """
    def _ffnDecide(sHandle, dictTurnRequest):
        if dictTurnRequest["sPhase"] == "synthesis":
            return fdictDecideCompleted(
                {"sEmptyResultReason": sEmptyReason})
        return fdictDecideCompleted(fdictMakeTurnResult(sVerdict="accept"))

    checkpointRecorder = VersionRecordingCheckpoint()
    fixtureCouncil = fixtureBuildCouncil(
        LIST_TWO_SPECS, _ffnDecide, sChairbotHandle="alpha",
        checkpoint=checkpointRecorder)
    dictOut = fixtureCouncil.fdictDrive()
    assert dictOut["sState"] == "failed", dictOut["sState"]
    return copy.deepcopy(checkpointRecorder.listVersions[-1])


def _fdictRetryToCompletion(dictStore, dictRegistry, dictControllerState,
                            sCampaignId, monkeypatch, listPhaseLog):
    """Run retry and await the continued drive task to settlement."""
    monkeypatch.setattr(
        agentCouncilController, "fconnectionBuildParticipantConnection",
        lambda dictRuntime, dictParticipant:
            _RecordingAcceptConnection(listPhaseLog))

    async def _fdictRun():
        dictRetried = (
            await agentCouncilController.fdictRetryCampaignFailedPhase(
                dictControllerState, dictStore, dictRegistry, sCampaignId,
                S_IMAGE_IDENTITY))
        dictRuntime = dictControllerState["dictCampaignRuntime"].get(
            sCampaignId)
        if dictRuntime is not None and dictRuntime.get(
                "taskDrive") is not None:
            await dictRuntime["taskDrive"]
        return dictRetried

    return asyncio.run(_fdictRun())


def testTheRetryWhitelistMirrorsTheProviderVocabulary():
    """The pure module's strings track the provider classification.

    The whitelist lives in the pure campaign module; the classifier
    constants live with the provider adapter. A mirror nobody checks
    is a second authority, so this pins every provider constant the
    whitelist names.
    """
    assert agentCouncilProviders.S_FAILURE_RATE_LIMIT in (
        SET_RETRYABLE_TURN_FAILURE_REASONS)
    assert agentCouncilProviders.S_FAILURE_KILLED_NO_EXIT_CODE in (
        SET_RETRYABLE_TURN_FAILURE_REASONS)
    assert agentCouncilProviders.S_FAILURE_NO_RESULT_EVENT in (
        SET_RETRYABLE_TURN_FAILURE_REASONS)
    assert agentCouncilProviders.S_FAILURE_CLEAN_EXIT in (
        SET_RETRYABLE_TURN_FAILURE_REASONS)
    assert agentCouncilProviders.S_FAILURE_NON_ZERO_EXIT in (
        SET_RETRYABLE_TURN_FAILURE_REASONS)
    assert agentCouncilProviders.S_EMPTY_BECAUSE_WALL_CLOCK in (
        SET_RETRYABLE_TURN_FAILURE_REASONS)
    assert agentCouncilProviders.S_FAILURE_NETWORK_UNREACHABLE in (
        SET_RETRYABLE_TURN_FAILURE_REASONS)
    # The identical-on-re-run classes stay OUT.
    assert agentCouncilProviders.S_FAILURE_AUTHENTICATION not in (
        SET_RETRYABLE_TURN_FAILURE_REASONS)
    assert "outputByteBudgetExceeded" not in (
        SET_RETRYABLE_TURN_FAILURE_REASONS)


@pytest.mark.falsification
def testRetryRetiresTheFailedAttemptAndRerunsThePhase(
        tmp_path, monkeypatch):
    """The ruling end to end: retire, restore, re-run, and say so.

    A rate-limited synthesis killed the campaign; retry restores the
    round to its pre-synthesis state, moves the failed attempt AND its
    turns into the retired record — a plan that reached consensus
    after a re-roll must be tellable from one that reached it first
    time — records the researcher decision, and re-runs ONLY synthesis
    and veto to the same planReady a clean run reaches, with the new
    attempt numbered 2.

    Kills: retirement forgetting to restore the pre-phase state, so
    bSynthesisSettled stays True and the re-run walks straight past
    the phase it was asked to re-run.
    """
    dictStore, dictRegistry, dictControllerState, sCampaignId = (
        _tPlantCrashedCampaign(
            tmp_path, _fdictDriveToFailedSynthesis("rateLimit")))
    listPhaseLog = []

    dictRetried = _fdictRetryToCompletion(
        dictStore, dictRegistry, dictControllerState, sCampaignId,
        monkeypatch, listPhaseLog)

    assert dictRetried["bRetried"] is True
    assert dictRetried["sRetriedPhase"] == "synthesis"
    assert dictRetried["iRetiredAttemptNumber"] == 1
    assert set(listPhaseLog) == {"synthesis", "veto"}, listPhaseLog
    dictRecord = agentCouncilStore.fjsonGetCampaignRecord(
        dictStore, sCampaignId)
    assert dictRecord["sState"] == "planReady"
    dictRound = dictRecord["listRounds"][-1]
    listRetired = dictRound.get("listRetiredAttempts") or []
    assert len(listRetired) == 1
    assert listRetired[0]["sPhase"] == "synthesis"
    assert listRetired[0]["listRetiredTurnRecords"], (
        "the retired attempt lost its turns — the provenance a reader "
        "needs to tell a re-roll from a first try")
    assert dictRound["dictPhaseAttempt"]["sPhase"] == "veto"
    assert any(
        dictDecision.get("sDecisionKind") == "phaseRetried"
        for dictDecision in dictRecord.get("listResearcherDecisions") or [])
    # The re-run attempt numbered itself above the retired one.
    listRetiredSynthesis = [d for d in listRetired
                            if d["sPhase"] == "synthesis"]
    assert listRetiredSynthesis[0]["iAttemptNumber"] == 1


@pytest.mark.falsification
def testANonRetryableFailureIsRefusedWithItsReasonNamed(
        tmp_path, monkeypatch):
    """An authentication failure fails identically on a re-run.

    Kills: the controller skipping the whitelist and re-spending the
    researcher's subscription on a failure that cannot change.
    """
    dictStore, dictRegistry, dictControllerState, sCampaignId = (
        _tPlantCrashedCampaign(
            tmp_path,
            _fdictDriveToFailedSynthesis("authenticationFailure")))

    with pytest.raises(agentCouncilController.CouncilCommandError) as error:
        _fdictRetryToCompletion(
            dictStore, dictRegistry, dictControllerState, sCampaignId,
            monkeypatch, [])
    assert "authenticationFailure" in str(error.value)


def testARetryBuildFaultAnswersAsARefusalNotAServerError(
        tmp_path, monkeypatch):
    """An egress fault during the retry's rebuild carries its reason.

    The translation first landed only on the resume rebuild; the retry
    kept an identical build block and a researcher's retry died as an
    unhandled 500 — the click looked like nothing at all (2026-08-27).
    Both paths now share one build-await helper, and this drives the
    RETRY one.
    """
    from vaibify.gui import agentCouncilEgress
    dictStore, dictRegistry, dictControllerState, sCampaignId = (
        _tPlantCrashedCampaign(
            tmp_path, _fdictDriveToFailedSynthesis("rateLimit")))

    def _fnRaiseEgressFault(dictRuntime, dictParticipant):
        raise agentCouncilEgress.EgressSetupError(
            "pinned egress proxy image pull failed: registry DNS down")

    monkeypatch.setattr(
        agentCouncilController, "fconnectionBuildParticipantConnection",
        _fnRaiseEgressFault)
    with pytest.raises(agentCouncilController.CouncilCommandError) as error:
        asyncio.run(agentCouncilController.fdictRetryCampaignFailedPhase(
            dictControllerState, dictStore, dictRegistry, sCampaignId,
            S_IMAGE_IDENTITY))
    assert "could not be rebuilt" in str(error.value)
    assert "registry DNS down" in str(error.value)
    assert "record is untouched" in str(error.value)


@pytest.mark.falsification
def testAnInterruptedCampaignRetriesAfterItsReservationsSettle(
        tmp_path, monkeypatch):
    """Reconcile, then Retry (continuation plan 2.5).

    An indeterminate turn interrupted the campaign. With a reservation
    still unsettled the retry refuses and names the remedy; once the
    registry is clean, the interrupted phase re-runs and the abandoned
    questions are regenerated by the re-run rather than pretended
    handled.

    Kills: retry skipping the unsettled-work refusal for interrupted
    campaigns.
    """
    from vaibify.gui import agentCouncilRegistry as registryModule

    def _ffnDecideIndeterminate(sHandle, dictTurnRequest):
        if (dictTurnRequest["sPhase"] == "crossReview"
                and sHandle == "beta"):
            return fdictDecideCompleted(
                fdictMakeTurnResult(sVerdict="accept"),
                sCompletion="indeterminate")
        return fdictDecideCompleted(fdictMakeTurnResult(sVerdict="accept"))

    checkpointRecorder = VersionRecordingCheckpoint()
    fixtureCouncil = fixtureBuildCouncil(
        LIST_TWO_SPECS, _ffnDecideIndeterminate, sChairbotHandle="alpha",
        checkpoint=checkpointRecorder)
    dictOut = fixtureCouncil.fdictDrive()
    assert dictOut["sState"] == "interrupted"
    dictStore, dictRegistry, dictControllerState, sCampaignId = (
        _tPlantCrashedCampaign(
            tmp_path, copy.deepcopy(checkpointRecorder.listVersions[-1])))

    dictRegistry["dictReservationsById"]["reservation-1"] = {
        "sCampaignId": sCampaignId,
        "sStatus": registryModule.S_RESERVATION_QUARANTINED,
    }
    # This reservation carries no container id, so nothing can be
    # re-proved about it: the refusal stands, naming the hub restart
    # whose startup reconciliation settles what the daemon has already
    # forgotten.
    with pytest.raises(agentCouncilController.CouncilCommandError,
                       match="Restart the hub"):
        _fdictRetryToCompletion(
            dictStore, dictRegistry, dictControllerState, sCampaignId,
            monkeypatch, [])

    del dictRegistry["dictReservationsById"]["reservation-1"]
    listPhaseLog = []
    dictRetried = _fdictRetryToCompletion(
        dictStore, dictRegistry, dictControllerState, sCampaignId,
        monkeypatch, listPhaseLog)
    assert dictRetried["sRetriedPhase"] == "crossReview"
    assert "crossReview" in listPhaseLog
    assert agentCouncilStore.fjsonGetCampaignRecord(
        dictStore, sCampaignId)["sState"] == "planReady"


def _fdictRetryClearingStop(dictStore, dictRegistry, dictControllerState,
                            sCampaignId, monkeypatch, listPhaseLog):
    """Retry with the stop explicitly cleared, to settlement."""
    monkeypatch.setattr(
        agentCouncilController, "fconnectionBuildParticipantConnection",
        lambda dictRuntime, dictParticipant:
            _RecordingAcceptConnection(listPhaseLog))

    async def _fdictRun():
        dictRetried = (
            await agentCouncilController.fdictRetryCampaignFailedPhase(
                dictControllerState, dictStore, dictRegistry, sCampaignId,
                S_IMAGE_IDENTITY, bClearStopRequest=True))
        dictRuntime = dictControllerState["dictCampaignRuntime"].get(
            sCampaignId)
        if dictRuntime is not None and dictRuntime.get(
                "taskDrive") is not None:
            await dictRuntime["taskDrive"]
        return dictRetried

    return asyncio.run(_fdictRun())


@pytest.mark.falsification
def testARetryWithAStandingStopSurfacesTheChoice(tmp_path, monkeypatch):
    """A kept flag turns "retry" into "silently destroy".

    Retirement transitions to planning and spawns the drive, whose
    FIRST act on a set bStopRequested is to archive — the researcher
    clicks Retry and watches the campaign vanish. The choice is
    surfaced exactly as resume surfaces it: refused without the
    explicit clear; with it, the clear lands as a recorded researcher
    decision and the phase actually re-runs.

    Kills: retry ignoring the standing stop request.
    """
    dictVersion = _fdictDriveToFailedSynthesis("rateLimit")
    dictVersion["bStopRequested"] = True
    dictStore, dictRegistry, dictControllerState, sCampaignId = (
        _tPlantCrashedCampaign(tmp_path, dictVersion))

    with pytest.raises(agentCouncilController.CouncilCommandError,
                       match="stop was requested"):
        _fdictRetryToCompletion(
            dictStore, dictRegistry, dictControllerState, sCampaignId,
            monkeypatch, [])

    listPhaseLog = []
    dictRetried = _fdictRetryClearingStop(
        dictStore, dictRegistry, dictControllerState, sCampaignId,
        monkeypatch, listPhaseLog)
    assert dictRetried["bRetried"] is True
    dictRecord = agentCouncilStore.fjsonGetCampaignRecord(
        dictStore, sCampaignId)
    # The regression: without the guard, the drive archives instantly.
    assert dictRecord["sState"] == "planReady"
    assert dictRecord["bStopRequested"] is False
    assert any(
        dictDecision.get("sDecisionKind") == "stopRequestClearedOnRetry"
        for dictDecision in dictRecord.get("listResearcherDecisions") or [])


@pytest.mark.falsification
def testRetiredEvidenceIsMarkedAndPreservedNeverDeleted(tmp_path):
    """Ledger entries bound to a retired attempt survive, marked.

    Kills: retirement deleting (or never marking) the retired
    attempt's evidence — the history a reader needs to weigh a
    re-rolled consensus.
    """
    dictStore, sCampaignId = _tBuildStoreWithBoundEvidence(tmp_path)

    agentCouncilStore.fnMarkEvidenceRetiredForAttempt(
        dictStore, sCampaignId, "synthesis#1")

    ledgerEvidence = dictStore["dictEntriesById"][sCampaignId][
        "ledgerEvidence"]
    listEntries = ledgerEvidence.listRecordedEntries
    assert [dictEntry["sAttemptBinding"] for dictEntry in listEntries] == [
        "synthesis#1", "veto#1"]
    assert listEntries[0].get("bRetiredWithAttempt") is True
    assert listEntries[1].get("bRetiredWithAttempt") is None
    # Marked state is durable: a reloaded store still holds it.
    dictReloaded = agentCouncilStore.fdictCreateCampaignStore(
        sDurableStoreRoot=dictStore["sDurableStoreRoot"])
    agentCouncilStore.fdictReloadDurableCampaigns(dictReloaded)
    listReloaded = dictReloaded["dictEntriesById"][sCampaignId][
        "ledgerEvidence"].listRecordedEntries
    assert listReloaded[0].get("bRetiredWithAttempt") is True


def _tBuildStoreWithBoundEvidence(tmp_path):
    """A store holding one campaign with two attempt-bound entries."""
    from vaibify.gui.agentCouncilCampaign import (
        fdictCreateCampaign, fdictCreateParticipant)
    dictStore = agentCouncilStore.fdictCreateCampaignStore(
        sDurableStoreRoot=str(tmp_path / "councils"))
    dictCampaign = fdictCreateCampaign(
        "How should the cache be keyed?",
        [fdictCreateParticipant("claude", "model-a"),
         fdictCreateParticipant("claude", "model-b")])
    agentCouncilStore.fdictRegisterStartedCampaign(dictStore, dictCampaign)
    for sBinding in ("synthesis#1", "veto#1"):
        dictOutcome = agentCouncilStore.fdictRecordCampaignEvidence(
            dictStore, dictCampaign["sCampaignId"], {
                "sClaimIdentifier": f"claim-{sBinding}",
                "sAttemptBinding": sBinding,
                "sCommandText": "pytest tests/testCacheLayer.py",
                "sStateForm": "baseline",
                "sSnapshotHash": "sealed-content-identity-0001",
                "sExecutionImageIdentity": "sha256:" + "cd34" * 16,
                "iExitCode": 0,
                "sOutputDigest": "sha256:" + "ef56" * 16,
            })
        assert dictOutcome["bRecorded"], dictOutcome
    return dictStore, dictCampaign["sCampaignId"]


def testASpentRuntimeRecordDoesNotBlockTheRetryItExistsFor(
        tmp_path, monkeypatch):
    """A settled runtime is discarded; retry rebuilds over it.

    The runtime stays registered after a drive settles so an unproven
    egress teardown keeps its retry state. Read as "work is live",
    that made every IN-PROCESS failure unretryable until the hub
    restarted — a council killed by a spend limit, which is exactly
    what retry is for, answered "there is nothing to retry"
    (2026-08-28, live).
    """
    dictStore, dictRegistry, dictControllerState, sCampaignId = (
        _tPlantCrashedCampaign(
            tmp_path, _fdictDriveToFailedSynthesis("rateLimit")))
    # A spent record: its drive is gone and its resources settled.
    dictControllerState["dictCampaignRuntime"][sCampaignId] = {
        "dictCampaign": {}, "dictRunnerAccess": None,
        "bLaunchInProgress": False, "taskDrive": None,
    }

    dictRetried = _fdictRetryToCompletion(
        dictStore, dictRegistry, dictControllerState, sCampaignId,
        monkeypatch, [])

    assert dictRetried["bRetried"] is True


def testAnUnprovenRunnerNetworkRefusesRetryAndNamesTheRemedy(
        tmp_path, monkeypatch):
    """Resources nobody proved gone still refuse — with the way out.

    The refusal that MUST survive: a retained runner network is the
    record that something may still be running, so a rebuild over it
    would launch beside it. The message names the startup sweep rather
    than leaving the researcher to guess.
    """
    dictStore, dictRegistry, dictControllerState, sCampaignId = (
        _tPlantCrashedCampaign(
            tmp_path, _fdictDriveToFailedSynthesis("rateLimit")))
    dictControllerState["dictCampaignRuntime"][sCampaignId] = {
        "dictCampaign": {}, "bLaunchInProgress": False, "taskDrive": None,
        "dictRunnerAccess": {"dictEgress": {"sNetworkName": "net-1"}},
    }

    with pytest.raises(agentCouncilController.CouncilCommandError) as error:
        _fdictRetryToCompletion(
            dictStore, dictRegistry, dictControllerState, sCampaignId,
            monkeypatch, [])
    assert "could not be proven gone" in str(error.value)
    assert "Restart the hub" in str(error.value)


def _fnInstallProbeDouble(monkeypatch, sAnswer):
    """Answer every absence probe with one fixed daemon verdict."""
    from vaibify.gui import agentCouncilDockerGateway
    monkeypatch.setattr(
        agentCouncilDockerGateway, "fdockerCreateCouncilClient",
        lambda *listArgs, **dictKeywords: object())
    monkeypatch.setattr(
        agentCouncilDockerGateway, "fdictProbeRunnerAbsence",
        lambda dockerCouncil, sContainerId: {
            "sAnswer": sAnswer, "sDetail": "", "dictLabels": {}})


def testAStaleQuarantineIsReprovedAndStopsBlockingTheRetry(
        tmp_path, monkeypatch):
    """A quarantined runner the daemon has forgotten is settled, not obeyed.

    A quarantine records an unproven teardown, not a live runner. Two
    of them blocked a researcher's retry over containers the daemon
    had already forgotten, and only a hub restart cleared them
    (2026-08-28, live).
    """
    from vaibify.gui import agentCouncilRegistry as registryModule
    dictStore, dictRegistry, dictControllerState, sCampaignId = (
        _tPlantCrashedCampaign(
            tmp_path, _fdictDriveToFailedSynthesis("rateLimit")))
    dictRegistry["dictReservationsById"]["reservation-stale"] = {
        "sReservationId": "reservation-stale",
        "sCampaignId": sCampaignId,
        "sStatus": registryModule.S_RESERVATION_QUARANTINED,
        "sContainerId": "container-long-gone",
        "iEpoch": 1,
    }
    _fnInstallProbeDouble(monkeypatch, "absent")

    dictRetried = _fdictRetryToCompletion(
        dictStore, dictRegistry, dictControllerState, sCampaignId,
        monkeypatch, [])

    assert dictRetried["bRetried"] is True
    assert "reservation-stale" not in dictRegistry["dictReservationsById"]


@pytest.mark.parametrize("sAnswer", ["present", "indeterminate"])
def testAQuarantineTheDaemonCannotDisproveStillRefuses(
        tmp_path, monkeypatch, sAnswer):
    """PRESENT and INDETERMINATE both keep the refusal.

    The distinction the probe exists to preserve: only a POSITIVE
    absence settles a reservation. A daemon that says the runner is
    there — or cannot say — leaves budget held and the retry refused,
    because launching beside a live runner is the harm.
    """
    from vaibify.gui import agentCouncilRegistry as registryModule
    dictStore, dictRegistry, dictControllerState, sCampaignId = (
        _tPlantCrashedCampaign(
            tmp_path, _fdictDriveToFailedSynthesis("rateLimit")))
    dictRegistry["dictReservationsById"]["reservation-live"] = {
        "sReservationId": "reservation-live",
        "sCampaignId": sCampaignId,
        "sStatus": registryModule.S_RESERVATION_QUARANTINED,
        "sContainerId": "container-maybe-there",
        "iEpoch": 1,
    }
    _fnInstallProbeDouble(monkeypatch, sAnswer)

    with pytest.raises(agentCouncilController.CouncilCommandError,
                       match="nobody proved gone"):
        _fdictRetryToCompletion(
            dictStore, dictRegistry, dictControllerState, sCampaignId,
            monkeypatch, [])
    assert "reservation-live" in dictRegistry["dictReservationsById"]
