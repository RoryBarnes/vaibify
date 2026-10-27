"""Falsification tests for the evidence ledger, event ring and caps.

Phase 1 of the Agent Council (design/agentCouncil.md sections 7.4-7.5 and
the section-15.1 checklist). The engine is driven with a real evidence
ledger and a real event ring — not stubs — so the properties that matter
are exercised end to end: a ``confirmed`` claim whose basis cannot be
retained reverts to ``asserted`` rather than shipping a label with no
evidence; a baseline claim is recorded against the fresh-sandbox snapshot
the server executor returns, never a hash the model asserted; a runner-
modified command carries a reconstructable, redacted change manifest and
reverts when it cannot; a credential-bearing command is never persisted;
event eviction is visible while the structured campaign record survives.

Each test fails if the property breaks: drop the reversion and a stale
``confirmed`` label survives; trust the model's snapshot hash and the
ledger records the wrong state.
"""

import pytest

from vaibify.gui.agentCouncilStore import (
    CouncilEventRing,
    CouncilEvidenceLedger,
    InMemoryCampaignCheckpoint,
    fbDetectCredentialText,
)

from tests.agentCouncilHarness import (
    fdictDecideCompleted,
    fdictMakeTurnResult,
    ffnDecideAllAccept,
    fixtureBuildCouncil,
)

LIST_TWO_SPECS = [
    {"sHandle": "A", "sProvider": "prov-a", "sRequestedModel": "model-a"},
    {"sHandle": "B", "sProvider": "prov-b", "sRequestedModel": "model-b"},
]


def _ffnDecideProposalEvidence(listEvidence):
    """A decider where participant A attaches evidence to its proposal."""
    def ffnDecide(sHandle, dictTurnRequest):
        if sHandle == "A" and dictTurnRequest["sPhase"] == (
                "independentProposals"):
            return fdictDecideCompleted(
                fdictMakeTurnResult("accept", listEvidence=listEvidence))
        return fdictDecideCompleted(fdictMakeTurnResult("accept"))
    return ffnDecide


def _fdictClaimAOf(fixture, dictOut):
    for dictRound in dictOut["listRounds"]:
        for dictRecord in dictRound["dictTurnsByPhase"].get(
                "independentProposals", []):
            if fixture.fsHandleForId(dictRecord["sParticipantId"]) == "A":
                return dictRecord["dictResult"]["listEvidence"][0]
    raise AssertionError("participant A proposal evidence not found")


# ----- baseline / server-driven executor ------------------------------

def testBaselineClaimIsRecordedAgainstTheServerSnapshotNotTheModelHash():
    """A baseline confirmed claim runs through the injected executor in a
    fresh sandbox; the ledger's state identity is the executor's snapshot
    hash, never a hash the model asserted (sections 7.4, 9.6). A model
    cannot even offer one now: validation refuses the field."""
    from vaibify.gui.agentCouncilCharter import fdictValidateTurnResult
    dictAsserting = fdictMakeTurnResult(listEvidence=[{
        "sStatus": "confirmed", "sStateForm": "baseline",
        "sCommandText": "pytest -q", "sSnapshotHash": "MODEL-ASSERTED-HASH"}])
    assert fdictValidateTurnResult(dictAsserting)["bValid"] is False
    listEvidence = [{"sStatus": "confirmed", "sStateForm": "baseline",
                     "sCommandText": "pytest -q"}]
    fixture = fixtureBuildCouncil(
        LIST_TWO_SPECS, _ffnDecideProposalEvidence(listEvidence),
        sChairbotHandle="A")
    dictOut = fixture.fdictDrive()
    dictClaim = _fdictClaimAOf(fixture, dictOut)
    assert dictClaim["sStatus"] == "confirmed"
    assert dictClaim["sLedgerStateForm"] == "baseline"
    assert len(fixture.listBaselineCalls) == 1
    assert fixture.listBaselineCalls[0]["sCommandText"] == "pytest -q"
    listEntries = fixture.ledger.flistCollectEntries()
    assert len(listEntries) == 1
    assert listEntries[0]["sSnapshotHash"] == "baseline-snapshot-hash-0001"


