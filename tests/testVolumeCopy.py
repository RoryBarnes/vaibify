"""The volume copier's profile, its exec user, and its content manifest.

The volume copier is the one disposable container that runs as root
(ruling D13), so its profile is pinned here EXACTLY: a widened
capability set, a host mount, a network or a writable source fails this
file and comes back for a new ruling. Which user an exec runs as is
read from the profile recorded with the handle's reservation, never a
caller argument, and every other profile keeps the unprivileged user.

The manifest is what makes a copied volume trustworthy, so each field a
copy could get wrong is driven separately against the real program
text, run by a real interpreter over a real tree: a byte flipped
without changing the length, a mode, an owner and a symlink target.
"""

import contextlib
import io
import json
import os
import subprocess
import sys

import pytest

from vaibify.docker import disposableContainer, disposableSpecification
from vaibify.docker import volumeCopy


S_IMAGE_ID = "sha256:" + "c" * 64


# ---------------------------------------------------------------------
# The profile, pinned.
# ---------------------------------------------------------------------


def testTheVolumeCopierProfileIsExactlyTheApprovedOne():
    """Any widening of this dictionary is a new ruling, not an edit."""
    dictSpecification = (
        disposableSpecification.fdictComposeVolumeCopierSpecification(
            S_IMAGE_ID, "reservation-1", "old-workspace", "new-workspace"))
    dictKeywords = dict(dictSpecification["dictCreateKeywords"])
    sName = dictKeywords.pop("name")
    assert sName == dictSpecification["sContainerName"]
    dictDefaults = disposableSpecification.fdictBuildDefaultLimits()
    assert dictKeywords == {
        "entrypoint": ["/bin/sh"],
        "command": ["-c", "sleep 2147483647"],
        "user": "0:0",
        "cap_drop": ["ALL"],
        "cap_add": ["CHOWN", "FOWNER", "DAC_OVERRIDE", "DAC_READ_SEARCH"],
        "security_opt": ["no-new-privileges:true"],
        "network_mode": "none",
        "ipc_mode": "private",
        "read_only": True,
        "tmpfs": {"/tmp": (
            f"size={dictDefaults['iScratchBytes']},mode=1777")},
        "mem_limit": dictDefaults["iMemoryBytes"],
        "memswap_limit": dictDefaults["iMemoryBytes"],
        "nano_cpus": int(dictDefaults["fCpuCount"] * 1_000_000_000),
        "pids_limit": dictDefaults["iPidsLimit"],
        "labels": {
            "vaibify-disposable": "reservation-1",
            "vaibify-disposable-role": "volumeCopier",
            "vaibify-disposable-resource": "",
        },
        "volumes": {
            "old-workspace": {"bind": "/source", "mode": "ro"},
            "new-workspace": {"bind": "/destination", "mode": "rw"},
        },
    }
    assert dictSpecification["sExecUser"] == "0:0"


def testEveryOtherProfileExecsAsTheUnprivilegedUser():
    for sRole in ("shadow", "council", "runner"):
        dictSpecification = (
            disposableSpecification.fdictComposeCreateSpecification(
                "image:tag", "reservation-1", sRole))
        assert dictSpecification["sExecUser"] == "1000:1000"
        assert dictSpecification["dictCreateKeywords"]["user"] == "1000:1000"
        assert "volumes" not in dictSpecification["dictCreateKeywords"]
        assert "cap_add" not in dictSpecification["dictCreateKeywords"]


@pytest.mark.parametrize("sImage,sSource,sDestination", [
    ("project:latest", "a-workspace", "b-workspace"),
    (S_IMAGE_ID, "/home/researcher", "b-workspace"),
    (S_IMAGE_ID, "a-workspace", "../b"),
    (S_IMAGE_ID, "same-volume", "same-volume"),
])
def testTheCopierRefusesAnythingButAnImageIdAndTwoVolumes(
    sImage, sSource, sDestination,
):
    """A tag would pull; a path would be a host mount; one volume twice
    would copy a volume onto itself."""
    with pytest.raises(ValueError):
        disposableSpecification.fdictComposeVolumeCopierSpecification(
            sImage, "reservation-1", sSource, sDestination)


