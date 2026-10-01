"""Branch coverage for the pure half of the disposable-container lifecycle.

``disposableSpecification`` composes the fixed security posture, refuses
resource limits that would turn a disk bound into an out-of-memory kill,
validates and ownership-stamps every tar member copied into a
disposable container, and pumps a multiplexed exec stream under a byte
cap and a deadline. Each of those is a refusal or a bound, so the tests
below drive the refusing branch and assert the message or the flag,
not merely that the code ran. The only boundary faked is the raw exec
socket (and, where a deadline must elapse, the monotonic clock).
"""

import io
import socket
import tarfile
import time

import pytest

from vaibify.docker import daemonCapacity, disposableSpecification
from vaibify.docker.dockerConnection import (
    _I_CONTAINER_DEFAULT_GID,
    _I_CONTAINER_DEFAULT_UID,
)


class _ScriptedSocket:
    """A raw exec socket that replays scripted recv results and records sends."""

    def __init__(self, listRecvResults=None, listSendResults=None):
        self.listRecvResults = list(listRecvResults or [])
        self.listSendResults = list(listSendResults or [])
        self.listSentChunks = []

    def recv(self, iByteCount):
        del iByteCount
        if not self.listRecvResults:
            return b""
        resultNext = self.listRecvResults.pop(0)
        if isinstance(resultNext, BaseException):
            raise resultNext
        return resultNext

    def send(self, baChunk):
        if self.listSendResults:
            resultNext = self.listSendResults.pop(0)
            if isinstance(resultNext, BaseException):
                raise resultNext
            self.listSentChunks.append(bytes(baChunk[:resultNext]))
            return resultNext
        self.listSentChunks.append(bytes(baChunk))
        return len(baChunk)


def fbaBuildFrame(baPayload, iStreamType=1):
    """Return one Docker multiplexed-stream frame carrying ``baPayload``."""
    return (
        bytes([iStreamType, 0, 0, 0])
        + len(baPayload).to_bytes(4, "big")
        + baPayload
    )


def fbaBuildArchive(listMembers):
    """Return tar bytes built from ``(TarInfo, bytes-or-None)`` pairs."""
    bufferArchive = io.BytesIO()
    with tarfile.open(fileobj=bufferArchive, mode="w") as fileTar:
        for infoMember, baContent in listMembers:
            if baContent is None:
                fileTar.addfile(infoMember)
            else:
                infoMember.size = len(baContent)
                fileTar.addfile(infoMember, io.BytesIO(baContent))
    return bufferArchive.getvalue()


def finfoBuildMember(sName, iType=tarfile.REGTYPE, sLinkName="",
                     iMode=0o644):
    """Return a root-owned tar member, as an unstamped archive would carry."""
    infoMember = tarfile.TarInfo(sName)
    infoMember.type = iType
    infoMember.linkname = sLinkName
    infoMember.mode = iMode
    infoMember.uid = 0
    infoMember.gid = 0
    infoMember.uname = "root"
    infoMember.gname = "root"
    return infoMember


def flistReadRepackedMembers(bufferRepacked):
    """Return every member of a repacked archive, in stream order."""
    with tarfile.open(fileobj=bufferRepacked, mode="r") as fileTar:
        return fileTar.getmembers()


# ----- resource-limit validation and the create specification ----------


def testScratchAtOrAboveTheMemoryLimitIsRefused():
    """A scratch tmpfs as large as memory would replace the disk bound."""
    dictLimits = {"fCpuCount": 1.0, "iMemoryBytes": 1000,
                  "iPidsLimit": 64, "iScratchBytes": 1000}
    with pytest.raises(ValueError, match="must stay below the memory limit"):
        disposableSpecification.fdictComposeCreateSpecification(
            "imageAlpha:latest", "reservationAlpha", "shadow", dictLimits)


@pytest.mark.parametrize("fCpuCount, iPidsLimit", [(0.0, 64), (1.0, 0),
                                                   (-1.0, 64)])
def testNonPositiveCpuOrPidBoundsAreRefused(fCpuCount, iPidsLimit):
    """A zero or negative CPU / PID bound is not a bound at all."""
    dictLimits = {"fCpuCount": fCpuCount, "iMemoryBytes": 2000,
                  "iPidsLimit": iPidsLimit, "iScratchBytes": 1000}
    with pytest.raises(ValueError, match="must both be positive"):
        disposableSpecification.fdictComposeCreateSpecification(
            "imageAlpha:latest", "reservationAlpha", "shadow", dictLimits)


