"""The backend's container directory copy is unprivileged and symlink-safe.

Source: ``vaibify/docker/confinedWrite.py`` (the tree receiver program)
and ``DockerConnection.fnWriteTreeViaTar``.

The copy used to hand the daemon a tarball (``put_archive``), which the
daemon extracts as root while following every symlink inside the
container. Measured against a real daemon on 2026-10-03: a symlink the
container user planted at the destination, or at any directory on the
way to it, redirected the whole copy (trees landed in ``/usr/local/bin``
and ``/etc/profile.d``), and an archive member whose name matched an
existing root-owned directory in the redirect target changed that
directory's owner to the container user.

These tests drive the REAL receiver program in a real subprocess against
a real directory tree, with crafted archives, and through a fake daemon
that speaks the real framing over a real socket pair
(``tests/confinedWriteHarness.py``). The ownership flip and the race need
a root-capable container and live in ``tests/testConfinedTreeWriteLive.py``.
"""

import io
import os
import stat
import subprocess
import sys
import tarfile
from unittest.mock import MagicMock, patch

import pytest

from tests.confinedWriteHarness import ExecProgramDaemon
from vaibify.config import mutationAdmission
from vaibify.docker import confinedWrite
from vaibify.docker import dockerConnection as dockerConnectionModule
from vaibify.docker.confinedWrite import (
    ContainerWriteRefusedError,
    I_REFUSED_EXIT_CODE,
    fnRaiseWhenTreeWriteFailed,
    fsRenderConfinedTreeProgram,
)
from vaibify.docker.dockerConnection import DockerConnection


S_CONTAINER_ID = "8e1d44c0b7a2f9936d10"


def _fsRealPath(pathTmp):
    return os.path.realpath(str(pathTmp))


def _fexecuteProgram(sProgram, baStdin=b""):
    return subprocess.run(
        [sys.executable, "-c", sProgram], input=baStdin,
        capture_output=True,
    )


def _fbaBuildArchive(listEntries):
    """Return tar bytes for ``(sName, sKind, xValue, iMode)`` entries.

    The kinds are ``file`` (``xValue`` is the content), ``dir``,
    ``symlink`` and ``hardlink`` (``xValue`` is the link target),
    ``device`` and ``fifo``. Crafted by hand because the host-side
    builder never makes the dangerous ones.
    """
    fileBuffer = io.BytesIO()
    with tarfile.open(fileobj=fileBuffer, mode="w") as archive:
        for sName, sKind, xValue, iMode in listEntries:
            archive.addfile(*_ftBuildMember(sName, sKind, xValue, iMode))
    return fileBuffer.getvalue()


_DICT_MEMBER_TYPES = {
    "file": tarfile.REGTYPE, "dir": tarfile.DIRTYPE,
    "symlink": tarfile.SYMTYPE, "hardlink": tarfile.LNKTYPE,
    "device": tarfile.CHRTYPE, "fifo": tarfile.FIFOTYPE,
}


def _ftBuildMember(sName, sKind, xValue, iMode):
    infoMember = tarfile.TarInfo(sName)
    infoMember.type = _DICT_MEMBER_TYPES[sKind]
    infoMember.mode = iMode
    infoMember.mtime = 1_600_000_000
    if sKind in ("symlink", "hardlink"):
        infoMember.linkname = xValue
    if sKind == "file":
        infoMember.size = len(xValue)
        return infoMember, io.BytesIO(xValue)
    return infoMember, None


def _fresultLandTree(sDestination, listEntries, **dictKeywords):
    dictKeywords.setdefault("sAuthorizedRoot", None)
    return _fexecuteProgram(
        fsRenderConfinedTreeProgram(sDestination, **dictKeywords),
        _fbaBuildArchive(listEntries),
    )


def _fsReadFile(sPath):
    with open(sPath, "rb") as fileRead:
        return fileRead.read()


# ---------------------------------------------------------------------
# What lands, and how
# ---------------------------------------------------------------------


