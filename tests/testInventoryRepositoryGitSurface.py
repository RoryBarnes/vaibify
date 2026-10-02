"""Tests for tools/inventoryRepositoryGitSurface.py (read-only, non-executing).

Every repository here is synthetic: directories and files written by the
test, with no git process involved except in the one cross-check that
asks real git to parse the same config text (parsing only; git is never
pointed at a repository whose config could run anything).
"""

import ast
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys

import pytest

PATH_REPOSITORY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH_TOOL = os.path.join(
    PATH_REPOSITORY, "tools", "inventoryRepositoryGitSurface.py",
)


def fmoduleLoadTool():
    """Load the tool by path; tools/ is not a package."""
    specTool = importlib.util.spec_from_file_location(
        "inventoryRepositoryGitSurface", PATH_TOOL,
    )
    moduleTool = importlib.util.module_from_spec(specTool)
    sys.modules["inventoryRepositoryGitSurface"] = moduleTool
    specTool.loader.exec_module(moduleTool)
    return moduleTool


tool = fmoduleLoadTool()


def fsMakeRepository(tmp_path, sName="repo", sConfig="", dictFiles=None,
                     dictHooks=None):
    """Create a synthetic repository directory and return its path."""
    sRepo = str(tmp_path / sName)
    os.makedirs(os.path.join(sRepo, ".git", "hooks"))
    with open(os.path.join(sRepo, ".git", "config"), "w") as fileConfig:
        fileConfig.write(sConfig)
    for sRelative, sText in (dictFiles or {}).items():
        sPath = os.path.join(sRepo, sRelative)
        os.makedirs(os.path.dirname(sPath), exist_ok=True)
        with open(sPath, "w") as fileText:
            fileText.write(sText)
    for sHook, bExecutable in (dictHooks or {}).items():
        sPath = os.path.join(sRepo, ".git", "hooks", sHook)
        with open(sPath, "w") as fileHook:
            fileHook.write("#!/bin/sh\nexit 0\n")
        iMode = 0o755 if bExecutable else 0o644
        os.chmod(sPath, iMode)
    return sRepo


def fsetItemKeys(dictInventory, sKind):
    """Return the identifying names of one kind of surface item."""
    return {
        dictItem.get("sKey") or dictItem.get("sName")
        or dictItem["sAttribute"] + "=" + dictItem["sDriverName"]
        for dictItem in dictInventory["listItems"]
        if dictItem["sKind"] == sKind
    }


def testConfigParserHandlesSectionsQuotesCommentsAndContinuations():
    sText = (
        "[core]\n\tfsmonitor = /bin/true # trailing comment\n"
        '[filter "my.lfs"]\n\tclean = "a ; b"\n\tsmudge = x \\\n\t  y\n'
        "[Url \"ext::tool %S\"]\n\tinsteadOf = alias:\n"
        "[bare]\n\tflag\n"
    )
    dictParsed = dict(tool.flistParseConfigText(sText))
    assert dictParsed["core.fsmonitor"] == "/bin/true"
    assert dictParsed["filter.my.lfs.clean"] == "a ; b"
    assert dictParsed["filter.my.lfs.smudge"] == "x \t  y"
    assert dictParsed["url.ext::tool %S.insteadof"] == "alias:"
    assert dictParsed["bare.flag"] is None


