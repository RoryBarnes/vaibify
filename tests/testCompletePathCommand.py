"""``vaibify complete-path``: the helper the completion scripts ask.

Every TAB in ``vaibify push`` / ``vaibify pull`` lands here, so the
contract is narrow and each clause of it has a test: candidates come
back in the form the researcher typed, a directory ends in ``/``, a name
that could move the terminal is never offered, the project is resolved
the way push and pull resolve it (``-p`` honoured), and EVERY failure --
a stopped container, an unknown project, an ambiguous one -- is silence
and exit 0, because a completion has no channel to explain itself and a
message here lands in the middle of the researcher's command line.

The project, the registry and the config file are REAL (a registry in a
scratch home, a ``vaibify.yml`` per project, read through
``fconfigResolveProject``). Only the Docker daemon is replaced, by an
object that answers the two typed reads the helper uses from a directory
tree and raises as a stopped container would. Container names, project
names and workspace roots are all distinct strings, so a lookup keyed by
the wrong one cannot pass.
"""

import os
import subprocess
import sys

import pytest
from click.testing import CliRunner

from vaibify.cli.commandCompletePath import fnCompletePathCommand
from vaibify.config import registryManager


S_ROOT_ALPHA = "/srv/alphaRoot"
S_ROOT_BETA = "/home/researcher/betaRoot"


class FakeContainerDaemon:
    """Answers the two typed reads from a tree; raises like a stopped one.

    ``dictTrees`` maps a container name to ``{directory: {name: bIsDir}}``.
    A container name absent from it is a stopped (or missing) container,
    for which every call raises, as a real daemon does.
    """

    def __init__(self, dictTrees):
        self.dictTrees = dictTrees
        self.listListedPaths = []

    def _fdictDirectories(self, sContainerId):
        if sContainerId not in self.dictTrees:
            raise RuntimeError(f"container {sContainerId} is not running")
        return self.dictTrees[sContainerId]

    def flistDirectoryEntries(self, sContainerId, sDirectoryPath):
        dictDirectories = self._fdictDirectories(sContainerId)
        self.listListedPaths.append(sDirectoryPath)
        sKey = sDirectoryPath.rstrip("/") or "/"
        if sKey not in dictDirectories:
            raise FileNotFoundError(sDirectoryPath)
        return sorted(dictDirectories[sKey])

    def flistContainerDirectoriesExist(self, sContainerId, listPaths):
        dictDirectories = self._fdictDirectories(sContainerId)
        listAnswers = []
        for sPath in listPaths:
            sParent, sName = sPath.rstrip("/").rsplit("/", 1)
            dictParent = dictDirectories.get(sParent or "/", {})
            listAnswers.append(bool(dictParent.get(sName, False)))
        return listAnswers


def _fnWriteProject(sDirectory, sName, sWorkspaceRoot=None):
    """Create a project directory holding a ``vaibify.yml``."""
    os.makedirs(sDirectory, exist_ok=True)
    sConfig = f"projectName: {sName}\n"
    if sWorkspaceRoot:
        sConfig += f"workspaceRoot: {sWorkspaceRoot}\n"
    with open(os.path.join(sDirectory, "vaibify.yml"), "w") as fileConfig:
        fileConfig.write(sConfig)


@pytest.fixture
def fixtureProjects(tmp_path, monkeypatch):
    """Register two container projects and one host project, no daemon."""
    sHome = str(tmp_path / "home")
    os.makedirs(sHome)
    for sAttribute, sValue in (
        ("_S_REGISTRY_DIRECTORY", sHome),
        ("_S_REGISTRY_PATH", os.path.join(sHome, "registry.json")),
        ("_S_LOCK_PATH", os.path.join(sHome, "registry.lock")),
    ):
        monkeypatch.setattr(registryManager, sAttribute, sValue)
    dictDirectories = {}
    for sName, sRoot, sMode in (
        ("alphaProject", S_ROOT_ALPHA, "container"),
        ("betaProject", S_ROOT_BETA, "container"),
        ("hostProject", None, "host"),
    ):
        sDirectory = str(tmp_path / "projects" / sName)
        _fnWriteProject(sDirectory, sName, sRoot)
        registryManager.fnAddProject(sDirectory, sMode=sMode)
        dictDirectories[sName] = sDirectory
    return dictDirectories


