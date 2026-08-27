"""Falsification tests: the ledger and turn counter survive a reload.

The specification lists the evidence ledger as part of the durable
campaign record (design section 7.5), but until the provenance sidecar
existed `fdictReloadDurableCampaigns` rebuilt every entry with an EMPTY
`CouncilEvidenceLedger` and `iTurnsLaunched: 0`. After any hub restart,
confirmed claims cited ledger entries that no longer existed, the next
entry re-minted `evidence-1`, the ledger byte budget silently refilled,
and the turn counter's only-rises contract broke. That corrupted
evidence provenance for any campaign read after a restart — resume or
no resume — and is the hard prerequisite for resume, which would
otherwise mint colliding ids into a half-amnesiac store.

Every test drives TWO real stores over one durable root — recording
into the first, reloading into the second — never a patched reload.
"""

import os

import pytest

from vaibify.gui import agentCouncilStore
from vaibify.gui.agentCouncilCampaign import (
    fdictCreateCampaign,
    fdictCreateParticipant,
)

S_QUESTION = "How should the cache be keyed?"


def _fdictBuildStoreWithOneCampaign(tmp_path):
    """Return (dictStore, sCampaignId) over a real durable root."""
    dictStore = agentCouncilStore.fdictCreateCampaignStore(
        sDurableStoreRoot=str(tmp_path / "councils"))
    dictCampaign = fdictCreateCampaign(
        S_QUESTION,
        [fdictCreateParticipant("claude", "model-a"),
         fdictCreateParticipant("claude", "model-b")])
    agentCouncilStore.fdictRegisterStartedCampaign(dictStore, dictCampaign)
    return dictStore, dictCampaign["sCampaignId"]


def _fdictReloadFreshStore(tmp_path):
    """Build a second store over the same root, as a restarted hub does."""
    dictReloaded = agentCouncilStore.fdictCreateCampaignStore(
        sDurableStoreRoot=str(tmp_path / "councils"))
    agentCouncilStore.fdictReloadDurableCampaigns(dictReloaded)
    return dictReloaded


def _fdictEvidenceEntry(sClaimIdentifier):
    """A schema-valid baseline evidence entry (the ledger's own shape)."""
    return {
        "sClaimIdentifier": sClaimIdentifier,
        "sCommandText": "pytest tests/testCacheLayer.py",
        "sStateForm": "baseline",
        "sSnapshotHash": "sealed-content-identity-0001",
        "sExecutionImageIdentity": "sha256:" + "cd34" * 16,
        "iExitCode": 0,
        "sOutputDigest": "sha256:" + "ef56" * 16,
    }


@pytest.mark.falsification
def testAClaimRecordedBeforeAReloadIsReadableAfterIt(tmp_path):
    """A confirmed claim's cited evidence must survive the hub.

    Kills: the reload path rebuilding the entry without its sidecar,
    which recreates the ledger empty and leaves every pre-restart
    claim citing evidence that no longer exists.
    """
    dictStore, sCampaignId = _fdictBuildStoreWithOneCampaign(tmp_path)
    dictOutcome = agentCouncilStore.fdictRecordCampaignEvidence(
        dictStore, sCampaignId, _fdictEvidenceEntry("claim-cache-holds"))
    assert dictOutcome["bRecorded"], dictOutcome
    sEntryIdentifier = dictOutcome["sEntryIdentifier"]

    dictReloaded = _fdictReloadFreshStore(tmp_path)

    ledgerReloaded = dictReloaded["dictEntriesById"][sCampaignId][
        "ledgerEvidence"]
    listIdentifiers = [dictEntry["sEntryIdentifier"]
                       for dictEntry in ledgerReloaded.listRecordedEntries]
    assert sEntryIdentifier in listIdentifiers
    # The byte budget is consumed history, not a fresh allowance.
    assert ledgerReloaded.iRecordedTotalBytes > 0


@pytest.mark.falsification
def testAnEvidenceIdMintedAfterAReloadNeverCollides(tmp_path):
    """evidence-N stays unique across the restart boundary.

    Kills: the ledger restore dropping the recorded entries from the
    identifier sequence, so the first post-restart entry re-mints
    evidence-1 and two different observations share one citation.
    """
    dictStore, sCampaignId = _fdictBuildStoreWithOneCampaign(tmp_path)
    agentCouncilStore.fdictRecordCampaignEvidence(
        dictStore, sCampaignId, _fdictEvidenceEntry("claim-first"))

    dictReloaded = _fdictReloadFreshStore(tmp_path)

    dictSecond = agentCouncilStore.fdictRecordCampaignEvidence(
        dictReloaded, sCampaignId, _fdictEvidenceEntry("claim-second"))
    assert dictSecond["bRecorded"], dictSecond
    assert dictSecond["sEntryIdentifier"] == "evidence-2"


