"""A converted project's links cross into its container faithfully, or are named.

Source: ``vaibify/gui/seedLinks.py``, ``vaibify/gui/routes/fileRoutes.py``
(``_flistResolveSeedPaths``) and ``vaibify/docker/dockerConnection.py``
(the tree tar).

After one researcher converted a repository that tracked ``AGENTS.md``,
``CLAUDE.md`` and ``GEMINI.md`` as links to ``.vaibify/AGENTS.md``, the
container's git showed all three deleted: the Files to Copy listing
skipped every link, and the entrypoint's link step ran before the copy
landed. A Push from the Repos panel stages tracked deletions, so it would
have deleted them from the author's repository. The expectations below
follow from the contract (a link crosses only when its target is
relative and stays inside the project, read on the destination layout)
and from the entrypoint's preserving rule, not from the code under test.

The link program is run for real, in a subprocess against a real
directory, as the container runs it.
"""

import io
import os
import subprocess
import sys
import tarfile

import pytest

from vaibify.docker.dockerConnection import DockerConnection, ExecResult
from vaibify.gui import seedLinks
from vaibify.gui.registryRoutes import flistQueryHostDirectory
from vaibify.gui.routes import fileRoutes


BA_SHARED_CONTEXT = b"shared context\n"


@pytest.fixture
def sProject(tmp_path):
    """A project with in-project, absolute and escaping links, top-level and nested."""
    sRoot = os.path.realpath(str(tmp_path / "project"))
    os.makedirs(os.path.join(sRoot, ".vaibify"))
    os.makedirs(os.path.join(sRoot, "docs"))
    with open(os.path.join(sRoot, ".vaibify", "AGENTS.md"), "wb") as fileOut:
        fileOut.write(BA_SHARED_CONTEXT)
    with open(os.path.join(sRoot, "docs", "guide.md"), "w") as fileOut:
        fileOut.write("guide\n")
    with open(str(tmp_path / "outside.md"), "w") as fileOut:
        fileOut.write("not the project's\n")
    for sName in ("AGENTS.md", "CLAUDE.md"):
        os.symlink(".vaibify/AGENTS.md", os.path.join(sRoot, sName))
    os.symlink("guide.md", os.path.join(sRoot, "docs", "intro.md"))
    os.symlink(os.path.join(sRoot, ".vaibify", "AGENTS.md"),
               os.path.join(sRoot, "absolute.md"))
    os.symlink("../outside.md", os.path.join(sRoot, "escape.md"))
    os.symlink("../../outside.md", os.path.join(sRoot, "docs", "up.md"))
    return sRoot


def _ftArchive(sProject, listSelected):
    """Return ``({name: (is link, target, bytes)}, skipped)`` for one seed archive."""
    listSkipped = []
    listHostPaths = fileRoutes._flistResolveSeedPaths(sProject, listSelected)
    fileTar = DockerConnection._ffileBuildTreeTar(
        listHostPaths, None, seedLinks.ffnBuildSeedLinkFilter(listSkipped))
    dictMembers = {}
    with tarfile.open(fileobj=fileTar) as fileArchive:
        for infoMember in fileArchive.getmembers():
            fileMember = (fileArchive.extractfile(infoMember)
                          if infoMember.isfile() else None)
            dictMembers[infoMember.name] = (
                infoMember.issym(), infoMember.linkname,
                fileMember.read() if fileMember else None)
    return dictMembers, listSkipped


_LIST_EVERY_ENTRY = ["AGENTS.md", "CLAUDE.md", "docs", "absolute.md",
                     "escape.md", ".vaibify"]


@pytest.mark.falsification
def testARelativeInProjectLinkCrossesAtTheTopAndNested(sProject):
    """Kills: refusing every link, which strands the tracked agent-doc links."""
    dictMembers, _listSkipped = _ftArchive(sProject, _LIST_EVERY_ENTRY)
    assert dictMembers["AGENTS.md"][:2] == (True, ".vaibify/AGENTS.md")
    assert dictMembers["docs/intro.md"][:2] == (True, "guide.md")


@pytest.mark.falsification
def testAbsoluteAndEscapingLinksStayBehindAndAreNamed(sProject):
    """An absolute target inside the project still stays behind: it names a host path.

    Kills: the filter keeping every link, which carried an absolute host
    path, and links out of the project, into the container verbatim.
    """
    dictMembers, listSkipped = _ftArchive(sProject, _LIST_EVERY_ENTRY)
    assert not {"absolute.md", "escape.md", "docs/up.md"} & set(dictMembers)
    assert sorted(listSkipped, key=lambda dictLink: dictLink["sPath"]) == [
        {"sPath": "absolute.md", "sReason": "absolute target"},
        {"sPath": "docs/up.md", "sReason": "points outside the project"},
        {"sPath": "escape.md", "sReason": "points outside the project"},
    ]


@pytest.mark.falsification
def testATopLevelLinkIsArchivedAsItselfNotAsItsTarget(sProject):
    """Kills: resolving the selected path, which landed the target's bytes under its name."""
    dictMembers, _listSkipped = _ftArchive(sProject, ["CLAUDE.md"])
    assert list(dictMembers) == ["CLAUDE.md"]
    assert dictMembers["CLAUDE.md"] == (True, ".vaibify/AGENTS.md", None)


