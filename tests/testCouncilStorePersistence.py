"""Durable campaign-record persistence for the Agent Council store.

The campaign record is checkpointed to host application-data OUTSIDE the
project repository, credential-redacted and owner-private, so a hub crash
loses at most the single in-flight turn (design section 7.3, 7.5). These
tests exercise the store directly against a temp app-data root: the
durable write, its permissions and location, credential redaction,
crash-restart reload, bounded retention, and campaign+snapshot deletion.
"""

import json
import os
import stat

import pytest

from vaibify.gui import agentCouncilStore


def _dictCampaign(sCampaignId, sQuestion="a question"):
    """Return a minimal engine-shaped campaign record for the store."""
    return {
        "sCampaignId": sCampaignId,
        "sState": "planning",
        "sQuestion": sQuestion,
        "listParticipants": [{"sParticipantId": "p1"}, {"sParticipantId": "p2"}],
        "listRounds": [],
    }


def _dictStore(tmp_path, dictBounds=None):
    """Return a campaign store rooted under a temp app-data directory."""
    return agentCouncilStore.fdictCreateCampaignStore(
        sDurableStoreRoot=str(tmp_path / "agentCouncils"),
        dictBounds=dictBounds)


def test_durable_checkpoint_writes_the_record_to_app_data(tmp_path):
    """Registering a campaign writes campaign.json under the app-data root."""
    dictStore = _dictStore(tmp_path)
    agentCouncilStore.fdictRegisterStartedCampaign(
        dictStore, _dictCampaign("campaign-1"))
    sRecordPath = os.path.join(
        dictStore["sDurableStoreRoot"], "campaign-1",
        agentCouncilStore.S_CAMPAIGN_RECORD_BASENAME)
    assert os.path.isfile(sRecordPath)
    with open(sRecordPath) as fileRecord:
        assert json.load(fileRecord)["sCampaignId"] == "campaign-1"


@pytest.mark.realCouncilStoreRoot
def test_the_store_root_is_outside_any_repository(tmp_path):
    """The default durable root is host app-data under the home directory."""
    sRoot = agentCouncilStore.fsResolveDurableStoreRoot()
    assert sRoot.startswith(os.path.expanduser("~"))
    assert ".vaibify" in sRoot and "agentCouncils" in sRoot


def test_durable_files_are_owner_private(tmp_path):
    """The per-campaign directory is 0700 and the record file 0600."""
    dictStore = _dictStore(tmp_path)
    agentCouncilStore.fdictRegisterStartedCampaign(
        dictStore, _dictCampaign("campaign-1"))
    sDirectory = os.path.join(dictStore["sDurableStoreRoot"], "campaign-1")
    sRecordPath = os.path.join(
        sDirectory, agentCouncilStore.S_CAMPAIGN_RECORD_BASENAME)
    assert stat.S_IMODE(os.stat(sDirectory).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(sRecordPath).st_mode) == 0o600


def test_credentials_are_redacted_before_the_record_lands(tmp_path):
    """A credential-shaped field never reaches the durable file."""
    dictStore = _dictStore(tmp_path)
    dictCampaign = _dictCampaign("campaign-secret")
    dictCampaign["sQuestion"] = "token ghp_" + "A" * 30 + " leaked into text"
    agentCouncilStore.fdictRegisterStartedCampaign(dictStore, dictCampaign)
    sRecordPath = os.path.join(
        dictStore["sDurableStoreRoot"], "campaign-secret",
        agentCouncilStore.S_CAMPAIGN_RECORD_BASENAME)
    with open(sRecordPath) as fileRecord:
        sContent = fileRecord.read()
    assert "ghp_" not in sContent
    assert agentCouncilStore.S_CREDENTIAL_REDACTION_MARKER in sContent


