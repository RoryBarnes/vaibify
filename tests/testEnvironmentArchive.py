"""The environment archive: what it claims, and what it refuses to claim.

Every test here drives the real gate against real files on disk. The
ones marked ``falsification`` were each proven to FAIL against a named
mutation of the guard they cover (see ``tests/falsificationRegistry``),
because a green assertion over freshly-authored code is agreement, not
evidence.
"""

import json
import os
import subprocess

import pytest

from vaibify.gui import archiveProgress
from vaibify.gui.pipelineServer import fdictBuildImageArchiveDetail
from vaibify.reproducibility import (
    imageArchive, imageDeposit, levelGates,
)
from vaibify.reproducibility.environmentSnapshot import (
    fdictCarryImageArchiveForward,
)


_S_DIGEST = "registry.example/project@sha256:" + "a" * 64
_S_ARCHITECTURE = "arm64"


def _fdictBuildRecord(**dictOverrides):
    """Return a well-formed deposit record, with any field overridden."""
    dictRecord = imageArchive.fdictBuildArchiveRecord(
        sVersionDoi="10.5281/zenodo.7000001",
        sConceptDoi="10.5281/zenodo.7000000",
        sTarballSha256="sha256:" + "b" * 64,
        iTarballBytes=861079552,
        sDepositedIso="2026-09-05T00:00:00+00:00",
        sProvenance=imageArchive.S_PROVENANCE_ORIGINAL,
        sImageDigest=_S_DIGEST,
        sArchitecture=_S_ARCHITECTURE,
        sTarballName="environment-image.tar.zst",
        sImageStreamSha256="sha256:" + "c" * 64,
    )
    dictRecord.update(dictOverrides)
    return dictRecord


def _fdictBuildEnvelope(dictRecord=None, **dictContainerOverrides):
    """Return an environment payload pinning one image."""
    dictContainer = {
        "sImageDigest": _S_DIGEST,
        "sArchitecture": _S_ARCHITECTURE,
    }
    dictContainer.update(dictContainerOverrides)
    if dictRecord is not None:
        dictContainer["dictImageArchive"] = dictRecord
    return {"dictContainer": dictContainer}