def testATreeLandsWithItsBytesModesAndTimesOwnedByTheCaller(tmp_path):
    sRoot = _fsRealPath(tmp_path)
    sDestination = os.path.join(sRoot, "dest")
    os.makedirs(sDestination)
    resultProc = _fresultLandTree(sDestination, [
        ("tree", "dir", None, 0o750),
        ("tree/run.sh", "file", b"#!/bin/sh\n", 0o755),
        ("tree/data.csv", "file", b"1,2,3\n", 0o640),
        ("tree/setuid", "file", b"x", 0o4755),
    ])
    assert resultProc.returncode == 0, resultProc.stderr
    sTree = os.path.join(sDestination, "tree")
    assert stat.S_IMODE(os.stat(sTree).st_mode) == 0o750
    assert _fsReadFile(os.path.join(sTree, "data.csv")) == b"1,2,3\n"
    infoScript = os.stat(os.path.join(sTree, "run.sh"))
    assert stat.S_IMODE(infoScript.st_mode) == 0o755
    assert infoScript.st_uid == os.getuid()
    assert int(infoScript.st_mtime) == 1_600_000_000
    assert stat.S_IMODE(os.stat(os.path.join(sTree, "setuid")).st_mode) == 0o755
    assert not [
        sName for sName in os.listdir(sTree)
        if sName.startswith(".vaibify-write-")
    ]


def testADirectoryModeNeverLocksTheCopyOutOfItsOwnTree(tmp_path):
    """A read-only directory mode still lets the members land in it."""
    sRoot = _fsRealPath(tmp_path)
    resultProc = _fresultLandTree(sRoot, [
        ("locked", "dir", None, 0o555),
        ("locked/inside.txt", "file", b"x", 0o644),
    ])
    assert resultProc.returncode == 0, resultProc.stderr
    assert stat.S_IMODE(os.stat(os.path.join(sRoot, "locked")).st_mode) == 0o755
    assert _fsReadFile(os.path.join(sRoot, "locked", "inside.txt")) == b"x"


def testSymlinkMembersLandAsSymlinksAndAreNeverFollowedByLaterMembers(
    tmp_path,
):
    sRoot = _fsRealPath(tmp_path / "project")
    sOutside = _fsRealPath(tmp_path / "outside")
    os.makedirs(sRoot)
    os.makedirs(sOutside)
    resultProc = _fresultLandTree(sRoot, [
        ("viaLink", "symlink", sOutside, 0o777),
        ("viaLink/planted.txt", "file", b"x", 0o644),
    ])
    assert resultProc.returncode == I_REFUSED_EXIT_CODE
    assert os.readlink(os.path.join(sRoot, "viaLink")) == sOutside
    assert os.listdir(sOutside) == []


def testAnEmptyNamedDirectoryMemberIsTheDestinationItself(tmp_path):
    """``tar`` of a path with a trailing slash names its root ``""``."""
    sRoot = _fsRealPath(tmp_path)
    resultProc = _fresultLandTree(sRoot, [
        ("", "dir", None, 0o755),
        ("child.txt", "file", b"c", 0o644),
    ])
    assert resultProc.returncode == 0, resultProc.stderr
    assert _fsReadFile(os.path.join(sRoot, "child.txt")) == b"c"


def testAReplacedFileLeavesNoTemporaryAndKeepsNoOldBytes(tmp_path):
    sRoot = _fsRealPath(tmp_path)
    with open(os.path.join(sRoot, "state.json"), "wb") as fileOld:
        fileOld.write(b"old content that is longer")
    resultProc = _fresultLandTree(
        sRoot, [("state.json", "file", b"new", 0o644)])
    assert resultProc.returncode == 0, resultProc.stderr
    assert os.listdir(sRoot) == ["state.json"]
    assert _fsReadFile(os.path.join(sRoot, "state.json")) == b"new"


def testALargeMemberStreamsIntact(tmp_path):
    sRoot = _fsRealPath(tmp_path)
    baPayload = os.urandom(1 << 20) * 9
    resultProc = _fresultLandTree(
        sRoot, [("big.bin", "file", baPayload, 0o644)])
    assert resultProc.returncode == 0, resultProc.stderr
    assert _fsReadFile(os.path.join(sRoot, "big.bin")) == baPayload


