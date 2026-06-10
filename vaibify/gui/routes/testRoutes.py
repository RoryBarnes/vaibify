"""Test management route handlers."""

__all__ = ["fnRegisterAll"]

import asyncio

from fastapi import HTTPException, Request

from ..actionCatalog import fnAgentAction
from ..fileStatusManager import (
    fnMaybeAutoArchive,
    fsWorkflowSlugFromPath,
)
from vaibify.reproducibility.levelGates import fiAICSLevel
from ..routeContext import ffilesForWorkflow
from ..pipelineRunner import fsShellQuote
from ..workflowManager import (
    fbDeriveUnnecessaryVerification,
    fsResolveStepWorkdir,
)
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


def _fsPrefixWithWorkflowEnv(sCommand, sWorkflowSlug):
    """Prepend ``VAIBIFY_ACTIVE_WORKFLOW_SLUG=<slug>`` to a shell command.

    The marker conftest reads this env var to namespace its writes
    under the right workflow's subdirectory. Returns the command
    unchanged when the slug is empty (no active workflow context).
    """
    if not sWorkflowSlug:
        return sCommand
    return (
        "export VAIBIFY_ACTIVE_WORKFLOW_SLUG="
        + fsShellQuote(sWorkflowSlug) + " && " + sCommand
    )


async def _fdictRunAllTestCategories(
    dictCtx, sContainerId, dictStep, sRepoRoot="", sWorkflowSlug="",
):
    """Run each test category and return {category: result}."""
    sDir = _fsAbsoluteStepWorkdir(dictStep, sRepoRoot)
    dictVerification = dictStep.setdefault(
        "dictVerification", {})
    dictTests = dictStep.get("dictTests", {})
    from .. import truthDerivation
    dictCategoryResults = {}
    for sCategory, sVerifKey in _LIST_TEST_CATEGORIES:
        dictResult = await _fdictRunOneTestCategory(
            dictCtx, sContainerId, dictStep, sDir, sCategory,
            sWorkflowSlug,
        )
        if dictResult is None:
            continue
        iExitCode = 0 if dictResult["bPassed"] else 1
        dictVerification[sVerifKey] = (
            truthDerivation.fsResolveUnitTestFromExitCode(iExitCode))
        sCatOutput = dictResult.get("sOutput", "")
        if sCatOutput and sCategory in dictTests:
            dictTests[sCategory]["sLastOutput"] = sCatOutput
        dictCategoryResults[sCategory] = dictResult
    return dictCategoryResults


async def _fdictRunOneTestCategory(
    dictCtx, sContainerId, dictStep, sDirectory, sCategory,
    sWorkflowSlug="",
):
    """Execute one test category and return result dict, or None."""
    dictCat = dictStep.get("dictTests", {}).get(sCategory, {})
    listCatCmds = dictCat.get("saCommands", [])
    if not listCatCmds:
        return None
    sCatCmd = " && ".join(
        [f"cd {fsShellQuote(sDirectory)}"] + listCatCmds)
    sCatCmd = _fsPrefixWithWorkflowEnv(sCatCmd, sWorkflowSlug)
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
        fbDeriveUnnecessaryVerification(dictWorkflow)
        dictCtx["save"](sContainerId, dictWorkflow)
        return {"bSuccess": True}


def _fnPersistTestEdit(connectionDocker, sContainerId, request):
    """Write the edited test file back to the container filesystem."""
    connectionDocker.fnWriteFile(
        sContainerId, request.sFilePath,
        request.sContent.encode("utf-8"),
    )


async def _fresultRunSaveAndRunTest(
    connectionDocker, sContainerId, dictStep, dictWorkflow, sFilePath,
):
    """Build the pytest command and run it; return the streamed result."""
    sTestCmd = _fsBuildPytestCommand(
        _fsAbsoluteStepWorkdir(
            dictStep, dictWorkflow.get("sProjectRepoPath", ""),
        ),
        sFilePath,
    )
    sTestCmd = _fsPrefixWithWorkflowEnv(
        sTestCmd,
        fsWorkflowSlugFromPath(dictWorkflow.get("sPath", "")),
    )
    return await asyncio.to_thread(
        connectionDocker.texecRunInContainerStreamed,
        sContainerId, sTestCmd,
    )


