"""A sandbox deposit withholds the Level 3 credit, not the rerun.

Zenodo's sandbox mints test DOIs, promises no preservation, and may be
cleared at any time. A rebuild attestation sitting in one really did
happen -- the bytes really do match -- so this criterion withholds the
CREDIT and leaves every other claim standing. Two consequences the
tests below pin, because each passes against a different wrong
implementation:

* ``fbAtLeastLevel3`` must return False. That gate enumerates its
  conjuncts BY HAND and does not consume ``_fdictL3WorkflowChecks``, so
  a criterion added to the dict and the tuple alone would leave the
  scalar reporting the level attained above rows that block it.
* ``/level3/verify`` must stay runnable. Ruling 5: withholding the
  credit must not make the attestation unproducible, or promoting the
  archive first and attesting second -- the only honest order, since
  Zenodo versions are immutable -- would be impossible.

``unknown`` passes throughout. The criterion fails OPEN: only positive
evidence of a test instance blocks, because there is no harm it
prevents that an abstention causes.
"""

import json
import os
import subprocess

import pytest

from vaibify.reproducibility import archivePermanence, imageArchive, levelGates


_S_DIGEST = "registry.example/project@sha256:" + "a" * 64
_S_ARCHITECTURE = "arm64"


def _fdictBuildRecord(sVersionDoi, sZenodoService=None):
    """Return a well-formed deposit record for one instance."""
    dictRecord = imageArchive.fdictBuildArchiveRecord(
        sVersionDoi=sVersionDoi,
        sConceptDoi=sVersionDoi,
        sTarballSha256="sha256:" + "b" * 64,
        iTarballBytes=861079552,
        sDepositedIso="2026-09-05T00:00:00+00:00",
        sProvenance=imageArchive.S_PROVENANCE_ORIGINAL,
        sImageDigest=_S_DIGEST,
        sArchitecture=_S_ARCHITECTURE,
        sTarballName="environment-image.tar.zst",
        sImageStreamSha256="sha256:" + "c" * 64,
        sZenodoService=sZenodoService,
    )
    return dictRecord


