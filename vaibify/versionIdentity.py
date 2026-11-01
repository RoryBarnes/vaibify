"""A truthful identity for the running vaibify.

``setuptools_scm`` writes ``vaibify/_version.py`` when vaibify is
installed and never again, so a checkout that has moved on keeps
reporting the version it had at install time. Asking git at the moment
of use is not enough either: a hub keeps running while its checkout
moves or gains uncommitted edits, and Python imports lazily, so a module
first imported an hour after start comes from whatever its file says
then.

So the identity has two halves:

* **At start**, :func:`fnCaptureStartupIdentity` records where the code
  came from -- a git checkout (its describe, its commit, whether it was
  modified) or the installed metadata -- and one SHA-256 over every
  source file of the package.
* **At use**, :func:`fdictDescribeRunningVaibify` recomputes that
  digest. When it differs, the running code may be a mixture of modules
  imported before and after the change, which no commit or digest names
  exactly, and the answer says so instead of naming one.

The DIGEST decides "changed", never the commit: a tree goes dirty with
HEAD unchanged.

Nothing here invents a version. A question that could not be answered
is left empty, and ``sError`` says why in words that name no path,
because the identity is written into attestations that are committed
and published. Every git query carries both ``gitHardening`` lists, a
short timeout, and an environment that selects no repository but the
checkout's own.
"""

import hashlib
import os
import stat
import subprocess
import threading

import vaibify
from vaibify.reproducibility.gitHardening import (
    LIST_GIT_CREDENTIAL_ISOLATION_CONFIG,
    LIST_GIT_HARDENING_CONFIG,
    fdictScrubRepositorySelection,
)


__all__ = [
    "F_GIT_TIMEOUT_SECONDS",
    "S_MEANING_CHANGED",
    "S_MEANING_UNCHANGED",
    "S_MEANING_UNKNOWN",
    "S_SOURCE_CHECKOUT",
    "S_SOURCE_INSTALLED",
    "fdictDescribeRunningVaibify",
    "fnCaptureStartupIdentity",
    "fnForgetStartupIdentity",
]


S_SOURCE_CHECKOUT = "checkout"
S_SOURCE_INSTALLED = "installed"
S_MEANING_UNCHANGED = "the code this process started with"
S_MEANING_CHANGED = (
    "the source changed since this process started, so the running code "
    "may be a mixture that no commit or digest names exactly")
S_MEANING_UNKNOWN = (
    "whether the source changed since this process started is unknown, "
    "because its digest could not be computed")
F_GIT_TIMEOUT_SECONDS = 5.0

# The tag selection setuptools_scm uses here (pyproject.toml:
# ``--tags --long --match v*``), so the two read the same tag. ``--long``
# names the commit even on a tag (``v0.3.0-0-g1a2b3c4d``), ``--always``
# falls back to the bare abbreviated commit where no ``v*`` tag is
# reachable (a shallow clone), and ``--dirty`` marks tracked changes.
_T_DESCRIBE_ARGUMENTS = (
    "describe", "--tags", "--long", "--always", "--dirty", "--match", "v*",
)
_S_DIRTY_SUFFIX = "-dirty"
_I_READ_CHUNK_BYTES = 1 << 20

_LOCK_STARTUP = threading.Lock()
_DICT_STARTUP = {"sPackageDirectory": "", "dictIdentity": None}


def fnCaptureStartupIdentity(sPackageDirectory=None):
    """Record this process's starting identity once; later calls do nothing.

    It runs as early as each entry point allows -- the CLI calls it
    before importing its commands, and first use is the fallback --
    which is after the imports that reach it. It is not a claim about
    the instant the interpreter started. ``sPackageDirectory`` defaults
    to the imported vaibify package; tests pass a throwaway one. Never
    raises: an identity that cannot be read must not stop the hub or the
    CLI from starting.
    """
    _ftStartupIdentity(sPackageDirectory)


def fnForgetStartupIdentity():
    """Forget the captured identity, so the next capture runs again.

    For tests only. A process starts once and production never forgets
    its start; a test that captures a throwaway package forgets before
    and after, so it neither inherits nor leaves behind a capture.
    """
    with _LOCK_STARTUP:
        _DICT_STARTUP.update(sPackageDirectory="", dictIdentity=None)


