"""The running vaibify's identity: what it started as, and whether it still is.

Every question here is asked of a REAL git, in a throwaway checkout
holding a throwaway package, so nothing reads or touches the vaibify
tree the suite runs from. The expected values come from what each test
did to the checkout, never from the module: a test that committed knows
HEAD moved, and a test that edited a file without committing knows HEAD
did not.
"""

import hashlib
import os
import re
import shutil

import pytest

from tests.reproductionSourceFixtures import (
    fnCommitEverything,
    fnWriteText,
    fsRunGit,
)
from vaibify import versionIdentity


S_PACKAGE_NAME = "somepackage"
S_MODULE_PATH = f"{S_PACKAGE_NAME}/module.py"
S_UNCHANGED = "the code this process started with"
T_STARTUP_KEYS = (
    "bModified", "sCommit", "sDescribe", "sSource", "sSourceTreeSha256",
)


@pytest.fixture(autouse=True)
def fnIsolateGitAndTheStartupIdentity(monkeypatch, tmp_path):
    """Give each test a process that has captured nothing, and no git config.

    The holder is swapped for a copy before it is forgotten, so the
    suite's own capture of the real package is back in place after the
    test, and no later test captures lazily in the middle of its run.
    """
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(
        versionIdentity, "_DICT_STARTUP", dict(versionIdentity._DICT_STARTUP))
    versionIdentity.fnForgetStartupIdentity()


def _fsBuildCheckout(tmp_path):
    """Return the package directory of a committed checkout tagged v1.2.3."""
    sWorkTree = str(tmp_path / "checkout")
    os.makedirs(sWorkTree)
    fsRunGit(["init", "--quiet"], sWorkTree)
    fnWriteText(sWorkTree, ".gitignore", "__pycache__/\n*.pyc\n")
    fnWriteText(sWorkTree, f"{S_PACKAGE_NAME}/__init__.py", "")
    fnWriteText(sWorkTree, S_MODULE_PATH, "VALUE = 1\n")
    fnCommitEverything(sWorkTree, "first")
    fsRunGit(["tag", "v1.2.3"], sWorkTree)
    return os.path.join(sWorkTree, S_PACKAGE_NAME)


def _fsBuildLoosePackage(sParent):
    """Return a package directory that no checkout holds."""
    sPackage = os.path.join(sParent, S_PACKAGE_NAME)
    fnWriteText(sPackage, "__init__.py", "")
    fnWriteText(sPackage, "module.py", "VALUE = 1\n")
    return sPackage


def _fnAnswerTheInstalledVersion(monkeypatch, sVersion="1.2.3",
                                 sCommit="gabc1234"):
    """Stand in for the installed ``_version`` module setuptools_scm writes."""
    monkeypatch.setattr(
        versionIdentity, "_ftReadInstalledVersion",
        lambda: (sVersion, sCommit))


def _fsHead(sPackage):
    """Return HEAD of the checkout holding ``sPackage``, asked by the test."""
    return fsRunGit(["rev-parse", "HEAD"], os.path.dirname(sPackage))


def _fdictCaptureThenDescribe(sPackage):
    """Capture ``sPackage`` as this process's start, then describe it now."""
    versionIdentity.fnCaptureStartupIdentity(sPackage)
    return versionIdentity.fdictDescribeRunningVaibify()


def testACleanUnchangedCheckoutIsTheCodeItStartedWith(tmp_path):
    sPackage = _fsBuildCheckout(tmp_path)
    dictIdentity = _fdictCaptureThenDescribe(sPackage)
    sHead = _fsHead(sPackage)
    matchDescribe = re.fullmatch(
        r"v1\.2\.3-0-g([0-9a-f]+)", dictIdentity["sDescribe"])
    assert matchDescribe, dictIdentity["sDescribe"]
    assert sHead.startswith(matchDescribe.group(1))
    assert dictIdentity["sSource"] == "checkout"
    assert dictIdentity["sCommit"] == sHead
    assert dictIdentity["bModified"] is False
    assert dictIdentity["bSourceChangedSinceStart"] is False
    assert dictIdentity["sMeaning"] == S_UNCHANGED
    assert re.fullmatch(r"[0-9a-f]{64}", dictIdentity["sSourceTreeSha256"])
    assert set(dictIdentity) == set(T_STARTUP_KEYS) | {
        "bSourceChangedSinceStart", "sMeaning"}


