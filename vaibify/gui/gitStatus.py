"""Host-side git status for a vaibify workspace.

Phase 3 of the workspace-as-git-repo plan. Given the host path of a
workspace, returns a snapshot of its git state suitable for feeding
the dashboard freshness badges (Phase 4). The module is deliberately
stateless: every call runs fresh subprocesses; caching is the
caller's concern.

The module uses the hardening pattern from
``vaibify.reproducibility.overleafMirror``: ``GIT_TERMINAL_PROMPT=0``
so a bad credential helper cannot hang the server, and the shared
``-c protocol.file.allow=never`` family of config flags on every git
invocation. Remote operations (fetch, clone) are never issued here;
the ``iBehind`` count reflects whatever the user's last ``git fetch``
produced.

All paths in and out are repo-root-relative posix strings.
"""

import datetime
import os
import posixpath
import subprocess

__all__ = [
    "LIST_GIT_HARDENING_CONFIG",
    "fdictGitStatusForWorkspace",
    "fdictEmptyStatus",
    "fsRunGit",
]


LIST_GIT_HARDENING_CONFIG = [
    "-c", "protocol.file.allow=never",
    "-c", "protocol.allow=user",
    "-c", "core.symlinks=false",
    "-c", "submodule.recurse=false",
]

S_UTC_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def fdictEmptyStatus(sReason=""):
    """Return a status dict representing 'not a git repo' or an error."""
    return {
        "bIsRepo": False,
        "sHeadSha": "",
        "sBranch": "",
        "iAhead": 0,
        "iBehind": 0,
        "dictFileStates": {},
        "sRefreshedAt": _fsUtcNow(),
        "sReason": sReason,
    }


def _fsUtcNow():
    """Return the current UTC time formatted for API responses."""
    return datetime.datetime.now(
        datetime.timezone.utc,
    ).strftime(S_UTC_FORMAT)


def _fdictBaseEnv():
    """Build an env dict that refuses to block on credential prompts."""
    dictEnv = os.environ.copy()
    dictEnv["GIT_TERMINAL_PROMPT"] = "0"
    return dictEnv


def fsRunGit(listArgs, sCwd):
    """Run a hardened git command and return a CompletedProcess.

    Never raises. On a missing workspace directory subprocess.run
    would normally raise FileNotFoundError before git even starts;
    this wrapper traps that into a synthetic failure so callers can
    treat every error uniformly.
    """
    listFullArgs = ["git"] + list(LIST_GIT_HARDENING_CONFIG) + listArgs
    try:
        return subprocess.run(
            listFullArgs,
            cwd=sCwd, env=_fdictBaseEnv(),
            capture_output=True, text=True,
        )
    except FileNotFoundError as error:
        return subprocess.CompletedProcess(
            args=listFullArgs,
            returncode=127,
            stdout="",
            stderr=str(error),
        )


def _fbIsGitRepo(sWorkspaceRoot):
    """Return True when sWorkspaceRoot is inside a git work tree."""
    if not sWorkspaceRoot or not os.path.isdir(sWorkspaceRoot):
        return False
    result = fsRunGit(
        ["rev-parse", "--is-inside-work-tree"], sCwd=sWorkspaceRoot,
    )
    if result.returncode != 0:
        return False
    return (result.stdout or "").strip() == "true"


def _fsHeadSha(sWorkspaceRoot):
    """Return the current HEAD SHA, or empty string on empty / detached."""
    result = fsRunGit(["rev-parse", "HEAD"], sCwd=sWorkspaceRoot)
    if result.returncode != 0:
        return ""
    return (result.stdout or "").strip()


def _fbLineIsBranchHeader(sLine):
    """Return True when sLine is a porcelain=v2 branch header."""
    return sLine.startswith("#")


def _fnParseBranchHeader(sLine, dictResult):
    """Update dictResult with branch/ahead/behind from a '#' header line."""
    listParts = sLine.split()
    if len(listParts) < 3:
        return
    sField = listParts[1]
    if sField == "branch.head":
        sName = listParts[2]
        if sName != "(detached)":
            dictResult["sBranch"] = sName
    elif sField == "branch.ab" and len(listParts) >= 4:
        try:
            dictResult["iAhead"] = int(listParts[2].lstrip("+"))
            dictResult["iBehind"] = int(listParts[3].lstrip("-"))
        except ValueError:
            pass


