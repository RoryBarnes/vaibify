"""Recovering a promotion: ask Zenodo, and let the answer decide.

A promotion mints a permanent DOI in the middle of a long upload, so
the interesting failures all happen in a window where vaibify knows
what it INTENDED and nothing about what landed. Every property here
follows from refusing to guess in that window:

* Seven outcomes, each offering only its own actions. Collapsing any
  pair loses a remedy.
* ``gone`` and ``unknown`` are different. A 404 is an answer; a
  timeout is not, and discarding on one throws away the only handle on
  a DOI that may exist.
* Adoption matches on the recorded MD5 and size, never on filenames —
  a flat Zenodo deposit makes filenames ambiguous, and a same-size
  different file is exactly what a name check calls a match.
"""

import pytest

from vaibify.reproducibility import archivePromotion, zenodoClient


def _fdictIntendedRecord(**dictOverrides):
    dictRecord = archivePromotion.fdictBuildPendingPromotion(
        "promotion-1", archivePromotion.S_LANE_IMAGE, "zenodo",
        [{"sBasename": "environment-image.tar.zst",
          "sSha256": "sha256:" + "b" * 64,
          "sMd5": "d" * 32, "iBytes": 4096}],
    )
    dictRecord["iDepositId"] = 4242
    dictRecord.update(dictOverrides)
    return dictRecord


def _fdictDeposit(sState="inprogress", listFiles=None, **dictOverrides):
    dictDeposit = {"state": sState, "files": listFiles or []}
    dictDeposit.update(dictOverrides)
    return dictDeposit


def _listMatchingFiles():
    return [{
        "filename": "environment-image.tar.zst",
        "checksum": "md5:" + "d" * 32,
        "filesize": 4096,
    }]


def test_an_empty_draft_is_resumable_and_offers_only_discard():
    """Resume needs bytes the interruption did not preserve.

    Offering resume here would mean re-producing the bytes and
    uploading those instead, which makes the draft hold something
    whose hashes no longer match the record reconciliation compares
    against — "upload whatever is there now" wearing a hash check.
    """
    dictOutcome = archivePromotion.fdictReconcilePromotion(
        _fdictIntendedRecord(), lambda iId: _fdictDeposit(),
    )
    assert dictOutcome["sOutcome"] == archivePromotion.S_OUTCOME_RESUMABLE
    assert dictOutcome["listActions"] == ["discard"]


def test_a_complete_draft_is_publishable_and_offers_resume():
    """The case the lane exists for: upload finished, publish did not."""
    dictOutcome = archivePromotion.fdictReconcilePromotion(
        _fdictIntendedRecord(),
        lambda iId: _fdictDeposit(listFiles=_listMatchingFiles()),
    )
    assert dictOutcome["sOutcome"] == (
        archivePromotion.S_OUTCOME_PUBLISHABLE
    )
    assert dictOutcome["listActions"] == ["resume", "discard"]


def test_a_published_record_offers_only_adoption():
    dictOutcome = archivePromotion.fdictReconcilePromotion(
        _fdictIntendedRecord(),
        lambda iId: _fdictDeposit(
            "done", _listMatchingFiles(),
            doi="10.5281/zenodo.4242", conceptdoi="10.5281/zenodo.4241",
        ),
    )
    assert dictOutcome["sOutcome"] == archivePromotion.S_OUTCOME_PUBLISHED
    assert dictOutcome["listActions"] == ["adopt"]
    assert dictOutcome["sDoi"] == "10.5281/zenodo.4242"


def test_a_same_size_different_file_is_mismatched_not_published():
    """The case a filename-only check would call a match."""
    dictOutcome = archivePromotion.fdictReconcilePromotion(
        _fdictIntendedRecord(),
        lambda iId: _fdictDeposit("done", [{
            "filename": "environment-image.tar.zst",
            "checksum": "md5:" + "e" * 32,
            "filesize": 4096,
        }]),
    )
    assert dictOutcome["sOutcome"] == (
        archivePromotion.S_OUTCOME_MISMATCHED
    )
    assert dictOutcome["listActions"] == []