@pytest.mark.parametrize("sMember, sTarget, sExpected", [
    ("AGENTS.md", ".vaibify/AGENTS.md", ""),
    ("docs/intro.md", "../README.md", ""),
    ("docs/up.md", "../../x", seedLinks.S_LINK_OUTSIDE_PROJECT),
    ("escape.md", "..", seedLinks.S_LINK_OUTSIDE_PROJECT),
    ("absolute.md", "/workspace/project/AGENTS.md", seedLinks.S_LINK_ABSOLUTE_TARGET),
])
def testTheRuleReadsTheDestinationLayout(sMember, sTarget, sExpected):
    assert seedLinks.fsSeedLinkRefusal(sMember, sTarget) == sExpected


def testTheListingOffersAKeptLinkAndNamesTheOthers(sProject):
    dictEntries = {dictEntry["sName"]: dictEntry for dictEntry in
                   flistQueryHostDirectory(sProject, bIncludeFiles=True,
                                           bDescribeLinks=True)}
    assert dictEntries["CLAUDE.md"]["bIsLink"] is True
    assert dictEntries["CLAUDE.md"]["sNotCopied"] == ""
    assert dictEntries["absolute.md"]["sNotCopied"] == "absolute target"
    assert dictEntries["escape.md"]["sNotCopied"] == "points outside the project"
    listWithoutLinks = flistQueryHostDirectory(sProject, bIncludeFiles=True)
    assert not [dictEntry for dictEntry in listWithoutLinks
                if dictEntry["sName"].endswith(".md")]


# ---------------------------------------------------------------------------
# The agent-doc links, made after the seed lands
# ---------------------------------------------------------------------------


class _RunTheProgramHere:
    """Run the link program in a local subprocess, as the container would."""

    def ftRunProgramWithStdin(self, sContainerId, listCommand, baStdin):
        del sContainerId
        assert listCommand[:2] == ["python3", "-I"]
        processDone = subprocess.run(
            [sys.executable] + listCommand[1:], input=baStdin,
            capture_output=True, check=False)
        return ExecResult(
            iExitCode=processDone.returncode,
            sStdout=processDone.stdout.decode("utf-8"),
            sStderr=processDone.stderr.decode("utf-8"))


@pytest.fixture
def sSeeded(tmp_path):
    """A seeded project root with the shared context and none of the links."""
    sRoot = str(tmp_path / "seeded")
    os.makedirs(os.path.join(sRoot, ".vaibify"))
    with open(os.path.join(sRoot, ".vaibify", "AGENTS.md"), "wb") as fileOut:
        fileOut.write(BA_SHARED_CONTEXT)
    return sRoot


def _flistLinkAgentDocs(sRoot):
    return seedLinks.flistCreateMissingAgentDocLinks(
        _RunTheProgramHere(), "cid", sRoot)


@pytest.mark.falsification
def testAResearchersOwnInstructionFileIsLeftByteIdentical(sSeeded):
    """Kills: removing what is at the name before linking, as the adoption helper does."""
    sPath = os.path.join(sSeeded, "CLAUDE.md")
    with open(sPath, "wb") as fileOut:
        fileOut.write(b"my own instructions\n")
    assert _flistLinkAgentDocs(sSeeded) == ["AGENTS.md", "GEMINI.md"]
    assert not os.path.islink(sPath)
    with open(sPath, "rb") as fileIn:
        assert fileIn.read() == b"my own instructions\n"


@pytest.mark.falsification
def testAResearchersOwnLinkIsLeftPointingWhereItPointed(sSeeded):
    """Kills: the same removal, which repointed a link the researcher made."""
    sPath = os.path.join(sSeeded, "AGENTS.md")
    os.symlink("notes/agents.md", sPath)
    assert "AGENTS.md" not in _flistLinkAgentDocs(sSeeded)
    assert os.readlink(sPath) == "notes/agents.md"


@pytest.mark.falsification
def testAnAbsentNameGetsTheLinkANewContainerWouldHave(sSeeded):
    """Kills: the program never creating a link."""
    assert _flistLinkAgentDocs(sSeeded) == ["CLAUDE.md", "AGENTS.md", "GEMINI.md"]
    for sName in seedLinks.T_AGENT_DOC_NAMES:
        sPath = os.path.join(sSeeded, sName)
        assert os.readlink(sPath) == ".vaibify/AGENTS.md"
        with open(sPath, "rb") as fileIn:
            assert fileIn.read() == BA_SHARED_CONTEXT


@pytest.mark.falsification
def testNothingIsLinkedWithoutTheSharedContext(sSeeded):
    """Kills: linking whether or not .vaibify/AGENTS.md exists."""
    os.remove(os.path.join(sSeeded, ".vaibify", "AGENTS.md"))
    assert _flistLinkAgentDocs(sSeeded) == []
    assert not any(os.path.lexists(os.path.join(sSeeded, sName))
                   for sName in seedLinks.T_AGENT_DOC_NAMES)


def testABrokenLinkIsNeverReplaced(sSeeded):
    sPath = os.path.join(sSeeded, "GEMINI.md")
    os.symlink("gone.md", sPath)
    assert "GEMINI.md" not in _flistLinkAgentDocs(sSeeded)
    assert os.readlink(sPath) == "gone.md"


def testAFailedProgramIsRaisedWithItsOwnWords(tmp_path):
    with pytest.raises(RuntimeError, match="No such file or directory"):
        _flistLinkAgentDocs(str(tmp_path / "missing"))


def testTheRealTreeTarStillClearsOwnershipBehindTheFilter(sProject):
    fileTar = DockerConnection._ffileBuildTreeTar(
        [os.path.join(sProject, "docs")], None,
        seedLinks.ffnBuildSeedLinkFilter([]))
    with tarfile.open(fileobj=io.BytesIO(fileTar.read())) as fileArchive:
        assert all((infoMember.uid, infoMember.uname) == (0, "")
                   for infoMember in fileArchive.getmembers())