class _RecordingApi:
    """Records each exec's user; a container whose every exec succeeds."""

    def __init__(self):
        self.listUsers = []

    def exec_create(self, sContainerId, listCommand, **dictKeywords):
        self.listUsers.append(dictKeywords["user"])
        return {"Id": f"exec{len(self.listUsers)}"}

    def exec_start(self, sExecId, socket=True):
        raise RuntimeError("the recording API opens no stream")


class _RecordingContainers:
    def create(self, sImage, **dictKeywords):
        return type("Created", (), {"id": "c" * 64, "start": lambda s: None})()


class _RecordingDocker:
    def __init__(self):
        self.api = _RecordingApi()
        self.containers = _RecordingContainers()


@pytest.mark.parametrize("bCopier,sExpectedUser", [
    (True, "0:0"), (False, "1000:1000"),
])
def testTheExecUserIsTheOneRecordedWithTheReservation(bCopier, sExpectedUser):
    dockerFake = _RecordingDocker()
    dictGateway = disposableContainer.fdictCreateDisposableGateway(dockerFake)
    if bCopier:
        dictCreated = disposableContainer.fdictReserveAndCreateVolumeCopier(
            dictGateway, S_IMAGE_ID, "old-workspace", "new-workspace")
    else:
        dictCreated = disposableContainer.fdictReserveAndCreateContainer(
            dictGateway, "shadow", S_IMAGE_ID)
    dictHandle = dictGateway["dictHandlesById"][dictCreated["sHandle"]]
    with pytest.raises(RuntimeError):
        disposableContainer._ftStartExecStream(
            dockerFake, dictHandle, ["id", "-u"], bStdin=False)
    assert dockerFake.api.listUsers == [sExpectedUser]


# ---------------------------------------------------------------------
# The manifest, field by field, against the real program text.
# ---------------------------------------------------------------------


def _fdictRunProgram(sProgram, *listArguments):
    """Run one of the copier's programs with a real interpreter."""
    processResult = subprocess.run(
        [sys.executable, "-I", "-B", "-c", sProgram, *listArguments],
        capture_output=True, text=True, check=True,
    )
    return json.loads(processResult.stdout.strip().splitlines()[-1])


def _fnBuildTree(sRoot):
    """A tree carrying every shape the copy must preserve."""
    os.makedirs(os.path.join(sRoot, "data", "nested"))
    for sRelative, baBody, iMode in (
        ("data/results.csv", b"1,2,3\n", 0o640),
        ("data/nested/private.key", b"secret-bytes", 0o600),
        ("run.sh", b"#!/bin/sh\necho hi\n", 0o755),
    ):
        sPath = os.path.join(sRoot, sRelative)
        with open(sPath, "wb") as fileHandle:
            fileHandle.write(baBody)
        os.chmod(sPath, iMode)
    os.link(os.path.join(sRoot, "data", "results.csv"),
            os.path.join(sRoot, "data", "linked.csv"))
    os.symlink("aaaa", os.path.join(sRoot, "pointer"))
    os.mkfifo(os.path.join(sRoot, "pipe"))
    os.chmod(os.path.join(sRoot, "data", "nested"), 0o750)
    for sDirectory, listDirectories, listFiles in os.walk(sRoot):
        for sName in listDirectories + listFiles:
            os.utime(os.path.join(sDirectory, sName), ns=(10**18, 10**18),
                     follow_symlinks=False)
    os.utime(sRoot, ns=(10**18, 10**18))


@pytest.fixture
def tCopiedTree(tmp_path):
    """Build a tree, copy it with the real copy program; return both roots."""
    sSource = str(tmp_path / "source")
    sDestination = str(tmp_path / "destination")
    os.makedirs(sSource)
    os.makedirs(sDestination)
    _fnBuildTree(sSource)
    dictReport = _fdictRunProgram(
        volumeCopy.S_COPY_PROGRAM, sSource, sDestination)
    assert dictReport["iEntries"] == 9, dictReport
    return sSource, sDestination


def _fsDigest(sRoot):
    return _fdictRunProgram(volumeCopy.S_MANIFEST_PROGRAM, sRoot)["sDigest"]


