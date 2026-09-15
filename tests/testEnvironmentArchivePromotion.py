"""Promoting the environment archive: order, bytes, and what stays put.

Three properties this module exists to hold, each of which passed
against a wrong implementation at some point in the design:

* **Policy before credentials before bytes.** A refusal vaibify can
  reach from what it already holds must not first read a secret across
  the container boundary, and must never first spend minutes on
  ``docker save``.
* **The fallback fires on ABSENCE, not on failure.** A ``docker save``
  fails for a stopped daemon, a full disk or a compression error, and
  substituting archived bytes for any of those hides a local problem
  the researcher should be told about.
* **Nothing about the project's configuration moves.** The deposit
  record carries its own instance and ``reproduce.sh`` reads it at run
  time, so the promotion needs no flip -- and a flip would send every
  retained sandbox record in ``listRecords`` to zenodo.org and abort
  the whole Zenodo verify.
"""

import pytest

from vaibify.reproducibility import (
    archivePermanence, archivePromotion, environmentSnapshot, imageArchive,
)


_S_DIGEST = "registry.example/project@sha256:" + "a" * 64
_S_ARCHITECTURE = "arm64"


def _fdictBuildRecord(sService="sandbox", **dictOverrides):
    dictRecord = imageArchive.fdictBuildArchiveRecord(
        sVersionDoi="10.5072/zenodo.7000001",
        sConceptDoi="10.5072/zenodo.7000000",
        sTarballSha256="sha256:" + "b" * 64,
        iTarballBytes=861079552,
        sDepositedIso="2026-09-05T00:00:00+00:00",
        sProvenance=imageArchive.S_PROVENANCE_ORIGINAL,
        sImageDigest=_S_DIGEST,
        sArchitecture=_S_ARCHITECTURE,
        sTarballName="environment-image.tar.zst",
        sImageStreamSha256="sha256:" + "c" * 64,
        sZenodoService=sService,
    )
    dictRecord.update(dictOverrides)
    return dictRecord


def _fdictBuildEnvelope(dictRecord=None, **dictOverrides):
    dictContainer = {
        "sImageDigest": _S_DIGEST, "sArchitecture": _S_ARCHITECTURE,
    }
    dictContainer.update(dictOverrides)
    if dictRecord is not None:
        dictContainer[imageArchive.S_IMAGE_ARCHIVE_KEY] = dictRecord
    return {"dictContainer": dictContainer}


# ── The refusals, before any byte ──


def test_a_permanent_record_is_refused_by_name():
    """The route checks; it does not rely on the button's absence."""
    with pytest.raises(archivePromotion.PromotionRefusedError) as error:
        archivePromotion.fnRefuseUnlessSandboxDeposit(
            _fdictBuildRecord("zenodo"), "The environment archive",
        )
    assert "already on production" in str(error.value)


def test_an_unclassifiable_record_is_refused_rather_than_guessed():
    """Promoting on a guess is the mirror of claiming permanence on one."""
    with pytest.raises(archivePromotion.PromotionRefusedError):
        archivePromotion.fnRefuseUnlessSandboxDeposit(
            {"sVersionDoi": "", "sZenodoService": ""},
            "The environment archive",
        )


def test_a_sandbox_record_passes_the_refusal():
    archivePromotion.fnRefuseUnlessSandboxDeposit(
        _fdictBuildRecord("sandbox"), "The environment archive",
    )


def test_an_invalid_token_is_refused_before_any_byte_is_saved():
    """Existence is not validity, and the save is the expensive half.

    A token that was revoked, or pasted with a character missing,
    fails at the draft -- which on this lane is AFTER a multi-gigabyte
    ``docker save``. The save is made to RAISE if it is reached, so
    this fails rather than passes when the check is moved after it.
    """
    from vaibify.reproducibility import zenodoClient

    class _RejectingClient:
        sService = "zenodo"

        def flistSearchDeposits(self, sQuery):
            raise zenodoClient.ZenodoAuthError("401 Unauthorized")

    with pytest.raises(archivePromotion.PromotionRefusedError) as error:
        archivePromotion.fnRefuseUnlessTokenValidates(_RejectingClient())
    assert "rejected the stored production token" in str(error.value)


def test_a_working_token_passes_the_check():
    class _AcceptingClient:
        sService = "zenodo"

        def flistSearchDeposits(self, sQuery):
            return []

    archivePromotion.fnRefuseUnlessTokenValidates(_AcceptingClient())


def test_the_route_validates_the_token_before_reaching_docker_save():
    """Order, read off the handler: policy, credentials, then bytes."""
    from vaibify.gui.routes import environmentArchiveRoutes
    sSource = open(environmentArchiveRoutes.__file__).read()
    iStart = sSource.index("async def fdictPromoteEnvironmentArchive")
    sHandler = sSource[iStart:sSource.index(
        "def _fnValidateProductionToken",
    )]
    iEnvelope = sHandler.index("_fdictRequireEnvelopeContainerBlock")
    iSandbox = sHandler.index("_fnRequireSandboxImageDeposit")
    iToken = sHandler.index("_fsReadProductionTokenFromContainer")
    iValidate = sHandler.index("_fnValidateProductionToken")
    iLaunch = sHandler.index("_fdictLaunchPromotionDurably")
    assert iEnvelope < iSandbox < iToken < iValidate < iLaunch, (
        "the promotion reads a secret, or starts the save, before a "
        "refusal it could have reached from what it already held"
    )


# ── The byte source ──


