"""Detect out-of-band edits to workflow.json and reload the host cache.

The host caches a parsed copy of every active ``workflow.json`` in
``dictCtx["workflows"][sContainerId]`` at connect time, then mutates +
writes via ``dictCtx["save"]`` for every UI-driven edit. Anything that
modifies ``workflow.json`` outside that channel — the in-container
agent, ``vim``, ``git pull`` — leaves the cache stale, which violates
the dashboard's ground-truth contract.

This module wires the file's mtime into the existing polling batch and
reloads the cache when it diverges from the host's own last-write
mtime, while suppressing spurious reloads triggered by UI saves.
"""

__all__ = [
    "fnRecordSelfWriteMtime",
    "fdictMaybeReloadWorkflow",
    "fdictDetectNewlyAvailableWorkflows",
]

import logging
import shlex

from . import workflowManager


logger = logging.getLogger("vaibify")


def fnRecordSelfWriteMtime(dictCtx, sContainerId, sPath):
    """Record the current mtime of sPath so the next poll won't re-trigger.

    Called from ``fnSave`` immediately after a UI-driven write.
    The poller compares the polled mtime against this stored value and
    skips reloading when they match — that's the host's own write.
    """
    if not sPath:
        return
    dictMtimes = _fdictGetSelfWriteMap(dictCtx)
    sMtime = _fsStatMtime(dictCtx["docker"], sContainerId, sPath)
    dictMtimes[sContainerId] = sMtime


def fdictMaybeReloadWorkflow(
    dictCtx, sContainerId, sWorkflowPath, dictModTimes,
):
    """Re-read workflow.json from disk if its mtime moved out-of-band.

    Returns ``{"bReplaced": bool, "dictWorkflow": dict | None,
    "sError": str | None}``. The caller forwards these into the
    file-status response so the frontend can decide whether to
    re-render. On replace, ``dictCtx["workflows"][sContainerId]`` is
    updated to the freshly-loaded dict and the stored last-write
    mtime is bumped to silence the next poll.

    An empty ``dictModTimes`` is ambiguous: the host shell wraps
    ``stat ... 2>/dev/null || true``, so a docker-exec timeout or
    flaky stream can produce an empty batch even when every file is
    fine. When the batch returns other paths but not the workflow,
    that disambiguates a real missing-file event from a hiccup.
    When the batch is empty entirely, we issue one direct existence
    probe to break the tie — minimal-workflow deletions are caught
    without the false-alarm tax on hiccupping polls.
    """
    if not sWorkflowPath:
        return _fdictNoChange()
    dictPolled = dictModTimes or {}
    sPolledMtime = dictPolled.get(sWorkflowPath, "")
    if not sPolledMtime:
        if dictPolled:
            return _fdictReloadFailure(
                "workflow.json missing from container"
            )
        if _fbWorkflowExistsInContainer(
            dictCtx["docker"], sContainerId, sWorkflowPath,
        ):
            return _fdictNoChange()
        return _fdictReloadFailure(
            "workflow.json missing from container"
        )
    dictMtimes = _fdictGetSelfWriteMap(dictCtx)
    if sPolledMtime == dictMtimes.get(sContainerId):
        return _fdictNoChange()
    return _fdictPerformReload(
        dictCtx, sContainerId, sWorkflowPath, sPolledMtime,
    )


def _fdictPerformReload(
    dictCtx, sContainerId, sWorkflowPath, sPolledMtime,
):
    """Load the workflow, update cache + last-write mtime, return result."""
    try:
        dictWorkflow = workflowManager.fdictLoadWorkflowFromContainer(
            dictCtx["docker"], sContainerId, sWorkflowPath,
        )
    except Exception as error:
        logger.warning(
            "Out-of-band workflow reload failed for %s: %s",
            sContainerId, error,
        )
        return _fdictReloadFailure(str(error))
    _fnApplyProjectRepoPath(
        dictCtx, sContainerId, sWorkflowPath, dictWorkflow,
    )
    dictCtx["workflows"][sContainerId] = dictWorkflow
    dictMtimes = _fdictGetSelfWriteMap(dictCtx)
    dictMtimes[sContainerId] = sPolledMtime
    logger.info(
        "Workflow reloaded out-of-band for container=%s path=%s",
        sContainerId, sWorkflowPath,
    )
    return {
        "bReplaced": True,
        "dictWorkflow": dictWorkflow,
        "sError": None,
    }