@pytest.mark.falsification
def testReadOnlyCouncilProducesNoConfirmedClaims():
    """A read-only council reverts every confirmed claim; nothing runs
    and nothing is ledgered (section 6.3.2).

    Kills: letting a read-only claim reach the baseline executor."""
    listEvidence = [{"sStatus": "confirmed", "sStateForm": "baseline",
                     "sCommandText": "pytest -q"}]
    fixture = fixtureBuildCouncil(
        LIST_TWO_SPECS, _ffnDecideProposalEvidence(listEvidence),
        dictSettings={"sExecutionPermission": "readOnly"},
        sChairbotHandle="A")
    dictOut = fixture.fdictDrive()
    dictClaim = _fdictClaimAOf(fixture, dictOut)
    assert dictClaim["sStatus"] == "asserted"
    assert dictClaim["sReversionReason"] == "readOnlyCouncil"
    assert fixture.listBaselineCalls == []
    assert fixture.ledger.flistCollectEntries() == []


# ----- reversion when the ledger cannot retain a basis ----------------

def testConfirmedClaimRevertsWhenLedgerEntryCannotBeRetained():
    """A ledger too small to hold the entry refuses it, and the claim it
    would have backed reverts to asserted (section 7.4)."""
    ledger = CouncilEvidenceLedger(iMaximumEntryBytes=10,
                                   iMaximumTotalBytes=10)
    listEvidence = [{"sStatus": "confirmed", "sStateForm": "baseline",
                     "sCommandText": "pytest -q"}]
    fixture = fixtureBuildCouncil(
        LIST_TWO_SPECS, _ffnDecideProposalEvidence(listEvidence),
        ledger=ledger, sChairbotHandle="A")
    dictOut = fixture.fdictDrive()
    dictClaim = _fdictClaimAOf(fixture, dictOut)
    assert dictClaim["sStatus"] == "asserted"
    assert dictClaim["sReversionReason"].startswith("evidenceLedgerRefused")
    assert ledger.flistCollectEntries() == []


DICT_PARTICIPANT_MANIFEST = {
    "listModifiedFiles": [{"sPath": "module.py", "sContent": "new source"}],
    "listDeletedPaths": [], "listChangedFileModes": [],
    "listSymlinkTargets": [],
}


@pytest.mark.falsification
def testModifiedStateClaimRevertsUntilVaibifyReplaysIt():
    """A participant's own report of a modified-state run never confirms.

    Kills: recording a modified-state claim from what the participant
    supplied, with no replay behind it.
    """
    listEvidence = [{"sStatus": "confirmed", "sStateForm": "modifiedState",
                     "sCommandText": "./experiment.sh",
                     "dictChangeManifest": DICT_PARTICIPANT_MANIFEST}]
    fixture = fixtureBuildCouncil(
        LIST_TWO_SPECS, _ffnDecideProposalEvidence(listEvidence),
        sChairbotHandle="A")
    dictOut = fixture.fdictDrive()
    dictClaim = _fdictClaimAOf(fixture, dictOut)
    assert dictClaim["sStatus"] == "asserted"
    assert dictClaim["sReversionReason"] == "modifiedStateNotReplayed"
    assert fixture.ledger.flistCollectEntries() == []


def testModifiedStateClaimWithIncompleteManifestIsRefusedAtValidation():
    """A manifest that cannot reconstruct the tested state never reaches
    the engine: validation names the missing kind of change, so the
    participant's one repair attempt is told what to add (section 7.4)."""
    from vaibify.gui.agentCouncilCharter import fdictValidateTurnResult
    dictResult = fdictMakeTurnResult(listEvidence=[{
        "sStatus": "confirmed", "sStateForm": "modifiedState",
        "sCommandText": "./experiment.sh",
        "dictChangeManifest": {"listModifiedFiles": [
            {"sPath": "module.py", "sContent": "x"}]}}])
    dictVerdict = fdictValidateTurnResult(dictResult)
    assert dictVerdict["bValid"] is False
    assert any("listDeletedPaths" in sProblem
               for sProblem in dictVerdict["listProblems"])


