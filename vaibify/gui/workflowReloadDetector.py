"""Detect out-of-band edits to workflow.json and reload the host cache.

The host caches a parsed copy of every active ``workflow.json`` in
``dictCtx["workflows"][sContainerId]`` at connect time, then mutates +
writes via ``dictCtx["save"]`` for every UI-driven edit. Anything that
modifies ``workflow.json`` outside that channel — the in-container
agent, ``vim``, ``git pull`` — leaves the cache stale, which violates
the dashboard's ground-truth contract.

Detection compares a sha256 **content fingerprint** of the file
(collected in the same exec batch as the polling stat) against the
host's own last-write fingerprint. The previous design compared
whole-second mtimes, which silently swallowed any agent edit landing
in the same second as a backend save; content comparison has no such
window, and the baseline is computed host-side from the exact bytes
written (save) or read (load), so a flaky stat can never poison it.

Every cache replacement bumps the per-container **workflow epoch**.
The file-status route reconciles each client against that epoch and
re-sends the workflow to any client whose epoch is stale, so delivery
is at-least-once per client — a dropped response or a second polling
client can no longer permanently strand a dashboard on a stale
workflow (the pre-epoch design consumed the change on whichever
single response observed it).
"""

__all__ = [
    "fnRecordSelfWriteFingerprint",
    "fdictMaybeReloadWorkflow",
    "fdictDetectNewlyAvailableWorkflows",
    "fnBumpWorkflowEpoch",
    "fiGetWorkflowEpoch",
]

import logging
import shlex

from . import workflowManager


logger = logging.getLogger("vaibify")


def fnRecordSelfWriteFingerprint(dictCtx, sContainerId, sFingerprint):
    """Record the fingerprint of content the host itself wrote or read.

    Called with :func:`workflowManager.fsComputeWorkflowFingerprint`
    of the just-saved dict after a UI-driven write, and with the
    loader's ``_sSourceFingerprint`` at connect time. The poller
    compares the polled file fingerprint against this stored value
    and skips reloading when they match — that's the host's own
    content. An empty fingerprint is never recorded: absence of
    evidence must not overwrite a known-good baseline.
    """
    if not sFingerprint:
        return
    dictFingerprints = _fdictGetSelfWriteMap(dictCtx)
    dictFingerprints[sContainerId] = sFingerprint


def fnBumpWorkflowEpoch(dictCtx, sContainerId):
    """Increment the per-container workflow epoch.

    Bumped on every out-of-band cache replacement so the file-status
    route can re-send the workflow to any client whose last-applied
    epoch is stale — per-client reconciliation instead of a one-shot
    notification.
    """
    dictEpochs = dictCtx.setdefault("dictWorkflowEpochs", {})
    dictEpochs[sContainerId] = dictEpochs.get(sContainerId, 0) + 1


def fiGetWorkflowEpoch(dictCtx, sContainerId):
    """Return the current workflow epoch for a container (0 when untouched)."""
    return dictCtx.get("dictWorkflowEpochs", {}).get(sContainerId, 0)


def fdictMaybeReloadWorkflow(
    dictCtx, sContainerId, sWorkflowPath, dictModTimes,
    sPolledFingerprint="",
):
    """Re-read workflow.json from disk if its content moved out-of-band.

    Returns ``{"bReplaced": bool, "dictWorkflow": dict | None,
    "sError": str | None}``. The caller forwards these into the
    file-status response so the frontend can decide whether to
    re-render. On replace, ``dictCtx["workflows"][sContainerId]`` is
    updated to the freshly-loaded dict, the stored last-write
    fingerprint is set to the loaded bytes' fingerprint, and the
    workflow epoch is bumped.

    ``dictModTimes`` still carries the existence signal: the host
    shell wraps ``stat ... 2>/dev/null || true``, so a docker-exec
    timeout or flaky stream can produce an empty batch even when
    every file is fine. When the batch returns other paths but not
    the workflow, that disambiguates a real missing-file event from a
    hiccup. When the batch is empty entirely, we issue one direct
    existence probe to break the tie. An empty ``sPolledFingerprint``
    alongside a successful stat is a hash-collection flake — report
    no change and let the next poll retry rather than firing a
    spurious reload.
    """
    if not sWorkflowPath:
        return _fdictNoChange()
    dictPolled = dictModTimes or {}
    if not dictPolled.get(sWorkflowPath, ""):
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
    if not sPolledFingerprint:
        return _fdictNoChange()
    dictFingerprints = _fdictGetSelfWriteMap(dictCtx)
    sKnownFingerprint = dictFingerprints.get(sContainerId)
    if sPolledFingerprint == sKnownFingerprint:
        return _fdictNoChange()
    if not sKnownFingerprint:
        # No trusted baseline yet — the container is freshly connected
        # or a prior flake left the baseline unrecorded. Absence of a
        # baseline is not evidence of an out-of-band edit, so seed it
        # from the current fingerprint and report no change rather
        # than firing a spurious reload (the cache was already loaded
        # fresh at connect time, which also seeds this baseline).
        dictFingerprints[sContainerId] = sPolledFingerprint
        return _fdictNoChange()
    return _fdictPerformReload(
        dictCtx, sContainerId, sWorkflowPath, sPolledFingerprint,
    )


def _fdictPerformReload(
    dictCtx, sContainerId, sWorkflowPath, sPolledFingerprint,
):
    """Load the workflow, update cache + baseline + epoch, return result."""
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
    dictFingerprints = _fdictGetSelfWriteMap(dictCtx)
    dictFingerprints[sContainerId] = (
        dictWorkflow.get("_sSourceFingerprint") or sPolledFingerprint
    )
    fnBumpWorkflowEpoch(dictCtx, sContainerId)
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
    """Return the per-container last-write fingerprint map, creating if absent."""
    dictFingerprints = dictCtx.get("lastSelfWriteFingerprints")
    if dictFingerprints is None:
        dictFingerprints = {}
        dictCtx["lastSelfWriteFingerprints"] = dictFingerprints
    return dictFingerprints


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