@pytest.fixture
def sProjectRepo(tmp_path):
    """A real git repository the gates can be pointed at."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    os.makedirs(os.path.join(str(tmp_path), ".vaibify"), exist_ok=True)
    return str(tmp_path)


def _fnWriteEnvelope(sProjectRepo, jsonEnvelope):
    """Write one environment.json into the project repo."""
    with open(
        os.path.join(sProjectRepo, ".vaibify", "environment.json"), "w",
    ) as fileOut:
        json.dump(jsonEnvelope, fileOut)


# ----------------------------------------------------------------------
# The Level 2 question: answering is the criterion, declining passes.
# ----------------------------------------------------------------------


def test_the_answer_is_three_way_and_declining_is_not_silence(
    sProjectRepo,
):
    """``declined`` and unanswered must not be the same state.

    The ``bAcceptBlasVariance`` trap one feature over: a boolean whose
    ``false`` meant both "I considered this and said no" and "nobody
    ever asked". A researcher who declines has met the requirement.
    """
    _fnWriteEnvelope(sProjectRepo, _fdictBuildEnvelope())
    assert levelGates.fbImageArchiveQuestionSettled({}, sProjectRepo) is False
    for sAnswer in (
        imageArchive.S_ANSWER_DECLINED,
        imageArchive.S_ANSWER_REFERENCED,
        imageArchive.S_ANSWER_ARCHIVED,
    ):
        assert levelGates.fbImageArchiveQuestionSettled(
            {"dictImageArchive": {"sAnswer": sAnswer}}, sProjectRepo,
        ) is True
    assert levelGates.fbImageArchiveQuestionSettled(
        {"dictImageArchive": {"sAnswer": "maybe"}}, sProjectRepo,
    ) is False


@pytest.mark.falsification
def test_a_host_project_is_never_asked_to_archive_an_image(sProjectRepo):
    """A project with no container must not carry a container criterion.

    Kills: asking the question of every project rather than only those
    whose envelope pins an image. A host project has no image, so the
    row would be permanently unsatisfiable and Level 2 unreachable —
    the shape that made Level 3 unreachable for every project vaibify
    built.
    """
    _fnWriteEnvelope(sProjectRepo, {"sMode": "host"})
    assert levelGates.fbImageArchiveQuestionSettled({}, sProjectRepo) is True
    listBlockers = levelGates.flistLevel2Blockers({}, sProjectRepo)
    assert not [
        dictEntry for dictEntry in listBlockers
        if dictEntry["sCriterion"] == "image-archive-unanswered"
    ]


def test_a_project_with_no_envelope_yet_is_not_asked(sProjectRepo):
    """Nothing to archive means nothing to answer about."""
    assert levelGates.fbImageArchiveQuestionSettled({}, sProjectRepo) is True


def test_a_deposit_settles_the_question_without_a_recorded_answer(
    sProjectRepo,
):
    """Having deposited is having decided.

    The deposit finishes inside a durable task whose request is long
    gone, so it holds no commit lane to persist an answer through. If
    the Level 2 criterion read only the answer key, a hub restart
    would put the row back to unanswered over an archive that exists.
    """
    _fnWriteEnvelope(
        sProjectRepo, _fdictBuildEnvelope(_fdictBuildRecord()),
    )
    assert levelGates.fbImageArchiveQuestionSettled({}, sProjectRepo) is True


# ----------------------------------------------------------------------
# The Level 3 criterion: does a matching archive exist.
# ----------------------------------------------------------------------


@pytest.mark.falsification
def test_the_level_three_criterion_never_reads_the_level_two_answer(
    sProjectRepo,
):
    """Declining must not lock Level 3 shut.

    Kills: letting the deposit criterion consult the recorded answer.
    A decline is an absent archive and nothing more — change the
    answer, deposit, and the level opens with nothing to undo. A gate
    that read the answer would refuse a project that HAD deposited
    after declining, and pass one that answered ``archived`` with no
    deposit at all.
    """
    _fnWriteEnvelope(
        sProjectRepo, _fdictBuildEnvelope(_fdictBuildRecord()),
    )
    assert levelGates.fbImageArchiveDeposited(sProjectRepo) is True
    _fnWriteEnvelope(sProjectRepo, _fdictBuildEnvelope())
    assert levelGates.fbImageArchiveDeposited(sProjectRepo) is False


@pytest.mark.falsification
def test_a_concept_doi_does_not_satisfy_the_criterion():
    """Zenodo's concept DOI always resolves to the NEWEST version.

    Kills: accepting whichever DOI the record carries. Recording the
    concept DOI silently repoints every earlier paper at whatever
    image was deposited last — the link resolves, nothing errors, and
    the archived environment is simply not the one that produced those
    numbers.
    """
    dictRecord = _fdictBuildRecord(sConceptDoi="10.5281/zenodo.7000001")
    listReasons = imageArchive.flistDescribeArchiveMismatch(
        _fdictBuildEnvelope(dictRecord),
    )
    assert listReasons
    assert "CONCEPT DOI" in listReasons[0]


@pytest.mark.falsification
def test_a_matching_digest_with_a_different_platform_is_refused():
    """A manifest-list digest spans platforms and pins none of them.

    Kills: dropping the architecture comparison, or inferring the
    platform from the digest. Normally a matching digest implies a
    matching platform — but not for a manifest list, and an archive of
    the wrong platform reproduces nothing while looking correct.
    """
    dictRecord = _fdictBuildRecord(sArchitecture="amd64")
    listReasons = imageArchive.flistDescribeArchiveMismatch(
        _fdictBuildEnvelope(dictRecord),
    )
    assert any("amd64" in sReason for sReason in listReasons)
    assert imageArchive.fbImageArchiveMatchesEnvelope(
        _fdictBuildEnvelope(dictRecord),
    ) is False


def test_a_deposit_of_another_image_is_refused():
    """The record must cover the image the envelope pins."""
    dictRecord = _fdictBuildRecord(sImageDigest="other@sha256:" + "d" * 64)
    assert imageArchive.fbImageArchiveMatchesEnvelope(
        _fdictBuildEnvelope(dictRecord),
    ) is False


def test_a_deposit_with_no_tarball_hash_binds_nothing():
    """Without the hash nothing ties the archived bytes to this project."""
    dictRecord = _fdictBuildRecord(sTarballSha256="")
    assert imageArchive.fbImageArchiveMatchesEnvelope(
        _fdictBuildEnvelope(dictRecord),
    ) is False


# ----------------------------------------------------------------------
# Unchecked is never red.
# ----------------------------------------------------------------------


@pytest.mark.falsification
def test_an_envelope_that_cannot_be_compared_is_unchecked_never_mismatched():
    """Red means DIVERGED — a claim about a deposit nobody compared.

    Kills: treating an uncomparable envelope as a mismatch. An
    envelope that records no architecture (a capture taken while the
    daemon was unreachable) has one side of the comparison missing;
    reporting that as a divergence asserts something nobody
    established, and sends the researcher to fix a deposit that may be
    perfectly good.
    """
    jsonEnvelope = _fdictBuildEnvelope(
        _fdictBuildRecord(), sArchitecture="",
    )
    with pytest.raises(LookupError):
        imageArchive.flistDescribeArchiveMismatch(jsonEnvelope)
    assert imageArchive.fsResolveArchiveState(
        jsonEnvelope, {},
    ) == imageArchive.S_STATE_UNCHECKED


def test_a_failed_deposit_is_unchecked_never_mismatched():
    """A deposit that could not run compared nothing."""
    assert imageArchive.fsResolveArchiveState(
        _fdictBuildEnvelope(), {}, sCheckState="uncheckable",
    ) == imageArchive.S_STATE_UNCHECKED


def test_a_running_deposit_moves_no_colour():
    """A deposit in flight has established nothing yet."""
    assert imageArchive.fsResolveArchiveState(
        _fdictBuildEnvelope(), {}, sCheckState="checking",
    ) == imageArchive.S_STATE_ARCHIVING


@pytest.mark.falsification
def test_closed_needs_positive_evidence_that_the_image_is_gone():
    """CLOSED says Level 3 is unreachable for this result — for good.

    Kills: reading "nobody looked" as "the image is gone". The
    presence probe is three-state, and only a probe that positively
    answered NO may close the door; an unreachable daemon is evidence
    of nothing, and announcing a permanent loss on it would be the
    worst false statement this row can make.
    """
    dictDeclined = {
        "dictImageArchive": {"sAnswer": imageArchive.S_ANSWER_DECLINED},
    }
    assert imageArchive.fsResolveArchiveState(
        _fdictBuildEnvelope(), dictDeclined,
        bPinnedImageInLocalStore=None,
    ) == imageArchive.S_STATE_NOT_ARCHIVED
    assert imageArchive.fsResolveArchiveState(
        _fdictBuildEnvelope(), dictDeclined,
        bPinnedImageInLocalStore=False,
    ) == imageArchive.S_STATE_CLOSED


def test_a_host_project_row_is_neutral_never_red():
    """A host project has no image; the row is not applicable."""
    assert imageArchive.fsResolveArchiveState(
        None, {}, bHostProject=True,
    ) == imageArchive.S_STATE_NOT_APPLICABLE


# ----------------------------------------------------------------------
# The attestation-time re-check.
# ----------------------------------------------------------------------


@pytest.mark.falsification
def test_an_image_loaded_from_the_deposit_recheck_is_vacuous(sProjectRepo):
    """Re-hashing a download against itself matches always.

    Kills: reporting the re-check as MATCHED when the local image was
    obtained by loading the deposit. The comparison would pass on
    every run and prove nothing — the same shape as rooting a rerun's
    comparison on the shadow rather than the live repository.
    """
    from vaibify.reproducibility.repoFiles import ffilesEnsureRepoFiles
    dictRecord = _fdictBuildRecord()
    _fnWriteEnvelope(sProjectRepo, _fdictBuildEnvelope(dictRecord))
    with open(
        os.path.join(
            sProjectRepo, imageArchive.S_LOADED_FROM_ARCHIVE_MARKER,
        ), "w",
    ) as fileMarker:
        fileMarker.write("")
    dictVerdict = imageDeposit.fdictRecheckArchiveAgainstLocalImage(
        ffilesEnsureRepoFiles(sProjectRepo),
        _fdictBuildEnvelope(dictRecord),
    )
    assert dictVerdict["sVerdict"] == imageArchive.S_RECHECK_VACUOUS


def test_the_recheck_compares_the_image_not_the_compressed_bytes():
    """The tarball hash depends on the codec; the image hash does not.

    zstd and gzip give different bytes for one image, and so can two
    builds of one codec — so comparing the TARBALL hash on a different
    machine would report an identical image as diverged.
    """
    dictRecord = _fdictBuildRecord()
    assert imageArchive.fdictJudgeArchiveRecheck(
        _fdictBuildEnvelope(dictRecord),
        dictRecord["sImageStreamSha256"],
    )["sVerdict"] == imageArchive.S_RECHECK_MATCHED
    assert imageArchive.fdictJudgeArchiveRecheck(
        _fdictBuildEnvelope(dictRecord),
        dictRecord["sTarballSha256"],
    )["sVerdict"] == imageArchive.S_RECHECK_DIFFERS


def test_an_absent_image_is_unavailable_never_a_difference():
    """A saveless machine compared nothing."""
    assert imageArchive.fdictJudgeArchiveRecheck(
        _fdictBuildEnvelope(_fdictBuildRecord()), "",
    )["sVerdict"] == imageArchive.S_RECHECK_UNAVAILABLE


# ----------------------------------------------------------------------
# The record survives an ordinary regeneration, and only that one.
# ----------------------------------------------------------------------


@pytest.mark.falsification
def test_a_regeneration_of_the_same_image_keeps_the_deposit_record():
    """The envelope regenerates on every Level 1 crossing.

    Kills: rebuilding ``dictContainer`` without carrying the record.
    The record is written once, at the one moment vaibify holds the
    bytes, and the next automatic regeneration would destroy it —
    dropping a project out of Level 3 for having crossed Level 1.
    """
    dictPrevious = {
        "sImageDigest": _S_DIGEST,
        "sArchitecture": _S_ARCHITECTURE,
        "dictImageArchive": _fdictBuildRecord(),
    }
    dictFresh = {
        "sImageDigest": _S_DIGEST, "sArchitecture": _S_ARCHITECTURE,
    }
    assert fdictCarryImageArchiveForward(
        dictPrevious, dictFresh,
    )["dictImageArchive"] == _fdictBuildRecord()


@pytest.mark.falsification
def test_a_regeneration_of_a_different_platform_drops_the_record():
    """Carrying on digest alone would claim a deposit nobody made.

    Kills: comparing only the digest when deciding to carry the record
    forward. A manifest-list digest names several platforms, so the
    same digest can front a different build — and the carried record
    would then assert that an image nobody deposited is archived.
    """
    dictPrevious = {
        "sImageDigest": _S_DIGEST,
        "sArchitecture": _S_ARCHITECTURE,
        "dictImageArchive": _fdictBuildRecord(),
    }
    for dictFresh in (
        {"sImageDigest": _S_DIGEST, "sArchitecture": "amd64"},
        {"sImageDigest": "other@sha256:" + "e" * 64,
         "sArchitecture": _S_ARCHITECTURE},
    ):
        assert "dictImageArchive" not in fdictCarryImageArchiveForward(
            dictPrevious, dictFresh,
        )


# ----------------------------------------------------------------------
# Referencing an existing deposit.
# ----------------------------------------------------------------------


def test_a_deposit_vaibify_cannot_read_is_refused_rather_than_trusted():
    """Zenodo metadata has no field for "which image is this"."""
    listProblems = imageArchive.flistDescribeReferenceProblems(
        {"doi": "10.5281/zenodo.7000001", "description": "A dataset."},
        "10.5281/zenodo.7000001", _S_DIGEST, _S_ARCHITECTURE,
    )
    assert listProblems
    assert "does not describe which container image" in listProblems[0]


@pytest.mark.falsification
def test_referencing_a_concept_doi_is_refused_by_the_record_it_resolves_to():
    """The resolved record's own DOI is what tells the two apart.

    Kills: accepting whatever DOI the researcher supplies. A concept
    DOI resolves to the NEWEST version, so the record that comes back
    carries a different ``doi`` than the one asked for — the only
    reliable signal, since the two strings are indistinguishable in
    shape.
    """
    dictRecord = _fdictBuildRecord()
    dictZenodo = {
        "doi": "10.5281/zenodo.7000001",
        "description": imageArchive.fdictStampDepositMetadata(
            {"sDescription": ""}, dictRecord,
        )["sDescription"],
    }
    assert imageArchive.flistDescribeReferenceProblems(
        dictZenodo, "10.5281/zenodo.7000001", _S_DIGEST, _S_ARCHITECTURE,
    ) == []
    listProblems = imageArchive.flistDescribeReferenceProblems(
        dictZenodo, "10.5281/zenodo.7000000", _S_DIGEST, _S_ARCHITECTURE,
    )
    assert listProblems
    assert "concept DOI" in listProblems[0]


# ----------------------------------------------------------------------
# The two provenance claims.
# ----------------------------------------------------------------------


@pytest.mark.falsification
def test_the_late_archive_path_claims_equivalence_not_identity():
    """``original`` and ``verified-equivalent`` are different claims.

    A deposit of the image a PASSING attestation ran under IS the
    environment that produced the results. A deposit of a DIFFERENT
    image than that one covers something that reproduced the manifest
    — good evidence, and a weaker claim. And only a passing rerun
    proves equivalence: a failed attestation naming another image has
    demonstrated nothing, so the deposit keeps the envelope's own
    claim rather than gaining one nobody earned.

    Kills: promoting the claim on any attestation, passing or not.
    """
    sRebuilt = "rebuilt@sha256:" + "f" * 64
    assert imageDeposit.fsJudgeDepositProvenance(
        _S_DIGEST, {"sStatus": "passed", "sImageDigest": _S_DIGEST},
    ) == imageArchive.S_PROVENANCE_ORIGINAL
    assert imageDeposit.fsJudgeDepositProvenance(
        _S_DIGEST, None,
    ) == imageArchive.S_PROVENANCE_ORIGINAL
    assert imageDeposit.fsJudgeDepositProvenance(
        _S_DIGEST, {"sStatus": "passed", "sImageDigest": sRebuilt},
    ) == imageArchive.S_PROVENANCE_VERIFIED_EQUIVALENT
    assert imageDeposit.fsJudgeDepositProvenance(
        _S_DIGEST, {"sStatus": "failed", "sImageDigest": sRebuilt},
    ) == imageArchive.S_PROVENANCE_ORIGINAL


def test_a_deposit_that_says_neither_covers_nothing():
    """A record with no provenance makes no claim at all."""
    assert imageArchive.fbImageArchiveMatchesEnvelope(
        _fdictBuildEnvelope(_fdictBuildRecord(sProvenance="")),
    ) is False


# ----------------------------------------------------------------------
# The row payload, built the way production builds it.
# ----------------------------------------------------------------------


def test_the_row_payload_reports_the_state_and_its_reasons(sProjectRepo):
    """The poll ships the verdict; the row renders it, never re-derives it."""
    _fnWriteEnvelope(
        sProjectRepo,
        _fdictBuildEnvelope(_fdictBuildRecord(sArchitecture="amd64")),
    )
    archiveProgress.fnForgetDeposit("cid-row")
    dictDetail = fdictBuildImageArchiveDetail(
        {}, sProjectRepo, "cid-row",
    )
    assert dictDetail["sState"] == imageArchive.S_STATE_MISMATCHED
    assert any(
        "amd64" in sIssue for sIssue in dictDetail["listIssues"]
    )
    assert dictDetail["dictRecord"]["sVersionDoi"] == (
        "10.5281/zenodo.7000001"
    )


def test_a_live_deposit_reaches_the_row(sProjectRepo):
    """A silent multi-minute upload is indistinguishable from a hang."""
    _fnWriteEnvelope(sProjectRepo, _fdictBuildEnvelope())
    archiveProgress.fnRegisterDeposit("cid-live", None)
    archiveProgress.fnRecordProgress(
        "cid-live", archiveProgress.S_PHASE_SAVING, 1024, 4096,
    )
    try:
        dictDetail = fdictBuildImageArchiveDetail(
            {}, sProjectRepo, "cid-live",
        )
        assert dictDetail["sState"] == imageArchive.S_STATE_ARCHIVING
        assert dictDetail["dictDeposit"]["iBytesRead"] == 1024
    finally:
        archiveProgress.fnForgetDeposit("cid-live")


# ----------------------------------------------------------------------
# The poll's own adapter, which refuses most of what it is asked.
# ----------------------------------------------------------------------


def test_the_gate_survives_the_polls_fail_closed_adapter():
    """The poll passes `SnapshotRepoFiles`, which raises on the rest.

    A permissive hand-written double answers any path; the adapter the
    poll actually passes answers only the paths one container exec
    sampled and raises ``KeyError`` for everything else. That
    difference has already shipped a defect once — a Level 3 gate
    raised on a path no fixture used, the poll answered 500, and every
    badge and level cell on the dashboard blanked. So this drives the
    real one.

    Both halves matter. The gate must READ the envelope through it
    (`.vaibify/environment.json` is sampled WITH its body, so the
    answer is the same one the readiness route computes), and it must
    not reach for anything else.
    """
    from vaibify.reproducibility.repoFiles import (
        SnapshotRepoFiles, TUPLE_SNAPSHOT_CONTENT_PATHS,
        TUPLE_SNAPSHOT_SKIP_TEXT_PATHS,
    )
    assert ".vaibify/environment.json" in TUPLE_SNAPSHOT_CONTENT_PATHS
    assert ".vaibify/environment.json" not in TUPLE_SNAPSHOT_SKIP_TEXT_PATHS

    dictRecord = _fdictBuildRecord()
    filesSnapshot = SnapshotRepoFiles(
        "/workspace/project",
        {".vaibify/environment.json": {
            "bIsFile": True,
            "sText": json.dumps(_fdictBuildEnvelope(dictRecord)),
        }},
        {},
    )
    assert levelGates.fbImageArchiveQuestionSettled(
        {}, filesSnapshot,
    ) is True
    assert levelGates.fbImageArchiveDeposited(filesSnapshot) is True
    assert levelGates.flistDescribeImageArchiveIssues(filesSnapshot) == []


def test_an_unarchived_project_also_survives_that_adapter():
    """The refusing branch reads the same file and nothing else."""
    from vaibify.reproducibility.repoFiles import SnapshotRepoFiles
    filesSnapshot = SnapshotRepoFiles(
        "/workspace/project",
        {".vaibify/environment.json": {
            "bIsFile": True,
            "sText": json.dumps(_fdictBuildEnvelope()),
        }},
        {},
    )
    assert levelGates.fbImageArchiveQuestionSettled(
        {}, filesSnapshot,
    ) is False
    assert levelGates.fbImageArchiveDeposited(filesSnapshot) is False


def test_both_blocker_lists_emit_their_criterion_through_that_adapter():
    """The criteria reach the dashboard, not just the unit gate.

    Driven through `SnapshotRepoFiles` populated the way one poll's
    exec populates it, because that is the object the poll passes and
    a permissive double cannot exercise its refusals. A gate that
    raised here would 500 the poll and blank every badge, which is how
    this class of defect has shipped before.
    """
    from vaibify.reproducibility.repoFiles import (
        SnapshotRepoFiles, TUPLE_SNAPSHOT_CONTENT_PATHS,
    )
    dictFiles = {
        sPath: {"bIsFile": False, "sText": None}
        for sPath in TUPLE_SNAPSHOT_CONTENT_PATHS
    }
    dictFiles[".vaibify/environment.json"] = {
        "bIsFile": True, "sText": json.dumps(_fdictBuildEnvelope()),
    }
    filesSnapshot = SnapshotRepoFiles(
        "/workspace/project", dictFiles, {},
    )
    setLevel2 = {
        dictEntry["sCriterion"]
        for dictEntry in levelGates.flistLevel2Blockers(
            {"listSteps": []}, filesSnapshot,
        )
    }
    setLevel3 = {
        dictEntry["sCriterion"]
        for dictEntry in levelGates.flistLevel3Blockers(
            {"listSteps": []}, filesSnapshot, False,
        )
    }
    assert "image-archive-unanswered" in setLevel2
    assert "image-not-archived" in setLevel3


def test_the_header_cell_counts_the_criteria_the_rows_emit():
    """A criterion the tuple omits is invisible to the header.

    The Project header intersects the live blocker list against a
    fixed tuple, so a criterion the gates emit but the tuple does not
    carry is silently dropped and the cell over-reports — a check
    painted above the orange rows that disagree with it.
    """
    assert "image-archive-unanswered" in (
        levelGates._T_WORKFLOW_LEVEL2_BASE_CRITERIA
    )
    assert "image-not-archived" in (
        levelGates._T_WORKFLOW_LEVEL3_CRITERIA
    )


@pytest.mark.falsification
def test_the_upload_phase_is_reported_before_the_bytes_go_up(
    monkeypatch, tmp_path,
):
    """The save's byte counter stops moving the moment the upload starts.

    From a laptop the upload is the longer half, and a row that keeps
    saying "saving" through it is the silent multi-minute stretch this
    progress record exists to prevent.

    Kills: dropping the upload-started callback from the deposit.
    """
    listEvents = []

    class _ClientRecordingOrder:
        def fdictCreateDraft(self, dictMetadata):
            del dictMetadata
            return {"id": 7, "links": {"bucket": "https://zenodo.example/b"}}

        def fnUploadToBucket(self, sBucketUrl, sTarballPath):
            del sBucketUrl
            listEvents.append(("upload", sTarballPath))

        def fdictPublishDraft(self, iDepositId):
            del iDepositId
            return {
                "doi": "10.5281/zenodo.7000001",
                "conceptdoi": "10.5281/zenodo.7000000",
            }

    sTarballPath = str(tmp_path / "environment-image.tar.zst")

    def ftSaveWithoutADaemon(sReference, sScratch, fnReportProgress=None):
        del sReference, sScratch, fnReportProgress
        return (sTarballPath, "sha256:" + "b" * 64, 4096, "sha256:" + "c" * 64)

    monkeypatch.setattr(
        imageDeposit, "ftSaveAndCompressImage", ftSaveWithoutADaemon,
    )
    dictRecord = imageDeposit.fdictDepositImageArchive(
        _ClientRecordingOrder(), _S_DIGEST, _S_ARCHITECTURE, str(tmp_path),
        {"sTitle": "image"},
        fnReportUploadStarted=lambda iBytes: listEvents.append(
            ("upload-started", iBytes),
        ),
    )
    assert listEvents == [("upload-started", 4096), ("upload", sTarballPath)]
    assert dictRecord["sVersionDoi"] == "10.5281/zenodo.7000001"


@pytest.mark.falsification
def test_a_failed_deposit_leaves_the_row_the_state_the_envelope_earns(
    sProjectRepo,
):
    """Nothing was deposited, so the row is still NOT ARCHIVED.

    Red, with the reason beside it. Painting a failed attempt UNCHECKED
    turns a red row grey, which reads as an improvement over the
    attempt that failed -- and UNCHECKED means "nothing could be
    compared", which is false here: the envelope is comparable and
    simply has no deposit.

    Kills: mapping a FAILED deposit onto the uncheckable state.
    """
    _fnWriteEnvelope(sProjectRepo, _fdictBuildEnvelope())
    archiveProgress.fnRecordFailure("cid-failed", "the upload was refused")
    try:
        dictDetail = fdictBuildImageArchiveDetail(
            {}, sProjectRepo, "cid-failed",
        )
        assert dictDetail["sState"] == imageArchive.S_STATE_NOT_ARCHIVED
        assert dictDetail["dictDeposit"]["sPhase"] == (
            archiveProgress.S_PHASE_FAILED
        )
        assert "refused" in dictDetail["dictDeposit"]["sReason"]
    finally:
        archiveProgress.fnForgetDeposit("cid-failed")


@pytest.mark.falsification
def test_the_presence_probe_reads_the_envelope_through_the_container_adapter(
    sProjectRepo, monkeypatch,
):
    """``sProjectRepoPath`` is a CONTAINER path; a host adapter over it finds nothing.

    The probe feeds CLOSED, the strongest statement the row makes. Read
    through a host adapter the pin comes back empty, the probe records
    None, and CLOSED is unreachable for every container project --
    while a test handing the same string to a host adapter passes.

    Kills: reading the envelope from the raw path rather than through
    the context's repo-files adapter.
    """
    from vaibify.gui import pipelineServer
    from vaibify.reproducibility import environmentSnapshot
    from vaibify.reproducibility.repoFiles import HostRepoFiles
    _fnWriteEnvelope(sProjectRepo, _fdictBuildEnvelope())
    listAsked = []

    def fbRecordTheProbe(sReference):
        listAsked.append(sReference)
        return False

    monkeypatch.setattr(
        environmentSnapshot, "fbImageExistsLocally", fbRecordTheProbe,
    )
    dictCtx = {"files": lambda sContainerId: HostRepoFiles(sProjectRepo)}
    pipelineServer._fnCapturePinnedImagePresence(
        dictCtx, "cid-probe",
        {"sProjectRepoPath": "/workspace/a-path-that-exists-in-no-host"},
    )
    assert listAsked == [_S_DIGEST]
    assert pipelineServer.fbPinnedImageIsInLocalStore(
        dictCtx, "cid-probe",
    ) is False


# ----------------------------------------------------------------------
# An absent deposit is a FACT, not an unchecked comparison
# ----------------------------------------------------------------------


@pytest.mark.falsification
def test_no_deposit_answers_not_archived_even_with_no_architecture():
    """Absence needs no comparison, so it is not reported as unchecked.

    The digest and architecture pair used to be required before the
    record was looked for at all, so a project whose envelope pinned
    an image but recorded no architecture reported UNCHECKED when the
    honest answer -- available one branch below, and unreachable --
    was that no archive had been deposited. The row rendered a grey
    "?" and the Level 3 cell followed it, so a researcher who had
    just declined watched Level 2 go green and Level 3 stay a
    question mark (researcher-reported, 2026-09-08).

    This does not weaken "unchecked is never red": that rule forbids
    claiming DIVERGENCE with one side missing, and "no deposit was
    made" is not a divergence claim. Its twin below is the half that
    must not move.

    Kills: restoring the combined
    ``if not sEnvelopeDigest or not sEnvelopeArchitecture`` guard
    ahead of the record lookup.
    """
    listReasons = imageArchive.flistDescribeArchiveMismatch(
        {"dictContainer": {"sImageDigest": _S_DIGEST}},
    )
    assert listReasons == [
        "No image archive has been deposited for this envelope.",
    ]


@pytest.mark.falsification
def test_a_deposit_with_no_architecture_is_still_unchecked():
    """The comparison half of the same function must NOT move.

    Once a deposit exists the question becomes whether it covers this
    envelope, and that is a comparison -- so a missing architecture
    leaves one side absent and the answer is unchecked, never a
    reason. Reporting a difference here would be a claim about the
    deposit that nobody earned, and a manifest-list digest spans
    several platforms and pins none of them, so the digest agreeing
    is not evidence the build does.

    Kills: moving the architecture guard below the comparison, or
    dropping it so ``_flistCompareRecordToEnvelope`` is handed an
    empty architecture.
    """
    dictEnvelope = {"dictContainer": {
        "sImageDigest": _S_DIGEST,
        "dictImageArchive": _fdictBuildRecord(),
    }}
    with pytest.raises(LookupError):
        imageArchive.flistDescribeArchiveMismatch(dictEnvelope)


@pytest.mark.falsification
def test_the_row_payload_reports_which_answer_was_recorded(sProjectRepo):
    """The form shows the answer back, so the poll has to carry it.

    `bAnswered` cannot serve: it is the gate's boolean verdict, true
    for a deposit record carrying no recorded answer at all, and it
    never says WHICH of the three was chosen. Without `sAnswer` the
    radios render blank on every open and a researcher who declined
    cannot tell a saved answer from a save that failed.

    Kills: dropping `sAnswer` from `fdictBuildImageArchiveDetail`.
    """
    _fnWriteEnvelope(sProjectRepo, _fdictBuildEnvelope())
    archiveProgress.fnForgetDeposit("cid-answer")
    dictDetail = fdictBuildImageArchiveDetail(
        {imageArchive.S_IMAGE_ARCHIVE_KEY: {
            "sAnswer": imageArchive.S_ANSWER_DECLINED,
        }},
        sProjectRepo, "cid-answer",
    )
    assert dictDetail["sAnswer"] == imageArchive.S_ANSWER_DECLINED


@pytest.mark.falsification
def test_the_row_payload_answers_level_two_with_the_gate(sProjectRepo):
    """`bAnswered` IS the gate's verdict, pinned as a relationship.

    The row renders two cells from one payload and the Level 2 cell
    must not be re-derived in JavaScript -- a mirrored predicate is a
    second authority on a question that has one, which is how the
    Reproducibility-rules row came to paint green over a refusing
    gate. Asserting the relationship rather than a literal is what
    keeps them from drifting apart.

    Kills: computing `bAnswered` from the deposit record or the row
    state instead of calling `fbImageArchiveQuestionSettled`.
    """
    _fnWriteEnvelope(sProjectRepo, _fdictBuildEnvelope())
    archiveProgress.fnForgetDeposit("cid-gate")
    from vaibify.reproducibility.repoFiles import ffilesEnsureRepoFiles
    for dictWorkflow in (
        {},
        {imageArchive.S_IMAGE_ARCHIVE_KEY: {
            "sAnswer": imageArchive.S_ANSWER_DECLINED,
        }},
    ):
        dictDetail = fdictBuildImageArchiveDetail(
            dictWorkflow, sProjectRepo, "cid-gate",
        )
        assert dictDetail["bAnswered"] == (
            levelGates.fbImageArchiveQuestionSettled(
                dictWorkflow, ffilesEnsureRepoFiles(sProjectRepo),
            )
        )
    # The two workflows must actually DISAGREE, or the loop above is
    # satisfied by a constant.
    assert fdictBuildImageArchiveDetail(
        {}, sProjectRepo, "cid-gate",
    )["bAnswered"] is False


@pytest.mark.falsification
def test_the_row_payload_carries_why_nothing_could_be_compared(
    sProjectRepo,
):
    """The unchecked reason is shipped, and kept apart from the issues.

    ``flistDescribeImageArchiveIssues`` returns [] for this state on
    purpose, so that a comparison nobody could make can never be
    rendered as a divergence -- and that discards the only actionable
    sentence there is. A researcher whose envelope recorded no
    architecture met a grey "?" with an empty issue list and nothing
    to act on.

    Both halves are asserted because either alone would be wrong:
    ``listIssues`` must stay EMPTY (no differences were found), and
    the reason must be PRESENT (something is missing and here it is).

    Kills: dropping ``sUncheckedReason`` from
    ``fdictBuildImageArchiveDetail``, or folding the reason into
    ``listIssues``.
    """
    _fnWriteEnvelope(sProjectRepo, {"dictContainer": {
        "sImageDigest": _S_DIGEST,
        "dictImageArchive": _fdictBuildRecord(),
    }})
    archiveProgress.fnForgetDeposit("cid-unchecked")
    dictDetail = fdictBuildImageArchiveDetail(
        {}, sProjectRepo, "cid-unchecked",
    )
    assert dictDetail["listIssues"] == []
    assert "architecture" in dictDetail["sUncheckedReason"], (
        "the unchecked state ships no reason: "
        + repr(dictDetail["sUncheckedReason"])
    )


def test_a_comparable_envelope_ships_no_unchecked_reason(sProjectRepo):
    """The other direction: a reason present here would be a lie.

    Something WAS compared, so there is no "could not look" to
    report, and a stale reason sitting beside real issues would tell
    the researcher to regenerate an envelope that is fine.
    """
    _fnWriteEnvelope(sProjectRepo, _fdictBuildEnvelope())
    archiveProgress.fnForgetDeposit("cid-comparable")
    dictDetail = fdictBuildImageArchiveDetail(
        {}, sProjectRepo, "cid-comparable",
    )
    assert dictDetail["sUncheckedReason"] == ""
    assert dictDetail["listIssues"] != []
