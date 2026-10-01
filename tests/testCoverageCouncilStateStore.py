"""The council store's refusals, bounds and durable-file edges.

The event ring and evidence ledger are bounded at admission and must
refuse visibly; the durable checkpoint must degrade honestly when a
file is missing or unreadable; every campaign-keyed entry point must
refuse an unknown campaign rather than silently creating one. Driven
against a real temp app-data root — no doubles are needed, because the
store's only boundary is the filesystem.
"""

import json
import os
import stat

import pytest

from vaibify.gui import agentCouncilStore


S_CAMPAIGN_ALPHA = "campaign-alpha"
S_CAMPAIGN_UNKNOWN = "campaign-never-stored"


def fdictBuildCampaign(sCampaignId, listRounds=None):
    """Return a minimal engine-shaped campaign record."""
    return {
        "sCampaignId": sCampaignId,
        "sState": "planning",
        "sQuestion": "Which configuration converges?",
        "listParticipants": [{"sParticipantId": "participant-one"},
                             {"sParticipantId": "participant-two"}],
        "listRounds": listRounds or [],
    }


def fdictBuildStore(tmp_path, dictBounds=None):
    """Return a store rooted in a temp app-data directory."""
    return agentCouncilStore.fdictCreateCampaignStore(
        sDurableStoreRoot=str(tmp_path / "agentCouncils"),
        dictBounds=dictBounds)


def fdictBuildEvidence(**dictOverrides):
    """Return a complete baseline evidence entry, with overrides."""
    dictEntry = {
        "sClaimIdentifier": "claim-1",
        "sCommandText": "python stepAlpha.py",
        "sStateForm": agentCouncilStore.S_STATE_FORM_BASELINE,
        "sSnapshotHash": "a" * 64,
        "sExecutionImageIdentity": "sha256:" + "b" * 64,
        "iExitCode": 0,
        "sOutputDigest": "c" * 64,
    }
    dictEntry.update(dictOverrides)
    return dictEntry


def fdictBuildManifest(**dictOverrides):
    """Return a complete modified-state change manifest, with overrides."""
    dictManifest = {
        "dictModifiedFileContents": {"stepAlpha/dataFile.csv": "x,y\n"},
        "listDeletedPaths": [],
        "dictChangedFileModes": {},
        "dictSymlinkTargets": {},
    }
    dictManifest.update(dictOverrides)
    return dictManifest


# ----- ring and ledger bounds ----------------------------------------------


@pytest.mark.parametrize("tBounds", [(0, 100), (10, 0), (-1, -1)])
def testEventRingRefusesNonPositiveBounds(tBounds):
    with pytest.raises(ValueError) as excInfo:
        agentCouncilStore.CouncilEventRing(*tBounds)
    assert "must be positive" in str(excInfo.value)


@pytest.mark.parametrize("tBounds", [(0, 100), (10, 0)])
def testEvidenceLedgerRefusesNonPositiveBounds(tBounds):
    with pytest.raises(ValueError) as excInfo:
        agentCouncilStore.CouncilEvidenceLedger(*tBounds)
    assert "must be positive" in str(excInfo.value)


def testEmptyEventRingReportsZeroBoundsAndNoEviction():
    ringEvents = agentCouncilStore.CouncilEventRing(4, 4096)
    assert ringEvents.iLowestRetainedSequence == 0
    assert ringEvents.iHighestRetainedSequence == 0
    assert ringEvents.bEvictionHasOccurred is False
    assert ringEvents.flistCollectEventsAfter(0) == []


# ----- ledger admission ----------------------------------------------------


def testAnUnknownStateFormIsRefusedBeforeAnythingIsKept():
    ledgerEvidence = agentCouncilStore.CouncilEvidenceLedger(65536, 262144)
    dictOutcome = ledgerEvidence.fdictRecordEvidence(
        fdictBuildEvidence(sStateForm="guessedState"))
    assert dictOutcome["sRefusalReason"] == (
        agentCouncilStore.S_REFUSAL_UNKNOWN_STATE_FORM)
    assert ledgerEvidence.iRecordedTotalBytes == 0
    assert ledgerEvidence.listRecordedEntries == []


def testModifiedStateWithoutAManifestMappingIsRefused():
    ledgerEvidence = agentCouncilStore.CouncilEvidenceLedger(65536, 262144)
    dictOutcome = ledgerEvidence.fdictRecordEvidence(fdictBuildEvidence(
        sStateForm=agentCouncilStore.S_STATE_FORM_MODIFIED,
        dictChangeManifest=["not", "a", "mapping"]))
    assert dictOutcome == {
        "bRecorded": False, "sEntryIdentifier": "",
        "sRefusalReason":
            agentCouncilStore.S_REFUSAL_INCOMPLETE_CHANGE_MANIFEST}
    assert ledgerEvidence.iRefusedEntryCount == 1
    assert ledgerEvidence.flistCollectEntries() == []


