"""A manifest another identity committed is never rewritten in silence.

MANIFEST.sha256 is the author's claim about their bytes. On a clone,
every writer of it -- the automatic refresh on the Level 1 crossing,
Regenerate, the re-pins that follow a reproduce.sh or archive-record
write -- replaced that claim with the reproducer's own, after which
"Check Files Against Manifest" compared the reproducer's outputs with
themselves and passed (researcher-reported, 2026-09-13: three data
files differed from the author's and the check said all twenty-four
matched). One predicate now sits behind every writer, the check says
which manifest it read, and only Regenerate can be consented past a
foreign manifest. Every question here is asked of a real git.
"""

import os
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from tests.reproductionSourceFixtures import (
    fnCommitEverything,
    fnWriteManifest,
    fnWriteText,
    fsRunGit,
)
from vaibify.gui.routes import environmentArchiveRoutes, pipelineRoutes
from vaibify.gui.routes import reproducibilityRoutes
from vaibify.reproducibility import gitEvidence


S_FIXTURE_EMAIL = "fixture@example.invalid"
S_OTHER_EMAIL = "reader@example.invalid"


@pytest.fixture(autouse=True)
def fnIsolateGitIdentity(monkeypatch):
    """Keep every git here from reading the developer's own config."""
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    for sName in ("GIT_COMMITTER_EMAIL", "GIT_AUTHOR_EMAIL", "EMAIL"):
        monkeypatch.delenv(sName, raising=False)


def _fsRepositoryWithACommittedManifest(tmp_path, sOwnEmail):
    """A repository whose MANIFEST.sha256 the fixture identity committed.

    ``sOwnEmail`` is the receiving identity; empty leaves it unset.
    """
    sRepo = str(tmp_path / "clone")
    os.makedirs(sRepo)
    fsRunGit(["init", "-q"], sRepo)
    fnWriteText(sRepo, "data.txt", "1 2 3\n")
    fnWriteManifest(sRepo, ["data.txt"])
    fnCommitEverything(sRepo, "author publishes")
    if sOwnEmail:
        fsRunGit(["config", "user.email", sOwnEmail], sRepo)
    return sRepo


# ---------------------------------------------------------------------
# The predicate, asked of a real git
# ---------------------------------------------------------------------


@pytest.mark.falsification
def test_a_manifest_committed_by_another_identity_is_foreign(tmp_path):
    """Kills: answering OWN for every repository."""
    sRepo = _fsRepositoryWithACommittedManifest(tmp_path, S_OTHER_EMAIL)
    ftRunGit = gitEvidence.ffnBuildGitRunnerForRepoFiles(sRepo)
    assert gitEvidence.fbRepositoryCarriesForeignManifest(ftRunGit)
    assert gitEvidence.fsManifestOwnershipForRepoFiles(sRepo) == (
        gitEvidence.S_MANIFEST_OWNERSHIP_FOREIGN
    )
    fsRunGit(["config", "user.email", S_FIXTURE_EMAIL], sRepo)
    assert not gitEvidence.fbRepositoryCarriesForeignManifest(ftRunGit)
    assert gitEvidence.fsManifestOwnershipForRepoFiles(sRepo) == (
        gitEvidence.S_MANIFEST_OWNERSHIP_OWN
    )


def test_an_unconfigured_identity_and_an_untracked_manifest_read_as_expected(
    tmp_path,
):
    sRepo = _fsRepositoryWithACommittedManifest(tmp_path, "")
    assert gitEvidence.fsManifestOwnershipForRepoFiles(sRepo) == (
        gitEvidence.S_MANIFEST_OWNERSHIP_FOREIGN
    )
    sFresh = str(tmp_path / "fresh")
    os.makedirs(sFresh)
    fsRunGit(["init", "-q"], sFresh)
    fnWriteManifest(sFresh, [])
    assert gitEvidence.fsManifestOwnershipForRepoFiles(sFresh) == (
        gitEvidence.S_MANIFEST_OWNERSHIP_OWN
    )


@pytest.mark.falsification
def test_a_git_that_cannot_answer_is_undetermined_never_own(tmp_path):
    """Kills: reading a directory git cannot read as the researcher's own."""
    sNotARepo = str(tmp_path / "plain")
    os.makedirs(sNotARepo)
    assert gitEvidence.fsManifestOwnershipForRepoFiles(sNotARepo) == (
        gitEvidence.S_MANIFEST_OWNERSHIP_UNDETERMINED
    )


