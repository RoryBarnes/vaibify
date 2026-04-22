"""Host-side Overleaf mirror for tree browsing, diffing, conflict detection.

A partial shallow clone of each Overleaf project is kept at
``~/.vaibify/overleaf-mirrors/<projectId>/``. The mirror is read-only
reference state consulted for:

- Listing existing remote directories and files (tree picker).
- Diffing a proposed push against the current remote state.
- Detecting conflicts against a per-file digest baseline recorded at
  the last successful push.

Mirror operations run in-process on the host via ``subprocess`` (never
inside the container). Tokens are fetched from the OS keyring through
an ephemeral askpass script shared with ``syncDispatcher``.

All host filesystem manipulation uses :mod:`os.path`. The sole use of
:mod:`posixpath` is for *remote* path joining (e.g. figures/foo.pdf)
where the remote side is always POSIX-flavoured regardless of host OS.

Overleaf behavior adapter
-------------------------

This module is the single quarantine point for Overleaf git-bridge
quirks. The rest of vaibify treats remote paths as ordinary
case-sensitive POSIX strings; only this module (and the fixture-based
tests in ``tests/testOverleafBehavior.py``) encodes the following
observed behaviors:

- Overleaf's git bridge is effectively case-insensitive: the same
  underlying storage blob may be surfaced under multiple case variants
  (``Figures/x.pdf`` and ``figures/x.pdf``). Pushing to the "wrong"
  case silently creates a phantom duplicate entry that both point at
  the same physical file on the Overleaf side.
- Manual edits made through the Overleaf web UI are committed with
  the exact message ``Update on Overleaf.`` and may touch several
  case-variants of the same path in one commit.
- We use partial clones (``--filter=blob:none``); blob contents are
  never fetched, only tree metadata. Diff logic therefore compares
  blob SHAs, never file content.

If Overleaf changes any of the above, the test fixtures in
``tests/testOverleafBehavior.py`` will fail with actionable signal,
and this module is the single place that needs editing.
"""

import hashlib
import os
import posixpath
import re
import shutil
import subprocess
from datetime import datetime, timezone

from vaibify.reproducibility.gitHardening import LIST_GIT_HARDENING_CONFIG
from vaibify.reproducibility.overleafAuth import (
    fnValidateOverleafProjectId,
    fsWriteAskpassScript,
)


__all__ = [
    "S_OVERLEAF_WEB_UI_COMMIT_MESSAGE",
    "fbRefreshMirror",
    "fdictDiffAgainstMirror",
    "fdictIndexMirrorBlobs",
    "flistDetectCaseCollisions",
    "flistDetectConflicts",
    "flistListMirrorTree",
    "fnDeleteMirror",
    "fsComputeBlobSha",
    "fsGetMirrorRoot",
    "fsReadMirrorHeadSha",
    "fsRedactStderr",
]


# ----------------------------------------------------------------------
# Overleaf-specific constants (behavior adapter values).
# These should only be referenced from within this module or from the
# fixture-based Overleaf behavior tests.
# ----------------------------------------------------------------------
S_OVERLEAF_WEB_UI_COMMIT_MESSAGE = "Update on Overleaf."


_S_OVERLEAF_HOST = "git.overleaf.com"
_I_MIRROR_DIR_MODE = 0o700

_REGEX_URL_WITH_CREDENTIALS = re.compile(
    r"https?://[^:@\s]+:[^@\s]+@",
)
_LIST_SENSITIVE_KEYWORDS = (
    "password", "token", "bearer", "authorization",
)

def fsGetMirrorRoot():
    """Return the root directory that holds every project mirror."""
    return os.path.expanduser(os.path.join("~", ".vaibify",
                                           "overleaf-mirrors"))


def fsRedactStderr(sStderr):
    """Return sStderr with URL credentials and secret-bearing lines redacted.

    The goal is best-effort leak prevention for error messages that bubble
    up to the GUI: git can emit the remote URL verbatim (including an
    embedded ``user:token@`` segment when libcurl echoes the full URL),
    and some git error paths print credential-helper output. Neither
    should ever reach a user-visible toast.
    """
    if not sStderr:
        return ""
    sRedacted = _REGEX_URL_WITH_CREDENTIALS.sub(
        "https://<redacted>@", sStderr,
    )
    listLines = [
        _fsRedactLineIfSensitive(sLine)
        for sLine in sRedacted.splitlines()
    ]
    return "\n".join(listLines)


