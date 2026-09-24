"""Each container's live pipeline runs, one slot per project.

A container can host several projects, and each may run its pipeline
while another runs its own: they share the container's CPU and memory,
which is the researcher's decision once they have been warned. Two runs
of the SAME project never overlap -- they would write one run-state
file and one set of outputs -- so a slot is keyed by the project's
repository.

``dictPipelineTasks`` is ``{sContainerId: {sProjectRepoPath: task}}``.
A registered task carries the project it runs (``sProjectRepoPath``,
``sWorkflowName``, ``sWorkflowPath``, ``dictWorkflow``) and the run's
process marker (``sRunId``), so a Stop sweeps that run's processes and
a refusal names that project, whichever project the dashboard has open.

Task ownership is a MUTABLE ``iOwnerGeneration`` field on the task
itself, retagged in place by a host transfer (design §2.3) -- never a
parallel ``{id: generation}`` map, which turns ambiguous when an old
completion callback fires after a transfer.
"""

import logging

from . import workflowManager

__all__ = [
    "S_ACKNOWLEDGE_CONCURRENT_RUN_FIELD",
    "S_REFUSAL_CONCURRENT_RUN",
    "S_JOINABLE_PIPELINE_WORK",
    "fdictBuildConcurrentRunNotice",
    "fdictReadContainerLimits",
    "fnRegisterRun",
    "flistLiveRuns",
    "ftaskLiveRunOfProject",
    "fbProjectRunIsLive",
]

logger = logging.getLogger("vaibify")

# The run frame's acknowledgment that the researcher (or the agent's
# CLI, on its behalf) has been told other projects are running here.
S_ACKNOWLEDGE_CONCURRENT_RUN_FIELD = "bAcknowledgeConcurrentRun"
S_REFUSAL_CONCURRENT_RUN = "concurrentRun"
# The carrier's joinable kind for pipeline runs: runs of different
# projects share ONE durable record per container (commitCarrier).
S_JOINABLE_PIPELINE_WORK = "pipeline-runs"
I_BYTES_PER_GIGABYTE = 1024 ** 3
F_NANO_CPUS_PER_CPU = 1e9


def fnRegisterRun(
    dictPipelineTasks, sContainerId, taskRun, iOwnerGeneration=1,
    dictWorkflow=None, sRunId="",
):
    """Store a run in its project's slot; evict it when it finishes.

    Without the done-callback, finished tasks would linger forever -- a
    leak proportional to the number of runs over a container's life.
    The callback drops the slot only while it still holds THIS task, so
    a newer run of the same project is never evicted by an older one.
    """
    dictWorkflow = dictWorkflow or {}
    taskRun.iOwnerGeneration = iOwnerGeneration
    taskRun.dictWorkflow = dictWorkflow
    taskRun.sRunId = sRunId
    taskRun.sWorkflowPath = dictWorkflow.get(
        workflowManager.S_LOADED_FROM_KEY, "",
    )
    taskRun.sProjectRepoPath = dictWorkflow.get("sProjectRepoPath", "")
    taskRun.sWorkflowName = dictWorkflow.get("sWorkflowName", "")
    dictSlots = dictPipelineTasks.setdefault(sContainerId, {})
    dictSlots[taskRun.sProjectRepoPath] = taskRun

    def fnEvictOnDone(taskCompleted):
        logger.debug(
            "Pipeline run for %s (%s) finished under owner generation %s",
            sContainerId, taskCompleted.sProjectRepoPath,
            getattr(taskCompleted, "iOwnerGeneration", 0),
        )
        dictLiveSlots = dictPipelineTasks.get(sContainerId) or {}
        if dictLiveSlots.get(taskCompleted.sProjectRepoPath) is taskCompleted:
            dictLiveSlots.pop(taskCompleted.sProjectRepoPath, None)
        if not dictLiveSlots:
            dictPipelineTasks.pop(sContainerId, None)
    taskRun.add_done_callback(fnEvictOnDone)


def flistLiveRuns(dictPipelineTasks, sContainerId):
    """Return every live run in the container, in no particular order."""
    dictSlots = (dictPipelineTasks or {}).get(sContainerId) or {}
    return [taskRun for taskRun in dictSlots.values() if not taskRun.done()]


