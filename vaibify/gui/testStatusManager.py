"""Test status tracking, result recording, and test file management."""

__all__ = []

import posixpath

from . import workflowManager
from .pipelineUtils import fsShellQuote


def _fsAbsoluteStepDir(sStepDirectory, sProjectRepoPath):
    """Return container-absolute step dir for path ops in this module."""
    if not sStepDirectory or posixpath.isabs(sStepDirectory):
        return sStepDirectory
    if not sProjectRepoPath:
        return sStepDirectory
    return posixpath.join(sProjectRepoPath, sStepDirectory)


_LIST_TEST_CATEGORIES = (
    ("dictIntegrity", "sIntegrity"),
    ("dictQualitative", "sQualitative"),
    ("dictQuantitative", "sQuantitative"),
)


def _fsBuildPytestCommand(sDirectory, sFilePath):
    """Build a pytest command string for a test file."""
    return (
        f"cd {fsShellQuote(sDirectory)}"
        f" && python -m pytest"
        f" {fsShellQuote(sFilePath)} -v"
    )


def _fnRegisterTestCommand(dictStep, bPassed, sFilePath):
    """Add the pytest run command to the step if the test passed."""
    if not bPassed:
        return
    dictStep.setdefault("saTestCommands", [])
    sRunCmd = f"python -m pytest {sFilePath} -v"
    if sRunCmd not in dictStep["saTestCommands"]:
        dictStep["saTestCommands"].append(sRunCmd)


def _flistResolveTestCommands(dictStep):
    """Return test commands from structured tests or legacy list."""
    from .workflowManager import flistResolveTestCommands
    return flistResolveTestCommands(dictStep)


def _fdictBuildTestResponse(bAllPassed, dictCategoryResults):
    """Build the HTTP response dict for a test run."""
    iMaxExitCode = max(
        (d["iExitCode"] for d in dictCategoryResults.values()),
        default=0,
    )
    return {
        "bPassed": bAllPassed,
        "iExitCode": iMaxExitCode,
        "sOutput": "",
        "dictCategoryResults": dictCategoryResults,
    }


def _fnRecordTestResult(dictStep, bPassed, dictWorkflow,
                        iStepIndex):
    """Update verification state after test execution.

    The truth-claim value resolves through the canonical
    ``truthDerivation`` so a fresh in-process run participates in
    the same derivation pattern future L2/L3 truths use.
    """
    from . import truthDerivation
    dictVerification = dictStep.setdefault(
        "dictVerification", {})
    iExitCode = 0 if bPassed else 1
    dictVerification["sUnitTest"] = (
        truthDerivation.fsResolveUnitTestFromExitCode(iExitCode))
    dictVerification.pop("listModifiedFiles", None)
    dictVerification.pop("bUpstreamModified", None)
    if bPassed:
        _fnClearDownstreamUpstreamFlags(
            dictWorkflow, iStepIndex)


def _fnClearDownstreamUpstreamFlags(dictWorkflow, iStepIndex):
    """Clear bUpstreamModified on downstream steps."""
    dictDownstream = workflowManager.fdictBuildDownstreamMap(
        dictWorkflow)
    listSteps = dictWorkflow.get("listSteps", [])
    for iDown in dictDownstream.get(iStepIndex, set()):
        if 0 <= iDown < len(listSteps):
            dictVerify = listSteps[iDown].get(
                "dictVerification", {})
            dictVerify.pop("bUpstreamModified", None)


def _fnRemoveTestFiles(
    connectionDocker, sContainerId, dictStep, iStepIndex,
    sProjectRepoPath="",
):
    """Remove generated test file from the container. Deprecated."""
    from .testGenerator import fsTestFilePath

    sAbsStepDir = _fsAbsoluteStepDir(
        dictStep.get("sDirectory", ""), sProjectRepoPath,
    )
    sPath = fsTestFilePath(sAbsStepDir, iStepIndex)
    connectionDocker.ftResultExecuteCommand(
        sContainerId, f"rm -f {fsShellQuote(sPath)}"
    )


def _fnRemoveTestDirectory(
    connectionDocker, sContainerId, dictStep,
    sProjectRepoPath="",
):
    """Remove the entire tests subdirectory from the container."""
    from .workflowManager import fsTestsDirectory

    sAbsStepDir = _fsAbsoluteStepDir(
        dictStep.get("sDirectory", ""), sProjectRepoPath,
    )
    sTestsDir = fsTestsDirectory(sAbsStepDir)
    connectionDocker.ftResultExecuteCommand(
        sContainerId, f"rm -rf {fsShellQuote(sTestsDir)}"
    )


def _flistEligibleCategoryStates(dictStep):
    """Return per-category axis values that participate in the aggregate.

    Empty-command and already-``"unnecessary"`` categories are
    excluded so the fold reflects only categories demanding a result.
    """
    dictVerification = dictStep.get("dictVerification", {})
    dictTests = dictStep.get("dictTests", {})
    listStates = []
    for sCategory, sVerifKey in _LIST_TEST_CATEGORIES:
        dictCat = dictTests.get(sCategory, {})
        if not dictCat.get("saCommands", []):
            continue
        sState = dictVerification.get(sVerifKey, "untested")
        if sState == "unnecessary":
            continue
        listStates.append(sState)
    return listStates


def _fnUpdateAggregateTestState(dictStep):
    """Recompute ``sUnitTest`` from per-category axes via the canonical fold."""
    from . import truthDerivation
    dictVerification = dictStep.setdefault("dictVerification", {})
    listStates = _flistEligibleCategoryStates(dictStep)
    dictVerification["sUnitTest"] = (
        truthDerivation.fsAggregateUnitTestFromAxes(listStates))
