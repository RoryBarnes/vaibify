"""The host leg of the confined reads, and the connection methods over it.

Source: ``vaibify/host/hostConfinedRead.py`` and
``HostConnection.fiterReadFileConfined`` / ``fiterReadDirectoryAsTar``.

The behaviour both legs share is pinned in
``tests/testConfinedReadParity.py``; what is here is what only the host
leg has: a path guard that runs first, a project root that may be spelled
through a symlink, and an in-process walk whose race can be provoked
deterministically.
"""

import io
import logging
import os
import tarfile

import pytest

from tests.confinedReadScenarios import (
    HostLeg,
    fdictBuildProject,
    fnWriteFile,
    fsRealPath,
)
from tests.testHostConnection import (  # noqa: F401  (fixtures)
    S_PROJECT_NAME,
    fixtureIsolateJournalAndScratch,
)
from vaibify.docker.confinedRead import ContainerReadRefusedError
from vaibify.host import hostConfinedRead
from vaibify.host.hostConnection import (
    HostConnection,
    HostPathOutsideProjectError,
)


@pytest.fixture()
def dictProject(tmp_path):
    return fdictBuildProject(tmp_path)


def _fconnectionOver(sProjectRoot):
    return HostConnection(fnResolveProjectRoot=lambda sResourceId: sProjectRoot)


@pytest.mark.falsification
def testALinkLeadingOutOfTheRootIsRefusedAndNoOutsideByteIsSent(dictProject):
    """Kills: making the root-containment test always pass, which follows a
    link to any file the researcher can read.
    """
    sOutcome, sMessage = HostLeg.ftReadFile(
        dictProject["sRoot"], dictProject["sRoot"] + "/escapeLink")
    assert sOutcome == "refused", sMessage
    assert "escapeLink" in sMessage and "secret.txt" in sMessage


@pytest.mark.falsification
def testASymlinkedDirectoryInThePathIsRefusedAndNothingIsSent(dictProject):
    """Kills: dropping ``O_NOFOLLOW`` from the directory walk."""
    sOutcome, objAnswer = HostLeg.ftReadFile(
        dictProject["sRoot"],
        dictProject["sRoot"] + "/linkedDir/nested/file.txt")
    assert sOutcome == "refused", objAnswer


@pytest.mark.falsification
def testAComponentSwappedAfterTheWalkCannotRedirectTheRead(
    tmp_path, monkeypatch,
):
    """The race closes by construction: descriptors, not names.

    The directory ``inner`` is moved away and replaced by a symlink to an
    outside directory after the walk holds a descriptor for it and just
    before the file is opened. A read that opened the file by NAME would
    follow the new link out of the project.

    Kills: opening the final component by its full path name instead of
    relative to the descriptor already held.
    """
    sRoot = fsRealPath(tmp_path) + "/project"
    sOutside = fsRealPath(tmp_path) + "/outside"
    fnWriteFile(sRoot + "/inner/data.txt", b"inside")
    fnWriteFile(sOutside + "/data.txt", b"outside")
    fnRealOpenOnce = hostConfinedRead._fiOpenOnce
    dictState = {"bSwapped": False}

    def fiSwapThenOpen(iParent, sName, iFlags):
        if not dictState["bSwapped"]:
            dictState["bSwapped"] = True
            os.rename(sRoot + "/inner", sRoot + "/moved")
            os.symlink(sOutside, sRoot + "/inner")
        return fnRealOpenOnce(iParent, sName, iFlags)

    monkeypatch.setattr(hostConfinedRead, "_fiOpenOnce", fiSwapThenOpen)
    baRead = b"".join(hostConfinedRead.fiterStreamFileInsideRoot(
        sRoot, sRoot + "/inner/data.txt"))
    assert dictState["bSwapped"]
    assert baRead == b"inside"


@pytest.mark.falsification
def testAnArchiveStoresALinkAsALinkAndNeverFollowsIt(dictProject):
    """Kills: ``follow_symlinks=True`` in the walk, which reads a link as
    whatever it points at.
    """
    sOutcome, baArchive = HostLeg.ftReadArchive(
        dictProject["sRoot"], dictProject["sRoot"])
    assert sOutcome == "ok", baArchive
    with tarfile.open(fileobj=io.BytesIO(baArchive)) as tarIn:
        dictMembers = {infoMember.name: infoMember for infoMember in tarIn}
    assert dictMembers["project/outsideDirLink"].issym()
    assert not [sName for sName in dictMembers
                if sName.startswith("project/outsideDirLink/")]


