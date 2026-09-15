"""A deposit is finished when the ARCHIVE agrees, not when the upload returns.

Everything before that point is vaibify reporting on vaibify: the
sha256 in the record is what the local file hashed to, and a truncated
upload, a dropped byte range, or a publish that stored a different
object would all leave the record saying exactly what it says now. The
row would go green over an archive that cannot satisfy it — the one
outcome the whole ladder exists to prevent.

The check is seconds, not minutes, and that is the point worth
keeping: Zenodo computes each file's MD5 **server-side** and publishes
it, so agreement is a statement about the bytes Zenodo holds, made by
Zenodo. No re-download is needed. That is also why the record carries
an MD5 beside its sha256 — without it the archive can be asked what it
holds and the answer compared to nothing.

What it does not prove is retrievability. That needs a real download,
it takes minutes, and conflating the two would let a seconds-long
check wear a claim it has not earned.
"""

import pytest

from vaibify.gui import archiveProgress
from vaibify.reproducibility import imageArchive, imageDeposit


_S_MD5 = "d" * 32
_S_NAME = "environment-image.tar.zst"


def _fdictRecord(**dictOverrides):
    dictRecord = imageArchive.fdictBuildArchiveRecord(
        sVersionDoi="10.5281/zenodo.1", sConceptDoi="10.5281/zenodo.0",
        sTarballSha256="sha256:" + "b" * 64, iTarballBytes=4096,
        sDepositedIso="", sProvenance=imageArchive.S_PROVENANCE_ORIGINAL,
        sImageDigest="img@sha256:" + "c" * 64, sArchitecture="arm64",
        sTarballName=_S_NAME, sTarballMd5=_S_MD5,
    )
    dictRecord.update(dictOverrides)
    return dictRecord


def _fdictDeposit(sMd5=_S_MD5, iBytes=4096, sName=_S_NAME):
    return {"files": [{
        "key": sName, "checksum": "md5:" + sMd5, "filesize": iBytes,
    }]}


def test_an_agreeing_archive_reports_nothing():
    assert imageDeposit.flistDescribeArchiveDisagreement(
        _fdictDeposit(), _fdictRecord(),
    ) == []


@pytest.mark.falsification
def test_a_same_size_different_file_is_caught():
    """The substitution a size-only check would wave through.

    Kills: comparing only ``filesize`` — the archive would then agree
    with the record over an object of the right length and the wrong
    bytes, which is precisely what a checksum exists to detect.
    """
    listProblems = imageDeposit.flistDescribeArchiveDisagreement(
        _fdictDeposit(sMd5="e" * 32), _fdictRecord(),
    )
    assert len(listProblems) == 1
    assert "md5" in listProblems[0]


def test_a_truncated_upload_is_caught():
    listProblems = imageDeposit.flistDescribeArchiveDisagreement(
        _fdictDeposit(iBytes=2048), _fdictRecord(),
    )
    assert any("bytes" in sProblem for sProblem in listProblems)


def test_a_deposit_serving_no_such_file_is_caught():
    """A flat deposit makes the NAME meaningless on its own."""
    listProblems = imageDeposit.flistDescribeArchiveDisagreement(
        _fdictDeposit(sName="something-else.tar"), _fdictRecord(),
    )
    assert len(listProblems) == 1
    assert "serves no file named" in listProblems[0]


def test_the_check_raises_rather_than_warning():
    """Green over an unsatisfiable archive is worse than no deposit."""
    class _FakeClient:
        def fdictGetDeposit(self, iDepositId):
            return _fdictDeposit(sMd5="e" * 32)

    with pytest.raises(imageDeposit.ArchiveVerificationError) as error:
        imageDeposit.fnRefuseUnlessArchiveHoldsWhatWeSent(
            _FakeClient(), 1, _fdictRecord(),
        )
    assert "does not hold what vaibify uploaded" in str(error.value)


def test_a_record_with_no_md5_is_not_refused():
    """The upgrade path: records predating the field are legal.

    Making absence a fault would turn every existing green row red,
    which is the trap the permanence classifier had to avoid too.
    """
    class _FakeClient:
        def fdictGetDeposit(self, iDepositId):
            raise AssertionError("the archive must not be asked")

    imageDeposit.fnRefuseUnlessArchiveHoldsWhatWeSent(
        _FakeClient(), 1, _fdictRecord(sTarballMd5=""),
    )