# ---------------------------------------------------------------------
# The destination
# ---------------------------------------------------------------------


@pytest.mark.falsification
def testAMissingDestinationIsRefusedUnlessCreationWasAsked(tmp_path):
    """The program creates destination components only when told to.

    Kills: creating missing destination components unconditionally,
    which turns a typo in a push destination into stray directories.
    """
    sRoot = _fsRealPath(tmp_path)
    sDestination = os.path.join(sRoot, "not", "yet")
    listEntries = [("a.txt", "file", b"a", 0o644)]
    resultProc = _fresultLandTree(sDestination, listEntries)
    assert resultProc.returncode == I_REFUSED_EXIT_CODE
    assert not os.path.exists(os.path.join(sRoot, "not"))
    resultProc = _fresultLandTree(
        sDestination, listEntries, bCreateDestination=True)
    assert resultProc.returncode == 0, resultProc.stderr
    assert _fsReadFile(os.path.join(sDestination, "a.txt")) == b"a"


def testCreationNeverMakesComponentsAboveTheAuthorizedRoot(tmp_path):
    sRoot = _fsRealPath(tmp_path)
    sAuthorized = os.path.join(sRoot, "gone")
    resultProc = _fresultLandTree(
        os.path.join(sAuthorized, "dest"),
        [("a.txt", "file", b"a", 0o644)],
        sAuthorizedRoot=sAuthorized, bCreateDestination=True,
    )
    assert resultProc.returncode == I_REFUSED_EXIT_CODE
    assert not os.path.exists(sAuthorized)


@pytest.mark.falsification
def testASymlinkedDestinationIsRefusedAndNothingLandsOutside(tmp_path):
    """The destination itself may not be a symlink.

    Kills: dropping ``O_NOFOLLOW`` from the directory walk, which makes
    the program follow a planted link exactly as the daemon's tar
    extraction did (trees landed in ``/usr/local/bin``).
    """
    sRoot = _fsRealPath(tmp_path / "project")
    sOutside = _fsRealPath(tmp_path / "outside")
    os.makedirs(sRoot)
    os.makedirs(sOutside)
    sLink = os.path.join(sRoot, "dest")
    os.symlink(sOutside, sLink)
    resultProc = _fresultLandTree(
        sLink, [("tree", "dir", None, 0o755),
                ("tree/a.txt", "file", b"a", 0o644)],
        sAuthorizedRoot=sRoot,
    )
    assert resultProc.returncode == I_REFUSED_EXIT_CODE
    assert b"symlink" in resultProc.stderr
    assert os.listdir(sOutside) == []


@pytest.mark.falsification
def testASymlinkedIntermediateDestinationComponentIsRefused(tmp_path):
    """A link in the middle of the destination is refused too.

    Kills: following links for every component but the last, which is
    the shape of the second measured redirect.
    """
    sRoot = _fsRealPath(tmp_path / "project")
    sOutside = _fsRealPath(tmp_path / "outside")
    os.makedirs(os.path.join(sOutside, "dest"))
    os.makedirs(sRoot)
    os.symlink(sOutside, os.path.join(sRoot, "hop"))
    resultProc = _fresultLandTree(
        os.path.join(sRoot, "hop", "dest"),
        [("a.txt", "file", b"a", 0o644)], sAuthorizedRoot=sRoot,
    )
    assert resultProc.returncode == I_REFUSED_EXIT_CODE
    assert os.listdir(os.path.join(sOutside, "dest")) == []


def testADestinationOutsideTheAuthorizedRootIsRefusedBeforeRunning(tmp_path):
    sRoot = _fsRealPath(tmp_path / "project")
    with pytest.raises(ValueError, match="is not below"):
        fsRenderConfinedTreeProgram(
            _fsRealPath(tmp_path / "elsewhere"), sAuthorizedRoot=sRoot)


# ---------------------------------------------------------------------
# What an archive may carry
# ---------------------------------------------------------------------