@pytest.mark.falsification
def testASpecialFileInATreeIsSkippedAndTheCountIsLogged(dictProject, caplog):
    """Kills: not counting what the archive leaves out."""
    with caplog.at_level(logging.WARNING, logger="vaibify"):
        sOutcome, _ = HostLeg.ftReadArchive(
            dictProject["sRoot"], dictProject["sRoot"])
    assert sOutcome == "ok"
    assert any("without 1 special" in sRecord for sRecord in caplog.messages)


def testAnArchiveIsRecordAlignedAndEndsWithTheTarTerminator(dictProject):
    """The hand-framed stream is a well-formed tar: blocks, then padding."""
    sOutcome, baArchive = HostLeg.ftReadArchive(
        dictProject["sRoot"], dictProject["sRoot"] + "/sub")
    assert sOutcome == "ok"
    assert len(baArchive) % 10240 == 0
    assert baArchive.rstrip(b"\0") != b""
    assert baArchive[-1024:] == bytes(1024)


# ---------------------------------------------------------------------
# Through the connection
# ---------------------------------------------------------------------


def testAFileLinkInsideTheProjectIsReadThroughTheConnection(dictProject):
    connection = _fconnectionOver(dictProject["sRoot"])
    baRead = b"".join(connection.fiterReadFileConfined(
        S_PROJECT_NAME, dictProject["sRoot"] + "/linkToPlain"))
    assert baRead == b"hello"


def testARelativePathResolvesAgainstTheProjectRoot(dictProject):
    connection = _fconnectionOver(dictProject["sRoot"])
    assert b"".join(connection.fiterReadFileConfined(
        S_PROJECT_NAME, "sub/nested/file.txt")) == b"nested"


def testALinkOutOfTheProjectIsRefusedByThePathGuardNamingIt(dictProject):
    connection = _fconnectionOver(dictProject["sRoot"])
    with pytest.raises(HostPathOutsideProjectError, match="escapeLink"):
        next(connection.fiterReadFileConfined(
            S_PROJECT_NAME, dictProject["sRoot"] + "/escapeLink"))


@pytest.mark.falsification
def testAProjectRootSpelledThroughASymlinkStillReadsItsFiles(tmp_path):
    """A registered root may be a symlink (macOS ``/tmp`` is one).

    The path is spelled through the alias; the walk starts from the real
    root, so the path has to be re-spelled under it.

    Kills: comparing the named path to the real root only, which refuses
    every file of a project whose root is spelled through a link.
    """
    sReal = fsRealPath(tmp_path) + "/real"
    sAlias = fsRealPath(tmp_path) + "/alias"
    fnWriteFile(sReal + "/data.txt", b"through the alias")
    os.symlink(sReal, sAlias)
    connection = _fconnectionOver(sAlias)
    assert b"".join(connection.fiterReadFileConfined(
        S_PROJECT_NAME, sAlias + "/data.txt")) == b"through the alias"
    assert b"".join(connection.fiterReadFileConfined(
        S_PROJECT_NAME, "data.txt")) == b"through the alias"


def testANarrowerAuthorizedRootConfinesTheReadFurther(dictProject):
    connection = _fconnectionOver(dictProject["sRoot"])
    sNarrow = dictProject["sRoot"] + "/sub"
    assert b"".join(connection.fiterReadFileConfined(
        S_PROJECT_NAME, sNarrow + "/nested/file.txt",
        sAuthorizedRoot=sNarrow)) == b"nested"
    with pytest.raises((ContainerReadRefusedError, HostPathOutsideProjectError)):
        next(connection.fiterReadFileConfined(
            S_PROJECT_NAME, dictProject["sRoot"] + "/plain.txt",
            sAuthorizedRoot=sNarrow))


def testADirectoryArchiveThroughTheConnectionHoldsTheTree(dictProject):
    connection = _fconnectionOver(dictProject["sRoot"])
    baArchive = b"".join(connection.fiterReadDirectoryAsTar(
        S_PROJECT_NAME, dictProject["sRoot"] + "/sub"))
    with tarfile.open(fileobj=io.BytesIO(baArchive)) as tarIn:
        assert "sub/nested/file.txt" in tarIn.getnames()
