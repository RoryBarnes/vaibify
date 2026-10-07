"""A host project gets a REAL poll snapshot, from the same program.

The connection router delegated ``ftReadRepoSnapshot`` to the Docker
leg verbatim. A host project has no Docker container, so the Docker leg
found none by its name, and the poll degraded to a conservative snapshot
on every tick -- which carries no hashes. With no Docker daemon the
error was a ``DockerLegUnavailableError`` (a RuntimeError the poll's
``except OSError`` did not catch). Once the marker lane judges digests
against the snapshot, a host project left like that could never display
Level 1.

The host leg now runs the SAME fixed program through its own gated,
journaled typed-read path, and the snapshot fetch asks the leg that
holds the resource rather than the router's Docker default. Every
answer below comes from a REAL subprocess over a REAL project
directory.
"""

import hashlib
import os
import subprocess

import pytest

from vaibify.config import containerLock, operationJournal
from vaibify.gui.connectionRouter import ConnectionRouter
from vaibify.host import hostScratch
from vaibify.host.hostConnection import (
    HostConnection,
    HostPathOutsideProjectError,
)
from vaibify.reproducibility.repoFiles import SnapshotRepoFiles

S_PROJECT_NAME = "host-snapshot-project"


@pytest.fixture(autouse=True)
def fixtureIsolateJournalAndScratch(tmp_path, monkeypatch):
    monkeypatch.setattr(
        operationJournal, "_S_JOURNAL_DIRECTORY", str(tmp_path / "journal"))
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"))
    monkeypatch.setattr(
        hostScratch, "_S_HOST_DIAGNOSTICS_ROOT",
        str(tmp_path / "host-diagnostics"))


@pytest.fixture()
def tProjectAndHostLeg(tmp_path):
    sProjectRoot = str(tmp_path / "project")
    os.makedirs(os.path.join(sProjectRoot, "out"))
    with open(os.path.join(sProjectRoot, "out", "data.bin"), "wb") as f:
        f.write(b"host bytes")
    return sProjectRoot, HostConnection(
        fnResolveProjectRoot=lambda sResourceId: sProjectRoot)


def _fsBlobShaOf(baContent):
    return hashlib.sha1(
        b"blob " + str(len(baContent)).encode() + b"\x00" + baContent,
    ).hexdigest()


@pytest.mark.falsification
def test_the_host_leg_answers_the_digest_a_marker_records(tProjectAndHostLeg):
    """The host twin hashes inside the project, with the one program.

    Kills: leaving the host leg without the method (a host project
    reads a conservative snapshot), and answering from anything other
    than the shared program (the digests would not agree with the
    container's, which the markers are written against).
    """
    sProjectRoot, connectionHost = tProjectAndHostLeg
    filesPoll = SnapshotRepoFiles.ffilesFetch(
        connectionHost, S_PROJECT_NAME, sProjectRoot,
        listHashRelPaths=["out/data.bin", "out/never.dat"],
    )
    dictEntries = filesPoll.fdictAllHashEntries()
    assert dictEntries["out/data.bin"]["sBlobSha"] == _fsBlobShaOf(
        b"host bytes")
    assert dictEntries["out/never.dat"]["bMissing"] is True


@pytest.mark.falsification
def test_a_path_leaving_the_project_is_not_hashed_on_the_host(
    tProjectAndHostLeg, tmp_path,
):
    """The program's containment check holds on the host too.

    Kills: hashing a path outside the project because the host leg
    skipped the program's realpath containment.
    """
    sProjectRoot, connectionHost = tProjectAndHostLeg
    with open(str(tmp_path / "outside.txt"), "w") as fileOutside:
        fileOutside.write("secret")
    filesPoll = SnapshotRepoFiles.ffilesFetch(
        connectionHost, S_PROJECT_NAME, sProjectRoot,
        listHashRelPaths=["../outside.txt"],
    )
    dictEntry = filesPoll.fdictAllHashEntries()["../outside.txt"]
    assert dictEntry["bEscapesRoot"] is True
    assert dictEntry["sBlobSha"] is None


@pytest.mark.falsification
def test_a_root_outside_the_project_is_refused_before_anything_runs(
    tProjectAndHostLeg, tmp_path,
):
    """The leg's own path guard vets the root first.

    Kills: handing the program a root the guard never saw.
    """
    _sProjectRoot, connectionHost = tProjectAndHostLeg
    with pytest.raises(HostPathOutsideProjectError):
        connectionHost.ftReadRepoSnapshot(
            S_PROJECT_NAME, str(tmp_path), [], [], [], [])


@pytest.mark.falsification
def test_absolute_binary_paths_are_never_read_on_the_host(
    tProjectAndHostLeg, tmp_path,
):
    """Declared binaries outside the project are 'not measured', not read.

    The host guard admits exactly two roots; an out-of-root read would
    be a second authority. The honest answer for an unread binary is
    None.

    Kills: forwarding ``listAbsHashPaths`` to the host program.
    """
    sProjectRoot, connectionHost = tProjectAndHostLeg
    sBinary = str(tmp_path / "tool")
    with open(sBinary, "wb") as fileBinary:
        fileBinary.write(b"#!/bin/sh\n")
    filesPoll = SnapshotRepoFiles.ffilesFetch(
        connectionHost, S_PROJECT_NAME, sProjectRoot,
        listAbsHashPaths=[sBinary],
    )
    assert filesPoll.fdictHashAbsolutePaths([sBinary]) == {sBinary: None}


@pytest.mark.falsification
def test_the_router_sends_a_host_projects_snapshot_to_the_host_leg(
    tProjectAndHostLeg, monkeypatch,
):
    """Asked of the leg that holds the project, never the Docker default.

    Kills: asking the router for the typed read, whose ``__getattr__``
    hands it to the Docker leg, which has no such container.
    """
    sProjectRoot, connectionHost = tProjectAndHostLeg

    class DockerLegThatMustNotBeAsked:
        def ftReadRepoSnapshot(self, *aArgs, **dictKeywords):
            raise AssertionError("a host project reached the Docker leg")

    router = ConnectionRouter(DockerLegThatMustNotBeAsked(), connectionHost)
    monkeypatch.setattr(
        "vaibify.gui.connectionRouter.fbIsHostProject",
        lambda sResourceId: sResourceId == S_PROJECT_NAME)
    filesPoll = SnapshotRepoFiles.ffilesFetch(
        router, S_PROJECT_NAME, sProjectRoot,
        listHashRelPaths=["out/data.bin"],
    )
    assert filesPoll.fdictAllHashEntries()["out/data.bin"]["sBlobSha"]


@pytest.mark.falsification
def test_a_host_project_polls_with_no_docker_daemon(
    tProjectAndHostLeg, monkeypatch,
):
    """With no Docker leg at all, a host project's snapshot still answers.

    Kills: letting the call reach the router's ``__getattr__``, which
    raises a ``DockerLegUnavailableError`` the poll does not catch.
    """
    sProjectRoot, connectionHost = tProjectAndHostLeg
    router = ConnectionRouter(None, connectionHost)
    monkeypatch.setattr(
        "vaibify.gui.connectionRouter.fbIsHostProject",
        lambda sResourceId: sResourceId == S_PROJECT_NAME)
    filesPoll = SnapshotRepoFiles.ffilesFetch(
        router, S_PROJECT_NAME, sProjectRoot,
        listHashRelPaths=["out/data.bin"],
    )
    assert filesPoll.fdictAllHashEntries()["out/data.bin"]["sBlobSha"]
