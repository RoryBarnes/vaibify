"""Route handler context: the typed dict wrapper and shared route helpers.

Provides attribute access with clear types so that route handlers
can use ``dictCtx.docker`` instead of ``dictCtx["docker"]``, making
dependencies explicit and enabling IDE auto-completion. The class
also acts as a dict for backward compatibility — existing code using
``dictCtx["key"]`` continues to work unchanged.

This module is also the home for helpers shared by MULTIPLE route
modules (``ffilesForWorkflow``, the post-push verify refresh): route
modules must not import siblings
(``testRouteModulesDoNotImportSiblings``), so cross-route logic
lives here, beneath them.
"""

__all__ = [
    "RouteContext",
    "fdictRunRemoteVerifyBlocking",
    "ffilesForWorkflow",
    "fnRecordAttributionEvent",
    "fsRefreshVerifyCacheAfterPush",
]

import asyncio
import logging

logger = logging.getLogger("vaibify")


def ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow):
    """Return the repo-file adapter for a workflow's project repo.

    Production contexts carry a ``files`` callable that builds a
    ``ContainerRepoFiles`` rooted at the workflow's project repo —
    ``sProjectRepoPath`` is a container path, so container IO is the
    only honest reader. Legacy and test contexts without the callable
    fall back to a host adapter over the raw path string, preserving
    host-clone semantics.
    """
    fnFiles = dictCtx.get("files")
    if fnFiles is not None:
        return fnFiles(sContainerId)
    from vaibify.reproducibility.repoFiles import ffilesEnsureRepoFiles
    return ffilesEnsureRepoFiles(
        (dictWorkflow or {}).get("sProjectRepoPath") or "",
    )


def fnRecordAttributionEvent(
    dictCtx, sContainerId, dictWorkflow, sChannel, sDetail,
):
    """Append a Supervised-mode attribution event from a route.

    Cheap no-op when supervision is off; failures are logged and
    swallowed — attribution must never break the action it records.
    """
    from . import attributionLog
    if not attributionLog.fbSupervisionEnabled(dictWorkflow):
        return
    try:
        attributionLog.fnAppendAttributionEvent(
            ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow),
            dictWorkflow, sChannel, "hub", sDetail,
        )
    except Exception as exc:  # noqa: BLE001 — never break the route
        logger.warning("Attribution event append failed: %s", exc)


def fdictRunRemoteVerifyBlocking(dictWorkflow, sService, filesRepo):
    """Run the synchronous verify call against the remote and return status."""
    from vaibify.reproducibility import scheduledReverify
    dictStatus = scheduledReverify.fdictVerifyRemoteService(
        filesRepo, dictWorkflow, sService,
    )
    scheduledReverify.fnWriteSyncStatus(filesRepo, dictStatus)
    return dictStatus


async def fsRefreshVerifyCacheAfterPush(
    dictCtx, sContainerId, dictWorkflow, sService,
):
    """Re-verify one service's remote right after a successful push.

    Shared by every push route (GitHub sync, Repos-panel staged and
    per-file pushes). The push already proved the network path to
    the service, so one verify round-trip is cheap, and it spares
    the researcher a manual "refresh remote status" click: the L2
    cells read the verify cache, which would otherwise stay stale
    for up to 24 hours after the very push that satisfied them.
    Best-effort by design — a failed verify leaves the cache stale
    (the cells keep reading unknown, never a fake green) and must
    not fail the push response. Returns "" on success and a short
    redaction-safe warning string on failure, so the push toast can
    tell the researcher the L2 check did not run instead of leaving
    a silent gap between "pushed" and "still unknown". A service the
    workflow never configured in ``dictRemotes`` is skipped without
    a warning — there is nothing to verify against, and nagging a
    researcher who only uses plain git pushes would teach them to
    ignore real warnings.
    """
    if not ((dictWorkflow or {}).get("dictRemotes") or {}).get(sService):
        return ""
    try:
        filesRepo = ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow)
        await asyncio.to_thread(
            fdictRunRemoteVerifyBlocking, dictWorkflow, sService,
            filesRepo,
        )
    except Exception as error:
        logger.warning(
            "Post-push %s verify failed; cached remote status stays "
            "stale until the next refresh.", sService, exc_info=True,
        )
        return _fsPostPushVerifyWarning(sService, error)
    return ""


