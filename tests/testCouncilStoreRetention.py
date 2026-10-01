"""Retention deletes the oldest SETTLED campaign, never a live or accepted one.

After a hub restart the store reloads campaigns from disk, and it used to
reload them in directory order, which is alphabetical by random UUID. The
next registration then evicted whichever campaign sorted first: an
effectively random one, including an accepted plan or a campaign still
running, bypassing the delete route's guards.
"""

import os

import pytest

from vaibify.gui import agentCouncilStore

I_RETAINED = 2


def _dictCampaign(sCampaignId, sState):
    return {
        "sCampaignId": sCampaignId,
        "sState": sState,
        "sQuestion": "a question",
        "listParticipants": [{"sParticipantId": "p1"}],
        "listRounds": [],
    }


def _dictStore(tmp_path):
    return agentCouncilStore.fdictCreateCampaignStore(
        sDurableStoreRoot=str(tmp_path / "agentCouncils"),
        dictBounds={"iRetainedCampaignCount": I_RETAINED})


def _fnAgeCampaignRecord(dictStore, sCampaignId, fSecondsAgo):
    sRecord = os.path.join(
        dictStore["sDurableStoreRoot"], sCampaignId,
        agentCouncilStore.S_CAMPAIGN_RECORD_BASENAME)
    fWhen = os.path.getmtime(sRecord) - fSecondsAgo
    os.utime(sRecord, (fWhen, fWhen))


def _dictRestartedStoreHolding(tmp_path, listCampaignAgesAndStates):
    """Persist campaigns with set ages, then reload them in a new store."""
    dictFirst = _dictStore(tmp_path)
    dictFirst["dictBounds"]["iRetainedCampaignCount"] = 99
    for sCampaignId, fSecondsAgo, sState in listCampaignAgesAndStates:
        agentCouncilStore.fdictRegisterStartedCampaign(
            dictFirst, _dictCampaign(sCampaignId, sState))
        _fnAgeCampaignRecord(dictFirst, sCampaignId, fSecondsAgo)
    dictRestarted = _dictStore(tmp_path)
    agentCouncilStore.fdictReloadDurableCampaigns(dictRestarted)
    return dictRestarted


@pytest.mark.falsification
def testAfterARestartTheOldestCheckpointIsEvictedNotTheFirstAlphabetically(
    tmp_path,
):
    """Kills: reloading in directory order, so retention evicts at random."""
    dictStore = _dictRestartedStoreHolding(tmp_path, [
        ("aaa-newest", 10, "failed"),
        ("mmm-middle", 500, "failed"),
        ("zzz-oldest", 9000, "failed"),
    ])
    agentCouncilStore.fdictRegisterStartedCampaign(
        dictStore, _dictCampaign("registered-now", "planning"))
    setRemaining = set(dictStore["dictEntriesById"])
    assert "zzz-oldest" not in setRemaining
    assert "aaa-newest" in setRemaining


@pytest.mark.falsification
def testRetentionNeverDeletesALiveOrAcceptedCampaign(tmp_path):
    """Kills: evicting by position alone, whatever state the campaign is in."""
    dictStore = _dictStore(tmp_path)
    for sCampaignId, sState in [
        ("running", "planning"), ("waiting", "needsHuman"),
        ("accepted", "planAccepted"), ("building", "awaitingImplementation"),
        ("ready", "planReady"), ("settled", "failed"),
    ]:
        agentCouncilStore.fdictRegisterStartedCampaign(
            dictStore, _dictCampaign(sCampaignId, sState))
    setRemaining = set(dictStore["dictEntriesById"])
    assert "settled" not in setRemaining
    assert setRemaining >= {
        "running", "waiting", "accepted", "building", "ready"}
    for sCampaignId in setRemaining:
        assert os.path.isdir(os.path.join(
            dictStore["sDurableStoreRoot"], sCampaignId))


def testWhenEveryCampaignOverTheBoundIsNeededNoneIsDeleted(tmp_path):
    dictStore = _dictStore(tmp_path)
    for iIndex in range(I_RETAINED + 2):
        agentCouncilStore.fdictRegisterStartedCampaign(
            dictStore, _dictCampaign(f"live-{iIndex}", "planning"))
    assert len(dictStore["dictEntriesById"]) == I_RETAINED + 2


def testSettledCampaignsAreEvictedOldestFirstUntilTheBoundHolds(tmp_path):
    dictStore = _dictRestartedStoreHolding(tmp_path, [
        ("c-new", 10, "archived"),
        ("c-mid", 500, "interrupted"),
        ("c-old", 9000, "failed"),
        ("c-live", 99999, "planning"),
    ])
    agentCouncilStore.fdictRegisterStartedCampaign(
        dictStore, _dictCampaign("registered-now", "planning"))
    setRemaining = set(dictStore["dictEntriesById"])
    assert setRemaining == {"c-live", "registered-now"}


def testAnUnreadableRecordSortsAsTheOldest(tmp_path):
    dictStore = _dictStore(tmp_path)
    os.makedirs(os.path.join(dictStore["sDurableStoreRoot"], "no-record"))
    agentCouncilStore.fdictRegisterStartedCampaign(
        dictStore, _dictCampaign("real", "failed"))
    listOrder = agentCouncilStore._flistCampaignIdsOldestFirst(dictStore)
    assert listOrder == ["no-record", "real"]