@pytest.mark.falsification
def testAMemberDirectoryThatIsASymlinkIsRefusedNotReplaced(tmp_path):
    """An existing link where a directory member lands is refused.

    Docker deleted such a link without a word and put a directory in its
    place. A researcher's own link (``data -> /mnt/data``) is theirs, so
    the copy stops and says which one.

    Kills: opening member directories without ``O_NOFOLLOW``, which
    walks through the link and writes into whatever it names.
    """
    sRoot = _fsRealPath(tmp_path / "project")
    sOutside = _fsRealPath(tmp_path / "outside")
    os.makedirs(sRoot)
    os.makedirs(sOutside)
    os.symlink(sOutside, os.path.join(sRoot, "data"))
    resultProc = _fresultLandTree(sRoot, [
        ("data", "dir", None, 0o755),
        ("data/a.txt", "file", b"a", 0o644),
    ])
    assert resultProc.returncode == I_REFUSED_EXIT_CODE
    assert b"'data' is a symlink" in resultProc.stderr
    assert os.path.islink(os.path.join(sRoot, "data"))
    assert os.listdir(sOutside) == []


@pytest.mark.falsification
def testAFileMemberReplacesASymlinkAtItsPathInsteadOfFollowingIt(tmp_path):
    """A link planted at a member's own name is replaced, not written through.

    Kills: writing the member through its final name instead of
    renaming a private temporary over it, which follows the link and
    overwrites the file it names.
    """
    sRoot = _fsRealPath(tmp_path / "project")
    sOutside = _fsRealPath(tmp_path / "outside")
    os.makedirs(sRoot)
    os.makedirs(sOutside)
    sVictim = os.path.join(sOutside, "victim.txt")
    with open(sVictim, "wb") as fileVictim:
        fileVictim.write(b"untouched")
    os.symlink(sVictim, os.path.join(sRoot, "report.txt"))
    resultProc = _fresultLandTree(
        sRoot, [("report.txt", "file", b"from the archive", 0o644)])
    assert resultProc.returncode == 0, resultProc.stderr
    assert _fsReadFile(sVictim) == b"untouched"
    sLanded = os.path.join(sRoot, "report.txt")
    assert not os.path.islink(sLanded)
    assert _fsReadFile(sLanded) == b"from the archive"


def testAFileMemberOverAnExistingDirectoryIsRefused(tmp_path):
    sRoot = _fsRealPath(tmp_path)
    os.makedirs(os.path.join(sRoot, "occupied"))
    resultProc = _fresultLandTree(
        sRoot, [("occupied", "file", b"x", 0o644)])
    assert resultProc.returncode == I_REFUSED_EXIT_CODE
    assert b"existing directory" in resultProc.stderr


@pytest.mark.parametrize("sName", [
    "/etc/profile.d/planted.sh", "../escaped.txt", "tree/../../escaped.txt",
    "a//b.txt", "./dotted.txt", "tree/./x",
])
def testAnAbsoluteOrTraversingMemberNameIsRefused(tmp_path, sName):
    sRoot = _fsRealPath(tmp_path / "project")
    os.makedirs(sRoot)
    resultProc = _fresultLandTree(
        sRoot, [(sName, "file", b"x", 0o644)])
    assert resultProc.returncode == I_REFUSED_EXIT_CODE, resultProc.stderr
    assert b"unsafe name" in resultProc.stderr
    assert os.listdir(str(tmp_path)) == ["project"]
    assert os.listdir(sRoot) == []


@pytest.mark.parametrize("sKind", ["hardlink", "device", "fifo"])
def testHardLinksDevicesAndFifosAreRefused(tmp_path, sKind):
    sRoot = _fsRealPath(tmp_path)
    resultProc = _fresultLandTree(
        sRoot, [("special", sKind, "/etc/passwd", 0o644)])
    assert resultProc.returncode == I_REFUSED_EXIT_CODE
    assert b"hard link or a special file" in resultProc.stderr
    assert os.listdir(sRoot) == []


def testForbiddenNamesAreRefusedWhereverTheyAppear(tmp_path):
    sRoot = _fsRealPath(tmp_path)
    resultProc = _fresultLandTree(
        sRoot, [("tree", "dir", None, 0o755),
                ("tree/.git", "dir", None, 0o755)],
        tForbiddenNames=confinedWrite.T_WRITE_DENYLISTED_NAMES,
    )
    assert resultProc.returncode == I_REFUSED_EXIT_CODE
    assert b"'.git'" in resultProc.stderr
    assert not os.path.exists(os.path.join(sRoot, "tree", ".git"))


