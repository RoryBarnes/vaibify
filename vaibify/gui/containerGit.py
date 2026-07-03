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
import re
import shlex
import time

from . import gitStatus

__all__ = [
    "S_CONTAINER_WORKSPACE",
    "fdictGitStatusInContainer",
    "fdictComputeBlobShasInContainer",
    "fdictProbePushOutcome",
    "fdictRemoteHeadsInContainer",
    "flistListContainerFiles",
    "fsDetectProjectRepoInContainer",
    "fsRemoteUrlInContainer",
    "ftResultGitAddInContainer",
    "ftResultGitCommitInContainer",
    "ftResultGitDiffCachedQuietInContainer",
    "ftResultGitRemoveCachedInContainer",
    "ftResultGitRestoreStagedInContainer",
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


_S_HEAD_MARKER = "__VAIBIFY_HEAD__"
_S_STATUS_MARKER = "__VAIBIFY_STATUS__"
_S_NOT_REPO_MARKER = "__VAIBIFY_NOT_REPO__"


def _fsBuildCombinedStatusCommand(sWorkspace):
    """Combined exec: repo-check + HEAD SHA + porcelain status in one call."""
    sHardening = _fsHardeningPrefix()
    return (
        "cd " + shlex.quote(sWorkspace) + " && "
        "if ! git rev-parse --is-inside-work-tree "
        ">/dev/null 2>&1; then "
        f"echo {_S_NOT_REPO_MARKER}; exit 0; fi && "
        f"echo {_S_HEAD_MARKER} && "
        "(git rev-parse HEAD 2>/dev/null || true) && "
        f"echo {_S_STATUS_MARKER} && "
        "git " + sHardening + " "
        "status --porcelain=v2 --branch --untracked-files=normal"
    )


def _ftParseCombinedStatusOutput(sOutput):
    """Split combined exec output into (sHeadSha, sStatusBody)."""
    sBefore, _, sStatus = sOutput.partition(_S_STATUS_MARKER + "\n")
    _, _, sHead = sBefore.partition(_S_HEAD_MARKER + "\n")
    return sHead.strip(), sStatus


def fdictGitStatusInContainer(
    connectionDocker, sContainerId, sWorkspace=S_CONTAINER_WORKSPACE,
):
    """Return a dashboard-friendly git status snapshot for a container.

    Matches the shape of ``gitStatus.fdictGitStatusForWorkspace`` so
    that badge and manifest logic can consume either source. Runs the
    repo-check, HEAD SHA lookup, and porcelain status in a single
    docker exec to halve the round-trip overhead.
    """
    sCommand = _fsBuildCombinedStatusCommand(sWorkspace)
    iExit, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand,
    )
    if iExit != 0:
        return gitStatus.fdictEmptyStatus(
            "git status failed: " + (sOutput or "").strip()
        )
    sOutput = sOutput or ""
    if sOutput.startswith(_S_NOT_REPO_MARKER):
        return gitStatus.fdictEmptyStatus("Not a git repository")
    sHeadSha, sStatusBody = _ftParseCombinedStatusOutput(sOutput)
    dictParsed = gitStatus._fdictParsePorcelain(sStatusBody)
    return {
        "bIsRepo": True,
        "sHeadSha": sHeadSha,
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


def ftResultGitDiffCachedQuietInContainer(
    connectionDocker, sContainerId, sWorkspace=S_CONTAINER_WORKSPACE,
):
    """Return (rc, output) of ``git diff --cached --quiet``.

    rc 0 means the index matches HEAD (nothing staged); rc 1 means
    staged changes exist. Callers that are about to stage-and-commit
    a scoped change use this to refuse when unrelated work is already
    staged — a bare ``git commit`` would sweep it in.
    """
    sCommand = (
        "cd " + shlex.quote(sWorkspace) + " && "
        "git diff --cached --quiet"
    )
    return connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand,
    )


