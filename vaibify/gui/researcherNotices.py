"""Things the dashboard must tell the researcher that the researcher did not do.

An in-container agent acts through the hub, and the open tab learns
what changed only by polling. A change the tab did not make -- a step
the agent added, a refusal the agent met, a container whose
``vaibify-do`` predates the hub -- has nowhere else to appear, so the
hub queues a notice and the file-status poll delivers it.

Delivery is at-least-once: every poll returns the notices newer than
the last id the tab acknowledged, so a dropped response is repeated
rather than lost. A tab that has acknowledged nothing yet (``-1``) is
given no history, only the newest id, because it has just loaded the
project as it now is.

The hub composes every message itself, from its own operation names
and step labels. Agent-supplied text never reaches a notice, and the
dashboard renders each one through its escaping toast.
"""

__all__ = [
    "S_NOTICE_LEVEL_INFO",
    "S_NOTICE_LEVEL_WARNING",
    "S_NOTICE_LEVEL_ERROR",
    "I_NOTICES_KEPT_PER_CONTAINER",
    "fnQueueResearcherNotice",
    "fnQueueResearcherNoticeOnce",
    "flistNoticesAfter",
    "fiNewestNoticeId",
    "fnAttachNoticesToPoll",
    "fnAnnounceAgentWorkflowChange",
]

S_NOTICE_LEVEL_INFO = "info"
S_NOTICE_LEVEL_WARNING = "warning"
S_NOTICE_LEVEL_ERROR = "error"
I_NOTICES_KEPT_PER_CONTAINER = 50

_S_NOTICES_KEY = "dictResearcherNotices"
_S_NEWEST_ID_KEY = "dictNewestResearcherNoticeId"
_S_ONCE_KEYS_KEY = "setResearcherNoticeOnceKeys"
_T_NOTICE_LEVELS = (
    S_NOTICE_LEVEL_INFO, S_NOTICE_LEVEL_WARNING, S_NOTICE_LEVEL_ERROR,
)


def fnQueueResearcherNotice(dictCtx, sContainerId, sLevel, sMessage):
    """Queue one notice for the container's open tab.

    The list is bounded: a tab that has been away for longer than the
    bound misses the oldest, and it reloads the project on return
    anyway. The id keeps rising across the bound, so the tab's
    acknowledgment stays meaningful.
    """
    if sLevel not in _T_NOTICE_LEVELS:
        raise ValueError(f"Unknown researcher-notice level: {sLevel!r}")
    dictNewestIds = dictCtx.setdefault(_S_NEWEST_ID_KEY, {})
    iNoticeId = dictNewestIds.get(sContainerId, 0) + 1
    dictNewestIds[sContainerId] = iNoticeId
    listNotices = dictCtx.setdefault(_S_NOTICES_KEY, {}).setdefault(
        sContainerId, [],
    )
    listNotices.append({
        "iNoticeId": iNoticeId, "sLevel": sLevel, "sMessage": sMessage,
    })
    del listNotices[:-I_NOTICES_KEPT_PER_CONTAINER]


def fnQueueResearcherNoticeOnce(
    dictCtx, sContainerId, sOnceKey, sLevel, sMessage,
):
    """Queue a notice the first time ``sOnceKey`` is met for a container.

    Once per container per hub process: a condition that every request
    re-detects (a stale in-container client) would otherwise toast on
    every agent call.
    """
    setOnceKeys = dictCtx.setdefault(_S_ONCE_KEYS_KEY, set())
    tKey = (sContainerId, sOnceKey)
    if tKey in setOnceKeys:
        return
    setOnceKeys.add(tKey)
    fnQueueResearcherNotice(dictCtx, sContainerId, sLevel, sMessage)


def fiNewestNoticeId(dictCtx, sContainerId):
    """Return the newest notice id queued for a container, 0 if none."""
    return dictCtx.get(_S_NEWEST_ID_KEY, {}).get(sContainerId, 0)


def flistNoticesAfter(dictCtx, sContainerId, iLastNoticeId):
    """Return the notices newer than the tab's acknowledged id.

    ``-1`` means the tab has acknowledged nothing yet, and gets none.
    """
    if iLastNoticeId < 0:
        return []
    return [
        dict(dictNotice)
        for dictNotice in dictCtx.get(_S_NOTICES_KEY, {}).get(
            sContainerId, [],
        )
        if dictNotice["iNoticeId"] > iLastNoticeId
    ]


def fnAttachNoticesToPoll(dictCtx, sContainerId, dictResponse, iLastNoticeId):
    """Add the undelivered notices and the newest id to a poll response."""
    dictResponse["listResearcherNotices"] = flistNoticesAfter(
        dictCtx, sContainerId, iLastNoticeId,
    )
    dictResponse["iNewestNoticeId"] = fiNewestNoticeId(dictCtx, sContainerId)


def fnAnnounceAgentWorkflowChange(
    dictCtx, sContainerId, requestHttp, sOperationName,
):
    """Tell the open tab that an in-container agent changed its project.

    Called only after a save has committed, so a refused or failed save
    announces nothing. A save the tab made itself is already on its
    screen; only the agent lane -- the one origin of a change the open
    tab did not make -- advances the workflow epoch, which makes the
    tab's next poll carry the new workflow, and queues a notice naming
    the change. One request that saves twice announces once.
    """
    from . import workflowReloadDetector
    from .serverMiddleware import fbRequestRidesAgentLane
    if not fbRequestRidesAgentLane(requestHttp):
        return
    workflowReloadDetector.fnBumpWorkflowEpoch(
        dictCtx, sContainerId,
        workflowReloadDetector.S_EPOCH_ORIGIN_AGENT,
    )
    if getattr(requestHttp.state, "bAgentWorkflowChangeAnnounced", False):
        return
    requestHttp.state.bAgentWorkflowChangeAnnounced = True
    fnQueueResearcherNotice(
        dictCtx, sContainerId, S_NOTICE_LEVEL_INFO,
        "An in-container agent changed this project: "
        + sOperationName[:1].lower() + sOperationName[1:] + ".",
    )
