"""Pure utility functions for pipeline execution (leaf module).

This module has ZERO intra-package imports. It exists to break circular
dependency cycles between pipelineRunner and its extracted submodules.
"""

import time


__all__ = [
    "fsShellQuote",
    "fsLabelFromStepIndex",
    "fiStepIndexFromLabel",
    "flistStepsWithLabels",
    "fdictWorkflowWithLabels",
    "fdictStepWithLabel",
    "fnClearOutputModifiedFlags",
]


def fsShellQuote(sValue):
    """Safely quote a value for use in a shell command.

    Wraps the value in single quotes and escapes any embedded single
    quotes with the standard '\\'' idiom, preventing shell injection.
    """
    return "'" + sValue.replace("'", "'\\''") + "'"


def fsLabelFromStepIndex(dictWorkflow, iStepIndex):
    """Return the display label (A01, I01) for a 0-based step index.

    Labels are per-type sequential: ``A09`` means the 9th automated
    step, ``I01`` means the 1st interactive step. See ``CLAUDE.md``
    Traps for why this differs from ``listSteps[index]``.
    """
    listSteps = dictWorkflow.get("listSteps", [])
    if iStepIndex < 0 or iStepIndex >= len(listSteps):
        return f"{iStepIndex + 1:02d}"
    bInteractive = listSteps[iStepIndex].get("bInteractive", False)
    sPrefix = "I" if bInteractive else "A"
    iCount = 0
    for iPos in range(iStepIndex + 1):
        bSameType = listSteps[iPos].get(
            "bInteractive", False) == bInteractive
        if bSameType:
            iCount += 1
    return f"{sPrefix}{iCount:02d}"


def fiStepIndexFromLabel(dictWorkflow, sLabel):
    """Return the 0-based index for a step label like ``A09`` or ``I01``.

    Raises ``ValueError`` with a readable message when ``sLabel`` is
    not the expected shape or does not resolve to a step in
    ``dictWorkflow``.
    """
    if not isinstance(sLabel, str):
        raise ValueError(
            f"step label must be a string, got "
            f"{type(sLabel).__name__}"
        )
    sNormalized = sLabel.strip().upper()
    if (len(sNormalized) < 2 or sNormalized[0] not in ("A", "I")
            or not sNormalized[1:].isdigit()):
        raise ValueError(
            f"invalid step label {sLabel!r} — expected "
            f"pattern like 'A09' or 'I01'"
        )
    return _fiResolveLabelWithinType(
        dictWorkflow, sLabel, sNormalized[0], int(sNormalized[1:]),
    )


def _fiResolveLabelWithinType(dictWorkflow, sLabel, sPrefix, iWanted):
    """Walk listSteps and find the iWanted-th step matching sPrefix."""
    bWantInteractive = sPrefix == "I"
    listSteps = dictWorkflow.get("listSteps", [])
    iCount = 0
    for iIndex, dictStep in enumerate(listSteps):
        bInteractive = dictStep.get("bInteractive", False)
        if bInteractive == bWantInteractive:
            iCount += 1
            if iCount == iWanted:
                return iIndex
    sKind = "interactive" if bWantInteractive else "automated"
    raise ValueError(
        f"no step {sLabel!r} — workflow has {iCount} "
        f"{sKind} step(s)"
    )


def flistStepsWithLabels(dictWorkflow):
    """Return listSteps shallow-copied with ``sLabel`` added to each.

    Does not mutate ``dictWorkflow``. The returned list contains
    fresh step dicts so the caller can hand them to a JSON
    serializer without risk of persisting ``sLabel`` into
    ``workflow.json``.
    """
    listSteps = dictWorkflow.get("listSteps", [])
    listOut = []
    for iIndex, dictStep in enumerate(listSteps):
        dictCopy = dict(dictStep)
        dictCopy["sLabel"] = fsLabelFromStepIndex(dictWorkflow, iIndex)
        listOut.append(dictCopy)
    return listOut


def fdictWorkflowWithLabels(dictWorkflow):
    """Return a shallow-copied workflow whose listSteps carry sLabel."""
    dictCopy = dict(dictWorkflow)
    dictCopy["listSteps"] = flistStepsWithLabels(dictWorkflow)
    return dictCopy


def fdictStepWithLabel(dictWorkflow, iStepIndex):
    """Return a shallow copy of the step at iStepIndex with sLabel."""
    dictStep = dict(dictWorkflow["listSteps"][iStepIndex])
    dictStep["sLabel"] = fsLabelFromStepIndex(dictWorkflow, iStepIndex)
    return dictStep


def _fnRecordRunStats(dictStep, fStartTime, fCpuTime=0.0):
    """Store timing information in the step's run stats."""
    dictStep["dictRunStats"] = {
        "fWallClock": round(time.time() - fStartTime, 1),
        "fCpuTime": round(fCpuTime, 1),
    }


def _fdictBuildWorkflowVars(dictWorkflow):
    """Extract variable substitution dict from workflow metadata."""
    return {
        "sPlotDirectory": dictWorkflow.get("sPlotDirectory", "Plot"),
        "sFigureType": dictWorkflow.get("sFigureType", "pdf"),
        "sRepoRoot": dictWorkflow.get("sProjectRepoPath", ""),
    }


def fnClearOutputModifiedFlags(dictWorkflow):
    """Clear modification flags on all steps before a pipeline run."""
    for dictStep in dictWorkflow.get("listSteps", []):
        dictVerification = dictStep.get("dictVerification", {})
        dictVerification.pop("bOutputModified", None)
        dictVerification.pop("listModifiedFiles", None)
        dictVerification.pop("bUpstreamModified", None)
        dictStep["dictVerification"] = dictVerification


async def _fnEmitCommandHeader(fnStatusCallback, sOriginal, sResolved):
    """Emit the command being run, showing resolution if different."""
    await fnStatusCallback(
        {"sType": "output", "sLine": f"$ {sOriginal}"}
    )
    if sResolved != sOriginal:
        await fnStatusCallback(
            {"sType": "output", "sLine": f"  => {sResolved}"}
        )


async def _fnEmitStepResult(fnStatusCallback, iStepNumber, iExitCode):
    """Send a stepPass or stepFail event based on exit code."""
    sType = "stepPass" if iExitCode == 0 else "stepFail"
    await fnStatusCallback({
        "sType": sType, "iStepNumber": iStepNumber,
        "iExitCode": iExitCode,
    })


async def _fnEmitCompletion(fnStatusCallback, iExitCode):
    """Send the final completed or failed event."""
    sResultType = "completed" if iExitCode == 0 else "failed"
    await fnStatusCallback(
        {"sType": sResultType, "iExitCode": iExitCode}
    )


async def _fnEmitBanner(
    fnStatusCallback, iStepNumber, sStepName, sStepLabel=None,
):
    """Emit step banner lines to the status callback."""
    if sStepLabel is None:
        sStepLabel = f"{iStepNumber:02d}"
    sBanner = f"Step {sStepLabel} - {sStepName}"
    sLine = "=" * len(sBanner)
    for sText in ["", sLine, sBanner, sLine, ""]:
        await fnStatusCallback({"sType": "output", "sLine": sText})
