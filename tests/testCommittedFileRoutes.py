"""The two places a reader can ask for the committed files back.

At conversion, the workspace seed can put the committed versions of
the pinned files that differ into the new container -- only among the
entries the researcher chose to copy. After conversion, the Level 3
remedy restores them in the running container. Both refuse the
researcher's own directory. Real git throughout; the container is the
stand-in adapter from ``testCommittedFiles``.
"""

import os

import pytest
from fastapi import HTTPException

from tests.reproductionSourceFixtures import fsRunGit
from tests.testCommittedFiles import (
    S_PINNED_FIGURE,
    S_PINNED_OUTPUT,
    S_UNPINNED_RECORD,
    _ContainerStandIn,
    _fsPublishedClone,
    _fsReadText,
)
from vaibify.gui.routes import committedFileRoutes, fileRoutes
from vaibify.reproducibility import repoFiles
from vaibify.reproducibility.repoFiles import HostRepoFiles


@pytest.fixture(autouse=True)
def fnIsolateGitIdentity(monkeypatch):
    """Keep every git here from reading the developer's own config."""
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)


@pytest.fixture
def fnSeedIntoStandIn(monkeypatch):
    """Make the seed's container adapter the stand-in over the same path."""
    monkeypatch.setattr(
        repoFiles, "ContainerRepoFiles",
        lambda connectionDocker, sContainerId, sRootPath: (
            _ContainerStandIn(sRootPath)
        ),
    )


# ---------------------------------------------------------------------
# The seed, at conversion
# ---------------------------------------------------------------------


@pytest.mark.falsification
def test_the_seed_restores_only_within_the_entries_it_copied(
    tmp_path, fnSeedIntoStandIn,
):
    """Kills: recreating a pinned file the researcher chose not to copy.

    The figure's directory was not copied, so in the container that
    file is absent -- which git reads as differing from HEAD. Starting
    from the committed files must not bring back what the researcher
    left out.
    """
    sRepo = _fsPublishedClone(tmp_path)
    os.remove(os.path.join(sRepo, S_PINNED_FIGURE))
    dictResult = fileRoutes._fdictRestoreCommittedFilesAfterSeed(
        None, "container-id", sRepo,
        ["Step", "MANIFEST.sha256", ".git", ".vaibify"],
    )
    assert dictResult["sRestoreRefusal"] == ""
    assert sorted(dictResult["listRestoredPaths"]) == [
        "MANIFEST.sha256", S_PINNED_OUTPUT,
    ]
    assert _fsReadText(sRepo, S_PINNED_OUTPUT) == "author 1.000000000001\n"
    assert not os.path.exists(os.path.join(sRepo, S_PINNED_FIGURE))


def test_a_seed_git_cannot_answer_is_reported_beside_the_copy(
    tmp_path, fnSeedIntoStandIn,
):
    """The files landed; a restore that could not run is said, not raised."""
    sDirectory = str(tmp_path / "copiedButNotARepository")
    os.makedirs(sDirectory)
    dictResult = fileRoutes._fdictRestoreCommittedFilesAfterSeed(
        None, "container-id", sDirectory, ["data"],
    )
    assert dictResult["listRestoredPaths"] == []
    assert dictResult["sRestoreRefusal"]


def test_an_entry_matches_itself_and_what_lies_under_it_only():
    assert fileRoutes._fbPathIsWithinEntries("Step/a.json", ["Step/"])
    assert fileRoutes._fbPathIsWithinEntries("MANIFEST.sha256", ["MANIFEST.sha256"])
    assert not fileRoutes._fbPathIsWithinEntries("StepTwo/a.json", ["Step"])
    assert not fileRoutes._fbPathIsWithinEntries("Plot/b.png", ["Step"])


# ---------------------------------------------------------------------
# The Level 3 remedy, after conversion
# ---------------------------------------------------------------------


def test_the_remedy_refuses_a_project_on_this_machine(tmp_path):
    """A host project's own files reach the caller as a 409, untouched."""
    sRepo = _fsPublishedClone(tmp_path)
    with pytest.raises(HTTPException) as errorRefused:
        committedFileRoutes._fdictRestoreDifferences(HostRepoFiles(sRepo))
    assert errorRefused.value.status_code == 409
    assert _fsReadText(sRepo, S_PINNED_OUTPUT) == "reader 1.000000000002\n"


def test_the_remedy_restores_every_differing_pinned_file_in_a_container(
    tmp_path,
):
    sRepo = _fsPublishedClone(tmp_path)
    dictResult = committedFileRoutes._fdictRestoreDifferences(
        _ContainerStandIn(sRepo),
    )
    assert S_PINNED_OUTPUT in dictResult["listRestoredPaths"]
    assert _fsReadText(sRepo, S_PINNED_OUTPUT) == "author 1.000000000001\n"
    assert _fsReadText(sRepo, S_UNPINNED_RECORD) == '{"bApproved": true}\n'


def test_the_description_says_whose_manifest_and_where_a_restore_runs(
    tmp_path,
):
    sRepo = _fsPublishedClone(tmp_path)
    fsRunGit(["config", "user.email", "reader@example.invalid"], sRepo)
    dictInContainer = committedFileRoutes._fdictDescribeDifferences(
        _ContainerStandIn(sRepo),
    )
    assert dictInContainer["sManifestOwnership"] == "foreign"
    assert dictInContainer["bRestoreRunsInContainer"] is True
    assert S_PINNED_OUTPUT in dictInContainer["listDifferingPaths"]
    dictOnHost = committedFileRoutes._fdictDescribeDifferences(
        HostRepoFiles(sRepo),
    )
    assert dictOnHost["bRestoreRunsInContainer"] is False