def testATreeModifiedAtStartupIsModifiedButUnchangedSinceStart(tmp_path):
    sPackage = _fsBuildCheckout(tmp_path)
    fnWriteText(os.path.dirname(sPackage), S_MODULE_PATH, "VALUE = 2\n")
    dictIdentity = _fdictCaptureThenDescribe(sPackage)
    assert dictIdentity["bModified"] is True
    assert dictIdentity["sDescribe"].endswith("-dirty")
    assert dictIdentity["sCommit"] == _fsHead(sPackage)
    assert dictIdentity["bSourceChangedSinceStart"] is False
    assert dictIdentity["sMeaning"] == S_UNCHANGED


@pytest.mark.falsification
def testTheIdentityFlagsHeadMovedSinceStartup(tmp_path):
    """A commit after start is a change, and the identity names the new HEAD.

    The test committed, so HEAD moved and a file's bytes changed; that is
    the oracle, whatever the module computes.

    Kills: the digest recomputation at use removed, so the startup
    digest is compared with itself.
    """
    sPackage = _fsBuildCheckout(tmp_path)
    sHeadAtStart = _fsHead(sPackage)
    versionIdentity.fnCaptureStartupIdentity(sPackage)
    fnWriteText(os.path.dirname(sPackage), S_MODULE_PATH, "VALUE = 2\n")
    sHeadNow = fnCommitEverything(os.path.dirname(sPackage), "second")
    dictIdentity = versionIdentity.fdictDescribeRunningVaibify()
    assert sHeadNow != sHeadAtStart
    assert dictIdentity["sCommit"] == sHeadAtStart
    assert dictIdentity["bSourceChangedSinceStart"] is True
    assert dictIdentity["sCurrentCommit"] == sHeadNow
    assert (dictIdentity["sCurrentSourceTreeSha256"]
            != dictIdentity["sSourceTreeSha256"])
    assert dictIdentity["sMeaning"] != S_UNCHANGED


@pytest.mark.falsification
def testTheIdentityFlagsACleanTreeMadeDirtyWithHeadUnchanged(tmp_path):
    """An edit after start is a change even though HEAD stays where it was.

    The test edited a file and committed nothing, so HEAD did not move;
    only the bytes did.

    Kills: "changed" decided by comparing commits instead of digests.
    """
    sPackage = _fsBuildCheckout(tmp_path)
    sHeadAtStart = _fsHead(sPackage)
    versionIdentity.fnCaptureStartupIdentity(sPackage)
    fnWriteText(os.path.dirname(sPackage), S_MODULE_PATH, "VALUE = 2\n")
    dictIdentity = versionIdentity.fdictDescribeRunningVaibify()
    assert _fsHead(sPackage) == sHeadAtStart == dictIdentity["sCommit"]
    assert dictIdentity["bModified"] is False
    assert dictIdentity["bSourceChangedSinceStart"] is True
    assert "sCurrentCommit" not in dictIdentity
    assert dictIdentity["sMeaning"] != S_UNCHANGED


def testADirtyTreeEditedFurtherIsAChangeSinceStart(tmp_path):
    sPackage = _fsBuildCheckout(tmp_path)
    fnWriteText(os.path.dirname(sPackage), S_MODULE_PATH, "VALUE = 2\n")
    versionIdentity.fnCaptureStartupIdentity(sPackage)
    fnWriteText(os.path.dirname(sPackage), S_MODULE_PATH, "VALUE = 3\n")
    dictIdentity = versionIdentity.fdictDescribeRunningVaibify()
    assert dictIdentity["bModified"] is True
    assert dictIdentity["bSourceChangedSinceStart"] is True
    assert "sCurrentCommit" not in dictIdentity


def testANewUntrackedModuleAfterStartupIsAChange(tmp_path):
    sPackage = _fsBuildCheckout(tmp_path)
    versionIdentity.fnCaptureStartupIdentity(sPackage)
    fnWriteText(sPackage, "added.py", "ADDED = True\n")
    dictIdentity = versionIdentity.fdictDescribeRunningVaibify()
    assert dictIdentity["bSourceChangedSinceStart"] is True
    assert "sCurrentCommit" not in dictIdentity


