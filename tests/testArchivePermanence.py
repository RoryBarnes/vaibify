"""A permanence claim needs positive evidence; abstention is free.

Zenodo's sandbox mints test DOIs and keeps no preservation promise, so
"this deposit is a permanent archive" is a claim vaibify must earn. The
classifier therefore has three states and not two: ``unknown`` renders
exactly as today and passes every gate, which is why abstaining costs a
researcher nothing while a wrong ``permanent`` costs them the warning
the feature exists to show.

The two traps this module exists to catch:

* deferring to ``zenodoClient.fsServiceForDoi`` wholesale. It is TOTAL
  by design -- its caller must pick a host, so it answers ``"zenodo"``
  for an empty string, a malformed DOI, and a foreign one. Believing
  its *sandbox* answer is right; believing its production answer turns
  "not recognizably a sandbox DOI" into "permanently archived".
* letting the upgrade path change an existing row. A deposit record
  written before ``sZenodoService`` existed is legal, and making its
  absence a mismatch reason would turn green rows red on upgrade.
"""

import pytest

from vaibify.reproducibility import archivePermanence, imageArchive, zenodoClient


S_SANDBOX_DOI = "10.5072/zenodo.4242"
S_PRODUCTION_DOI = "10.5281/zenodo.4242"


def test_a_recorded_service_beats_the_doi_prefix_in_both_directions():
    """The record says where it lives; the string is only a fallback.

    Both directions, because a classifier that merely read the DOI
    would pass the first assertion by accident.
    """
    assert archivePermanence.fsClassifyDeposit(
        "sandbox", S_PRODUCTION_DOI,
    ) == archivePermanence.S_PERMANENCE_SANDBOX
    assert archivePermanence.fsClassifyDeposit(
        "zenodo", S_SANDBOX_DOI,
    ) == archivePermanence.S_PERMANENCE_PERMANENT


def test_a_service_vaibify_cannot_read_abstains():
    """A recorded-but-unknown instance is not guessed around."""
    assert archivePermanence.fsClassifyDeposit(
        "zenodo-next", S_PRODUCTION_DOI,
    ) == archivePermanence.S_PERMANENCE_UNKNOWN


def test_the_sandbox_fallback_is_delegated_not_copied(monkeypatch):
    """One ``10.5072/`` constant, in ``zenodoClient``, and no other.

    Driven by monkeypatching the delegate: a second copy of the prefix
    here would keep answering ``sandbox`` and fail this.
    """
    monkeypatch.setattr(
        archivePermanence.zenodoClient, "fsServiceForDoi",
        lambda sDoi: "sandbox",
    )
    assert archivePermanence.fsClassifyDeposit(
        "", S_PRODUCTION_DOI,
    ) == archivePermanence.S_PERMANENCE_SANDBOX


@pytest.mark.parametrize("sDoi", [
    "", "   ", "not-a-doi", "10.9999/dryad.123", "10.9999/notzenodo.123",
    "zenodo.123",
])
def test_nothing_weaker_than_a_zenodo_doi_claims_permanence(sDoi):
    """``fsServiceForDoi`` answers ``zenodo`` for every one of these."""
    assert zenodoClient.fsServiceForDoi(sDoi) == "zenodo"
    assert archivePermanence.fsClassifyDeposit(
        "", sDoi,
    ) == archivePermanence.S_PERMANENCE_UNKNOWN


def test_both_lanes_spell_the_same_two_fields_differently():
    """The image lane and the project lane use different key names.

    Each fixture makes the recorded instance DISAGREE with the DOI
    prefix, so a classifier that read only the other lane's spelling
    would fall through to the DOI and answer the opposite.
    """
    assert archivePermanence.fsClassifyDepositRecord({
        "sVersionDoi": S_PRODUCTION_DOI, "sZenodoService": "sandbox",
    }) == archivePermanence.S_PERMANENCE_SANDBOX
    assert archivePermanence.fsClassifyDepositRecord({
        "sDoi": S_PRODUCTION_DOI, "sService": "sandbox",
    }) == archivePermanence.S_PERMANENCE_SANDBOX
    assert archivePermanence.fsClassifyDepositRecord({
        "sDoi": S_SANDBOX_DOI, "sService": "zenodo",
    }) == archivePermanence.S_PERMANENCE_PERMANENT


def test_a_record_predating_the_service_field_classifies_by_prefix():
    assert archivePermanence.fsClassifyDepositRecord({
        "sVersionDoi": S_SANDBOX_DOI,
    }) == archivePermanence.S_PERMANENCE_SANDBOX
    assert archivePermanence.fsClassifyDepositRecord({
        "sDoi": S_PRODUCTION_DOI,
    }) == archivePermanence.S_PERMANENCE_PERMANENT
    assert archivePermanence.fsClassifyDepositRecord(
        None,
    ) == archivePermanence.S_PERMANENCE_UNKNOWN


def test_a_record_without_a_service_gains_no_mismatch_reason():
    """The upgrade path: no existing green row may turn red.

    ``sZenodoService`` arrived after the record shape did. Permanence
    is reported beside the comparison, never folded into it.
    """
    dictEnvironment = {"dictContainer": {
        "sImageDigest": "sha256:" + "ab" * 32,
        "sArchitecture": "arm64",
        imageArchive.S_IMAGE_ARCHIVE_KEY: {
            "sVersionDoi": S_PRODUCTION_DOI,
            "sConceptDoi": "10.5281/zenodo.4241",
            "sTarballName": "image.tar.gz",
            "sTarballSha256": "sha256:" + "cd" * 32,
            "iTarballBytes": 4096,
            "sProvenance": imageArchive.S_PROVENANCE_ORIGINAL,
            "sImageDigest": "sha256:" + "ab" * 32,
            "sArchitecture": "arm64",
        },
    }}
    assert imageArchive.flistDescribeArchiveMismatch(dictEnvironment) == []
