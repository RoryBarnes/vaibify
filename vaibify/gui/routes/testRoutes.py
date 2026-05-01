"""Test management route handlers."""

__all__ = ["fnRegisterAll"]

import asyncio

from fastapi import HTTPException, Request

from ..actionCatalog import fnAgentAction
from ..fileStatusManager import fbIsStepFullyVerified, fnMaybeAutoArchive
from ..pipelineRunner import fsShellQuote
from ..workflowManager import fsResolveStepWorkdir
from .. import pipelineServer as _pipelineServer
from ..pipelineServer import (
    SaveAndRunTestRequest,
    TestGenerateRequest,
    fdictRequireWorkflow,
    _fsSanitizeServerError,
)
from ..testStatusManager import (
    _LIST_TEST_CATEGORIES,
    _fdictBuildTestResponse,
    _flistResolveTestCommands,
    _fnRecordTestResult,
    _fnRegisterTestCommand,
    _fnRemoveTestDirectory,
    _fnUpdateAggregateTestState,
    _fsBuildPytestCommand,
)


async def _fdictRunTestGeneration(
    dictCtx, sContainerId, iStepIndex,
    dictWorkflow, fdictGenerate, request,
):
    """Invoke the test generator and return its result dict."""
    dictVars = dictCtx["variables"](sContainerId)
    sUser = dictCtx["containerUsers"].get(
        sContainerId, _pipelineServer.sTerminalUser
    )
    try:
        return await asyncio.to_thread(
            fdictGenerate,
            dictCtx["docker"], sContainerId, iStepIndex,
            dictWorkflow, dictVars,
            request.bUseApi, request.sApiKey,
            sUser=sUser,
            bDeterministic=request.bDeterministic,
            bForceOverwrite=request.bForceOverwrite,
        )
    except Exception as error:
        raise HTTPException(
            500, f"Generation failed: "
            f"{_fsSanitizeServerError(str(error))}")


def _fbNeedsClaudeFallback(dictCtx, sContainerId, request):
    """Return True if we need an LLM fallback and Claude is unavailable."""
    if request.bDeterministic or request.bUseApi:
        return False
    from ..testGenerator import fbContainerHasClaude
    return not fbContainerHasClaude(
        dictCtx["docker"], sContainerId)


def _fnApplyGeneratedTests(
    dictCtx, sContainerId, dictWorkflow, iStepIndex,
    dictResult,
):
    """Store generated test categories in the step and save."""
    from ..workflowManager import flistBuildTestCommands
    dictStep = dictWorkflow["listSteps"][iStepIndex]
    dictTests = dictStep.setdefault("dictTests", {})
    for sCategory in (
        "dictIntegrity", "dictQualitative", "dictQuantitative"
    ):
        if sCategory in dictResult:
            dictTests[sCategory] = dictResult[sCategory]
    dictStep["saTestCommands"] = flistBuildTestCommands(dictStep)
    dictCtx["save"](sContainerId, dictWorkflow)


def _fdictBuildGenerateResponse(dictResult):
    """Build the HTTP response dict for test generation."""
    return {
        "bGenerated": True,
        "dictIntegrity": dictResult.get("dictIntegrity", {}),
        "dictQualitative": dictResult.get(
            "dictQualitative", {}),
        "dictQuantitative": dictResult.get(
            "dictQuantitative", {}),
    }


def _fsAbsoluteStepWorkdir(dictStep, sRepoRoot):
    """Return the container-absolute step workdir for a `cd` command.

    Step ``sDirectory`` values are repo-relative; ``cd``-ing into them
    from docker exec's default WORKDIR (`/workspace`) misses the
    project repo prefix. Joining with ``sProjectRepoPath`` makes the
    cd land inside the workflow's repo. Idempotent on already-absolute
    values; returns ``"/workspace"`` for the legacy empty-dir case.
    """
    sDir = dictStep.get("sDirectory", "")
    if not sDir:
        return "/workspace"
    return fsResolveStepWorkdir(sDir, {"sRepoRoot": sRepoRoot})


async def _fdictRunAllTestCategories(
    dictCtx, sContainerId, dictStep, sRepoRoot="",
):
    """Run each test category and return {category: result}."""
    sDir = _fsAbsoluteStepWorkdir(dictStep, sRepoRoot)
    dictVerification = dictStep.setdefault(
        "dictVerification", {})
    dictTests = dictStep.get("dictTests", {})
    dictCategoryResults = {}
    for sCategory, sVerifKey in _LIST_TEST_CATEGORIES:
        dictResult = await _fdictRunOneTestCategory(
            dictCtx, sContainerId, dictStep, sDir, sCategory,
        )
        if dictResult is None:
            continue
        dictVerification[sVerifKey] = (
            "passed" if dictResult["bPassed"] else "failed")
        sCatOutput = dictResult.get("sOutput", "")
        if sCatOutput and sCategory in dictTests:
            dictTests[sCategory]["sLastOutput"] = sCatOutput
        dictCategoryResults[sCategory] = dictResult
    return dictCategoryResults


