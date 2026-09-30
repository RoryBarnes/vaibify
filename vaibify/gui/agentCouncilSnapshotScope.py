"""What a council snapshot copies: the whole directory, or git-tracked only.

A council snapshot used to have one scope — the whole project directory,
git-ignored files included (ruling 2026-08-24). A project that keeps
sweep output inside its repository could then never fit the bounds,
however small its code: hundreds of tracked files beside hundreds of
thousands of ignored ones. The second scope, ``gitTracked``, copies the
eligible files git tracks, AS THEY ARE IN THE WORKING TREE (uncommitted
edits included), and records exactly what it leaves out.

The scope is a campaign fact, ``{"sScope", "iScopeVersion"}``. It is
chosen at convene, recorded in the campaign and the manifest, and passed
into EVERY identity read that follows — the capture's pre and post
observations, the dirty-state digest, and the staleness poll — so churn
in omitted output can never tear a capture or mark a council stale. A
campaign that predates scopes reads as ``wholeDirectory``; a scope with
an unknown version REFUSES rather than being guessed at.

The git-tracked set (plan contract B1), per path, from the declared
``gitTrackedIdentities`` read:

* present tracked file or symlink (modified or not, a staged addition,
  a skip-worktree file that IS present, a tracked file matching an
  ignore pattern) — included, identity = the worktree bytes' blob sha;
* tracked but deleted in the worktree — omitted, "deleted in worktree";
* skip-worktree and absent — omitted, "not checked out (skip-worktree)";
* under a mandatory component exclusion — omitted with that reason;
* a merge conflict (any stage other than 0), a gitlink/submodule, a
  tracked path that is now a directory or special file, or a tracked
  path that lies BEYOND a symbolic link (a parent directory replaced by
  a link, which would otherwise read files from wherever it points) —
  REFUSED, naming the paths.

Untracked and ignored files are omitted with those reasons. The full
omission list stays HOST-SIDE, in an owner-only compressed inventory
named by an observation id; participants get a bounded summary only,
because file names can be sensitive. Say "untracked", never "not yet
committed": an untracked file may never be meant for git at all.
"""

import gzip
import hashlib
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone

__all__ = [
    "S_SCOPE_WHOLE_DIRECTORY",
    "S_SCOPE_GIT_TRACKED",
    "I_SCOPE_VERSION",
    "SET_SCOPES",
    "S_REASON_UNTRACKED",
    "S_REASON_IGNORED",
    "S_REASON_DELETED_IN_WORKTREE",
    "S_REASON_NOT_CHECKED_OUT",
    "S_REASON_POLICY_EXCLUDED",
    "DICT_REASON_LABELS",
    "F_INVENTORY_LIFETIME_SECONDS",
    "I_DEFAULT_PAGE_SIZE",
    "I_MAX_PAGE_SIZE",
    "I_MAX_SUMMARY_GROUPS",
    "SnapshotScopeError",
    "OmissionInventoryExpiredError",
    "fdictNormaliseSnapshotScope",
    "fdictComposeSnapshotScope",
    "fdictInterpretTrackedIndex",
    "fdictObserveTrackedScope",
    "fdictObserveForStaleness",
    "fdictSummariseOmissions",
    "fsWriteOmissionInventory",
    "fdictProbeTrackedScope",
    "fdictReadOmissionPage",
    "fsResolveSnapshotScopeDirectory",
    "fdictReadRememberedScope",
    "fnRememberScope",
    "flistCollectOmissions",
    "fdictRecordCaptureOmissions",
    "fnApplyTrackedScopeOffer",
]

S_SCOPE_WHOLE_DIRECTORY = "wholeDirectory"
S_SCOPE_GIT_TRACKED = "gitTracked"
I_SCOPE_VERSION = 1
SET_SCOPES = frozenset({S_SCOPE_WHOLE_DIRECTORY, S_SCOPE_GIT_TRACKED})

S_REASON_UNTRACKED = "untracked"
S_REASON_IGNORED = "ignored"
S_REASON_DELETED_IN_WORKTREE = "deletedInWorktree"
S_REASON_NOT_CHECKED_OUT = "notCheckedOut"
S_REASON_POLICY_EXCLUDED = "policyExcluded"

