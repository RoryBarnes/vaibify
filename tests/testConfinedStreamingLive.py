"""The streaming writes and confined reads, against a real container.

The unit tests run the programs in a subprocess behind a fake daemon; only
a real container proves what that cannot: that a file past the old 64 MiB
fetch ceiling crosses the hijacked exec socket intact in both directions
with bounded memory, that the programs run as the container user, and that
the swap race cannot redirect a read while a real swapper is flipping a
real directory.

Skipped when no daemon answers, unless ``VAIBIFY_REQUIRE_DOCKER_DAEMON``
demands one (see ``tests/testDockerConnectionLive.py``). The container is
the one ``tests/testConfinedWriteLive.py`` creates and labels.
"""

import hashlib
import io
import tarfile
import time
import tracemalloc

import pytest

from tests.testConfinedWriteLive import (  # noqa: F401  (fixture)
    S_OUTSIDE,
    S_PROJECT,
    S_USER,
    _fsRunAsUser,
    liveContainer,
)
from vaibify.docker.confinedRead import ContainerReadRefusedError
from vaibify.docker.confinedWrite import (
    ContainerWriteExistsError,
    ContainerWriteRefusedError,
    T_WRITE_DENYLISTED_NAMES,
)
from vaibify.docker.dockerConnection import I_MAX_FETCH_FILE_BYTES

pytestmark = pytest.mark.docker_live

I_MEBIBYTE = 1 << 20
I_LARGE_BYTES = I_MAX_FETCH_FILE_BYTES + 6 * I_MEBIBYTE