async def _fdictRunOneTestCategory(
    dictCtx, sContainerId, dictStep, sDirectory, sCategory,
):
    """Execute one test category and return result dict, or None."""
    dictCat = dictStep.get("dictTests", {}).get(sCategory, {})
    listCatCmds = dictCat.get("saCommands", [])
    if not listCatCmds:
        return None
    sCatCmd = " && ".join(
        [f"cd {fsShellQuote(sDirectory)}"] + listCatCmds)
    resultExec = await asyncio.to_thread(
        dictCtx["docker"].texecRunInContainerStreamed,
        sContainerId, sCatCmd,
    )
    return {
        "bPassed": resultExec.iExitCode == 0,
        "sOutput": resultExec.sStdout + resultExec.sStderr,
        "sStdout": resultExec.sStdout,
        "sStderr": resultExec.sStderr,
        "iExitCode": resultExec.iExitCode,
    }


def _fnRegisterTestGenerate(app, dictCtx):
    """Register test generation and deletion routes."""

    @fnAgentAction("generate-tests")
    @app.post(
        "/api/steps/{sContainerId}/{iStepIndex}/generate-test"
    )
    async def fnGenerateTest(
        sContainerId: str, iStepIndex: int,
        request: TestGenerateRequest,
    ):
        dictCtx["require"]()
        from ..testGenerator import fdictGenerateAllTests
        if _fbNeedsClaudeFallback(
            dictCtx, sContainerId, request
        ):
            return {"bNeedsFallback": True}
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId
        )
        dictResult = await _fdictRunTestGeneration(
            dictCtx, sContainerId, iStepIndex,
            dictWorkflow, fdictGenerateAllTests, request,
        )
        if dictResult.get("bNeedsOverwriteConfirm"):
            return {
                "bNeedsOverwriteConfirm": True,
                "listModifiedFiles": dictResult.get(
                    "listModifiedFiles", []
                ),
            }
        _fnApplyGeneratedTests(
            dictCtx, sContainerId, dictWorkflow,
            iStepIndex, dictResult,
        )
        return _fdictBuildGenerateResponse(dictResult)

    @fnAgentAction("delete-generated-tests")
    @app.delete(
        "/api/steps/{sContainerId}/{iStepIndex}/generated-test"
    )
    async def fnDeleteGeneratedTest(
        sContainerId: str, iStepIndex: int,
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId
        )
        dictStep = dictWorkflow["listSteps"][iStepIndex]
        _fnRemoveTestDirectory(
            dictCtx["docker"], sContainerId, dictStep,
            sProjectRepoPath=dictWorkflow.get(
                "sProjectRepoPath", ""),
        )
        dictStep["dictTests"] = {
            "dictQualitative": {
                "saCommands": [], "sFilePath": "",
            },
            "dictQuantitative": {
                "saCommands": [],
                "sFilePath": "",
                "sStandardsPath": "",
            },
            "dictIntegrity": {
                "saCommands": [], "sFilePath": "",
            },
            "listUserTests": [],
        }
        dictStep["saTestCommands"] = []
        dictVerification = dictStep.setdefault(
            "dictVerification", {})
        dictVerification["sUnitTest"] = "untested"
        dictVerification["sQualitative"] = "untested"
        dictVerification["sQuantitative"] = "untested"
        dictVerification["sIntegrity"] = "untested"
        dictCtx["save"](sContainerId, dictWorkflow)
        return {"bSuccess": True}