DICT_REASON_LABELS = {
    S_REASON_UNTRACKED: "untracked",
    S_REASON_IGNORED: "ignored",
    S_REASON_DELETED_IN_WORKTREE: "tracked but deleted in the working tree",
    S_REASON_NOT_CHECKED_OUT: "not checked out (skip-worktree)",
    S_REASON_POLICY_EXCLUDED:
        "excluded by vaibify policy (credential stores, agent instruction "
        "files, caches)",
}

# Defaults (plan section 8). An inventory is superseded by a newer
# observation of the same project, or expires after this long, whichever
# comes first; either way a page request is refused with a re-probe.
F_INVENTORY_LIFETIME_SECONDS = 3600.0
I_DEFAULT_PAGE_SIZE = 200
I_MAX_PAGE_SIZE = 1000
I_MAX_SUMMARY_GROUPS = 12

_S_TOP_LEVEL_GROUP = "(top level)"
_RE_OBSERVATION_ID = re.compile(r"^[0-9a-f]{32}$")
_S_LATEST_INDEX_BASENAME = "latestObservations.json"
_S_REMEMBERED_BASENAME = "rememberedScopes.json"


class SnapshotScopeError(Exception):
    """A snapshot scope is unknown, or its version is not one this reads."""


class OmissionInventoryExpiredError(Exception):
    """An observation id no longer names a current inventory."""


# ----- the scope itself --------------------------------------------------------


def fdictComposeSnapshotScope(sScope):
    """Return the recorded scope for a name, refusing an unknown one."""
    if sScope not in SET_SCOPES:
        raise SnapshotScopeError(
            f"{sScope!r} is not a snapshot scope; choose "
            f"{S_SCOPE_WHOLE_DIRECTORY!r} or {S_SCOPE_GIT_TRACKED!r}")
    return {"sScope": sScope, "iScopeVersion": I_SCOPE_VERSION}


def fdictNormaliseSnapshotScope(dictScope):
    """Return a validated scope; ABSENT reads as the whole directory.

    Absent is every campaign convened before scopes existed, and their
    snapshots were whole-directory, so that reading is a fact, not a
    default. A present scope with an unknown version, or an unknown
    name, refuses: guessing would compare a later identity read against
    a snapshot taken under different rules.
    """
    if not dictScope:
        return fdictComposeSnapshotScope(S_SCOPE_WHOLE_DIRECTORY)
    if not isinstance(dictScope, dict) or dictScope.get(
            "iScopeVersion") != I_SCOPE_VERSION:
        raise SnapshotScopeError(
            "this snapshot scope was recorded by a version of vaibify "
            f"this one does not read ({dictScope!r}); convene again")
    return fdictComposeSnapshotScope(dictScope.get("sScope"))


# ----- the git-tracked set (B1) ------------------------------------------------


def fdictInterpretTrackedIndex(dictRead, ftFindExcludedComponent):
    """Classify one ``gitTrackedIdentities`` answer by the B1 table. Pure.

    ``ftFindExcludedComponent`` is the snapshot's own policy predicate,
    passed in so this module never re-states the exclusion list.
    Returns the eligible identities, the tracked omissions, and the
    paths that REFUSE the scope, each list sorted.
    """
    dictAnswer = {"dictEligible": {}, "dictTrackedOmissions": {},
                  "listConflicts": [], "listSubmodules": [],
                  "listBeyondSymlink": [], "listUnrepresentable": [],
                  "listSkipWorktreePaths": []}
    for sPath, dictEntry in sorted(dictRead.get("dictEntries", {}).items()):
        _fnClassifyTrackedPath(dictAnswer, sPath, dictEntry,
                               ftFindExcludedComponent)
    return dictAnswer


