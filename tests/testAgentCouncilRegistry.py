"""Unit falsification of the app-owned council registry (design 9.3/9.4/21).

No Docker here: drain and reconcile monkeypatch the runner's destruction
transaction so the registry's accounting is exercised in isolation. The
idle-watchdog mutant test drives the REAL
``serverLifespan._fbHubShouldSelfExit`` and proves the council veto is
load-bearing — neutralize the predicate and the hub self-SIGTERMs under
live council work.
"""

import time
import types

import pytest

from vaibify.gui import agentCouncilDockerGateway
from vaibify.gui import agentCouncilRegistry as registry
from vaibify.gui import agentCouncilRunner
from vaibify.gui import serverLifespan


def _fdictRunnerCost(iGibibytes=1, fCpu=1.0):
    return {"iMemoryBytes": iGibibytes * 1024 * 1024 * 1024,
            "fCpuCount": fCpu}


def testTwoScopeAdmissionBoundsBothCampaignAndHub():
    """Neither a single campaign nor two competing ones exceed the hub."""
    dictRegistry = registry.fdictCreateCouncilRegistry()
    # Per-campaign quota is 3; the fourth in one campaign is refused for
    # the campaign, even though the hub ceiling (4) still has room.
    for iIndex in range(3):
        dictAnswer = registry.fdictReserveRunner(
            dictRegistry, "campaignA", f"resA{iIndex}", "claude",
            _fdictRunnerCost())
        assert dictAnswer["bReserved"] is True
    dictFourthSameCampaign = registry.fdictReserveRunner(
        dictRegistry, "campaignA", "resA3", "claude", _fdictRunnerCost())
    assert dictFourthSameCampaign["bReserved"] is False
    assert "per-campaign" in dictFourthSameCampaign["sRefusalReason"]
    # A second campaign takes the last hub slot (total 4)...
    dictSecondCampaign = registry.fdictReserveRunner(
        dictRegistry, "campaignB", "resB0", "claude", _fdictRunnerCost())
    assert dictSecondCampaign["bReserved"] is True
    # ...and its next is refused at the hub-wide ceiling, so two
    # campaigns cannot together exceed the global budget.
    dictOverHub = registry.fdictReserveRunner(
        dictRegistry, "campaignB", "resB1", "claude", _fdictRunnerCost())
    assert dictOverHub["bReserved"] is False
    assert "hub-wide" in dictOverHub["sRefusalReason"]


def testAggregateMemoryCeilingRefusesBeforeRunnerCountDoes():
    """A memory-heavy reservation trips the aggregate bound, not the count."""
    dictRegistry = registry.fdictCreateCouncilRegistry(
        {"iMaxAggregateMemoryBytes": 3 * 1024 * 1024 * 1024,
         "iPerCampaignMaxConcurrentRunners": 4})
    for iIndex in range(3):
        assert registry.fdictReserveRunner(
            dictRegistry, "campaignA", f"res{iIndex}", "claude",
            _fdictRunnerCost(iGibibytes=1))["bReserved"] is True
    dictOverMemory = registry.fdictReserveRunner(
        dictRegistry, "campaignA", "res3", "claude",
        _fdictRunnerCost(iGibibytes=1))
    assert dictOverMemory["bReserved"] is False
    assert "aggregate-memory" in dictOverMemory["sRefusalReason"]


def testDuplicateReservationIdIsRefused():
    dictRegistry = registry.fdictCreateCouncilRegistry()
    registry.fdictReserveRunner(
        dictRegistry, "campaignA", "res0", "claude", _fdictRunnerCost())
    with pytest.raises(registry.CouncilAdmissionError):
        registry.fdictReserveRunner(
            dictRegistry, "campaignA", "res0", "claude", _fdictRunnerCost())


def testDuplicateTurnInFlightIsRefused():
    dictRegistry = registry.fdictCreateCouncilRegistry()
    assert registry.fbRegisterTurnInFlight(
        dictRegistry, "campaignA", "turn1") is True
    assert registry.fbRegisterTurnInFlight(
        dictRegistry, "campaignA", "turn1") is False
    registry.fnRetireTurnInFlight(dictRegistry, "campaignA", "turn1")
    assert registry.fbRegisterTurnInFlight(
        dictRegistry, "campaignA", "turn1") is True


def testCompareAndSettleRefusesAStaleEpoch():
    """A settle naming a stale epoch cannot erase a successor."""
    dictRegistry = registry.fdictCreateCouncilRegistry()
    dictReserved = registry.fdictReserveRunner(
        dictRegistry, "campaignA", "res0", "claude", _fdictRunnerCost())
    iEpoch = dictReserved["dictReservation"]["iEpoch"]
    registry.fnMarkRunnerCreated(dictRegistry, "res0", "container-abc")
    dictStale = registry.fdictSettleReservation(
        dictRegistry, "res0", agentCouncilRunner.S_OUTCOME_DESTROYED,
        iExpectedEpoch=iEpoch + 999)
    assert dictStale["bSettled"] is False
    assert dictStale["sReason"] == "staleEpochRefused"
    assert "res0" in dictRegistry["dictReservationsById"]
    dictSettled = registry.fdictSettleReservation(
        dictRegistry, "res0", agentCouncilRunner.S_OUTCOME_DESTROYED,
        iExpectedEpoch=iEpoch)
    assert dictSettled["bSettled"] is True
    assert "res0" not in dictRegistry["dictReservationsById"]
    dictReSettle = registry.fdictSettleReservation(
        dictRegistry, "res0", agentCouncilRunner.S_OUTCOME_DESTROYED)
    assert dictReSettle["sReason"] == "reservationAlreadyGone"