def testOmittedLimitsResolveToTheDaemonCapacityFloors():
    """No limits means the floors, never an unbounded container."""
    dictSpecification = (
        disposableSpecification.fdictComposeCreateSpecification(
            "imageAlpha:latest", "reservationAlpha", "shadow"))
    dictKeywords = dictSpecification["dictCreateKeywords"]
    dictFloor = daemonCapacity.fdictFloorDaemonCapacity()
    assert dictKeywords["mem_limit"] == dictFloor["iContainerMemoryBytes"]
    assert dictKeywords["memswap_limit"] == dictFloor["iContainerMemoryBytes"]
    assert dictKeywords["tmpfs"] == {
        disposableSpecification.S_SCRATCH_ROOT: (
            f"size={dictFloor['iContainerScratchBytes']},mode=1777"),
    }
    assert dictKeywords["nano_cpus"] == 2_000_000_000
    assert dictKeywords["pids_limit"] == 512


def testDefaultSpecificationIsIsolatedAndCarriesNoPlatformRequest():
    """The fail-closed defaults: no network, writable root, no platform key."""
    dictSpecification = (
        disposableSpecification.fdictComposeCreateSpecification(
            "imageAlpha:latest", "reservationAlpha", "shadow"))
    dictKeywords = dictSpecification["dictCreateKeywords"]
    assert "platform" not in dictKeywords
    assert dictKeywords["network_mode"] == "none"
    assert dictKeywords["read_only"] is False
    assert dictKeywords["cap_drop"] == ["ALL"]
    assert dictKeywords["security_opt"] == ["no-new-privileges:true"]
    assert dictKeywords["ipc_mode"] == "private"
    assert dictKeywords["user"] == (
        f"{_I_CONTAINER_DEFAULT_UID}:{_I_CONTAINER_DEFAULT_GID}")
    assert dictKeywords["labels"][
        disposableSpecification.S_DISPOSABLE_RESOURCE_LABEL] == ""


def testCallerChoicesReachTheSpecificationAndNothingElseMoves():
    """Network, platform, read-only root and the resource stamp pass through."""
    dictLimits = {"fCpuCount": 0.5, "iMemoryBytes": 4096,
                  "iPidsLimit": 32, "iScratchBytes": 1024}
    dictSpecification = (
        disposableSpecification.fdictComposeCreateSpecification(
            "imageAlpha:latest", "reservationAlpha", "council",
            dictLimits=dictLimits, sNetworkName="networkEgress",
            sResourceName="projectContainerName",
            bReadOnlyRootFilesystem=1, sPlatform="linux/arm64"))
    dictKeywords = dictSpecification["dictCreateKeywords"]
    assert dictKeywords["platform"] == "linux/arm64"
    assert dictKeywords["network_mode"] == "networkEgress"
    assert dictKeywords["read_only"] is True
    assert dictKeywords["nano_cpus"] == 500_000_000
    assert dictKeywords["labels"] == {
        disposableSpecification.S_DISPOSABLE_LABEL: "reservationAlpha",
        disposableSpecification.S_DISPOSABLE_ROLE_LABEL: "council",
        disposableSpecification.S_DISPOSABLE_RESOURCE_LABEL:
            "projectContainerName",
    }
    assert dictSpecification["sRole"] == "council"
    assert dictSpecification["sContainerName"].startswith(
        "vaibifyDisposableCouncil")
    assert dictKeywords["name"] == dictSpecification["sContainerName"]


def testEveryComposedContainerNameIsDistinct():
    """Two specifications for the same role never collide on the daemon."""
    setNames = {
        disposableSpecification.fdictComposeCreateSpecification(
            "imageAlpha:latest", "reservationAlpha", "shadow",
        )["sContainerName"]
        for _ in range(8)
    }
    assert len(setNames) == 8


# ----- archive validation, stamping and relocation ---------------------


@pytest.mark.parametrize("sMemberName", ["/etc/passwdCopy", "../escaped.txt",
                                         "inner/../../escaped.txt"])
