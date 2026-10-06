"""The two legs of the confined reads answer every scenario the same way.

Source: ``vaibify/docker/confinedRead.py`` (the container programs) and
``vaibify/host/hostConfinedRead.py`` (the host leg).

The container leg is text run elsewhere and the host leg is a Python
module; nothing but these tests holds them to one contract. Each case
runs through BOTH legs over one real tree, so a rule changed in one leg
and forgotten in the other is a failing test here.
"""

import io
import os
import stat
import tarfile

import pytest

from tests.confinedReadScenarios import (
    BA_BIG,
    LIST_LEGS,
    fdictBuildProject,
)


@pytest.fixture()
def dictProject(tmp_path):
    return fdictBuildProject(tmp_path)


def _fnAssertOk(leg, dictProject, sRelative, baExpected):
    sOutcome, objAnswer = leg.ftReadFile(
        dictProject["sRoot"], dictProject["sRoot"] + "/" + sRelative)
    assert (sOutcome, objAnswer) == ("ok", baExpected), (leg.sName, sRelative)


def _fnAssertRefused(leg, dictProject, sPath, *tNamedInMessage):
    sOutcome, sMessage = leg.ftReadFile(dictProject["sRoot"], sPath)
    assert sOutcome == "refused", (leg.sName, sPath, sOutcome, sMessage)
    for sNamed in tNamedInMessage:
        assert sNamed in sMessage, (leg.sName, sNamed, sMessage)


DICT_EXPECTED_BYTES = {
    "plain.txt": b"hello",
    "big.bin": BA_BIG,
    "empty.txt": b"",
    "linkToPlain": b"hello",
    "chain1": b"hello",
    "absoluteLink": b"hello",
}


@pytest.mark.parametrize("sRelative", sorted(DICT_EXPECTED_BYTES))
def testAFileOrAnInRootLinkStreamsItsBytesOnBothLegs(dictProject, sRelative):
    for leg in LIST_LEGS:
        _fnAssertOk(leg, dictProject, sRelative, DICT_EXPECTED_BYTES[sRelative])


def testAPathThroughALinkedDirectoryIsRefusedOnBothLegs(dictProject):
    for leg in LIST_LEGS:
        _fnAssertRefused(
            leg, dictProject,
            dictProject["sRoot"] + "/linkedDir/nested/file.txt", "linkedDir")


def testALinkLeadingOutOfTheRootIsRefusedNamingTheLinkAndItsTarget(
    dictProject,
):
    for leg in LIST_LEGS:
        _fnAssertRefused(
            leg, dictProject, dictProject["sRoot"] + "/escapeLink",
            "escapeLink", "secret.txt", "outside")
        _fnAssertRefused(
            leg, dictProject, dictProject["sRoot"] + "/dotdotEscape",
            "dotdotEscape", "../outside/secret.txt")


def testALinkWhoseTargetPassesThroughALinkedDirectoryIsRefused(dictProject):
    for leg in LIST_LEGS:
        _fnAssertRefused(
            leg, dictProject, dictProject["sRoot"] + "/linkThroughLinkedDir",
            "linkedDir")


def testTooManyLinksInARowAreRefused(dictProject):
    for leg in LIST_LEGS:
        _fnAssertRefused(
            leg, dictProject, dictProject["sRoot"] + "/longchain0",
            "links in a row")
        _fnAssertOk(leg, dictProject, "longchain9", b"hello")


def testAPathOutsideTheRootIsRefusedAndAMissingOneIsMissing(dictProject):
    for leg in LIST_LEGS:
        _fnAssertRefused(
            leg, dictProject, dictProject["sOutside"] + "/secret.txt")
        sOutcome, _ = leg.ftReadFile(
            dictProject["sRoot"], dictProject["sRoot"] + "/nothing.txt")
        assert sOutcome == "missing", leg.sName
        sOutcome, _ = leg.ftReadFile(
            dictProject["sRoot"], dictProject["sRoot"] + "/no/such/dir.txt")
        assert sOutcome == "missing", leg.sName


@pytest.mark.parametrize("sRelative", ["emptydir", "sub", "fifo"])
def testAnythingButARegularFileIsRefusedAndAFifoNeverBlocks(
    dictProject, sRelative,
):
    for leg in LIST_LEGS:
        _fnAssertRefused(
            leg, dictProject, dictProject["sRoot"] + "/" + sRelative,
            "not a regular file")


# ---------------------------------------------------------------------
# Directory archives
# ---------------------------------------------------------------------


