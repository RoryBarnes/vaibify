"""Two seams a promotion needs, and the properties that must survive them.

A promotion re-deposits bytes it did not just produce, so it needs the
upload half of the deposit without the ``docker save`` in front of it,
and the hash-checked download without the unbounded client call. Both
halves already existed inside larger functions; splitting them is only
safe if the original path is unchanged and the new entry points keep
every guard the originals had.
"""

from unittest.mock import MagicMock

import pytest

from vaibify.reproducibility import imageArchive, imageDeposit


class _FakeZenodoClient:
    """Enough of the client to drive a draft, an upload and a publish."""

    def __init__(self, sService="zenodo"):
        self.sService = sService
        self.listUploaded = []
        self.listPublished = []

    def fdictCreateDraft(self, dictMetadata):
        del dictMetadata
        return {"id": 4242, "links": {"bucket": "https://example/bucket"}}

    def fnUploadToBucket(self, sBucketUrl, sTarballPath):
        self.listUploaded.append((sBucketUrl, sTarballPath))

    def fdictPublishDraft(self, iDepositId):
        self.listPublished.append(iDepositId)
        return {
            "doi": "10.5281/zenodo.4242",
            "conceptdoi": "10.5281/zenodo.4241",
        }

    def fdictGetDeposit(self, iDepositId):
        """What the post-deposit check asks, answered honestly.

        A deposit is not finished until the archive has been asked
        what it stored, so a double that refused to answer would
        make every test here fail for the wrong reason.
        """
        return {"files": [{
            "key": "environment-image.tar.zst",
            "checksum": "md5:" + "d" * 32,
            "filesize": 19,
        }]}


def _ftBuildTarball(tmp_path):
    """Return the tuple ``ftSaveAndCompressImage`` produces."""
    pathTarball = tmp_path / "environment-image.tar.zst"
    pathTarball.write_bytes(b"not really an image")
    return (
        str(pathTarball), "sha256:" + "b" * 64, 19,
        "sha256:" + "c" * 64, "d" * 32,
    )


def test_the_upload_half_runs_against_a_tarball_already_on_disk(tmp_path):
    clientZenodo = _FakeZenodoClient()
    dictRecord = imageDeposit.fdictUploadAndPublishImageArchive(
        clientZenodo, "registry.example/p@sha256:" + "a" * 64, "arm64",
        {"sTitle": "A project"}, _ftBuildTarball(tmp_path),
    )
    assert clientZenodo.listPublished == [4242]
    assert dictRecord["sVersionDoi"] == "10.5281/zenodo.4242"
    assert dictRecord["sZenodoService"] == "zenodo"


def test_the_original_deposit_path_still_saves_before_uploading(
    tmp_path, monkeypatch,
):
    """The split must not change what the first deposit does."""
    listSaved = []

    def _ftFakeSave(sImageReference, sScratchDirectory, fnReportProgress):
        listSaved.append(sImageReference)
        return _ftBuildTarball(tmp_path)

    monkeypatch.setattr(imageDeposit, "ftSaveAndCompressImage", _ftFakeSave)
    clientZenodo = _FakeZenodoClient("sandbox")
    dictRecord = imageDeposit.fdictDepositImageArchive(
        clientZenodo, "registry.example/p@sha256:" + "a" * 64, "arm64",
        str(tmp_path), {"sTitle": "A project"},
    )
    assert listSaved == ["registry.example/p@sha256:" + "a" * 64]
    assert clientZenodo.listUploaded
    assert dictRecord["sZenodoService"] == "sandbox"


def test_provenance_is_carried_forward_rather_than_rejudged(tmp_path):
    """``original`` and ``verified-equivalent`` are distinct claims.

    A re-deposit establishes NEITHER, so it must carry the old
    record's claim forward verbatim. The natural test — promoting an
    ``original`` record — passes against the bug, because the
    judgement would answer ``original`` anyway.
    """
    clientZenodo = _FakeZenodoClient()
    dictRecord = imageDeposit.fdictUploadAndPublishImageArchive(
        clientZenodo, "registry.example/p@sha256:" + "a" * 64, "arm64",
        {"sTitle": "A project"}, _ftBuildTarball(tmp_path),
        sProvenance=imageArchive.S_PROVENANCE_VERIFIED_EQUIVALENT,
    )
    assert dictRecord["sProvenance"] == (
        imageArchive.S_PROVENANCE_VERIFIED_EQUIVALENT
    )


@pytest.mark.falsification
def test_the_deposit_id_is_reported_before_any_byte_goes_up(tmp_path):
    """A lost permanent DOI cannot be recovered by guessing.

    Kills: moving ``fnReportDraftCreated`` after the upload, or after
    the publish — the window the recovery lane exists to cover is
    exactly the upload, and an id recorded at the end covers nothing.
    """
    listEvents = []
    clientZenodo = _FakeZenodoClient()
    clientZenodo.fnUploadToBucket = lambda sUrl, sPath: listEvents.append(
        "upload",
    )
    imageDeposit.fdictUploadAndPublishImageArchive(
        clientZenodo, "registry.example/p@sha256:" + "a" * 64, "arm64",
        {"sTitle": "A project"}, _ftBuildTarball(tmp_path),
        fnReportDraftCreated=lambda iId: listEvents.append(
            "draft:" + str(iId),
        ),
    )
    assert listEvents == ["draft:4242", "upload"]


def test_the_download_seam_refuses_a_record_naming_no_hash(tmp_path):
    """The promotion's fallback must keep every guard the original had."""
    from vaibify.reproducibility import imageAcquisition
    with pytest.raises(imageAcquisition.ImageAcquisitionRefusedError):
        imageAcquisition.fsDownloadVerifiedTarball(
            {"sVersionDoi": "10.5072/zenodo.1",
             "sTarballName": "image.tar.zst"},
            str(tmp_path), MagicMock(),
        )


def test_the_download_seam_refuses_a_record_naming_no_size(tmp_path):
    """Unbounded is the one thing the shared client call would have been."""
    from vaibify.reproducibility import imageAcquisition
    with pytest.raises(imageAcquisition.ImageAcquisitionRefusedError) as error:
        imageAcquisition.fsDownloadVerifiedTarball(
            {"sVersionDoi": "10.5072/zenodo.1",
             "sTarballName": "image.tar.zst",
             "sTarballSha256": "sha256:" + "b" * 64},
            str(tmp_path), MagicMock(),
        )
    assert "bounded" in str(error.value)