def _fnApplyProjectRepoPath(
    dictCtx, sContainerId, sWorkflowPath, dictWorkflow,
):
    """Override sProjectRepoPath via the in-container git probe.

    Mirrors connect-time semantics in ``pipelineServer.fdictHandleConnect``
    so the reloaded workflow has the same project-repo resolution as
    the originally-loaded one.
    """
    from . import containerGit
    dictWorkflow["sProjectRepoPath"] = (
        containerGit.fsDetectProjectRepoInContainer(
            dictCtx["docker"], sContainerId, sWorkflowPath,
        )
    )


def fdictDetectNewlyAvailableWorkflows(dictCtx, sContainerId):
    """Discover workflow.json files in the container, flag changes since last poll.

    Returns ``{"listWorkflows": [...], "bChangedSinceLastPoll": bool,
    "listNewWorkflowPaths": [str, ...]}``. The first poll seeds the
    cache and reports ``bChangedSinceLastPoll=False`` so initial
    connect doesn't trip a false positive. Subsequent polls compare
    the current path-set against the previous one to surface
    additions and disappearances; only additions populate
    ``listNewWorkflowPaths`` so the caller can toast the newcomers.
    """
    listWorkflows = workflowManager.flistFindWorkflowsInContainer(
        dictCtx["docker"], sContainerId,
    )
    setCurrent = {dictWf["sPath"] for dictWf in listWorkflows}
    dictPrevious = _fdictGetDiscoveredMap(dictCtx)
    bSeeded = sContainerId in dictPrevious
    setPrevious = dictPrevious.get(sContainerId, set())
    listNew = sorted(setCurrent - setPrevious) if bSeeded else []
    bChanged = bSeeded and setCurrent != setPrevious
    dictPrevious[sContainerId] = setCurrent
    return {
        "listWorkflows": listWorkflows,
        "bChangedSinceLastPoll": bChanged,
        "listNewWorkflowPaths": listNew,
    }


def _fdictGetDiscoveredMap(dictCtx):
    """Return the per-container last-discovery set map, creating if absent."""
    dictMap = dictCtx.get("lastDiscoveredWorkflows")
    if dictMap is None:
        dictMap = {}
        dictCtx["lastDiscoveredWorkflows"] = dictMap
    return dictMap


def _fdictGetSelfWriteMap(dictCtx):
    """Return the per-container last-write mtime map, creating if absent."""
    dictMtimes = dictCtx.get("lastSelfWriteMtimes")
    if dictMtimes is None:
        dictMtimes = {}
        dictCtx["lastSelfWriteMtimes"] = dictMtimes
    return dictMtimes


def _fsStatMtime(connectionDocker, sContainerId, sPath):
    """Return the mtime string for a single path, or empty on miss."""
    from .fileStatusManager import _fdictGetModTimes
    dictResult = _fdictGetModTimes(
        connectionDocker, sContainerId, [sPath],
    )
    return dictResult.get(sPath, "")


def _fbWorkflowExistsInContainer(connectionDocker, sContainerId, sPath):
    """Return True when ``sPath`` exists in the container.

    Used to disambiguate "the stat batch hiccupped" from "the file
    was genuinely deleted" when the polled-mtimes dict came back
    empty. Wraps ``test -e`` with an explicit ``exists:N`` marker so
    the answer is robust against stray stderr noise and the
    catch-all empty-output fallthrough in flaky docker-exec streams.
    """
    sCmd = (
        f"test -e {shlex.quote(sPath)} && echo exists:1 || echo exists:0"
    )
    _iExit, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCmd,
    )
    return "exists:1" in (sOutput or "")


def _fdictNoChange():
    """Return the no-replace result dict."""
    return {"bReplaced": False, "dictWorkflow": None, "sError": None}


def _fdictReloadFailure(sError):
    """Return the failure result dict (no replace, error surfaced)."""
    return {
        "bReplaced": False,
        "dictWorkflow": None,
        "sError": sError,
    }
