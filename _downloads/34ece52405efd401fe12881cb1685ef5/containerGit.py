"""Container-side git driver for per-workflow project repos.

On macOS and Windows the vaibify workspace is a Docker-managed named
volume; its source path lives inside the Docker Desktop VM and isn't
accessible to the host's Python. Host-side git subprocess calls
(``gitStatus.fsRunGit``) therefore can't see those workspaces at all.

This module reproduces the repo-level git operations that
``gitStatus``, ``badgeState``, and ``manifestCheck`` need, but routes
every git invocation through ``docker exec``. Parsing reuses the
helpers from ``gitStatus``; only transport differs.

``/workspace`` is the discovery root only — it is a named volume
containing one or more project-repo subdirectories (plus shared
config). The authoritative git target for any given workflow is the
enclosing project repo returned by
``fsDetectProjectRepoInContainer``; callers pass that path as
``sWorkspace`` to every function below. ``S_CONTAINER_WORKSPACE`` is
retained as a fallback for legacy call sites that have no active
workflow to key off.
"""

import json
import posixpath
import shlex

from . import gitStatus

__all__ = [
    "S_CONTAINER_WORKSPACE",
    "fdictGitStatusInContainer",
    "fdictComputeBlobShasInContainer",
    "flistListContainerFiles",
    "fsDetectProjectRepoInContainer",
    "ftResultGitAddInContainer",
    "ftResultGitCommitInContainer",
    "ftResultGitFetchInContainer",
    "ftResultGitPullFastForwardInContainer",
    "fsGitHeadShaInContainer",
]


S_CONTAINER_WORKSPACE = "/workspace"


def fsDetectProjectRepoInContainer(
    connectionDocker, sContainerId, sWorkflowPath,
):
    """Return the git work-tree root enclosing sWorkflowPath.

    Runs ``git rev-parse --show-toplevel`` inside the container,
    starting from the directory containing ``sWorkflowPath``.
    Returns the absolute container path on success; returns ``""``
    when the directory is not inside a git work tree (caller decides
    whether to raise).
    """
    sWorkflowDir = posixpath.dirname(sWorkflowPath or "")
    if not sWorkflowDir:
        return ""
    sCommand = (
        "cd " + shlex.quote(sWorkflowDir) + " && "
        "git rev-parse --show-toplevel 2>/dev/null"
    )
    iExit, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand,
    )
    if iExit != 0:
        return ""
    return (sOutput or "").strip()


def _fsHardeningPrefix():
    """Return the container-side hardening flags as a shell string."""
    return " ".join(
        shlex.quote(s) for s in gitStatus.LIST_GIT_HARDENING_CONFIG
    )


def _fbIsRepoInContainer(connectionDocker, sContainerId, sWorkspace):
    """Return True when the container's workspace is a git work tree."""
    sCommand = (
        "cd " + shlex.quote(sWorkspace) + " && "
        "git rev-parse --is-inside-work-tree 2>/dev/null"
    )
    iExit, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand,
    )
    if iExit != 0:
        return False
    return (sOutput or "").strip() == "true"


def fsGitHeadShaInContainer(
    connectionDocker, sContainerId, sWorkspace=S_CONTAINER_WORKSPACE,
):
    """Return the HEAD SHA of the container's workspace repo."""
    sCommand = (
        "cd " + shlex.quote(sWorkspace) + " && git rev-parse HEAD"
    )
    iExit, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand,
    )
    if iExit != 0:
        return ""
    return (sOutput or "").strip()


def fdictGitStatusInContainer(
    connectionDocker, sContainerId, sWorkspace=S_CONTAINER_WORKSPACE,
):
    """Return a dashboard-friendly git status snapshot for a container.

    Matches the shape of ``gitStatus.fdictGitStatusForWorkspace`` so
    that badge and manifest logic can consume either source.
    """
    if not _fbIsRepoInContainer(
        connectionDocker, sContainerId, sWorkspace,
    ):
        return gitStatus.fdictEmptyStatus("Not a git repository")
    sHardening = _fsHardeningPrefix()
    sCommand = (
        "cd " + shlex.quote(sWorkspace) + " && "
        "git " + sHardening + " "
        "status --porcelain=v2 --branch --untracked-files=all"
    )
    iExit, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand,
    )
    if iExit != 0:
        return gitStatus.fdictEmptyStatus(
            "git status failed: " + (sOutput or "").strip()
        )
    dictParsed = gitStatus._fdictParsePorcelain(sOutput or "")
    return {
        "bIsRepo": True,
        "sHeadSha": fsGitHeadShaInContainer(
            connectionDocker, sContainerId, sWorkspace,
        ),
        "sBranch": dictParsed["sBranch"],
        "iAhead": dictParsed["iAhead"],
        "iBehind": dictParsed["iBehind"],
        "dictFileStates": dictParsed["dictFileStates"],
        "sRefreshedAt": gitStatus._fsUtcNow(),
        "sReason": "",
    }