def ftaskLiveRunOfProject(dictPipelineTasks, sContainerId, sProjectRepoPath):
    """Return the project's live run in the container, or None."""
    dictSlots = (dictPipelineTasks or {}).get(sContainerId) or {}
    taskRun = dictSlots.get(sProjectRepoPath or "")
    if taskRun is None or taskRun.done():
        return None
    return taskRun


def fbProjectRunIsLive(dictPipelineTasks, sContainerId, sProjectRepoPath):
    """Return True while the project has a run in flight."""
    return ftaskLiveRunOfProject(
        dictPipelineTasks, sContainerId, sProjectRepoPath,
    ) is not None


def fdictReadContainerLimits(connectionDocker, sContainerId):
    """Return ``{"fCpuLimit", "fMemoryGigabytes"}`` for the container.

    ``None`` for a limit the container does not set (it may then use
    the whole Docker VM) or that could not be read. A container's
    reported core count is NOT its limit: under a CPU quota ``nproc``
    still counts the VM's cores, which is why a warning names the quota.
    """
    dictLimits = {"fCpuLimit": None, "fMemoryGigabytes": None}
    try:
        container = connectionDocker.fcontainerGetById(sContainerId)
        container.reload()
        dictHostConfig = dict(container.attrs["HostConfig"])
        iNanoCpus = int(dictHostConfig.get("NanoCpus") or 0)
        iQuota = int(dictHostConfig.get("CpuQuota") or 0)
        iPeriod = int(dictHostConfig.get("CpuPeriod") or 0)
        iMemoryBytes = int(dictHostConfig.get("Memory") or 0)
    except Exception as error:  # noqa: BLE001 -- a warning degrades, never fails
        logger.warning("Could not read limits of %s: %s", sContainerId, error)
        return dictLimits
    if iNanoCpus > 0:
        dictLimits["fCpuLimit"] = iNanoCpus / F_NANO_CPUS_PER_CPU
    elif iQuota > 0 and iPeriod > 0:
        dictLimits["fCpuLimit"] = iQuota / iPeriod
    if iMemoryBytes > 0:
        dictLimits["fMemoryGigabytes"] = iMemoryBytes / I_BYTES_PER_GIGABYTE
    return dictLimits


def fdictBuildConcurrentRunNotice(listOtherRuns, dictLimits):
    """Return what a researcher is told before a run joins others.

    Names every running project and the limits the runs will share.
    The dashboard shows it in a confirmation; ``vaibify-do`` prints it.
    """
    listProjects = [
        {
            "sWorkflowName": getattr(taskRun, "sWorkflowName", ""),
            "sProjectRepoPath": getattr(taskRun, "sProjectRepoPath", ""),
        }
        for taskRun in listOtherRuns
    ]
    return {
        "iRunningProjectCount": len(listProjects),
        "listRunningProjects": listProjects,
        "dictContainerLimits": dict(dictLimits),
        "sMessage": _fsDescribeConcurrentRun(listProjects, dictLimits),
    }


def _fsDescribeConcurrentRun(listProjects, dictLimits):
    """Return the plain-language warning for a concurrent run."""
    listNames = [
        dictProject["sWorkflowName"] or dictProject["sProjectRepoPath"]
        for dictProject in listProjects
    ]
    sCount = (
        "1 other project is" if len(listNames) == 1
        else f"{len(listNames)} other projects are"
    )
    return (
        f"{sCount} already running a pipeline in this container "
        f"({', '.join(listNames)}). The runs will share "
        f"{_fsDescribeLimits(dictLimits)}, so each will run more slowly, "
        "and a script that sizes itself by the machine's core count "
        "(for example 'all cores but one') will oversubscribe: inside a "
        "container the reported core count is not the CPU limit."
    )


def _fsDescribeLimits(dictLimits):
    """Return "1 CPU and 5 GB of memory", or what is known of it."""
    fCpu = dictLimits.get("fCpuLimit")
    fMemory = dictLimits.get("fMemoryGigabytes")
    sCpu = (
        f"{fCpu:g} CPU" + ("" if fCpu == 1 else "s") if fCpu
        else "all the CPUs Docker gives this container"
    )
    sMemory = (
        f"{fMemory:.3g} GB of memory" if fMemory
        else "the memory Docker gives this container"
    )
    return f"{sCpu} and {sMemory}"
