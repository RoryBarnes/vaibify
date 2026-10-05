"""Pinned files that differ from the last commit, and putting them back.

A reader who re-runs a published project outside the author's pinned
environment changes files the manifest pins, and the Level 3 readiness
gate then refuses the verification. The way back is to restore the
committed bytes -- inside a container, whose copy of the project is a
copy -- never in the researcher's own directory. Every question here
is asked of a real git; the "container" is a stand-in adapter that
runs that git in a temporary directory but, like a container adapter,
names no local root.
"""

import os

import pytest

from tests.reproductionSourceFixtures import (
    fnCommitEverything,
    fnWriteManifest,
    fnWriteText,
    fsRunGit,
)
from vaibify.reproducibility import committedFiles
from vaibify.reproducibility.repoFiles import HostRepoFiles


S_PINNED_OUTPUT = "Step/result.json"
S_PINNED_FIGURE = "Plot/figure.png"
S_UNPINNED_RECORD = ".vaibify/projects/project.json"
S_PINNED_GLOB_NAME = "Data/*.txt"
S_UNPINNED_GLOB_MATCH = "Data/unpinned.txt"
S_PINNED_SPACED_NAME = "Data/with space.txt"


@pytest.fixture(autouse=True)
def fnIsolateGitIdentity(monkeypatch):
    """Keep every git here from reading the developer's own config."""
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)


class _ContainerStandIn(HostRepoFiles):
    """Runs git in a temporary directory, but names no local root.

    The one property a container adapter has that matters here: its
    root is not a directory on the researcher's machine.
    """

    def fsLocalRootOrNone(self):
        return None


def _fsReadText(sRepo, sRelative):
    with open(os.path.join(sRepo, sRelative), encoding="utf-8") as fileHandle:
        return fileHandle.read()


def _fsPublishedClone(tmp_path):
    """A clone the author published, which the reader then re-ran.

    The reader's run changed two pinned outputs, deleted a third
    pinned file, changed a file the manifest does not pin, and
    regenerated the manifest into a description of their own bytes.
    """
    sRepo = str(tmp_path / "clone")
    os.makedirs(sRepo)
    fsRunGit(["init", "-q"], sRepo)
    for sRelative, sBody in (
        (S_PINNED_OUTPUT, "author 1.000000000001\n"),
        (S_PINNED_FIGURE, "author figure\n"),
        (S_PINNED_GLOB_NAME, "author literal star\n"),
        (S_PINNED_SPACED_NAME, "author spaced\n"),
        (S_UNPINNED_GLOB_MATCH, "author unpinned\n"),
        (S_UNPINNED_RECORD, "{}\n"),
    ):
        fnWriteText(sRepo, sRelative, sBody)
    fnWriteManifest(sRepo, [
        S_PINNED_OUTPUT, S_PINNED_FIGURE, S_PINNED_GLOB_NAME,
        S_PINNED_SPACED_NAME,
    ])
    fnCommitEverything(sRepo, "author publishes")
    fnWriteText(sRepo, S_PINNED_OUTPUT, "reader 1.000000000002\n")
    fnWriteText(sRepo, S_PINNED_GLOB_NAME, "reader literal star\n")
    os.remove(os.path.join(sRepo, S_PINNED_SPACED_NAME))
    fnWriteText(sRepo, S_UNPINNED_GLOB_MATCH, "reader unpinned\n")
    fnWriteText(sRepo, S_UNPINNED_RECORD, '{"bApproved": true}\n')
    fnWriteManifest(sRepo, [
        S_PINNED_OUTPUT, S_PINNED_FIGURE, S_PINNED_GLOB_NAME,
    ])
    return sRepo


# ---------------------------------------------------------------------
# Which pinned files differ
# ---------------------------------------------------------------------


@pytest.mark.falsification
def test_only_pinned_files_that_differ_from_head_are_listed(tmp_path):
    """Kills: listing every changed tracked file, pinned or not."""
    sRepo = _fsPublishedClone(tmp_path)
    listDiffering = committedFiles.flistPinnedPathsDifferingFromHead(sRepo)
    assert sorted(listDiffering) == sorted([
        "MANIFEST.sha256", S_PINNED_GLOB_NAME, S_PINNED_SPACED_NAME,
        S_PINNED_OUTPUT,
    ])
    assert S_UNPINNED_RECORD not in listDiffering
    assert S_UNPINNED_GLOB_MATCH not in listDiffering


@pytest.mark.falsification
def test_a_regenerated_manifest_is_itself_listed(tmp_path):
    """Kills: forgetting that the manifest pins itself.

    The working-tree manifest here describes the READER's bytes, so a
    check against it would say nothing differs. The pinned set is read
    from HEAD, and the manifest is part of it.
    """
    sRepo = _fsPublishedClone(tmp_path)
    assert "MANIFEST.sha256" in (
        committedFiles.flistPinnedPathsDifferingFromHead(sRepo)
    )