def testGitAndVaibifyMetadataCrossWhenNoDenylistIsGiven(tmp_path):
    """A seed carries ``.git`` and ``.vaibify``; the default refuses nothing."""
    sRoot = _fsRealPath(tmp_path)
    resultProc = _fresultLandTree(sRoot, [
        (".git", "dir", None, 0o755),
        (".git/HEAD", "file", b"ref: refs/heads/main\n", 0o644),
        (".vaibify", "dir", None, 0o755),
    ])
    assert resultProc.returncode == 0, resultProc.stderr
    assert os.path.isdir(os.path.join(sRoot, ".vaibify"))


# ---------------------------------------------------------------------
# Failure reporting
# ---------------------------------------------------------------------


@pytest.mark.falsification
def testARefusalAfterSomeMembersSaysHowManyLanded(tmp_path):
    """A partial copy is reported as one, with the count of what landed.

    Kills: the program dropping its ``landed`` line, which makes a
    refusal after two members look like a clean refusal of everything.
    """
    sRoot = _fsRealPath(tmp_path)
    resultProc = _fresultLandTree(sRoot, [
        ("a.txt", "file", b"a", 0o644),
        ("b.txt", "file", b"b", 0o644),
        ("../c.txt", "file", b"c", 0o644),
    ])
    assert resultProc.returncode == I_REFUSED_EXIT_CODE
    tResult = type("Result", (), {
        "iExitCode": resultProc.returncode,
        "sStderr": resultProc.stderr.decode(),
    })
    with pytest.raises(ContainerWriteRefusedError) as infoError:
        fnRaiseWhenTreeWriteFailed(tResult, "/workspace/project")
    assert infoError.value.iMembersLanded == 2
    assert "2 entries had already landed" in str(infoError.value)
    assert _fsReadFile(os.path.join(sRoot, "b.txt")) == b"b"


def testARefusalThatLandedNothingSaysNothingWasWritten(tmp_path):
    sRoot = _fsRealPath(tmp_path)
    resultProc = _fresultLandTree(
        sRoot, [("../c.txt", "file", b"c", 0o644)])
    tResult = type("Result", (), {
        "iExitCode": resultProc.returncode,
        "sStderr": resultProc.stderr.decode(),
    })
    with pytest.raises(ContainerWriteRefusedError) as infoError:
        fnRaiseWhenTreeWriteFailed(tResult, "/workspace/project")
    assert infoError.value.iMembersLanded == 0
    assert "Nothing was written" in str(infoError.value)


def testATruncatedStreamFailsCleanlyAndLeavesNoTemporary(tmp_path):
    """A cancelled copy: the stream ends mid-member."""
    sRoot = _fsRealPath(tmp_path)
    baArchive = _fbaBuildArchive([
        ("first.txt", "file", b"complete", 0o644),
        ("second.bin", "file", os.urandom(1 << 20), 0o644),
    ])
    resultProc = _fexecuteProgram(
        fsRenderConfinedTreeProgram(sRoot), baArchive[:100000])
    assert resultProc.returncode == confinedWrite.I_FAILED_EXIT_CODE
    assert b"failed:" in resultProc.stderr
    assert sorted(os.listdir(sRoot)) == ["first.txt"]
    assert resultProc.stderr.strip().splitlines()[-1] == (
        b"vaibify-landed=1")