def ftResultGitRestoreStagedInContainer(
    connectionDocker, sContainerId, listFilePaths,
    sWorkspace=S_CONTAINER_WORKSPACE,
):
    """Unstage the given paths (restore index entries from HEAD).

    Rollback primitive: undoes a staged ``rm --cached`` when the
    follow-up commit fails, so a failed operation leaves the index
    exactly as it found it. Literal pathspecs for the same reason as
    the removal itself.
    """
    if not listFilePaths:
        return (0, "")
    sPaths = " ".join(shlex.quote(s) for s in listFilePaths)
    sCommand = (
        "cd " + shlex.quote(sWorkspace) + " && "
        "GIT_LITERAL_PATHSPECS=1 "
        "git restore --staged -- " + sPaths
    )
    return connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand,
    )


def ftResultGitRemoveCachedInContainer(
    connectionDocker, sContainerId, listFilePaths,
    sWorkspace=S_CONTAINER_WORKSPACE,
):
    """git rm --cached the given paths in the container; return (rc, stdout).

    The files stay on disk — only the index entries are removed, so a
    follow-up commit publishes the removal without deleting content.
    ``GIT_LITERAL_PATHSPECS`` disables pathspec magic and globbing so
    a value like ``:(glob)**`` can only ever match a file literally
    named that — this function takes request-derived paths, and the
    route-level filter must not be the only wall.
    """
    if not listFilePaths:
        return (0, "")
    sHardening = _fsHardeningPrefix()
    sPaths = " ".join(shlex.quote(s) for s in listFilePaths)
    sCommand = (
        "cd " + shlex.quote(sWorkspace) + " && "
        "GIT_LITERAL_PATHSPECS=1 "
        "git " + sHardening + " rm --cached -- " + sPaths
    )
    return connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand,
    )


def ftResultGitCommitInContainer(
    connectionDocker, sContainerId, sCommitMessage,
    sWorkspace=S_CONTAINER_WORKSPACE, listFilePaths=None,
):
    """git commit -m in the container; returns (rc, stdout).

    When listFilePaths is provided, only those explicit pathspecs are
    committed via ``git commit -m ... -- <paths>``; anything else
    already staged is left alone.
    """
    sHardening = _fsHardeningPrefix()
    sBase = (
        "cd " + shlex.quote(sWorkspace) + " && "
        "git " + sHardening + " commit -m " +
        shlex.quote(sCommitMessage)
    )
    if listFilePaths:
        sBase += " -- " + " ".join(
            shlex.quote(s) for s in listFilePaths
        )
    return connectionDocker.ftResultExecuteCommand(
        sContainerId, sBase,
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


def fsRemoteUrlInContainer(
    connectionDocker, sContainerId, sProjectRepoPath,
):
    """Return the origin remote URL for the project repo or "".

    Runs ``git remote get-url origin`` inside the container. Returns
    the URL string on success; returns ``""`` when no remote is
    configured, the path is not a git work tree, or the command
    fails for any reason. Callers must validate the returned URL
    before rendering it (e.g. with a JavaScript URL whitelist).

    Strips any embedded userinfo (``https://user:token@host/...``) so
    a misconfigured remote can never leak credentials to the frontend,
    browser history, or the Referer header on a "View on GitHub" click.
    """
    if not sProjectRepoPath:
        return ""
    sCommand = (
        "cd " + shlex.quote(sProjectRepoPath) + " && "
        "git remote get-url origin 2>/dev/null"
    )
    iExit, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand,
    )
    if iExit != 0:
        return ""
    return _fsStripUrlUserinfo((sOutput or "").strip())


def _fsStripUrlUserinfo(sUrl):
    """Remove ``user:password@`` from an http(s) URL, if present."""
    if not sUrl:
        return ""
    return re.sub(r"(https?://)[^/\s@]+@", r"\1", sUrl, flags=re.I)


def _ftParseAheadBehindCounts(sLine):
    """Parse 'iAhead<TAB>iBehind' emitted by git rev-list --left-right --count."""
    listParts = (sLine or "").split()
    if len(listParts) != 2:
        return (None, None)
    try:
        return (int(listParts[0]), int(listParts[1]))
    except ValueError:
        return (None, None)