def testArchiveMemberEscapingTheRootIsRefused(sMemberName):
    """An absolute or ``..`` member would land outside the extraction root."""
    baArchive = fbaBuildArchive([(finfoBuildMember(sMemberName), b"x")])
    with pytest.raises(ValueError, match="escapes the extraction root"):
        disposableSpecification.fbufferRepackArchiveStamped(baArchive)


@pytest.mark.parametrize("sMemberName, iLinkType, sLinkName", [
    ("repo/linkAlpha", tarfile.SYMTYPE, "/etc/shadowTarget"),
    ("repo/linkAlpha", tarfile.SYMTYPE, "../../outsideTarget"),
    ("repo/linkAlpha", tarfile.LNKTYPE, "/etc/shadowTarget"),
    ("linkAlpha", tarfile.LNKTYPE, "../outsideTarget"),
])
def testLinkMemberTargetingOutsideTheRootIsRefused(
        sMemberName, iLinkType, sLinkName):
    """A symlink or hard link may not point outside the extraction root."""
    baArchive = fbaBuildArchive([
        (finfoBuildMember(sMemberName, iLinkType, sLinkName), None),
    ])
    with pytest.raises(ValueError, match="outside the extraction root"):
        disposableSpecification.fbufferRepackArchiveStamped(baArchive)


@pytest.mark.xfail(strict=True, raises=pytest.fail.Exception, reason=(
    "A tar hard-link target is resolved against the extraction ROOT, but "
    "_fnValidateArchiveMember resolves it against the member's own "
    "directory, so a nested hard link naming ../outside is accepted."))
def testNestedHardLinkEscapingTheRootIsRefused():
    """A hard link's target is root-relative, whatever the member's depth."""
    baArchive = fbaBuildArchive([
        (finfoBuildMember("repo/linkAlpha", tarfile.LNKTYPE,
                          "../outsideTarget"), None),
    ])
    with pytest.raises(ValueError, match="outside the extraction root"):
        disposableSpecification.fbufferRepackArchiveStamped(baArchive)


def testLinkMemberInsideTheRootIsKeptAndStamped():
    """A relative link that stays inside the root survives the repack."""
    baArchive = fbaBuildArchive([
        (finfoBuildMember("repo/dataFile.csv"), b"a,b\n"),
        (finfoBuildMember("repo/linkAlpha", tarfile.SYMTYPE,
                          "dataFile.csv"), None),
    ])
    listMembers = flistReadRepackedMembers(
        disposableSpecification.fbufferRepackArchiveStamped(baArchive))
    dictByName = {infoMember.name: infoMember for infoMember in listMembers}
    assert dictByName["repo/linkAlpha"].issym()
    assert dictByName["repo/linkAlpha"].linkname == "dataFile.csv"
    assert dictByName["repo/linkAlpha"].uid == _I_CONTAINER_DEFAULT_UID


@pytest.mark.parametrize("sPrefix", ["/absolutePrefix", "../upward",
                                     "inner/../../upward"])
def testEscapingArchivePrefixIsRefused(sPrefix):
    """A relocation prefix is held to the same rule as every member."""
    baArchive = fbaBuildArchive([(finfoBuildMember("dataFile.csv"), b"x")])
    with pytest.raises(ValueError, match="must be a relative path"):
        disposableSpecification.fbufferRepackArchiveStamped(
            baArchive, sPathPrefix=sPrefix)


@pytest.mark.parametrize("sPrefix", [".", "/", ""])
def testTrivialArchivePrefixRelocatesNothing(sPrefix):
    """A prefix of ``.`` or ``/`` normalizes to no relocation at all."""
    baArchive = fbaBuildArchive([(finfoBuildMember("dataFile.csv"), b"x")])
    listMembers = flistReadRepackedMembers(
        disposableSpecification.fbufferRepackArchiveStamped(
            baArchive, sPathPrefix=sPrefix))
    assert [infoMember.name for infoMember in listMembers] == [
        "dataFile.csv"]