def testQuarantinedReservationStaysLiveWork():
    """An indeterminate destruction leaves a visible, budget-holding runner."""
    dictRegistry = registry.fdictCreateCouncilRegistry()
    registry.fdictReserveRunner(
        dictRegistry, "campaignA", "res0", "claude", _fdictRunnerCost())
    registry.fnMarkRunnerCreated(dictRegistry, "res0", "container-abc")
    dictSettled = registry.fdictSettleReservation(
        dictRegistry, "res0", agentCouncilRunner.S_OUTCOME_QUARANTINED)
    assert dictSettled["sReason"] == "quarantined"
    assert dictRegistry["dictReservationsById"]["res0"]["sStatus"] == (
        registry.S_RESERVATION_QUARANTINED)
    assert registry.fbCouncilRegistryReportsLiveWork(dictRegistry) is True


def testDrainStopsAdmissionAndSettlesLiveRunners(monkeypatch):
    dictRegistry = registry.fdictCreateCouncilRegistry()
    registry.fdictReserveRunner(
        dictRegistry, "campaignA", "resLive", "claude", _fdictRunnerCost())
    registry.fnMarkRunnerCreated(dictRegistry, "resLive", "container-live")
    registry.fdictReserveRunner(
        dictRegistry, "campaignA", "resPending", "claude", _fdictRunnerCost())
    registry.fdictRecordApiRequest(
        dictRegistry, "req0", "campaignA", "claude")

    monkeypatch.setattr(
        agentCouncilDockerGateway, "fdictDestroyRunnerAndProveAbsence",
        lambda dockerCouncil, sContainerId: {
            "sOutcome": agentCouncilRunner.S_OUTCOME_DESTROYED,
            "sReason": "", "dictProbe": {}})
    dictReport = registry.fdictDrainCouncilRegistry(dictRegistry, object())

    assert dictRegistry["bAdmittingNewTurns"] is False
    assert dictReport["listDestroyed"] == ["resLive"]
    assert dictReport["listInterruptedApi"] == ["req0"]
    # The pending reservation with no container is dropped, the live one
    # destroyed, so no admission slot survives the drain.
    assert dictRegistry["dictReservationsById"] == {}
    dictRefused = registry.fdictReserveRunner(
        dictRegistry, "campaignA", "resAfter", "claude", _fdictRunnerCost())
    assert dictRefused["bReserved"] is False
    assert "draining" in dictRefused["sRefusalReason"]


def testReconcileDiscoversAndSettlesOrphanedSurvivors(monkeypatch):
    dictRegistry = registry.fdictCreateCouncilRegistry()
    monkeypatch.setattr(
        agentCouncilDockerGateway, "flistDiscoverLabeledRunners",
        lambda dockerCouncil: [
            {"sContainerId": "c1", "sReservationId": "res1", "sRole": "runner",
             "sContainerName": "n1", "sStatus": "running"}])
    monkeypatch.setattr(
        agentCouncilDockerGateway, "fdictDestroyRunnerAndProveAbsence",
        lambda dockerCouncil, sContainerId: {
            "sOutcome": agentCouncilRunner.S_OUTCOME_DESTROYED,
            "sReason": "", "dictProbe": {}})
    dictReport = registry.fdictReconcileLabeledRunnersOnRestart(
        dictRegistry, object())
    assert dictReport["iSurvivorsDiscovered"] == 1
    assert dictReport["listDestroyed"] == ["res1"]


def _fappWithIdleState():
    """Build an app whose non-council idle checks all say 'exit'."""
    stateApp = types.SimpleNamespace(
        iActiveWebSockets=0,
        dictContainerOwners={},
        iHubPort=8000,
        fLastActivityMonotonic=time.monotonic() - 100000.0,
    )
    return types.SimpleNamespace(state=stateApp)


def testIdleVetoMutantSelfExitsWhenPredicateNeutralized(monkeypatch):
    """The council idle veto is load-bearing: neutralize it and the hub
    self-SIGTERMs mid-turn (design section 21 blocker)."""
    app = _fappWithIdleState()
    fTimeout = 1.0
    # Baseline: with no council work, every other signal says exit.
    assert serverLifespan._fbHubShouldSelfExit(app, {}, fTimeout) is True

    dictRegistry = registry.fdictCreateCouncilRegistry()
    registry.fdictReserveRunner(
        dictRegistry, "campaignA", "resLive", "claude", _fdictRunnerCost())
    registry.fnMarkRunnerCreated(dictRegistry, "resLive", "container-live")
    app.state.dictCouncilRegistry = dictRegistry

    def fbShouldExitWithCouncilVeto():
        # The exact composition Phase 3 installs in _fbHubShouldSelfExit.
        return (serverLifespan._fbHubShouldSelfExit(app, {}, fTimeout)
                and not registry.fbHubHasLiveCouncilWork(app))

    # With the real predicate, live council work vetoes the self-exit.
    assert registry.fbHubHasLiveCouncilWork(app) is True
    assert fbShouldExitWithCouncilVeto() is False

    # MUTANT: neutralize the predicate. The hub now self-exits despite a
    # live runner mid-turn — the discarded-turn / orphaned-runner bug.
    monkeypatch.setattr(
        registry, "fbCouncilRegistryReportsLiveWork", lambda dictReg: False)
    assert registry.fbHubHasLiveCouncilWork(app) is False
    assert fbShouldExitWithCouncilVeto() is True