@pytest.mark.parametrize("sMissingKey",
                         agentCouncilStore.LIST_CHANGE_MANIFEST_KEYS)
def testModifiedStateMissingAnyManifestKeyIsRefused(sMissingKey):
    dictManifest = fdictBuildManifest()
    del dictManifest[sMissingKey]
    ledgerEvidence = agentCouncilStore.CouncilEvidenceLedger(65536, 262144)
    dictOutcome = ledgerEvidence.fdictRecordEvidence(fdictBuildEvidence(
        sStateForm=agentCouncilStore.S_STATE_FORM_MODIFIED,
        dictChangeManifest=dictManifest))
    assert dictOutcome["sRefusalReason"] == (
        agentCouncilStore.S_REFUSAL_INCOMPLETE_CHANGE_MANIFEST)


@pytest.mark.parametrize("valueContents", [
    ["stepAlpha/dataFile.csv"], {"stepAlpha/dataFile.csv": None}])
def testModifiedContentsThatAreNotBoundedTextAreRefused(valueContents):
    """Digests or absent bytes cannot reconstruct the tested state."""
    ledgerEvidence = agentCouncilStore.CouncilEvidenceLedger(65536, 262144)
    dictOutcome = ledgerEvidence.fdictRecordEvidence(fdictBuildEvidence(
        sStateForm=agentCouncilStore.S_STATE_FORM_MODIFIED,
        dictChangeManifest=fdictBuildManifest(
            dictModifiedFileContents=valueContents)))
    assert dictOutcome["sRefusalReason"] == (
        agentCouncilStore.S_REFUSAL_INCOMPLETE_CHANGE_MANIFEST)


def testACredentialInASymlinkTargetRefusesTheWholeEntry():
    ledgerEvidence = agentCouncilStore.CouncilEvidenceLedger(65536, 262144)
    dictOutcome = ledgerEvidence.fdictRecordEvidence(fdictBuildEvidence(
        sStateForm=agentCouncilStore.S_STATE_FORM_MODIFIED,
        dictChangeManifest=fdictBuildManifest(
            dictSymlinkTargets={"link": "ghp_" + "Q" * 30})))
    assert dictOutcome["sRefusalReason"] == (
        agentCouncilStore.S_REFUSAL_CREDENTIAL_REDACTION)
    assert ledgerEvidence.listRecordedEntries == []


def testACompleteModifiedStateEntryIsRecorded():
    ledgerEvidence = agentCouncilStore.CouncilEvidenceLedger(65536, 262144)
    dictOutcome = ledgerEvidence.fdictRecordEvidence(fdictBuildEvidence(
        sStateForm=agentCouncilStore.S_STATE_FORM_MODIFIED,
        dictChangeManifest=fdictBuildManifest(
            dictSymlinkTargets={"latest": "dataFile.csv"})))
    assert dictOutcome == {"bRecorded": True, "sRefusalReason": "",
                           "sEntryIdentifier": "evidence-1"}


def testTheLedgerBudgetRefusesOnceExhaustedAndKeepsEarlierEntries():
    dictFirst = fdictBuildEvidence(sClaimIdentifier="claim-1")
    iEntryBytes = len(json.dumps(dictFirst, sort_keys=True).encode("utf-8"))
    ledgerEvidence = agentCouncilStore.CouncilEvidenceLedger(
        iEntryBytes * 4, iEntryBytes * 2)
    assert ledgerEvidence.fdictRecordEvidence(dictFirst)["bRecorded"]
    dictOutcome = ledgerEvidence.fdictRecordEvidence(
        fdictBuildEvidence(sClaimIdentifier="claim-2"))
    assert dictOutcome["sRefusalReason"] == (
        agentCouncilStore.S_REFUSAL_LEDGER_EXHAUSTED)
    assert [dictEntry["sClaimIdentifier"] for dictEntry in
            ledgerEvidence.flistCollectEntries()] == ["claim-1"]
    assert ledgerEvidence.iRefusedEntryCount == 1


# ----- the in-memory checkpoint --------------------------------------------


def testInMemoryCheckpointIsEmptyUntilWrittenAndCopiesOnRead():
    checkpointMemory = agentCouncilStore.InMemoryCampaignCheckpoint()
    assert checkpointMemory.fdictLoadLatestCheckpoint() is None
    assert checkpointMemory.ffFindLastWrittenEpoch() == 0.0
    dictCampaign = fdictBuildCampaign(S_CAMPAIGN_ALPHA)
    checkpointMemory.fnCheckpointCampaign(dictCampaign)
    dictCampaign["sState"] = "mutatedAfterCheckpoint"
    dictLoaded = checkpointMemory.fdictLoadLatestCheckpoint()
    assert dictLoaded["sState"] == "planning"
    dictLoaded["sState"] = "mutatedAfterLoad"
    assert checkpointMemory.fdictLoadLatestCheckpoint()["sState"] == (
        "planning")
    assert checkpointMemory.iCheckpointCount == 1
    assert checkpointMemory.ffFindLastWrittenEpoch() > 0.0


