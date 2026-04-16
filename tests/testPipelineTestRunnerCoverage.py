"""Tests for vaibify.gui.pipelineTestRunner covering uncovered lines."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fnRunAsync(coroutine):
    """Run a coroutine synchronously for testing."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coroutine)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# _fdictRunTestsByCategory — line 65 (structured dictTests branch)
# ---------------------------------------------------------------------------

class TestFdictRunTestsByCategory:
    """Cover the per-category iteration path (line 65)."""

    @patch(
        "vaibify.gui.pipelineTestRunner._fdictRunOneCategoryCommands",
        new_callable=AsyncMock,
    )
    def test_structured_tests_run_per_category(self, mockRunOne):
        """When dictTests has categories with commands, each is dispatched."""
        from vaibify.gui.pipelineTestRunner import _fdictRunTestsByCategory

        mockRunOne.return_value = {"iExitCode": 0, "sOutput": "ok"}
        dictStep = {
            "dictTests": {
                "dictIntegrity": {"saCommands": ["pytest -k integrity"]},
                "dictQualitative": {"saCommands": []},
                "dictQuantitative": {"saCommands": ["pytest -k quant"]},
            }
        }
        mockDocker = MagicMock()
        sContainerId = "ctr1"
        sStepDir = "/workspace/step1"
        dictVars = {}
        fnCallback = AsyncMock()

        dictResults = _fnRunAsync(
            _fdictRunTestsByCategory(
                mockDocker, sContainerId, dictStep,
                sStepDir, dictVars, fnCallback,
            )
        )
        assert "dictIntegrity" in dictResults
        assert "dictQuantitative" in dictResults
        assert "dictQualitative" not in dictResults
        assert mockRunOne.call_count == 2

    @patch(
        "vaibify.gui.pipelineTestRunner._fdictRunLegacyTestCommands",
        new_callable=AsyncMock,
    )
    def test_falls_back_to_legacy_when_no_structured(self, mockLegacy):
        """When no category has commands, falls through to legacy path."""
        from vaibify.gui.pipelineTestRunner import _fdictRunTestsByCategory

        mockLegacy.return_value = {"legacy": {"iExitCode": 0, "sOutput": ""}}
        dictStep = {"dictTests": {}}
        dictResults = _fnRunAsync(
            _fdictRunTestsByCategory(
                MagicMock(), "ctr1", dictStep, "/ws", {}, AsyncMock(),
            )
        )
        assert "legacy" in dictResults
        mockLegacy.assert_awaited_once()


# ---------------------------------------------------------------------------
# _fdictRunLegacyTestCommands — line 104 (empty saTestCommands)
# ---------------------------------------------------------------------------

class TestFdictRunLegacyTestCommands:
    """Cover the empty-command early return (line 104)."""

    def test_returns_empty_dict_when_no_commands(self):
        from vaibify.gui.pipelineTestRunner import _fdictRunLegacyTestCommands

        dictStep = {"saTestCommands": []}
        dictResult = _fnRunAsync(
            _fdictRunLegacyTestCommands(
                MagicMock(), "ctr1", dictStep, "/ws", {}, AsyncMock(),
            )
        )
        assert dictResult == {}

    def test_returns_empty_dict_when_key_missing(self):
        from vaibify.gui.pipelineTestRunner import _fdictRunLegacyTestCommands

        dictResult = _fnRunAsync(
            _fdictRunLegacyTestCommands(
                MagicMock(), "ctr1", {}, "/ws", {}, AsyncMock(),
            )
        )
        assert dictResult == {}


# ---------------------------------------------------------------------------
# fnRunAllTests — lines 179-196
# ---------------------------------------------------------------------------