@pytest.mark.falsification
def testAWebSourceKeepsItsLinksAndLosesAnyCredential():
    """A web source stays in the record; a token in its query does not.

    Kills: settling a web source without scanning it for credentials.
    """
    listEvidence = [
        {"sStatus": "supportedBySourceInspection", "sStateForm": "webSource",
         "listSourceLinks": ["https://example.org/notes"],
         "sSearchQuery": "solver tolerance default"},
        {"sStatus": "supportedBySourceInspection", "sStateForm": "webSource",
         "listSourceLinks": ["https://example.org/notes"],
         "sSearchQuery": "Bearer abcdefghij0123456789zz"},
    ]
    fixture = fixtureBuildCouncil(
        LIST_TWO_SPECS, _ffnDecideProposalEvidence(listEvidence),
        sChairbotHandle="A")
    dictOut = fixture.fdictDrive()
    dictClean = _fdictClaimAOf(fixture, dictOut)
    dictRound = dictOut["listRounds"][0]
    dictTainted = next(
        dictRecord["dictResult"]["listEvidence"][1]
        for dictRecord in dictRound["dictTurnsByPhase"]["independentProposals"]
        if fixture.fsHandleForId(dictRecord["sParticipantId"]) == "A")
    assert dictClean["sStatus"] == "supportedBySourceInspection"
    assert dictClean["listSourceLinks"] == ["https://example.org/notes"]
    assert dictTainted["sStatus"] == "asserted"
    assert dictTainted["sReversionReason"] == "credentialInWebSource"
    assert "abcdefghij0123456789zz" not in dictTainted["sSearchQuery"]
    assert fixture.ledger.flistCollectEntries() == []


def testCredentialBearingCommandIsNotPersistedAndClaimReverts():
    """Redaction wins over provenance: a credential-bearing command is
    never persisted, and its claim reverts to asserted (section 7.4)."""
    sCommand = 'curl -H "Authorization: Bearer abcdefghij0123456789zz" api'
    assert fbDetectCredentialText(sCommand) is True
    listEvidence = [{"sStatus": "confirmed", "sStateForm": "baseline",
                     "sCommandText": sCommand}]
    fixture = fixtureBuildCouncil(
        LIST_TWO_SPECS, _ffnDecideProposalEvidence(listEvidence),
        sChairbotHandle="A")
    dictOut = fixture.fdictDrive()
    dictClaim = _fdictClaimAOf(fixture, dictOut)
    assert dictClaim["sStatus"] == "asserted"
    assert "credentialRedaction" in dictClaim["sReversionReason"]
    assert fixture.ledger.flistCollectEntries() == []


# ----- event ring caps -------------------------------------------------

def testEventRingEvictionIsVisibleWhileTheCampaignRecordSurvives():
    """Driven past its count bound, the ring evicts visibly (lowest
    retained sequence rises, evicted count grows) while the structured
    campaign record is untouched (section 7.4)."""
    eventRing = CouncilEventRing(iMaximumEventCount=5,
                                 iMaximumTotalBytes=10_000_000)
    fixture = fixtureBuildCouncil(
        LIST_TWO_SPECS, ffnDecideAllAccept, sChairbotHandle="A",
        listEventRing=eventRing)
    dictOut = fixture.fdictDrive()
    assert eventRing.bEvictionHasOccurred is True
    assert eventRing.iEvictedEventCount > 0
    assert eventRing.iLowestRetainedSequence > 1
    assert len(eventRing.listRetainedEvents) <= 5
    assert dictOut["dictCandidatePlan"] is not None
    assert dictOut["listRounds"][0]["dictVetoVerdicts"]


def testEventRingByteBoundEvictsButAlwaysRetainsTheNewest():
    """A byte-bounded ring still keeps the most recent event (section
    7.4)."""
    eventRing = CouncilEventRing(iMaximumEventCount=10_000,
                                 iMaximumTotalBytes=400)
    fixture = fixtureBuildCouncil(
        LIST_TWO_SPECS, ffnDecideAllAccept, sChairbotHandle="A",
        listEventRing=eventRing)
    fixture.fdictDrive()
    assert eventRing.bEvictionHasOccurred is True
    assert len(eventRing.listRetainedEvents) >= 1


# ----- checkpoint seam -------------------------------------------------

def testCheckpointCapturesLatestSettledCampaign():
    """Each settled turn and phase checkpoints, so a crash loses at most
    the in-flight turn (section 7.5)."""
    fixture = fixtureBuildCouncil(LIST_TWO_SPECS, ffnDecideAllAccept,
                                  sChairbotHandle="A")
    dictOut = fixture.fdictDrive()
    assert fixture.checkpoint.iCheckpointCount > 0
    dictLatest = fixture.checkpoint.fdictLoadLatestCheckpoint()
    assert dictLatest["sState"] == dictOut["sState"]
    assert dictLatest is not dictOut
