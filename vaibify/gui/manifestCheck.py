"""Pre-push manifest check: what canonical files aren't committed yet?

Phase 5 of the workspace-as-git-repo plan. Before a user pushes to any
service the dashboard surfaces canonical files (the set defined by
``stateContract.flistCanonicalTrackedFiles``) that are uncommitted,
untracked, or dirty. The dialog is a safety net — non-blocking — so
the user can one-click commit them before the push proceeds.

The computation is pure: take the canonical list and the git status
snapshot, return a structured report. The route layer handles the
actual ``git add`` + ``git commit`` side effect when the user accepts.
"""

from . import gitStatus, stateContract
from . import workflowManager

__all__ = [
    "S_STATE_MODIFIED",
    "S_STATE_UNTRACKED",
    "S_STATE_DIRTY",
    "S_STATE_STAGED",
    "fdictBuildManifestReport",
    "fdictBuildManifestReportFromStatus",
    "flistFilesNeedingCommit",
    "flistScopeCanonicalToService",
]


S_STATE_MODIFIED = "modified"
S_STATE_UNTRACKED = "untracked"
S_STATE_DIRTY = "dirty"
S_STATE_STAGED = "staged-only"

_SET_NEEDS_COMMIT = {"uncommitted", "dirty", "untracked"}

_DICT_LABEL_MAP = {
    "uncommitted": S_STATE_STAGED,
    "dirty": S_STATE_DIRTY,
    "untracked": S_STATE_UNTRACKED,
}

_FROZENSET_OVERLEAF_EXTENSIONS = frozenset({
    ".tex", ".pdf", ".png", ".jpg", ".jpeg",
    ".eps", ".svg", ".bib",
})


def flistScopeCanonicalToService(
    listCanonical, dictWorkflow, sService,
):
    """Filter the canonical file list to those relevant to ``sService``.

    GitHub pushes go through git itself, so every canonical path is
    relevant — the list is returned unchanged. Overleaf only accepts
    a fixed set of extensions; Zenodo accepts any file but only those
    the user has explicitly opted into (``bZenodo=True``). Returning
    an unscoped list here would flood the pre-push warning with
    files that couldn't possibly be part of the push.
    """
    sNormalized = (sService or "").lower()
    if sNormalized in ("", "github"):
        return listCanonical
    dictSyncStatus = dictWorkflow.get("dictSyncStatus", {}) or {}
    sProjectRepoPath = dictWorkflow.get("sProjectRepoPath", "")
    listScoped = []
    for sPath in listCanonical:
        if sNormalized == "overleaf":
            sLower = sPath.lower()
            iDot = sLower.rfind(".")
            if iDot < 0 or sLower[iDot:] not in (
                _FROZENSET_OVERLEAF_EXTENSIONS
            ):
                continue
            dictEntry = workflowManager.fdictLookupSyncEntry(
                dictSyncStatus, sPath, sProjectRepoPath,
            )
            if not dictEntry.get("bOverleaf"):
                continue
        elif sNormalized == "zenodo":
            dictEntry = workflowManager.fdictLookupSyncEntry(
                dictSyncStatus, sPath, sProjectRepoPath,
            )
            if not dictEntry.get("bZenodo"):
                continue
        listScoped.append(sPath)
    return listScoped


def flistFilesNeedingCommit(listCanonical, dictGitStatus):
    """Return entries of canonical files that are not cleanly committed.

    Each entry is ``{sPath, sState}`` where sState is one of the
    public S_STATE_* constants. Files tracked and clean are omitted.
    """
    listResult = []
    dictFileStates = dictGitStatus.get("dictFileStates", {}) or {}
    for sPath in listCanonical:
        sRawState = dictFileStates.get(sPath)
        if sRawState is None:
            continue
        if sRawState not in _SET_NEEDS_COMMIT:
            continue
        sLabel = _DICT_LABEL_MAP.get(sRawState, S_STATE_MODIFIED)
        listResult.append({"sPath": sPath, "sState": sLabel})
    return listResult


def fdictBuildManifestReportFromStatus(
    dictGit, listCanonical,
):
    """Assemble the manifest report from pre-computed inputs.

    Transport-agnostic version used by the route layer: the caller
    supplies ``dictGit`` (from either host-side or container-side git
    execution) and the canonical tracked-file list. Keeps the report
    shape identical across transports.
    """
    if not dictGit.get("bIsRepo"):
        return {
            "bIsRepo": False,
            "listNeedsCommit": [],
            "iCanonicalCount": 0,
            "sBranch": "",
            "iAhead": 0,
            "iBehind": 0,
            "sHeadSha": "",
            "sReason": dictGit.get("sReason", ""),
        }
    listNeedsCommit = flistFilesNeedingCommit(listCanonical, dictGit)
    return {
        "bIsRepo": True,
        "listNeedsCommit": listNeedsCommit,
        "iCanonicalCount": len(listCanonical),
        "sBranch": dictGit.get("sBranch", ""),
        "iAhead": dictGit.get("iAhead", 0),
        "iBehind": dictGit.get("iBehind", 0),
        "sHeadSha": dictGit.get("sHeadSha", ""),
        "sReason": "",
    }


def fdictBuildManifestReport(dictWorkflow, sWorkspaceRoot):
    """Host-side manifest report. Requires a filesystem-accessible workspace.

    Retained for bind-mounted workspaces and tests. Routes running
    against Docker-volume workspaces should use
    ``fdictBuildManifestReportFromStatus`` with a container-sourced
    ``dictGit`` instead.
    """
    dictGit = gitStatus.fdictGitStatusForWorkspace(sWorkspaceRoot)
    listCanonical = []
    if dictGit.get("bIsRepo"):
        listCanonical = stateContract.flistCanonicalTrackedFiles(
            dictWorkflow, sWorkspaceRoot,
        )
    return fdictBuildManifestReportFromStatus(dictGit, listCanonical)