def fdictDescribeRunningVaibify():
    """Return the running vaibify's identity, checked against its source now.

    The startup keys -- ``sSource``, ``sDescribe``, ``sCommit``,
    ``bModified`` and ``sSourceTreeSha256`` -- plus
    ``bSourceChangedSinceStart`` and ``sMeaning``. When the source
    changed, also ``sCurrentSourceTreeSha256``, and ``sCurrentCommit``
    when a checkout's HEAD moved. ``sError`` appears only when something
    could not be read. The digest is recomputed on every call; it alone
    decides "changed". Captures the startup identity first if nothing
    has (first use is the fallback).
    """
    sPackageDirectory, dictStartup = _ftStartupIdentity()
    dictIdentity = dict(dictStartup)
    listErrors = [dictIdentity.pop("sError")] if "sError" in dictIdentity else []
    sCurrentSha256 = _fsSourceTreeSha256OrEmpty(sPackageDirectory, listErrors)
    dictIdentity.update(_fdictCompareWithStart(
        dictIdentity, sCurrentSha256, sPackageDirectory, listErrors))
    return _fdictWithErrors(dictIdentity, listErrors)


def _ftStartupIdentity(sPackageDirectory=None):
    """Return ``(sPackageDirectory, dictIdentity)``, capturing them once."""
    with _LOCK_STARTUP:
        if _DICT_STARTUP["dictIdentity"] is None:
            sDirectory = os.path.realpath(
                sPackageDirectory or _fsImportedPackageDirectory())
            _DICT_STARTUP.update(
                sPackageDirectory=sDirectory,
                dictIdentity=_fdictCaptureIdentitySafely(sDirectory))
        return _DICT_STARTUP["sPackageDirectory"], _DICT_STARTUP["dictIdentity"]


def _fsImportedPackageDirectory():
    """Return the directory of the vaibify package this process imported."""
    return os.path.dirname(vaibify.__file__)


def _fdictCaptureIdentitySafely(sPackageDirectory):
    """Return the startup identity, or a recorded failure; never raises.

    The expected failures are answered where they occur. This catches
    whatever was not expected, because the capture runs at the start of
    the hub and of every CLI command.
    """
    try:
        return _fdictCaptureIdentity(sPackageDirectory)
    except Exception as error:  # noqa: BLE001 -- recorded, see docstring
        return {
            "sSource": "", "sDescribe": "", "sCommit": "", "bModified": None,
            "sSourceTreeSha256": "",
            "sError": f"the identity could not be captured "
                      f"({type(error).__name__})",
        }


def _fdictCaptureIdentity(sPackageDirectory):
    """Return the startup identity of the package at ``sPackageDirectory``."""
    listErrors = []
    dictIdentity = (
        _fdictCheckoutIdentityOrNone(sPackageDirectory, listErrors)
        or _fdictInstalledIdentity(listErrors))
    dictIdentity["sSourceTreeSha256"] = _fsSourceTreeSha256OrEmpty(
        sPackageDirectory, listErrors)
    return _fdictWithErrors(dictIdentity, listErrors)


def _fdictWithErrors(dictIdentity, listErrors):
    """Return ``dictIdentity`` with ``sError`` set when anything failed."""
    if listErrors:
        dictIdentity["sError"] = "; ".join(dict.fromkeys(listErrors))
    return dictIdentity


def _fdictCheckoutIdentityOrNone(sPackageDirectory, listErrors):
    """Return what git says about the checkout, or None when there is none.

    The package comes from a checkout when its parent directory is the
    root of a work tree, i.e. holds ``.git``. A package installed INSIDE
    some other repository -- a virtual environment within a project --
    is not vaibify's checkout, and asking git from there would describe
    the wrong repository. A checkout git cannot read falls back to the
    installed version, and the reason is recorded.
    """
    sWorkTree = os.path.dirname(sPackageDirectory)
    if not os.path.lexists(os.path.join(sWorkTree, ".git")):
        return None
    dictAnswers = {}
    for sKey, listArguments in (
        ("sDescribe", list(_T_DESCRIBE_ARGUMENTS)),
        ("sCommit", ["rev-parse", "--verify", "HEAD"]),
        ("sUntracked", ["ls-files", "--others", "--exclude-standard", "--",
                        os.path.basename(sPackageDirectory)]),
    ):
        dictAnswers[sKey], sFailure = _ftRunGit(listArguments, sWorkTree)
        if sFailure:
            listErrors.append(
                f"git could not read the checkout ({sFailure}), so the "
                "installed version is reported")
            return None
    return _fdictCheckoutIdentityFromAnswers(dictAnswers)