class TestFnRunAllTests:
    """Cover the public fnRunAllTests entry point."""

    @patch("vaibify.gui.pipelineTestRunner._fnEmitCompletion", new_callable=AsyncMock)
    @patch("vaibify.gui.pipelineTestRunner._fiRunTestsForAllSteps", new_callable=AsyncMock)
    @patch("vaibify.gui.pipelineTestRunner._fdictBuildWorkflowVars")
    def test_with_provided_workflow(self, mockBuildVars, mockRunAll, mockEmit):
        """When dictWorkflow is provided, skips loading."""
        from vaibify.gui.pipelineTestRunner import fnRunAllTests

        mockBuildVars.return_value = {"sVar": "val"}
        mockRunAll.return_value = 0
        dictWorkflow = {"listSteps": []}
        fnCallback = AsyncMock()

        iResult = _fnRunAsync(
            fnRunAllTests(
                MagicMock(), "ctr1", "/ws", fnCallback,
                dictWorkflow=dictWorkflow,
            )
        )
        assert iResult == 0
        fnCallback.assert_any_await(
            {"sType": "started", "sCommand": "runAllTests"}
        )
        mockEmit.assert_awaited_once()

    @patch("vaibify.gui.pipelineTestRunner._fnEmitCompletion", new_callable=AsyncMock)
    @patch("vaibify.gui.pipelineTestRunner._fiRunTestsForAllSteps", new_callable=AsyncMock)
    @patch("vaibify.gui.pipelineTestRunner._fdictBuildWorkflowVars")
    @patch("vaibify.gui.pipelineRunner._fdictLoadWorkflow", new_callable=AsyncMock)
    def test_loads_workflow_when_none(self, mockLoad, mockBuildVars, mockRunAll, mockEmit):
        """When dictWorkflow is None, loads it from container."""
        from vaibify.gui.pipelineTestRunner import fnRunAllTests

        dictWorkflow = {"listSteps": []}
        mockLoad.return_value = (dictWorkflow, "/path/wf.json")
        mockBuildVars.return_value = {}
        mockRunAll.return_value = 0
        fnCallback = AsyncMock()

        iResult = _fnRunAsync(
            fnRunAllTests(MagicMock(), "ctr1", "/ws", fnCallback)
        )
        assert iResult == 0
        mockLoad.assert_awaited_once()

    @patch("vaibify.gui.pipelineRunner._fdictLoadWorkflow", new_callable=AsyncMock)
    def test_returns_1_when_workflow_load_fails(self, mockLoad):
        """When workflow loading returns None, return 1 immediately."""
        from vaibify.gui.pipelineTestRunner import fnRunAllTests

        mockLoad.return_value = (None, None)
        fnCallback = AsyncMock()

        iResult = _fnRunAsync(
            fnRunAllTests(MagicMock(), "ctr1", "/ws", fnCallback)
        )
        assert iResult == 1

    @patch("vaibify.gui.pipelineTestRunner._fnEmitCompletion", new_callable=AsyncMock)
    @patch("vaibify.gui.pipelineTestRunner._fiRunTestsForAllSteps", new_callable=AsyncMock)
    @patch("vaibify.gui.pipelineTestRunner._fdictBuildWorkflowVars")
    def test_returns_nonzero_on_failure(self, mockBuildVars, mockRunAll, mockEmit):
        """When tests fail, the exit code propagates."""
        from vaibify.gui.pipelineTestRunner import fnRunAllTests

        mockBuildVars.return_value = {}
        mockRunAll.return_value = 1
        fnCallback = AsyncMock()

        iResult = _fnRunAsync(
            fnRunAllTests(
                MagicMock(), "ctr1", "/ws", fnCallback,
                dictWorkflow={"listSteps": []},
            )
        )
        assert iResult == 1


# ---------------------------------------------------------------------------
# _fiRunTestsForAllSteps — lines 204-219
# ---------------------------------------------------------------------------

