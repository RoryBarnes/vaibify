"""A hash taken in the same clock tick as the last write is never cached.

The poll caches a digest under the stat key ``[mtime_ns, ctime_ns, size,
inode]`` so an unchanged file is not rehashed every five seconds. A key
cannot see a same-size, in-place rewrite that lands in the very tick the
cached hash was taken in: the timestamps have not advanced yet, so the
key still matches and the stale digest is served. This is git's "racy
clean" problem (J. C. Hamano, git ``Documentation/technical/
racy-git.txt``), and git's answer is the one used here: a digest is
cached only when it was taken at least one whole second after the file's
last change, so any later write must move a timestamp.

The container's own clock decides (``iHashedAtNs``, read after the
steady check), never the host's, so a skewed host cannot make a racy
entry look settled. A digest taken inside the window is still USED for
that poll; it is only not remembered.

These tests run the REAL snapshot program. The in-place rewrite that
keeps every key field equal is made by wrapping the program's own stat
calls, because on a real filesystem the ctime moves on every write --
which is exactly why the racy rule needs a test of its own: ctime alone
would hide a missing rule.
"""

import os
import subprocess
import sys
import time
import types

import pytest

from tests.snapshotProgramHarness import LocalSnapshotConnection
from vaibify.docker import dockerConnection
from vaibify.gui.routes import pipelineRoutes
from vaibify.reproducibility.repoFiles import SnapshotRepoFiles

S_CONTAINER_ID = "cid-racy-clean"
S_RELATIVE = "out/data.bin"


def _fnWrite(sRoot, baContent):
    sPath = os.path.join(sRoot, *S_RELATIVE.split("/"))
    os.makedirs(os.path.dirname(sPath), exist_ok=True)
    with open(sPath, "wb") as fileOut:
        fileOut.write(baContent)
    return sPath


def _fdictPollOnce(connection, sRoot, dictShaCache):
    """One poll's snapshot, then the cache update the poll performs."""
    filesPoll = SnapshotRepoFiles.ffilesFetch(
        connection, S_CONTAINER_ID, sRoot,
        listHashRelPaths=[S_RELATIVE],
        dictCachedEntries=pipelineRoutes._fdictCachedEntriesForSnapshot(
            dictShaCache),
    )
    pipelineRoutes._fbUpdateShaCache(dictShaCache, filesPoll)
    return filesPoll.fdictAllHashEntries()[S_RELATIVE]


class _ConnectionWithAForcedStatKey(LocalSnapshotConnection):
    """Runs the real program with one file's stat key pinned.

    Every ``stat``/``fstat`` the program makes of the file that has
    ``iInode`` answers the given mtime, ctime, size and inode, however
    the file changes underneath: a coarse-clock filesystem on which a
    rewrite lands in the tick the hash was taken in.
    """

    def __init__(self, tStatKey):
        super().__init__()
        self._tStatKey = tStatKey

    def _ftRunTypedRead(self, sContainerId, sOperation, listArgs):
        iMtime, iCtime, iSize, iInode = self._tStatKey
        sHook = (
            "import os as _os\n"
            "class _Forced:\n"
            "    def __init__(self, statReal):\n"
            "        self._statReal = statReal\n"
            "    def __getattr__(self, sName):\n"
            "        return getattr(self._statReal, sName)\n"
            "    st_mtime_ns = %d\n"
            "    st_ctime_ns = %d\n"
            "    st_size = %d\n"
            "    st_ino = %d\n"
            "_fStat = _os.stat\n"
            "_fFstat = _os.fstat\n"
            "def _fForce(statReal):\n"
            "    return _Forced(statReal) if statReal.st_ino == %d "
            "else statReal\n"
            "_os.stat = lambda *a, **k: _fForce(_fStat(*a, **k))\n"
            "_os.fstat = lambda *a, **k: _fForce(_fFstat(*a, **k))\n"
        ) % (iMtime, iCtime, iSize, iInode, iInode)
        sProgram = sHook + dockerConnection.fsRenderBatchedTypedReadProgram(
            sOperation, listArgs)
        processRun = subprocess.run(
            [sys.executable, "-c", sProgram], capture_output=True, text=True)
        return types.SimpleNamespace(
            iExitCode=processRun.returncode, sStdout=processRun.stdout,
            sStderr=processRun.stderr)


