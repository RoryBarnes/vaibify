"""The poll snapshot answers the digest a test marker records, and a deletion.

A test marker records each output's git blob SHA. The poll used to judge
those hashes by opening the files on the HOST, which cannot see a
container's volume, so every read failed and every failure counted as
drift. The snapshot already runs inside the container and decides cache
hits there; it now answers the marker's digest too, in the same read
loop, and says outright when a path was deleted. These tests run the
REAL snapshot program, so the digest domain and the torn-read rule are
exercised on the text the container runs.
"""

import hashlib
import os
import subprocess
import sys
import time
import types

import pytest

from tests.snapshotProgramHarness import LocalSnapshotConnection
from vaibify.gui import conftestManager
from vaibify.gui.routes import pipelineRoutes
from vaibify.reproducibility.repoFiles import SnapshotRepoFiles

S_CONTAINER_ID = "cid-blob-sha"


def _fsConftestBlobSha(sRoot, sAbsolutePath):
    """Hash with the function the container's conftest uses."""
    sSource = conftestManager.fsBuildConftestSource(sRoot)
    moduleNs = types.ModuleType("vaibify_conftest_digest_ns")
    moduleNs.__dict__["__name__"] = "vaibify_conftest_digest_ns"
    exec(compile(sSource, "<template>", "exec"), moduleNs.__dict__)
    return moduleNs._fsBlobSha(sAbsolutePath)


def _fsGitHashObject(sRoot, sRelativePath):
    return subprocess.run(
        ["git", "hash-object", "--no-filters", sRelativePath],
        cwd=sRoot, capture_output=True, text=True, check=True,
    ).stdout.strip()


def _fnWriteBytes(sRoot, sRelativePath, baContent):
    sAbsolutePath = os.path.join(sRoot, *sRelativePath.split("/"))
    os.makedirs(os.path.dirname(sAbsolutePath), exist_ok=True)
    with open(sAbsolutePath, "wb") as fileOut:
        fileOut.write(baContent)


def _fdictSnapshotHashes(sRoot, listRelativePaths, dictCachedEntries=None):
    filesPoll = SnapshotRepoFiles.ffilesFetch(
        LocalSnapshotConnection(), S_CONTAINER_ID, sRoot,
        listHashRelPaths=listRelativePaths,
        dictCachedEntries=dictCachedEntries,
    )
    return filesPoll.fdictAllHashEntries()


@pytest.mark.falsification
def test_the_snapshot_blob_sha_is_the_digest_the_conftest_records(tmp_path):
    """Empty, under, at and over the 64 KiB read chunk all agree.

    Three authorities must name one digest: the program the poll runs,
    the conftest that wrote the baseline, and git itself. A header size
    taken from the wrong place, or a chunk loop that drops a tail, would
    disagree on exactly these sizes.

    Kills: computing the blob header from something other than the
    pre-read stat size.
    """
    sRoot = str(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=sRoot, check=True)
    for iSize in (0, 7, 65535, 65536, 65537, 150000):
        sRelativePath = "out/data%d.bin" % iSize
        baContent = bytes((iIndex * 31 + 7) % 251 for iIndex in range(iSize))
        _fnWriteBytes(sRoot, sRelativePath, baContent)
        dictEntry = _fdictSnapshotHashes(
            sRoot, [sRelativePath])[sRelativePath]
        sExpected = hashlib.sha1(
            b"blob " + str(iSize).encode() + b"\x00" + baContent,
        ).hexdigest()
        assert dictEntry["sBlobSha"] == sExpected, iSize
        assert dictEntry["sBlobSha"] == _fsConftestBlobSha(
            sRoot, os.path.join(sRoot, *sRelativePath.split("/")),
        ), iSize
        assert dictEntry["sBlobSha"] == _fsGitHashObject(
            sRoot, sRelativePath), iSize
        assert dictEntry["sSha256"] == hashlib.sha256(
            baContent).hexdigest(), iSize


@pytest.mark.falsification
def test_a_deleted_file_is_reported_missing_and_nothing_else_is(tmp_path):
    """Only FileNotFoundError proves deletion; other failures are unknown.

    A directory sits where a file was expected: the open succeeds, the
    read raises, and the answer carries neither a digest nor a deletion.

    Kills: reporting ``bMissing`` for every failed open, which would
    turn a permission error or a vanished mount into a proven deletion
    and invalidate a step on no evidence.
    """
    sRoot = str(tmp_path)
    os.makedirs(os.path.join(sRoot, "was_a_file"))
    dictHashes = _fdictSnapshotHashes(
        sRoot, ["never_existed.dat", "was_a_file"],
    )
    assert dictHashes["never_existed.dat"]["bMissing"] is True
    assert dictHashes["never_existed.dat"]["sBlobSha"] is None
    assert not dictHashes["was_a_file"].get("bMissing")
    assert dictHashes["was_a_file"]["sSha256"] is None
    assert dictHashes["was_a_file"]["sBlobSha"] is None