class TestFiRunTestsForAllSteps:
    """Cover the step iteration logic."""

    @patch("vaibify.gui.pipelineTestRunner._fiRunStepTests", new_callable=AsyncMock)
    @patch(
        "vaibify.gui.pipelineTestRunner._flistResolveTestCommands",
        return_value=["pytest"],
    )
    def test_runs_enabled_steps_with_tests(self, mockResolve, mockRunStep):
        from vaibify.gui.pipelineTestRunner import _fiRunTestsForAllSteps

        mockRunStep.return_value = 0
        dictWorkflow = {
            "listSteps": [
                {"sName": "A", "bEnabled": True},
                {"sName": "B", "bEnabled": True},
            ]
        }
        iResult = _fnRunAsync(
            _fiRunTestsForAllSteps(
                MagicMock(), "ctr1", dictWorkflow, {}, AsyncMock(),
            )
        )
        assert iResult == 0
        assert mockRunStep.call_count == 2

    @patch("vaibify.gui.pipelineTestRunner._fiRunStepTests", new_callable=AsyncMock)
    @patch(
        "vaibify.gui.pipelineTestRunner._flistResolveTestCommands",
        return_value=["pytest"],
    )
    def test_skips_disabled_steps(self, mockResolve, mockRunStep):
        from vaibify.gui.pipelineTestRunner import _fiRunTestsForAllSteps

        mockRunStep.return_value = 0
        dictWorkflow = {
            "listSteps": [
                {"sName": "A", "bEnabled": False},
                {"sName": "B", "bEnabled": True},
            ]
        }
        iResult = _fnRunAsync(
            _fiRunTestsForAllSteps(
                MagicMock(), "ctr1", dictWorkflow, {}, AsyncMock(),
            )
        )
        assert iResult == 0
        assert mockRunStep.call_count == 1

    @patch(
        "vaibify.gui.pipelineTestRunner._flistResolveTestCommands",
        return_value=[],
    )
    def test_skips_steps_without_tests(self, mockResolve):
        from vaibify.gui.pipelineTestRunner import _fiRunTestsForAllSteps

        dictWorkflow = {
            "listSteps": [{"sName": "A", "bEnabled": True}]
        }
        iResult = _fnRunAsync(
            _fiRunTestsForAllSteps(
                MagicMock(), "ctr1", dictWorkflow, {}, AsyncMock(),
            )
        )
        assert iResult == 0

    @patch("vaibify.gui.pipelineTestRunner._fiRunStepTests", new_callable=AsyncMock)
    @patch(
        "vaibify.gui.pipelineTestRunner._flistResolveTestCommands",
        return_value=["pytest"],
    )
    def test_returns_1_when_any_step_fails(self, mockResolve, mockRunStep):
        from vaibify.gui.pipelineTestRunner import _fiRunTestsForAllSteps

        mockRunStep.side_effect = [0, 1]
        dictWorkflow = {
            "listSteps": [
                {"sName": "A", "bEnabled": True},
                {"sName": "B", "bEnabled": True},
            ]
        }
        iResult = _fnRunAsync(
            _fiRunTestsForAllSteps(
                MagicMock(), "ctr1", dictWorkflow, {}, AsyncMock(),
            )
        )
        assert iResult == 1


# ---------------------------------------------------------------------------
# _fnEmitStepBanner — lines 226-232
# ---------------------------------------------------------------------------

class TestFnEmitStepBanner:
    """Cover banner emission."""

    @patch("vaibify.gui.pipelineTestRunner._fnEmitBanner", new_callable=AsyncMock)
    @patch(
        "vaibify.gui.pipelineTestRunner.fsComputeStepLabel",
        return_value="A01",
    )
    def test_emits_banner_and_step_started(self, mockLabel, mockBanner):
        from vaibify.gui.pipelineTestRunner import _fnEmitStepBanner

        fnCallback = AsyncMock()
        dictStep = {"sName": "Build"}
        dictWorkflow = {"listSteps": [dictStep]}

        _fnRunAsync(
            _fnEmitStepBanner(fnCallback, 1, dictStep, dictWorkflow)
        )
        mockBanner.assert_awaited_once()
        fnCallback.assert_awaited_with(
            {"sType": "stepStarted", "iStepNumber": 1}
        )

    @patch("vaibify.gui.pipelineTestRunner._fnEmitBanner", new_callable=AsyncMock)
    @patch(
        "vaibify.gui.pipelineTestRunner.fsComputeStepLabel",
        return_value="I02",
    )
    def test_uses_default_name_when_missing(self, mockLabel, mockBanner):
        from vaibify.gui.pipelineTestRunner import _fnEmitStepBanner

        fnCallback = AsyncMock()
        dictStep = {}
        dictWorkflow = {"listSteps": [dictStep]}

        _fnRunAsync(
            _fnEmitStepBanner(fnCallback, 2, dictStep, dictWorkflow)
        )
        sBannerCall = mockBanner.call_args
        assert sBannerCall[0][1] == 2
        assert sBannerCall[0][2] == "Step 2"