def test_a_404_is_an_answer_and_licenses_a_discard():
    def _fdictRaiseNotFound(iDepositId):
        raise zenodoClient.ZenodoNotFoundError("404")

    dictOutcome = archivePromotion.fdictReconcilePromotion(
        _fdictIntendedRecord(), _fdictRaiseNotFound,
    )
    assert dictOutcome["sOutcome"] == archivePromotion.S_OUTCOME_GONE
    assert dictOutcome["listActions"] == ["discard"]


@pytest.mark.falsification
def test_an_unreadable_zenodo_keeps_the_record_and_offers_nothing():
    """A question nobody answered is not a "no".

    Kills: treating any fetch failure as absence — a timeout would
    then license discarding the only handle on a DOI that may exist.
    """
    def _fdictRaiseTimeout(iDepositId):
        raise zenodoClient.ZenodoError("connection timed out")

    dictOutcome = archivePromotion.fdictReconcilePromotion(
        _fdictIntendedRecord(), _fdictRaiseTimeout,
    )
    assert dictOutcome["sOutcome"] == archivePromotion.S_OUTCOME_UNKNOWN
    assert dictOutcome["listActions"] == []


def test_zenodos_own_error_state_offers_neither():
    dictOutcome = archivePromotion.fdictReconcilePromotion(
        _fdictIntendedRecord(),
        lambda iId: _fdictDeposit("error", _listMatchingFiles()),
    )
    assert dictOutcome["sOutcome"] == archivePromotion.S_OUTCOME_ERRORED
    assert dictOutcome["listActions"] == []


def test_a_record_naming_no_deposit_never_asks_zenodo():
    """Nothing remote was created, so nothing remote can be asked."""
    listAsked = []
    dictOutcome = archivePromotion.fdictReconcilePromotion(
        _fdictIntendedRecord(iDepositId=0),
        lambda iId: listAsked.append(iId) or _fdictDeposit(),
    )
    assert listAsked == []
    assert dictOutcome["sOutcome"] == archivePromotion.S_OUTCOME_GONE


def test_every_outcome_is_reachable_and_has_its_own_action_set():
    """Collapsing any pair of outcomes loses a remedy."""
    setOutcomes = {
        archivePromotion.S_OUTCOME_RESUMABLE,
        archivePromotion.S_OUTCOME_PUBLISHABLE,
        archivePromotion.S_OUTCOME_PUBLISHED,
        archivePromotion.S_OUTCOME_MISMATCHED,
        archivePromotion.S_OUTCOME_GONE,
        archivePromotion.S_OUTCOME_ERRORED,
        archivePromotion.S_OUTCOME_UNKNOWN,
    }
    assert len(setOutcomes) == 7
    for sOutcome in setOutcomes:
        assert isinstance(
            archivePromotion.flistOfferedActionsFor(sOutcome), list,
        )
    assert archivePromotion.flistOfferedActionsFor("nonsense") == []


def test_the_promotion_id_rides_in_the_remote_description():
    """Otherwise a writable local file is the only thing binding
    a remote mutation to the intent that started it."""
    sDescription = archivePromotion.fsStampPromotionIdIntoDescription(
        "An image deposit.", "promotion-1",
    )
    assert archivePromotion.fbDescriptionCarriesPromotionId(
        sDescription, "promotion-1",
    ) is True
    assert archivePromotion.fbDescriptionCarriesPromotionId(
        sDescription, "promotion-2",
    ) is False
    assert archivePromotion.fbDescriptionCarriesPromotionId(
        sDescription, "",
    ) is False


def test_the_image_lane_and_the_project_lane_write_different_files():
    """One adoption path per lane, and neither may touch the other's."""
    from vaibify.gui.routes import promotionRecoveryRoutes
    sSource = open(promotionRecoveryRoutes.__file__).read()
    iProject = sSource.index("def _fnAdoptProjectDeposit")
    iImage = sSource.index("def _fnAdoptImageDeposit")
    sProject = sSource[iProject:iImage]
    sImage = sSource[iImage:sSource.index("def _fdictBuildAdoptedImage")]
    assert "environment" not in sProject.lower()
    assert "fdictCommitWorkflowSave" not in sImage
    assert "fdictStampArchiveRecord" in sImage