class _PatternSource:
    """A readable of ``iSize`` bytes that never holds more than a chunk."""

    def __init__(self, iSize):
        self._iRemaining = iSize
        self._baBlock = bytes(range(256)) * 4096
        self.hasherAll = hashlib.sha256()
        self.iLargestRead = 0

    def read(self, iSize=-1):
        iWanted = self._iRemaining if iSize < 0 else min(iSize, self._iRemaining)
        baChunk = (self._baBlock * (iWanted // len(self._baBlock) + 1))[:iWanted]
        self._iRemaining -= len(baChunk)
        self.iLargestRead = max(self.iLargestRead, len(baChunk))
        self.hasherAll.update(baChunk)
        return baChunk


def _fsContainerSha256(container, sPath):
    iExit, sOutput = _fsRunAsUser(container, f"sha256sum {sPath}")
    assert iExit == 0, sOutput
    return sOutput.split()[0]


def _fnWriteLargeFile(container, connection, sPath):
    sourceLarge = _PatternSource(I_LARGE_BYTES)
    tracemalloc.start()
    try:
        connection.fnWriteFileFromStream(
            container.id, sPath, sourceLarge,
            iExpectedBytes=I_LARGE_BYTES, sAuthorizedRoot=S_PROJECT,
            tForbiddenNames=T_WRITE_DENYLISTED_NAMES)
        _, iPeakBytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return sourceLarge, iPeakBytes


def testAFilePastTheFetchCeilingIsWrittenIntactInBoundedMemory(liveContainer):
    container, connection = liveContainer
    sTarget = f"{S_PROJECT}/docs/large.bin"
    sourceLarge, iPeakBytes = _fnWriteLargeFile(container, connection, sTarget)
    assert _fsContainerSha256(container, sTarget) == (
        sourceLarge.hasherAll.hexdigest())
    iExit, sStat = _fsRunAsUser(container, f"stat -c '%a %U %s' {sTarget}")
    assert sStat.split() == ["644", S_USER, str(I_LARGE_BYTES)]
    assert sourceLarge.iLargestRead <= I_MEBIBYTE
    assert iPeakBytes < 24 * I_MEBIBYTE, iPeakBytes


def testThePriorHashOfAFilePastTheCeilingIsARealHash(liveContainer):
    container, connection = liveContainer
    sTarget = f"{S_PROJECT}/docs/large.bin"
    if _fsRunAsUser(container, f"test -f {sTarget}")[0] != 0:
        _fnWriteLargeFile(container, connection, sTarget)
    sDigest = connection.fsHashContainerFileSha256(container.id, sTarget)
    assert sDigest == _fsContainerSha256(container, sTarget)
    from vaibify.gui.routeContext import fsHashContainerFileOrEmpty
    assert fsHashContainerFileOrEmpty(
        {"docker": connection}, container.id, sTarget) == sDigest


def testALargeFileStreamsBackIntactInBoundedChunksAndMemory(liveContainer):
    container, connection = liveContainer
    sTarget = f"{S_PROJECT}/docs/large.bin"
    if _fsRunAsUser(container, f"test -f {sTarget}")[0] != 0:
        _fnWriteLargeFile(container, connection, sTarget)
    hasherRead = hashlib.sha256()
    iLargestChunk = 0
    tracemalloc.start()
    try:
        for baChunk in connection.fiterReadFileConfined(
            container.id, sTarget, sAuthorizedRoot=S_PROJECT,
        ):
            hasherRead.update(baChunk)
            iLargestChunk = max(iLargestChunk, len(baChunk))
        _, iPeakBytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert hasherRead.hexdigest() == _fsContainerSha256(container, sTarget)
    assert iLargestChunk <= I_MEBIBYTE
    assert iPeakBytes < 24 * I_MEBIBYTE, iPeakBytes


def testReplacementAndShortBodiesAreRefusedByTheRealProgram(liveContainer):
    container, connection = liveContainer
    sTarget = f"{S_PROJECT}/docs/kept.txt"
    connection.fnWriteFile(container.id, sTarget, b"original")
    with pytest.raises(ContainerWriteExistsError):
        connection.fnWriteFileFromStream(
            container.id, sTarget, io.BytesIO(b"replacement"),
            bReplaceAllowed=False, sAuthorizedRoot=S_PROJECT)
    with pytest.raises(ContainerWriteRefusedError):
        connection.fnWriteFileFromStream(
            container.id, sTarget, io.BytesIO(b"short"),
            iExpectedBytes=1000, sAuthorizedRoot=S_PROJECT)
    assert connection.fbaFetchFile(container.id, sTarget) == b"original"
    iExit, sListing = _fsRunAsUser(
        container, f"ls -A {S_PROJECT}/docs | grep -c vaibify-write- ; true")
    assert sListing.strip() == "0"


def testTheConfinedReadFollowsInRootLinksAndRefusesTheRest(liveContainer):
    container, connection = liveContainer
    iExit, sOutput = _fsRunAsUser(
        container,
        f"cd {S_PROJECT} && echo inside > read-target.txt && "
        f"ln -sfn read-target.txt read-link && "
        f"echo secret > {S_OUTSIDE}/secret.txt && "
        f"ln -sfn {S_OUTSIDE}/secret.txt read-escape && "
        f"ln -sfn {S_OUTSIDE} read-dir-escape && "
        f"rm -f read-fifo && mkfifo read-fifo")
    assert iExit == 0, sOutput

    def fbaRead(sName):
        return b"".join(connection.fiterReadFileConfined(
            container.id, f"{S_PROJECT}/{sName}", sAuthorizedRoot=S_PROJECT))

    assert fbaRead("read-link") == b"inside\n"
    with pytest.raises(ContainerReadRefusedError, match="read-escape"):
        fbaRead("read-escape")
    with pytest.raises(ContainerReadRefusedError):
        fbaRead("read-dir-escape/secret.txt")
    with pytest.raises(ContainerReadRefusedError, match="regular file"):
        fbaRead("read-fifo")
    with pytest.raises(FileNotFoundError):
        fbaRead("no-such-file")


def testAFolderArchiveRoundTripsTheTreeAsTheContainerSeesIt(liveContainer):
    container, connection = liveContainer
    iExit, sOutput = _fsRunAsUser(
        container,
        f"rm -rf {S_PROJECT}/tree && mkdir -p {S_PROJECT}/tree/a/b "
        f"{S_PROJECT}/tree/empty && "
        f"echo one > {S_PROJECT}/tree/a/one.txt && "
        f"head -c 300000 /dev/urandom > {S_PROJECT}/tree/a/b/blob.bin && "
        f"ln -s a/one.txt {S_PROJECT}/tree/inner-link && "
        f"ln -s {S_OUTSIDE} {S_PROJECT}/tree/outer-link && "
        f"mkfifo {S_PROJECT}/tree/pipe")
    assert iExit == 0, sOutput
    baArchive = b"".join(connection.fiterReadDirectoryAsTar(
        container.id, f"{S_PROJECT}/tree", sAuthorizedRoot=S_PROJECT))
    with tarfile.open(fileobj=io.BytesIO(baArchive)) as tarIn:
        dictMembers = {infoMember.name: infoMember for infoMember in tarIn}
        baBlob = tarIn.extractfile("tree/a/b/blob.bin").read()
    assert dictMembers["tree/empty"].isdir()
    assert dictMembers["tree/inner-link"].issym()
    assert dictMembers["tree/outer-link"].issym()
    assert "tree/pipe" not in dictMembers
    assert not [sName for sName in dictMembers
                if sName.startswith("tree/outer-link/")]
    assert hashlib.sha256(baBlob).hexdigest() == _fsContainerSha256(
        container, f"{S_PROJECT}/tree/a/b/blob.bin")


def testNoOutsideByteIsEverReadWhileADirectoryIsSwappedForASymlink(
    liveContainer,
):
    """The read race, live: a swapper flips a parent between directory and link.

    Every read either returns the project's bytes, is refused, or fails
    because the parent momentarily did not exist; none may return the
    bytes of the file the link points at.

    A SMOKE test, not a proof. The window a swap must hit is a few
    microseconds wide and a live swapper rarely lands in it: on
    2026-10-06 a mutation that opened the final component by name (the
    exact defect this guards) did NOT make this test fail. The proof is
    the deterministic hook in ``tests/testConfinedRead.py::
    testAComponentSwappedAfterTheCheckCannotRedirectTheRead``, which is
    kill-confirmed against that mutation.
    """
    container, connection = liveContainer
    _fsRunAsUser(
        container,
        f"rm -rf {S_PROJECT}/racing {S_OUTSIDE}/racing-secret.txt; "
        f"mkdir -p {S_PROJECT}/racing && echo INSIDE > {S_PROJECT}/racing/data.txt "
        f"&& mkdir -p {S_OUTSIDE}/racing && echo OUTSIDE > {S_OUTSIDE}/racing/data.txt")
    sSwapper = (
        f"cd {S_PROJECT}; while true; do "
        f"mv racing racing.kept 2>/dev/null; ln -s {S_OUTSIDE}/racing racing; "
        f"rm racing; mv racing.kept racing; done"
    )
    container.exec_run(["sh", "-c", sSwapper], user=S_USER, detach=True)
    dictOutcomes = {"inside": 0, "refused": 0, "failed": 0}
    fDeadline = time.monotonic() + 20
    iAttempt = 0
    while time.monotonic() < fDeadline and iAttempt < 150:
        iAttempt += 1
        try:
            baRead = b"".join(connection.fiterReadFileConfined(
                container.id, f"{S_PROJECT}/racing/data.txt",
                sAuthorizedRoot=S_PROJECT))
            assert baRead == b"INSIDE\n", baRead
            dictOutcomes["inside"] += 1
        except ContainerReadRefusedError:
            dictOutcomes["refused"] += 1
        except OSError:
            dictOutcomes["failed"] += 1
    container.exec_run(["pkill", "-f", "mv racing"], user=S_USER)
    _fsRunAsUser(container, "pkill -f 'while true' ; true")
    assert sum(dictOutcomes.values()) == iAttempt