def _fdictProbePushOnce(connectionDocker, sContainerId, sWorkspace):
    """Run one HEAD + ahead/behind probe; return None when inconclusive."""
    sCommand = (
        "cd " + shlex.quote(sWorkspace) + " && "
        "git rev-parse HEAD && "
        "git rev-list --left-right --count 'HEAD...@{upstream}'"
    )
    try:
        iExit, sOutput = connectionDocker.ftResultExecuteCommand(
            sContainerId, sCommand,
        )
    except Exception:
        return None
    if iExit != 0:
        return None
    listLines = [
        sLine for sLine in (sOutput or "").strip().splitlines()
        if sLine.strip()
    ]
    if len(listLines) < 2:
        return None
    iAhead, iBehind = _ftParseAheadBehindCounts(listLines[-1])
    if iAhead is None:
        return None
    return {
        "sHeadSha": listLines[0].strip(),
        "iAhead": iAhead,
        "iBehind": iBehind,
    }


def fdictProbePushOutcome(
    connectionDocker, sContainerId, sWorkspace=S_CONTAINER_WORKSPACE,
    iAttempts=3, fDelaySeconds=2.0,
):
    """Probe whether an interrupted push actually reached the upstream.

    Used when the host-side ``docker exec`` for a push raised (e.g. a
    read timeout) while the push may still have completed inside the
    container. Retries a bounded number of times because the original
    exec can still be finishing. ``bPushLanded`` is True only when a
    conclusive probe shows zero commits ahead of ``@{upstream}`` —
    the probe never fabricates success.
    """
    dictProbe = {
        "bProbeConclusive": False, "bPushLanded": False,
        "sHeadSha": "", "iAhead": -1, "iBehind": -1,
    }
    for iAttempt in range(max(1, iAttempts)):
        if iAttempt > 0:
            time.sleep(fDelaySeconds)
        dictOnce = _fdictProbePushOnce(
            connectionDocker, sContainerId, sWorkspace,
        )
        if dictOnce is None:
            continue
        dictProbe.update(dictOnce)
        dictProbe["bProbeConclusive"] = True
        if dictProbe["iAhead"] == 0:
            dictProbe["bPushLanded"] = True
            return dictProbe
    return dictProbe


def _ftSplitShaAndCommitDate(sLine):
    """Split one 'sha date' git log line; '-' placeholders become ''."""
    listParts = (sLine or "").strip().split()
    sSha = listParts[0] if listParts else ""
    sDate = listParts[1] if len(listParts) > 1 else ""
    if sSha == "-":
        sSha = ""
    if sDate == "-":
        sDate = ""
    return (sSha, sDate)


def _fdictParseRemoteHeads(sOutput):
    """Parse the three-line remote-heads probe into a response dict."""
    listLines = [
        sLine for sLine in (sOutput or "").strip().splitlines()
        if sLine.strip()
    ]
    if len(listLines) < 3:
        return {"bSuccess": False, "sReason": "unexpected git output"}
    tHead = _ftSplitShaAndCommitDate(listLines[0])
    tUpstream = _ftSplitShaAndCommitDate(listLines[1])
    iAhead, iBehind = _ftParseAheadBehindCounts(listLines[2])
    return {
        "bSuccess": True,
        "sHeadSha": tHead[0], "sHeadCommittedAt": tHead[1],
        "sUpstreamSha": tUpstream[0],
        "sUpstreamCommittedAt": tUpstream[1],
        "iAhead": iAhead if iAhead is not None else 0,
        "iBehind": iBehind if iBehind is not None else 0,
        "sRefreshedAt": gitStatus._fsUtcNow(),
    }


def fdictRemoteHeadsInContainer(
    connectionDocker, sContainerId, sWorkspace=S_CONTAINER_WORKSPACE,
):
    """Return HEAD and upstream shas, committer dates, and ahead/behind.

    Single docker exec so the dashboard can reconcile against the
    freshly fetched remote-tracking refs in one round trip. The
    upstream lines fall back to '-' placeholders when no upstream is
    configured, which parse to empty strings rather than failing.
    """
    sCommand = (
        "cd " + shlex.quote(sWorkspace) + " && "
        "git log -1 --format='%H %cI' HEAD && "
        "{ git log -1 --format='%H %cI' '@{upstream}' 2>/dev/null"
        " || echo '- -'; } && "
        "{ git rev-list --left-right --count 'HEAD...@{upstream}'"
        " 2>/dev/null || printf '0\\t0\\n'; }"
    )
    iExit, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand,
    )
    if iExit != 0:
        return {"bSuccess": False, "sReason": (sOutput or "").strip()}
    return _fdictParseRemoteHeads(sOutput)


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
