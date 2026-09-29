"""A changed environment image is archived as the next version of its record.

A project's environment was deposited as a fresh Zenodo record every
time, so an author who rebuilt and re-archived ended up with unrelated
records for what is one environment's history. The envelope now keeps
a lineage note when the image changes -- the old record no longer
covers the envelope, so the record itself is dropped -- and the next
deposit on the same Zenodo service is made a new version of it. Across
services, which Zenodo cannot version, it stays a fresh record.
"""

import pytest

from vaibify.reproducibility import imageDeposit
from vaibify.reproducibility.environmentSnapshot import (
    S_ARCHIVE_LINEAGE_KEY,
    fdictArchiveLineageOf,
    fdictCarryImageArchiveForward,
)


_S_MD5 = "d" * 32
_S_NAME = "environment-image.tar.zst"
_S_OLD_VERSION_DOI = "10.5281/zenodo.22758870"
_S_CONCEPT_DOI = "10.5281/zenodo.22758869"


def _fdictPreviousContainer():
    return {
        "sImageDigest": "img@sha256:" + "a" * 64, "sArchitecture": "amd64",
        "dictImageArchive": {
            "sVersionDoi": _S_OLD_VERSION_DOI, "sConceptDoi": _S_CONCEPT_DOI,
            "sZenodoService": "zenodo", "sTarballName": _S_NAME,
        },
    }


class _FakeZenodo:
    """Records every call; serves a draft whose files the test chooses."""

    def __init__(self, sService="zenodo", listLeftoverFiles=()):
        self.sService = sService
        self.listCalls = []
        self.listLeftoverFiles = list(listLeftoverFiles)

    def fdictCreateDraft(self, dictMetadata):
        self.listCalls.append(("create",))
        return {"id": 9, "links": {"bucket": "https://example/fresh"}}

    def fdictGetNewVersionDraft(self, iParent):
        self.listCalls.append(("newversion", iParent))
        return {"id": 11, "links": {"bucket": "https://example/next"}}

    def fnClearDraftFiles(self, iDepositId):
        self.listCalls.append(("clear", iDepositId))

    def fnSetMetadata(self, iDepositId, dictMetadata):
        self.listCalls.append(("metadata", iDepositId))

    def fnUploadToBucket(self, sBucketUrl, sPath, fnReportProgress=None):
        self.listCalls.append(("upload", sBucketUrl))

    def fdictGetDeposit(self, iDepositId):
        listFiles = [{"key": _S_NAME, "checksum": "md5:" + _S_MD5,
                      "filesize": 10}]
        listFiles += [{"key": sName, "checksum": "md5:" + "e" * 32,
                       "filesize": 5} for sName in self.listLeftoverFiles]
        return {"files": listFiles}

    def fdictPublishDraft(self, iDepositId):
        self.listCalls.append(("publish", iDepositId))
        return {"doi": "10.5281/zenodo.30000001", "conceptdoi": _S_CONCEPT_DOI}

    def fnDeleteDraft(self, iDepositId):
        self.listCalls.append(("discard", iDepositId))


def _fdictDeposit(tmp_path, clientZenodo, dictParent):
    pathTarball = tmp_path / _S_NAME
    pathTarball.write_bytes(b"some bytes")
    return imageDeposit.fdictUploadAndPublishImageArchive(
        clientZenodo, "img@sha256:" + "b" * 64, "amd64",
        {"sTitle": "A project"},
        (str(pathTarball), "sha256:" + "b" * 64, 10,
         "sha256:" + "c" * 64, _S_MD5),
        dictParentArchive=dictParent,
    )


@pytest.mark.falsification
def test_a_changed_image_keeps_the_record_it_was_archived_under():
    """Kills: dropping the lineage with the record when the image changes."""
    dictFresh = {"sImageDigest": "img@sha256:" + "b" * 64,
                 "sArchitecture": "amd64"}
    dictCarried = fdictCarryImageArchiveForward(
        _fdictPreviousContainer(), dictFresh,
    )
    assert "dictImageArchive" not in dictCarried, (
        "the old record does not cover the new image and must not stay"
    )
    assert dictCarried[S_ARCHIVE_LINEAGE_KEY] == {
        "sVersionDoi": _S_OLD_VERSION_DOI, "sConceptDoi": _S_CONCEPT_DOI,
        "sZenodoService": "zenodo",
    }
    dictAgain = fdictCarryImageArchiveForward(
        dictCarried, {"sImageDigest": "img@sha256:" + "f" * 64,
                      "sArchitecture": "amd64"},
    )
    assert dictAgain[S_ARCHIVE_LINEAGE_KEY]["sVersionDoi"] == (
        _S_OLD_VERSION_DOI
    ), "a second change lost the note the first one kept"


@pytest.mark.falsification
def test_a_new_image_on_the_same_service_is_a_new_version(tmp_path):
    """Kills: always opening a fresh record, whatever the lineage says."""
    clientZenodo = _FakeZenodo("zenodo")
    dictRecord = _fdictDeposit(
        tmp_path, clientZenodo,
        fdictArchiveLineageOf(_fdictPreviousContainer()),
    )
    assert clientZenodo.listCalls[:3] == [
        ("newversion", 22758870), ("clear", 11), ("metadata", 11),
    ], clientZenodo.listCalls
    assert ("create",) not in clientZenodo.listCalls
    assert dictRecord["sConceptDoi"] == _S_CONCEPT_DOI
    assert dictRecord["sVersionDoi"] == "10.5281/zenodo.30000001"


def test_a_lineage_on_the_other_service_starts_a_fresh_record(tmp_path):
    """Sandbox and production cannot version each other's records."""
    clientZenodo = _FakeZenodo("sandbox")
    _fdictDeposit(
        tmp_path, clientZenodo,
        fdictArchiveLineageOf(_fdictPreviousContainer()),
    )
    assert clientZenodo.listCalls[0] == ("create",)
    assert imageDeposit.fiParentDepositIdOnService({}, "zenodo") == 0


@pytest.mark.falsification
def test_a_new_version_still_holding_the_old_image_is_never_published(tmp_path):
    """Kills: skipping the leftover-file check on a new-version draft.

    A new-version draft inherits the previous image. A clear that did
    not happen would publish a version holding both, and every check of
    the file vaibify sent would still pass.
    """
    clientZenodo = _FakeZenodo("zenodo", ["environment-image-old.tar.zst"])
    with pytest.raises(imageDeposit.ArchiveVerificationError):
        _fdictDeposit(
            tmp_path, clientZenodo,
            fdictArchiveLineageOf(_fdictPreviousContainer()),
        )
    assert ("discard", 11) in clientZenodo.listCalls
    assert not any(t[0] == "publish" for t in clientZenodo.listCalls)
