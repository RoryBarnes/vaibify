"""The build reads each project's own declaration, at one commit, strictly.

The image used to install only ``pythonPackages`` from the environment's
``vaibify.yml``; a package a project declared in its own repository
reached a container only if its start installed it, which the Level 3
shadow and ``reproduce.sh`` bypass. These tests hold the host-side
reader to its contract: which repositories are read, which file, at
which commit, through which git, and which lines are refused.

The reader's git is driven for real against a local ``git daemon``
(``git://`` is an ordinary transport under the hardening list, where
``file://`` is refused), so the shallow blobless fetch and the lazy
blob read are exercised, not imagined.
"""

import os
import shutil
import socket
import subprocess
import time
from types import SimpleNamespace

import pytest

from vaibify.cli import projectDeclarations, repositoryPreflight
from vaibify.cli.preflightResult import (
    S_LEVEL_FAIL, S_LEVEL_NOT_CHECKED, S_LEVEL_WARN,
)
from vaibify.cli.projectDeclarations import (
    ProjectDeclarationError, T_DECLARATION_FILES, flistCandidateRepositories,
    flistParsePyprojectDependencies, flistParseRequirementsText,
    fpreflightProjectDeclarations,
)
from vaibify.cli.repositoryPreflight import (
    RemoteUnreachableError, ftGitTransportFor,
)
from vaibify.reproducibility.dependencyPinning import T_LOCK_INPUT_CANDIDATES
from vaibify.reproducibility.gitHardening import LIST_GIT_HARDENING_CONFIG


# The daemon binds a port, so the whole file runs in the lane of its own.
pytestmark = pytest.mark.exclusive

S_LABEL = "toolbox's requirements.txt at 0123456789ab"
# Taken at import, before the suite's autouse stub replaces it per test.
_FN_REAL_BRANCH_PROBE = repositoryPreflight.fdictProbeRepositoryBranch


# ---------------------------------------------------------------------------
# A real remote: a bare repository served by a local git daemon
# ---------------------------------------------------------------------------


def _fnGit(sDirectory, *listArguments):
    subprocess.run(
        ["git", "-c", "user.email=t@example.org", "-c", "user.name=t",
         "-C", sDirectory, *listArguments],
        check=True, capture_output=True, text=True,
    )


def _fsCommitTree(sRoot, sName, dictFiles):
    """Commit ``dictFiles`` on ``main`` and serve it as ``<sName>.git``."""
    sSource = os.path.join(sRoot, sName + "-source")
    os.makedirs(sSource)
    _fnGit(sSource, "init", "-q", "-b", "main")
    for sPath, sContent in dictFiles.items():
        sFull = os.path.join(sSource, sPath)
        os.makedirs(os.path.dirname(sFull), exist_ok=True)
        with open(sFull, "w", encoding="utf-8") as fileHandle:
            fileHandle.write(sContent)
    _fnGit(sSource, "add", "-A")
    _fnGit(sSource, "commit", "-q", "-m", "declare")
    sBare = os.path.join(sRoot, "served", sName + ".git")
    subprocess.run(["git", "clone", "-q", "--bare", sSource, sBare],
                   check=True, capture_output=True)
    _fnGit(sBare, "config", "uploadpack.allowFilter", "true")
    return subprocess.run(
        ["git", "-C", sBare, "rev-parse", "refs/heads/main"],
        check=True, capture_output=True, text=True).stdout.strip()


def _fiFreePort():
    with socket.socket() as socketProbe:
        socketProbe.bind(("127.0.0.1", 0))
        return socketProbe.getsockname()[1]


