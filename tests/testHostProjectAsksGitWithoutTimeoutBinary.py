"""A host project's git questions are answered on a host with no ``timeout``.

The repo-files adapter bounded every command with GNU ``timeout``, which
every vaibify container has and macOS does not. A host project's
commands run on the researcher's own machine, so on a Mac each one
exited 127, and every git question a host project asked answered "could
not say": the manifest check reported that git could not tell whether
the manifest was the committed one, on a fresh clone whose manifest
git could see perfectly well (researcher-reported, 2026-10-11).

The host is made to look like a Mac by a PATH that holds ``git`` and
nothing else, and the questions run through a REAL ``HostConnection``
against a REAL repository whose manifest another identity committed.
"""

import json
import os
import shutil
import subprocess

import pytest

from vaibify.config import containerLock, operationJournal, registryManager
from vaibify.host import hostScratch
from vaibify.host.hostConnection import HostConnection
from vaibify.reproducibility import gitEvidence
from vaibify.reproducibility.repoFiles import ContainerRepoFiles

S_HOST_PROJECT = "hostGitQuestionsProject"
S_AUTHOR_EMAIL = "author@example.invalid"
S_READER_EMAIL = "reader@example.invalid"


@pytest.fixture(autouse=True)
def fixtureIsolateJournalAndScratch(tmp_path, monkeypatch):
    """Redirect the journal, locks, and scratch roots to tmp_path."""
    monkeypatch.setattr(
        operationJournal, "_S_JOURNAL_DIRECTORY", str(tmp_path / "journal"),
    )
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"),
    )
    monkeypatch.setattr(
        hostScratch, "_S_HOST_DIAGNOSTICS_ROOT",
        str(tmp_path / "host-diagnostics"),
    )


@pytest.fixture()
def fixtureClonedProject(tmp_path, monkeypatch):
    """Register a host project whose manifest the author committed."""
    sProjectRoot = str(tmp_path / "repo")
    os.makedirs(sProjectRoot)
    pathRegistry = tmp_path / "registry.json"
    pathRegistry.write_text(json.dumps({"listProjects": [{
        "sName": S_HOST_PROJECT, "sMode": "host", "sDirectory": sProjectRoot,
    }]}))
    monkeypatch.setattr(registryManager, "_S_REGISTRY_PATH", str(pathRegistry))
    monkeypatch.setattr(
        registryManager, "_S_LOCK_PATH", str(tmp_path / "registry.lock"),
    )
    with open(os.path.join(sProjectRoot, "MANIFEST.sha256"), "w") as fileHandle:
        fileHandle.write(f"{'0' * 64}  data.json\n")
    for listCommand in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", S_AUTHOR_EMAIL],
        ["git", "config", "user.name", "Author"],
        ["git", "add", "MANIFEST.sha256"],
        ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "publish"],
        ["git", "config", "user.email", S_READER_EMAIL],
    ):
        subprocess.run(
            listCommand, cwd=sProjectRoot, check=True, capture_output=True,
        )
    return sProjectRoot


@pytest.fixture()
def fixturePathWithoutTimeout(tmp_path, monkeypatch):
    """Leave ``git`` as the only program on PATH, as on a stock Mac."""
    sGitPath = shutil.which("git")
    sBinDirectory = str(tmp_path / "bin")
    os.makedirs(sBinDirectory)
    os.symlink(sGitPath, os.path.join(sBinDirectory, "git"))
    monkeypatch.setenv("PATH", sBinDirectory)
    assert shutil.which("timeout") is None


def _ffilesForHostProject(sProjectRoot):
    connection = HostConnection(
        fnResolveProjectRoot=lambda sResourceId: sProjectRoot,
    )
    return ContainerRepoFiles(connection, S_HOST_PROJECT, sProjectRoot)


def testAReaderCloneSeesTheAuthorsManifestWithoutTimeout(
    fixtureClonedProject, fixturePathWithoutTimeout,
):
    """Kills: the ``timeout`` prefix on the host leg (every answer 127)."""
    dictProvenance = gitEvidence.fdictManifestProvenanceForRepoFiles(
        _ffilesForHostProject(fixtureClonedProject),
    )
    assert dictProvenance == {
        "sManifestOwnership": gitEvidence.S_MANIFEST_OWNERSHIP_FOREIGN,
        "bManifestDiffersFromHead": False,
    }


def testAnEditedManifestIsReportedAsDifferingFromHead(
    fixtureClonedProject, fixturePathWithoutTimeout,
):
    with open(os.path.join(fixtureClonedProject, "MANIFEST.sha256"), "a") as fileHandle:
        fileHandle.write(f"{'1' * 64}  other.json\n")
    dictProvenance = gitEvidence.fdictManifestProvenanceForRepoFiles(
        _ffilesForHostProject(fixtureClonedProject),
    )
    assert dictProvenance["bManifestDiffersFromHead"] is True


def testTheHostLegStillBoundsACommandThatHangs(fixtureClonedProject):
    """The bound moved into the leg; it did not disappear."""
    filesRepo = _ffilesForHostProject(fixtureClonedProject)
    iExitCode, _sStdout, _sStderr = filesRepo.ftRunCommand(
        ["sleep", "30"], 1.0,
    )
    assert iExitCode == 124