@pytest.mark.falsification
def test_a_manifest_that_moved_since_head_is_reported(tmp_path):
    """Kills: reporting the working copy as the committed one."""
    sRepo = _fsRepositoryWithACommittedManifest(tmp_path, S_FIXTURE_EMAIL)
    ftRunGit = gitEvidence.ffnBuildGitRunnerForRepoFiles(sRepo)
    assert gitEvidence.fbManifestDiffersFromHead(ftRunGit) is False
    fnWriteText(sRepo, "data.txt", "3 2 1\n")
    fnWriteManifest(sRepo, ["data.txt"])
    assert gitEvidence.fbManifestDiffersFromHead(ftRunGit) is True
    dictProvenance = gitEvidence.fdictManifestProvenanceForRepoFiles(sRepo)
    assert dictProvenance == {
        "sManifestOwnership": gitEvidence.S_MANIFEST_OWNERSHIP_OWN,
        "bManifestDiffersFromHead": True,
    }
    sNotARepo = str(tmp_path / "plain")
    os.makedirs(sNotARepo)
    assert gitEvidence.fdictManifestProvenanceForRepoFiles(sNotARepo) == {
        "sManifestOwnership": gitEvidence.S_MANIFEST_OWNERSHIP_UNDETERMINED,
        "bManifestDiffersFromHead": None,
    }


# ---------------------------------------------------------------------
# Regenerate asks; the re-pins and the automatic refresh never write
# ---------------------------------------------------------------------


@pytest.mark.falsification
def test_regenerate_refuses_a_foreign_manifest_without_consent(tmp_path):
    """Kills: regenerating over the author's manifest on a clone."""
    sRepo = _fsRepositoryWithACommittedManifest(tmp_path, S_OTHER_EMAIL)
    with pytest.raises(HTTPException) as excinfo:
        reproducibilityRoutes.fnRefuseToReplaceAForeignManifest(sRepo, False)
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["sAction"] == (
        reproducibilityRoutes.S_ACTION_CONFIRM_REPLACE_FOREIGN_MANIFEST
    )
    assert "git checkout -- MANIFEST.sha256" in excinfo.value.detail["sMessage"]
    # Consent lets the author replace a collaborator's manifest.
    reproducibilityRoutes.fnRefuseToReplaceAForeignManifest(sRepo, True)
    # One's own manifest needs no consent.
    fsRunGit(["config", "user.email", S_FIXTURE_EMAIL], sRepo)
    reproducibilityRoutes.fnRefuseToReplaceAForeignManifest(sRepo, False)


@pytest.mark.falsification
def test_an_undetermined_owner_cannot_be_consented_past(tmp_path):
    """Kills: letting consent through when git could not say whose it is."""
    sNotARepo = str(tmp_path / "plain")
    os.makedirs(sNotARepo)
    with pytest.raises(HTTPException) as excinfo:
        reproducibilityRoutes.fnRefuseToReplaceAForeignManifest(sNotARepo, True)
    assert excinfo.value.status_code == 409
    assert "sAction" not in excinfo.value.detail


@pytest.mark.falsification
def test_the_archive_repin_skips_a_foreign_manifest(tmp_path):
    """Kills: re-pinning the manifest after an archive record on a clone."""
    sRepo = _fsRepositoryWithACommittedManifest(tmp_path, S_OTHER_EMAIL)
    with patch(
        "vaibify.reproducibility.manifestWriter.fnWriteManifest",
    ) as mockWrite:
        assert environmentArchiveRoutes._fbRepinManifestOrWarn(sRepo, {}) is False
    assert not mockWrite.called
    fsRunGit(["config", "user.email", S_FIXTURE_EMAIL], sRepo)
    with patch(
        "vaibify.reproducibility.manifestWriter.fnWriteManifest",
    ) as mockWrite:
        assert environmentArchiveRoutes._fbRepinManifestOrWarn(sRepo, {}) is True
    assert mockWrite.called


@pytest.mark.falsification
def test_the_reproduce_script_repin_skips_a_foreign_manifest(tmp_path):
    """Kills: re-pinning the manifest after writing reproduce.sh on a clone."""
    sRepo = _fsRepositoryWithACommittedManifest(tmp_path, S_OTHER_EMAIL)
    with patch.object(
        reproducibilityRoutes, "ffilesForWorkflow", lambda *a: sRepo,
    ), patch(
        "vaibify.reproducibility.manifestWriter.fnWriteManifest",
    ) as mockWrite:
        assert reproducibilityRoutes._fbRepinManifestOrWarn({}, "c", {}) is False
    assert not mockWrite.called


@pytest.mark.falsification
def test_the_manifest_check_says_which_manifest_it_read(tmp_path):
    """Kills: dropping the provenance from the check's answer."""
    sRepo = _fsRepositoryWithACommittedManifest(tmp_path, S_OTHER_EMAIL)
    dictResult = pipelineRoutes._fdictBuildManifestVerifyResult(sRepo, [], [])
    assert dictResult["sManifestOwnership"] == "foreign"
    assert dictResult["bManifestDiffersFromHead"] is False
    fnWriteText(sRepo, "data.txt", "3 2 1\n")
    fnWriteManifest(sRepo, ["data.txt"])
    dictResult = pipelineRoutes._fdictBuildManifestVerifyResult(sRepo, [], [])
    assert dictResult["bManifestDiffersFromHead"] is True
    assert dictResult["iTotal"] == 1 and dictResult["iMatching"] == 1