def testAnUntrackedModuleAtStartupMakesTheCheckoutModified(tmp_path):
    """Python imports an untracked module; describe's ``-dirty`` cannot see it."""
    sPackage = _fsBuildCheckout(tmp_path)
    fnWriteText(sPackage, "added.py", "ADDED = True\n")
    dictIdentity = _fdictCaptureThenDescribe(sPackage)
    assert not dictIdentity["sDescribe"].endswith("-dirty")
    assert dictIdentity["bModified"] is True


def testBytecodeWrittenAfterStartupIsNotAChange(tmp_path):
    sPackage = _fsBuildCheckout(tmp_path)
    versionIdentity.fnCaptureStartupIdentity(sPackage)
    fnWriteText(sPackage, "__pycache__/module.cpython-311.pyc", "bytecode")
    fnWriteText(sPackage, "stray.pyc", "bytecode")
    dictIdentity = versionIdentity.fdictDescribeRunningVaibify()
    assert dictIdentity["bSourceChangedSinceStart"] is False
    assert dictIdentity["sMeaning"] == S_UNCHANGED


def testAPackageOutsideACheckoutReportsTheInstalledVersion(
    tmp_path, monkeypatch,
):
    _fnAnswerTheInstalledVersion(monkeypatch)
    sPackage = _fsBuildLoosePackage(str(tmp_path / "site"))
    dictIdentity = _fdictCaptureThenDescribe(sPackage)
    assert dictIdentity["sSource"] == "installed"
    assert dictIdentity["sDescribe"] == "1.2.3"
    assert dictIdentity["sCommit"] == "gabc1234"
    assert dictIdentity["bModified"] is None
    assert dictIdentity["bSourceChangedSinceStart"] is False
    assert "sError" not in dictIdentity


def testAPackageInsideAnotherRepositoryIsNotReadAsItsCheckout(
    tmp_path, monkeypatch,
):
    """A virtual environment inside a project must not describe the project."""
    _fnAnswerTheInstalledVersion(monkeypatch)
    sProject = str(tmp_path / "project")
    os.makedirs(sProject)
    fsRunGit(["init", "--quiet"], sProject)
    sPackage = _fsBuildLoosePackage(os.path.join(sProject, "venv", "site"))
    fnCommitEverything(sProject, "the project, venv and all")
    dictIdentity = _fdictCaptureThenDescribe(sPackage)
    assert dictIdentity["sSource"] == "installed"
    assert dictIdentity["sDescribe"] == "1.2.3"


def testAnUnreadableInstalledVersionIsRecordedNotInvented(
    tmp_path, monkeypatch,
):
    _fnAnswerTheInstalledVersion(monkeypatch, sVersion="", sCommit="")
    sPackage = _fsBuildLoosePackage(str(tmp_path / "site"))
    dictIdentity = _fdictCaptureThenDescribe(sPackage)
    assert dictIdentity["sSource"] == "installed"
    assert dictIdentity["sDescribe"] == ""
    assert dictIdentity["sCommit"] == ""
    assert "installed version could not be read" in dictIdentity["sError"]


def testAMissingGitFallsBackToTheInstalledVersionAndSaysWhy(
    tmp_path, monkeypatch,
):
    sPackage = _fsBuildCheckout(tmp_path)
    _fnAnswerTheInstalledVersion(monkeypatch)
    sEmptyPath = str(tmp_path / "noGitHere")
    os.makedirs(sEmptyPath)
    monkeypatch.setenv("PATH", sEmptyPath)
    dictIdentity = _fdictCaptureThenDescribe(sPackage)
    assert dictIdentity["sSource"] == "installed"
    assert dictIdentity["sDescribe"] == "1.2.3"
    assert dictIdentity["bModified"] is None
    assert "git is not available" in dictIdentity["sError"]
    assert str(tmp_path) not in dictIdentity["sError"]
    assert dictIdentity["bSourceChangedSinceStart"] is False