def _fdictBuildSaveRunResponse(bPassed, resultExec):
    """Return the JSON response body for save-and-run-test."""
    return {
        "bPassed": bPassed,
        "sOutput": resultExec.sStdout + resultExec.sStderr,
        "sStdout": resultExec.sStdout,
        "sStderr": resultExec.sStderr,
        "iExitCode": resultExec.iExitCode,
    }


# Category name (request body) -> (dictTests key, dictVerification key).
_DICT_TEST_CATEGORY_KEYS = {
    "integrity": ("dictIntegrity", "sIntegrity"),
    "qualitative": ("dictQualitative", "sQualitative"),
    "quantitative": ("dictQuantitative", "sQuantitative"),
}


def _ftResolveCategoryKeys(sCategory):
    """Return ``(sDictKey, sVerifKey)`` or raise HTTP 400 for an unknown name."""
    if sCategory not in _DICT_TEST_CATEGORY_KEYS:
        raise HTTPException(400, f"Unknown category: {sCategory}")
    return _DICT_TEST_CATEGORY_KEYS[sCategory]


def _ftRequireCategoryCommands(dictStep, sDictKey, sCategory):
    """Return ``(listCmds, dictCat)`` or raise HTTP 400 when no commands exist."""
    dictCat = dictStep.get("dictTests", {}).get(sDictKey, {})
    listCmds = dictCat.get("saCommands", [])
    if not listCmds:
        raise HTTPException(
            400, f"No commands for category: {sCategory}",
        )
    return listCmds, dictCat


def _fdictResolveCategoryContext(
    dictCtx, sContainerId, iStepIndex, sCategory,
):
    """Resolve workflow, step, category and pre-run AICS level for a request.

    Returns a tuple of (dictWorkflow, dictStep, dictCat, listCmds,
    sVerifKey, iLevelBefore). All HTTP errors raise here so the handler
    body stays linear.
    """
    sDictKey, sVerifKey = _ftResolveCategoryKeys(sCategory)
    dictWorkflow = fdictRequireWorkflow(
        dictCtx["workflows"], sContainerId,
    )
    dictStep = dictWorkflow["listSteps"][iStepIndex]
    listCmds, dictCat = _ftRequireCategoryCommands(
        dictStep, sDictKey, sCategory,
    )
    iLevelBefore = fiAICSLevel(
        dictWorkflow,
        ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow),
    )
    return (
        dictWorkflow, dictStep, dictCat, listCmds, sVerifKey, iLevelBefore,
    )


def _fsBuildCategoryCommand(dictStep, dictWorkflow, listCmds):
    """Build the cd + && joined category command with the workflow env prefix."""
    sDir = _fsAbsoluteStepWorkdir(
        dictStep, dictWorkflow.get("sProjectRepoPath", ""),
    )
    sFullCmd = " && ".join([f"cd {fsShellQuote(sDir)}"] + listCmds)
    return _fsPrefixWithWorkflowEnv(
        sFullCmd, fsWorkflowSlugFromPath(dictWorkflow.get("sPath", "")),
    )


async def _ftRunCategoryCommands(
    connectionDocker, sContainerId, dictStep, dictWorkflow, listCmds,
):
    """Run the category commands; return (resultExec, bPassed, sOutput)."""
    sFullCmd = _fsBuildCategoryCommand(dictStep, dictWorkflow, listCmds)
    resultExec = await asyncio.to_thread(
        connectionDocker.texecRunInContainerStreamed,
        sContainerId, sFullCmd,
    )
    bPassed = resultExec.iExitCode == 0
    sOutput = resultExec.sStdout + resultExec.sStderr
    return resultExec, bPassed, sOutput