def _fdictCheckoutIdentityFromAnswers(dictAnswers):
    """Return the checkout identity from git's three answers.

    Modified means a tracked file differs from HEAD (describe's own
    ``-dirty``) or an untracked, unignored file sits in the package,
    since Python imports a module whether or not git tracks it.
    """
    sDescribe = dictAnswers["sDescribe"]
    return {
        "sSource": S_SOURCE_CHECKOUT,
        "sDescribe": sDescribe,
        "sCommit": dictAnswers["sCommit"],
        "bModified": (
            sDescribe.endswith(_S_DIRTY_SUFFIX)
            or bool(dictAnswers["sUntracked"])),
    }


def _fdictInstalledIdentity(listErrors):
    """Return the identity setuptools_scm recorded when vaibify was installed.

    ``bModified`` is None: an installed copy cannot say whether it was
    edited afterward, and unknown is not clean. ``sCommit`` is the commit
    id exactly as setuptools_scm wrote it (abbreviated, with its ``g``),
    or empty where it wrote none.
    """
    sVersion, sCommit = _ftReadInstalledVersion()
    if not sVersion:
        listErrors.append("the installed version could not be read")
    return {
        "sSource": S_SOURCE_INSTALLED, "sDescribe": sVersion,
        "sCommit": sCommit, "bModified": None,
    }


def _ftReadInstalledVersion():
    """Return ``(sVersion, sCommitId)`` as setuptools_scm wrote them, or empty.

    Both are empty when no ``_version`` module was written -- a checkout
    nobody installed -- and the version is empty when the module names
    none.
    """
    try:
        from vaibify import _version as moduleVersion
    except ImportError:
        return "", ""
    return (
        str(getattr(moduleVersion, "__version__", None) or ""),
        str(getattr(moduleVersion, "__commit_id__", None) or ""),
    )


def _fdictCompareWithStart(
    dictIdentity, sCurrentSha256, sPackageDirectory, listErrors,
):
    """Return the keys that say whether the source changed since start."""
    sStartSha256 = dictIdentity["sSourceTreeSha256"]
    if not sStartSha256 or not sCurrentSha256:
        return {"bSourceChangedSinceStart": None, "sMeaning": S_MEANING_UNKNOWN}
    if sCurrentSha256 == sStartSha256:
        return {
            "bSourceChangedSinceStart": False, "sMeaning": S_MEANING_UNCHANGED,
        }
    dictChanged = {
        "bSourceChangedSinceStart": True, "sMeaning": S_MEANING_CHANGED,
        "sCurrentSourceTreeSha256": sCurrentSha256,
    }
    dictChanged.update(_fdictCurrentCommitIfMoved(
        dictIdentity, sPackageDirectory, listErrors))
    return dictChanged


def _fdictCurrentCommitIfMoved(dictIdentity, sPackageDirectory, listErrors):
    """Return ``{"sCurrentCommit": ...}`` when a checkout's HEAD moved, else {}.

    Only a checkout is asked: an installed copy has no HEAD to move.
    """
    if dictIdentity["sSource"] != S_SOURCE_CHECKOUT:
        return {}
    sCommit, sFailure = _ftRunGit(
        ["rev-parse", "--verify", "HEAD"], os.path.dirname(sPackageDirectory))
    if sFailure:
        listErrors.append(f"the current commit could not be read ({sFailure})")
        return {}
    if sCommit == dictIdentity["sCommit"]:
        return {}
    return {"sCurrentCommit": sCommit}


