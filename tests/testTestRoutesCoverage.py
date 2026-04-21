"""Tests for vaibify.gui.routes.testRoutes — covers uncovered lines."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vaibify.gui.routes.testRoutes import (
    _fbNeedsClaudeFallback,
    _fdictBuildGenerateResponse,
    _fdictRunAllTestCategories,
    _fdictRunOneTestCategory,
    _fdictRunTestGeneration,
    _fnApplyGeneratedTests,
)


def _fdictBuildContext():
    """Build a minimal dictCtx for test route tests."""
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand = MagicMock(
        return_value=(0, "OK"))
    return {
        "require": MagicMock(),
        "docker": mockDocker,
        "containerUsers": {},
        "variables": MagicMock(return_value={}),
        "workflows": {"cid-1": {"listSteps": [
            {
                "sDirectory": ".",
                "dictTests": {},
                "dictVerification": {},
            }
        ]}},
        "save": MagicMock(),
    }


# -------------------------------------------------------------------
# _fdictRunTestGeneration (lines 34-51)
# -------------------------------------------------------------------

class TestFdictRunTestGeneration:
    @pytest.mark.asyncio
    async def test_successful_generation(self):
        dictCtx = _fdictBuildContext()
        dictCtx["containerUsers"] = {"cid-1": "astro"}
        dictWorkflow = {"listSteps": [{"sDirectory": "/ws"}]}
        mockRequest = MagicMock()
        mockRequest.bUseApi = False
        mockRequest.sApiKey = None
        mockRequest.bDeterministic = True
        mockRequest.bForceOverwrite = False

        dictExpected = {"dictIntegrity": {"saCommands": ["x"]}}
        mockGenerate = MagicMock(return_value=dictExpected)

        dictResult = await _fdictRunTestGeneration(
            dictCtx, "cid-1", 0, dictWorkflow,
            mockGenerate, mockRequest,
        )
        assert dictResult == dictExpected
        mockGenerate.assert_called_once()
        listArgs = mockGenerate.call_args
        assert listArgs[1]["sUser"] == "astro"

    @pytest.mark.asyncio
    async def test_default_user_fallback(self):
        dictCtx = _fdictBuildContext()
        dictWorkflow = {"listSteps": []}
        mockRequest = MagicMock()
        mockRequest.bUseApi = True
        mockRequest.sApiKey = "key"
        mockRequest.bDeterministic = False
        mockRequest.bForceOverwrite = True

        mockGenerate = MagicMock(return_value={})

        with patch(
            "vaibify.gui.routes.testRoutes._pipelineServer"
        ) as mockPs:
            mockPs.sTerminalUser = "defaultuser"
            await _fdictRunTestGeneration(
                dictCtx, "cid-1", 0, dictWorkflow,
                mockGenerate, mockRequest,
            )
        assert mockGenerate.call_args[1]["sUser"] == "defaultuser"

    @pytest.mark.asyncio
    async def test_generation_exception_raises_http(self):
        from fastapi import HTTPException

        dictCtx = _fdictBuildContext()
        dictWorkflow = {"listSteps": []}
        mockRequest = MagicMock()
        mockRequest.bUseApi = False
        mockRequest.sApiKey = None
        mockRequest.bDeterministic = True
        mockRequest.bForceOverwrite = False

        mockGenerate = MagicMock(
            side_effect=RuntimeError("disk full"))

        with pytest.raises(HTTPException) as excInfo:
            await _fdictRunTestGeneration(
                dictCtx, "cid-1", 0, dictWorkflow,
                mockGenerate, mockRequest,
            )
        assert excInfo.value.status_code == 500
        assert "Generation failed" in excInfo.value.detail


# -------------------------------------------------------------------
# _fbNeedsClaudeFallback (lines 54-60)
# -------------------------------------------------------------------

class TestFbNeedsClaudeFallback:
    def test_deterministic_returns_false(self):
        mockRequest = MagicMock()
        mockRequest.bDeterministic = True
        mockRequest.bUseApi = False
        assert _fbNeedsClaudeFallback({}, "c", mockRequest) is False

    def test_use_api_returns_false(self):
        mockRequest = MagicMock()
        mockRequest.bDeterministic = False
        mockRequest.bUseApi = True
        assert _fbNeedsClaudeFallback({}, "c", mockRequest) is False

    def test_claude_available_returns_false(self):
        mockRequest = MagicMock()
        mockRequest.bDeterministic = False
        mockRequest.bUseApi = False
        dictCtx = {"docker": MagicMock()}
        with patch(
            "vaibify.gui.testGenerator.fbContainerHasClaude",
            return_value=True,
        ):
            assert _fbNeedsClaudeFallback(
                dictCtx, "c", mockRequest) is False

    def test_claude_missing_returns_true(self):
        mockRequest = MagicMock()
        mockRequest.bDeterministic = False
        mockRequest.bUseApi = False
        dictCtx = {"docker": MagicMock()}
        with patch(
            "vaibify.gui.testGenerator.fbContainerHasClaude",
            return_value=False,
        ):
            assert _fbNeedsClaudeFallback(
                dictCtx, "c", mockRequest) is True


# -------------------------------------------------------------------
# _fnRegisterTestGenerate route handler (lines 153-171)
# -------------------------------------------------------------------

class TestGenerateTestRoute:
    @pytest.mark.asyncio
    async def test_needs_overwrite_confirm(self):
        """Lines 160-166: bNeedsOverwriteConfirm branch."""
        from vaibify.gui.routes import testRoutes

        dictCtx = _fdictBuildContext()
        app = MagicMock()
        listHandlers = {}

        def fnCapturePost(sPath):
            def fnDecorator(fnHandler):
                listHandlers[sPath] = fnHandler
                return fnHandler
            return fnDecorator

        def fnCaptureDelete(sPath):
            def fnDecorator(fnHandler):
                listHandlers[sPath] = fnHandler
                return fnHandler
            return fnDecorator

        app.post = fnCapturePost
        app.delete = fnCaptureDelete
        testRoutes._fnRegisterTestGenerate(app, dictCtx)

        sPath = "/api/steps/{sContainerId}/{iStepIndex}/generate-test"
        fnHandler = listHandlers[sPath]

        mockRequest = MagicMock()
        mockRequest.bDeterministic = True
        mockRequest.bUseApi = False
        mockRequest.bForceOverwrite = False

        dictOverwrite = {
            "bNeedsOverwriteConfirm": True,
            "listModifiedFiles": ["a.py"],
        }

        with patch.object(
            testRoutes, "_fbNeedsClaudeFallback",
            return_value=False,
        ), patch.object(
            testRoutes, "fdictRequireWorkflow",
            return_value={"listSteps": [{"sDirectory": "/ws"}]},
        ), patch.object(
            testRoutes, "_fdictRunTestGeneration",
            new_callable=AsyncMock,
            return_value=dictOverwrite,
        ), patch(
            "vaibify.gui.routes.testRoutes.fdictGenerateAllTests",
            create=True,
        ):
            dictResult = await fnHandler("cid-1", 0, mockRequest)

        assert dictResult["bNeedsOverwriteConfirm"] is True
        assert dictResult["listModifiedFiles"] == ["a.py"]

    @pytest.mark.asyncio
    async def test_successful_generation_applies_and_returns(self):
        """Lines 167-171: apply tests and return response."""
        from vaibify.gui.routes import testRoutes

        dictCtx = _fdictBuildContext()
        app = MagicMock()
        listHandlers = {}

        def fnCapturePost(sPath):
            def fnDecorator(fnHandler):
                listHandlers[sPath] = fnHandler
                return fnHandler
            return fnDecorator

        def fnCaptureDelete(sPath):
            def fnDecorator(fnHandler):
                listHandlers[sPath] = fnHandler
                return fnHandler
            return fnDecorator

        app.post = fnCapturePost
        app.delete = fnCaptureDelete
        testRoutes._fnRegisterTestGenerate(app, dictCtx)

        sPath = "/api/steps/{sContainerId}/{iStepIndex}/generate-test"
        fnHandler = listHandlers[sPath]

        mockRequest = MagicMock()
        mockRequest.bDeterministic = True
        mockRequest.bUseApi = False
        mockRequest.bForceOverwrite = False

        dictGenResult = {
            "dictIntegrity": {"saCommands": ["pytest"]},
        }

        with patch.object(
            testRoutes, "_fbNeedsClaudeFallback",
            return_value=False,
        ), patch.object(
            testRoutes, "fdictRequireWorkflow",
            return_value={"listSteps": [
                {"sDirectory": "/ws", "dictTests": {}}
            ]},
        ), patch.object(
            testRoutes, "_fdictRunTestGeneration",
            new_callable=AsyncMock,
            return_value=dictGenResult,
        ), patch.object(
            testRoutes, "_fnApplyGeneratedTests",
        ) as mockApply, patch(
            "vaibify.gui.routes.testRoutes.fdictGenerateAllTests",
            create=True,
        ):
            dictResult = await fnHandler("cid-1", 0, mockRequest)

        assert dictResult["bGenerated"] is True
        mockApply.assert_called_once()

    @pytest.mark.asyncio
    async def test_needs_fallback(self):
        """Line 152: bNeedsFallback branch."""
        from vaibify.gui.routes import testRoutes

        dictCtx = _fdictBuildContext()
        app = MagicMock()
        listHandlers = {}

        def fnCapturePost(sPath):
            def fnDecorator(fnHandler):
                listHandlers[sPath] = fnHandler
                return fnHandler
            return fnDecorator

        def fnCaptureDelete(sPath):
            def fnDecorator(fnHandler):
                listHandlers[sPath] = fnHandler
                return fnHandler
            return fnDecorator

        app.post = fnCapturePost
        app.delete = fnCaptureDelete
        testRoutes._fnRegisterTestGenerate(app, dictCtx)

        sPath = "/api/steps/{sContainerId}/{iStepIndex}/generate-test"
        fnHandler = listHandlers[sPath]

        mockRequest = MagicMock()
        mockRequest.bDeterministic = False
        mockRequest.bUseApi = False

        with patch.object(
            testRoutes, "_fbNeedsClaudeFallback",
            return_value=True,
        ), patch(
            "vaibify.gui.routes.testRoutes.fdictGenerateAllTests",
            create=True,
        ):
            dictResult = await fnHandler("cid-1", 0, mockRequest)

        assert dictResult["bNeedsFallback"] is True


# -------------------------------------------------------------------
# Run test category route (lines 304-347)
# -------------------------------------------------------------------

class TestRunTestCategoryRoute:
    def _fnRegisterAndCapture(self, dictCtx):
        """Register routes and return handler dict."""
        from vaibify.gui.routes import testRoutes

        app = MagicMock()
        listHandlers = {}

        def fnCapturePost(sPath):
            def fnDecorator(fnHandler):
                listHandlers[sPath] = fnHandler
                return fnHandler
            return fnDecorator

        app.post = fnCapturePost
        testRoutes._fnRegisterTestRun(app, dictCtx)
        return listHandlers

    @pytest.mark.asyncio
    async def test_unknown_category_raises_400(self):
        dictCtx = _fdictBuildContext()
        listHandlers = self._fnRegisterAndCapture(dictCtx)

        sPath = (
            "/api/steps/{sContainerId}/{iStepIndex}"
            "/run-test-category"
        )
        fnHandler = listHandlers[sPath]

        mockRequest = AsyncMock()
        mockRequest.json = AsyncMock(
            return_value={"sCategory": "bogus"})

        with patch(
            "vaibify.gui.routes.testRoutes.fdictRequireWorkflow",
            return_value={"listSteps": [{"sDirectory": "/ws"}]},
        ):
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as excInfo:
                await fnHandler("cid-1", 0, mockRequest)
            assert excInfo.value.status_code == 400
            assert "Unknown category" in excInfo.value.detail

    @pytest.mark.asyncio
    async def test_empty_commands_raises_400(self):
        dictCtx = _fdictBuildContext()
        listHandlers = self._fnRegisterAndCapture(dictCtx)

        sPath = (
            "/api/steps/{sContainerId}/{iStepIndex}"
            "/run-test-category"
        )
        fnHandler = listHandlers[sPath]

        mockRequest = AsyncMock()
        mockRequest.json = AsyncMock(
            return_value={"sCategory": "integrity"})

        dictWorkflow = {"listSteps": [{
            "sDirectory": "/ws",
            "dictTests": {"dictIntegrity": {"saCommands": []}},
            "dictVerification": {},
        }]}

        with patch(
            "vaibify.gui.routes.testRoutes.fdictRequireWorkflow",
            return_value=dictWorkflow,
        ):
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as excInfo:
                await fnHandler("cid-1", 0, mockRequest)
            assert excInfo.value.status_code == 400
            assert "No commands" in excInfo.value.detail

    @pytest.mark.asyncio
    async def test_successful_category_run_passed(self):
        dictCtx = _fdictBuildContext()
        dictCtx["docker"].ftResultExecuteCommand = MagicMock(
            return_value=(0, "all passed"))
        listHandlers = self._fnRegisterAndCapture(dictCtx)

        sPath = (
            "/api/steps/{sContainerId}/{iStepIndex}"
            "/run-test-category"
        )
        fnHandler = listHandlers[sPath]

        mockRequest = AsyncMock()
        mockRequest.json = AsyncMock(
            return_value={"sCategory": "qualitative"})

        dictStep = {
            "sDirectory": ".",
            "dictTests": {
                "dictQualitative": {
                    "saCommands": ["pytest test_q.py"],
                },
            },
            "dictVerification": {
                "bUpstreamModified": True,
                "listModifiedFiles": ["a.dat"],
            },
        }
        dictWorkflow = {"listSteps": [dictStep]}

        with patch(
            "vaibify.gui.routes.testRoutes.fdictRequireWorkflow",
            return_value=dictWorkflow,
        ), patch(
            "vaibify.gui.routes.testRoutes._fnUpdateAggregateTestState",
        ):
            dictResult = await fnHandler("cid-1", 0, mockRequest)

        assert dictResult["bPassed"] is True
        assert dictResult["sOutput"] == "all passed"
        assert dictResult["iExitCode"] == 0
        assert dictStep["dictVerification"]["sQualitative"] == "passed"
        assert "bUpstreamModified" not in dictStep["dictVerification"]
        assert "listModifiedFiles" not in dictStep["dictVerification"]
        assert dictStep["dictTests"]["dictQualitative"][
            "sLastOutput"] == "all passed"
        dictCtx["save"].assert_called_once()

    @pytest.mark.asyncio
    async def test_failed_category_run(self):
        dictCtx = _fdictBuildContext()
        dictCtx["docker"].ftResultExecuteCommand = MagicMock(
            return_value=(1, "FAILED"))
        listHandlers = self._fnRegisterAndCapture(dictCtx)

        sPath = (
            "/api/steps/{sContainerId}/{iStepIndex}"
            "/run-test-category"
        )
        fnHandler = listHandlers[sPath]

        mockRequest = AsyncMock()
        mockRequest.json = AsyncMock(
            return_value={"sCategory": "integrity"})

        dictStep = {
            "sDirectory": ".",
            "dictTests": {
                "dictIntegrity": {
                    "saCommands": ["pytest test_i.py"],
                },
            },
            "dictVerification": {},
        }
        dictWorkflow = {"listSteps": [dictStep]}

        with patch(
            "vaibify.gui.routes.testRoutes.fdictRequireWorkflow",
            return_value=dictWorkflow,
        ), patch(
            "vaibify.gui.routes.testRoutes._fnUpdateAggregateTestState",
        ):
            dictResult = await fnHandler("cid-1", 0, mockRequest)

        assert dictResult["bPassed"] is False
        assert dictStep["dictVerification"]["sIntegrity"] == "failed"

    @pytest.mark.asyncio
    async def test_quantitative_category_run(self):
        """Cover the quantitative branch in dictCategoryKeyMap."""
        dictCtx = _fdictBuildContext()
        dictCtx["docker"].ftResultExecuteCommand = MagicMock(
            return_value=(0, "ok"))
        listHandlers = self._fnRegisterAndCapture(dictCtx)

        sPath = (
            "/api/steps/{sContainerId}/{iStepIndex}"
            "/run-test-category"
        )
        fnHandler = listHandlers[sPath]

        mockRequest = AsyncMock()
        mockRequest.json = AsyncMock(
            return_value={"sCategory": "quantitative"})

        dictStep = {
            "sDirectory": ".",
            "dictTests": {
                "dictQuantitative": {
                    "saCommands": ["pytest test_quant.py"],
                },
            },
            "dictVerification": {},
        }
        dictWorkflow = {"listSteps": [dictStep]}

        with patch(
            "vaibify.gui.routes.testRoutes.fdictRequireWorkflow",
            return_value=dictWorkflow,
        ), patch(
            "vaibify.gui.routes.testRoutes._fnUpdateAggregateTestState",
        ):
            dictResult = await fnHandler("cid-1", 0, mockRequest)

        assert dictResult["bPassed"] is True
        assert dictStep["dictVerification"][
            "sQuantitative"] == "passed"

    @pytest.mark.asyncio
    async def test_empty_output_not_stored(self):
        """Line 343: sLastOutput only set when sOutput truthy."""
        dictCtx = _fdictBuildContext()
        dictCtx["docker"].ftResultExecuteCommand = MagicMock(
            return_value=(0, ""))
        listHandlers = self._fnRegisterAndCapture(dictCtx)

        sPath = (
            "/api/steps/{sContainerId}/{iStepIndex}"
            "/run-test-category"
        )
        fnHandler = listHandlers[sPath]

        mockRequest = AsyncMock()
        mockRequest.json = AsyncMock(
            return_value={"sCategory": "quantitative"})

        dictStep = {
            "sDirectory": ".",
            "dictTests": {
                "dictQuantitative": {
                    "saCommands": ["pytest test_q.py"],
                },
            },
            "dictVerification": {},
        }
        dictWorkflow = {"listSteps": [dictStep]}

        with patch(
            "vaibify.gui.routes.testRoutes.fdictRequireWorkflow",
            return_value=dictWorkflow,
        ), patch(
            "vaibify.gui.routes.testRoutes._fnUpdateAggregateTestState",
        ):
            dictResult = await fnHandler("cid-1", 0, mockRequest)

        assert "sLastOutput" not in dictStep["dictTests"][
            "dictQuantitative"]


# -------------------------------------------------------------------
# Delete generated test route (lines 179-209)
# -------------------------------------------------------------------

class TestDeleteGeneratedTestRoute:
    def _fnRegisterAndCapture(self, dictCtx):
        from vaibify.gui.routes import testRoutes

        app = MagicMock()
        listHandlers = {}

        def fnCapturePost(sPath):
            def fnDecorator(fnHandler):
                listHandlers[sPath] = fnHandler
                return fnHandler
            return fnDecorator

        def fnCaptureDelete(sPath):
            def fnDecorator(fnHandler):
                listHandlers[sPath] = fnHandler
                return fnHandler
            return fnDecorator

        app.post = fnCapturePost
        app.delete = fnCaptureDelete
        testRoutes._fnRegisterTestGenerate(app, dictCtx)
        return listHandlers

    @pytest.mark.asyncio
    async def test_delete_resets_step_tests(self):
        dictCtx = _fdictBuildContext()
        dictStep = {
            "sDirectory": ".",
            "dictTests": {
                "dictIntegrity": {"saCommands": ["old"]},
                "dictQualitative": {"saCommands": ["old"]},
                "dictQuantitative": {"saCommands": ["old"]},
            },
            "saTestCommands": ["old"],
            "dictVerification": {
                "sUnitTest": "passed",
                "sQualitative": "passed",
                "sQuantitative": "passed",
                "sIntegrity": "passed",
            },
        }
        dictWorkflow = {"listSteps": [dictStep]}

        listHandlers = self._fnRegisterAndCapture(dictCtx)
        sPath = (
            "/api/steps/{sContainerId}/{iStepIndex}"
            "/generated-test"
        )
        fnHandler = listHandlers[sPath]

        with patch(
            "vaibify.gui.routes.testRoutes.fdictRequireWorkflow",
            return_value=dictWorkflow,
        ), patch(
            "vaibify.gui.routes.testRoutes._fnRemoveTestDirectory",
        ):
            dictResult = await fnHandler("cid-1", 0)

        assert dictResult["bSuccess"] is True
        assert dictStep["saTestCommands"] == []
        assert dictStep["dictVerification"]["sUnitTest"] == "untested"
        assert dictStep["dictVerification"]["sIntegrity"] == "untested"
        dictCtx["save"].assert_called_once()


# -------------------------------------------------------------------
# Save and run test route (lines 215-249)
# -------------------------------------------------------------------

class TestSaveAndRunTestRoute:
    def _fnRegisterAndCapture(self, dictCtx):
        from vaibify.gui.routes import testRoutes

        app = MagicMock()
        listHandlers = {}

        def fnCapturePost(sPath):
            def fnDecorator(fnHandler):
                listHandlers[sPath] = fnHandler
                return fnHandler
            return fnDecorator

        app.post = fnCapturePost
        testRoutes._fnRegisterTestSaveAndRun(app, dictCtx)
        return listHandlers

    @pytest.mark.asyncio
    async def test_save_and_run_passing(self):
        dictCtx = _fdictBuildContext()
        dictCtx["docker"].ftResultExecuteCommand = MagicMock(
            return_value=(0, "1 passed"))

        listHandlers = self._fnRegisterAndCapture(dictCtx)
        sPath = (
            "/api/steps/{sContainerId}/{iStepIndex}"
            "/save-and-run-test"
        )
        fnHandler = listHandlers[sPath]

        mockRequest = MagicMock()
        mockRequest.sFilePath = "/workspace/test_foo.py"
        mockRequest.sContent = "import pytest\ndef test_x(): pass"

        dictStep = {
            "sDirectory": ".",
            "dictTests": {},
            "dictVerification": {},
        }
        dictWorkflow = {"listSteps": [dictStep]}

        with patch(
            "vaibify.gui.routes.testRoutes.fdictRequireWorkflow",
            return_value=dictWorkflow,
        ), patch(
            "vaibify.gui.routes.testRoutes._fnRecordTestResult",
        ) as mockRecord, patch(
            "vaibify.gui.routes.testRoutes._fnRegisterTestCommand",
        ) as mockRegister:
            dictResult = await fnHandler("cid-1", 0, mockRequest)

        assert dictResult["bPassed"] is True
        assert dictResult["iExitCode"] == 0
        dictCtx["docker"].fnWriteFile.assert_called_once()
        mockRecord.assert_called_once()
        mockRegister.assert_called_once()
        dictCtx["save"].assert_called_once()

    @pytest.mark.asyncio
    async def test_save_and_run_failing(self):
        dictCtx = _fdictBuildContext()
        dictCtx["docker"].ftResultExecuteCommand = MagicMock(
            return_value=(1, "FAILED"))

        listHandlers = self._fnRegisterAndCapture(dictCtx)
        sPath = (
            "/api/steps/{sContainerId}/{iStepIndex}"
            "/save-and-run-test"
        )
        fnHandler = listHandlers[sPath]

        mockRequest = MagicMock()
        mockRequest.sFilePath = "/workspace/test_foo.py"
        mockRequest.sContent = "bad test"

        dictWorkflow = {"listSteps": [{
            "sDirectory": "/ws",
            "dictTests": {},
            "dictVerification": {},
        }]}

        with patch(
            "vaibify.gui.routes.testRoutes.fdictRequireWorkflow",
            return_value=dictWorkflow,
        ), patch(
            "vaibify.gui.routes.testRoutes._fnRecordTestResult",
        ), patch(
            "vaibify.gui.routes.testRoutes._fnRegisterTestCommand",
        ):
            dictResult = await fnHandler("cid-1", 0, mockRequest)

        assert dictResult["bPassed"] is False
        assert dictResult["iExitCode"] == 1


# -------------------------------------------------------------------
# Run tests route (lines 273-294)
# -------------------------------------------------------------------

class TestRunTestsRoute:
    def _fnRegisterAndCapture(self, dictCtx):
        from vaibify.gui.routes import testRoutes

        app = MagicMock()
        listHandlers = {}

        def fnCapturePost(sPath):
            def fnDecorator(fnHandler):
                listHandlers[sPath] = fnHandler
                return fnHandler
            return fnDecorator

        app.post = fnCapturePost
        testRoutes._fnRegisterTestRun(app, dictCtx)
        return listHandlers

    @pytest.mark.asyncio
    async def test_no_commands_raises_400(self):
        dictCtx = _fdictBuildContext()
        listHandlers = self._fnRegisterAndCapture(dictCtx)
        sPath = "/api/steps/{sContainerId}/{iStepIndex}/run-tests"
        fnHandler = listHandlers[sPath]

        dictStep = {
            "sDirectory": "/ws",
            "dictTests": {},
            "dictVerification": {},
        }
        dictWorkflow = {"listSteps": [dictStep]}

        with patch(
            "vaibify.gui.routes.testRoutes.fdictRequireWorkflow",
            return_value=dictWorkflow,
        ), patch(
            "vaibify.gui.routes.testRoutes._flistResolveTestCommands",
            return_value=[],
        ), patch(
            "vaibify.gui.routes.testRoutes.flistBuildTestCommands",
            create=True,
        ):
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as excInfo:
                await fnHandler("cid-1", 0)
            assert excInfo.value.status_code == 400

    @pytest.mark.asyncio
    async def test_run_all_categories_success(self):
        dictCtx = _fdictBuildContext()
        listHandlers = self._fnRegisterAndCapture(dictCtx)
        sPath = "/api/steps/{sContainerId}/{iStepIndex}/run-tests"
        fnHandler = listHandlers[sPath]

        dictStep = {
            "sDirectory": "/ws",
            "dictTests": {
                "dictIntegrity": {"saCommands": ["pytest"]},
            },
            "dictVerification": {},
        }
        dictWorkflow = {"listSteps": [dictStep]}

        dictCatResults = {
            "dictIntegrity": {"bPassed": True, "sOutput": "ok"},
        }

        with patch(
            "vaibify.gui.routes.testRoutes.fdictRequireWorkflow",
            return_value=dictWorkflow,
        ), patch(
            "vaibify.gui.routes.testRoutes._flistResolveTestCommands",
            return_value=["pytest"],
        ), patch(
            "vaibify.gui.routes.testRoutes._fdictRunAllTestCategories",
            new_callable=AsyncMock,
            return_value=dictCatResults,
        ), patch(
            "vaibify.gui.routes.testRoutes._fnRecordTestResult",
        ), patch(
            "vaibify.gui.routes.testRoutes._fdictBuildTestResponse",
            return_value={"bAllPassed": True},
        ) as mockBuild, patch(
            "vaibify.gui.routes.testRoutes.flistBuildTestCommands",
            create=True,
        ):
            dictResult = await fnHandler("cid-1", 0)

        assert dictResult["bAllPassed"] is True
        dictCtx["save"].assert_called_once()

    @pytest.mark.asyncio
    async def test_run_all_categories_mixed_failure(self):
        dictCtx = _fdictBuildContext()
        listHandlers = self._fnRegisterAndCapture(dictCtx)
        sPath = "/api/steps/{sContainerId}/{iStepIndex}/run-tests"
        fnHandler = listHandlers[sPath]

        dictStep = {
            "sDirectory": "/ws",
            "dictTests": {},
            "dictVerification": {},
        }
        dictWorkflow = {"listSteps": [dictStep]}

        dictCatResults = {
            "dictIntegrity": {"bPassed": True, "sOutput": ""},
            "dictQualitative": {"bPassed": False, "sOutput": "fail"},
        }

        with patch(
            "vaibify.gui.routes.testRoutes.fdictRequireWorkflow",
            return_value=dictWorkflow,
        ), patch(
            "vaibify.gui.routes.testRoutes._flistResolveTestCommands",
            return_value=["pytest"],
        ), patch(
            "vaibify.gui.routes.testRoutes._fdictRunAllTestCategories",
            new_callable=AsyncMock,
            return_value=dictCatResults,
        ), patch(
            "vaibify.gui.routes.testRoutes._fnRecordTestResult",
        ) as mockRecord, patch(
            "vaibify.gui.routes.testRoutes._fdictBuildTestResponse",
            return_value={"bAllPassed": False},
        ), patch(
            "vaibify.gui.routes.testRoutes.flistBuildTestCommands",
            create=True,
        ):
            dictResult = await fnHandler("cid-1", 0)

        bPassedArg = mockRecord.call_args[0][1]
        assert bPassedArg is False


# -------------------------------------------------------------------
# _fnApplyGeneratedTests (lines 63-77)
# -------------------------------------------------------------------

class TestFnApplyGeneratedTests:
    def test_stores_categories_and_saves(self):
        dictCtx = _fdictBuildContext()
        dictStep = {"sDirectory": "/ws", "dictTests": {}}
        dictWorkflow = {"listSteps": [dictStep]}
        dictResult = {
            "dictIntegrity": {"saCommands": ["pytest"]},
            "dictQualitative": {"saCommands": ["pytest"]},
        }

        with patch(
            "vaibify.gui.workflowManager.flistBuildTestCommands",
            return_value=["pytest test_i.py", "pytest test_q.py"],
        ):
            _fnApplyGeneratedTests(
                dictCtx, "cid-1", dictWorkflow, 0, dictResult)

        assert "dictIntegrity" in dictStep["dictTests"]
        assert "dictQualitative" in dictStep["dictTests"]
        assert dictStep["saTestCommands"] == [
            "pytest test_i.py", "pytest test_q.py"]
        dictCtx["save"].assert_called_once()


# -------------------------------------------------------------------
# _fdictRunAllTestCategories (lines 92-113)
# -------------------------------------------------------------------

class TestFdictRunAllTestCategories:
    @pytest.mark.asyncio
    async def test_runs_categories_and_records(self):
        dictCtx = _fdictBuildContext()
        dictStep = {
            "sDirectory": ".",
            "dictTests": {
                "dictIntegrity": {"saCommands": ["pytest"]},
                "dictQualitative": {"saCommands": ["pytest"]},
            },
            "dictVerification": {},
        }

        with patch(
            "vaibify.gui.routes.testRoutes._fdictRunOneTestCategory",
            new_callable=AsyncMock,
            side_effect=[
                {"bPassed": True, "sOutput": "ok"},
                {"bPassed": False, "sOutput": "fail"},
                None,
            ],
        ):
            dictResults = await _fdictRunAllTestCategories(
                dictCtx, "cid-1", dictStep)

        assert dictStep["dictVerification"]["sIntegrity"] == "passed"
        assert dictStep["dictVerification"][
            "sQualitative"] == "failed"

    @pytest.mark.asyncio
    async def test_skips_none_results(self):
        dictCtx = _fdictBuildContext()
        dictStep = {
            "sDirectory": ".",
            "dictTests": {},
            "dictVerification": {},
        }

        with patch(
            "vaibify.gui.routes.testRoutes._fdictRunOneTestCategory",
            new_callable=AsyncMock,
            return_value=None,
        ):
            dictResults = await _fdictRunAllTestCategories(
                dictCtx, "cid-1", dictStep)

        assert dictResults == {}


# -------------------------------------------------------------------
# _fdictRunOneTestCategory (lines 116-134)
# -------------------------------------------------------------------

class TestFdictRunOneTestCategory:
    @pytest.mark.asyncio
    async def test_returns_none_for_empty_commands(self):
        dictCtx = _fdictBuildContext()
        dictStep = {
            "dictTests": {"dictIntegrity": {"saCommands": []}},
        }
        dictResult = await _fdictRunOneTestCategory(
            dictCtx, "cid-1", dictStep, "/ws", "dictIntegrity")
        assert dictResult is None

    @pytest.mark.asyncio
    async def test_returns_result_dict(self):
        dictCtx = _fdictBuildContext()
        dictCtx["docker"].ftResultExecuteCommand = MagicMock(
            return_value=(0, "ok"))
        dictStep = {
            "dictTests": {
                "dictIntegrity": {
                    "saCommands": ["pytest test.py"],
                },
            },
        }
        dictResult = await _fdictRunOneTestCategory(
            dictCtx, "cid-1", dictStep, "/ws", "dictIntegrity")
        assert dictResult["bPassed"] is True
        assert dictResult["sOutput"] == "ok"
        assert dictResult["iExitCode"] == 0

    @pytest.mark.asyncio
    async def test_returns_failure(self):
        dictCtx = _fdictBuildContext()
        dictCtx["docker"].ftResultExecuteCommand = MagicMock(
            return_value=(2, "error"))
        dictStep = {
            "dictTests": {
                "dictIntegrity": {
                    "saCommands": ["pytest test.py"],
                },
            },
        }
        dictResult = await _fdictRunOneTestCategory(
            dictCtx, "cid-1", dictStep, "/ws", "dictIntegrity")
        assert dictResult["bPassed"] is False
        assert dictResult["iExitCode"] == 2

    @pytest.mark.asyncio
    async def test_missing_category_returns_none(self):
        dictCtx = _fdictBuildContext()
        dictStep = {"dictTests": {}}
        dictResult = await _fdictRunOneTestCategory(
            dictCtx, "cid-1", dictStep, "/ws", "dictIntegrity")
        assert dictResult is None