def test_the_local_image_is_used_when_the_daemon_still_holds_it(
    monkeypatch,
):
    monkeypatch.setattr(
        archivePromotion, "fsChooseImageByteSource",
        archivePromotion.fsChooseImageByteSource,
    )
    monkeypatch.setattr(
        environmentSnapshot, "fbImageExistsLocally", lambda sRef: True,
    )
    assert archivePromotion.fsChooseImageByteSource(
        _S_DIGEST, _fdictBuildEnvelope(_fdictBuildRecord()),
    ) == archivePromotion.S_BYTES_FROM_LOCAL_IMAGE


def test_an_undetermined_daemon_refuses_rather_than_falling_back(
    monkeypatch,
):
    """``None`` is "nobody could look", which is evidence of nothing.

    Falling back here would substitute archived bytes for a stopped
    daemon -- a local problem the researcher should be told about.
    """
    monkeypatch.setattr(
        environmentSnapshot, "fbImageExistsLocally", lambda sRef: None,
    )
    with pytest.raises(archivePromotion.PromotionRefusedError) as error:
        archivePromotion.fsChooseImageByteSource(
            _S_DIGEST, _fdictBuildEnvelope(_fdictBuildRecord()),
        )
    assert "could not ask Docker" in str(error.value)


def test_the_fallback_needs_the_record_to_cover_this_envelope(
    monkeypatch,
):
    """The fallback's bytes have no claim to identity but the record's.

    Promoting a mismatched record would spend a permanent DOI on a
    deposit that can never satisfy the gate.
    """
    monkeypatch.setattr(
        environmentSnapshot, "fbImageExistsLocally", lambda sRef: False,
    )
    dictEnvelope = _fdictBuildEnvelope(
        _fdictBuildRecord(sImageDigest="registry.example/p@sha256:" + "f" * 64),
    )
    with pytest.raises(archivePromotion.PromotionRefusedError) as error:
        archivePromotion.fsChooseImageByteSource(_S_DIGEST, dictEnvelope)
    assert "cannot stand in for it" in str(error.value)


def test_the_fallback_fires_for_a_matching_record_and_an_absent_image(
    monkeypatch,
):
    monkeypatch.setattr(
        environmentSnapshot, "fbImageExistsLocally", lambda sRef: False,
    )
    assert archivePromotion.fsChooseImageByteSource(
        _S_DIGEST, _fdictBuildEnvelope(_fdictBuildRecord()),
    ) == archivePromotion.S_BYTES_FROM_ARCHIVE


# ── What the promotion leaves alone ──


def test_the_promotion_writes_no_project_configuration():
    """Grep-level, because the absence is the property.

    Flipping top-level ``sZenodoService`` would send every retained
    sandbox record in ``listRecords`` to zenodo.org, 404 them, and
    abort the whole Zenodo verify -- over a promotion that had nothing
    to do with them.
    """
    sSource = open(archivePromotion.__file__).read()
    assert "sZenodoService" not in sSource, (
        "the promotion module writes or reads a project-level Zenodo "
        "instance; the deposit record carries its own"
    )
    from vaibify.gui.routes import environmentArchiveRoutes
    sRoutes = open(environmentArchiveRoutes.__file__).read()
    iStart = sRoutes.index("def _fnRegisterPromoteEnvironmentArchive")
    assert 'dictWorkflow["sZenodoService"]' not in sRoutes[iStart:]


def test_the_superseded_note_survives_a_regeneration():
    """A note that evaporates at the next regeneration is worse than none."""
    dictPrevious = {
        "sImageDigest": _S_DIGEST, "sArchitecture": _S_ARCHITECTURE,
        "dictImageArchive": _fdictBuildRecord("zenodo"),
        environmentSnapshot.S_SUPERSEDED_ARCHIVE_KEY:
            _fdictBuildRecord("sandbox"),
    }
    dictFresh = {
        "sImageDigest": _S_DIGEST, "sArchitecture": _S_ARCHITECTURE,
    }
    dictCarried = environmentSnapshot.fdictCarryImageArchiveForward(
        dictPrevious, dictFresh,
    )
    assert dictCarried[environmentSnapshot.S_SUPERSEDED_ARCHIVE_KEY][
        "sZenodoService"] == "sandbox"
    assert dictCarried["dictImageArchive"]["sZenodoService"] == "zenodo"


def test_a_disagreeing_capture_drops_both_records_together():
    """Neither describes this envelope any more."""
    dictPrevious = {
        "sImageDigest": _S_DIGEST, "sArchitecture": _S_ARCHITECTURE,
        "dictImageArchive": _fdictBuildRecord("zenodo"),
        environmentSnapshot.S_SUPERSEDED_ARCHIVE_KEY:
            _fdictBuildRecord("sandbox"),
    }
    dictFresh = {
        "sImageDigest": _S_DIGEST, "sArchitecture": "amd64",
    }
    dictCarried = environmentSnapshot.fdictCarryImageArchiveForward(
        dictPrevious, dictFresh,
    )
    assert "dictImageArchive" not in dictCarried
    assert environmentSnapshot.S_SUPERSEDED_ARCHIVE_KEY not in dictCarried


def test_the_superseded_note_is_never_read_by_a_gate():
    """It is a note. No criterion may consult it."""
    dictEnvironment = _fdictBuildEnvelope(
        None, **{
            environmentSnapshot.S_SUPERSEDED_ARCHIVE_KEY:
                _fdictBuildRecord("sandbox"),
        },
    )
    assert imageArchive.flistDescribeArchiveMismatch(dictEnvironment) == [
        "No image archive has been deposited for this envelope.",
    ]
    assert archivePermanence.fsClassifyDepositRecord(
        imageArchive.fdictReadArchiveRecord(dictEnvironment),
    ) == archivePermanence.S_PERMANENCE_UNKNOWN
