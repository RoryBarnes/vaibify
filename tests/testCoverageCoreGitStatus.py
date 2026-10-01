"""gitStatus against real git repositories built in tmp_path.

git is a local, hermetic subprocess here: every repository is created
inside tmp_path with an explicit identity, and nothing touches a
remote. The porcelain parser is additionally driven with hand-written
lines for the entry kinds (renames, conflicts) that are awkward to
produce on disk.
"""

import shutil
import subprocess

import pytest

from vaibify.gui import gitStatus


pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git executable not installed",
)


def fnRunGit(pathRepository, listArgs):
    """Run git in pathRepository with a fixed identity; raise on failure."""
    subprocess.run(
        ["git", "-c", "user.name=Tester", "-c", "user.email=t@example.com",
         "-c", "commit.gpgsign=false"] + listArgs,
        cwd=str(pathRepository), check=True, capture_output=True,
    )


@pytest.fixture
def pathRepository(tmp_path):
    """A repository with one committed file on branch mainline."""
    pathRoot = tmp_path / "repositoryAlpha"
    pathRoot.mkdir()
    fnRunGit(pathRoot, ["init", "-q", "-b", "mainline"])
    (pathRoot / "tracked.txt").write_text("first\n")
    fnRunGit(pathRoot, ["add", "tracked.txt"])
    fnRunGit(pathRoot, ["commit", "-q", "-m", "initial"])
    return pathRoot


def testReadOriginUrlIsEmptyWithoutRemoteAndForMissingDirectory(
    pathRepository, tmp_path,
):
    """No remote and no directory both read as an empty origin."""
    assert gitStatus.fsReadOriginUrl(str(pathRepository)) == ""
    assert gitStatus.fsReadOriginUrl(str(tmp_path / "absent")) == ""
    assert gitStatus.fsReadOriginUrl("") == ""


def testAddOriginUrlAddsThenRepointsAnExistingRemote(pathRepository):
    """A second call falls back to set-url instead of failing."""
    sFirst = "https://example.com/first.git"
    sSecond = "https://example.com/second.git"
    assert gitStatus.fbAddOriginUrl(str(pathRepository), sFirst) is True
    assert gitStatus.fsReadOriginUrl(str(pathRepository)) == sFirst
    assert gitStatus.fbAddOriginUrl(str(pathRepository), sSecond) is True
    assert gitStatus.fsReadOriginUrl(str(pathRepository)) == sSecond


def testAddOriginUrlRefusesOptionLookingUrlWithoutRunningGit(pathRepository):
    """An option-shaped URL is refused and leaves no remote behind."""
    assert gitStatus.fbAddOriginUrl(
        str(pathRepository), "--upload-pack=touch",
    ) is False
    assert gitStatus.fsReadOriginUrl(str(pathRepository)) == ""


def testAddOriginUrlReportsFailureOutsideARepository(tmp_path):
    """Both add and set-url fail in a plain directory; the result is False."""
    pathPlain = tmp_path / "plainDirectory"
    pathPlain.mkdir()
    assert gitStatus.fbAddOriginUrl(
        str(pathPlain), "https://example.com/x.git",
    ) is False


@pytest.mark.parametrize(
    "sUrl", ["", "   ", "-x", "https://a\nb", "https://a\rb", "https://a\0b"],
)
def testUrlSafetyRefusesEmptyOptionAndControlCharacters(sUrl):
    """Each unsafe candidate is refused."""
    assert gitStatus.fbUrlIsSafeGitArgument(sUrl) is False


def testUrlSafetyAcceptsOrdinaryRemote():
    """An ordinary scp-style remote passes."""
    assert gitStatus.fbUrlIsSafeGitArgument("git@example.com:group/x.git")


def testRunGitInMissingDirectoryReturnsSyntheticFailure(tmp_path):
    """A missing cwd becomes returncode 127 rather than an exception."""
    processResult = gitStatus.fsRunGit(["status"], str(tmp_path / "absent"))
    assert processResult.returncode == 127
    assert processResult.stdout == ""
    assert processResult.stderr