# ── The routes' guards, driven directly ──


class _FakeDocker:
    def fsFetchKeyringSecret(self, sContainerId, sSlot):
        return "a-token"


class _FakeClient:
    def __init__(self, dictDeposit):
        self._dictDeposit = dictDeposit
        self.listDeleted = []

    def fdictGetDeposit(self, iDepositId):
        if self._dictDeposit is None:
            raise zenodoClient.ZenodoNotFoundError("404")
        return self._dictDeposit

    def fnDeleteDraft(self, iDepositId):
        self.listDeleted.append(iDepositId)


def _fnPatchClient(monkeypatch, clientFake):
    from vaibify.gui.routes import promotionRecoveryRoutes
    monkeypatch.setattr(
        promotionRecoveryRoutes, "_fclientForPromotion",
        lambda dictCtx, sId, dictRecord, dictWorkflow: clientFake,
    )


def test_an_action_is_refused_when_the_remote_does_not_name_it(
    monkeypatch,
):
    """A writable local file must not be the only thing binding a
    remote mutation to the intent that started it."""
    from vaibify.gui.routes import promotionRecoveryRoutes
    clientFake = _FakeClient({
        "state": "inprogress", "files": _listMatchingFiles(),
        "metadata": {"description": "Somebody else's deposit."},
    })
    _fnPatchClient(monkeypatch, clientFake)
    with pytest.raises(Exception) as error:
        promotionRecoveryRoutes._ftRequireSettleableDeposit(
            {}, "container", _fdictIntendedRecord(), {},
            (archivePromotion.S_OUTCOME_PUBLISHABLE,),
        )
    assert "does not name this promotion" in str(error.value.detail)


def test_an_action_passes_when_the_remote_names_it(monkeypatch):
    from vaibify.gui.routes import promotionRecoveryRoutes
    clientFake = _FakeClient({
        "state": "inprogress", "files": _listMatchingFiles(),
        "metadata": {"description":
                     "An image.\n\nvaibify-promotion: promotion-1"},
    })
    _fnPatchClient(monkeypatch, clientFake)
    _client, dictOutcome = (
        promotionRecoveryRoutes._ftRequireSettleableDeposit(
            {}, "container", _fdictIntendedRecord(), {},
            (archivePromotion.S_OUTCOME_PUBLISHABLE,),
        )
    )
    assert dictOutcome["sOutcome"] == (
        archivePromotion.S_OUTCOME_PUBLISHABLE
    )


@pytest.mark.falsification
def test_discard_refuses_a_published_record_and_an_unreadable_one(
    monkeypatch, tmp_path,
):
    """Discarding either throws away something nobody may throw away.

    Kills: widening the discard route's allowed outcomes to include
    ``published`` or ``unknown`` — the first destroys a real DOI's
    record, the second answers a question nobody asked with a "no".
    """
    from vaibify.gui.routes import promotionRecoveryRoutes
    for dictDeposit in (
        {"state": "done", "files": _listMatchingFiles(),
         "doi": "10.5281/zenodo.4242", "conceptdoi": ""},
        None,
    ):
        clientFake = _FakeClient(dictDeposit)
        if dictDeposit is None:
            # An unreadable Zenodo, not a 404: the outcome is unknown.
            clientFake.fdictGetDeposit = _fnRaiseUnreadable
        _fnPatchClient(monkeypatch, clientFake)
        with pytest.raises(Exception):
            promotionRecoveryRoutes._fnDiscardDraftAndRecord(
                {}, "container", {}, str(tmp_path), "project.json",
                _fdictIntendedRecord(),
            )
        assert clientFake.listDeleted == []


def _fnRaiseUnreadable(iDepositId):
    raise zenodoClient.ZenodoError("connection timed out")