def fdictComputeBlobShasInContainer(
    connectionDocker, sContainerId, listRepoRelPaths,
    sWorkspace=S_CONTAINER_WORKSPACE,
):
    """Compute git blob SHAs for a list of files in one docker-exec call.

    Returns ``{repo-rel-path: 40-hex-sha}`` for every file that was
    readable; missing or unreadable files are silently omitted.
    Hashing every file in one subprocess keeps per-poll latency flat
    regardless of file count.
    """
    if not listRepoRelPaths:
        return {}
    sPathsJson = json.dumps(list(listRepoRelPaths))
    sScript = (
        "import hashlib, json, os, sys\n"
        "base = " + repr(sWorkspace) + "\n"
        "paths = json.loads(sys.stdin.read())\n"
        "out = {}\n"
        "for p in paths:\n"
        "    full = os.path.join(base, p)\n"
        "    try:\n"
        "        with open(full, 'rb') as f: data = f.read()\n"
        "    except OSError:\n"
        "        continue\n"
        "    header = ('blob ' + str(len(data)) + chr(0)).encode()\n"
        "    h = hashlib.sha1(); h.update(header); h.update(data)\n"
        "    out[p] = h.hexdigest()\n"
        "print(json.dumps(out))\n"
    )
    sCommand = (
        "python3 -c " + shlex.quote(sScript) +
        " <<< " + shlex.quote(sPathsJson)
    )
    iExit, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand,
    )
    if iExit != 0:
        return {}
    try:
        dictLoaded = json.loads((sOutput or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {}
    if not isinstance(dictLoaded, dict):
        return {}
    return dictLoaded


def flistListContainerFiles(
    connectionDocker, sContainerId, listRelGlobs,
    sWorkspace=S_CONTAINER_WORKSPACE,
):
    """Expand glob patterns against the container's workspace.

    ``listRelGlobs`` is a list of repo-relative glob expressions such
    as ``".vaibify/workflows/*.json"``. Returns the concrete file
    paths (repo-relative) that currently exist. Single round-trip.
    """
    if not listRelGlobs:
        return []
    sGlobsJson = json.dumps(list(listRelGlobs))
    sScript = (
        "import glob, json, os, sys\n"
        "base = " + repr(sWorkspace) + "\n"
        "patterns = json.loads(sys.stdin.read())\n"
        "out = []\n"
        "seen = set()\n"
        "for pat in patterns:\n"
        "    for p in sorted(glob.glob(os.path.join(base, pat))):\n"
        "        rel = os.path.relpath(p, base)\n"
        "        if rel in seen or not os.path.isfile(p):\n"
        "            continue\n"
        "        seen.add(rel)\n"
        "        out.append(rel.replace(os.sep, '/'))\n"
        "print(json.dumps(out))\n"
    )
    sCommand = (
        "python3 -c " + shlex.quote(sScript) +
        " <<< " + shlex.quote(sGlobsJson)
    )
    iExit, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand,
    )
    if iExit != 0:
        return []
    try:
        listLoaded = json.loads(
            (sOutput or "").strip().splitlines()[-1]
        )
    except (ValueError, IndexError):
        return []
    if not isinstance(listLoaded, list):
        return []
    return listLoaded


def ftResultGitAddInContainer(
    connectionDocker, sContainerId, listFilePaths,
    sWorkspace=S_CONTAINER_WORKSPACE,
):
    """git add the given paths inside the container; return (rc, stdout)."""
    if not listFilePaths:
        return (0, "")
    sHardening = _fsHardeningPrefix()
    sPaths = " ".join(shlex.quote(s) for s in listFilePaths)
    sCommand = (
        "cd " + shlex.quote(sWorkspace) + " && "
        "git " + sHardening + " add -- " + sPaths
    )
    return connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand,
    )


def ftResultGitCommitInContainer(
    connectionDocker, sContainerId, sCommitMessage,
    sWorkspace=S_CONTAINER_WORKSPACE,
):
    """git commit -m in the container; returns (rc, stdout)."""
    sHardening = _fsHardeningPrefix()
    sCommand = (
        "cd " + shlex.quote(sWorkspace) + " && "
        "git " + sHardening + " commit -m " +
        shlex.quote(sCommitMessage)
    )
    return connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand,
    )


def ftResultGitFetchInContainer(
    connectionDocker, sContainerId,
    sWorkspace=S_CONTAINER_WORKSPACE,
):
    """git fetch --no-tags origin in the container; returns (rc, stdout).

    Updates the local-tracking refs so a subsequent
    ``fdictGitStatusInContainer`` reports an accurate ``iBehind``
    against ``origin/<branch>``. Does not modify the working tree.
    """
    sHardening = _fsHardeningPrefix()
    sCommand = (
        "cd " + shlex.quote(sWorkspace) + " && "
        "git " + sHardening + " fetch --no-tags origin 2>&1"
    )
    return connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand,
    )


def ftResultGitPullFastForwardInContainer(
    connectionDocker, sContainerId,
    sWorkspace=S_CONTAINER_WORKSPACE,
):
    """git pull --ff-only in the container; returns (rc, stdout).

    Refuses to perform a merge or rebase. If the local branch has
    diverged, git exits non-zero and the working tree is left
    untouched. Callers should verify the working tree is clean
    before invoking (see ``dictFileStates`` from
    ``fdictGitStatusInContainer``).
    """
    sHardening = _fsHardeningPrefix()
    sCommand = (
        "cd " + shlex.quote(sWorkspace) + " && "
        "git " + sHardening + " pull --ff-only 2>&1"
    )
    return connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand,
    )