@pytest.mark.falsification
def test_a_digest_taken_within_a_second_of_the_write_is_used_not_cached(
    tmp_path,
):
    """The window protects the cache, never the answer.

    Kills: dropping the racy condition from the cache update, which
    would remember a digest taken in the same tick as the last write.
    """
    sRoot = str(tmp_path)
    _fnWrite(sRoot, b"fresh bytes")
    dictShaCache = {}
    dictEntry = _fdictPollOnce(
        LocalSnapshotConnection(), sRoot, dictShaCache)
    assert dictEntry["sBlobSha"] and dictEntry["iHashedAtNs"]
    assert dictShaCache == {}


@pytest.mark.falsification
def test_a_settled_file_is_cached_and_then_hit(tmp_path):
    """The rule is a window, not "never cache".

    Kills: making the settled test unsatisfiable (a cache that never
    fills, which silently rehashes every output on every poll).
    """
    sRoot = str(tmp_path)
    _fnWrite(sRoot, b"settled bytes")
    time.sleep(1.2)
    dictShaCache = {}
    dictFirst = _fdictPollOnce(
        LocalSnapshotConnection(), sRoot, dictShaCache)
    assert S_RELATIVE in dictShaCache
    dictSecond = _fdictPollOnce(
        LocalSnapshotConnection(), sRoot, dictShaCache)
    assert dictSecond["bCacheHit"] is True
    assert dictSecond["sBlobSha"] == dictFirst["sBlobSha"]


@pytest.mark.falsification
def test_a_same_tick_rewrite_with_an_unchanged_key_is_rehashed(tmp_path):
    """The racy rule itself, with every key field forced equal.

    Poll one hashes X inside the tick of its write. The file is then
    rewritten IN PLACE with Y of the same size, and the stat key still
    reads exactly as it did. Had poll one cached its digest, poll two
    would be offered that key, answer a hit, and serve X's digest for
    Y's bytes.

    Kills: dropping the racy condition from the cache update.
    """
    sRoot = str(tmp_path)
    sPath = _fnWrite(sRoot, b"X" * 64)
    statFirst = os.stat(sPath)
    tStatKey = (statFirst.st_mtime_ns, statFirst.st_ctime_ns,
                statFirst.st_size, statFirst.st_ino)
    connection = _ConnectionWithAForcedStatKey(tStatKey)
    dictShaCache = {}
    dictFirst = _fdictPollOnce(connection, sRoot, dictShaCache)
    with open(sPath, "r+b") as fileInPlace:
        fileInPlace.write(b"Y" * 64)
    dictSecond = _fdictPollOnce(connection, sRoot, dictShaCache)
    assert dictSecond["listStatKey"] == dictFirst["listStatKey"]
    assert not dictSecond.get("bCacheHit")
    assert dictSecond["sBlobSha"] != dictFirst["sBlobSha"]


def test_an_in_place_rewrite_that_restores_the_mtime_is_rehashed(tmp_path):
    """The ordinary protection: ctime moves on every write.

    Not a test of the racy rule (the key differs, so the rule is not
    needed to refuse the entry); it pins that the key still catches the
    rewrite a researcher's own ``touch -r`` would hide.
    """
    sRoot = str(tmp_path)
    sPath = _fnWrite(sRoot, b"X" * 64)
    time.sleep(1.2)
    dictShaCache = {}
    dictFirst = _fdictPollOnce(
        LocalSnapshotConnection(), sRoot, dictShaCache)
    statBefore = os.stat(sPath)
    with open(sPath, "r+b") as fileInPlace:
        fileInPlace.write(b"Y" * 64)
    os.utime(sPath, ns=(statBefore.st_atime_ns, statBefore.st_mtime_ns))
    dictSecond = _fdictPollOnce(
        LocalSnapshotConnection(), sRoot, dictShaCache)
    assert not dictSecond.get("bCacheHit")
    assert dictSecond["sBlobSha"] != dictFirst["sBlobSha"]


def test_an_entry_the_container_gave_no_hash_time_is_never_cached():
    """No ``iHashedAtNs`` means the settling cannot be judged.

    A cache hit carries no fresh hash time, and neither does an answer
    from an older program; neither may be (re)stored as settled.
    """
    dictShaCache = {}

    class _Files:
        def fdictAllHashEntries(self):
            return {S_RELATIVE: {
                "sSha256": "a" * 64, "sBlobSha": "b" * 40,
                "listStatKey": [1, 2, 3, 4], "sSymlinkSegment": None,
                "bEscapesRoot": False}}

    assert pipelineRoutes._fbUpdateShaCache(dictShaCache, _Files()) is False
    assert dictShaCache == {}