def _fnParseEntry(sLine, dictFileStates):
    """Update dictFileStates with one porcelain=v2 entry line."""
    if not sLine:
        return
    sType = sLine[0]
    if sType == "?":
        sPath = sLine[2:].strip()
        if sPath:
            dictFileStates[sPath] = "untracked"
        return
    if sType == "!":
        sPath = sLine[2:].strip()
        if sPath:
            dictFileStates[sPath] = "ignored"
        return
    if sType == "1":
        _fnParseOrdinaryEntry(sLine, dictFileStates)
    elif sType == "2":
        _fnParseRenamedEntry(sLine, dictFileStates)
    elif sType == "u":
        listParts = sLine.split()
        if len(listParts) >= 2:
            sPath = sLine.split(" ", 10)[-1].strip()
            if sPath:
                dictFileStates[sPath] = "conflict"


def _fnParseOrdinaryEntry(sLine, dictFileStates):
    """Handle a '1 XY ...' ordinary change line (8 fields + path)."""
    listParts = sLine.split(" ", 8)
    if len(listParts) < 9:
        return
    sXy = listParts[1]
    sPath = listParts[8].strip()
    if not sPath:
        return
    dictFileStates[sPath] = _fsStateFromXy(sXy)


def _fnParseRenamedEntry(sLine, dictFileStates):
    """Handle a '2 XY ... path\torig' renamed/copied line."""
    listParts = sLine.split(" ", 9)
    if len(listParts) < 10:
        return
    sXy = listParts[1]
    sRest = listParts[9]
    sPath = sRest.split("\t", 1)[0].strip()
    if not sPath:
        return
    dictFileStates[sPath] = _fsStateFromXy(sXy)


def _fsStateFromXy(sXy):
    """Map a 2-char porcelain XY code to our state vocabulary."""
    if len(sXy) < 2:
        return "uncommitted"
    sIndex, sWorktree = sXy[0], sXy[1]
    if sWorktree != "." and sWorktree != " ":
        return "dirty"
    if sIndex != "." and sIndex != " ":
        return "uncommitted"
    return "committed"


def _fdictParsePorcelain(sOutput):
    """Return {branch, ahead, behind, dictFileStates} from porcelain=v2 output."""
    dictResult = {
        "sBranch": "",
        "iAhead": 0,
        "iBehind": 0,
        "dictFileStates": {},
    }
    for sLine in (sOutput or "").splitlines():
        sLine = sLine.rstrip("\n")
        if not sLine:
            continue
        if _fbLineIsBranchHeader(sLine):
            _fnParseBranchHeader(sLine, dictResult)
        else:
            _fnParseEntry(sLine, dictResult["dictFileStates"])
    return dictResult


def fdictGitStatusForWorkspace(sWorkspaceRoot):
    """Return a dashboard-friendly git status snapshot for a workspace.

    Keys: bIsRepo, sHeadSha, sBranch, iAhead, iBehind, dictFileStates,
    sRefreshedAt, sReason. Always safe to call; never raises.
    """
    if not _fbIsGitRepo(sWorkspaceRoot):
        return fdictEmptyStatus("Not a git repository")
    result = fsRunGit(
        ["status", "--porcelain=v2", "--branch", "--untracked-files=all"],
        sCwd=sWorkspaceRoot,
    )
    if result.returncode != 0:
        return fdictEmptyStatus(
            "git status failed: " + (result.stderr or "").strip()
        )
    dictParsed = _fdictParsePorcelain(result.stdout or "")
    return {
        "bIsRepo": True,
        "sHeadSha": _fsHeadSha(sWorkspaceRoot),
        "sBranch": dictParsed["sBranch"],
        "iAhead": dictParsed["iAhead"],
        "iBehind": dictParsed["iBehind"],
        "dictFileStates": dictParsed["dictFileStates"],
        "sRefreshedAt": _fsUtcNow(),
        "sReason": "",
    }
