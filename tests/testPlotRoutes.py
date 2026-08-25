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


def _fdictMakeContextWithProbe(dictExistsByPath):
    """Build a dictCtx whose existence probe answers per PATH.

    Keyed by path rather than by call order because that is the
    property the typed reads have and the batched command did not: the
    old parser matched stdout lines to plots positionally, so a
    reordered or short answer silently attributed one plot's result to
    another. A double keyed by call order would have reproduced that
    hazard instead of testing it away.
    """
    dictCtx = _fdictMakeContext()
    dictCtx["docker"].fbContainerPathIsFile = MagicMock(
        side_effect=lambda _sCid, sPath: dictExistsByPath[sPath],
    )
    return dictCtx


class TestFlistConvertToStandards:
    @patch("vaibify.gui.routes.plotRoutes._fsPlotStandardPath",
           side_effect=lambda sBase: f"{sBase}_standard.png")
    @patch("vaibify.gui.routes.plotRoutes._fsBuildConvertCommand",
           side_effect=lambda sR, sO, sB: f"convert {sB}")
    @patch("vaibify.gui.routes.plotRoutes._flistVerifyConverted",
           return_value=["plot1_standard.png"])
    def test_converts_all_plots(
        self, mockVerify, mockBuild, mockStdPath,
    ):
        dictCtx = _fdictMakeContext()
        listPlots = [("/out/plot1.pdf", "plot1.pdf"),
                      ("/out/plot2.pdf", "plot2.pdf")]
        listResult = _flistConvertToStandards(
            dictCtx, "ctr1", listPlots, "")
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
           return_value=["plot2_standard.png"])
    def test_filters_to_target(
        self, mockVerify, mockBuild, mockStdPath,
    ):
        dictCtx = _fdictMakeContext()
        listPlots = [("/out/plot1.pdf", "plot1.pdf"),
                      ("/out/plot2.pdf", "plot2.pdf")]
        listResult = _flistConvertToStandards(
            dictCtx, "ctr1", listPlots, "plot2.pdf")
        sCommand = (dictCtx["docker"]
                     .ftResultExecuteCommand.call_args[0][1])
        assert "convert plot1.pdf" not in sCommand
        assert "convert plot2.pdf" in sCommand

    def test_returns_empty_when_no_commands(self):
        dictCtx = _fdictMakeContext()
        listPlots = [("/out/plot1.pdf", "plot1.pdf")]
        listResult = _flistConvertToStandards(
            dictCtx, "ctr1", listPlots, "nonexistent.pdf")
        assert listResult == []


class TestFlistVerifyConverted:
    """The verifier now takes the pairs the conversion itself built.

    It used to take the full plot list, the converted list and the
    target filter, and re-derive the pairing by zipping the first two.
    The old tests here handed it an UNFILTERED converted list -- an
    input the production caller never produces, because that caller
    filters as it builds. So the lists were always aligned under test
    and always offset in production whenever the target was not the
    first plot. The bug lived in the seam between two functions that
    were each tested alone.
    """

    def test_verifies_existing_files(self):
        dictCtx = _fdictMakeContext(ftResult=(0, ""))
        listTargets = [("/out/plot1_standard.png", "plot1_standard.png")]
        listResult = _flistVerifyConverted(dictCtx, "ctr1", listTargets)
        assert listResult == ["plot1_standard.png"]

    def test_excludes_missing_files(self):
        dictCtx = _fdictMakeContext(ftResult=(1, ""))
        listTargets = [("/out/plot1_standard.png", "plot1_standard.png")]
        listResult = _flistVerifyConverted(dictCtx, "ctr1", listTargets)
        assert listResult == []


class TestStandardizingANonFirstPlot:
    """The end-to-end pairing, driven the way production drives it.

    This is the case no unit test could see: each half was correct in
    isolation, and only the caller's own filtered output exposed the
    offset. Standardizing the SECOND plot of a step used to report
    "Conversion failed" on a conversion that had succeeded.
    """

    def test_targeting_the_second_plot_verifies_that_plots_standard(self):
        dictCtx = _fdictMakeContext(ftResult=(0, ""))
        listPlots = [("/out/plot1.pdf", "plot1.pdf"),
                     ("/out/plot2.pdf", "plot2.pdf")]
        listResult = _flistConvertToStandards(
            dictCtx, "ctr1", listPlots, "plot2.pdf")
        assert listResult == ["plot2_standard.png"], (
            "targeting the second plot must verify the second plot's "
            "standard; an empty list here is the researcher being told "
            "a successful conversion failed"
        )

    def test_targeting_the_second_plot_converts_only_that_plot(self):
        dictCtx = _fdictMakeContext(ftResult=(0, ""))
        listPlots = [("/out/plot1.pdf", "plot1.pdf"),
                     ("/out/plot2.pdf", "plot2.pdf")]
        _flistConvertToStandards(
            dictCtx, "ctr1", listPlots, "plot2.pdf")
        sConvertCommand = (
            dictCtx["docker"].ftResultExecuteCommand
            .call_args_list[0][0][1]
        )
        assert "plot2.pdf" in sConvertCommand
        assert "plot1.pdf" not in sConvertCommand