def _ftRunGit(listArguments, sWorkTree):
    """Return ``(sStdout, sFailure)`` for one hardened git query.

    ``sFailure`` is empty when git answered with status 0. Otherwise it
    names the question and what went wrong, never git's own stderr,
    which can carry a path.
    """
    sQuestion = "git " + listArguments[0]
    try:
        processGit = subprocess.run(
            ["git", *LIST_GIT_CREDENTIAL_ISOLATION_CONFIG,
             *LIST_GIT_HARDENING_CONFIG, *listArguments],
            cwd=sWorkTree, env=_fdictGitEnvironment(),
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=F_GIT_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        return "", "git is not available on this host"
    except subprocess.TimeoutExpired:
        return "", (
            f"{sQuestion} did not answer within "
            f"{F_GIT_TIMEOUT_SECONDS:g} seconds")
    except (OSError, subprocess.SubprocessError) as error:
        return "", f"{sQuestion} could not run ({type(error).__name__})"
    if processGit.returncode != 0:
        return "", f"{sQuestion} exited with status {processGit.returncode}"
    return processGit.stdout.strip(), ""


def _fdictGitEnvironment():
    """Return the environment of a git query: its own checkout, no prompt.

    The repository-selecting variables are scrubbed so a vaibify started
    from a git hook describes its own checkout, not the hook's
    repository. ``GIT_OPTIONAL_LOCKS=0`` keeps the query from rewriting
    the index of a checkout the researcher may be working in, and
    ``GIT_LITERAL_PATHSPECS=1`` reads the package directory's name as a
    name, never as a pattern.
    """
    dictEnvironment = fdictScrubRepositorySelection(os.environ)
    dictEnvironment.update(
        GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0",
        GIT_LITERAL_PATHSPECS="1",
    )
    return dictEnvironment


def _fsSourceTreeSha256OrEmpty(sPackageDirectory, listErrors):
    """Return the source-tree digest, or "" with the reason recorded."""
    try:
        return _fsComputeSourceTreeSha256(sPackageDirectory)
    except OSError as error:
        listErrors.append(
            f"the source tree could not be hashed ({type(error).__name__})")
        return ""


def _fsComputeSourceTreeSha256(sPackageDirectory):
    """Return one SHA-256 over every source file under ``sPackageDirectory``.

    Each entry is the file's path relative to the directory in POSIX
    form, a NUL, the entry's digest field, and a newline, in the order
    of the path bytes. A path cannot hold a NUL and a digest field holds
    no newline, so the encoding is unambiguous. ``__pycache__``
    directories and ``*.pyc`` files are skipped: Python writes them on
    import, and they are not source.
    """
    hasherTree = hashlib.sha256()
    for baPath, sDigestField in _flistSourceTreeEntries(sPackageDirectory):
        hasherTree.update(
            baPath + b"\0" + sDigestField.encode("ascii") + b"\n")
    return hasherTree.hexdigest()


def _flistSourceTreeEntries(sPackageDirectory):
    """Return the sorted ``(baRelativePath, sDigestField)`` tree entries.

    The walk never follows a link, so a symlinked directory is one
    entry rather than a subtree, and an unreadable directory raises
    instead of silently leaving its files out.
    """
    listEntries = []
    for sDirectory, listDirectoryNames, listFileNames in os.walk(
        sPackageDirectory, onerror=_fnRaiseWalkError,
    ):
        listDirectoryNames[:] = [
            sName for sName in listDirectoryNames if sName != "__pycache__"]
        for sName in listDirectoryNames + listFileNames:
            sPath = os.path.join(sDirectory, sName)
            sDigestField = _fsDigestFieldOrEmpty(sPath)
            if sDigestField:
                listEntries.append(
                    (_fbaRelativePosixPath(sPath, sPackageDirectory),
                     sDigestField))
    return sorted(listEntries)


def _fnRaiseWalkError(error):
    """Raise the error ``os.walk`` would otherwise ignore."""
    raise error


def _fsDigestFieldOrEmpty(sPath):
    """Return one entry's digest field, or "" for an entry that is skipped.

    A regular file's field is its SHA-256. A symbolic link is recorded
    as a link and never followed: its field is ``symlink:`` and the
    SHA-256 of its target text, so a link pointing out of the tree pulls
    no foreign bytes into the digest, while retargeting it still changes
    the digest. A directory has no field of its own, and anything else
    (a pipe, a socket) is skipped, since reading a pipe would block.
    """
    if sPath.endswith(".pyc"):
        return ""
    iMode = os.lstat(sPath).st_mode
    if stat.S_ISLNK(iMode):
        return "symlink:" + hashlib.sha256(
            os.fsencode(os.readlink(sPath))).hexdigest()
    if stat.S_ISREG(iMode):
        return _fsHashFileWithoutFollowing(sPath)
    return ""


def _fsHashFileWithoutFollowing(sPath):
    """Return a regular file's SHA-256, refusing a link swapped in since."""
    hasherFile = hashlib.sha256()
    iDescriptor = os.open(sPath, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(iDescriptor, "rb") as fileSource:
        for baChunk in iter(lambda: fileSource.read(_I_READ_CHUNK_BYTES), b""):
            hasherFile.update(baChunk)
    return hasherFile.hexdigest()


def _fbaRelativePosixPath(sPath, sRoot):
    """Return ``sPath`` relative to ``sRoot``, in POSIX form, as bytes."""
    return os.fsencode(os.path.relpath(sPath, sRoot).replace(os.sep, "/"))