def testAnInheritedGitDirDoesNotRedirectTheQuestion(tmp_path, monkeypatch):
    """A vaibify started from another repository's hook describes itself."""
    sPackage = _fsBuildCheckout(tmp_path)
    sHead = _fsHead(sPackage)
    sOther = str(tmp_path / "other")
    os.makedirs(sOther)
    fsRunGit(["init", "--quiet"], sOther)
    fnWriteText(sOther, "elsewhere.txt", "elsewhere\n")
    assert fnCommitEverything(sOther, "other") != sHead
    monkeypatch.setenv("GIT_DIR", os.path.join(sOther, ".git"))
    dictIdentity = _fdictCaptureThenDescribe(sPackage)
    assert dictIdentity["sSource"] == "checkout"
    assert dictIdentity["sCommit"] == sHead


def testASourceTreeThatCannotBeHashedIsUnknownNotUnchanged(
    tmp_path, monkeypatch,
):
    _fnAnswerTheInstalledVersion(monkeypatch)
    sPackage = _fsBuildLoosePackage(str(tmp_path / "site"))
    versionIdentity.fnCaptureStartupIdentity(sPackage)
    shutil.rmtree(sPackage)
    dictIdentity = versionIdentity.fdictDescribeRunningVaibify()
    assert dictIdentity["bSourceChangedSinceStart"] is None
    assert dictIdentity["sMeaning"] != S_UNCHANGED
    assert "could not be hashed" in dictIdentity["sError"]


def testTheDigestIsTheDocumentedEncodingOfTheTree(tmp_path, monkeypatch):
    """Sorted entries of path, NUL, the file's SHA-256 and a newline."""
    _fnAnswerTheInstalledVersion(monkeypatch)
    sPackage = str(tmp_path / S_PACKAGE_NAME)
    dictSources = {"b.py": "B = 2\n", "a/z.py": "Z = 26\n"}
    for sRelative, sBody in dictSources.items():
        fnWriteText(sPackage, sRelative, sBody)
    fnWriteText(sPackage, "__pycache__/b.cpython-311.pyc", "bytecode")
    fnWriteText(sPackage, "a/stale.pyc", "bytecode")
    baExpected = b"".join(
        sRelative.encode() + b"\0"
        + hashlib.sha256(sBody.encode()).hexdigest().encode() + b"\n"
        for sRelative, sBody in sorted(dictSources.items()))
    dictIdentity = _fdictCaptureThenDescribe(sPackage)
    assert (dictIdentity["sSourceTreeSha256"]
            == hashlib.sha256(baExpected).hexdigest())


def testALinkOutOfTheTreeIsRecordedAsALinkAndNeverFollowed(
    tmp_path, monkeypatch,
):
    _fnAnswerTheInstalledVersion(monkeypatch)
    sPackage = _fsBuildLoosePackage(str(tmp_path / "site"))
    fnWriteText(str(tmp_path), "outside.py", "SECRET = 1\n")
    os.symlink(str(tmp_path / "outside.py"),
               os.path.join(sPackage, "linked.py"))
    versionIdentity.fnCaptureStartupIdentity(sPackage)
    fnWriteText(str(tmp_path), "outside.py", "SECRET = 2\n")
    assert versionIdentity.fdictDescribeRunningVaibify()[
        "bSourceChangedSinceStart"] is False


def testASecondCaptureChangesNothing(tmp_path, monkeypatch):
    _fnAnswerTheInstalledVersion(monkeypatch)
    sPackage = _fsBuildCheckout(tmp_path)
    versionIdentity.fnCaptureStartupIdentity(sPackage)
    versionIdentity.fnCaptureStartupIdentity(
        _fsBuildLoosePackage(str(tmp_path / "site")))
    dictIdentity = versionIdentity.fdictDescribeRunningVaibify()
    assert dictIdentity["sSource"] == "checkout"
    assert dictIdentity["sCommit"] == _fsHead(sPackage)


def testFirstUseCapturesWhenNothingDid(tmp_path, monkeypatch):
    sPackage = _fsBuildCheckout(tmp_path)
    monkeypatch.setattr(
        versionIdentity, "_fsImportedPackageDirectory", lambda: sPackage)
    dictIdentity = versionIdentity.fdictDescribeRunningVaibify()
    assert dictIdentity["sSource"] == "checkout"
    assert dictIdentity["sCommit"] == _fsHead(sPackage)
    assert dictIdentity["bSourceChangedSinceStart"] is False