class TestRasterPlotsGetAStandard:
    """A PNG project had no path through the converter at all.

    ``pdftoppm`` and ``gs`` both read PDF/PostScript, so both failed on
    raster bytes, the trailing ``|| true`` swallowed both, and the
    route told the researcher to check for a ghostscript that was
    installed and working. A raster source's standard is the image
    itself.
    """

    def test_a_png_plot_is_copied_rather_than_run_through_a_pdf_reader(self):
        dictCtx = _fdictMakeContext(ftResult=(0, ""))
        listPlots = [("/out/figure.png", "figure.png")]
        listResult = _flistConvertToStandards(
            dictCtx, "ctr1", listPlots, "")
        assert listResult == ["figure_standard.png"]
        sConvertCommand = (
            dictCtx["docker"].ftResultExecuteCommand
            .call_args_list[0][0][1]
        )
        assert "cp -f" in sConvertCommand, (
            "a PNG must be copied; routing it through pdftoppm/gs is "
            f"what produced nothing at all: {sConvertCommand}"
        )
        assert "pdftoppm" not in sConvertCommand
        assert "figure_standard.png" in sConvertCommand

    def test_a_vector_plot_still_goes_through_the_converters(self):
        dictCtx = _fdictMakeContext(ftResult=(0, ""))
        listPlots = [("/out/figure.pdf", "figure.pdf")]
        _flistConvertToStandards(dictCtx, "ctr1", listPlots, "")
        sConvertCommand = (
            dictCtx["docker"].ftResultExecuteCommand
            .call_args_list[0][0][1]
        )
        assert "pdftoppm" in sConvertCommand
        assert "gs " in sConvertCommand

    def test_an_unsupported_format_is_skipped_rather_than_attempted(self):
        dictCtx = _fdictMakeContext(ftResult=(0, ""))
        listPlots = [("/out/figure.svg", "figure.svg")]
        assert _flistConvertToStandards(
            dictCtx, "ctr1", listPlots, "") == []
        assert dictCtx["docker"].ftResultExecuteCommand.call_count == 0, (
            "an unconvertible format must not reach the container at "
            "all; the route refuses it by name instead"
        )


class TestFdictCheckStandardsExist:
    def test_returns_empty_dict_for_empty_plots(self):
        dictCtx = _fdictMakeContext()
        dictResult = _fnRunAsync(
            _fdictCheckStandardsExist(dictCtx, "ctr1", []))
        assert dictResult == {}

    @patch("vaibify.gui.routes.plotRoutes._fsPlotStandardPath",
           side_effect=lambda sBase: f"{sBase}_standard.png")
    def test_detects_existing_standards(self, _mock):
        dictCtx = _fdictMakeContextWithProbe({
            "/out/plot1_standard.png": True,
            "/out/plot2_standard.png": True,
        })
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
        dictCtx = _fdictMakeContextWithProbe({
            "/out/plot1_standard.png": True,
            "/out/plot2_standard.png": False,
        })
        listPlots = [("/out/plot1.pdf", "plot1.pdf"),
                      ("/out/plot2.pdf", "plot2.pdf")]
        dictResult = _fnRunAsync(
            _fdictCheckStandardsExist(
                dictCtx, "ctr1", listPlots))
        assert dictResult == {"plot1.pdf": True,
                              "plot2.pdf": False}

    @patch("vaibify.gui.routes.plotRoutes._fsPlotStandardPath",
           side_effect=lambda sBase: f"{sBase}_standard.png")
    def test_answers_per_plot_not_by_output_position(self, _mock):
        """Each plot gets its OWN answer, keyed by its own path.

        Replaces two tests that fed the batched command a short or
        empty stdout and asserted the parser degraded to False. There
        is no shared stdout to truncate now -- each plot is a separate
        typed read -- so the property worth asserting is that the
        answers cannot slide across plots, which is exactly what the
        positional line-index parsing could do.
        """
        dictCtx = _fdictMakeContextWithProbe({
            "/out/plot1_standard.png": False,
            "/out/plot2_standard.png": True,
        })
        listPlots = [("/out/plot1.pdf", "plot1.pdf"),
                      ("/out/plot2.pdf", "plot2.pdf")]
        dictResult = _fnRunAsync(
            _fdictCheckStandardsExist(
                dictCtx, "ctr1", listPlots))
        assert dictResult["plot1.pdf"] is False
        assert dictResult["plot2.pdf"] is True

    @patch("vaibify.gui.routes.plotRoutes._fsPlotStandardPath",
           side_effect=lambda sBase: f"{sBase}_standard.png")
    def test_a_failed_probe_propagates_rather_than_reading_as_absent(
        self, _mock,
    ):
        """"vaibify could not look" must not be shown as "not there".

        The batched command answered N for every plot when the exec
        itself failed, so an unreachable container told the researcher
        their standards were missing. The typed read raises instead.
        """
        dictCtx = _fdictMakeContext()
        dictCtx["docker"].fbContainerPathIsFile.side_effect = OSError(
            "cannot probe path in container",
        )
        listPlots = [("/out/plot1.pdf", "plot1.pdf")]
        with pytest.raises(OSError):
            _fnRunAsync(
                _fdictCheckStandardsExist(
                    dictCtx, "ctr1", listPlots))


# ── Route handler tests ──────────────────────────────────────────


class TestRouteStandardizePlots:
    @patch("vaibify.gui.routes.plotRoutes._flistResolvePlotPaths",
           return_value=[("/out/fig.pdf", "fig.pdf")])
    @patch("vaibify.gui.routes.plotRoutes."
           "_flistConvertPlotsUnderTheDrain",
           new_callable=AsyncMock,
           return_value=["fig_standard.png"])
    @patch("vaibify.gui.routes.plotRoutes.fdictCommitWorkflowSave")
    @patch("vaibify.gui.routes.plotRoutes.fdictRequireWorkflow")
    def test_standardize_success(
        self, mockRequire, mockSave, mockConvert, mockResolve,
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
        # The save goes through the mode-(a) carrier now, not straight
        # to dictCtx["save"]; what this asserts is unchanged -- the
        # route records the standardization exactly once.
        mockSave.assert_called_once()

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
    @patch("vaibify.gui.routes.plotRoutes."
           "_flistConvertPlotsUnderTheDrain",
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