@pytest.fixture
def fixtureDaemon(monkeypatch):
    """Replace Docker with a tree-backed fake and return it."""
    daemonFake = FakeContainerDaemon({
        "alphaProject": {
            S_ROOT_ALPHA: {
                "Step01": True, "Step02": True, "notes.md": False,
                ".git": True, "Step01.md": False,
            },
            S_ROOT_ALPHA + "/Step01": {
                "output.csv": False, "plots": True, ".hidden": False,
                "other.txt": False,
            },
            "/etc": {"hostname": False},
        },
        "betaProject": {
            S_ROOT_BETA: {"onlyInBeta": True},
        },
    })
    monkeypatch.setattr(
        "vaibify.docker.dockerConnection.DockerConnection",
        lambda: daemonFake,
    )
    return daemonFake


def _flistComplete(listArguments):
    """Run the command; return (stdout lines, exit code)."""
    resultRun = CliRunner().invoke(fnCompletePathCommand, listArguments)
    return resultRun.output.splitlines(), resultRun.exit_code


def _flistCompleteContainer(sPartial, sProject="alphaProject"):
    """Complete a container-side partial for a project."""
    return _flistComplete(
        ["--side", "container", "-p", sProject, sPartial],
    )


def testAnEmptyPartialListsTheProjectsOwnWorkspaceRoot(
    fixtureProjects, fixtureDaemon,
):
    """The root is the PROJECT's, not a constant: `/workspace` is a default."""
    listLines, iExit = _flistCompleteContainer("")
    assert iExit == 0
    assert listLines == ["Step01/", "Step01.md", "Step02/", "notes.md"]
    assert fixtureDaemon.listListedPaths == [S_ROOT_ALPHA]


def testAPrefixNarrowsTheListAndDirectoriesEndInASlash(
    fixtureProjects, fixtureDaemon,
):
    listLines, _iExit = _flistCompleteContainer("Step0")
    assert listLines == ["Step01/", "Step01.md", "Step02/"]


def testANestedRelativePartialKeepsTheDirectoryAsTyped(
    fixtureProjects, fixtureDaemon,
):
    """Candidates come back in the form typed, so they replace the word."""
    listLines, _iExit = _flistCompleteContainer("Step01/o")
    assert listLines == ["Step01/other.txt", "Step01/output.csv"]
    assert fixtureDaemon.listListedPaths == [S_ROOT_ALPHA + "/Step01/"]


def testAnAbsolutePartialStaysAbsoluteAndIsNotRebased(
    fixtureProjects, fixtureDaemon,
):
    listLines, _iExit = _flistCompleteContainer(S_ROOT_ALPHA + "/Step01/pl")
    assert listLines == [S_ROOT_ALPHA + "/Step01/plots/"]
    listOutside, _iExit = _flistCompleteContainer("/etc/")
    assert listOutside == ["/etc/hostname"]
    assert fixtureDaemon.listListedPaths[-1] == "/etc/"


def testHiddenNamesAreOfferedOnlyToAPrefixThatStartsWithADot(
    fixtureProjects, fixtureDaemon,
):
    listPlain, _iExit = _flistCompleteContainer("")
    listDotted, _iExit = _flistCompleteContainer(".")
    listNested, _iExit = _flistCompleteContainer("Step01/.")
    assert ".git/" not in listPlain
    assert listDotted == [".git/"]
    assert listNested == ["Step01/.hidden"]


def testTheProjectNamedWithDashPIsTheOneThatIsListed(
    fixtureProjects, fixtureDaemon,
):
    """``-p`` is honoured: the other project's tree and root are used."""
    listBeta, _iExit = _flistCompleteContainer("", "betaProject")
    assert listBeta == ["onlyInBeta/"]
    assert fixtureDaemon.listListedPaths == [S_ROOT_BETA]