def _fsRedactLineIfSensitive(sLine):
    """Return ``<redacted>`` when a line names a credential concept."""
    sLower = sLine.lower()
    for sKeyword in _LIST_SENSITIVE_KEYWORDS:
        if sKeyword in sLower:
            return "<redacted>"
    return sLine


def _fsMirrorPath(sProjectId):
    """Return the per-project mirror directory for sProjectId."""
    fnValidateOverleafProjectId(sProjectId)
    return os.path.join(fsGetMirrorRoot(), sProjectId)


def _fnEnsureMirrorRoot():
    """Create the mirror root directory with mode 0700."""
    sRoot = fsGetMirrorRoot()
    os.makedirs(sRoot, exist_ok=True)
    try:
        os.chmod(sRoot, _I_MIRROR_DIR_MODE)
    except OSError:
        pass


def _fbMirrorExists(sProjectId):
    """Return True when the per-project mirror dir has a .git subdir."""
    sMirror = _fsMirrorPath(sProjectId)
    return os.path.isdir(os.path.join(sMirror, ".git"))


def _fdictBaseGitEnv():
    """Return an env dict with GIT_TERMINAL_PROMPT disabled."""
    dictEnv = os.environ.copy()
    dictEnv["GIT_TERMINAL_PROMPT"] = "0"
    return dictEnv


def _fdictBuildGitEnv(sAskpassPath):
    """Build an env dict that routes credentials via the askpass helper."""
    dictEnv = _fdictBaseGitEnv()
    dictEnv["GIT_ASKPASS"] = sAskpassPath
    return dictEnv


def _fnRunGit(listArgs, sCwd=None, dictEnv=None):
    """Run a git command and return a CompletedProcess; never raises.

    Always sets ``GIT_TERMINAL_PROMPT=0`` so a misconfigured git
    credential helper cannot hang the server thread waiting for input.
    When ``sCwd`` points at a missing directory, subprocess.run would
    normally raise ``FileNotFoundError`` before even executing git;
    this helper traps that and returns a synthetic non-zero result so
    callers can treat it uniformly alongside genuine git failures.
    """
    if dictEnv is None:
        dictEnv = _fdictBaseGitEnv()
    try:
        return subprocess.run(
            ["git"] + listArgs,
            cwd=sCwd, env=dictEnv,
            capture_output=True, text=True,
        )
    except FileNotFoundError as error:
        return subprocess.CompletedProcess(
            args=["git"] + listArgs,
            returncode=127,
            stdout="",
            stderr=str(error),
        )


def _fsStrippedStderr(result):
    """Return the stripped, redacted stderr of a CompletedProcess."""
    return fsRedactStderr((result.stderr or "").strip())


def _fnClonePartial(sProjectId, sAskpassPath):
    """First-time partial clone of an Overleaf project into the mirror dir."""
    _fnEnsureMirrorRoot()
    sMirror = _fsMirrorPath(sProjectId)
    sUrl = f"https://{_S_OVERLEAF_HOST}/{sProjectId}"
    dictEnv = _fdictBuildGitEnv(sAskpassPath)
    listArgs = list(LIST_GIT_HARDENING_CONFIG) + [
        "clone", "--filter=blob:none", "--no-checkout",
        "--no-recurse-submodules", sUrl, sMirror,
    ]
    result = _fnRunGit(listArgs, dictEnv=dictEnv)
    if result.returncode != 0:
        raise RuntimeError(
            f"Mirror clone failed: {_fsStrippedStderr(result)}"
        )


def _fnFetchAndReset(sProjectId, sAskpassPath):
    """Refresh an existing mirror with fetch + reset to origin/HEAD."""
    dictEnv = _fdictBuildGitEnv(sAskpassPath)
    _fnFetchOrigin(sProjectId, dictEnv)
    _fnResetToOriginHead(sProjectId, dictEnv)


def _fnFetchOrigin(sProjectId, dictEnv):
    """Run git fetch with blob filtering against the mirror remote."""
    sMirror = _fsMirrorPath(sProjectId)
    listArgs = list(LIST_GIT_HARDENING_CONFIG) + [
        "fetch", "--filter=blob:none",
        "--no-recurse-submodules", "origin",
    ]
    result = _fnRunGit(listArgs, sCwd=sMirror, dictEnv=dictEnv)
    if result.returncode != 0:
        raise RuntimeError(
            f"Mirror fetch failed: {_fsStrippedStderr(result)}"
        )