def _fdictMembers(baArchive):
    with tarfile.open(fileobj=io.BytesIO(baArchive), mode="r:") as tarIn:
        return {infoMember.name: infoMember for infoMember in tarIn}


def _fbaMemberBytes(baArchive, sName):
    with tarfile.open(fileobj=io.BytesIO(baArchive), mode="r:") as tarIn:
        return tarIn.extractfile(sName).read()


def testAnArchiveRoundTripsNestedDirectoriesEmptyOnesAndLinks(dictProject):
    for leg in LIST_LEGS:
        sOutcome, baArchive = leg.ftReadArchive(
            dictProject["sRoot"], dictProject["sRoot"])
        assert sOutcome == "ok", (leg.sName, baArchive)
        dictMembers = _fdictMembers(baArchive)
        assert dictMembers["project/emptydir"].isdir(), leg.sName
        assert dictMembers["project/sub/nested"].isdir(), leg.sName
        assert _fbaMemberBytes(
            baArchive, "project/sub/nested/file.txt") == b"nested"
        assert _fbaMemberBytes(baArchive, "project/big.bin") == BA_BIG
        assert _fbaMemberBytes(baArchive, "project/empty.txt") == b""
        infoLink = dictMembers["project/linkToPlain"]
        assert infoLink.issym() and infoLink.linkname == "plain.txt", leg.sName
        infoEscape = dictMembers["project/escapeLink"]
        assert infoEscape.issym(), "a link out of the tree is stored as a link"
        assert "project/fifo" not in dictMembers, leg.sName


def testAnArchiveNeverHoldsAMemberBeneathALink(dictProject):
    """No link's name is a prefix of another member: nothing writes through it."""
    for leg in LIST_LEGS:
        sOutcome, baArchive = leg.ftReadArchive(
            dictProject["sRoot"], dictProject["sRoot"])
        assert sOutcome == "ok"
        dictMembers = _fdictMembers(baArchive)
        listLinks = [sName for sName, infoMember in dictMembers.items()
                     if infoMember.issym()]
        assert listLinks, "the fixture must contain links for this to mean anything"
        for sLink in listLinks:
            assert not [sName for sName in dictMembers
                        if sName.startswith(sLink + "/")], (leg.sName, sLink)


def testAnArchiveMemberClaimsNoOwner(dictProject):
    for leg in LIST_LEGS:
        _, baArchive = leg.ftReadArchive(
            dictProject["sRoot"], dictProject["sRoot"] + "/sub")
        for infoMember in _fdictMembers(baArchive).values():
            assert (infoMember.uid, infoMember.gid) == (0, 0), leg.sName
            assert (infoMember.uname, infoMember.gname) == ("", ""), leg.sName


def testAnArchivePreservesModeAndModificationTime(dictProject):
    sFile = dictProject["sRoot"] + "/sub/nested/file.txt"
    os.chmod(sFile, 0o640)
    os.utime(sFile, (1_700_000_000, 1_700_000_000))
    for leg in LIST_LEGS:
        _, baArchive = leg.ftReadArchive(
            dictProject["sRoot"], dictProject["sRoot"] + "/sub")
        infoFile = _fdictMembers(baArchive)["sub/nested/file.txt"]
        assert stat.S_IMODE(infoFile.mode) == 0o640, leg.sName
        assert infoFile.mtime == 1_700_000_000, leg.sName


def testAnArchiveOfALinkedDirectoryInsideTheRootIsFollowedOnceAtTheTop(
    dictProject,
):
    for leg in LIST_LEGS:
        sOutcome, baArchive = leg.ftReadArchive(
            dictProject["sRoot"], dictProject["sRoot"] + "/linkedDir")
        assert sOutcome == "ok", (leg.sName, baArchive)
        assert _fbaMemberBytes(
            baArchive, "linkedDir/nested/file.txt") == b"nested"


def testAnArchiveOfALinkOutOfTheRootOrAFileOrAMissingPathIsNotProduced(
    dictProject,
):
    for leg in LIST_LEGS:
        sOutcome, sMessage = leg.ftReadArchive(
            dictProject["sRoot"], dictProject["sRoot"] + "/outsideDirLink")
        assert sOutcome == "refused", (leg.sName, sMessage)
        assert "outsideDirLink" in sMessage
        sOutcome, _ = leg.ftReadArchive(
            dictProject["sRoot"], dictProject["sRoot"] + "/plain.txt")
        assert sOutcome == "refused", leg.sName
        sOutcome, _ = leg.ftReadArchive(
            dictProject["sRoot"], dictProject["sRoot"] + "/nothing")
        assert sOutcome == "missing", leg.sName