def testAnIoFailureIsReportedAsAFailureNotARefusal(tmp_path):
    sRoot = _fsRealPath(tmp_path)
    os.makedirs(os.path.join(sRoot, "locked"))
    os.chmod(os.path.join(sRoot, "locked"), 0o500)
    try:
        resultProc = _fresultLandTree(
            sRoot, [("locked/a.txt", "file", b"a", 0o644)])
    finally:
        os.chmod(os.path.join(sRoot, "locked"), 0o700)
    if os.getuid() == 0:
        pytest.skip("root ignores directory permissions")
    assert resultProc.returncode == confinedWrite.I_FAILED_EXIT_CODE
    tResult = type("Result", (), {
        "iExitCode": resultProc.returncode,
        "sStderr": resultProc.stderr.decode(),
    })
    with pytest.raises(OSError) as infoError:
        fnRaiseWhenTreeWriteFailed(tResult, "/workspace/project")
    assert not isinstance(infoError.value, ContainerWriteRefusedError)
    assert infoError.value.iMembersLanded == 0


def testTheProgramIsAnInertStringForAnyPath():
    """Only str and bool values reach the program, as repr literals."""
    sProgram = fsRenderConfinedTreeProgram(
        "/home/researcher/it's \"quoted\"; $(touch /tmp/x)",
        bCreateDestination=True)
    compile(sProgram, "tree", "exec")
    with pytest.raises(TypeError):
        fsRenderConfinedTreeProgram("/home/x", bCreateDestination="yes")
    with pytest.raises(TypeError):
        fsRenderConfinedTreeProgram("/home/x", tForbiddenNames=(1,))
    with pytest.raises(ValueError):
        fsRenderConfinedTreeProgram("relative/path")
    with pytest.raises(ValueError):
        fsRenderConfinedTreeProgram("/home/x\nimport os")


# ---------------------------------------------------------------------
# Through the connection, over a daemon that really runs the program
# ---------------------------------------------------------------------


def _fconnectionOverDaemon(daemon):
    mockDocker = MagicMock()
    mockClient = MagicMock()
    mockDocker.from_env.return_value = mockClient
    mockContainer = MagicMock()
    mockContainer.id = S_CONTAINER_ID
    mockContainer.image.attrs = {"Config": {"User": "researcher"}}
    mockClient.containers.get.return_value = mockContainer
    mockClient.api = daemon
    with patch.object(
        dockerConnectionModule, "_fmoduleGetDocker",
        return_value=mockDocker,
    ):
        return DockerConnection(), mockContainer


def _fsPopulateHostTree(pathRoot):
    pathTree = pathRoot / "inputData"
    (pathTree / "nested").mkdir(parents=True)
    (pathTree / "dataFile.csv").write_bytes(b"1,2,3\n")
    (pathTree / "nested" / "notes.txt").write_bytes(b"notes")
    os.symlink("/etc/passwd", str(pathTree / "outsideLink"))
    return str(pathTree)


@pytest.mark.falsification
def testTheTreeWriteRunsTheConfinedProgramAndNeverPutsAnArchive(tmp_path):
    """The copy lands through the receiver program, not ``put_archive``.

    Kills: restoring ``put_archive`` for trees, which extracts as root,
    follows planted symlinks and changes the owner of a directory it
    reaches through one.
    """
    sHostTree = _fsPopulateHostTree(tmp_path)
    sRoot = _fsRealPath(tmp_path / "container")
    os.makedirs(sRoot)
    daemon = ExecProgramDaemon()
    connection, mockContainer = _fconnectionOverDaemon(daemon)
    connection.fnWriteTreeViaTar(S_CONTAINER_ID, sRoot, [sHostTree])
    mockContainer.put_archive.assert_not_called()
    assert len(daemon.listExecCreateKeywords) == 1
    assert daemon.listExecCreateKeywords[0]["user"] == "researcher"
    assert _fsReadFile(
        os.path.join(sRoot, "inputData", "dataFile.csv")) == b"1,2,3\n"
    assert _fsReadFile(
        os.path.join(sRoot, "inputData", "nested", "notes.txt")) == b"notes"
    assert os.readlink(
        os.path.join(sRoot, "inputData", "outsideLink")) == "/etc/passwd"