@pytest.mark.falsification
def testATurnIdMintedAfterAReloadNeverCollides(tmp_path):
    """turn-N only ever rises, restart or no restart.

    The registry's turn-in-flight key and every turn record ride this
    id; a counter that resets on reload hands a resumed campaign the
    same turn id an earlier run already settled under.

    Kills: the reload path zeroing the turn counter.
    """
    dictStore, sCampaignId = _fdictBuildStoreWithOneCampaign(tmp_path)
    assert agentCouncilStore.fsMintNextTurnId(
        dictStore, sCampaignId) == "turn-1"
    assert agentCouncilStore.fsMintNextTurnId(
        dictStore, sCampaignId) == "turn-2"

    dictReloaded = _fdictReloadFreshStore(tmp_path)

    assert agentCouncilStore.fsMintNextTurnId(
        dictReloaded, sCampaignId) == "turn-3"


@pytest.mark.falsification
def testARefusalBudgetDoesNotRefillOnReload(tmp_path):
    """The refusal count is ledger history too.

    Kills: the sidecar being written only when an entry is RECORDED,
    which lets the refusal count silently reset across a restart.
    """
    dictStore, sCampaignId = _fdictBuildStoreWithOneCampaign(tmp_path)
    dictRefused = agentCouncilStore.fdictRecordCampaignEvidence(
        dictStore, sCampaignId, {"sClaimIdentifier": "claim-shapeless"})
    assert not dictRefused["bRecorded"]

    dictReloaded = _fdictReloadFreshStore(tmp_path)

    assert dictReloaded["dictEntriesById"][sCampaignId][
        "ledgerEvidence"].iRefusedEntryCount == 1


def _fnGiveCampaignRecordedActivity(dictStore, sCampaignId):
    """Checkpoint one settled turn into the record, as a real run does."""
    jsonRecord = agentCouncilStore.fjsonGetCampaignRecord(
        dictStore, sCampaignId)
    jsonRecord["listRounds"] = [{
        "iRoundNumber": 1,
        "sResolution": "",
        "dictTurnsByPhase": {
            "independentProposals": [{"sStatus": "completed"}]},
    }]
    agentCouncilStore.fnCheckpointStoredCampaign(
        dictStore, sCampaignId, jsonRecord)


@pytest.mark.falsification
def testALostSidecarUnderARunCampaignRefusesNotResets(tmp_path):
    """Lost provenance is marked and refused, never silently rebuilt.

    A record that already RAN carries confirmed claims citing ledger
    entries; a rebuilt-empty ledger orphans every citation, re-mints
    evidence-1, and a zeroed counter re-mints turn identifiers earlier
    work already used. That is the original reload corruption wearing
    a fresh coat (2026-08-27 review). The campaign stays DISCOVERABLE
    — a researcher can still read it — but nothing may extend or
    resume its history.

    Kills: the reload treating a lost sidecar under recorded activity
    as an honest empty history.
    """
    dictStore, sCampaignId = _fdictBuildStoreWithOneCampaign(tmp_path)
    agentCouncilStore.fsMintNextTurnId(dictStore, sCampaignId)
    agentCouncilStore.fdictRecordCampaignEvidence(
        dictStore, sCampaignId, _fdictEvidenceEntry("claim-recorded"))
    _fnGiveCampaignRecordedActivity(dictStore, sCampaignId)
    os.remove(os.path.join(
        str(tmp_path / "councils"), sCampaignId,
        agentCouncilStore.S_PROVENANCE_SIDECAR_BASENAME))

    dictReloaded = _fdictReloadFreshStore(tmp_path)

    assert agentCouncilStore.fbCampaignProvenanceUnavailable(
        dictReloaded, sCampaignId)
    # Still discoverable, but the listing says why it cannot continue.
    listSummaries = agentCouncilStore.flistSummariseCampaigns(dictReloaded)
    assert [dictSummary["sCampaignId"] for dictSummary in listSummaries] == [
        sCampaignId]
    assert listSummaries[0]["dictStoppingPoint"]["bResumable"] is False
    assert "provenance sidecar" in listSummaries[0][
        "dictStoppingPoint"]["sBlockedReason"]
    # No new evidence, and no identifier reuse.
    dictRefused = agentCouncilStore.fdictRecordCampaignEvidence(
        dictReloaded, sCampaignId, _fdictEvidenceEntry("claim-later"))
    assert dictRefused["bRecorded"] is False
    assert "provenance sidecar" in dictRefused["sRefusalReason"]
    with pytest.raises(ValueError, match="provenance sidecar"):
        agentCouncilStore.fsMintNextTurnId(dictReloaded, sCampaignId)


def testAFreshCampaignWithNoSidecarIsNotPunished(tmp_path):
    """No sidecar under a record that never ran lost nothing.

    A freshly registered campaign writes its sidecar at the first mint
    or evidence record, so absence with no recorded turns is the
    ordinary young-campaign state — refusing it would strand every
    campaign a hub restarts under before its first turn.
    """
    dictStore, sCampaignId = _fdictBuildStoreWithOneCampaign(tmp_path)

    dictReloaded = _fdictReloadFreshStore(tmp_path)

    assert not agentCouncilStore.fbCampaignProvenanceUnavailable(
        dictReloaded, sCampaignId)
    assert agentCouncilStore.fsMintNextTurnId(
        dictReloaded, sCampaignId) == "turn-1"