def _fnResetToOriginHead(sProjectId, dictEnv):
    """Hard-reset the mirror working tree to origin/HEAD."""
    sMirror = _fsMirrorPath(sProjectId)
    listArgs = list(LIST_GIT_HARDENING_CONFIG) + [
        "reset", "--hard", "origin/HEAD",
    ]
    result = _fnRunGit(listArgs, sCwd=sMirror, dictEnv=dictEnv)
    if result.returncode != 0:
        raise RuntimeError(
            f"Mirror reset failed: {_fsStrippedStderr(result)}"
        )


def fsReadMirrorHeadSha(sProjectId):
    """Return the current HEAD commit SHA of the mirror.

    Returns an empty string when the mirror is missing or git fails.
    """
    fnValidateOverleafProjectId(sProjectId)
    if not _fbMirrorExists(sProjectId):
        return ""
    sMirror = _fsMirrorPath(sProjectId)
    result = _fnRunGit(
        ["rev-parse", "HEAD"], sCwd=sMirror,
    )
    if result.returncode != 0:
        return ""
    return (result.stdout or "").strip()


def _fnCountMirrorFiles(sProjectId):
    """Return the number of blob entries currently tracked in the mirror."""
    listEntries = flistListMirrorTree(sProjectId)
    return sum(1 for d in listEntries if d["sType"] == "blob")


def fbRefreshMirror(sProjectId, sToken):
    """Create or refresh a partial clone mirror of an Overleaf project.

    Returns ``{"sHeadSha", "iFileCount", "sRefreshedAt"}`` on success.
    Raises ``RuntimeError`` with a classified message on failure.
    """
    fnValidateOverleafProjectId(sProjectId)
    sAskpass = fsWriteAskpassScript()
    try:
        _fnSyncMirror(sProjectId, sAskpass)
    finally:
        _fnRemovePath(sAskpass)
    return _fdictBuildRefreshSummary(sProjectId)


def _fnSyncMirror(sProjectId, sAskpass):
    """Dispatch to clone or fetch+reset depending on mirror presence."""
    if _fbMirrorExists(sProjectId):
        _fnFetchAndReset(sProjectId, sAskpass)
    else:
        _fnClonePartial(sProjectId, sAskpass)


def _fdictBuildRefreshSummary(sProjectId):
    """Build the success payload returned by fbRefreshMirror."""
    return {
        "sHeadSha": fsReadMirrorHeadSha(sProjectId),
        "iFileCount": _fnCountMirrorFiles(sProjectId),
        "sRefreshedAt": _fsIsoTimestampNow(),
    }