def _fnClassifyTrackedPath(dictAnswer, sPath, dictEntry,
                           ftFindExcludedComponent):
    """Place one tracked path into exactly one bucket of the answer."""
    if any(iStage != 0 for iStage in dictEntry.get("listStages", [0])):
        dictAnswer["listConflicts"].append(sPath)
        return
    if dictEntry.get("sMode") == "160000":
        dictAnswer["listSubmodules"].append(sPath)
        return
    sType = dictEntry.get("sType", "missing")
    if sType == "beyondSymlink":
        dictAnswer["listBeyondSymlink"].append(sPath)
        return
    if ftFindExcludedComponent(sPath) is not None:
        dictAnswer["dictTrackedOmissions"][sPath] = (
            S_REASON_POLICY_EXCLUDED, dictEntry.get("iSizeBytes", 0))
        return
    if sType == "missing":
        dictAnswer["dictTrackedOmissions"][sPath] = (
            S_REASON_NOT_CHECKED_OUT if dictEntry.get("bSkipWorktree")
            else S_REASON_DELETED_IN_WORKTREE, 0)
        return
    if sType not in ("file", "symlink"):
        dictAnswer["listUnrepresentable"].append(sPath)
        return
    if dictEntry.get("bSkipWorktree"):
        dictAnswer["listSkipWorktreePaths"].append(sPath)
    dictAnswer["dictEligible"][sPath] = {
        "sType": sType, "sIdentity": dictEntry.get("sIdentity", ""),
        "iSizeBytes": int(dictEntry.get("iSizeBytes", 0))}


def _fnRefuseUnrepresentableIndex(dictInterpreted, fnRefuse):
    """Refuse a tracked set holding conflicts, submodules, or odd types."""
    for sKey, sWhat in (
            ("listConflicts", "unresolved merge conflicts"),
            ("listSubmodules", "submodules (gitlinks)"),
            ("listBeyondSymlink",
             "tracked paths that now lie beyond a symbolic link (a "
             "tracked directory was replaced by a link, so its files "
             "would be read from wherever the link points)"),
            ("listUnrepresentable",
             "tracked paths that are now directories or special files")):
        listPaths = dictInterpreted[sKey]
        if listPaths:
            fnRefuse(
                f"The git-tracked snapshot cannot be taken: the index has "
                f"{sWhat}: " + ", ".join(repr(s) for s in listPaths[:5])
                + (" and more" if len(listPaths) > 5 else "")
                + ". Resolve them in the project, then convene.")


def fdictObserveTrackedScope(connectionDocker, sContainerId, sRepoRoot,
                             ftFindExcludedComponent, fnRefuse):
    """Observe the git-tracked scope; return a coherence identity.

    The identity has the same keys the whole-directory observation has,
    so the capture compares pre and post the same way in both scopes:
    the HEAD commit, a digest of ``git status`` over TRACKED files only
    (``--untracked-files=no``, so new untracked output cannot move it),
    and the eligible paths' identities. ``fnRefuse`` raises the
    capture's own refusal type; this module never picks one.
    """
    dictRead = connectionDocker.fdictFetchTrackedIdentities(
        sContainerId, sRepoRoot)
    if not dictRead.get("bSuccess"):
        fnRefuse("The git-tracked observation failed ("
                 f"{dictRead.get('sReason') or 'no detail'}); a capture "
                 "whose coherence cannot be established is refused.")
    dictInterpreted = fdictInterpretTrackedIndex(
        dictRead, ftFindExcludedComponent)
    _fnRefuseUnrepresentableIndex(dictInterpreted, fnRefuse)
    return {
        "sCommitSha": dictRead.get("sHeadSha", ""),
        "sDirtyStateDigest": dictRead.get("sPorcelainDigest", ""),
        "sObservedHeadSha": dictRead.get("sHeadSha", ""),
        "sObservedPorcelainDigest": dictRead.get("sPorcelainDigest", ""),
        "dictPathIdentities": {
            sPath: {"sType": dictEntry["sType"],
                    "sIdentity": dictEntry["sIdentity"]}
            for sPath, dictEntry in dictInterpreted["dictEligible"].items()},
        "listIgnoredPaths": [],
        "iChangedCount": int(dictRead.get("iChangedCount", 0)),
        "dictEligible": dictInterpreted["dictEligible"],
        "dictTrackedOmissions": {
            sPath: list(tOmission) for sPath, tOmission
            in dictInterpreted["dictTrackedOmissions"].items()},
        "listSkipWorktreePaths": dictInterpreted["listSkipWorktreePaths"],
    }