# ----- the durable checkpoint ----------------------------------------------


def testDurableCheckpointReportsNoEpochBeforeAnyWrite(tmp_path):
    checkpointDurable = agentCouncilStore.DurableCampaignCheckpoint(
        str(tmp_path / "neverWritten"))
    assert checkpointDurable.ffFindLastWrittenEpoch() == 0.0
    assert checkpointDurable.fdictLoadLatestCheckpoint() is None
    assert checkpointDurable.fdictLoadProvenance() is None
    assert checkpointDurable.fsReadAcceptedPlanText() == ""


def testAnUnreadableProvenanceSidecarAnswersNone(tmp_path):
    sDirectory = tmp_path / "campaignDirectory"
    sDirectory.mkdir()
    (sDirectory / "provenance.json").write_text("{ not json")
    checkpointDurable = agentCouncilStore.DurableCampaignCheckpoint(
        str(sDirectory))
    assert checkpointDurable.fdictLoadProvenance() is None


def testAcceptedPatchIsWrittenPrivatelyAndRedacted(tmp_path):
    dictStore = fdictBuildStore(tmp_path)
    agentCouncilStore.fdictRegisterStartedCampaign(
        dictStore, fdictBuildCampaign(S_CAMPAIGN_ALPHA))
    sPatchText = ("--- a/stepAlpha.py\n+++ b/stepAlpha.py\n"
                  "+sToken = 'ghp_" + "Z" * 30 + "'\n")
    sPatchPath = agentCouncilStore.fsAcceptCampaignPatchLocally(
        dictStore, S_CAMPAIGN_ALPHA, sPatchText)
    assert os.path.basename(sPatchPath) == "implementation.patch"
    assert os.path.dirname(sPatchPath) == os.path.join(
        dictStore["sDurableStoreRoot"], S_CAMPAIGN_ALPHA)
    assert stat.S_IMODE(os.stat(sPatchPath).st_mode) == 0o600
    with open(sPatchPath, encoding="utf-8") as filePatch:
        assert filePatch.read() == (
            agentCouncilStore.S_CREDENTIAL_REDACTION_MARKER)


def testAcceptedPlanTextRoundTripsThroughTheStore(tmp_path):
    dictStore = fdictBuildStore(tmp_path)
    agentCouncilStore.fdictRegisterStartedCampaign(
        dictStore, fdictBuildCampaign(S_CAMPAIGN_ALPHA))
    assert agentCouncilStore.fsReadAcceptedPlanText(
        dictStore, S_CAMPAIGN_ALPHA) == ""
    agentCouncilStore.fsAcceptCampaignPlanLocally(
        dictStore, S_CAMPAIGN_ALPHA, "# The plan\n\nStep one.\n")
    assert agentCouncilStore.fsReadAcceptedPlanText(
        dictStore, S_CAMPAIGN_ALPHA) == "# The plan\n\nStep one.\n"
    assert agentCouncilStore.fsReadAcceptedPlanText(
        dictStore, S_CAMPAIGN_UNKNOWN) == ""


# ----- store construction and unknown campaigns ----------------------------


def testStoreRefusesAnUnknownBoundName(tmp_path):
    with pytest.raises(ValueError) as excInfo:
        fdictBuildStore(tmp_path, dictBounds={"iEventCountBoundTypo": 3})
    assert "iEventCountBoundTypo" in str(excInfo.value)


def testRegisteringTheSameCampaignTwiceIsRefused(tmp_path):
    dictStore = fdictBuildStore(tmp_path)
    agentCouncilStore.fdictRegisterStartedCampaign(
        dictStore, fdictBuildCampaign(S_CAMPAIGN_ALPHA))
    with pytest.raises(ValueError) as excInfo:
        agentCouncilStore.fdictRegisterStartedCampaign(
            dictStore, fdictBuildCampaign(S_CAMPAIGN_ALPHA))
    assert "already stored" in str(excInfo.value)
    assert dictStore["listInsertionOrder"] == [S_CAMPAIGN_ALPHA]