def testPrefixedArchiveSynthesizesStampedParentsAndKeepsContent():
    """Every parent directory is emitted, stamped, before the file it holds."""
    baArchive = fbaBuildArchive([
        (finfoBuildMember("data/nested/dataFile.csv"), b"1,2,3\n"),
    ])
    bufferRepacked = disposableSpecification.fbufferRepackArchiveStamped(
        baArchive, sPathPrefix="repoAlpha")
    with tarfile.open(fileobj=bufferRepacked, mode="r") as fileTar:
        listMembers = fileTar.getmembers()
        baContent = fileTar.extractfile(
            "repoAlpha/data/nested/dataFile.csv").read()
    assert [infoMember.name for infoMember in listMembers] == [
        "repoAlpha", "repoAlpha/data", "repoAlpha/data/nested",
        "repoAlpha/data/nested/dataFile.csv",
    ]
    assert baContent == b"1,2,3\n"
    for infoMember in listMembers:
        assert (infoMember.uid, infoMember.gid) == (
            _I_CONTAINER_DEFAULT_UID, _I_CONTAINER_DEFAULT_GID)
        assert infoMember.uname == "" and infoMember.gname == ""
    assert all(infoMember.isdir() for infoMember in listMembers[:3])


def testDirectoryMemberAlreadySynthesizedIsNotEmittedTwice():
    """A directory named after its own child arrives once, not twice."""
    baArchive = fbaBuildArchive([
        (finfoBuildMember("data/dataFile.csv"), b"x"),
        (finfoBuildMember("data", tarfile.DIRTYPE, iMode=0o755), None),
    ])
    listMembers = flistReadRepackedMembers(
        disposableSpecification.fbufferRepackArchiveStamped(baArchive))
    listNames = [infoMember.name.rstrip("/") for infoMember in listMembers]
    assert listNames.count("data") == 1
    assert listNames == ["data", "data/dataFile.csv"]


def testExplicitDirectoryMemberGainsOwnerWriteAccess():
    """A read-only directory in the archive stays writable by its owner."""
    baArchive = fbaBuildArchive([
        (finfoBuildMember("lockedDirectory", tarfile.DIRTYPE,
                          iMode=0o500), None),
        (finfoBuildMember("lockedDirectory/dataFile.csv"), b"x"),
    ])
    listMembers = flistReadRepackedMembers(
        disposableSpecification.fbufferRepackArchiveStamped(baArchive))
    infoDirectory = listMembers[0]
    assert infoDirectory.name == "lockedDirectory"
    assert infoDirectory.mode & 0o700 == 0o700
    assert infoDirectory.uid == _I_CONTAINER_DEFAULT_UID
    assert [infoMember.name for infoMember in listMembers].count(
        "lockedDirectory") == 1


def testEmptyFileEntryIsStampedToTheContainerUser():
    """The marker-file member carries the uid-1000 contract, never root."""
    infoMember = disposableSpecification.finfoBuildEmptyFileEntry(
        "repoAlpha/.markerFile")
    assert infoMember.name == "repoAlpha/.markerFile"
    assert infoMember.size == 0
    assert infoMember.mode == 0o644
    assert (infoMember.uid, infoMember.gid) == (
        _I_CONTAINER_DEFAULT_UID, _I_CONTAINER_DEFAULT_GID)
    assert infoMember.uname == "" and infoMember.gname == ""


# ----- bounded socket send and the multiplexed stream pump -------------


def testSendAllBoundedDeliversEveryByteAcrossPartialSends():
    """Partial sends and a socket timeout still deliver the full payload."""
    baPayload = bytes(range(256)) * 300
    socketScripted = _ScriptedSocket(
        listSendResults=[1000, socket.timeout(), 65536])
    disposableSpecification.fnSendAllBounded(
        socketScripted, baPayload, time.monotonic() + 30.0)
    assert b"".join(socketScripted.listSentChunks) == baPayload
    assert len(socketScripted.listSentChunks[0]) == 1000
    assert all(len(baChunk) <= 65536
               for baChunk in socketScripted.listSentChunks)


def testSendAllBoundedRaisesOnceTheDeadlinePasses():
    """A past deadline refuses to stream rather than blocking forever."""
    socketScripted = _ScriptedSocket()
    with pytest.raises(RuntimeError, match="write timed out"):
        disposableSpecification.fnSendAllBounded(
            socketScripted, b"payloadBytes", time.monotonic() - 1.0)
    assert socketScripted.listSentChunks == []