def fdictObserveForStaleness(connectionDocker, sContainerId, sRepoRoot,
                             dictScope, ftFindExcludedComponent):
    """Return ``{"bSuccess", "sHeadSha", "sPorcelainDigest",
    "dictPathIdentities"}`` observed IN the campaign's scope.

    The staleness poll compares this to the manifest's baseline, which
    was recorded in the same scope — so in git-tracked scope a new
    untracked output file cannot mark a council stale, and an edit to a
    tracked file does.
    """
    dictScope = fdictNormaliseSnapshotScope(dictScope)
    if dictScope["sScope"] == S_SCOPE_WHOLE_DIRECTORY:
        return connectionDocker.fdictFetchWorktreeIdentities(
            sContainerId, sRepoRoot)
    try:
        dictIdentity = fdictObserveTrackedScope(
            connectionDocker, sContainerId, sRepoRoot,
            ftFindExcludedComponent, _fnRaiseScopeError)
    except SnapshotScopeError as error:
        return {"bSuccess": False, "sReason": str(error)}
    return {"bSuccess": True, "sReason": "",
            "sHeadSha": dictIdentity["sObservedHeadSha"],
            "sPorcelainDigest": dictIdentity["sObservedPorcelainDigest"],
            "dictPathIdentities": dictIdentity["dictPathIdentities"]}


# ----- the omission inventory (B3) -----------------------------------------------


def flistCollectOmissions(dictTrackedIdentity, dictUntrackedRead):
    """Return every omitted path once, as ``[path, reason, size]`` rows."""
    listRows = [[sPath, tOmission[0], int(tOmission[1])]
                for sPath, tOmission in sorted(
                    dictTrackedIdentity["dictTrackedOmissions"].items())]
    setTracked = {listRow[0] for listRow in listRows}
    for listEntry in dictUntrackedRead.get("listEntries", []):
        if listEntry[0] not in setTracked:
            listRows.append([listEntry[0], listEntry[1], int(listEntry[2])])
    return listRows


def _fsGroupOf(sPath):
    """Return the top-level directory a path is counted under."""
    return sPath.split("/", 1)[0] if "/" in sPath else _S_TOP_LEVEL_GROUP


def fdictSummariseOmissions(listRows, bComplete):
    """Summarise omitted rows by top-level directory AND reason.

    Every file lands in exactly one (directory, reason) group — a file
    is never counted under its parent AND its child — so the group
    totals sum to the omitted total.
    """
    dictGroups = {}
    dictByReason = {}
    for sPath, sReason, iSize in listRows:
        dictGroup = dictGroups.setdefault(
            (_fsGroupOf(sPath), sReason),
            {"sDirectory": _fsGroupOf(sPath), "sReason": sReason,
             "iCount": 0, "iBytes": 0})
        dictGroup["iCount"] += 1
        dictGroup["iBytes"] += iSize
        dictReason = dictByReason.setdefault(sReason, {"iCount": 0,
                                                       "iBytes": 0})
        dictReason["iCount"] += 1
        dictReason["iBytes"] += iSize
    listGroups = sorted(dictGroups.values(),
                        key=lambda dictGroup: (-dictGroup["iBytes"],
                                               dictGroup["sDirectory"],
                                               dictGroup["sReason"]))
    return {"iOmittedCount": len(listRows),
            "iOmittedBytes": sum(listRow[2] for listRow in listRows),
            "bComplete": bool(bComplete),
            "dictByReason": dictByReason, "listGroups": listGroups}


def fsResolveSnapshotScopeDirectory():
    """Return the host directory inventories and remembered scopes use."""
    return os.path.join(os.path.expanduser("~"), ".vaibify",
                        "agentCouncils", "snapshotScope")


def _fsEnsurePrivateDirectory(sDirectory):
    os.makedirs(sDirectory, mode=0o700, exist_ok=True)
    os.chmod(sDirectory, 0o700)
    return sDirectory