def _fnRecordCategoryOutcome(
    dictStep, dictCat, sVerifKey, bPassed, sOutput,
):
    """Update dictVerification + aggregate state after a category run."""
    from .. import truthDerivation
    dictVerification = dictStep.setdefault("dictVerification", {})
    iExitCode = 0 if bPassed else 1
    dictVerification[sVerifKey] = (
        truthDerivation.fsResolveUnitTestFromExitCode(iExitCode))
    dictVerification.pop("listModifiedFiles", None)
    dictVerification.pop("bUpstreamModified", None)
    if sOutput:
        dictCat["sLastOutput"] = sOutput
    _fnUpdateAggregateTestState(dictStep)


def _fdictBuildRunCategoryResponse(bPassed, resultExec):
    """Return the JSON response body for run-test-category."""
    return {
        "bPassed": bPassed,
        "sOutput": resultExec.sStdout + resultExec.sStderr,
        "sStdout": resultExec.sStdout,
        "sStderr": resultExec.sStderr,
        "iExitCode": resultExec.iExitCode,
    }


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
        iLevelBefore = fiAICSLevel(
            dictWorkflow,
            ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow),
        )
        _fnPersistTestEdit(dictCtx["docker"], sContainerId, request)
        resultExec = await _fresultRunSaveAndRunTest(
            dictCtx["docker"], sContainerId, dictStep,
            dictWorkflow, request.sFilePath,
        )
        bPassed = resultExec.iExitCode == 0
        _fnRecordTestResult(dictStep, bPassed, dictWorkflow, iStepIndex)
        _fnRegisterTestCommand(dictStep, bPassed, request.sFilePath)
        dictCtx["save"](sContainerId, dictWorkflow)
        await fnMaybeAutoArchive(
            dictCtx["docker"], sContainerId, dictWorkflow,
            iStepIndex, iLevelBefore,
        )
        return _fdictBuildSaveRunResponse(bPassed, resultExec)


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
        iLevelBefore = fiAICSLevel(
            dictWorkflow,
            ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow),
        )
        dictCategoryResults = await _fdictRunAllTestCategories(
            dictCtx, sContainerId, dictStep,
            sRepoRoot=dictWorkflow.get("sProjectRepoPath", ""),
            sWorkflowSlug=fsWorkflowSlugFromPath(
                dictWorkflow.get("sPath", "")),
        )
        bAllPassed = all(
            d["bPassed"] for d in dictCategoryResults.values()
        )
        _fnRecordTestResult(
            dictStep, bAllPassed, dictWorkflow, iStepIndex)
        dictCtx["save"](sContainerId, dictWorkflow)
        await fnMaybeAutoArchive(
            dictCtx["docker"], sContainerId, dictWorkflow,
            iStepIndex, iLevelBefore,
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
        sCategory = (await request.json()).get("sCategory", "")
        (dictWorkflow, dictStep, dictCat, listCmds, sVerifKey,
         iLevelBefore) = _fdictResolveCategoryContext(
            dictCtx, sContainerId, iStepIndex, sCategory,
        )
        resultExec, bPassed, sOutput = await _ftRunCategoryCommands(
            dictCtx["docker"], sContainerId, dictStep,
            dictWorkflow, listCmds,
        )
        _fnRecordCategoryOutcome(
            dictStep, dictCat, sVerifKey, bPassed, sOutput,
        )
        dictCtx["save"](sContainerId, dictWorkflow)
        await fnMaybeAutoArchive(
            dictCtx["docker"], sContainerId, dictWorkflow,
            iStepIndex, iLevelBefore,
        )
        return _fdictBuildRunCategoryResponse(bPassed, resultExec)


def fnRegisterAll(app, dictCtx):
    """Register all test management routes."""
    _fnRegisterTestGenerate(app, dictCtx)
    _fnRegisterTestSaveAndRun(app, dictCtx)
    _fnRegisterTestRun(app, dictCtx)
