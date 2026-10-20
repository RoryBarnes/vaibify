"""A run's own writes, handed to the poll's change-detection baseline.

Change detection (``fileStatusManager._fdictDetectChangedFiles``)
compares each declared file's mtime with the baseline the previous
check measured, and while a run is live it moves that baseline without
flagging anything. A check AFTER a run therefore compares the run's
outputs with a baseline taken before them whenever no check ran in
between: on every command-line run (nothing polls during one), and on
the dashboard for every output written after the run's last poll. The
run's own work then read as a modification nobody made, and running
the step again could not clear it, because the next run's check came
before its writes.

The runner measures the mtimes of the outputs of the steps it executed
just before its terminal state is written, and records them IN that
write (``pipelineLogger._fnFinalizeRun``). Whichever check first sees
the finished run -- a dashboard poll or a command's connect -- merges
them into its baseline, once per run. A file changed after the run
still differs from the run's own measurement, so it still reads as
changed.
"""

import logging

from . import workflowManager
from .fileStatusManager import (
    _fdictGetModTimes,
    _fnMergeIntoModTimeBaseline,
    _fsBaselineOwnerOfWorkflow,
    fdictCollectOutputPathsByStep,
)


__all__ = [
    "S_RUN_OUTPUT_MODTIMES_KEY",
    "fdictMeasureRunOutputModTimes",
    "fnAbsorbFinishedRunOutputs",
]

logger = logging.getLogger("vaibify")

S_RUN_OUTPUT_MODTIMES_KEY = "dictRunOutputModTimes"


def fdictMeasureRunOutputModTimes(
    connectionDocker, sContainerId, dictState, dictWorkflow, sWorkflowPath,
):
    """Return the mtimes of the outputs of the steps a finishing run executed.

    The runner measures them just before its terminal state is written
    and carries them IN that write, so no check can learn that the run
    finished without also learning what it wrote. Paths are resolved as
    the poll resolves them -- the same resolver, the same variables --
    so they key into the poll's baseline. A failed measurement answers
    ``{}``, and the run's outputs then read as changed, as they always
    did: never a modification hidden.
    """
    dictPathsByStep = fdictCollectOutputPathsByStep(
        dictWorkflow,
        workflowManager.fdictBuildGlobalVariables(dictWorkflow, sWorkflowPath),
    )
    listPaths = []
    for sStepNumber in dictState.get("dictStepStats") or {}:
        listPaths.extend(dictPathsByStep.get(int(sStepNumber) - 1, []))
    try:
        return _fdictGetModTimes(connectionDocker, sContainerId, listPaths)
    except Exception as error:  # noqa: BLE001 — best effort; fails visible
        logger.warning("Could not measure the run's outputs: %s", error)
        return {}


def fnAbsorbFinishedRunOutputs(
    dictCtx, sContainerId, dictWorkflow, dictPipelineState,
):
    """Overlay a finished run's own output mtimes on the baseline, once.

    Once per run, never again: a later merge would put the run's mtimes
    back over a baseline that has since recorded a change, and the next
    check would report that change a second time. Only the run of the
    project this baseline measures is merged.
    """
    dictPipelineState = dictPipelineState or {}
    dictRunModTimes = dictPipelineState.get(S_RUN_OUTPUT_MODTIMES_KEY)
    sOwner = _fsBaselineOwnerOfWorkflow(dictWorkflow)
    if (not dictRunModTimes or dictPipelineState.get("bRunning")
            or dictPipelineState.get("sWorkflowPath") != sOwner):
        return
    tRun = (sOwner, dictPipelineState.get("sStartTime", ""),
            dictPipelineState.get("sEndTime", ""))
    dictAbsorbed = dictCtx.setdefault("dictAbsorbedRunOutputs", {})
    if dictAbsorbed.get(sContainerId) == tRun:
        return
    dictAbsorbed[sContainerId] = tRun
    _fnMergeIntoModTimeBaseline(
        dictCtx, sContainerId, dictRunModTimes, dictWorkflow)
