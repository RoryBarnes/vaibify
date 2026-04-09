"""Unit tests for vaibify.gui.routes.plotRoutes."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vaibify.gui.routes.plotRoutes import (
    _flistStandardizedBasenames,
    _fsFindPlotPath,
    _fsFindStandardForFile,
    _flistConvertToStandards,
    _flistVerifyConverted,
    _fdictCheckStandardsExist,
)


# ── Synchronous helpers ──────────────────────────────────────────


class TestFlistStandardizedBasenames:
    def test_returns_all_basenames_when_no_target(self):
        listPlots = [("/a/plot1.pdf", "plot1.pdf"),
                      ("/a/plot2.pdf", "plot2.pdf")]
        listResult = _flistStandardizedBasenames(listPlots, "")
        assert listResult == ["plot1.pdf", "plot2.pdf"]

    def test_filters_to_target_file(self):
        listPlots = [("/a/plot1.pdf", "plot1.pdf"),
                      ("/a/plot2.pdf", "plot2.pdf")]
        listResult = _flistStandardizedBasenames(
            listPlots, "plot2.pdf")
        assert listResult == ["plot2.pdf"]

    def test_returns_empty_when_target_not_found(self):
        listPlots = [("/a/plot1.pdf", "plot1.pdf")]
        listResult = _flistStandardizedBasenames(
            listPlots, "missing.pdf")
        assert listResult == []

    def test_returns_empty_for_empty_list(self):
        listResult = _flistStandardizedBasenames([], "")
        assert listResult == []


class TestFsFindPlotPath:
    def test_finds_by_basename(self):
        listPlots = [("/workspace/out/fig.pdf", "fig.pdf")]
        sResult = _fsFindPlotPath(listPlots, "fig.pdf")
        assert sResult == "/workspace/out/fig.pdf"

    def test_finds_by_suffix(self):
        listPlots = [("/workspace/out/fig.pdf", "fig.pdf")]
        sResult = _fsFindPlotPath(listPlots, "out/fig.pdf")
        assert sResult == "/workspace/out/fig.pdf"

    def test_returns_empty_when_not_found(self):
        listPlots = [("/workspace/out/fig.pdf", "fig.pdf")]
        sResult = _fsFindPlotPath(listPlots, "nope.pdf")
        assert sResult == ""

    def test_empty_list(self):
        assert _fsFindPlotPath([], "x.pdf") == ""


class TestFsFindStandardForFile:
    @patch("vaibify.gui.routes.plotRoutes._fsPlotStandardPath",
           return_value="fig_standard.png")
    def test_finds_standard_path(self, _mock):
        listPlots = [("/workspace/out/fig.pdf", "fig.pdf")]
        sResult = _fsFindStandardForFile(listPlots, "fig.pdf")
        assert sResult == "/workspace/out/fig_standard.png"

    @patch("vaibify.gui.routes.plotRoutes._fsPlotStandardPath",
           return_value="fig_standard.png")
    def test_finds_by_suffix(self, _mock):
        listPlots = [("/workspace/out/fig.pdf", "fig.pdf")]
        sResult = _fsFindStandardForFile(
            listPlots, "out/fig.pdf")
        assert sResult == "/workspace/out/fig_standard.png"

    def test_returns_empty_when_not_found(self):
        listPlots = [("/workspace/out/fig.pdf", "fig.pdf")]
        sResult = _fsFindStandardForFile(listPlots, "nope.pdf")
        assert sResult == ""


# ── Async helpers ────────────────────────────────────────────────


def _fnRunAsync(coroutine):
    """Run a coroutine synchronously for testing."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coroutine)
    finally:
        loop.close()


def _fdictMakeContext(ftResult=(0, "")):
    """Build a minimal dictCtx with a mock Docker client."""
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand = MagicMock(
        return_value=ftResult)
    return {"docker": mockDocker}