@pytest.mark.falsification
def test_an_unreadable_file_is_not_reported_missing(tmp_path):
    """A file the program may not open is unknown, never deleted.

    Kills: mapping PermissionError onto ``bMissing``.
    """
    if os.geteuid() == 0:
        pytest.skip("root can open a file whatever its mode")
    sRoot = str(tmp_path)
    _fnWriteBytes(sRoot, "locked.dat", b"secret")
    os.chmod(os.path.join(sRoot, "locked.dat"), 0)
    dictEntry = _fdictSnapshotHashes(sRoot, ["locked.dat"])["locked.dat"]
    assert not dictEntry.get("bMissing")
    assert dictEntry["sBlobSha"] is None


@pytest.mark.falsification
def test_a_file_changing_during_the_hash_yields_neither_digest(tmp_path):
    """A torn read discards BOTH digests, and neither is cached.

    The program's own reader is wrapped so each attempt's first read
    appends a byte, which moves the stat key between the read and the
    steady check; the one immediate retry is torn the same way.

    Kills: keeping the blob digest of a torn read while discarding the
    SHA-256, which a later cache hit would then serve as settled.
    """
    from vaibify.docker import dockerConnection
    sRoot = str(tmp_path)
    _fnWriteBytes(sRoot, "out/data.bin", b"abc" * 100)
    sTarget = os.path.join(sRoot, "out", "data.bin")
    sHook = (
        "import os as _os\n"
        "_iReal = _os.read\n"
        "def _fiTorn(iFd, iSize):\n"
        "    iPosition = _os.lseek(iFd, 0, _os.SEEK_CUR)\n"
        "    baData = _iReal(iFd, iSize)\n"
        "    if baData and iPosition == 0 and _os.fstat(iFd).st_ino == "
        + str(os.stat(sTarget).st_ino) + ":\n"
        "        with open(" + repr(sTarget) + ", 'ab') as f:\n"
        "            f.write(b'+')\n"
        "    return baData\n"
        "_os.read = _fiTorn\n"
    )

    class TornConnection(LocalSnapshotConnection):
        def _ftRunTypedRead(self, sContainerId, sOperation, listArgs):
            sProgram = sHook + dockerConnection.fsRenderBatchedTypedReadProgram(
                sOperation, listArgs,
            )
            processRun = subprocess.run(
                [sys.executable, "-c", sProgram],
                capture_output=True, text=True,
            )
            return types.SimpleNamespace(
                iExitCode=processRun.returncode,
                sStdout=processRun.stdout, sStderr=processRun.stderr,
            )

    filesPoll = SnapshotRepoFiles.ffilesFetch(
        TornConnection(), S_CONTAINER_ID, sRoot,
        listHashRelPaths=["out/data.bin"],
    )
    dictEntry = filesPoll.fdictAllHashEntries()["out/data.bin"]
    assert dictEntry["bTornRead"] is True
    assert dictEntry["sSha256"] is None
    assert dictEntry["sBlobSha"] is None
    dictShaCache = {}
    assert pipelineRoutes._fbUpdateShaCache(dictShaCache, filesPoll) is False
    assert "out/data.bin" not in dictShaCache


@pytest.mark.falsification
def test_a_cache_hit_carries_both_digests_and_an_old_entry_is_rehashed(
    tmp_path,
):
    """The cache returns what it stored; an entry missing one is not offered.

    The first poll hashes (a second after the write, so the digest has
    settled and may be remembered) and the cache keeps both digests under
    the stat key. The second offers that entry and the program answers a
    hit, which the fetch fills with BOTH. A cache written before the
    blob digest existed holds a SHA-256 only, so it is never offered and
    the file is hashed once more.

    Kills: filling only the SHA-256 on a hit (the blob digest would read
    as absent, which the lane must treat as unknown), and offering an
    entry that cannot supply both.
    """
    sRoot = str(tmp_path)
    _fnWriteBytes(sRoot, "out/data.bin", b"settled bytes")
    time.sleep(1.2)
    dictShaCache = {}
    filesFirst = SnapshotRepoFiles.ffilesFetch(
        LocalSnapshotConnection(), S_CONTAINER_ID, sRoot,
        listHashRelPaths=["out/data.bin"],
    )
    assert pipelineRoutes._fbUpdateShaCache(dictShaCache, filesFirst) is True
    dictCached = dictShaCache["out/data.bin"]
    assert dictCached["sBlobSha"] and dictCached["sSha256"]
    connectionSecond = LocalSnapshotConnection()
    filesSecond = SnapshotRepoFiles.ffilesFetch(
        connectionSecond, S_CONTAINER_ID, sRoot,
        listHashRelPaths=["out/data.bin"],
        dictCachedEntries=pipelineRoutes._fdictCachedEntriesForSnapshot(
            dictShaCache),
    )
    assert any(sArg.startswith("x:") for sArg in connectionSecond.listLastArgs)
    dictHit = filesSecond.fdictAllHashEntries()["out/data.bin"]
    assert dictHit["bCacheHit"] is True
    assert dictHit["sBlobSha"] == dictCached["sBlobSha"]
    assert dictHit["sSha256"] == dictCached["sSha256"]
    dictLegacy = {"out/data.bin": {
        "listStatKey": dictCached["listStatKey"],
        "sSha256": dictCached["sSha256"],
    }}
    assert pipelineRoutes._fdictCachedEntriesForSnapshot(dictLegacy) == {}