def testAFaithfulCopyHasTheSameManifest(tCopiedTree):
    sSource, sDestination = tCopiedTree
    assert _fsDigest(sSource) == _fsDigest(sDestination)
    statOne = os.lstat(os.path.join(sDestination, "data", "results.csv"))
    statTwo = os.lstat(os.path.join(sDestination, "data", "linked.csv"))
    assert statOne.st_ino == statTwo.st_ino
    assert os.readlink(os.path.join(sDestination, "pointer")) == "aaaa"


def _fnRestoreTimes(sPath):
    os.utime(sPath, ns=(10**18, 10**18), follow_symlinks=False)


@pytest.mark.falsification
def testOneByteFlippedWithoutChangingTheLengthChangesTheManifest(tCopiedTree):
    """Kills: a manifest reduced to the file count and the total bytes."""
    sSource, sDestination = tCopiedTree
    sPath = os.path.join(sDestination, "data", "nested", "private.key")
    with open(sPath, "r+b") as fileHandle:
        fileHandle.write(b"S")
    _fnRestoreTimes(sPath)
    assert _fsDigest(sSource) != _fsDigest(sDestination)


@pytest.mark.falsification
def testAChangedModeChangesTheManifest(tCopiedTree):
    """Kills: dropping the permission bits from the manifest."""
    sSource, sDestination = tCopiedTree
    os.chmod(os.path.join(sDestination, "run.sh"), 0o700)
    assert _fsDigest(sSource) != _fsDigest(sDestination)


@pytest.mark.falsification
def testAChangedSymlinkTargetChangesTheManifest(tCopiedTree):
    """Kills: dropping a symlink's target from the manifest.

    The new target has the old one's length, so only the target itself
    can tell them apart.
    """
    sSource, sDestination = tCopiedTree
    sPath = os.path.join(sDestination, "pointer")
    os.unlink(sPath)
    os.symlink("bbbb", sPath)
    _fnRestoreTimes(sPath)
    _fnRestoreTimes(sDestination)
    assert _fsDigest(sSource) != _fsDigest(sDestination)


def _fsDigestSeeingAnotherOwner(sRoot, sRelative):
    """Run the manifest in-process, with one entry reporting uid + 4242.

    Changing a file's owner needs root, which the suite does not have,
    so the stat the program reads is adjusted instead; the program text
    is the real one, executed as written.
    """
    fnRealLstat = os.lstat
    sTarget = os.path.normpath(os.path.join(sRoot, sRelative))

    def fstatAdjusted(sPath, *aArgs, **dictKeywords):
        statReal = fnRealLstat(sPath, *aArgs, **dictKeywords)
        if os.path.normpath(sPath) != sTarget:
            return statReal
        listFields = list(statReal)
        listFields[4] += 4242
        return os.stat_result(listFields, {
            sName: getattr(statReal, sName) for sName in (
                "st_atime_ns", "st_mtime_ns", "st_ctime_ns")})
    bufferOutput = io.StringIO()
    listSavedArguments = sys.argv
    try:
        os.lstat = fstatAdjusted
        sys.argv = ["manifest", sRoot]
        with contextlib.redirect_stdout(bufferOutput):
            exec(compile(volumeCopy.S_MANIFEST_PROGRAM, "manifest", "exec"),
                 {"__name__": "__main__"})
    finally:
        os.lstat = fnRealLstat
        sys.argv = listSavedArguments
    return json.loads(bufferOutput.getvalue().splitlines()[-1])["sDigest"]


@pytest.mark.falsification
def testAChangedOwnerChangesTheManifest(tCopiedTree):
    """Kills: dropping the numeric owner from the manifest."""
    sSource, sDestination = tCopiedTree
    sUnchanged = _fsDigestSeeingAnotherOwner(sDestination, "absent")
    assert sUnchanged == _fsDigest(sSource)
    assert _fsDigestSeeingAnotherOwner(
        sDestination, "data/results.csv") != _fsDigest(sSource)


def testTheCopyRefusesADestinationThatIsNotEmpty(tmp_path):
    sSource = str(tmp_path / "source")
    sDestination = str(tmp_path / "destination")
    os.makedirs(sSource)
    os.makedirs(os.path.join(sDestination, "already"))
    dictReport = _fdictRunProgram(
        volumeCopy.S_COPY_PROGRAM, sSource, sDestination)
    assert dictReport == {"sRefusal": "the destination volume is not empty"}