@pytest.mark.falsification
def testNamesThatCouldMoveTheTerminalAreNeverOffered(
    fixtureProjects, fixtureDaemon,
):
    """A name from inside a container may not split a line or send a code.

    An agent controls these names. A newline would split one candidate
    into two (and the second could be anything); an escape sequence would
    be interpreted by the terminal that draws the completion menu; a tab
    is the separator some shells use between a candidate and its
    description; a byte string that is not text would make the print
    itself fail.

    Kills: removing the control-character filter, so a hostile name
    reaches the shell's candidate list.
    """
    fixtureDaemon.dictTrees["alphaProject"][S_ROOT_ALPHA].update({
        "bad\nname": False, "esc\x1b[2Jname": False, "tab\tname": False,
        "c1\x9bname": False, "nul\x7fname": False,
        "not\udcfftext": False, "fine.csv": False,
    })
    listLines, iExit = _flistCompleteContainer("")
    assert iExit == 0
    assert listLines == [
        "Step01/", "Step01.md", "Step02/", "fine.csv", "notes.md",
    ]


@pytest.mark.parametrize("sName", ["bad\nname", "esc\x1b[2Jname", "x\ty"])
def testAHostileNameIsAbsentEvenWhenItIsTheOnlyMatch(
    fixtureProjects, fixtureDaemon, sName,
):
    fixtureDaemon.dictTrees["alphaProject"][S_ROOT_ALPHA] = {sName: False}
    listLines, iExit = _flistCompleteContainer("")
    assert (listLines, iExit) == ([], 0)


def testAStoppedContainerIsSilenceNotAnError(
    fixtureProjects, fixtureDaemon,
):
    """No daemon answer is no suggestions; the exit status stays 0."""
    del fixtureDaemon.dictTrees["alphaProject"]
    assert _flistCompleteContainer("") == ([], 0)


@pytest.mark.falsification
def testAnUnknownProjectIsSilentAndSoIsAnAmbiguousOne(
    fixtureProjects, fixtureDaemon,
):
    """``fconfigResolveProject`` explains itself on stdout; completion may not.

    Unnamed with three projects registered, or named with a name that is
    registered nowhere, the resolver prints a list of projects and exits
    1. That text would be pasted into the researcher's command line by
    ``$(...)`` in the completion script.

    Kills: letting the resolver's message through.
    """
    assert _flistCompleteContainer("", "noSuchProject") == ([], 0)
    resultAmbiguous = CliRunner().invoke(
        fnCompletePathCommand, ["--side", "container", ""],
    )
    assert (resultAmbiguous.output, resultAmbiguous.exit_code) == ("", 0)


@pytest.mark.falsification
def testADaemonThatNeverAnswersCannotFreezeThePrompt(
    fixtureProjects, fixtureDaemon, monkeypatch,
):
    """A TAB is abandoned after a few seconds, not after ten minutes.

    The Docker client waits ten minutes for an answer, because a long
    push needs it to; a daemon that accepts a connection and goes quiet
    would hold the researcher's prompt for that long. The helper bounds
    its own wait and answers with silence.

    Kills: removing the time limit around the listing.
    """
    import time

    from vaibify.cli import commandCompletePath
    monkeypatch.setattr(
        commandCompletePath, "I_COMPLETION_TIME_LIMIT_SECONDS", 1,
    )
    fnListDirectory = fixtureDaemon.flistDirectoryEntries

    def fnListSlowly(sContainerId, sDirectoryPath):
        time.sleep(30)
        return fnListDirectory(sContainerId, sDirectoryPath)

    fixtureDaemon.flistDirectoryEntries = fnListSlowly
    fStarted = time.time()
    assert _flistCompleteContainer("") == ([], 0)
    assert time.time() - fStarted < 10


def testTheTimeLimitLeavesNoAlarmAndRestoresTheHandler(
    fixtureProjects, fixtureDaemon,
):
    """Nothing the helper armed outlives it, in-process callers included."""
    import signal
    handlerBefore = signal.getsignal(signal.SIGALRM)
    _flistCompleteContainer("")
    assert signal.alarm(0) == 0
    assert signal.getsignal(signal.SIGALRM) is handlerBefore


def testAnUnreadableDirectoryIsSilence(fixtureProjects, fixtureDaemon):
    assert _flistCompleteContainer("NoSuchDirectory/") == ([], 0)