@pytest.fixture
def fnServedRemote(tmp_path):
    """Yield a function that serves a tree and returns its git:// URL and commit."""
    sServed = tmp_path / "served"
    sServed.mkdir()
    iPort = _fiFreePort()
    processDaemon = subprocess.Popen(
        ["git", "daemon", "--reuseaddr", "--export-all",
         f"--base-path={sServed}", "--listen=127.0.0.1", f"--port={iPort}",
         "--enable=upload-pack", str(sServed)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    fDeadline = time.monotonic() + 10
    while time.monotonic() < fDeadline:
        with socket.socket() as socketProbe:
            if socketProbe.connect_ex(("127.0.0.1", iPort)) == 0:
                break
        time.sleep(0.05)
    else:
        processDaemon.kill()
        pytest.fail("git daemon did not start; it is part of git itself")

    def ftServe(sName, dictFiles):
        sCommit = _fsCommitTree(str(tmp_path), sName, dictFiles)
        return f"git://127.0.0.1:{iPort}/{sName}.git", sCommit
    yield ftServe
    processDaemon.kill()
    processDaemon.wait()


@pytest.fixture
def fnRealBranchProbe(monkeypatch):
    """Undo the suite's stub of the branch probe, which the reader calls."""
    monkeypatch.setattr(
        repositoryPreflight, "fdictProbeRepositoryBranch",
        _FN_REAL_BRANCH_PROBE)


def _fdictRead(sUrl, sName="toolbox", sMethod="reference", sBranch="main"):
    return projectDeclarations.fdictReadProjectDeclaration({
        "name": sName, "url": sUrl, "branch": sBranch,
        "installMethod": sMethod,
    })


def testTheDeclarationIsReadAtTheCommitTheFetchBrought(
    fnServedRemote, fnRealBranchProbe,
):
    sUrl, sCommit = fnServedRemote("toolbox", {
        ".vaibify/workflows/a.json": "{}",
        "requirements.txt": "numpy>=1.24  # arrays\n\nscipy\n",
    })
    dictDeclaration = _fdictRead(sUrl)
    assert dictDeclaration == {
        "sRepository": "toolbox", "sUrl": sUrl, "sBranch": "main",
        "sCommit": sCommit, "sFile": "requirements.txt",
        "listRequirements": ["numpy>=1.24", "scipy"],
    }


@pytest.mark.falsification
def testATreeWithoutVaibifyIsNotAProject(fnServedRemote, fnRealBranchProbe):
    """Kills: reading every listed repository as a project.

    Mutation: the ``.vaibify`` tree test is dropped, so a plain library
    clone's requirements reach the image.
    """
    sUrl, _ = fnServedRemote("library", {"requirements.txt": "numpy\n"})
    assert _fdictRead(sUrl, sName="library") is None


@pytest.mark.falsification
def testTheFirstFileInTheLocksOrderIsTheOneRead(
    fnServedRemote, fnRealBranchProbe,
):
    """Kills: reading the declaration files in any other order.

    The lock compiles from the first of these it finds, so the image
    must install from the same one. Mutation: the files are tried in
    reverse.
    """
    sUrl, _ = fnServedRemote("toolbox", {
        ".vaibify/k": "",
        "pyproject.toml": '[project]\nname = "toolbox"\n'
                          'dependencies = ["astropy>=5"]\n',
        "requirements.txt": "numpy\n",
    })
    dictDeclaration = _fdictRead(sUrl)
    assert dictDeclaration["sFile"] == "pyproject.toml"
    assert dictDeclaration["listRequirements"] == ["astropy>=5"]


def testTheMirrorIsNeverReadAsADeclaration(fnServedRemote, fnRealBranchProbe):
    sUrl, _ = fnServedRemote("toolbox", {
        ".vaibify/requirements.txt": "numpy\n",
    })
    dictDeclaration = _fdictRead(sUrl)
    assert (dictDeclaration["sFile"], dictDeclaration["listRequirements"]) == (
        "", [])


def testTheDeclarationFilesAreTheLocksOwnInputsWithoutTheMirror():
    assert T_DECLARATION_FILES == (
        "pyproject.toml", "requirements.in", "requirements.txt")
    assert T_DECLARATION_FILES == T_LOCK_INPUT_CANDIDATES[:3]


def testABranchTheRemoteLacksIsRefusedByName(fnServedRemote, fnRealBranchProbe):
    sUrl, _ = fnServedRemote("toolbox", {".vaibify/k": ""})
    with pytest.raises(ProjectDeclarationError, match="no branch 'develop'"):
        _fdictRead(sUrl, sBranch="develop")


def testAnUnsetBranchReadsTheRemotesDefault(fnServedRemote, fnRealBranchProbe):
    sUrl, sCommit = fnServedRemote("toolbox", {".vaibify/k": ""})
    dictDeclaration = _fdictRead(sUrl, sBranch="")
    assert (dictDeclaration["sBranch"], dictDeclaration["sCommit"]) == (
        "main", sCommit)


def testARemoteThatDoesNotAnswerIsUnreachable(fnRealBranchProbe):
    with pytest.raises(RemoteUnreachableError):
        _fdictRead(f"git://127.0.0.1:{_fiFreePort()}/absent.git")


# ---------------------------------------------------------------------------
# The git calls themselves
# ---------------------------------------------------------------------------


def _fnRecordingGit(listCalls, dictAnswers):
    def fnRun(listCommand, **kwargs):
        listCalls.append((listCommand, kwargs))
        sAnswer = ""
        for sMarker, sValue in dictAnswers.items():
            if sMarker in listCommand:
                sAnswer = sValue
        if not kwargs.get("text", False):
            sAnswer = sAnswer.encode()
        return SimpleNamespace(returncode=0, stdout=sAnswer, stderr="")
    return fnRun


S_COMMIT = "a" * 40


def _flistRecordedCalls(monkeypatch):
    monkeypatch.setattr(
        repositoryPreflight, "fdictProbeRepositoryBranch",
        lambda sUrl, sBranch, fnRun=None: {
            "bBranchExists": True, "sDefaultBranch": "main"})
    listCalls = []
    projectDeclarations.fdictReadProjectDeclaration(
        {"name": "toolbox", "url": "https://host.example/group/toolbox"},
        _fnRecordingGit(listCalls, {
            "rev-parse": S_COMMIT + "\n",
            "ls-tree": (f"040000 tree {'b' * 40}\t.vaibify\0"
                        f"100644 blob {'c' * 40}\trequirements.txt\0"),
            "cat-file": "numpy\n",
        }))
    return listCalls


@pytest.mark.falsification
def testEveryGitCallCarriesTheHardeningList(monkeypatch):
    """Kills: dropping the hardening list from the reader's git calls."""
    listCalls = _flistRecordedCalls(monkeypatch)
    assert len(listCalls) == 6
    for listCommand, _ in listCalls:
        iStart = listCommand.index(LIST_GIT_HARDENING_CONFIG[1]) - 1
        assert listCommand[iStart:iStart + len(LIST_GIT_HARDENING_CONFIG)] \
            == LIST_GIT_HARDENING_CONFIG


def testEveryGitCallRunsInAnEmptyDirectoryAndNeverPrompts(
    monkeypatch, tmp_path,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_DIR", str(tmp_path / ".git"))
    listCalls = _flistRecordedCalls(monkeypatch)
    for _, dictKeywords in listCalls:
        assert os.path.realpath(dictKeywords["cwd"]) != str(tmp_path.resolve())
        assert dictKeywords["env"]["GIT_TERMINAL_PROMPT"] == "0"
        assert "GIT_DIR" not in dictKeywords["env"]


def testTheBlobReadIsTheObjectTheCommitsTreeNames(monkeypatch):
    listCalls = _flistRecordedCalls(monkeypatch)
    listLsTree = [c for c, _ in listCalls if "ls-tree" in c][0]
    listCatFile = [c for c, _ in listCalls if "cat-file" in c][0]
    assert listLsTree[-1] == S_COMMIT
    assert listCatFile[-1] == "c" * 40


def testASymbolicLinkIsNotReadAsADeclaration(monkeypatch):
    monkeypatch.setattr(
        repositoryPreflight, "fdictProbeRepositoryBranch",
        lambda sUrl, sBranch, fnRun=None: {
            "bBranchExists": True, "sDefaultBranch": "main"})
    with pytest.raises(ProjectDeclarationError, match="not an ordinary file"):
        projectDeclarations.fdictReadProjectDeclaration(
            {"name": "toolbox", "url": "https://host.example/group/toolbox"},
            _fnRecordingGit([], {
                "rev-parse": S_COMMIT + "\n",
                "ls-tree": (f"040000 tree {'b' * 40}\t.vaibify\0"
                            f"120000 blob {'c' * 40}\trequirements.txt\0"),
            }))


# ---------------------------------------------------------------------------
# GitHub is asked the way the container clones it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sUrl, sAsked", [
    ("git@github.com:owner/repo.git", "https://github.com/owner/repo.git"),
    ("https://github.com/owner/repo", "https://github.com/owner/repo"),
])
def testGithubIsAskedOverHttpsWithTheGhHelper(monkeypatch, sUrl, sAsked):
    monkeypatch.setattr(shutil, "which", lambda sName: "/usr/bin/gh")
    sAskedUrl, listConfig = ftGitTransportFor(sUrl)
    assert sAskedUrl == sAsked
    assert listConfig == [
        "-c", "credential.helper=",
        "-c", "credential.helper=!gh auth git-credential",
    ]


@pytest.mark.parametrize("sUrl", [
    "git@github.com:owner/repo.git", "https://gitlab.example/group/repo",
    "ssh://git@host.example:2222/owner/repo.git",
])
def testWithoutGhOrOffGithubTheUrlIsAskedAsWritten(monkeypatch, sUrl):
    monkeypatch.setattr(
        shutil, "which",
        lambda sName: None if "github.com" in sUrl else "/usr/bin/gh")
    assert ftGitTransportFor(sUrl) == (sUrl, [])


# ---------------------------------------------------------------------------
# Only requirement lines are accepted
# ---------------------------------------------------------------------------


def testRequirementLinesAreReadInTheirStandardSpelling():
    assert flistParseRequirementsText(
        "# a comment\nNumPy >= 1.24 ; python_version >= '3.9'\n"
        "scipy[extra]==1.11  # pinned\n\n", S_LABEL,
    ) == ['NumPy>=1.24; python_version >= "3.9"', "scipy[extra]==1.11"]


@pytest.mark.parametrize("sLine", [
    "-r other.txt", "-c constraints.txt", "-e .", "--index-url https://x",
    "--extra-index-url https://x", "numpy \\",
])
def testAnOptionLineIsRefusedNamingTheFileAndLine(sLine):
    with pytest.raises(ProjectDeclarationError) as errorInfo:
        flistParseRequirementsText(f"numpy\n{sLine}\n", S_LABEL)
    assert f"{S_LABEL}, line 2" in str(errorInfo.value)
    assert "pip option" in str(errorInfo.value)


@pytest.mark.falsification
def testARequirementNamingAUrlIsRefused():
    """Kills: accepting a direct reference.

    ``name @ url`` parses as a requirement, so only the URL check stands
    between it and an image that installs whatever the URL serves.
    """
    with pytest.raises(ProjectDeclarationError, match="names a URL"):
        flistParseRequirementsText(
            "toolbox @ https://host.example/toolbox-1.0.tar.gz\n", S_LABEL)


@pytest.mark.parametrize("sLine", [
    "./local/package", "/absolute/package", "git+https://host.example/x.git",
    "https://host.example/x.whl",
])
def testAPathOrBareUrlIsRefused(sLine):
    with pytest.raises(ProjectDeclarationError, match="line 1"):
        flistParseRequirementsText(sLine + "\n", S_LABEL)


def testPyprojectDependenciesAreRead():
    assert flistParsePyprojectDependencies(
        '[project]\nname = "x"\ndependencies = ["numpy>=1.24", "scipy"]\n',
        S_LABEL) == ["numpy>=1.24", "scipy"]


def testAPyprojectWithoutAProjectTableDeclaresNothing():
    assert flistParsePyprojectDependencies(
        "[tool.black]\nline-length = 79\n", S_LABEL) == []


@pytest.mark.parametrize("sText, sMatch", [
    ('[project]\nname = "x"\ndynamic = ["dependencies"]\n', "dynamic"),
    ('[project]\ndependencies = "numpy"\n', "not a list"),
    ("[project\n", "not valid TOML"),
    ('[project]\ndependencies = ["x @ https://h/x.whl"]\n', "names a URL"),
])
def testAPyprojectThatCannotBeInstalledAsWrittenIsRefused(sText, sMatch):
    with pytest.raises(ProjectDeclarationError, match=sMatch):
        flistParsePyprojectDependencies(sText, S_LABEL)


# ---------------------------------------------------------------------------
# Which repositories are read, and the preflight's verdicts
# ---------------------------------------------------------------------------


@pytest.mark.falsification
def testARepositoryInstalledWithoutItsDependenciesIsNeverRead():
    """Kills: reading every repository, whatever its install method.

    Mutation: the unread install methods become the empty tuple.
    """
    listRepositories = [
        {"name": "engine", "url": "https://h/engine", "installMethod": "c_and_pip"},
        {"name": "helper", "url": "https://h/helper", "installMethod": "pip_no_deps"},
        {"name": "study", "url": "https://h/study", "installMethod": "reference"},
        {"name": "toolbox", "url": "https://h/toolbox"},
    ]
    assert [d["name"] for d in flistCandidateRepositories(listRepositories)] \
        == ["study", "toolbox"]


def _fconfigWith(listRepositories, sPipInstallFlags=""):
    return SimpleNamespace(
        listRepositories=listRepositories, sPipInstallFlags=sPipInstallFlags)


LIST_REPOSITORIES = [
    {"name": "study", "url": "https://h/study", "installMethod": "reference"},
    {"name": "plotkit", "url": "https://h/plotkit", "installMethod": "pip_editable"},
]


def _fdictDeclaration(listRequirements, sRepository="study"):
    return {"sRepository": sRepository, "sFile": "requirements.txt",
            "sCommit": S_COMMIT, "listRequirements": listRequirements}


@pytest.mark.falsification
@pytest.mark.parametrize("errorRead", [
    ProjectDeclarationError("study's requirements.txt, line 3: '-e .'"),
    RemoteUnreachableError("https://h/study: could not read"),
])
def testADeclarationThatCannotBeReadRefusesTheBuild(errorRead):
    """Kills: answering an unreadable declaration with "not checked".

    The build itself must read it, so a build allowed on would stop at
    the same remote after the dashboard had stopped the container.
    """
    def fnRead(listRepositories):
        raise errorRead
    preflightResult = fpreflightProjectDeclarations(
        _fconfigWith(LIST_REPOSITORIES), fnRead=fnRead)
    assert preflightResult.sLevel == S_LEVEL_FAIL
    assert str(errorRead) in preflightResult.sMessage


def testAnUnknownDeclaredNameRefusesNamingItsRepository():
    preflightResult = fpreflightProjectDeclarations(
        _fconfigWith(LIST_REPOSITORIES),
        fnRead=lambda listRepositories: [_fdictDeclaration(["numpy", "nmupy"])],
        fbNameExists=lambda sName: sName != "nmupy")
    assert preflightResult.sLevel == S_LEVEL_FAIL
    assert "'nmupy' (study's requirements.txt)" in preflightResult.sMessage


def testAnIndexThatCannotBeAskedIsNotChecked():
    from vaibify.cli.pythonPackagePreflight import IndexUnreachableError

    def fbNameExists(sName):
        raise IndexUnreachableError("pypi.org could not be reached")
    preflightResult = fpreflightProjectDeclarations(
        _fconfigWith(LIST_REPOSITORIES),
        fnRead=lambda listRepositories: [_fdictDeclaration(["numpy"])],
        fbNameExists=fbNameExists)
    assert preflightResult.sLevel == S_LEVEL_NOT_CHECKED


def testAnOverriddenIndexSkipsTheNameCheck():
    preflightResult = fpreflightProjectDeclarations(
        _fconfigWith(LIST_REPOSITORIES, "--index-url https://mirror.example"),
        fnRead=lambda listRepositories: [_fdictDeclaration(["internal-only"])],
        fbNameExists=lambda sName: False)
    assert preflightResult is None


def testADeclaredPackageThatIsAlsoAListedCloneIsReported():
    preflightResult = fpreflightProjectDeclarations(
        _fconfigWith(LIST_REPOSITORIES),
        fnRead=lambda listRepositories: [_fdictDeclaration(["PlotKit>=1"])],
        fbNameExists=lambda sName: True)
    assert preflightResult.sLevel == S_LEVEL_WARN
    assert preflightResult.sMessage.startswith("plotkit (study declares it)")
    assert "reproduce.sh use the image's copy" in preflightResult.sMessage


def testNoCandidateAsksNothing():
    def fnRead(listRepositories):
        raise AssertionError("no repository should have been read")
    assert fpreflightProjectDeclarations(
        _fconfigWith([LIST_REPOSITORIES[0] | {"installMethod": "c_and_pip"}]),
        fnRead=fnRead) is None