def _fsPostPushVerifyWarning(sService, error):
    """Actionable, redaction-safe summary of a failed post-push verify.

    Never embeds the raw exception text: verify errors can carry
    remote URLs, and URLs can carry credentials. Known causes get a
    specific remedy; everything else points at the hub log.
    """
    if (isinstance(error, FileNotFoundError)
            and "manifest" in str(error).lower()):
        return (
            "Pushed, but the " + sService + " status check needs "
            "MANIFEST.sha256, which does not exist yet — vaibify "
            "generates it when the workflow reaches Level 1."
        )
    return (
        "Pushed, but the " + sService + " status check failed — "
        "the Published (L2) cells stay unknown. See the hub log."
    )


class RouteContext:
    """Typed context object passed to all route handlers.

    Wraps the underlying dict so both attribute access and dict
    access work identically.  New code should prefer attributes;
    old code using bracket notation keeps working.
    """

    def __init__(self, dictRaw):
        object.__setattr__(self, "_dictRaw", dictRaw)

    # --- typed attribute access ---

    @property
    def docker(self):
        """Docker connection for executing container commands."""
        return self._dictRaw["docker"]

    @property
    def workflows(self):
        """Dict of {sContainerId: dictWorkflow} cache."""
        return self._dictRaw["workflows"]

    @property
    def paths(self):
        """Dict of {sContainerId: sWorkflowPath} cache."""
        return self._dictRaw["paths"]

    @property
    def terminals(self):
        """Dict of {sSessionId: TerminalSession} cache."""
        return self._dictRaw["terminals"]

    @property
    def containerUsers(self):
        """Dict of {sContainerId: sUsername} cache."""
        return self._dictRaw["containerUsers"]

    @property
    def pipelineTasks(self):
        """Dict of {sContainerId: asyncio.Task} for running pipelines."""
        return self._dictRaw["pipelineTasks"]

    @property
    def sSessionToken(self):
        """Session token for WebSocket origin validation."""
        return self._dictRaw.get("sSessionToken", "")

    def require(self):
        """Raise if Docker is not available."""
        return self._dictRaw["require"]()

    def save(self, sContainerId, dictWorkflow):
        """Persist workflow to container."""
        return self._dictRaw["save"](sContainerId, dictWorkflow)

    def variables(self, sContainerId):
        """Build variable substitution dict for a container."""
        return self._dictRaw["variables"](sContainerId)

    def workflowDir(self, sContainerId):
        """Return the workflow directory path for a container."""
        return self._dictRaw["workflowDir"](sContainerId)

    def files(self, sContainerId):
        """Return a ContainerRepoFiles rooted at the workflow's project repo."""
        return self._dictRaw["files"](sContainerId)

    # --- dict-compatible access for backward compatibility ---

    def __getitem__(self, sKey):
        return self._dictRaw[sKey]

    def __setitem__(self, sKey, value):
        self._dictRaw[sKey] = value

    def __contains__(self, sKey):
        return sKey in self._dictRaw

    def __delitem__(self, sKey):
        del self._dictRaw[sKey]

    def get(self, sKey, default=None):
        """Dict-compatible get with default."""
        return self._dictRaw.get(sKey, default)

    def setdefault(self, sKey, default=None):
        """Dict-compatible setdefault."""
        return self._dictRaw.setdefault(sKey, default)

    def pop(self, sKey, *args):
        """Dict-compatible pop."""
        return self._dictRaw.pop(sKey, *args)