def testTheArchiveOnTheWireClaimsNoOwnership(tmp_path):
    sHostTree = _fsPopulateHostTree(tmp_path)
    sRoot = _fsRealPath(tmp_path / "container")
    os.makedirs(sRoot)
    daemon = ExecProgramDaemon(bExecute=False)
    connection, _ = _fconnectionOverDaemon(daemon)
    connection.fnWriteTreeViaTar(
        S_CONTAINER_ID, sRoot, [sHostTree], iUid=2001, iGid=2002)
    with tarfile.open(
        fileobj=io.BytesIO(daemon.listReceivedStdin[0])) as archive:
        listMembers = archive.getmembers()
    assert {infoMember.name for infoMember in listMembers} == {
        "inputData", "inputData/dataFile.csv", "inputData/nested",
        "inputData/nested/notes.txt", "inputData/outsideLink",
    }
    for infoMember in listMembers:
        assert (infoMember.uid, infoMember.gid) == (0, 0)
        assert (infoMember.uname, infoMember.gname) == ("", "")


def testTheTreeWriteStreamsALargeArchiveFromItsSpoolFile(tmp_path):
    sHostTree = tmp_path / "bulk"
    sHostTree.mkdir()
    baPayload = os.urandom(1 << 20) * 40
    (sHostTree / "bulk.bin").write_bytes(baPayload)
    sRoot = _fsRealPath(tmp_path / "container")
    os.makedirs(sRoot)
    daemon = ExecProgramDaemon()
    connection, _ = _fconnectionOverDaemon(daemon)
    connection.fnWriteTreeViaTar(S_CONTAINER_ID, sRoot, [str(sHostTree)])
    assert _fsReadFile(os.path.join(sRoot, "bulk", "bulk.bin")) == baPayload


def testACopyRefusedBeforeAnythingLandedRaisesARefusalWithCountZero(tmp_path):
    sHostTree = _fsPopulateHostTree(tmp_path)
    sOutside = _fsRealPath(tmp_path / "outside")
    sRoot = _fsRealPath(tmp_path / "container")
    os.makedirs(sOutside)
    os.makedirs(sRoot)
    os.symlink(sOutside, os.path.join(sRoot, "inputData"))
    daemon = ExecProgramDaemon()
    connection, _ = _fconnectionOverDaemon(daemon)
    with pytest.raises(ContainerWriteRefusedError) as infoError:
        connection.fnWriteTreeViaTar(S_CONTAINER_ID, sRoot, [sHostTree])
    assert infoError.value.iMembersLanded == 0
    assert os.listdir(sOutside) == []


def testTheTreeWriteInAnEnforcedLaneIsRefusedBeforeTheDaemon(tmp_path):
    sHostTree = _fsPopulateHostTree(tmp_path)
    daemon = ExecProgramDaemon()
    connection, _ = _fconnectionOverDaemon(daemon)
    tokenLane = mutationAdmission.ftokenMarkEnforcedLane()
    try:
        with pytest.raises(mutationAdmission.MutationNotAdmittedError):
            connection.fnWriteTreeViaTar(
                S_CONTAINER_ID, "/workspace", [sHostTree])
    finally:
        mutationAdmission.fnResetEnforcedLane(tokenLane)
    assert daemon.listExecCreateKeywords == []


def testAnArchiveNameForSeveralPathsIsRefused(tmp_path):
    sHostTree = _fsPopulateHostTree(tmp_path)
    daemon = ExecProgramDaemon()
    connection, _ = _fconnectionOverDaemon(daemon)
    with pytest.raises(ValueError, match="exactly one host path"):
        connection.fnWriteTreeViaTar(
            S_CONTAINER_ID, "/workspace", [sHostTree, sHostTree],
            sArchiveName="renamed")
    assert daemon.listExecCreateKeywords == []


def testADirectoryCopiedToANewNameLandsUnderThatName(tmp_path):
    sHostTree = _fsPopulateHostTree(tmp_path)
    sRoot = _fsRealPath(tmp_path / "container")
    os.makedirs(sRoot)
    daemon = ExecProgramDaemon()
    connection, _ = _fconnectionOverDaemon(daemon)
    connection.fnWriteTreeViaTar(
        S_CONTAINER_ID, sRoot, [sHostTree], sArchiveName="renamedData")
    assert _fsReadFile(
        os.path.join(sRoot, "renamedData", "dataFile.csv")) == b"1,2,3\n"
    assert not os.path.exists(os.path.join(sRoot, "inputData"))