def test_accepted_plan_is_written_locally_only(tmp_path):
    """Accepting a plan writes a 0600 plan.md beside the record."""
    dictStore = _dictStore(tmp_path)
    agentCouncilStore.fdictRegisterStartedCampaign(
        dictStore, _dictCampaign("campaign-1"))
    sPlanPath = agentCouncilStore.fsAcceptCampaignPlanLocally(
        dictStore, "campaign-1", "# Plan\n\nDo the thing.\n")
    assert os.path.isfile(sPlanPath)
    assert sPlanPath.endswith(agentCouncilStore.S_ACCEPTED_PLAN_BASENAME)
    assert stat.S_IMODE(os.stat(sPlanPath).st_mode) == 0o600


def test_accepted_campaigns_reload_after_a_restart(tmp_path):
    """A fresh store rebuilds durable campaigns from app-data on restart."""
    dictStoreFirst = _dictStore(tmp_path)
    agentCouncilStore.fdictRegisterStartedCampaign(
        dictStoreFirst, _dictCampaign("campaign-1", "first"))
    agentCouncilStore.fdictRegisterStartedCampaign(
        dictStoreFirst, _dictCampaign("campaign-2", "second"))
    dictStoreRestarted = _dictStore(tmp_path)
    dictReloaded = agentCouncilStore.fdictReloadDurableCampaigns(
        dictStoreRestarted)
    assert dictReloaded["iReloaded"] == 2
    assert agentCouncilStore.fjsonGetCampaignRecord(
        dictStoreRestarted, "campaign-1")["sQuestion"] == "first"


def test_retention_evicts_the_oldest_from_memory_and_disk(tmp_path):
    """Beyond the retained-count bound the oldest campaign is deleted."""
    dictStore = _dictStore(tmp_path, dictBounds={"iRetainedCampaignCount": 2})
    for iIndex in range(3):
        agentCouncilStore.fdictRegisterStartedCampaign(
            dictStore, _dictCampaign(f"campaign-{iIndex}"))
    assert agentCouncilStore.fjsonGetCampaignRecord(
        dictStore, "campaign-0") is None
    assert not os.path.isdir(
        os.path.join(dictStore["sDurableStoreRoot"], "campaign-0"))
    assert len(agentCouncilStore.flistSummariseCampaigns(dictStore)) == 2


def test_deletion_removes_the_campaign_and_its_directory(tmp_path):
    """Deleting a campaign discards its whole app-data directory."""
    dictStore = _dictStore(tmp_path)
    agentCouncilStore.fdictRegisterStartedCampaign(
        dictStore, _dictCampaign("campaign-1"))
    sDirectory = os.path.join(dictStore["sDurableStoreRoot"], "campaign-1")
    assert os.path.isdir(sDirectory)
    assert agentCouncilStore.fbDeleteStoredCampaign(dictStore, "campaign-1")
    assert not os.path.isdir(sDirectory)
    assert agentCouncilStore.fjsonGetCampaignRecord(
        dictStore, "campaign-1") is None


def test_events_are_sequence_numbered_with_visible_bounds(tmp_path):
    """The ring stamps sequence numbers and reports the retained bounds."""
    dictStore = _dictStore(tmp_path)
    agentCouncilStore.fdictRegisterStartedCampaign(
        dictStore, _dictCampaign("campaign-1"))
    for sKind in ("one", "two", "three"):
        agentCouncilStore.fdictAppendCampaignEvent(
            dictStore, "campaign-1", {"sKind": sKind})
    dictEvents = agentCouncilStore.fdictCollectCampaignEvents(
        dictStore, "campaign-1", 1)
    assert [dictEvent["sKind"] for dictEvent in dictEvents["listEvents"]] == (
        ["two", "three"])
    assert dictEvents["iHighestRetainedSequence"] == 3


def test_turn_ids_are_server_minted_and_monotonic(tmp_path):
    """Turn ids rise per campaign so a duplicate turn collides."""
    dictStore = _dictStore(tmp_path)
    agentCouncilStore.fdictRegisterStartedCampaign(
        dictStore, _dictCampaign("campaign-1"))
    assert agentCouncilStore.fsMintNextTurnId(dictStore, "campaign-1") == (
        "turn-1")
    assert agentCouncilStore.fsMintNextTurnId(dictStore, "campaign-1") == (
        "turn-2")