class TestFlistConvertToStandards:
    @patch("vaibify.gui.routes.plotRoutes._fsPlotStandardPath",
           side_effect=lambda sBase: f"{sBase}_standard.png")
    @patch("vaibify.gui.routes.plotRoutes._fsBuildConvertCommand",
           side_effect=lambda sR, sO, sB: f"convert {sB}")
    @patch("vaibify.gui.routes.plotRoutes._flistVerifyConverted",
           new_callable=AsyncMock,
           return_value=["plot1_standard.png"])
    def test_converts_all_plots(
        self, mockVerify, mockBuild, mockStdPath,
    ):
        dictCtx = _fdictMakeContext()
        listPlots = [("/out/plot1.pdf", "plot1.pdf"),
                      ("/out/plot2.pdf", "plot2.pdf")]
        listResult = _fnRunAsync(
            _flistConvertToStandards(
                dictCtx, "ctr1", listPlots, ""))
        assert listResult == ["plot1_standard.png"]
        dictCtx["docker"].ftResultExecuteCommand.assert_called_once()
        sCommand = (dictCtx["docker"]
                     .ftResultExecuteCommand.call_args[0][1])
        assert "convert plot1.pdf" in sCommand
        assert "convert plot2.pdf" in sCommand

    @patch("vaibify.gui.routes.plotRoutes._fsPlotStandardPath",
           side_effect=lambda sBase: f"{sBase}_standard.png")
    @patch("vaibify.gui.routes.plotRoutes._fsBuildConvertCommand",
           side_effect=lambda sR, sO, sB: f"convert {sB}")
    @patch("vaibify.gui.routes.plotRoutes._flistVerifyConverted",
           new_callable=AsyncMock,
           return_value=["plot2_standard.png"])
    def test_filters_to_target(
        self, mockVerify, mockBuild, mockStdPath,
    ):
        dictCtx = _fdictMakeContext()
        listPlots = [("/out/plot1.pdf", "plot1.pdf"),
                      ("/out/plot2.pdf", "plot2.pdf")]
        listResult = _fnRunAsync(
            _flistConvertToStandards(
                dictCtx, "ctr1", listPlots, "plot2.pdf"))
        sCommand = (dictCtx["docker"]
                     .ftResultExecuteCommand.call_args[0][1])
        assert "convert plot1.pdf" not in sCommand
        assert "convert plot2.pdf" in sCommand

    def test_returns_empty_when_no_commands(self):
        dictCtx = _fdictMakeContext()
        listPlots = [("/out/plot1.pdf", "plot1.pdf")]
        listResult = _fnRunAsync(
            _flistConvertToStandards(
                dictCtx, "ctr1", listPlots, "nonexistent.pdf"))
        assert listResult == []


class TestFlistVerifyConverted:
    def test_verifies_existing_files(self):
        dictCtx = _fdictMakeContext(ftResult=(0, ""))
        listPlots = [("/out/plot1.pdf", "plot1.pdf")]
        listConverted = ["plot1_standard.png"]
        listResult = _fnRunAsync(
            _flistVerifyConverted(
                dictCtx, "ctr1", listPlots,
                listConverted, ""))
        assert listResult == ["plot1_standard.png"]

    def test_excludes_missing_files(self):
        dictCtx = _fdictMakeContext(ftResult=(1, ""))
        listPlots = [("/out/plot1.pdf", "plot1.pdf")]
        listConverted = ["plot1_standard.png"]
        listResult = _fnRunAsync(
            _flistVerifyConverted(
                dictCtx, "ctr1", listPlots,
                listConverted, ""))
        assert listResult == []

    def test_filters_by_target(self):
        dictCtx = _fdictMakeContext(ftResult=(0, ""))
        listPlots = [("/out/plot1.pdf", "plot1.pdf"),
                      ("/out/plot2.pdf", "plot2.pdf")]
        listConverted = ["plot1_standard.png",
                          "plot2_standard.png"]
        listResult = _fnRunAsync(
            _flistVerifyConverted(
                dictCtx, "ctr1", listPlots,
                listConverted, "plot2.pdf"))
        assert listResult == ["plot2_standard.png"]


