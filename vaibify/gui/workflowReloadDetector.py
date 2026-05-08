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
]

import logging

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
    """
    if not sWorkflowPath:
        return _fdictNoChange()
    sPolledMtime = (dictModTimes or {}).get(sWorkflowPath, "")
    if not sPolledMtime:
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
