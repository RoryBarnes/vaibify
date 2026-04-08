"""Pure utility functions for pipeline execution (leaf module).

This module has ZERO intra-package imports. It exists to break circular
dependency cycles between pipelineRunner and its extracted submodules.
"""

import time


__all__ = [
    "fsShellQuote",
    "fsComputeStepLabel",
    "fnClearOutputModifiedFlags",
]


def fsShellQuote(sValue):
    """Safely quote a value for use in a shell command.

    Wraps the value in single quotes and escapes any embedded single
    quotes with the standard '\\'' idiom, preventing shell injection.
    """
    return "'" + sValue.replace("'", "'\\''") + "'"


def fsComputeStepLabel(dictWorkflow, iStepNumber):
    """Return the display label (A01, I01) for a 1-based step number."""
    iIndex = iStepNumber - 1
    listSteps = dictWorkflow.get("listSteps", [])
    if iIndex < 0 or iIndex >= len(listSteps):
        return f"{iStepNumber:02d}"
    bInteractive = listSteps[iIndex].get("bInteractive", False)
    sPrefix = "I" if bInteractive else "A"
    iCount = 0
    for iPos in range(iIndex + 1):
        bSameType = listSteps[iPos].get(
            "bInteractive", False) == bInteractive
        if bSameType:
            iCount += 1
    return f"{sPrefix}{iCount:02d}"


def _fnRecordRunStats(
    dictStep, sStartTimestamp, fStartTime, fCpuTime=0.0,
):
    """Store timing information in the step's run stats."""
    dictStep["dictRunStats"] = {
        "sLastRun": sStartTimestamp,
        "fWallClock": round(time.time() - fStartTime, 1),
        "fCpuTime": round(fCpuTime, 1),
    }


def _fdictBuildWorkflowVars(dictWorkflow):
    """Extract variable substitution dict from workflow metadata."""
    return {
        "sPlotDirectory": dictWorkflow.get("sPlotDirectory", "Plot"),
        "sFigureType": dictWorkflow.get("sFigureType", "pdf"),
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