def _fsIsoTimestampNow():
    """Return the current UTC time formatted as an ISO-8601 Z string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fnRemovePath(sPath):
    """Delete a file, tolerating missing paths."""
    try:
        os.remove(sPath)
    except FileNotFoundError:
        pass


def _fdictParseLsTreeLine(sLine):
    """Parse one ``git ls-tree -r --long`` line, returning None on malformed."""
    sTabSplit = sLine.split("\t", 1)
    if len(sTabSplit) != 2:
        return None
    listHeader = sTabSplit[0].split()
    if len(listHeader) < 4:
        return None
    return {
        "sPath": sTabSplit[1],
        "sType": listHeader[1],
        "iSize": _fiParseLsTreeSize(listHeader[3]),
        "sDigest": listHeader[2],
    }


def _fiParseLsTreeSize(sSize):
    """Return the integer size for an ls-tree size field; 0 on anomaly."""
    if sSize == "-":
        return 0
    try:
        return int(sSize)
    except ValueError:
        return 0


def flistListMirrorTree(sProjectId):
    """Return ls-tree entries for the mirror as a list of dicts.

    Each entry has keys ``sPath``, ``sType`` ("blob" or "tree"),
    ``iSize``, ``sDigest``. Returns an empty list when the mirror
    does not exist. Malformed lines are silently skipped.
    """
    fnValidateOverleafProjectId(sProjectId)
    if not _fbMirrorExists(sProjectId):
        return []
    sMirror = _fsMirrorPath(sProjectId)
    result = _fnRunGit(
        ["ls-tree", "-r", "--long", "HEAD"], sCwd=sMirror,
    )
    if result.returncode != 0:
        return []
    return _flistParseLsTreeOutput(result.stdout or "")


def _flistParseLsTreeOutput(sOutput):
    """Parse every non-empty ls-tree line and drop malformed rows."""
    listEntries = []
    for sLine in sOutput.splitlines():
        if not sLine.strip():
            continue
        dictEntry = _fdictParseLsTreeLine(sLine)
        if dictEntry is not None:
            listEntries.append(dictEntry)
    return listEntries


def fsComputeBlobSha(sPath):
    """Return the git-compatible blob SHA for the file at sPath.

    Matches ``git hash-object <file>``:
    ``sha1("blob " + str(size) + "\\0" + content)``.
    """
    with open(sPath, "rb") as handleFile:
        baContent = handleFile.read()
    iSize = len(baContent)
    sHeader = f"blob {iSize}\x00"
    hasher = hashlib.sha1()
    hasher.update(sHeader.encode("utf-8"))
    hasher.update(baContent)
    return hasher.hexdigest()


def fdictIndexMirrorBlobs(sProjectId):
    """Return a dict mapping remote path -> digest for blobs in the mirror."""
    dictBlobs = {}
    for dictEntry in flistListMirrorTree(sProjectId):
        if dictEntry["sType"] == "blob":
            dictBlobs[dictEntry["sPath"]] = dictEntry["sDigest"]
    return dictBlobs


def _fsRemotePathFor(sLocalPath, sTargetDirectory):
    """Map a local file path to its intended remote mirror path."""
    sBasename = os.path.basename(sLocalPath)
    if not sTargetDirectory:
        return sBasename
    return posixpath.join(sTargetDirectory, sBasename)


def fdictDiffAgainstMirror(
    sProjectId, dictLocalDigests, sTargetDirectory,
):
    """Classify each local file against the current mirror tree.

    ``dictLocalDigests`` maps local absolute path -> blob SHA.
    Returns ``{"listNew", "listOverwrite", "listUnchanged"}`` where
    each list item contains ``sLocalPath``, ``sRemotePath``,
    ``sLocalDigest`` and (for overwrite/unchanged) ``sRemoteDigest``.
    """
    dictRemoteBlobs = fdictIndexMirrorBlobs(sProjectId)
    dictResult = {"listNew": [], "listOverwrite": [], "listUnchanged": []}
    for sLocalPath, sLocalDigest in dictLocalDigests.items():
        sRemotePath = _fsRemotePathFor(sLocalPath, sTargetDirectory)
        _fnClassifyOne(
            sLocalPath, sLocalDigest, sRemotePath,
            dictRemoteBlobs, dictResult,
        )
    return dictResult


def _fnClassifyOne(
    sLocalPath, sLocalDigest, sRemotePath, dictRemoteBlobs, dictResult,
):
    """Append the local file to new/overwrite/unchanged in dictResult."""
    dictEntry = {
        "sLocalPath": sLocalPath,
        "sRemotePath": sRemotePath,
        "sLocalDigest": sLocalDigest,
    }
    if sRemotePath not in dictRemoteBlobs:
        dictResult["listNew"].append(dictEntry)
        return
    sRemoteDigest = dictRemoteBlobs[sRemotePath]
    dictEntry["sRemoteDigest"] = sRemoteDigest
    if sRemoteDigest == sLocalDigest:
        dictResult["listUnchanged"].append(dictEntry)
    else:
        dictResult["listOverwrite"].append(dictEntry)


def flistDetectConflicts(
    sProjectId, listLocalAbsPaths, sTargetDirectory, dictSyncStatus,
):
    """Return files whose remote digest diverges from our last-push baseline.

    A conflict means: the file exists in the current mirror AND the
    ``sOverleafLastPushedDigest`` recorded in ``dictSyncStatus`` differs
    from the current remote digest. Missing baselines are treated as
    "no conflict" — the first push under this feature establishes the
    baseline silently.
    """
    dictRemoteBlobs = fdictIndexMirrorBlobs(sProjectId)
    listConflicts = []
    for sLocalPath in listLocalAbsPaths:
        _fnAppendConflictIfAny(
            sLocalPath, sTargetDirectory, dictRemoteBlobs,
            dictSyncStatus, listConflicts,
        )
    return listConflicts


def _fnAppendConflictIfAny(
    sLocalPath, sTargetDirectory, dictRemoteBlobs,
    dictSyncStatus, listConflicts,
):
    """Append one conflict dict to listConflicts when baseline disagrees."""
    sRemotePath = _fsRemotePathFor(sLocalPath, sTargetDirectory)
    sCurrentRemote = dictRemoteBlobs.get(sRemotePath, "")
    sBaseline = _fsGetBaselineDigest(dictSyncStatus, sLocalPath)
    if _fbIsConflict(sCurrentRemote, sBaseline):
        listConflicts.append({
            "sLocalPath": sLocalPath,
            "sRemotePath": sRemotePath,
            "sBaselineDigest": sBaseline,
            "sCurrentDigest": sCurrentRemote,
        })


def _fsGetBaselineDigest(dictSyncStatus, sLocalPath):
    """Return the last-pushed digest for sLocalPath from sync status."""
    dictEntry = dictSyncStatus.get(sLocalPath) or {}
    return dictEntry.get("sOverleafLastPushedDigest", "")


def _fbIsConflict(sCurrentRemote, sBaseline):
    """Return True when baseline and current remote disagree."""
    if not sBaseline:
        return False
    if not sCurrentRemote:
        return False
    return sBaseline != sCurrentRemote


def _fdictLowercaseRemoteIndex(sProjectId):
    """Map lowercased remote path -> original-case remote path.

    Encodes the Overleaf adapter rule: the git bridge surfaces paths
    whose case may differ from the underlying storage canonical case.
    The first occurrence wins; later duplicates (same lowercased key,
    different original case) are ignored because Overleaf's own
    storage treats them as a single file regardless.
    """
    dictLowerToOriginal = {}
    for dictEntry in flistListMirrorTree(sProjectId):
        if dictEntry["sType"] != "blob":
            continue
        sOriginal = dictEntry["sPath"]
        sLower = sOriginal.lower()
        if sLower not in dictLowerToOriginal:
            dictLowerToOriginal[sLower] = sOriginal
    return dictLowerToOriginal


def _fdictBuildCaseCollision(sLocalPath, sTypedRemote, sCanonicalRemote):
    """Return one case-collision record."""
    return {
        "sLocalPath": sLocalPath,
        "sTypedRemotePath": sTypedRemote,
        "sCanonicalRemotePath": sCanonicalRemote,
    }


def flistDetectCaseCollisions(
    sProjectId, listLocalAbsPaths, sTargetDirectory,
):
    """Return per-file case-collision records for a proposed push.

    A collision is: the user's intended remote path lowercases to the
    same value as an existing mirror blob whose original-case path
    differs from the intended path. This catches the Overleaf
    ``Figures/`` vs ``figures/`` phantom-entry trap. Callers should
    suggest the ``sCanonicalRemotePath`` case (mirror's existing form)
    to avoid creating duplicates.
    """
    fnValidateOverleafProjectId(sProjectId)
    dictLowerToOriginal = _fdictLowercaseRemoteIndex(sProjectId)
    listCollisions = []
    for sLocalPath in listLocalAbsPaths:
        sTypedRemote = _fsRemotePathFor(sLocalPath, sTargetDirectory)
        sOriginal = dictLowerToOriginal.get(sTypedRemote.lower())
        if sOriginal is None:
            continue
        if sOriginal == sTypedRemote:
            continue
        listCollisions.append(_fdictBuildCaseCollision(
            sLocalPath, sTypedRemote, sOriginal,
        ))
    return listCollisions


def fnDeleteMirror(sProjectId):
    """Remove the mirror directory for sProjectId.

    Idempotent: no error when the mirror does not exist.
    """
    fnValidateOverleafProjectId(sProjectId)
    sMirror = _fsMirrorPath(sProjectId)
    shutil.rmtree(sMirror, ignore_errors=True)


# ----------------------------------------------------------------------
# Backwards-compatible private aliases.
# External callers that reached into the old underscore-prefixed names
# (``_fsReadMirrorHeadSha``, ``_fdictIndexMirrorBlobs``, ``_fsComputeBlobSha``)
# continue to resolve. New call sites should use the public names.
# ----------------------------------------------------------------------
_fsReadMirrorHeadSha = fsReadMirrorHeadSha
_fdictIndexMirrorBlobs = fdictIndexMirrorBlobs
_fsComputeBlobSha = fsComputeBlobSha
