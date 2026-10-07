"""Doubles that run, or stand in for, the poll's one-exec snapshot program.

``LocalSnapshotConnection`` executes the REAL snapshot program (the text
the container runs) with a local interpreter, so a change to the program
is exercised rather than shadowed by a fake that agrees with itself.
``CannedSnapshotConnection`` answers what the program would have
answered for a container whose paths do not exist on this machine -- the
shape every container project has, and the one a ``tmp_path`` root
cannot imitate.
"""

import json
import os
import subprocess
import sys
import uuid
from types import SimpleNamespace

from vaibify.docker import dockerConnection
from vaibify.reproducibility import repoFiles


class LocalSnapshotConnection:
    """Runs the REAL typed-read snapshot program against a local directory."""

    ftReadRepoSnapshot = dockerConnection.DockerConnection.ftReadRepoSnapshot

    def __init__(self):
        self.iSnapshotReads = 0
        self.listLastArgs = []

    def _ftRunTypedRead(self, sContainerId, sOperation, listArgs):
        assert sOperation == dockerConnection.S_TYPED_READ_REPO_SNAPSHOT
        self.iSnapshotReads += 1
        self.listLastArgs = list(listArgs)
        sProgram = dockerConnection.fsRenderBatchedTypedReadProgram(
            sOperation, listArgs,
        )
        processRun = subprocess.run(
            [sys.executable, "-c", sProgram], capture_output=True, text=True,
        )
        return SimpleNamespace(
            iExitCode=processRun.returncode, sStdout=processRun.stdout,
            sStderr=processRun.stderr,
        )


class CannedSnapshotConnection:
    """Answers a fixed snapshot, as a container whose root this host lacks.

    ``dictHashEntries`` maps repo-relative paths to the entries the real
    program would have written. ``listRequestedHashPaths`` records what
    the poll asked to have hashed, so a test can pin the request.
    """

    def __init__(self, dictHashEntries=None, iExitCode=0):
        self.dictHashEntries = dict(dictHashEntries or {})
        self.iExitCode = iExitCode
        self.iSnapshotReads = 0
        self.listRequestedHashPaths = []

    def ftReadRepoSnapshot(
        self, sContainerId, sRootPath, listContentPaths,
        listSkipTextPaths, listHashPaths, listAbsHashPaths,
        dictCachedKeys=None, bHashManifestEntries=False,
        bReadReproductions=False,
    ):
        self.iSnapshotReads += 1
        self.listRequestedHashPaths = list(listHashPaths)
        dictAnswer = {
            "dictFiles": {
                sPath: {"bIsFile": False, "sText": None, "iMtime": None}
                for sPath in repoFiles.TUPLE_SNAPSHOT_CONTENT_PATHS
            },
            "dictHashes": dict(self.dictHashEntries),
            "dictAbsHashes": {},
        }
        return SimpleNamespace(
            iExitCode=self.iExitCode, sStdout=json.dumps(dictAnswer),
            sStderr="",
        )


def fsContainerRootAbsentFromThisHost():
    """Return a repo root that exists only inside a container.

    Asserted absent here, because a test that used ``tmp_path`` for a
    container root let a host-side read succeed where the container's
    could not -- which is how the host-read lane stayed green while it
    failed for every real container project.
    """
    sRoot = "/workspace/proj-" + uuid.uuid4().hex
    assert not os.path.exists(sRoot)
    return sRoot


def fdictSteadyHashEntry(sBlobSha, sSha256="0" * 64, listStatKey=None):
    """Return the entry the real program writes for a settled file."""
    return {
        "sSha256": sSha256, "sBlobSha": sBlobSha,
        "listStatKey": listStatKey or [1, 2, 3, 4],
        "sSymlinkSegment": None, "bEscapesRoot": False,
    }


def fdictHashStaleFromRealSnapshot(dictWorkflow, dictMarkersByStep, sRoot):
    """Return ``{iStep: [drifted paths]}`` judged against a REAL snapshot.

    The real snapshot program runs over the real directory ``sRoot`` and
    the real verdict function judges every digest the markers recorded:
    the lane the poll runs, with this machine standing in for the
    container. For tests of invalidation that need a project whose
    files really are on disk.
    """
    from vaibify.gui import fileStatusManager, hashStaleness
    from vaibify.reproducibility.repoFiles import SnapshotRepoFiles
    filesPoll = SnapshotRepoFiles.ffilesFetch(
        LocalSnapshotConnection(), "cid-real-snapshot", sRoot,
        listHashRelPaths=hashStaleness.flistMarkerHashedPaths(
            dictMarkersByStep),
    )
    dictVerdicts = fileStatusManager.fdictMarkerVerdictsByStep(
        dictWorkflow, dictMarkersByStep, filesPoll)
    return {
        iStep: dictEntry["listDrifted"]
        for iStep, dictEntry in dictVerdicts.items()
        if dictEntry["listDrifted"]
    }


def fsetDriftedAgainstRealSnapshot(dictMarker, sRoot):
    """Return the paths one marker's digests prove drifted, via a real snapshot.

    The real snapshot program hashes the real files under ``sRoot``;
    the real verdict function judges each recorded digest. Unknown is
    not drift, so a path the snapshot could not answer is not returned.
    """
    from vaibify.gui import hashStaleness
    from vaibify.reproducibility.repoFiles import SnapshotRepoFiles
    filesPoll = SnapshotRepoFiles.ffilesFetch(
        LocalSnapshotConnection(), "cid-real-snapshot", sRoot,
        listHashRelPaths=hashStaleness.flistMarkerHashedPaths(
            {0: dictMarker}),
    )
    dictVerdicts = hashStaleness.fdictVerdictsForMarker(
        dictMarker, hashStaleness.fdictHashEntriesOfSnapshot(filesPoll))
    return set(dictVerdicts["listDrifted"])