@pytest.mark.parametrize("sFunctionName,tArguments,sFragment", [
    ("fsMintNextTurnId", (), "to launch a turn"),
    ("fnCheckpointStoredCampaign", ({"sCampaignId": "x"},), "to checkpoint"),
    ("fdictAppendCampaignEvent", ({"sEventKind": "noted"},), "for an event"),
    ("fdictRecordCampaignEvidence", ({},), "for evidence"),
    ("fsAcceptCampaignPatchLocally", ("patch",), "to accept"),
    ("fsAcceptCampaignPlanLocally", ("plan",), "to accept"),
])
def testCampaignKeyedWritesRefuseAnUnknownCampaign(
        tmp_path, sFunctionName, tArguments, sFragment):
    dictStore = fdictBuildStore(tmp_path)
    with pytest.raises(ValueError) as excInfo:
        getattr(agentCouncilStore, sFunctionName)(
            dictStore, S_CAMPAIGN_UNKNOWN, *tArguments)
    assert sFragment in str(excInfo.value)
    assert S_CAMPAIGN_UNKNOWN in str(excInfo.value)
    assert not os.path.exists(
        os.path.join(dictStore["sDurableStoreRoot"], S_CAMPAIGN_UNKNOWN))


def testUnknownCampaignReadsAnswerEmptyRatherThanRaise(tmp_path):
    dictStore = fdictBuildStore(tmp_path)
    assert agentCouncilStore.fjsonGetCampaignRecord(
        dictStore, S_CAMPAIGN_UNKNOWN) is None
    assert agentCouncilStore.fdictCollectCampaignEvents(
        dictStore, S_CAMPAIGN_UNKNOWN, 0) is None
    assert agentCouncilStore.fbCampaignProvenanceUnavailable(
        dictStore, S_CAMPAIGN_UNKNOWN) is False
    dictStopping = agentCouncilStore.fdictDescribeStoredStoppingPoint(
        dictStore, S_CAMPAIGN_UNKNOWN)
    assert dictStopping["bResumable"] is False
    assert dictStopping["sAction"] == "none"


def testRetiringAnAttemptWithNoBindingOrCampaignChangesNothing(tmp_path):
    dictStore = fdictBuildStore(tmp_path)
    agentCouncilStore.fdictRegisterStartedCampaign(
        dictStore, fdictBuildCampaign(S_CAMPAIGN_ALPHA))
    agentCouncilStore.fdictRecordCampaignEvidence(
        dictStore, S_CAMPAIGN_ALPHA,
        fdictBuildEvidence(sAttemptBinding="attempt-1"))
    agentCouncilStore.fnMarkEvidenceRetiredForAttempt(
        dictStore, S_CAMPAIGN_ALPHA, "")
    agentCouncilStore.fnMarkEvidenceRetiredForAttempt(
        dictStore, S_CAMPAIGN_UNKNOWN, "attempt-1")
    dictEntry = dictStore["dictEntriesById"][S_CAMPAIGN_ALPHA]
    assert "bRetiredWithAttempt" not in (
        dictEntry["ledgerEvidence"].listRecordedEntries[0])
    agentCouncilStore.fnMarkEvidenceRetiredForAttempt(
        dictStore, S_CAMPAIGN_ALPHA, "attempt-1")
    assert dictEntry["ledgerEvidence"].listRecordedEntries[0][
        "bRetiredWithAttempt"] is True


def testListingSkipsAnOrderEntryWhoseRecordIsGone(tmp_path):
    dictStore = fdictBuildStore(tmp_path)
    agentCouncilStore.fdictRegisterStartedCampaign(
        dictStore, fdictBuildCampaign(S_CAMPAIGN_ALPHA))
    dictStore["listInsertionOrder"].append("campaign-orphaned-order")
    listSummaries = agentCouncilStore.flistSummariseCampaigns(dictStore)
    assert [dictSummary["sCampaignId"] for dictSummary in listSummaries] == [
        S_CAMPAIGN_ALPHA]


# ----- reload ------------------------------------------------------------


def testReloadSkipsCampaignsAlreadyInMemoryAndUnreadableRecords(tmp_path):
    dictStore = fdictBuildStore(tmp_path)
    agentCouncilStore.fdictRegisterStartedCampaign(
        dictStore, fdictBuildCampaign(S_CAMPAIGN_ALPHA))
    sCorruptDirectory = os.path.join(
        dictStore["sDurableStoreRoot"], "campaign-corrupt")
    os.makedirs(sCorruptDirectory)
    with open(os.path.join(sCorruptDirectory, "campaign.json"), "w") as \
            fileCorrupt:
        fileCorrupt.write("{ truncated")
    os.makedirs(os.path.join(dictStore["sDurableStoreRoot"],
                             "campaign-without-record"))
    dictReloaded = agentCouncilStore.fdictReloadDurableCampaigns(dictStore)
    assert dictReloaded == {"iReloaded": 0}
    assert list(dictStore["dictEntriesById"]) == [S_CAMPAIGN_ALPHA]


def testReloadOfAnEmptyRootReloadsNothing(tmp_path):
    dictStore = fdictBuildStore(tmp_path)
    assert agentCouncilStore.fdictReloadDurableCampaigns(dictStore) == {
        "iReloaded": 0}
