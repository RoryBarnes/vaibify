"""The record that has to survive the crash it exists for.

A promotion mints a permanent DOI in the middle of a long upload. If
the process dies with the DOI minted and nothing written down, the DOI
cannot be recovered by guessing -- so the record has to survive two
things a naive implementation loses it to:

* **An ordinary workflow save.** ``syncBookkeeping`` extracts and
  merges a CLOSED key set, so a sidecar key the contract does not know
  is silently erased by the next save of any workflow field.
* **A concurrent settlement.** Serializing the whole workflow to
  settle one promotion would let a save revert the settlement and a
  settlement revert the save. The update is therefore narrow, and
  takes the same per-file lock the ordinary save takes.
"""

import json
import os

import pytest

from vaibify.reproducibility import archivePromotion, syncBookkeeping
from vaibify.reproducibility.repoFiles import ffilesEnsureRepoFiles


S_WORKFLOW_KEY = ".vaibify/projects/project.json"


@pytest.fixture
def filesRepo(tmp_path):
    os.makedirs(str(tmp_path / ".vaibify"), exist_ok=True)
    return ffilesEnsureRepoFiles(str(tmp_path))


def _fdictBuildRecord(sPromotionId="promotion-1"):
    return archivePromotion.fdictBuildPendingPromotion(
        sPromotionId, archivePromotion.S_LANE_IMAGE, "zenodo",
        [{"sBasename": "image.tar.zst", "sSha256": "sha256:" + "b" * 64,
          "sMd5": "d" * 32, "iBytes": 4096}],
    )


def test_a_pending_record_survives_an_ordinary_workflow_save(filesRepo):
    """The closed key set is the trap: an unnamed key is erased."""
    dictRecord = _fdictBuildRecord()
    syncBookkeeping.fnUpdatePendingPromotion(
        filesRepo, S_WORKFLOW_KEY, dictRecord["sPromotionId"], dictRecord,
    )
    # An ordinary save: the definition is extracted and the sidecar
    # section rewritten from what the merged workflow carries.
    dictWorkflow = {"listSteps": [], "sZenodoLatestDoi": "10.5281/zenodo.1"}
    syncBookkeeping.fnMergeSyncBookkeepingIntoWorkflow(
        dictWorkflow,
        syncBookkeeping.fdictReadSyncBookkeeping(filesRepo, S_WORKFLOW_KEY),
    )
    assert dictWorkflow["listPendingPromotions"], (
        "the load path dropped the record, so the save cannot carry it"
    )
    syncBookkeeping.fnWriteSyncBookkeeping(
        filesRepo, S_WORKFLOW_KEY,
        syncBookkeeping.fdictExtractSyncBookkeeping(dict(dictWorkflow)),
    )
    listPending = syncBookkeeping.flistReadPendingPromotions(
        filesRepo, S_WORKFLOW_KEY,
    )
    assert [d["sPromotionId"] for d in listPending] == ["promotion-1"]


def test_a_save_and_a_settlement_do_not_lose_each_other(filesRepo):
    """Each writes its own concern; neither reverts the other's."""
    dictRecord = _fdictBuildRecord()
    syncBookkeeping.fnUpdatePendingPromotion(
        filesRepo, S_WORKFLOW_KEY, "promotion-1", dictRecord,
    )
    syncBookkeeping.fnWriteSyncBookkeeping(
        filesRepo, S_WORKFLOW_KEY,
        {"dictSyncStatus": {"paper.tex": {"bZenodo": True}},
         "listPendingPromotions": [dictRecord]},
    )
    # The draft is created while that save is in flight: the deposit
    # id must land without the whole section being rewritten.
    syncBookkeeping.fnUpdatePendingPromotion(
        filesRepo, S_WORKFLOW_KEY, "promotion-1",
        {"iDepositId": 4242, "sPhase": archivePromotion.S_PHASE_DRAFTED},
    )
    dictSection = syncBookkeeping.fdictReadSyncBookkeeping(
        filesRepo, S_WORKFLOW_KEY,
    )
    assert dictSection["dictSyncStatus"]["paper.tex"]["bZenodo"] is True
    assert dictSection["listPendingPromotions"][0]["iDepositId"] == 4242
    assert dictSection["listPendingPromotions"][0]["sPhase"] == "drafted"


def test_settling_removes_only_its_own_record(filesRepo):
    for sId in ("promotion-1", "promotion-2"):
        dictRecord = _fdictBuildRecord(sId)
        syncBookkeeping.fnUpdatePendingPromotion(
            filesRepo, S_WORKFLOW_KEY, sId, dictRecord,
        )
    syncBookkeeping.fnRemovePendingPromotion(
        filesRepo, S_WORKFLOW_KEY, "promotion-1",
    )
    listPending = syncBookkeeping.flistReadPendingPromotions(
        filesRepo, S_WORKFLOW_KEY,
    )
    assert [d["sPromotionId"] for d in listPending] == ["promotion-2"]
    syncBookkeeping.fnRemovePendingPromotion(
        filesRepo, S_WORKFLOW_KEY, "promotion-2",
    )
    assert syncBookkeeping.flistReadPendingPromotions(
        filesRepo, S_WORKFLOW_KEY,
    ) == []


def test_a_file_is_described_in_zenodos_vocabulary_too(tmp_path):
    """Zenodo reports MD5 and size, so reconciliation needs both."""
    pathFile = tmp_path / "environment-image.tar.zst"
    pathFile.write_bytes(b"some bytes")
    dictFile = archivePromotion.fdictDescribeFileForPromotion(str(pathFile))
    assert dictFile["sBasename"] == "environment-image.tar.zst"
    assert dictFile["sSha256"].startswith("sha256:")
    assert len(dictFile["sMd5"]) == 32
    assert dictFile["iBytes"] == len(b"some bytes")


def test_the_record_timestamps_in_utc_not_monotonic():
    """Monotonic time is meaningless across the restart this survives."""
    dictRecord = _fdictBuildRecord()
    assert dictRecord["sStartedIso"].endswith("+00:00")
    assert dictRecord["sPhase"] == archivePromotion.S_PHASE_INTENDED
    assert dictRecord["iDepositId"] == 0


def test_the_promotion_id_is_unique_per_promotion():
    assert (archivePromotion.fsGeneratePromotionId()
            != archivePromotion.fsGeneratePromotionId())