def test_a_repository_without_a_committed_manifest_pins_nothing(tmp_path):
    sRepo = str(tmp_path / "plain")
    os.makedirs(sRepo)
    fsRunGit(["init", "-q"], sRepo)
    fnWriteText(sRepo, "notes.txt", "a\n")
    fnCommitEverything(sRepo, "no manifest")
    fnWriteText(sRepo, "notes.txt", "b\n")
    assert committedFiles.flistPinnedPathsDifferingFromHead(sRepo) == []


def test_a_directory_git_cannot_read_is_undetermined_never_empty(tmp_path):
    sDirectory = str(tmp_path / "notARepository")
    os.makedirs(sDirectory)
    with pytest.raises(committedFiles.CommittedFilesUndeterminedError):
        committedFiles.flistPinnedPathsDifferingFromHead(sDirectory)


def test_a_runner_that_raises_is_undetermined(tmp_path, monkeypatch):
    sRepo = _fsPublishedClone(tmp_path)

    def ftRunGitThatBreaks(listArguments):
        raise OSError("git is not installed")

    monkeypatch.setattr(
        committedFiles.gitEvidence, "ffnBuildGitRunnerForRepoFiles",
        lambda filesRepo: ftRunGitThatBreaks,
    )
    with pytest.raises(committedFiles.CommittedFilesUndeterminedError):
        committedFiles.flistPinnedPathsDifferingFromHead(sRepo)


# ---------------------------------------------------------------------
# Restoring them
# ---------------------------------------------------------------------


@pytest.mark.falsification
def test_the_restore_writes_the_committed_bytes_and_nothing_else(tmp_path):
    """Kills: a restore that does not reach the working tree."""
    sRepo = _fsPublishedClone(tmp_path)
    filesContainer = _ContainerStandIn(sRepo)
    listDiffering = committedFiles.flistPinnedPathsDifferingFromHead(
        filesContainer,
    )
    listRestored = committedFiles.flistRestorePinnedPathsFromHead(
        filesContainer, listDiffering,
    )
    assert sorted(listRestored) == sorted(listDiffering)
    assert _fsReadText(sRepo, S_PINNED_OUTPUT) == "author 1.000000000001\n"
    assert _fsReadText(sRepo, S_PINNED_SPACED_NAME) == "author spaced\n"
    assert committedFiles.flistPinnedPathsDifferingFromHead(sRepo) == []
    assert _fsReadText(sRepo, S_UNPINNED_RECORD) == '{"bApproved": true}\n'


@pytest.mark.falsification
def test_a_pinned_name_that_looks_like_a_pattern_restores_only_itself(
    tmp_path,
):
    """Kills: dropping --literal-pathspecs.

    A file literally named with a ``*`` is pinned; an unpinned file
    beside it would match that name read as a glob. Read as a pattern,
    the restore would silently throw away the reader's unpinned work.
    """
    sRepo = _fsPublishedClone(tmp_path)
    committedFiles.flistRestorePinnedPathsFromHead(
        _ContainerStandIn(sRepo), [S_PINNED_GLOB_NAME],
    )
    assert _fsReadText(sRepo, S_PINNED_GLOB_NAME) == "author literal star\n"
    assert _fsReadText(sRepo, S_UNPINNED_GLOB_MATCH) == "reader unpinned\n"


@pytest.mark.falsification
def test_the_restore_refuses_the_researchers_own_directory(tmp_path):
    """Kills: restoring on the host, where it discards the reader's run."""
    sRepo = _fsPublishedClone(tmp_path)
    with pytest.raises(committedFiles.CommittedFilesUndeterminedError):
        committedFiles.flistRestorePinnedPathsFromHead(
            HostRepoFiles(sRepo), [S_PINNED_OUTPUT],
        )
    assert _fsReadText(sRepo, S_PINNED_OUTPUT) == "reader 1.000000000002\n"


@pytest.mark.falsification
def test_a_path_that_is_not_pinned_is_never_restored(tmp_path):
    """Kills: trusting the caller's list instead of re-asking git."""
    sRepo = _fsPublishedClone(tmp_path)
    listRestored = committedFiles.flistRestorePinnedPathsFromHead(
        _ContainerStandIn(sRepo), [S_UNPINNED_RECORD, S_PINNED_OUTPUT],
    )
    assert listRestored == [S_PINNED_OUTPUT]
    assert _fsReadText(sRepo, S_UNPINNED_RECORD) == '{"bApproved": true}\n'


def test_a_restore_git_reports_but_did_not_take_raises(tmp_path, monkeypatch):
    """A restore that left a file differing is never reported as done."""
    sRepo = _fsPublishedClone(tmp_path)
    monkeypatch.setattr(
        committedFiles, "_fnRestoreOneBatch",
        lambda ftRunGit, listBatch: None,
    )
    with pytest.raises(committedFiles.CommittedFilesUndeterminedError):
        committedFiles.flistRestorePinnedPathsFromHead(
            _ContainerStandIn(sRepo), [S_PINNED_OUTPUT],
        )