# ---------------------------------------------------------------------------
# _fiRunStepTests — lines 243-258
# ---------------------------------------------------------------------------

class TestFiRunStepTests:
    """Cover the single-step test runner."""

    @patch("vaibify.gui.pipelineTestRunner._fnEmitStepResult", new_callable=AsyncMock)
    @patch("vaibify.gui.pipelineTestRunner._fiRunTestCommands", new_callable=AsyncMock)
    @patch("vaibify.gui.pipelineTestRunner._fnEmitStepBanner", new_callable=AsyncMock)
    def test_runs_tests_and_records_hashes(
        self, mockBanner, mockRunTests, mockEmitResult,
    ):
        from vaibify.gui.pipelineTestRunner import _fiRunStepTests

        mockRunTests.return_value = 0
        dictStep = {"sName": "Analyze", "sDirectory": "/workspace/analyze"}
        dictWorkflow = {"listSteps": [dictStep]}
        fnCallback = AsyncMock()

        iResult = _fnRunAsync(
            _fiRunStepTests(
                MagicMock(), "ctr1", dictStep, {}, fnCallback, 1, dictWorkflow,
            )
        )
        assert iResult == 0
        mockBanner.assert_awaited_once()
        mockRunTests.assert_awaited_once()
        mockEmitResult.assert_awaited_once()

    @patch("vaibify.gui.pipelineTestRunner._fnEmitStepResult", new_callable=AsyncMock)
    @patch("vaibify.gui.pipelineTestRunner._fiRunTestCommands", new_callable=AsyncMock)
    @patch("vaibify.gui.pipelineTestRunner._fnEmitStepBanner", new_callable=AsyncMock)
    def test_propagates_nonzero_exit_code(
        self, mockBanner, mockRunTests, mockEmitResult,
    ):
        from vaibify.gui.pipelineTestRunner import _fiRunStepTests

        mockRunTests.return_value = 1
        dictStep = {"sDirectory": ""}
        dictWorkflow = {"listSteps": [dictStep]}
        fnCallback = AsyncMock()

        iResult = _fnRunAsync(
            _fiRunStepTests(
                MagicMock(), "ctr1", dictStep, {}, fnCallback, 1, dictWorkflow,
            )
        )
        assert iResult == 1

    @patch("vaibify.gui.pipelineTestRunner._fnEmitStepResult", new_callable=AsyncMock)
    @patch("vaibify.gui.pipelineTestRunner._fiRunTestCommands", new_callable=AsyncMock)
    @patch("vaibify.gui.pipelineTestRunner._fnEmitStepBanner", new_callable=AsyncMock)
    def test_uses_empty_directory_when_missing(
        self, mockBanner, mockRunTests, mockEmitResult,
    ):
        from vaibify.gui.pipelineTestRunner import _fiRunStepTests

        mockRunTests.return_value = 0
        dictStep = {}
        dictWorkflow = {"listSteps": [dictStep]}

        _fnRunAsync(
            _fiRunStepTests(
                MagicMock(), "ctr1", dictStep, {}, AsyncMock(), 1, dictWorkflow,
            )
        )
        sStepDirArg = mockRunTests.call_args[0][3]
        assert sStepDirArg == ""