def _fnRegisterTestSaveAndRun(app, dictCtx):
    """Register POST /api/steps/{id}/{step}/save-and-run-test."""

    @fnAgentAction("save-and-run-test")
    @app.post(
        "/api/steps/{sContainerId}/{iStepIndex}"
        "/save-and-run-test"
    )
    async def fnSaveAndRunTest(
        sContainerId: str, iStepIndex: int,
        request: SaveAndRunTestRequest,
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        dictStep = dictWorkflow["listSteps"][iStepIndex]
        bWasVerified = fbIsStepFullyVerified(dictStep)
        dictCtx["docker"].fnWriteFile(
            sContainerId, request.sFilePath,
            request.sContent.encode("utf-8"),
        )
        sTestCmd = _fsBuildPytestCommand(
            _fsAbsoluteStepWorkdir(
                dictStep,
                dictWorkflow.get("sProjectRepoPath", ""),
            ),
            request.sFilePath,
        )
        resultExec = await asyncio.to_thread(
            dictCtx["docker"].texecRunInContainerStreamed,
            sContainerId, sTestCmd,
        )
        bPassed = resultExec.iExitCode == 0
        _fnRecordTestResult(
            dictStep, bPassed, dictWorkflow, iStepIndex)
        _fnRegisterTestCommand(
            dictStep, bPassed, request.sFilePath)
        dictCtx["save"](sContainerId, dictWorkflow)
        await fnMaybeAutoArchive(
            dictCtx["docker"], sContainerId, dictWorkflow,
            iStepIndex, bWasVerified,
        )
        return {
            "bPassed": bPassed,
            "sOutput": resultExec.sStdout + resultExec.sStderr,
            "sStdout": resultExec.sStdout,
            "sStderr": resultExec.sStderr,
            "iExitCode": resultExec.iExitCode,
        }


def _fnRegisterTestRun(app, dictCtx):
    """Register POST /api/steps/{id}/{step}/run-tests."""

    @fnAgentAction("run-unit-tests")
    @app.post(
        "/api/steps/{sContainerId}/{iStepIndex}/run-tests"
    )
    async def fnRunTests(
        sContainerId: str, iStepIndex: int
    ):
        from ..workflowManager import flistBuildTestCommands
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        dictStep = dictWorkflow["listSteps"][iStepIndex]
        listCmds = _flistResolveTestCommands(dictStep)
        if not listCmds:
            raise HTTPException(400, "No test commands")
        bWasVerified = fbIsStepFullyVerified(dictStep)
        dictCategoryResults = await _fdictRunAllTestCategories(
            dictCtx, sContainerId, dictStep,
            sRepoRoot=dictWorkflow.get("sProjectRepoPath", ""),
        )
        bAllPassed = all(
            d["bPassed"] for d in dictCategoryResults.values()
        )
        _fnRecordTestResult(
            dictStep, bAllPassed, dictWorkflow, iStepIndex)
        dictCtx["save"](sContainerId, dictWorkflow)
        await fnMaybeAutoArchive(
            dictCtx["docker"], sContainerId, dictWorkflow,
            iStepIndex, bWasVerified,
        )
        return _fdictBuildTestResponse(
            bAllPassed, dictCategoryResults)

    @fnAgentAction("run-test-category")
    @app.post(
        "/api/steps/{sContainerId}/{iStepIndex}"
        "/run-test-category"
    )
    async def fnRunTestCategory(
        sContainerId: str, iStepIndex: int,
        request: Request,
    ):
        dictCtx["require"]()
        dictBody = await request.json()
        sCategory = dictBody.get("sCategory", "")
        dictCategoryKeyMap = {
            "integrity": ("dictIntegrity", "sIntegrity"),
            "qualitative": (
                "dictQualitative", "sQualitative"),
            "quantitative": (
                "dictQuantitative", "sQuantitative"),
        }
        if sCategory not in dictCategoryKeyMap:
            raise HTTPException(
                400, f"Unknown category: {sCategory}")
        sDictKey, sVerifKey = dictCategoryKeyMap[sCategory]
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        dictStep = dictWorkflow["listSteps"][iStepIndex]
        dictTests = dictStep.get("dictTests", {})
        dictCat = dictTests.get(sDictKey, {})
        listCmds = dictCat.get("saCommands", [])
        if not listCmds:
            raise HTTPException(
                400,
                f"No commands for category: {sCategory}",
            )
        bWasVerified = fbIsStepFullyVerified(dictStep)
        sDir = _fsAbsoluteStepWorkdir(
            dictStep,
            dictWorkflow.get("sProjectRepoPath", ""),
        )
        sFullCmd = " && ".join(
            [f"cd {fsShellQuote(sDir)}"] + listCmds)
        resultExec = await asyncio.to_thread(
            dictCtx["docker"].texecRunInContainerStreamed,
            sContainerId, sFullCmd,
        )
        bPassed = resultExec.iExitCode == 0
        sOutput = resultExec.sStdout + resultExec.sStderr
        dictVerification = dictStep.setdefault(
            "dictVerification", {})
        dictVerification[sVerifKey] = (
            "passed" if bPassed else "failed")
        dictVerification.pop("listModifiedFiles", None)
        dictVerification.pop("bUpstreamModified", None)
        if sOutput:
            dictCat["sLastOutput"] = sOutput
        _fnUpdateAggregateTestState(dictStep)
        dictCtx["save"](sContainerId, dictWorkflow)
        await fnMaybeAutoArchive(
            dictCtx["docker"], sContainerId, dictWorkflow,
            iStepIndex, bWasVerified,
        )
        return {
            "bPassed": bPassed,
            "sOutput": sOutput,
            "sStdout": resultExec.sStdout,
            "sStderr": resultExec.sStderr,
            "iExitCode": resultExec.iExitCode,
        }


def fnRegisterAll(app, dictCtx):
    """Register all test management routes."""
    _fnRegisterTestGenerate(app, dictCtx)
    _fnRegisterTestSaveAndRun(app, dictCtx)
    _fnRegisterTestRun(app, dictCtx)
