"""Apply a saved CPU or memory limit to the running container where that is safe.

A settings save writes ``vaibify.yml`` first. Then, for each limit field
the save changed, the running container's limits are read with a fresh
inspect and the change is PLANNED against them, never against the file
(``resourceLimits.flistPlanLimitChanges``): a memory raise with a known
swap limit and any finite CPU change are applied live; anything that
could kill a process, or that Docker cannot do to a running container,
waits for the next start and says so.

The live half issues ONE ``docker update`` through the lifecycle gateway
(``containerManager.fnApplyResourceChangesLive``) under the per-container
mutation lock, against the container id the inspect just named, and then
RE-INSPECTS that id. The re-inspection, not the exit code, decides each
field's outcome. The stop route holds no such lock (its own comment
records why), so a stop that lands mid-update is not serialized against
it; the pinned id means such an update can only ever touch the container
that was inspected, and a re-inspect that cannot find it reports failed.
"""

import asyncio

from vaibify.config import resourceLimits

from . import sessionLifecycle

__all__ = [
    "flistApplyChangedLimits",
]


async def flistApplyChangedLimits(
    appState, sContainerName, listChangedFields, configSaved,
):
    """Return one outcome per changed field, applying the live-safe ones."""
    from vaibify.docker import containerManager
    if not listChangedFields:
        return []
    dictInspect = await asyncio.to_thread(
        containerManager.fjsonInspectContainer, sContainerName)
    if not (dictInspect.get("State") or {}).get("Running"):
        return resourceLimits.flistDescribeNextStartOutcomes(
            listChangedFields, configSaved)
    listPlan = [
        dictEntry for dictEntry in resourceLimits.flistPlanLimitChanges(
            resourceLimits.fdictParseRunningLimits(
                dictInspect.get("HostConfig") or {}),
            resourceLimits.fdictResolveDesiredLimits(configSaved))
        if dictEntry["sField"] in listChangedFields
    ]
    dictRunningAfter, sFailure = await _ftApplyLiveChanges(
        appState, sContainerName, dictInspect, listPlan)
    return [
        resourceLimits.fdictDescribeLimitOutcome(
            dictEntry, dictRunningAfter, configSaved, sFailure)
        for dictEntry in listPlan
    ]


async def _ftApplyLiveChanges(appState, sContainerName, dictInspect, listPlan):
    """Return ``(dictRunningAfter, sFailure)`` after any live update."""
    from vaibify.docker import containerManager
    listLive = [
        dictEntry for dictEntry in listPlan
        if dictEntry["sAction"] == resourceLimits.S_ACTION_APPLY_LIVE]
    dictRunningBefore = resourceLimits.fdictParseRunningLimits(
        dictInspect.get("HostConfig") or {})
    if not listLive:
        return dictRunningBefore, ""
    sContainerId = dictInspect.get("Id", "")
    sFailure = ""
    async with sessionLifecycle.flockContainerMutationForAppState(
        appState, sContainerName,
    ):
        try:
            await asyncio.to_thread(
                containerManager.fnApplyResourceChangesLive,
                sContainerId, listLive)
        except Exception as error:  # noqa: BLE001 -- reported per field
            sFailure = _fsExplainUpdateFailure(sContainerName, error)
        dictAfter = await asyncio.to_thread(
            containerManager.fjsonInspectContainer, sContainerId)
    return _fdictRunningLimitsOf(dictAfter), sFailure


def _fdictRunningLimitsOf(dictInspect):
    """Return tagged running limits, unknown when the inspect failed."""
    if not dictInspect:
        return resourceLimits.fdictUnknownRunningLimits()
    return resourceLimits.fdictParseRunningLimits(
        dictInspect.get("HostConfig") or {})


def _fsExplainUpdateFailure(sContainerName, error):
    """Return Docker's refusal in researcher terms, keeping its words."""
    from vaibify.docker.dockerErrorDiagnosis import (
        fsExplainContainerOperationFailure,
    )
    return fsExplainContainerOperationFailure(
        "The limit update", sContainerName, str(error)).rstrip(".")