def testAHostProjectListsItsOwnDirectoryOnBothSides(
    fixtureProjects, fixtureDaemon, tmp_path,
):
    """A host project has no container: its files ARE on this machine.

    ``vaibify push`` and ``pull`` resolve both paths against the project
    directory for it, so both sides complete there -- and Docker is never
    asked (the daemon fake has no tree for it, so asking would raise).
    """
    sProject = fixtureProjects["hostProject"]
    os.makedirs(os.path.join(sProject, "Step01"))
    with open(os.path.join(sProject, "Step01", "results.json"), "w"):
        pass
    for sSide in ("container", "host"):
        listLines, iExit = _flistComplete(
            ["--side", sSide, "-p", "hostProject", "Step01/r"],
        )
        assert (listLines, iExit) == (["Step01/results.json"], 0)
    assert fixtureDaemon.listListedPaths == []
    listRoot, _iExit = _flistComplete(
        ["--side", "container", "-p", "hostProject", ""],
    )
    assert listRoot == ["Step01/", "vaibify.yml"]


def testTheHostSideOfAContainerProjectIsTheCurrentDirectory(
    fixtureProjects, fixtureDaemon, tmp_path, monkeypatch,
):
    """The researcher's side is read from where the shell is, as push is."""
    sWorkDirectory = tmp_path / "somewhere"
    (sWorkDirectory / "data").mkdir(parents=True)
    (sWorkDirectory / "data" / "local.csv").write_text("a\n")
    (sWorkDirectory / "dataset.txt").write_text("a\n")
    monkeypatch.chdir(sWorkDirectory)
    listLines, iExit = _flistComplete(
        ["--side", "host", "-p", "alphaProject", "data"],
    )
    assert (listLines, iExit) == (["data/", "dataset.txt"], 0)
    listNested, _iExit = _flistComplete(
        ["--side", "host", "-p", "alphaProject", "data/"],
    )
    assert listNested == ["data/local.csv"]
    assert fixtureDaemon.listListedPaths == []


def testAHomeDirectoryPartialIsExpandedToReadAndKeptAsTyped(
    fixtureProjects, fixtureDaemon, tmp_path, monkeypatch,
):
    sHome = tmp_path / "researcherHome"
    sHome.mkdir()
    (sHome / "report.pdf").write_text("x")
    monkeypatch.setenv("HOME", str(sHome))
    listLines, _iExit = _flistComplete(
        ["--side", "host", "-p", "alphaProject", "~/rep"],
    )
    assert listLines == ["~/report.pdf"]


def testTheHelperIsHiddenFromTheCommandListing():
    """It is machinery for the completion scripts, not a command."""
    from vaibify.cli.main import main
    resultHelp = CliRunner().invoke(main, ["--help"])
    assert "complete-path" not in resultHelp.output
    assert main.commands["complete-path"].hidden is True


@pytest.mark.falsification
def testTheHelperIsServedWithoutLoadingEveryOtherCommand(tmp_path):
    """A TAB may not pay to import the whole command set.

    Run in a fresh interpreter, because only a fresh interpreter can say
    which modules an import pulled in. The process must also not run
    first-time setup, which appends to the researcher's shell
    configuration -- its home is a scratch directory so that a failure of
    this test cannot do that either.

    Kills: dropping the early dispatch in ``main.py``, which makes every
    TAB load the build, reproduce and server modules before answering.
    """
    sProgram = (
        "import sys\n"
        "sys.argv = ['vaibify', 'complete-path', '--side', 'host', '']\n"
        "try:\n"
        "    import vaibify.cli.main\n"
        "except SystemExit as errorExit:\n"
        "    print('exit', errorExit.code)\n"
        "print('loaded', 'vaibify.cli.commandBuild' in sys.modules)\n"
    )
    dictEnvironment = dict(os.environ, HOME=str(tmp_path))
    tResult = subprocess.run(
        [sys.executable, "-c", sProgram], capture_output=True, text=True,
        env=dictEnvironment, cwd=str(tmp_path),
    )
    assert tResult.stdout.split() == ["exit", "0", "loaded", "False"], (
        tResult.stdout + tResult.stderr
    )
    assert not os.path.exists(str(tmp_path / ".vaibify" / ".setup_done"))
