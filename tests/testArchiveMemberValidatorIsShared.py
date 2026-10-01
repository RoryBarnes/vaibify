"""One judgement of whether a tar member escapes the extraction root.

The disposable-container repack and the council snapshot repack each kept
their own copy of the check, and the copies shared a defect: both resolved
a HARD link's target against the member's own directory, but a tar
hard-link target is relative to the archive root, so a nested member
hard-linked to ``../outside`` was admitted. They now delegate to
``pathContainment.fsDescribeMemberEscape``; these tests pin the rule and
that both callers answer every case identically.
"""

import inspect
import tarfile

import pytest

from vaibify.docker import disposableSpecification, pathContainment
from vaibify.gui import agentCouncilRunner


def _finfoMember(sName, iType=tarfile.REGTYPE, sLink=""):
    infoMember = tarfile.TarInfo(name=sName)
    infoMember.type = iType
    infoMember.linkname = sLink
    return infoMember


# (name, type, linkname, escapes)
LIST_CASES = [
    ("repo/data.csv", tarfile.REGTYPE, "", False),
    ("..data/x", tarfile.REGTYPE, "", False),
    ("../x", tarfile.REGTYPE, "", True),
    ("/abs", tarfile.REGTYPE, "", True),
    # A symbolic link is relative to the directory it sits in.
    ("repo/sub/link", tarfile.SYMTYPE, "../data.csv", False),
    ("repo/sub/link", tarfile.SYMTYPE, "../../data.csv", False),
    ("repo/sub/link", tarfile.SYMTYPE, "../../../data.csv", True),
    ("repo/link", tarfile.SYMTYPE, "/etc/shadow", True),
    ("link", tarfile.SYMTYPE, "../outside", True),
    # A hard link is relative to the ARCHIVE ROOT, whatever the depth.
    ("repo/sub/link", tarfile.LNKTYPE, "repo/data.csv", False),
    ("repo/link", tarfile.LNKTYPE, "../outside", True),
    ("repo/sub/deeper/link", tarfile.LNKTYPE, "../outside", True),
    ("repo/link", tarfile.LNKTYPE, "/etc/shadow", True),
    ("link", tarfile.LNKTYPE, "../outside", True),
]


@pytest.mark.falsification
def testTheSharedJudgementReadsEachLinkKindTheWayTheArchiveDoes():
    """Kills: resolving a hard link's target from the member's directory."""
    for sName, iType, sLink, bEscapes in LIST_CASES:
        sVerdict = pathContainment.fsDescribeMemberEscape(
            _finfoMember(sName, iType, sLink))
        assert bool(sVerdict) is bEscapes, (sName, sLink, sVerdict)


@pytest.mark.parametrize("sName,iType,sLink,bEscapes", LIST_CASES)
def testBothRepackersAnswerEveryCaseIdentically(
        sName, iType, sLink, bEscapes):
    listVerdicts = []
    for fnValidate in (
        disposableSpecification._fnValidateArchiveMember,
        agentCouncilRunner._fnValidateSnapshotMember,
    ):
        try:
            fnValidate(_finfoMember(sName, iType, sLink))
            listVerdicts.append(False)
        except ValueError as error:
            assert "extraction root" in str(error)
            listVerdicts.append(True)
    assert listVerdicts == [bEscapes, bEscapes]


def testEachRepackerKeepsItsOwnRefusalPrefix():
    infoEscape = _finfoMember("../x")
    with pytest.raises(ValueError, match="^Archive refused: "):
        disposableSpecification._fnValidateArchiveMember(infoEscape)
    with pytest.raises(ValueError, match="^Snapshot tarball refused: "):
        agentCouncilRunner._fnValidateSnapshotMember(infoEscape)


def testNeitherRepackerKeepsItsOwnCopyOfTheRule():
    for fnValidate in (
        disposableSpecification._fnValidateArchiveMember,
        agentCouncilRunner._fnValidateSnapshotMember,
    ):
        sSource = inspect.getsource(fnValidate)
        assert "fsDescribeMemberEscape" in sSource
        assert "startswith" not in sSource and "normpath" not in sSource