def testSendAllBoundedWithEmptyPayloadSendsNothing():
    """An empty payload is already fully sent, even past its deadline."""
    socketScripted = _ScriptedSocket()
    disposableSpecification.fnSendAllBounded(
        socketScripted, b"", time.monotonic() - 1.0)
    assert socketScripted.listSentChunks == []


def testPumpReassemblesFramesSplitAcrossReads():
    """A frame split over two reads, and a stderr frame, are both captured."""
    baStream = fbaBuildFrame(b"hello ") + fbaBuildFrame(b"world", 2)
    socketScripted = _ScriptedSocket(
        [baStream[:5], socket.timeout(), baStream[5:17], baStream[17:]])
    dictPumped = disposableSpecification.fdictPumpBoundedExecStream(
        socketScripted, 1024, time.monotonic() + 30.0)
    assert dictPumped == {"baCaptured": b"hello world",
                          "bOutputCapExceeded": False,
                          "bDeadlineExceeded": False}


def testPumpTruncatesAtTheOutputCapAndFlagsIt():
    """Output beyond the cap is cut at exactly the cap and reported."""
    socketScripted = _ScriptedSocket(
        [fbaBuildFrame(b"0123456789"), fbaBuildFrame(b"never read")])
    dictPumped = disposableSpecification.fdictPumpBoundedExecStream(
        socketScripted, 4, time.monotonic() + 30.0)
    assert dictPumped["baCaptured"] == b"0123"
    assert dictPumped["bOutputCapExceeded"] is True
    assert dictPumped["bDeadlineExceeded"] is False
    assert len(socketScripted.listRecvResults) == 1


def testPumpReportsTheDeadlineWithoutReading():
    """A deadline already past ends the pump and says why."""
    socketScripted = _ScriptedSocket([fbaBuildFrame(b"unread")])
    dictPumped = disposableSpecification.fdictPumpBoundedExecStream(
        socketScripted, 1024, time.monotonic() - 1.0)
    assert dictPumped == {"baCaptured": b"", "bOutputCapExceeded": False,
                          "bDeadlineExceeded": True}
    assert len(socketScripted.listRecvResults) == 1


def testPumpStopsOnASocketErrorKeepingWhatArrived():
    """A transport error ends the read; neither bound is claimed breached."""
    socketScripted = _ScriptedSocket(
        [fbaBuildFrame(b"partial"), ConnectionResetError("reset by peer")])
    dictPumped = disposableSpecification.fdictPumpBoundedExecStream(
        socketScripted, 1024, time.monotonic() + 30.0)
    assert dictPumped == {"baCaptured": b"partial",
                          "bOutputCapExceeded": False,
                          "bDeadlineExceeded": False}


def testPumpDropsAnIncompleteTrailingFrame():
    """A header promising more bytes than arrived contributes nothing."""
    baTruncated = fbaBuildFrame(b"complete") + fbaBuildFrame(b"truncated")[:12]
    socketScripted = _ScriptedSocket([baTruncated])
    dictPumped = disposableSpecification.fdictPumpBoundedExecStream(
        socketScripted, 1024, time.monotonic() + 30.0)
    assert dictPumped["baCaptured"] == b"complete"


# ----- the OOM verdict --------------------------------------------------


def testOomCountParserReadsOnlyTheExactKey():
    """``oom_kill_disable`` must not be read as the kill count."""
    sCounterText = "oom_kill_disable 0\nunder_oom 0\noom_kill 3\n"
    assert disposableSpecification.fiParseOomKillCount(sCounterText) == 3
    assert disposableSpecification.fiParseOomKillCount("oom_kill_disable 1") \
        is None
    assert disposableSpecification.fiParseOomKillCount("oom_kill many") is None
    assert disposableSpecification.fiParseOomKillCount("") is None


@pytest.mark.parametrize("iBefore, iAfter, bState, bExpected", [
    (0, 1, False, True),
    (2, 2, False, False),
    (None, 5, False, False),
    (0, None, False, False),
    (None, None, True, True),
])
def testOomVerdictNeedsTwoReadableCountsOrTheStateFlag(
        iBefore, iAfter, bState, bExpected):
    """An unreadable counter is never concluded as zero."""
    assert disposableSpecification.fbConcludeOomKilled(
        iBefore, iAfter, bState) is bExpected
