"""The write-ahead journal's prior hash does not depend on the file's size.

Source: ``routeContext.fsHashContainerFileOrEmpty`` and the host
connection's ``fsHashContainerFileSha256``.

A file write is journaled BEFORE it happens with the hash of what it will
replace, so a crash can be settled by comparing the target to both the
intended and the prior content. The helper used to fetch the whole file
and was capped at 64 MiB: replacing a larger file recorded ``''``
("unproven"), and a crash during exactly that replacement could never be
settled. Nothing here uses a mock to assert a mock: the host tests hash
real files, one of them past the real cap.
"""

import hashlib
import os
from types import SimpleNamespace

import pytest

from tests.testHostConnection import (  # noqa: F401  (fixtures)
    S_PROJECT_NAME,
    fixtureIsolateJournalAndScratch,
    tProjectAndConnection,
)
from vaibify.gui.routeContext import fsHashContainerFileOrEmpty
from vaibify.host import hostConnection as hostConnectionModule

I_MEBIBYTE = 1 << 20


class _ConnectionWithACappedFetch:
    """A docker leg whose whole-file fetch refuses large files, as it does."""

    def __init__(self, sDigest="", errorOnHash=None):
        self._sDigest = sDigest
        self._errorOnHash = errorOnHash

    def fbaFetchFile(self, sContainerId, sPath):
        raise ValueError("File exceeds fbaFetchFile cap")

    def fsHashContainerFileSha256(self, sContainerId, sPath):
        if self._errorOnHash is not None:
            raise self._errorOnHash
        return self._sDigest


@pytest.mark.falsification
def testAPriorHashComesFromTheChunkedReadNotTheCappedFetch():
    """A file past the fetch cap still gets its real prior hash.

    Kills: computing the prior hash from ``fbaFetchFile``, whose cap
    turns a large file into ``''``.
    """
    sDigest = hashlib.sha256(b"a large file").hexdigest()
    dictCtx = {"docker": _ConnectionWithACappedFetch(sDigest)}
    assert fsHashContainerFileOrEmpty(dictCtx, "cid", "/p/large.bin") == sDigest


def testAnUnreadableFileStillRecordsTheFailSafeEmptyPrior():
    dictCtx = {"docker": _ConnectionWithACappedFetch(
        errorOnHash=OSError("exec failed"))}
    assert fsHashContainerFileOrEmpty(dictCtx, "cid", "/p/x") == ""


@pytest.mark.falsification
def testTheHostHashesAFilePastTheRealFetchCapNotEmpty(tProjectAndConnection):
    """A real file one chunk past 64 MiB: sparse on disk, hashed in full.

    The cap constant is NOT patched; the file is genuinely larger than
    it. The expected digest is computed here, independently, over the
    same number of zero bytes.

    Kills: hashing through the capped ``fbaFetchFile`` (answers ``''``
    for this file).
    """
    sProjectRoot, connection = tProjectAndConnection
    sTarget = os.path.join(sProjectRoot, "large.bin")
    iSize = hostConnectionModule.I_MAX_FETCH_FILE_BYTES + I_MEBIBYTE
    with open(sTarget, "wb") as fileSparse:
        fileSparse.truncate(iSize)
    hashExpected = hashlib.sha256()
    baZeros = bytes(I_MEBIBYTE)
    for _ in range(iSize // I_MEBIBYTE):
        hashExpected.update(baZeros)
    assert connection.fsHashContainerFileSha256(
        S_PROJECT_NAME, sTarget) == hashExpected.hexdigest()


def testTheHostHashReadsInBoundedChunks(tProjectAndConnection, monkeypatch):
    """No single read asks for more than the chunk size."""
    sProjectRoot, connection = tProjectAndConnection
    sTarget = os.path.join(sProjectRoot, "five.bin")
    baPayload = os.urandom(I_MEBIBYTE) * 5
    with open(sTarget, "wb") as fileOut:
        fileOut.write(baPayload)
    listReadSizes = []
    fnRealOpen = open

    class _RecordingFile:
        def __init__(self, fileReal):
            self._fileReal = fileReal

        def __enter__(self):
            return self

        def __exit__(self, *tArguments):
            self._fileReal.close()

        def read(self, iSize=-1):
            listReadSizes.append(iSize)
            return self._fileReal.read(iSize)

    monkeypatch.setitem(
        hostConnectionModule.__dict__, "open",
        lambda *tArgs, **dictArgs: _RecordingFile(fnRealOpen(*tArgs, **dictArgs)),
    )
    sDigest = connection.fsHashContainerFileSha256(S_PROJECT_NAME, sTarget)
    assert sDigest == hashlib.sha256(baPayload).hexdigest()
    assert listReadSizes and all(
        0 < iSize <= I_MEBIBYTE for iSize in listReadSizes), listReadSizes
