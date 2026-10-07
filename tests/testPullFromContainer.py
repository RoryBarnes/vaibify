"""``vaibify pull`` reads through the confined programs and lands safely.

Source: ``vaibify/docker/fileTransfer.py`` and
``vaibify/host/archiveExtraction.py``.

The gateway here runs the REAL confined read programs in subprocesses over
a real directory tree (container paths are ordinary absolute paths on the
test machine), so a pull is exercised end to end: the program that walks
and streams, the generator that raises on a refusal, the file or archive
that lands on the host, and what is left behind when something goes wrong.
The hostile-archive tests build tars by hand, because the archive comes
from a program running inside a container whose interpreter the agent
there may have shadowed.
"""

import io
import os
import stat
import subprocess
import sys
import tarfile

import pytest

from tests.confinedReadScenarios import (
    BA_BIG,
    fdictBuildProject,
    fsRealPath,
)
from vaibify.docker import confinedRead, fileTransfer
from vaibify.docker.confinedRead import ContainerReadRefusedError
from vaibify.docker.dockerConnection import DockerConnection
from vaibify.host.archiveExtraction import (
    ArchiveExtractionError,
    fiExtractTarStream,
)


class GatewayOverRealPrograms:
    """The part of the gateway a pull uses, running the real programs."""

    def __init__(self):
        self.listRequests = []

    def fbContainerPathIsDirectory(self, sProject, sPath):
        return os.path.isdir(sPath)

    def fiterReadFileConfined(self, sProject, sPath, sAuthorizedRoot=None):
        self.listRequests.append(("file", sPath, sAuthorizedRoot))
        sProgram = DockerConnection._fsRenderReadProgramOrRefuse(
            confinedRead.fsRenderConfinedReadProgram, sPath, sAuthorizedRoot)
        yield from self._fiterRun(sProgram, sPath)

    def fiterReadDirectoryAsTar(self, sProject, sPath, sAuthorizedRoot=None):
        self.listRequests.append(("folder", sPath, sAuthorizedRoot))
        sProgram = DockerConnection._fsRenderReadProgramOrRefuse(
            confinedRead.fsRenderConfinedArchiveProgram, sPath,
            sAuthorizedRoot)
        yield from self._fiterRun(sProgram, sPath)

    @staticmethod
    def _fiterRun(sProgram, sPath):
        processRun = subprocess.Popen(
            [sys.executable, "-c", sProgram],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            for baChunk in iter(lambda: processRun.stdout.read(65536), b""):
                yield baChunk
            sStderr = processRun.stderr.read().decode()
            processRun.wait()
        finally:
            processRun.stdout.close()
            processRun.stderr.close()
        confinedRead.fnRaiseWhenReadFailed(
            processRun.returncode, sStderr, sPath)


@pytest.fixture()
def dictProject(tmp_path):
    dictProject = fdictBuildProject(tmp_path / "container")
    dictProject["sHost"] = fsRealPath(tmp_path / "host")
    os.makedirs(dictProject["sHost"])
    return dictProject


def _fnPull(dictProject, sRelative, sDestination, gateway=None):
    fileTransfer.fnPullFromContainer(
        "proj", dictProject["sRoot"] + "/" + sRelative, sDestination,
        sAuthorizedRoot=dictProject["sRoot"],
        connectionDocker=gateway or GatewayOverRealPrograms())


def _fsRead(sPath):
    with open(sPath, "rb") as fileIn:
        return fileIn.read()


# ---------------------------------------------------------------------
# One file
# ---------------------------------------------------------------------


@pytest.mark.falsification
def testAFilePulledIntoAFolderLandsUnderItsOwnName(dictProject):
    """``docker cp``'s reading: a destination that is a folder receives it.

    Kills: treating a destination folder as the file to write.
    """
    _fnPull(dictProject, "plain.txt", dictProject["sHost"])
    assert _fsRead(dictProject["sHost"] + "/plain.txt") == b"hello"


def testAFilePulledToANewPathIsThatPath(dictProject):
    _fnPull(dictProject, "big.bin", dictProject["sHost"] + "/renamed.bin")
    assert _fsRead(dictProject["sHost"] + "/renamed.bin") == BA_BIG
    assert os.listdir(dictProject["sHost"]) == ["renamed.bin"]


def testAPulledFileTakesTheResearchersUmaskNotTheTemporaryFilesMode(
    dictProject,
):
    _fnPull(dictProject, "plain.txt", dictProject["sHost"] + "/out.txt")
    iUmask = os.umask(0)
    os.umask(iUmask)
    assert stat.S_IMODE(os.stat(
        dictProject["sHost"] + "/out.txt").st_mode) == 0o666 & ~iUmask


def testAMissingDestinationParentIsRefusedBeforeAnythingIsRead(dictProject):
    gateway = GatewayOverRealPrograms()
    with pytest.raises(OSError, match="does not exist"):
        _fnPull(dictProject, "plain.txt",
                dictProject["sHost"] + "/nope/out.txt", gateway)
    assert gateway.listRequests == []


@pytest.mark.falsification
def testALinkInsideTheProjectPullsTheTargetsBytesNotADanglingLink(
    dictProject,
):
    """Kills: pulling the link itself (``docker cp`` landed a dangling one)."""
    _fnPull(dictProject, "linkToPlain", dictProject["sHost"] + "/got.txt")
    assert not os.path.islink(dictProject["sHost"] + "/got.txt")
    assert _fsRead(dictProject["sHost"] + "/got.txt") == b"hello"


@pytest.mark.falsification
def testALinkLeadingOutOfTheProjectIsRefusedAndNothingLands(dictProject):
    """The project root is the authorized root of the read.

    Kills: dropping the authorized root from the read, which follows the
    link to any file the container user can read.
    """
    with pytest.raises(ContainerReadRefusedError, match="escapeLink"):
        _fnPull(dictProject, "escapeLink", dictProject["sHost"] + "/x.txt")
    assert os.listdir(dictProject["sHost"]) == []


@pytest.mark.falsification
def testAReadThatFailsMidStreamLeavesTheOldFileAndNoTemporary(dictProject):
    """A broken stream never replaces the destination with half a file.

    Kills: removing the temporary file when the stream fails.
    """
    sDestination = dictProject["sHost"] + "/keep.txt"
    with open(sDestination, "wb") as fileOld:
        fileOld.write(b"old")

    class _BreaksAfterOneChunk(GatewayOverRealPrograms):
        def fiterReadFileConfined(self, sProject, sPath, sAuthorizedRoot=None):
            yield b"partial"
            raise OSError("the container went away")

    with pytest.raises(OSError, match="went away"):
        _fnPull(dictProject, "big.bin", sDestination, _BreaksAfterOneChunk())
    assert _fsRead(sDestination) == b"old"
    assert os.listdir(dictProject["sHost"]) == ["keep.txt"]


# ---------------------------------------------------------------------
# A folder
# ---------------------------------------------------------------------


@pytest.mark.falsification
def testAFolderPulledToANewNameTakesThatName(dictProject):
    """Kills: ignoring the destination's name (landing under the source's)."""
    _fnPull(dictProject, "sub", dictProject["sHost"] + "/mine")
    assert _fsRead(dictProject["sHost"] + "/mine/nested/file.txt") == b"nested"
    assert not os.path.exists(dictProject["sHost"] + "/sub")


def testAFolderPulledIntoAFolderNestsUnderItsOwnName(dictProject):
    _fnPull(dictProject, "sub", dictProject["sHost"])
    assert _fsRead(dictProject["sHost"] + "/sub/nested/file.txt") == b"nested"


def testAWholeTreeKeepsItsEmptyDirectoriesAndItsLinksAsLinks(dictProject):
    _fnPull(dictProject, ".", dictProject["sHost"] + "/copy")
    sCopy = dictProject["sHost"] + "/copy"
    assert os.path.isdir(sCopy + "/emptydir")
    assert os.path.islink(sCopy + "/linkToPlain")
    assert os.readlink(sCopy + "/linkToPlain") == "plain.txt"
    assert _fsRead(sCopy + "/big.bin") == BA_BIG
    assert not os.path.exists(sCopy + "/fifo")


def testAFolderPullIntoAMissingParentIsRefused(dictProject):
    with pytest.raises(OSError, match="does not exist"):
        _fnPull(dictProject, "sub", dictProject["sHost"] + "/a/b")


# ---------------------------------------------------------------------
# The extractor, against archives nobody should trust
# ---------------------------------------------------------------------


def _fbaTar(listMembers):
    """Build a tar from ``(name, type, payload-or-linkname)`` triples."""
    bufferTar = io.BytesIO()
    with tarfile.open(fileobj=bufferTar, mode="w") as tarOut:
        for sName, sType, objPayload in listMembers:
            infoMember = tarfile.TarInfo(sName)
            infoMember.type = sType
            infoMember.mode = 0o644
            if sType == tarfile.REGTYPE:
                infoMember.size = len(objPayload)
                tarOut.addfile(infoMember, io.BytesIO(objPayload))
            else:
                infoMember.linkname = objPayload or ""
                tarOut.addfile(infoMember)
    return bufferTar.getvalue()


def _fnExtract(baTar, sDestination, sRename=None):
    return fiExtractTarStream(
        [baTar[i:i + 700] for i in range(0, len(baTar), 700)],
        sDestination, sRename)


@pytest.mark.falsification
@pytest.mark.parametrize("sName", ["/etc/evil", "top/../../evil", "..", "a//b"])
def testAMemberNamedOutsideItsFolderIsRefusedAndNothingLands(
    tmp_path, sName,
):
    """Kills: dropping the check on absolute and climbing member names."""
    sDestination = fsRealPath(tmp_path / "dest")
    os.makedirs(sDestination)
    baTar = _fbaTar([("top", tarfile.DIRTYPE, None),
                     (sName, tarfile.REGTYPE, b"x")])
    with pytest.raises(ArchiveExtractionError):
        _fnExtract(baTar, sDestination)
    assert not os.path.exists(str(tmp_path / "evil"))
    assert not os.path.exists("/etc/evil")


@pytest.mark.falsification
def testNothingIsWrittenThroughALinkTheArchiveItselfMade(tmp_path):
    """A hostile archive: a link out, then a file "inside" it.

    Kills: dropping the check that no directory on the way is a link.
    """
    sDestination = fsRealPath(tmp_path / "dest")
    sOutside = fsRealPath(tmp_path / "outside")
    os.makedirs(sDestination)
    os.makedirs(sOutside)
    baTar = _fbaTar([("top", tarfile.DIRTYPE, None),
                     ("top/door", tarfile.SYMTYPE, sOutside),
                     ("top/door/planted", tarfile.REGTYPE, b"x")])
    with pytest.raises(ArchiveExtractionError):
        _fnExtract(baTar, sDestination)
    assert os.listdir(sOutside) == []


def testNothingIsWrittenThroughALinkAlreadyInTheDestination(tmp_path):
    sDestination = fsRealPath(tmp_path / "dest")
    sOutside = fsRealPath(tmp_path / "outside")
    os.makedirs(sDestination)
    os.makedirs(sOutside)
    os.symlink(sOutside, sDestination + "/top")
    baTar = _fbaTar([("top", tarfile.DIRTYPE, None),
                     ("top/planted", tarfile.REGTYPE, b"x")])
    with pytest.raises(ArchiveExtractionError):
        _fnExtract(baTar, sDestination)
    assert os.listdir(sOutside) == []


@pytest.mark.falsification
def testHardLinksDevicesAndFifosAreSkippedAndCounted(tmp_path):
    """Kills: landing a member type the extractor does not accept."""
    sDestination = fsRealPath(tmp_path / "dest")
    os.makedirs(sDestination)
    baTar = _fbaTar([("top", tarfile.DIRTYPE, None),
                     ("top/hard", tarfile.LNKTYPE, "top/target"),
                     ("top/pipe", tarfile.FIFOTYPE, None),
                     ("top/ok.txt", tarfile.REGTYPE, b"ok")])
    assert _fnExtract(baTar, sDestination) == 2
    assert sorted(os.listdir(sDestination + "/top")) == ["ok.txt"]


@pytest.mark.falsification
def testSetIdBitsAreNeverCarriedOntoTheHost(tmp_path):
    """Kills: copying the member's whole mode (set-id bits included)."""
    sDestination = fsRealPath(tmp_path / "dest")
    os.makedirs(sDestination)
    bufferTar = io.BytesIO()
    with tarfile.open(fileobj=bufferTar, mode="w") as tarOut:
        infoMember = tarfile.TarInfo("top/tool")
        infoMember.size = 1
        infoMember.mode = 0o6755
        tarOut.addfile(infoMember, io.BytesIO(b"x"))
    _fnExtract(bufferTar.getvalue(), sDestination)
    iMode = stat.S_IMODE(os.stat(sDestination + "/top/tool").st_mode)
    assert iMode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX) == 0


def testAFileCannotReplaceAFolderOfTheSameName(tmp_path):
    sDestination = fsRealPath(tmp_path / "dest")
    os.makedirs(sDestination + "/top/thing")
    baTar = _fbaTar([("top", tarfile.DIRTYPE, None),
                     ("top/thing", tarfile.REGTYPE, b"x")])
    with pytest.raises(ArchiveExtractionError, match="is a folder"):
        _fnExtract(baTar, sDestination)
    assert os.path.isdir(sDestination + "/top/thing")


def testATruncatedArchiveIsAnErrorNotASilentPartialSuccess(tmp_path):
    sDestination = fsRealPath(tmp_path / "dest")
    os.makedirs(sDestination)
    baTar = _fbaTar([("top", tarfile.DIRTYPE, None),
                     ("top/big", tarfile.REGTYPE, b"y" * 5000)])
    with pytest.raises(ArchiveExtractionError):
        _fnExtract(baTar[:1200], sDestination)