class TestFdictCheckStandardsExist:
    def test_returns_empty_dict_for_empty_plots(self):
        dictCtx = _fdictMakeContext()
        dictResult = _fnRunAsync(
            _fdictCheckStandardsExist(dictCtx, "ctr1", []))
        assert dictResult == {}

    @patch("vaibify.gui.routes.plotRoutes._fsPlotStandardPath",
           side_effect=lambda sBase: f"{sBase}_standard.png")
    def test_detects_existing_standards(self, _mock):
        dictCtx = _fdictMakeContext(ftResult=(0, "Y\nY\n"))
        listPlots = [("/out/plot1.pdf", "plot1.pdf"),
                      ("/out/plot2.pdf", "plot2.pdf")]
        dictResult = _fnRunAsync(
            _fdictCheckStandardsExist(
                dictCtx, "ctr1", listPlots))
        assert dictResult == {"plot1.pdf": True,
                              "plot2.pdf": True}

    @patch("vaibify.gui.routes.plotRoutes._fsPlotStandardPath",
           side_effect=lambda sBase: f"{sBase}_standard.png")
    def test_detects_missing_standards(self, _mock):
        dictCtx = _fdictMakeContext(ftResult=(0, "Y\nN\n"))
        listPlots = [("/out/plot1.pdf", "plot1.pdf"),
                      ("/out/plot2.pdf", "plot2.pdf")]
        dictResult = _fnRunAsync(
            _fdictCheckStandardsExist(
                dictCtx, "ctr1", listPlots))
        assert dictResult == {"plot1.pdf": True,
                              "plot2.pdf": False}

    @patch("vaibify.gui.routes.plotRoutes._fsPlotStandardPath",
           side_effect=lambda sBase: f"{sBase}_standard.png")
    def test_handles_short_output(self, _mock):
        dictCtx = _fdictMakeContext(ftResult=(0, "Y\n"))
        listPlots = [("/out/plot1.pdf", "plot1.pdf"),
                      ("/out/plot2.pdf", "plot2.pdf")]
        dictResult = _fnRunAsync(
            _fdictCheckStandardsExist(
                dictCtx, "ctr1", listPlots))
        assert dictResult["plot1.pdf"] is True
        assert dictResult["plot2.pdf"] is False

    @patch("vaibify.gui.routes.plotRoutes._fsPlotStandardPath",
           side_effect=lambda sBase: f"{sBase}_standard.png")
    def test_handles_empty_result(self, _mock):
        dictCtx = _fdictMakeContext(ftResult=None)
        listPlots = [("/out/plot1.pdf", "plot1.pdf")]
        dictResult = _fnRunAsync(
            _fdictCheckStandardsExist(
                dictCtx, "ctr1", listPlots))
        assert dictResult["plot1.pdf"] is False


# ── Route handler tests ──────────────────────────────────────────


class TestRouteStandardizePlots:
    @patch("vaibify.gui.routes.plotRoutes._flistResolvePlotPaths",
           return_value=[("/out/fig.pdf", "fig.pdf")])
    @patch("vaibify.gui.routes.plotRoutes._flistConvertToStandards",
           new_callable=AsyncMock,
           return_value=["fig_standard.png"])
    @patch("vaibify.gui.routes.plotRoutes.fdictRequireWorkflow")
    def test_standardize_success(
        self, mockRequire, mockConvert, mockResolve,
    ):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        dictWorkflow = {
            "listSteps": [{"sDirectory": "/out"}],
        }
        mockRequire.return_value = dictWorkflow
        dictCtx = {
            "require": MagicMock(),
            "workflows": {},
            "variables": MagicMock(return_value={}),
            "docker": MagicMock(),
            "save": MagicMock(),
        }
        app = FastAPI()
        from vaibify.gui.routes.plotRoutes import fnRegisterAll
        fnRegisterAll(app, dictCtx)
        client = TestClient(app)
        response = client.post(
            "/api/steps/ctr1/0/standardize-plots",
            json={"sFileName": ""})
        assert response.status_code == 200
        dictData = response.json()
        assert dictData["bSuccess"] is True
        assert "fig_standard.png" in dictData["listConverted"]
        assert dictData["listStandardizedBasenames"] == [
            "fig.pdf"]
        assert "sTimestamp" in dictData
        dictCtx["save"].assert_called_once()

    @patch("vaibify.gui.routes.plotRoutes._flistResolvePlotPaths",
           return_value=[])
    @patch("vaibify.gui.routes.plotRoutes.fdictRequireWorkflow")
    def test_standardize_no_plots_raises_400(
        self, mockRequire, mockResolve,
    ):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        mockRequire.return_value = {
            "listSteps": [{"sDirectory": "/out"}]}
        dictCtx = {
            "require": MagicMock(),
            "workflows": {},
            "variables": MagicMock(return_value={}),
            "docker": MagicMock(),
            "save": MagicMock(),
        }
        app = FastAPI()
        from vaibify.gui.routes.plotRoutes import fnRegisterAll
        fnRegisterAll(app, dictCtx)
        client = TestClient(app)
        response = client.post(
            "/api/steps/ctr1/0/standardize-plots",
            json={})
        assert response.status_code == 400

    @patch("vaibify.gui.routes.plotRoutes._flistResolvePlotPaths",
           return_value=[("/out/fig.pdf", "fig.pdf")])
    @patch("vaibify.gui.routes.plotRoutes._flistConvertToStandards",
           new_callable=AsyncMock, return_value=[])
    @patch("vaibify.gui.routes.plotRoutes.fdictRequireWorkflow")
    def test_standardize_conversion_failure_raises_500(
        self, mockRequire, mockConvert, mockResolve,
    ):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        mockRequire.return_value = {
            "listSteps": [{"sDirectory": "/out"}]}
        dictCtx = {
            "require": MagicMock(),
            "workflows": {},
            "variables": MagicMock(return_value={}),
            "docker": MagicMock(),
            "save": MagicMock(),
        }
        app = FastAPI()
        from vaibify.gui.routes.plotRoutes import fnRegisterAll
        fnRegisterAll(app, dictCtx)
        client = TestClient(app)
        response = client.post(
            "/api/steps/ctr1/0/standardize-plots",
            json={})
        assert response.status_code == 500