def testStatusReportsBranchHeadAndFileStates(pathRepository):
    """Dirty, staged, untracked and committed files map to their states."""
    (pathRepository / "tracked.txt").write_text("changed\n")
    (pathRepository / "staged.txt").write_text("new\n")
    fnRunGit(pathRepository, ["add", "staged.txt"])
    (pathRepository / "loose.txt").write_text("loose\n")
    dictStatus = gitStatus.fdictGitStatusForWorkspace(str(pathRepository))
    assert dictStatus["bIsRepo"] is True
    assert dictStatus["sBranch"] == "mainline"
    assert len(dictStatus["sHeadSha"]) == 40
    assert dictStatus["dictFileStates"] == {
        "tracked.txt": "dirty",
        "staged.txt": "uncommitted",
        "loose.txt": "untracked",
    }
    assert dictStatus["sReason"] == ""


def testStatusOfEmptyRepositoryHasNoHeadSha(tmp_path):
    """A repository with no commit reports an empty HEAD, not an error."""
    pathEmpty = tmp_path / "emptyRepository"
    pathEmpty.mkdir()
    fnRunGit(pathEmpty, ["init", "-q"])
    dictStatus = gitStatus.fdictGitStatusForWorkspace(str(pathEmpty))
    assert dictStatus["bIsRepo"] is True
    assert dictStatus["sHeadSha"] == ""


def testStatusOfPlainDirectoryIsNotARepository(tmp_path):
    """A directory outside any work tree says so in sReason."""
    dictStatus = gitStatus.fdictGitStatusForWorkspace(str(tmp_path / "absent"))
    assert dictStatus["bIsRepo"] is False
    assert dictStatus["sReason"] == "Not a git repository"


def testStatusCommandFailureIsReportedWithStderr(pathRepository, monkeypatch):
    """A failing `git status` yields an empty status carrying stderr."""
    fnRealRunGit = gitStatus.fsRunGit

    def fprocessRunGit(listArgs, sCwd):
        if listArgs and listArgs[0] == "status":
            return subprocess.CompletedProcess(
                listArgs, 128, stdout="", stderr="index locked\n")
        return fnRealRunGit(listArgs, sCwd)

    monkeypatch.setattr(gitStatus, "fsRunGit", fprocessRunGit)
    dictStatus = gitStatus.fdictGitStatusForWorkspace(str(pathRepository))
    assert dictStatus["bIsRepo"] is False
    assert dictStatus["sReason"] == "git status failed: index locked"


def testPorcelainParserHandlesEveryEntryKind():
    """Headers, renames, conflicts, ignored and malformed lines."""
    sOutput = "\n".join([
        "# branch.oid abc",
        "# branch.head (detached)",
        "# branch.ab +2 -3",
        "# short",
        "1 .M N... 100644 100644 100644 aaa bbb modified.txt",
        "1 M. N... 100644 100644 100644 aaa bbb",
        "1 .M too few fields",
        "2 R. N... 100644 100644 100644 aaa bbb R100 renamed.txt\told.txt",
        "2 R. N... 100644 100644 100644 aaa bbb R100 \told.txt",
        "2 R. too few",
        "u UU N... 100644 100644 100644 100644 aaa bbb ccc conflicted.txt",
        "! ignored.log",
        "? ",
        "X unknown entry kind",
    ])
    dictParsed = gitStatus._fdictParsePorcelain(sOutput)
    assert dictParsed["sBranch"] == ""
    assert dictParsed["iAhead"] == 2
    assert dictParsed["iBehind"] == 3
    assert dictParsed["dictFileStates"] == {
        "modified.txt": "dirty",
        "renamed.txt": "uncommitted",
        "conflicted.txt": "conflict",
        "ignored.log": "ignored",
    }


def testPorcelainParserIgnoresNonNumericAheadBehind():
    """A garbled ahead/behind header leaves the counts at zero."""
    dictParsed = gitStatus._fdictParsePorcelain("# branch.ab +x -y\n")
    assert (dictParsed["iAhead"], dictParsed["iBehind"]) == (0, 0)


def testStateFromShortXyCodeIsUncommitted():
    """A truncated XY code is read conservatively as uncommitted."""
    assert gitStatus._fsStateFromXy("M") == "uncommitted"
    assert gitStatus._fsStateFromXy("..") == "committed"
