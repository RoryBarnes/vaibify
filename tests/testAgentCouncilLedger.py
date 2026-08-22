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
    hash, never the hash the model asserted (sections 7.4, 9.6)."""
    listEvidence = [{"sStatus": "confirmed", "sStateForm": "baseline",
                     "sCommandText": "pytest -q",
                     "sSnapshotHash": "MODEL-ASSERTED-HASH"}]
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
    assert listEntries[0]["sSnapshotHash"] != "MODEL-ASSERTED-HASH"


def testReadOnlyCouncilProducesNoConfirmedClaims():
    """A read-only council reverts every confirmed claim; nothing runs
    and nothing is ledgered (section 6.3.2)."""
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


def testModifiedStateClaimWithCompleteManifestIsConfirmed():
    """A runner-modified command is a labeled modified-state experiment
    carrying a reconstructable change manifest (section 7.4)."""
    dictManifest = {"dictModifiedFileContents": {"module.py": "new source"},
                    "listDeletedPaths": [], "dictChangedFileModes": {},
                    "dictSymlinkTargets": {}}
    listEvidence = [{"sStatus": "confirmed", "sStateForm": "modifiedState",
                     "sCommandText": "./experiment.sh", "sSnapshotHash": "h",
                     "sExecutionImageIdentity": "image", "iExitCode": 0,
                     "sOutputDigest": "digest",
                     "dictChangeManifest": dictManifest}]
    fixture = fixtureBuildCouncil(
        LIST_TWO_SPECS, _ffnDecideProposalEvidence(listEvidence),
        sChairbotHandle="A")
    dictOut = fixture.fdictDrive()
    dictClaim = _fdictClaimAOf(fixture, dictOut)
    assert dictClaim["sStatus"] == "confirmed"
    assert dictClaim["sLedgerStateForm"] == "modifiedState"
    listEntries = fixture.ledger.flistCollectEntries()
    assert listEntries[0]["dictChangeManifest"][
        "dictModifiedFileContents"] == {"module.py": "new source"}
    assert fixture.listBaselineCalls == []


def testModifiedStateClaimWithIncompleteManifestReverts():
    """A modified-state manifest that cannot reconstruct the tested state
    is refused, and the claim reverts (section 7.4)."""
    listEvidence = [{"sStatus": "confirmed", "sStateForm": "modifiedState",
                     "sCommandText": "./experiment.sh", "sSnapshotHash": "h",
                     "sExecutionImageIdentity": "image", "iExitCode": 0,
                     "sOutputDigest": "digest",
                     "dictChangeManifest": {
                         "dictModifiedFileContents": {"module.py": "x"}}}]
    fixture = fixtureBuildCouncil(
        LIST_TWO_SPECS, _ffnDecideProposalEvidence(listEvidence),
        sChairbotHandle="A")
    dictOut = fixture.fdictDrive()
    dictClaim = _fdictClaimAOf(fixture, dictOut)
    assert dictClaim["sStatus"] == "asserted"
    assert "incompleteChangeManifest" in dictClaim["sReversionReason"]
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