@pytest.mark.falsification
def test_a_disagreeing_archive_costs_a_draft_and_never_a_doi(tmp_path):
    """The check runs BEFORE the publish, and the ordering is the point.

    Verifying afterwards would mean raising with a DOI already minted:
    an orphan the researcher owns, that vaibify refused to record, and
    that nothing on this path could clean up — a published record
    cannot be discarded. Checked in front of the publish, the same
    disagreement costs a discardable draft and mints nothing.

    Kills: dropping the ``fnRefuseUnlessArchiveHoldsWhatWeSent`` call,
    OR moving it back after ``fdictPublishDraft`` — the first reports
    success over an archive nobody asked, the second orphans a DOI.
    """
    pathTarball = tmp_path / _S_NAME
    pathTarball.write_bytes(b"some bytes")
    listPhases = []

    class _FakeClient:
        sService = "zenodo"

        def fdictCreateDraft(self, dictMetadata):
            return {"id": 7, "links": {"bucket": "https://example/b"}}

        def fnUploadToBucket(self, sBucketUrl, sPath):
            listPhases.append("upload")

        def fdictPublishDraft(self, iDepositId):
            listPhases.append("published")
            return {"doi": "10.5281/zenodo.1", "conceptdoi": ""}

        def fnDeleteDraft(self, iDepositId):
            listPhases.append("discarded")

        def fdictGetDeposit(self, iDepositId):
            listPhases.append("asked")
            # The archive reports something else entirely.
            return _fdictDeposit(sMd5="e" * 32)

    with pytest.raises(imageDeposit.ArchiveVerificationError) as error:
        imageDeposit.fdictUploadAndPublishImageArchive(
            _FakeClient(), "img@sha256:" + "c" * 64, "arm64",
            {"sTitle": "A project"},
            (str(pathTarball), "sha256:" + "b" * 64, 10,
             "sha256:" + "c" * 64, _S_MD5),
            fnReportVerifying=lambda: listPhases.append("verifying"),
        )
    assert listPhases == ["upload", "verifying", "asked", "discarded"], (
        "the draft must be asked before it is published, and "
        "discarded when it disagrees"
    )
    assert "published" not in listPhases, "a DOI was minted anyway"
    # The refusal names the thing on Zenodo, because one a researcher
    # cannot trace to a deposit is one they cannot act on.
    assert "7" in str(error.value)
    assert "nothing was published" in str(error.value)


def test_the_verifying_phase_keeps_the_row_pulsing():
    """A multi-second silence at the end reads as a hang."""
    assert archiveProgress.S_PHASE_VERIFYING in (
        archiveProgress._T_LIVE_PHASES
    )


# ── The failure modes md5 actually has, and none of them is collisions ──


def test_an_unparseable_checksum_reads_as_unchecked_not_mismatched():
    """The check must not become an outage over a format question.

    Zenodo publishes ``md5:<hex>`` today. An object store reporting a
    composite checksum (S3's multipart ETag is ``<hex>-<partcount>``),
    or Zenodo changing algorithm, would otherwise make EVERY deposit
    refuse — the verification turning into a failure about something
    it merely could not read. Unchecked is never red; the size
    comparison still stands, and sha256 remains what a download
    verifies.
    """
    for sChecksum in (
        "sha256:" + "a" * 64, "d" * 32 + "-4", "", None, "md5:zzzz",
    ):
        dictDeposit = {"files": [{
            "key": _S_NAME, "checksum": sChecksum, "filesize": 4096,
        }]}
        assert imageDeposit.flistDescribeArchiveDisagreement(
            dictDeposit, _fdictRecord(),
        ) == [], sChecksum


def test_an_unparseable_checksum_still_checks_the_size():
    """Abstaining on one comparison must not abandon the other."""
    dictDeposit = {"files": [{
        "key": _S_NAME, "checksum": "etag-not-a-digest", "filesize": 1,
    }]}
    listProblems = imageDeposit.flistDescribeArchiveDisagreement(
        dictDeposit, _fdictRecord(),
    )
    assert len(listProblems) == 1
    assert "bytes" in listProblems[0]


def test_both_digests_come_from_one_read(tmp_path):
    """Two reads describe two files if the file changes between them.

    The md5 is what the archive is asked to confirm, so a second-pass
    md5 would let the archive confirm bytes the recorded sha256 does
    not describe — the check passing while the integrity claim names
    something nobody uploaded.
    """
    from vaibify.reproducibility._hashing import (
        fsHashFileSha256, ftHashFileSha256AndMd5,
    )
    pathFile = tmp_path / "bytes.bin"
    pathFile.write_bytes(b"some bytes to hash")
    sSha256, sMd5 = ftHashFileSha256AndMd5(str(pathFile))
    assert sSha256 == fsHashFileSha256(str(pathFile))
    assert len(sMd5) == 32
    import hashlib
    assert sMd5 == hashlib.md5(  # noqa: S324 -- the value under test
        b"some bytes to hash",
    ).hexdigest()


def test_the_md5_is_requested_without_the_security_flag():
    """``hashlib.md5()`` RAISES on a FIPS-enabled host.

    Several institutional Linux builds run FIPS mode, where the
    unflagged constructor raises ValueError from OpenSSL. The deposit
    would then die on a researcher's own cluster and nowhere in
    testing — a portability failure, not a cryptographic one, and the
    only md5 failure mode that actually applies here.
    """
    import inspect
    from vaibify.reproducibility import _hashing
    sSource = inspect.getsource(_hashing.ftHashFileSha256AndMd5)
    assert "usedforsecurity=False" in sSource