@pytest.fixture
def sProjectRepo(tmp_path):
    """A real git repository the gates can be pointed at."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    os.makedirs(os.path.join(str(tmp_path), ".vaibify"), exist_ok=True)
    return str(tmp_path)


def _fnWriteEnvelope(sProjectRepo, dictRecord):
    """Write an environment.json carrying one deposit record."""
    with open(
        os.path.join(sProjectRepo, ".vaibify", "environment.json"), "w",
    ) as fileOut:
        json.dump({"dictContainer": {
            "sImageDigest": _S_DIGEST,
            "sArchitecture": _S_ARCHITECTURE,
            "dictImageArchive": dictRecord,
        }}, fileOut)


def test_a_sandbox_image_deposit_blocks_the_criterion(sProjectRepo):
    _fnWriteEnvelope(
        sProjectRepo, _fdictBuildRecord(
            "10.5072/zenodo.7000001", "sandbox",
        ),
    )
    assert levelGates.fbNoArchiveIsKnownSandbox({}, sProjectRepo) is False
    listIssues = levelGates.flistDescribeSandboxArchives({}, sProjectRepo)
    assert len(listIssues) == 1
    assert "environment archive" in listIssues[0]


def test_a_sandbox_project_deposit_blocks_the_criterion(sProjectRepo):
    """The project deposit is the second archive, and it is separate."""
    _fnWriteEnvelope(
        sProjectRepo, _fdictBuildRecord(
            "10.5281/zenodo.7000001", "zenodo",
        ),
    )
    # The recorded instance and the DOI prefix DISAGREE on purpose:
    # the project record spells its instance `sService`, and a gate
    # reading only the image lane's `sZenodoService` would fall
    # through to the DOI and answer permanent.
    dictWorkflow = {"dictRemotes": {"zenodo": {
        "sRecordId": "991", "sDoi": "10.5281/zenodo.991",
        "sService": "sandbox",
    }}}
    listIssues = levelGates.flistDescribeSandboxArchives(
        dictWorkflow, sProjectRepo,
    )
    assert len(listIssues) == 1
    assert "project's Zenodo deposit" in listIssues[0]


def test_a_production_pair_passes_and_an_unknown_one_does_too(
    sProjectRepo,
):
    """``unknown`` is not a soft failure; it renders and gates as today."""
    _fnWriteEnvelope(
        sProjectRepo, _fdictBuildRecord(
            "10.5281/zenodo.7000001", "zenodo",
        ),
    )
    assert levelGates.fbNoArchiveIsKnownSandbox(
        {"dictRemotes": {"zenodo": {"sRecordId": "991"}}}, sProjectRepo,
    ) is True
    _fnWriteEnvelope(sProjectRepo, _fdictBuildRecord("", ""))
    assert levelGates.fbNoArchiveIsKnownSandbox({}, sProjectRepo) is True


def test_the_criterion_is_registered_in_all_five_places(sProjectRepo):
    """Four of the five are silent when missed; the fifth is the gate.

    A criterion in the checks dict but not the tuple is dropped from
    the header count; in both but not ``fbAtLeastLevel3`` leaves the
    scalar gate reporting the level attained above rows that block it.
    """
    sCriterion = "an-archive-is-a-sandbox-deposit"
    assert sCriterion in levelGates._fdictL3WorkflowChecks({}, sProjectRepo)
    assert sCriterion in levelGates._T_WORKFLOW_LEVEL3_CRITERIA
    assert sCriterion in levelGates._DICT_L3_REMEDIATION_HINTS
    sJs = _fsReadApplicationScript()
    assert '"' + sCriterion + '"' in sJs


def _fsReadApplicationScript():
    """Return the dashboard script that owns the blocker glyph table."""
    import vaibify.gui as guiPackage
    sPath = os.path.join(
        os.path.dirname(guiPackage.__file__),
        "static", "scriptApplication.js",
    )
    with open(sPath) as fileIn:
        return fileIn.read()


@pytest.mark.falsification
def test_a_sandbox_archive_blocks_the_scalar_gate(sProjectRepo, monkeypatch):
    """The gate enumerates by hand, so the dict entry alone is not enough.

    Kills: adding the criterion to ``_fdictL3WorkflowChecks`` and
    ``_T_WORKFLOW_LEVEL3_CRITERIA`` without the ``fbAtLeastLevel3``
    conjunct -- the scalar reports Level 3 attained while the rows
    block.
    """
    _fnWriteEnvelope(
        sProjectRepo, _fdictBuildRecord(
            "10.5072/zenodo.7000001", "sandbox",
        ),
    )
    for sName in (
        "fbAtLeastLevel2", "fbL3ReadinessOK", "fbL3AttestationCurrent",
        "fbEnvelopeMatchesGithubMirror", "fbEnvelopeMatchesZenodoArchive",
        "fbImageArchiveDeposited",
    ):
        monkeypatch.setattr(
            levelGates, sName, lambda *args, **kwargs: True,
        )
    assert levelGates.fbAtLeastLevel3({}, sProjectRepo) is False


def test_withholding_the_credit_leaves_the_rerun_runnable(sProjectRepo):
    """Ruling 5: the rerun still runs and still records.

    Zenodo versions are immutable, so the production deposit must
    carry the envelope AND the attestation together -- if permanence
    gated the rerun, the attestation that deposit is meant to carry
    could never be produced.
    """
    from vaibify.gui.routes import reproducibilityRoutes
    sSource = open(reproducibilityRoutes.__file__).read()
    assert "fbNoArchiveIsKnownSandbox" not in sSource
    assert "archivePermanence" not in sSource


def test_the_row_payload_names_which_archive(sProjectRepo):
    _fnWriteEnvelope(
        sProjectRepo, _fdictBuildRecord(
            "10.5072/zenodo.7000001", "sandbox",
        ),
    )
    dictState = levelGates.fdictArchivePermanenceState({}, sProjectRepo)
    assert dictState["bNoArchiveKnownSandbox"] is False
    assert dictState["sImageArchivePermanence"] == (
        archivePermanence.S_PERMANENCE_SANDBOX
    )
    assert dictState["sProjectArchivePermanence"] == (
        archivePermanence.S_PERMANENCE_UNKNOWN
    )
    assert len(dictState["listPermanenceIssues"]) == 1