def fsWriteOmissionInventory(sInventoryPath, dictMetadata, listRows):
    """Write an owner-only gzip JSON-lines inventory; return its sha256.

    The first line is the metadata, each later line one ``[path,
    reason, size]`` row, in the order pages are served. Names only —
    no content is ever read.
    """
    _fsEnsurePrivateDirectory(os.path.dirname(sInventoryPath))
    iDescriptor = os.open(sInventoryPath + ".partial",
                          os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(iDescriptor, "wb") as fileRaw:
        with gzip.GzipFile(fileobj=fileRaw, mode="wb", mtime=0) as fileGzip:
            fileGzip.write((json.dumps(dictMetadata, sort_keys=True)
                            + "\n").encode("utf-8"))
            for listRow in listRows:
                fileGzip.write((json.dumps(listRow) + "\n").encode("utf-8"))
    os.replace(sInventoryPath + ".partial", sInventoryPath)
    with open(sInventoryPath, "rb") as fileIn:
        return hashlib.sha256(fileIn.read()).hexdigest()


def _fsComposeProjectKey(sContainerId, sRepoRoot):
    return f"{sContainerId}|{sRepoRoot}"


def fdictProbeTrackedScope(connectionDocker, sContainerId, sRepoRoot,
                           dictBounds, ftFindExcludedComponent):
    """Weigh the git-tracked scope and record its omission inventory.

    Returns the offer the size modal renders: whether the tracked set
    fits, its count and size, how many tracked files have uncommitted
    edits, its largest files, and the bounded omission summary with the
    ``sObservationId`` pages are read from. A tracked set the scope
    refuses (a conflict, a submodule) answers ``bOffered`` False with
    the reason, never a partial offer.
    """
    try:
        dictIdentity = fdictObserveTrackedScope(
            connectionDocker, sContainerId, sRepoRoot,
            ftFindExcludedComponent, _fnRaiseScopeError)
    except SnapshotScopeError as error:
        return {"bOffered": False, "sReason": str(error)}
    dictUntracked = connectionDocker.fdictFetchUntrackedInventory(
        sContainerId, sRepoRoot)
    if not dictUntracked.get("bSuccess"):
        return {"bOffered": False, "sReason":
                "the untracked-file inventory could not be read ("
                f"{dictUntracked.get('sReason') or 'no detail'})"}
    listRows = flistCollectOmissions(dictIdentity, dictUntracked)
    dictSummary = fdictSummariseOmissions(
        listRows, dictUntracked.get("bComplete", False))
    sObservationId = _fsRecordObservation(
        sContainerId, sRepoRoot, listRows, dictSummary)
    return _fdictComposeOffer(dictIdentity, dictBounds, dictSummary,
                              sObservationId)


def _fnRaiseScopeError(sReason):
    raise SnapshotScopeError(sReason)


def _fdictComposeOffer(dictIdentity, dictBounds, dictSummary, sObservationId):
    """Compose the tracked-scope offer from an observation and the bounds."""
    listSizes = sorted(((dictEntry["iSizeBytes"], sPath) for sPath, dictEntry
                        in dictIdentity["dictEligible"].items()),
                       reverse=True)
    iTotalBytes = sum(iSize for iSize, _ in listSizes)
    iMemberBound = dictBounds["iMaxSnapshotMemberBytes"]
    bFits = (len(listSizes) <= dictBounds["iMaxSnapshotFileCount"]
             and iTotalBytes <= dictBounds["iMaxSnapshotTotalBytes"]
             and all(iSize <= iMemberBound for iSize, _ in listSizes))
    dictSummary = dict(dictSummary)
    dictSummary["listGroups"] = dictSummary["listGroups"][
        :I_MAX_SUMMARY_GROUPS]
    return {
        "bOffered": True, "bFits": bFits,
        "iTrackedFileCount": len(listSizes), "iTrackedBytes": iTotalBytes,
        "iUncommittedEditCount": dictIdentity["iChangedCount"],
        "listLargestTrackedFiles": [
            {"sPath": sPath, "iSizeBytes": iSize}
            for iSize, sPath in listSizes[:10]],
        "sObservationId": sObservationId,
        "sObservedIso": datetime.now(timezone.utc).isoformat(),
        "dictOmissionSummary": dictSummary,
    }


def _fsRecordObservation(sContainerId, sRepoRoot, listRows, dictSummary):
    """Write the inventory and make it the project's latest observation."""
    sObservationId = uuid.uuid4().hex
    sDirectory = _fsEnsurePrivateDirectory(os.path.join(
        fsResolveSnapshotScopeDirectory(), "inventories"))
    fsWriteOmissionInventory(
        os.path.join(sDirectory, f"{sObservationId}.jsonl.gz"),
        {"sObservationId": sObservationId, "sContainerId": sContainerId,
         "sRepoRoot": sRepoRoot, "fObservedEpoch": time.time(),
         "bComplete": dictSummary["bComplete"]},
        _flistSortedForPaging(listRows))
    sLatestPath = os.path.join(sDirectory, _S_LATEST_INDEX_BASENAME)
    sSuperseded = _fdictReadJsonFile(sLatestPath).get(
        _fsComposeProjectKey(sContainerId, sRepoRoot), "")
    _fnUpdateJsonFile(sLatestPath,
                      _fsComposeProjectKey(sContainerId, sRepoRoot),
                      sObservationId)
    _fnPruneInventories(sDirectory, sSuperseded)
    return sObservationId


def _fnPruneInventories(sDirectory, sSupersededId):
    """Delete the superseded inventory and every one past its lifetime.

    A page request for either is already refused, so keeping the file
    would only let a project re-measured many times fill the disk.
    """
    fCutoff = time.time() - F_INVENTORY_LIFETIME_SECONDS
    for sName in os.listdir(sDirectory):
        if not sName.endswith(".jsonl.gz"):
            continue
        sPath = os.path.join(sDirectory, sName)
        try:
            if sName == f"{sSupersededId}.jsonl.gz" or (
                    os.path.getmtime(sPath) < fCutoff):
                os.remove(sPath)
        except OSError:
            continue


def _flistSortedForPaging(listRows):
    """Order rows by (group, reason, path) so a page is a contiguous run."""
    return sorted(listRows, key=lambda listRow: (
        _fsGroupOf(listRow[0]), listRow[1], listRow[0]))


def _fdictReadJsonFile(sPath):
    try:
        with open(sPath, encoding="utf-8") as fileIn:
            jsonValue = json.load(fileIn)
    except (OSError, ValueError):
        return {}
    return jsonValue if isinstance(jsonValue, dict) else {}


def _fnUpdateJsonFile(sPath, sKey, jsonValue):
    """Set one key of a small owner-only JSON map, atomically."""
    from . import agentCouncilCredentialStore
    dictMap = _fdictReadJsonFile(sPath)
    dictMap[sKey] = jsonValue
    agentCouncilCredentialStore.fnWriteJsonAtomically(sPath, dictMap)


def fdictReadOmissionPage(sObservationId, sContainerId, sDirectory, sReason,
                          iOffset=0, iLimit=I_DEFAULT_PAGE_SIZE):
    """Return one page of one omission group from a CURRENT inventory.

    Pages read the file recorded at observation time, never the live
    tree, so files appearing or vanishing while the researcher pages
    change nothing: every inventory path is served exactly once. An id
    that is malformed, belongs to another container, has expired, or
    was superseded by a newer observation of the same project raises
    :class:`OmissionInventoryExpiredError`.
    """
    dictMetadata, sPath = _ftRequireCurrentInventory(
        sObservationId, sContainerId)
    iLimit = max(1, min(int(iLimit), I_MAX_PAGE_SIZE))
    iOffset = max(0, int(iOffset))
    listPaths = []
    iMatched = 0
    with gzip.open(sPath, "rt", encoding="utf-8") as fileIn:
        next(fileIn)
        for sLine in fileIn:
            sRowPath, sRowReason, iSize = json.loads(sLine)
            if _fsGroupOf(sRowPath) != sDirectory or sRowReason != sReason:
                continue
            if iOffset <= iMatched < iOffset + iLimit:
                listPaths.append({"sPath": sRowPath, "iSizeBytes": iSize})
            iMatched += 1
    return {"sObservationId": sObservationId, "sDirectory": sDirectory,
            "sReason": sReason, "iOffset": iOffset, "iTotal": iMatched,
            "bComplete": dictMetadata.get("bComplete", False),
            "listPaths": listPaths}


def _ftRequireCurrentInventory(sObservationId, sContainerId):
    """Return ``(metadata, path)`` for a live inventory, else refuse."""
    if not _RE_OBSERVATION_ID.match(sObservationId or ""):
        raise OmissionInventoryExpiredError("unknown observation")
    sDirectory = os.path.join(fsResolveSnapshotScopeDirectory(), "inventories")
    sPath = os.path.join(sDirectory, f"{sObservationId}.jsonl.gz")
    try:
        with gzip.open(sPath, "rt", encoding="utf-8") as fileIn:
            dictMetadata = json.loads(next(fileIn))
    except (OSError, ValueError, StopIteration):
        raise OmissionInventoryExpiredError("unknown observation")
    if dictMetadata.get("sContainerId") != sContainerId:
        raise OmissionInventoryExpiredError("unknown observation")
    dictLatest = _fdictReadJsonFile(
        os.path.join(sDirectory, _S_LATEST_INDEX_BASENAME))
    sKey = _fsComposeProjectKey(sContainerId, dictMetadata.get("sRepoRoot"))
    if dictLatest.get(sKey) != sObservationId:
        raise OmissionInventoryExpiredError("superseded by a newer look")
    if time.time() - float(dictMetadata.get("fObservedEpoch", 0)) > (
            F_INVENTORY_LIFETIME_SECONDS):
        raise OmissionInventoryExpiredError("expired")
    return dictMetadata, sPath


# ----- the remembered per-project scope ------------------------------------------


def fdictReadRememberedScope(sResourceName, sRepoRoot):
    """Return the scope last chosen for this project, or None."""
    dictRemembered = _fdictReadJsonFile(os.path.join(
        fsResolveSnapshotScopeDirectory(), _S_REMEMBERED_BASENAME))
    dictScope = dictRemembered.get(f"{sResourceName}|{sRepoRoot}")
    try:
        return fdictNormaliseSnapshotScope(dictScope) if dictScope else None
    except SnapshotScopeError:
        return None


def fnRememberScope(sResourceName, sRepoRoot, dictScope):
    """Record the scope as this project's visible default."""
    _fsEnsurePrivateDirectory(fsResolveSnapshotScopeDirectory())
    _fnUpdateJsonFile(
        os.path.join(fsResolveSnapshotScopeDirectory(),
                     _S_REMEMBERED_BASENAME),
        f"{sResourceName}|{sRepoRoot}",
        fdictNormaliseSnapshotScope(dictScope))


def fdictRecordCaptureOmissions(connectionDocker, sContainerId, sRepoRoot,
                                sInventoryPath, dictScope,
                                dictIdentityBefore, fnRefuse):
    """Re-enumerate a tracked capture's omissions beside its manifest.

    Returns the manifest fields that make "what was left out" part of the
    sealed record: the inventory's sha256, whether it is complete, the
    bounded summary participants are told, and the skip-worktree paths
    included as worktree bytes. Nothing for the whole-directory scope.
    Churn among omitted files between probe and capture changes only
    this record, never the capture: only eligible identities are pinned.
    """
    if dictScope["sScope"] != S_SCOPE_GIT_TRACKED:
        return {}
    dictUntracked = connectionDocker.fdictFetchUntrackedInventory(
        sContainerId, sRepoRoot)
    if not dictUntracked.get("bSuccess"):
        fnRefuse("The omission inventory could not be taken ("
                 f"{dictUntracked.get('sReason') or 'no detail'}); a "
                 "tracked snapshot that cannot say what it left out is "
                 "refused.")
    listRows = flistCollectOmissions(dictIdentityBefore, dictUntracked)
    dictSummary = fdictSummariseOmissions(
        listRows, dictUntracked.get("bComplete", False))
    sSha256 = fsWriteOmissionInventory(
        sInventoryPath, {"sScope": dictScope["sScope"],
                         "bComplete": dictSummary["bComplete"]},
        _flistSortedForPaging(listRows))
    dictSummary["listGroups"] = dictSummary["listGroups"][
        :I_MAX_SUMMARY_GROUPS]
    return {"sOmissionInventorySha256": sSha256,
            "bOmissionInventoryComplete": dictSummary["bComplete"],
            "dictOmissionSummary": dictSummary,
            "listSkipWorktreePaths": list(
                dictIdentityBefore["listSkipWorktreePaths"])}


def fnApplyTrackedScopeOffer(connectionDocker, sContainerId, sResourceName,
                             sRepoRoot, dictCapabilities):
    """Add the scope default and, when it matters, the tracked offer.

    Runs after the whole-directory pre-flight. When the whole directory
    fits and nothing was remembered, there is nothing to offer and no
    extra read is spent. Otherwise the tracked set is weighed and the
    answer stamped: a remembered git-tracked choice that fits clears the
    size refusal; a whole directory that does not fit, beside a tracked
    set that does, becomes a CHOICE (``bNeedsSnapshotChoice``); a
    tracked set that does not fit either leaves the refusal standing,
    with the tracked numbers attached so the modal can say why.
    """
    dictRemembered = fdictReadRememberedScope(sResourceName, sRepoRoot)
    dictCapabilities["dictSnapshotScopeDefault"] = (
        dictRemembered or fdictComposeSnapshotScope(S_SCOPE_WHOLE_DIRECTORY))
    dictFeasibility = dictCapabilities["dictSnapshotFeasibility"]
    bWholeFits = dictFeasibility["bFits"] or dictFeasibility[
        "bResolvableByExcludingFiles"]
    bRememberedTracked = bool(dictRemembered) and (
        dictRemembered["sScope"] == S_SCOPE_GIT_TRACKED)
    if bWholeFits and not bRememberedTracked:
        return
    from . import agentCouncilContext
    try:
        dictOffer = fdictProbeTrackedScope(
            connectionDocker, sContainerId, sRepoRoot,
            _fdictBoundsFromFeasibility(dictFeasibility),
            agentCouncilContext.ftFindExcludedComponent)
    except OSError as error:
        dictOffer = {"bOffered": False, "sReason":
                     f"the git-tracked files could not be weighed "
                     f"({type(error).__name__})"}
    dictCapabilities["dictTrackedScopeOffer"] = dictOffer
    _fnStampScopeVerdict(dictCapabilities, dictOffer, bWholeFits,
                         bRememberedTracked)


def _fdictBoundsFromFeasibility(dictFeasibility):
    """Return the bounds the whole-directory pre-flight measured against."""
    return {sKey: dictFeasibility[sKey] for sKey in (
        "iMaxSnapshotFileCount", "iMaxSnapshotMemberBytes",
        "iMaxSnapshotTotalBytes")}


def _fnStampScopeVerdict(dictCapabilities, dictOffer, bWholeFits,
                         bRememberedTracked):
    """Turn the offer into readiness facts on the capabilities answer."""
    bTrackedFits = dictOffer.get("bOffered") and dictOffer.get("bFits")
    if bTrackedFits and bRememberedTracked:
        if dictCapabilities.get("sUnavailableIn") == "snapshot-too-large":
            dictCapabilities["sUnavailableIn"] = ""
            dictCapabilities["sReason"] = ""
        return
    if bTrackedFits and not bWholeFits:
        dictCapabilities["bNeedsSnapshotChoice"] = True
        dictCapabilities["sUnavailableIn"] = "snapshot-needs-choice"
        return
    if not bWholeFits:
        dictCapabilities["sReason"] = (
            dictCapabilities.get("sReason", "") + " Copying only the files "
            "git tracks does not help: " + (
                dictOffer.get("sReason") or
                f"the tracked set alone is {dictOffer.get('iTrackedFileCount')}"
                f" files, {dictOffer.get('iTrackedBytes')} bytes, which is "
                "still over the limits") + ".")