def testConfigParserAgreesWithGitOnATrickyFile(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    sText = (
        '[core]\n\tfsmonitor = "/p/a b" ; note\n'
        '[filter "x"]\n\tclean = one \\\n\ttwo\n'
        '[remote "origin"]\n\turl = ext::tool\n'
        '[credential "https://example.org"]\n\thelper = store\n'
    )
    sFile = str(tmp_path / "tricky.cfg")
    with open(sFile, "w") as fileConfig:
        fileConfig.write(sText)
    processListing = subprocess.run(
        ["git", "config", "--file", sFile, "--list"],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert processListing.returncode == 0, processListing.stderr
    sListing = processListing.stdout
    dictGit = dict(sLine.split("=", 1) for sLine in sListing.splitlines())
    dictMine = dict(tool.flistParseConfigText(sText))
    assert set(dictMine) == set(dictGit)
    for sKey, sValue in dictGit.items():
        assert " ".join(dictMine[sKey].split()) == " ".join(sValue.split())


@pytest.mark.parametrize("sConfig,sKey", [
    ("[core]\n\tfsmonitor = /usr/bin/hook\n", "core.fsmonitor"),
    ('[filter "x"]\n\tclean = c\n', "filter.x.clean"),
    ('[filter "x"]\n\tsmudge = s\n', "filter.x.smudge"),
    ('[filter "x"]\n\tprocess = p\n', "filter.x.process"),
    ('[diff "x"]\n\ttextconv = t\n', "diff.x.textconv"),
    ("[diff]\n\texternal = e\n", "diff.external"),
    ("[core]\n\tpager = p\n", "core.pager"),
    ("[pager]\n\tstatus = p\n", "pager.status"),
    ("[core]\n\tsshCommand = s\n", "core.sshcommand"),
    ("[core]\n\tgitProxy = g\n", "core.gitproxy"),
    ("[credential]\n\thelper = h\n", "credential.helper"),
    ('[credential "https://h"]\n\thelper = h\n',
     "credential.https://h.helper"),
    ("[gpg]\n\tprogram = g\n", "gpg.program"),
    ('[remote "o"]\n\turl = ext::tool %S\n', "remote.o.url"),
    ('[url "ext::tool %S"]\n\tinsteadOf = a:\n', "url.ext::tool %S.insteadof"),
    ('[merge "m"]\n\tdriver = d\n', "merge.m.driver"),
])
def testEveryExecutingConfigKeyIsReported(tmp_path, sConfig, sKey):
    dictInventory = tool.fdictInventoryRepository(
        fsMakeRepository(tmp_path, sConfig=sConfig))
    assert sKey in fsetItemKeys(dictInventory, "config")


@pytest.mark.parametrize("sConfig", [
    "[core]\n\tfsmonitor = true\n",
    "[core]\n\tfsmonitor = false\n",
    "[credential]\n\thelper =\n",
    "[pager]\n\tstatus = false\n",
    '[remote "o"]\n\turl = https://example.org/r.git\n',
    '[url "https://mirror/"]\n\tinsteadOf = https://example.org/\n',
    "[user]\n\tname = Someone\n",
])
def testInertConfigIsNotReported(tmp_path, sConfig):
    dictInventory = tool.fdictInventoryRepository(
        fsMakeRepository(tmp_path, sConfig=sConfig))
    assert dictInventory["listItems"] == []


def testIncludedFileIsResolvedAndConditionalIncludeIsFlagged(tmp_path):
    sRepo = fsMakeRepository(
        tmp_path, sConfig=(
            "[include]\n\tpath = more.cfg\n"
            '[includeIf "gitdir:/anything/"]\n\tpath = maybe.cfg\n'
        ),
    )
    with open(os.path.join(sRepo, ".git", "more.cfg"), "w") as fileMore:
        fileMore.write("[core]\n\tfsmonitor = /a\n")
    with open(os.path.join(sRepo, ".git", "maybe.cfg"), "w") as fileMaybe:
        fileMaybe.write("[core]\n\tpager = /b\n")
    dictInventory = tool.fdictInventoryRepository(sRepo)
    dictByKey = {d["sKey"]: d for d in dictInventory["listItems"]}
    assert dictByKey["core.fsmonitor"]["bConditional"] is False
    assert dictByKey["core.pager"]["bConditional"] is True
    assert dictByKey["core.pager"]["sSource"] == os.path.join(
        ".git", "maybe.cfg")


def testIncludeCycleTerminates(tmp_path):
    sRepo = fsMakeRepository(tmp_path, sConfig="[include]\n\tpath = a.cfg\n")
    for sName, sOther in (("a.cfg", "b.cfg"), ("b.cfg", "a.cfg")):
        with open(os.path.join(sRepo, ".git", sName), "w") as fileCycle:
            fileCycle.write("[include]\n\tpath = " + sOther + "\n"
                            "[core]\n\tpager = /p\n")
    dictInventory = tool.fdictInventoryRepository(sRepo)
    assert "core.pager" in fsetItemKeys(dictInventory, "config")


def testAnIncludeOutsideTheRepositoryNeverLeaksAnAbsolutePath(tmp_path):
    sOutside = str(tmp_path / "elsewhere.cfg")
    with open(sOutside, "w") as fileOutside:
        fileOutside.write("[core]\n\tpager = /p\n")
    sRepo = fsMakeRepository(
        tmp_path, sConfig="[include]\n\tpath = " + sOutside + "\n")
    sReport = json.dumps(tool.fdictInventoryRepository(sRepo))
    assert str(tmp_path) not in sReport
    assert "outside:elsewhere.cfg" in sReport


def testOnlyExecutableNonSampleHooksAreReported(tmp_path):
    sRepo = fsMakeRepository(tmp_path, dictHooks={
        "pre-commit": True, "commit-msg": False, "pre-push.sample": True,
    })
    dictInventory = tool.fdictInventoryRepository(sRepo)
    assert fsetItemKeys(dictInventory, "hook") == {"pre-commit"}


def testHooksPathRedirectIsReportedAndItsHooksListed(tmp_path):
    sRepo = fsMakeRepository(
        tmp_path, sConfig="[core]\n\thooksPath = shared\n")
    sShared = os.path.join(sRepo, "shared")
    os.makedirs(sShared)
    sHook = os.path.join(sShared, "pre-push")
    with open(sHook, "w") as fileHook:
        fileHook.write("#!/bin/sh\n")
    os.chmod(sHook, os.stat(sHook).st_mode | stat.S_IXUSR)
    dictInventory = tool.fdictInventoryRepository(sRepo)
    assert fsetItemKeys(dictInventory, "hooksPathRedirect") == {
        "core.hookspath"}
    assert fsetItemKeys(dictInventory, "hook") == {"pre-push"}


def testAttributeDriversReportWhetherTheConfigDefinesThem(tmp_path):
    sRepo = fsMakeRepository(
        tmp_path, sConfig='[filter "known"]\n\tclean = c\n',
        dictFiles={
            ".gitattributes": "*.dat filter=known\n*.nb filter=stranger\n",
            "sub/.gitattributes": "*.txt diff=pretty merge=union\n",
            ".git/info/attributes": "*.x filter=hidden\n",
        },
    )
    dictInventory = tool.fdictInventoryRepository(sRepo)
    listDrivers = [
        d for d in dictInventory["listItems"] if d["sKind"] == "attributeDriver"
    ]
    dictDefined = {
        d["sAttribute"] + "=" + d["sDriverName"]: d["bDefinedLocally"]
        for d in listDrivers
    }
    assert dictDefined == {
        "filter=known": True, "filter=stranger": False,
        "diff=pretty": False, "merge=union": False, "filter=hidden": False,
    }
    dictSources = {d["sDriverName"]: d["sSource"] for d in listDrivers}
    assert dictSources["hidden"] == os.path.join(".git", "info", "attributes")
    assert dictSources["pretty"] == os.path.join("sub", ".gitattributes")


def testWorktreeGitFilePointerIsFollowed(tmp_path):
    sCommonGitDirectory = tmp_path / "common" / "worktreeGit"
    os.makedirs(sCommonGitDirectory)
    with open(sCommonGitDirectory / "config", "w") as fileConfig:
        fileConfig.write("[core]\n\tpager = /p\n")
    sWorktree = tmp_path / "work"
    os.makedirs(sWorktree)
    with open(sWorktree / ".git", "w") as filePointer:
        filePointer.write("gitdir: " + str(sCommonGitDirectory) + "\n")
    dictInventory = tool.fdictInventoryRepository(str(sWorktree))
    assert "core.pager" in fsetItemKeys(dictInventory, "config")


def testFingerprintIsStableAndChangesWithTheSurface(tmp_path):
    sRepo = fsMakeRepository(tmp_path, sConfig="[core]\n\tpager = /p\n")
    sFirst = tool.fdictInventoryRepository(sRepo)["sFingerprint"]
    assert tool.fdictInventoryRepository(sRepo)["sFingerprint"] == sFirst
    with open(os.path.join(sRepo, ".git", "config"), "a") as fileConfig:
        fileConfig.write("[core]\n\tfsmonitor = /q\n")
    assert tool.fdictInventoryRepository(sRepo)["sFingerprint"] != sFirst


def testFingerprintIgnoresInertChanges(tmp_path):
    sRepo = fsMakeRepository(tmp_path, sConfig="[core]\n\tpager = /p\n")
    sFirst = tool.fdictInventoryRepository(sRepo)["sFingerprint"]
    with open(os.path.join(sRepo, ".git", "config"), "a") as fileConfig:
        fileConfig.write("[user]\n\tname = Someone\n")
    assert tool.fdictInventoryRepository(sRepo)["sFingerprint"] == sFirst


def testNonRepositoryIsRefusedAndCommandLineExitsTwo(tmp_path, capsys):
    with pytest.raises(ValueError):
        tool.fdictInventoryRepository(str(tmp_path))
    sys.argv = ["inventoryRepositoryGitSurface.py", str(tmp_path)]
    assert tool.main() == 2
    assert "not a git repository" in capsys.readouterr().err


def testDiscoverFindsRepositoriesToAFixedDepthOnly(tmp_path):
    fsMakeRepository(tmp_path, "top")
    fsMakeRepository(tmp_path / "group", "inner")
    os.makedirs(tmp_path / "a" / "b" / "c")
    fsMakeRepository(tmp_path / "a" / "b" / "c", "tooDeep")
    listFound = [os.path.basename(s) for s in
                 tool.flistDiscoverRepositories(str(tmp_path))]
    assert sorted(listFound) == ["inner", "top"]


def testSummaryCountsRepositoriesPerMechanismWithoutPaths(tmp_path, capsys):
    fsMakeRepository(tmp_path, "one", sConfig="[core]\n\tpager = /p\n",
                     dictHooks={"pre-commit": True})
    fsMakeRepository(tmp_path, "two")
    sys.argv = ["inventoryRepositoryGitSurface.py", "--discover",
                str(tmp_path), "--summary"]
    assert tool.main() == 0
    sOutput = capsys.readouterr().out
    dictSummary = json.loads(sOutput)
    assert dictSummary["iRepositories"] == 2
    assert dictSummary["iRepositoriesWithAnySurface"] == 1
    assert dictSummary["dictRepositoriesPerMechanism"] == {
        "core.pager": 1, "hook": 1}
    assert str(tmp_path) not in sOutput


def testInventoryNeverExecutesAnythingTheRepositoryConfigures(tmp_path):
    sMarker = str(tmp_path / "markerThatMustNotExist")
    sScript = str(tmp_path / "program.sh")
    with open(sScript, "w") as fileScript:
        fileScript.write("#!/bin/sh\n: > " + sMarker + "\n")
    os.chmod(sScript, 0o755)
    sRepo = fsMakeRepository(
        tmp_path, sConfig=(
            "[core]\n\tfsmonitor = " + sScript + "\n\tpager = " + sScript
            + "\n"), dictHooks={"pre-commit": True},
        dictFiles={".gitattributes": "* filter=x\n"},
    )
    shutil.copy(sScript, os.path.join(sRepo, ".git", "hooks", "post-index-change"))
    tool.fdictInventoryRepository(sRepo)
    assert not os.path.exists(sMarker)


def testToolSourceImportsNoProcessLaunchingModule():
    with open(PATH_TOOL) as fileTool:
        treeTool = ast.parse(fileTool.read())
    setImported = set()
    for nodeTool in ast.walk(treeTool):
        if isinstance(nodeTool, ast.Import):
            setImported.update(a.name.split(".")[0] for a in nodeTool.names)
        elif isinstance(nodeTool, ast.ImportFrom):
            setImported.add((nodeTool.module or "").split(".")[0])
    assert not setImported & {"subprocess", "pty", "multiprocessing",
                              "asyncio", "shlex"}
    assert "system" not in {
        n.attr for n in ast.walk(treeTool) if isinstance(n, ast.Attribute)
    }