class TestRouteComparePlot:
    @patch("vaibify.gui.routes.plotRoutes._flistResolvePlotPaths",
           return_value=[("/out/fig.pdf", "fig.pdf")])
    @patch("vaibify.gui.routes.plotRoutes._fsFindStandardForFile",
           return_value="/out/fig_standard.png")
    @patch("vaibify.gui.routes.plotRoutes._fsFindPlotPath",
           return_value="/out/fig.pdf")
    @patch("vaibify.gui.routes.plotRoutes.fdictRequireWorkflow")
    def test_compare_success(
        self, mockRequire, mockFind, mockStd, mockResolve,
    ):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        mockRequire.return_value = {
            "listSteps": [{"sDirectory": "/out"}]}
        dictCtx = {
            "require": MagicMock(),
            "workflows": {},
            "variables": MagicMock(return_value={}),
        }
        app = FastAPI()
        from vaibify.gui.routes.plotRoutes import fnRegisterAll
        fnRegisterAll(app, dictCtx)
        client = TestClient(app)
        response = client.post(
            "/api/steps/ctr1/0/compare-plot",
            json={"sFileName": "fig.pdf"})
        assert response.status_code == 200
        dictData = response.json()
        assert dictData["sPlotPath"] == "/out/fig.pdf"
        assert dictData["sStandardPath"] == "/out/fig_standard.png"

    @patch("vaibify.gui.routes.plotRoutes.fdictRequireWorkflow")
    def test_compare_missing_filename_raises_400(
        self, mockRequire,
    ):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        mockRequire.return_value = {
            "listSteps": [{"sDirectory": "/out"}]}
        dictCtx = {
            "require": MagicMock(),
            "workflows": {},
            "variables": MagicMock(return_value={}),
        }
        app = FastAPI()
        from vaibify.gui.routes.plotRoutes import fnRegisterAll
        fnRegisterAll(app, dictCtx)
        client = TestClient(app)
        response = client.post(
            "/api/steps/ctr1/0/compare-plot",
            json={"sFileName": ""})
        assert response.status_code == 400

    @patch("vaibify.gui.routes.plotRoutes._flistResolvePlotPaths",
           return_value=[("/out/fig.pdf", "fig.pdf")])
    @patch("vaibify.gui.routes.plotRoutes._fsFindStandardForFile",
           return_value="")
    @patch("vaibify.gui.routes.plotRoutes._fsFindPlotPath",
           return_value="/out/fig.pdf")
    @patch("vaibify.gui.routes.plotRoutes.fdictRequireWorkflow")
    def test_compare_no_standard_raises_404(
        self, mockRequire, mockFind, mockStd, mockResolve,
    ):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        mockRequire.return_value = {
            "listSteps": [{"sDirectory": "/out"}]}
        dictCtx = {
            "require": MagicMock(),
            "workflows": {},
            "variables": MagicMock(return_value={}),
        }
        app = FastAPI()
        from vaibify.gui.routes.plotRoutes import fnRegisterAll
        fnRegisterAll(app, dictCtx)
        client = TestClient(app)
        response = client.post(
            "/api/steps/ctr1/0/compare-plot",
            json={"sFileName": "fig.pdf"})
        assert response.status_code == 404


class TestRouteCheckPlotStandards:
    @patch("vaibify.gui.routes.plotRoutes._flistResolvePlotPaths",
           return_value=[("/out/fig.pdf", "fig.pdf")])
    @patch("vaibify.gui.routes.plotRoutes._fdictCheckStandardsExist",
           new_callable=AsyncMock,
           return_value={"fig.pdf": True})
    @patch("vaibify.gui.routes.plotRoutes.fdictRequireWorkflow")
    def test_check_standards(
        self, mockRequire, mockCheck, mockResolve,
    ):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        mockRequire.return_value = {
            "listSteps": [{"sDirectory": "/out"}]}
        dictCtx = {
            "require": MagicMock(),
            "workflows": {},
            "variables": MagicMock(return_value={}),
        }
        app = FastAPI()
        from vaibify.gui.routes.plotRoutes import fnRegisterAll
        fnRegisterAll(app, dictCtx)
        client = TestClient(app)
        response = client.get(
            "/api/steps/ctr1/0/plot-standards")
        assert response.status_code == 200
        dictData = response.json()
        assert dictData["dictStandards"]["fig.pdf"] is True
